"""Mutation check: plant small bugs in the code the PR changed and see whether
the tests notice. A test suite that stays green with a bug planted in it was
never evidence that the code works.
"""

from __future__ import annotations

import ast
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import sandbox

SWAP_CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
            ast.In: ast.NotIn, ast.NotIn: ast.In}
SWAP_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv,
            ast.FloorDiv: ast.Mult, ast.Div: ast.Mult, ast.Mod: ast.FloorDiv}
SWAP_CALL = {"min": "max", "max": "min"}


@dataclass
class Mutant:
    file: str
    function: str
    line: int
    before: str
    after: str
    killed: bool | None = None


def _points(fn: ast.AST) -> list[tuple[ast.AST, str]]:
    """Every place in `fn` we know how to mutate, in a stable order."""
    pts = []
    doc = fn.body[0] if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else None
    for node in ast.walk(fn):
        if node is doc or (doc is not None and node is doc.value):
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in SWAP_CMP:
                    pts.append((node, f"cmp:{i}"))
        elif isinstance(node, ast.BinOp) and type(node.op) in SWAP_BIN:
            pts.append((node, "bin"))
        elif isinstance(node, ast.BoolOp):
            pts.append((node, "bool"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            pts.append((node, "not"))
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            pts.append((node, "int"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SWAP_CALL:
            pts.append((node, "call"))
    return pts


def _apply(node: ast.AST, kind: str) -> ast.AST:
    """Return the replacement for `node` (may mutate it in place)."""
    if kind.startswith("cmp:"):
        i = int(kind.split(":")[1])
        node.ops[i] = SWAP_CMP[type(node.ops[i])]()
    elif kind == "bin":
        node.op = SWAP_BIN[type(node.op)]()
    elif kind == "bool":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "not":
        return node.operand
    elif kind == "int":
        node.value = node.value + 1
    elif kind == "call":
        node.func.id = SWAP_CALL[node.func.id]
    return node


class _Replace(ast.NodeTransformer):
    def __init__(self, target: ast.AST, kind: str):
        self.target, self.kind = target, kind

    def visit(self, node):
        if node is self.target:
            return _apply(node, self.kind)
        return super().visit(node)


def _find(tree: ast.Module, qualname: str) -> ast.AST | None:
    parts = qualname.split(".")
    scope = tree.body
    node = None
    for part in parts:
        node = next((n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            return None
        scope = node.body
    return node


def generate(project: Path, targets: list[tuple[str, str]], limit: int = 40) -> list[tuple[Mutant, str]]:
    """Return (mutant, mutated file source) pairs for the target functions."""
    out = []
    for rel, qualname in targets:
        src = (project / rel).read_text()
        tree = ast.parse(src)
        fn = _find(tree, qualname)
        if fn is None:
            continue
        for idx in range(len(_points(fn))):
            mtree = copy.deepcopy(tree)
            mfn = _find(mtree, qualname)
            node, kind = _points(mfn)[idx]
            line = getattr(node, "lineno", mfn.lineno)
            before_line = src.splitlines()[line - 1].strip()
            _Replace(node, kind).visit(mfn)
            ast.fix_missing_locations(mtree)
            new_src = ast.unparse(mtree)
            after_line = ast.unparse(node) if kind != "not" else "(negation removed)"
            out.append((Mutant(rel, qualname, line, before_line, after_line), new_src))
    if len(out) > limit:  # spread the sample evenly across functions
        step = len(out) / limit
        out = [out[int(i * step)] for i in range(limit)]
    return out


def run(project: Path, targets: list[tuple[str, str]], tests: Path | None = None,
        limit: int = 40, workers: int = 8) -> list[Mutant]:
    """Mutate `project` and run either its own suite (tests=None) or an
    external isolated test dir. A mutant is killed when the tests go red."""
    pairs = generate(project, targets, limit)

    def one(pair):
        mutant, src = pair
        copy_dir = sandbox.copy_project(project)
        (copy_dir / mutant.file).write_text(src)
        res = sandbox.run_pytest(copy_dir, tests, isolated=tests is not None, timeout=30)
        mutant.killed = res.timed_out or res.returncode != 0
        return mutant

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, pairs))


def score(mutants: list[Mutant]) -> float | None:
    if not mutants:
        return None
    return sum(m.killed for m in mutants) / len(mutants)
