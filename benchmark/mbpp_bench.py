"""500-task benchmark on the MBPP test split (task ids 11-510): Meta alone vs Meta + Receipts + SubQ.

MBPP: "Mostly Basic Python Problems", Google Research, CC-BY 4.0
(https://github.com/google-research/google-research/tree/master/mbpp). Not ours; used with attribution.

Each task becomes a tiny project: the task text, the function signature and ONE example assert are
the issue; MBPP's three official asserts are the hidden grader, never shown to any agent.

    python -m benchmark.mbpp_bench --config subq --shard 0/16
    python -m benchmark.mbpp_bench --report
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmark" / "data" / "mbpp" / "mbpp.jsonl"
OUT = ROOT / "benchmark" / "results" / "mbpp"


def tasks() -> list[dict]:
    rows = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    return [r for r in rows if 11 <= r["task_id"] <= 510]


def signature(task: dict) -> tuple[str, str]:
    """(function name, 'def name(args):' line) of the function the tests call."""
    called = ast.parse(task["test_list"][0]).body[0].test
    while not isinstance(called, ast.Call):
        called = next(n for n in ast.walk(called) if isinstance(n, ast.Call))
    name = called.func.id if isinstance(called.func, ast.Name) else ast.unparse(called.func)
    for node in ast.walk(ast.parse(task["code"])):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return name, f"def {name}({ast.unparse(node.args)}):"
    return name, f"def {name}(*args):"


def build(task: dict) -> Path:
    name, sig = signature(task)
    doc = task["text"].replace("\\", "\\\\").replace('"""', "'")
    d = Path(tempfile.mkdtemp(prefix=f"mbpp-{task['task_id']}-")) / "project"
    (d / "tests").mkdir(parents=True)
    (d / "solution.py").write_text(f'"""{doc}"""\n\n\n{sig}\n    """{doc}"""\n'
                                   "    raise NotImplementedError\n")
    (d / "tests" / "test_solution.py").write_text("import solution\n\n\ndef test_module_imports():\n"
                                                  "    assert solution is not None\n")
    (d / "ISSUES.md").write_text(f"# Open issues\n\n## #1 Implement `{name}`\n\n{task['text']}\n\n"
                                 f"Implement `{sig[4:-1]}` in `solution.py`.\n\nExample:\n\n```python\n"
                                 f"{task['test_list'][0]}\n```\n")
    return d


def grade(project: Path, task: dict) -> bool:
    setup = task.get("test_setup_code") or ""
    body = "\n".join(f"def test_{i}():\n    {a}" for i, a in enumerate(task["test_list"]))
    gdir = Path(tempfile.mkdtemp(prefix="mbpp-grade-"))
    (gdir / "test_hidden.py").write_text(f"from solution import *\n{setup}\n\n{body}\n")
    env = {**os.environ, "PYTHONPATH": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
                               str(gdir)], capture_output=True, text=True, env=env, timeout=60, cwd=gdir)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_task(config: str, task: dict) -> dict:
    from receipts import loop, sandbox
    from receipts.__main__ import read_issue

    base = build(task)
    issue = read_issue(str(base / "ISSUES.md"), 1)
    row, t0 = {"task_id": task["task_id"], "config": config}, time.time()
    try:
        if config == "meta":
            work = sandbox.copy_project(base)
            _, files = loop.implement(issue, work, [])
            loop.apply_files(work, files)
            row.update(shipped=True, rounds=1)
        else:
            res = loop.run(issue, base, max_rounds=3, use_subq=True, mutants=10)
            work = Path(res.workdir)
            row.update(shipped=res.status == "shipped", rounds=len(res.rounds),
                       judge=[r.judge.get("problem") for r in res.rounds])
        row["correct"] = grade(work, task)
    except Exception as e:  # noqa: BLE001
        row.update(shipped=False, correct=False, rounds=0, error=str(e)[:200])
    row["seconds"] = round(time.time() - t0, 1)
    return row


def report() -> None:
    rows = {}
    for f in sorted(OUT.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            rows[(r["config"], r["task_id"])] = r
    names = {"meta": "Meta alone", "subq": "Meta + Receipts + SubQ"}
    print("| Configuration | Tasks done | Correct & shipped | Wrong but shipped | Held back | Infra errors | Avg rounds |")
    print("|---|---|---|---|---|---|---|")
    for c in ("meta", "subq"):
        rs = [r for (cc, _), r in rows.items() if cc == c]
        if not rs:
            continue
        n = len(rs)
        ok = sum(r["shipped"] and r["correct"] for r in rs)
        bad = sum(r["shipped"] and not r["correct"] for r in rs)
        held = sum(not r["shipped"] and "error" not in r for r in rs)
        err = sum("error" in r for r in rs)
        rounds = sum(r.get("rounds", 0) for r in rs) / n
        print(f"| {names[c]} | {n} | {ok} ({ok / n:.1%}) | {bad} ({bad / n:.1%}) | {held} | {err} | {rounds:.2f} |")
    both = [(rows[("meta", t)], rows[("subq", t)]) for (c, t) in rows if c == "meta" and ("subq", t) in rows]
    if both:
        caught = sum(m["shipped"] and not m["correct"] and not s["shipped"] for m, s in both)
        fixed = sum(not m["correct"] and s["shipped"] and s["correct"] for m, s in both)
        lost = sum(m["correct"] and not (s["shipped"] and s["correct"]) for m, s in both)
        print(f"\nOn the {len(both)} tasks both configurations finished: SubQ loop turned {fixed} wrong Meta answers "
              f"into correct shipped ones, held back {caught} that would have shipped wrong, and lost {lost} "
              "that Meta alone got right.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=["meta", "subq"])
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
        return
    i, n = map(int, a.shard.split("/"))
    os.environ["RECEIPTS_TRIAL"] = "mbpp"
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{a.config}-{i}of{n}.jsonl"
    done = {r["task_id"] for r in map(json.loads, out.read_text().splitlines()) if "error" not in r} \
        if out.exists() else set()  # rows that hit an infrastructure error are retried
    for task in tasks()[i::n]:
        if task["task_id"] in done:
            continue
        row = run_task(a.config, task)
        with out.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
