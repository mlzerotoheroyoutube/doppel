"""Coding benchmark: how much do Receipts and SubQ add to Meta as a coding agent?

Configurations (same Meta coder in all three):
    meta        Meta Muse Wonder fixes the issue once; whatever it writes ships.
    receipts    + Receipts evidence (deterministic checks + Meta's hidden exam); rework up to 3
                rounds; ships when Receipts doesn't block. No SubQ anywhere.
    subq        + SubQ judges the hidden-exam failures and every round; ships only when
                Receipts doesn't block AND SubQ votes to ship.

Every shipped result is graded by independent acceptance tests (benchmark/graders) that no
agent ever sees. Tasks are labelled generic (one clear behavior) or complex (several
interacting rules, edge cases, API changes).

    python -m benchmark.loop_bench --config subq --trial 1
    python -m benchmark.loop_bench --report
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark" / "results" / "loop"

TASKS = [
    # id, category, project, issue number, grader file, grader prefix
    ("store-1-percent", "generic", "demo_store", 1, "test_store.py", "i1"),
    ("store-2-shipping", "generic", "demo_store", 2, "test_store.py", "i2"),
    ("cal-1-overlap", "generic", "benchmark/fixtures/calendar", 1, "test_calendar.py", "i1"),
    ("store-3-remove-qty", "complex", "demo_store", 3, "test_store.py", "i3"),
    ("cal-2-free-slots", "complex", "benchmark/fixtures/calendar", 2, "test_calendar.py", "i2"),
    ("cal-3-12h-times", "complex", "benchmark/fixtures/calendar", 3, "test_calendar.py", "i3"),
]


def grade(project: Path, grader: str, prefix: str) -> dict:
    """Run the hidden acceptance tests for this issue plus the contract tests."""
    env = {**os.environ, "PYTHONPATH": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
                           str(ROOT / "benchmark" / "graders" / grader), "-k", f"{prefix}_ or contract"],
                          capture_output=True, text=True, env=env, timeout=120)
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    return {"passed": proc.returncode == 0, "summary": last}


def run_one(config: str, task: tuple) -> dict:
    from receipts import loop, sandbox
    from receipts.__main__ import read_issue

    tid, cat, proj, num, grader, prefix = task
    base = (ROOT / proj).resolve()
    issue = read_issue(str(base / "ISSUES.md"), num)
    t0 = time.time()
    row = {"task": tid, "category": cat, "config": config}
    try:
        if config == "meta":
            work = sandbox.copy_project(base)
            _, files = loop.implement(issue, work, [])
            loop.apply_files(work, files)
            row.update(shipped=True, rounds=1)
        else:
            res = loop.run(issue, base, max_rounds=3, use_subq=(config == "subq"), mutants=20)
            work = Path(res.workdir)
            row.update(shipped=res.status == "shipped", rounds=len(res.rounds),
                       judge=[r.judge.get("problem") for r in res.rounds])
        g = grade(work, grader, prefix)
        row.update(correct=g["passed"], grade=g["summary"])
    except Exception as e:  # noqa: BLE001 - a crash is a failed task
        row.update(shipped=False, correct=False, rounds=0, error=str(e)[:300])
    row["seconds"] = round(time.time() - t0, 1)
    return row


def report() -> None:
    rows = []
    for f in sorted(OUT.glob("*.json")):
        rows += json.loads(f.read_text())
    configs = ["meta", "receipts", "subq"]
    names = {"meta": "Meta alone", "receipts": "Meta + Receipts (no SubQ)", "subq": "Meta + Receipts + SubQ"}
    print("| Configuration | Tasks | Correct & shipped | Wrong but shipped | Held back | Avg rounds |")
    print("|---|---|---|---|---|---|")
    for cat in ("generic", "complex", "overall"):
        for c in configs:
            rs = [r for r in rows if r["config"] == c and (cat == "overall" or r["category"] == cat)]
            if not rs:
                continue
            n = len(rs)
            good = sum(r["shipped"] and r["correct"] for r in rs)
            bad = sum(r["shipped"] and not r["correct"] for r in rs)
            held = sum(not r["shipped"] for r in rs)
            rounds = sum(r.get("rounds", 0) for r in rs) / n
            print(f"| **{cat}** · {names[c]} | {n} | {good}/{n} ({good / n:.0%}) | {bad}/{n} ({bad / n:.0%}) | "
                  f"{held}/{n} | {rounds:.1f} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=["meta", "receipts", "subq"])
    ap.add_argument("--trial", type=int, default=1)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        report()
        return
    os.environ["RECEIPTS_TRIAL"] = str(args.trial)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in TASKS:
        row = run_one(args.config, task)
        rows.append(row)
        print(json.dumps(row), flush=True)
    (OUT / f"{args.config}-t{args.trial}.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
