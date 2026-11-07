"""Blind tests: written from the issue and the public interface only.

The writer never sees the diff, the PR description, the PR's tests, or any
function body. It gets the issue text plus signatures, docstrings and public
constants taken from the BASE commit. That keeps it out of the implementer's
blind spots: it cannot be talked into the implementer's reading of the issue.

Each blind test is then run on BASE and on HEAD. The pair of outcomes is what
makes a receipt trustworthy:

  base  head   meaning
  pass  fail   regression: the PR broke behavior that used to work
  fail  pass   the PR fixed something (good)
  fail  fail   issue test -> the PR did not fix the issue
               contract test -> pre-existing problem, not this PR's fault
  pass  pass   fine

Failures are then double-checked by a separate judge call that sees the spec,
the test and the failure message (still no code), to throw out tests where the
writer misread the spec.
"""

from __future__ import annotations

import ast
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import llm, sandbox
from .tamper import is_source_file, py_files

WRITER_SYSTEM = """You are an independent QA engineer. You write pytest tests that \
decide whether a pull request really resolves an issue. You have NOT seen the \
pull request, its code, or its tests, and you never will. You only know the \
issue and the public interface. Test behavior that the issue or the documented \
rules state explicitly; never test behavior you are guessing at."""

WRITER_PROMPT = """# Issue
{issue}

# Public interface (from the code BEFORE the change; bodies removed)
{interface}

# Your task
Write one pytest file.

Name every test either:
- `test_issue_<what>`: checks the behavior the issue asks for, including edge \
cases a sloppy fix would miss (quantities, boundaries, empty inputs, values just \
above and below a threshold, combinations with other features).
- `test_contract_<what>`: checks a documented rule the change must not break.

Rules:
- Use only the public interface shown. Import from the module paths given.
- Give every test a one-line docstring that quotes or paraphrases the exact rule \
it checks.
- Compute expected values by hand from the stated rules and write them as \
literals, with the arithmetic in a comment.
- Write 8 to 14 tests. Each test checks one thing.
- No network, no files, no randomness, no sleeps.

Reply with only the test file in one ```python block."""

JUDGE_SYSTEM = """You audit test cases for correctness against a written specification. \
You have not seen the implementation. Be strict: a test is valid only if the \
specification clearly implies its expected outcome."""

JUDGE_PROMPT = """# Specification (issue + documented rules)
{issue}

{interface}

# Test
```python
{test_src}
```

# Observed failure
{message}

Is the test's expectation clearly required by the specification? If the test \
is valid, the failure means the code is wrong.

Answer with one JSON object and nothing else:
{{"valid": true or false, "reason": "<one sentence>"}}"""


@dataclass
class BlindOutcome:
    name: str
    kind: str        # issue | contract
    base: str        # passed | failed
    head: str
    message: str
    source: str
    judged_valid: bool | None = None
    judge_reason: str = ""


def interface(project: Path) -> str:
    """Public surface of the project: docstrings, signatures, public constants."""
    chunks = []
    for rel, src in py_files(project).items():
        if not is_source_file(rel) or rel.endswith("__init__.py"):
            continue
        tree = ast.parse(src)
        module = rel[:-3].replace("/", ".")
        lines = [f"## module `{module}`"]
        if doc := ast.get_docstring(tree):
            lines.append(f'"""{doc}"""')
        for node in tree.body:
            lines += _stub(node, "")
        chunks.append("```python\n" + "\n".join(lines) + "\n```")
    return "\n\n".join(chunks)


def _stub(node: ast.AST, indent: str) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name.startswith("_") and not node.name.startswith("__"):
            return []
        sig = ast.unparse(node).split("\n", 1)[0]
        decos = [f"{indent}@{ast.unparse(d)}" for d in node.decorator_list]
        doc = ast.get_docstring(node)
        body = f'{indent}    """{doc}"""' if doc else f"{indent}    ..."
        return decos + [indent + sig, body]
    if isinstance(node, ast.ClassDef):
        decos = [f"{indent}@{ast.unparse(d)}" for d in node.decorator_list]
        head = ast.unparse(node).split("\n", 1)[0]
        out = decos + [indent + head]
        if doc := ast.get_docstring(node):
            out.append(f'{indent}    """{doc}"""')
        for sub in node.body:
            if isinstance(sub, (ast.AnnAssign, ast.Assign)):
                out.append(indent + "    " + ast.unparse(sub))
            else:
                out += _stub(sub, indent + "    ")
        return out
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.ImportFrom)) and indent == "":
        return [ast.unparse(node)]
    return []


def write_tests_from(issue: str, iface: str) -> str:
    """The writer's whole world: the issue text and the interface text."""
    prompt = WRITER_PROMPT.format(issue=issue.strip(), interface=iface)
    return llm.extract_block(llm.complete(WRITER_SYSTEM, prompt, effort="high", role="exam"))


def write_tests(issue: str, base_dir: Path) -> str:
    return write_tests_from(issue, interface(base_dir))


def _split_tests(src: str) -> dict[str, str]:
    tree = ast.parse(src)
    return {n.name: ast.unparse(n) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")}


def _outcomes(run: sandbox.RunResult) -> dict[str, tuple[str, str]]:
    out = {}
    for r in run.results:
        name = r.nodeid.rsplit("::", 1)[-1]
        out[name] = ("passed" if r.outcome == "passed" else "failed", r.message)
    return out


def materialize(test_src: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="receipts-blind-"))
    (d / "test_receipts_blind.py").write_text(test_src)
    return d


def judge_one(issue: str, iface: str, test_src: str, message: str) -> tuple[bool, str]:
    """Code-blind check, by SubQ, that a failing test's expectation follows from the spec."""
    state = (f"# Specification (issue and documented rules)\n{issue.strip()}\n\n{iface}\n\n"
             f"# Test\n```python\n{test_src}\n```\n\n# Observed failure\n{message[:1500]}")
    a = llm.decide(state, {"valid": {
        "type": "noul",
        "instructions": "Is this test's expected outcome clearly required by the specification?",
        "criteria": {"true": "The specification clearly implies exactly this expected outcome",
                     "false": "The test guesses at behavior the specification does not state, or computes "
                              "the expected value wrongly"}}}, role="judge")
    p = float(a["valid"]["noul"])
    return p >= 0.5, f"SubQ P(test matches the spec) = {p:.2f}"


def needs_judging(o: BlindOutcome) -> bool:
    return o.head == "failed" and (o.kind == "issue" or o.base == "passed")


def differential(test_src: str, base_dir: Path, head_dir: Path) -> tuple[list[BlindOutcome], str]:
    """Run the blind tests on base and on head. Returns (outcomes, error)."""
    tdir = materialize(test_src)
    base_run = sandbox.run_pytest(sandbox.copy_project(base_dir), tdir, isolated=True)
    head_run = sandbox.run_pytest(sandbox.copy_project(head_dir), tdir, isolated=True)
    if not base_run.results:
        return [], "blind tests failed to load on base:\n" + base_run.output[-1500:]

    sources = _split_tests(test_src)
    b, h = _outcomes(base_run), _outcomes(head_run)
    outcomes = []
    for name in sources:
        bo, _ = b.get(name, ("failed", "did not run"))
        ho, msg = h.get(name, ("failed", "did not run on head (import or collection error):\n"
                                + head_run.output[-800:]))
        kind = "issue" if name.startswith("test_issue") else "contract"
        outcomes.append(BlindOutcome(name, kind, bo, ho, msg, sources[name]))
    return outcomes, ""


def run(issue: str, base_dir: Path, head_dir: Path, test_src: str | None = None,
        judge: bool = True) -> tuple[str, list[BlindOutcome], str]:
    """Write (unless given), run and judge blind tests.
    Returns (test source, outcomes, error)."""
    test_src = test_src or write_tests(issue, base_dir)
    outcomes, err = differential(test_src, base_dir, head_dir)
    if judge and not err:
        iface = interface(base_dir)
        for o in outcomes:
            if needs_judging(o):
                o.judged_valid, o.judge_reason = judge_one(issue, iface, o.source, o.message)
    return test_src, outcomes, err
