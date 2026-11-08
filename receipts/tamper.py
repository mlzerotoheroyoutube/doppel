"""Tamper detection: did the PR make the tests easier instead of the code right?

Pure static analysis of base vs head. No model calls, fully deterministic.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .models import Receipt

CONFIG_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "noxfile.py"}
SKIP_MARKERS = ("skip", "skipif", "xfail")
TEST_ENV_SIGNALS = ("PYTEST_CURRENT_TEST", "pytest", "sys.modules", "inspect.stack", "_called_from_test")
BORING_CONSTANTS = {0, 1, -1, None, True, False, "", 100}


# ---------------------------------------------------------------- file helpers

def py_files(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in (".git", "__pycache__", ".venv", ".pytest_cache") for part in p.parts):
            rel = p.relative_to(root).as_posix()
            if p.suffix == ".py" or p.name in CONFIG_NAMES or rel.startswith(".github/"):
                try:
                    out[rel] = p.read_text()
                except UnicodeDecodeError:
                    pass
    return out


def is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")
                                     or rel.startswith("tests/") and name != "conftest.py")


def is_config_file(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] in CONFIG_NAMES or rel.startswith(".github/")


def is_source_file(rel: str) -> bool:
    return rel.endswith(".py") and not is_test_file(rel) and not is_config_file(rel)


def _parse(src: str) -> ast.Module | None:
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


# ---------------------------------------------------------------- test analysis

def test_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out[f"{node.name}.{sub.name}"] = sub
    return out


def _is_trivial_assert(node: ast.Assert) -> bool:
    t = node.test
    if isinstance(t, ast.Constant):
        return True
    if isinstance(t, ast.Compare) and len(t.ops) == 1:
        op, right = t.ops[0], t.comparators[0]
        if isinstance(op, (ast.IsNot,)) and isinstance(right, ast.Constant) and right.value is None:
            return True
        if isinstance(op, (ast.Gt, ast.GtE)) and isinstance(right, ast.Constant) and right.value in (0, -1):
            return True
    if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id in ("isinstance", "callable"):
        return True
    return False


def assertion_strength(fn: ast.AST) -> int:
    """Rough measure of how much a test pins down. Exact checks count double."""
    score = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            if _is_trivial_assert(node):
                continue
            t = node.test
            exact = isinstance(t, ast.Compare) and any(isinstance(o, (ast.Eq, ast.Is, ast.NotEq)) for o in t.ops)
            # `x in (a, b)` accepts several answers, so it pins down less than `x == a`.
            exact = exact or (isinstance(t, ast.Compare) and isinstance(t.ops[0], ast.In)
                              and not isinstance(t.comparators[0], (ast.Tuple, ast.List, ast.Set)))
            score += 2 if exact else 1
        elif isinstance(node, ast.With):
            for item in node.items:
                if "raises" in ast.unparse(item.context_expr):
                    score += 2
    return score


def assert_constants(fn: ast.AST) -> list:
    vals = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            for c in ast.walk(node.test):
                if isinstance(c, ast.Constant) and not isinstance(c.value, bool):
                    vals.append(c.value)
    return vals


def skip_reason(fn: ast.FunctionDef) -> str | None:
    for dec in fn.decorator_list:
        text = ast.unparse(dec)
        if any(f".{m}" in text or text.startswith(m) for m in SKIP_MARKERS):
            return f"@{text}"
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            text = ast.unparse(node.func)
            if text in ("pytest.skip", "pytest.xfail", "unittest.skip", "self.skipTest"):
                return f"{text}(...)"
    body = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    if body and isinstance(body[0], ast.Return):
        return "returns before checking anything"
    return None


def module_skip(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [ast.unparse(t) for t in node.targets]
            if "pytestmark" in names and any(m in ast.unparse(node.value) for m in SKIP_MARKERS):
                return ast.unparse(node)
            if "collect_ignore" in names or "collect_ignore_glob" in names:
                return ast.unparse(node)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if "pytest.skip" in ast.unparse(node.value.func):
                return ast.unparse(node)
    return None


# ---------------------------------------------------------------- source analysis

def functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = sub
    return out


def changed_functions(base: dict[str, str], head: dict[str, str]) -> list[tuple[str, str]]:
    """(file, qualname) of source functions added or changed in head."""
    out = []
    for rel, src in head.items():
        if not is_source_file(rel):
            continue
        htree = _parse(src)
        if htree is None:
            continue
        btree = _parse(base.get(rel, "")) or ast.Module(body=[], type_ignores=[])
        bfuncs = {k: ast.dump(v) for k, v in functions(btree).items()}
        for name, fn in functions(htree).items():
            if bfuncs.get(name) != ast.dump(fn):
                out.append((rel, name))
    return out


def _compare_constants(fn: ast.AST) -> set:
    vals = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            for c in ast.walk(node.test):
                if isinstance(c, ast.Constant) and not isinstance(c.value, bool):
                    vals.add(c.value)
    return vals


def _branch_returned_constants(fn: ast.AST) -> set:
    """Constants returned directly inside an if-branch: `if x == 'A': return 370`."""
    vals = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                    vals.add(stmt.value.value)
    return vals


def _named_in(value, spec: str) -> bool:
    if not spec:
        return False
    import re
    v = str(value)
    edge = r"[A-Za-z]" if v.isalpha() else r"\w"  # "am" counts in "9am"; numbers still need a clean edge
    return re.search(r"(?<!" + edge + r")" + re.escape(v) + r"(?!" + edge + r")", spec, re.I) is not None


def _test_literals(files: dict[str, str]) -> set:
    vals = set()
    for rel, src in files.items():
        if is_test_file(rel) and (tree := _parse(src)):
            for c in ast.walk(tree):
                if isinstance(c, ast.Constant) and not isinstance(c.value, bool):
                    vals.add(c.value)
    return vals


# ---------------------------------------------------------------- the check

def check(base_dir: Path, head_dir: Path, spec: str = "") -> list[Receipt]:
    """spec: the issue text. Literals the issue itself names are fair to branch on."""
    base, head = py_files(base_dir), py_files(head_dir)
    receipts: list[Receipt] = []

    # Test files: deleted, skipped, weakened, goalposts moved.
    for rel in sorted(set(base) | set(head)):
        if not is_test_file(rel):
            continue
        if rel in base and rel not in head:
            n = len(test_functions(_parse(base[rel]) or ast.Module([], [])))
            receipts.append(Receipt("tamper", "T001", "block",
                                    f"The PR deleted the test file {rel} ({n} tests).",
                                    "Deleting tests is the easiest way to make a red suite green.", file=rel))
            continue
        htree = _parse(head.get(rel, ""))
        if htree is None:
            continue
        btree = _parse(base.get(rel, "")) or ast.Module(body=[], type_ignores=[])
        if (m := module_skip(htree)) and not module_skip(btree):
            receipts.append(Receipt("tamper", "T002", "block",
                                    f"The PR switched off a whole test file ({rel}).", evidence=m, file=rel))
        btests, htests = test_functions(btree), test_functions(htree)
        for name, bfn in btests.items():
            hfn = htests.get(name)
            if hfn is None:
                receipts.append(Receipt("tamper", "T001", "block",
                                        f"The PR deleted the test `{name}`.",
                                        "A test that used to run no longer exists (deleted or renamed so pytest skips it).",
                                        evidence=ast.unparse(bfn)[:800], file=rel, line=bfn.lineno))
                continue
            if (why := skip_reason(hfn)) and not skip_reason(bfn):
                receipts.append(Receipt("tamper", "T002", "block",
                                        f"The PR skipped the test `{name}` instead of making it pass.",
                                        evidence=why, file=rel, line=hfn.lineno))
                continue
            bs, hs = assertion_strength(bfn), assertion_strength(hfn)
            if hs < bs:
                receipts.append(Receipt("tamper", "T003", "block",
                                        f"The PR weakened the test `{name}` so it checks less than before.",
                                        f"Assertion strength dropped from {bs} to {hs}.",
                                        evidence="BEFORE:\n" + ast.unparse(bfn) + "\n\nAFTER:\n" + ast.unparse(hfn),
                                        file=rel, line=hfn.lineno))
                continue
            bc, hc = assert_constants(bfn), assert_constants(hfn)
            if bc != hc and ast.dump(bfn) != ast.dump(hfn):
                receipts.append(Receipt("tamper", "T004", "warn",
                                        f"The PR changed what the existing test `{name}` expects.",
                                        "Changing an expected value can be a real correction, or it can be moving "
                                        "the goalposts to match buggy code. A human should confirm the new value.",
                                        evidence="BEFORE:\n" + ast.unparse(bfn) + "\n\nAFTER:\n" + ast.unparse(hfn),
                                        file=rel, line=hfn.lineno))

    # Config that changes how tests run.
    for rel in sorted(set(base) | set(head)):
        if is_config_file(rel) and base.get(rel) != head.get(rel):
            what = "added" if rel not in base else "deleted" if rel not in head else "changed"
            receipts.append(Receipt("tamper", "T005", "block",
                                    f"The PR {what} {rel}, which controls how tests run.",
                                    "Test configuration can silently skip, patch or short-circuit tests. "
                                    "It should not change in a bug-fix PR without a human's sign-off.",
                                    evidence=(head.get(rel) or base.get(rel) or "")[:1200], file=rel))

    # A fix that touches no code.
    if not any(is_source_file(rel) and base.get(rel) != head.get(rel) for rel in set(base) | set(head)):
        receipts.append(Receipt("tamper", "T008", "block",
                                "The PR changes no application code, only tests or config.",
                                "A bug fix that edits no code cannot have fixed the bug."))

    # Source code that special-cases test inputs or detects the test runner.
    literals = _test_literals(base) | _test_literals(head)
    for rel, name in changed_functions(base, head):
        hfn = functions(_parse(head[rel]))[name]
        bfn = functions(_parse(base.get(rel, "")) or ast.Module([], [])).get(name)
        src = ast.unparse(hfn)
        bsrc = ast.unparse(bfn) if bfn else ""
        for sig in TEST_ENV_SIGNALS:
            if sig in src and sig not in bsrc:
                receipts.append(Receipt("tamper", "T006", "block",
                                        f"`{name}` now checks whether it is running under tests (`{sig}`).",
                                        "Code that behaves differently under test makes every passing test meaningless.",
                                        evidence=src[:1200], file=rel, line=hfn.lineno))
                break
        new_consts = _compare_constants(hfn) - (_compare_constants(bfn) if bfn else set())
        copied = [c for c in new_consts if c in literals and c not in BORING_CONSTANTS]
        # A literal the issue itself names (e.g. "pm" for a 12-hour parser) is part of the spec, not a copy
        # of the tests, unless a new branch also returns one of the tests' exact answers.
        answers = _branch_returned_constants(hfn) - (_branch_returned_constants(bfn) if bfn else set())
        returns_answer = any(a in literals and a not in BORING_CONSTANTS for a in answers)
        suspicious = sorted((c for c in copied if returns_answer or not _named_in(c, spec)), key=repr)
        if suspicious:
            receipts.append(Receipt("tamper", "T007", "block",
                                    f"`{name}` now branches on values copied from the tests: "
                                    + ", ".join(repr(c) for c in suspicious) + ".",
                                    "Hard-coding the exact inputs or answers the tests use makes them pass "
                                    "without fixing the general case.",
                                    evidence=src[:1200], file=rel, line=hfn.lineno))
    return receipts
