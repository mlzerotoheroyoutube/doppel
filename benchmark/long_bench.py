"""Long-horizon and long-vertical benchmarks: Meta alone vs Meta + Receipts + SubQ.

horizon   one project, six dependent issues in a row (benchmark/fixtures/calendar_horizon).
          Each step starts from whatever the previous step left. After every step the code is
          graded on the hidden tests of ALL issues so far, so regressions and drift show up.
vertical  one deep feature (itemized receipts with exact allocation) on benchmark/fixtures/store_vertical,
          graded by exact and property-based hidden tests.

    python -m benchmark.long_bench --suite horizon --config subq --trial 1
    python -m benchmark.long_bench --report
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark" / "results" / "long"
G = ROOT / "benchmark" / "graders"

HORIZON = {"fixture": "benchmark/fixtures/calendar_horizon",
           "steps": [(1, G / "test_calendar.py", "i1"), (2, G / "test_calendar.py", "i2"),
                     (3, G / "test_calendar.py", "i3"), (4, G / "test_calendar_horizon.py", "i4"),
                     (5, G / "test_calendar_horizon.py", "i5"), (6, G / "test_calendar_horizon.py", "i6")]}
VERTICAL = {"fixture": "benchmark/fixtures/store_vertical", "steps": [(1, G / "test_store_vertical.py", "i1")]}


def grade_issue(project: Path, grader: Path, prefix: str) -> tuple[int, int]:
    """(passed, total) for one issue's hidden tests plus that file's contract tests."""
    env = {**os.environ, "PYTHONPATH": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
                           str(grader), "-k", f"{prefix}_ or contract"], capture_output=True, text=True,
                          env=env, timeout=120)
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", last)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", last)) else 0
    errors = int(m.group(1)) if (m := re.search(r"(\d+) error", last)) else 0
    return passed, passed + failed + errors


def run(suite: str, config: str) -> dict:
    from receipts import loop, sandbox
    from receipts.__main__ import read_issue

    spec = HORIZON if suite == "horizon" else VERTICAL
    fixture = (ROOT / spec["fixture"]).resolve()
    current = sandbox.copy_project(fixture)
    steps, t0 = [], time.time()
    for k, (num, _, _) in enumerate(spec["steps"]):
        issue = read_issue(str(fixture / "ISSUES.md"), num)
        s0 = time.time()
        row = {"step": num}
        try:
            if config == "meta":
                work = sandbox.copy_project(current)
                _, files = loop.implement(issue, work, [])
                loop.apply_files(work, files)
                current, row["shipped"], row["rounds"] = work, True, 1
            else:
                res = loop.run(issue, current, max_rounds=3, use_subq=True, mutants=20)
                row["shipped"], row["rounds"] = res.status == "shipped", len(res.rounds)
                row["judge"] = [r.judge.get("problem") for r in res.rounds]
                if res.status == "shipped":
                    current = Path(res.workdir)
        except Exception as e:  # noqa: BLE001
            row.update(shipped=False, rounds=0, error=str(e)[:300])
        # grade every issue so far on the code as it now stands
        per = {}
        for n2, grader, prefix in spec["steps"][: k + 1]:
            p, tot = grade_issue(current, grader, prefix)
            per[str(n2)] = [p, tot]
        row["grades"] = per
        row["step_correct"] = per[str(num)][0] == per[str(num)][1] > 0
        row["all_correct_so_far"] = all(p == t > 0 for p, t in per.values())
        row["seconds"] = round(time.time() - s0, 1)
        steps.append(row)
        print(json.dumps(row), flush=True)
    return {"suite": suite, "config": config, "steps": steps, "seconds": round(time.time() - t0, 1)}


def report() -> None:
    runs = [json.loads(f.read_text()) for f in sorted(OUT.glob("*.json"))]
    names = {"meta": "Meta alone", "subq": "Meta + Receipts + SubQ"}
    print("### Long horizon (6 dependent issues, graded on all issues so far after every step)\n")
    print("| Configuration | Runs | Steps fully correct | Final repo: issues correct | Regressions | Wrong code shipped | Held back |")
    print("|---|---|---|---|---|---|---|")
    for c in ("meta", "subq"):
        rs = [r for r in runs if r["suite"] == "horizon" and r["config"] == c]
        if not rs:
            continue
        steps = [s for r in rs for s in r["steps"]]
        final = [r["steps"][-1]["grades"] for r in rs]
        final_ok = sum(sum(p == t > 0 for p, t in g.values()) for g in final)
        regress = 0
        for r in rs:
            for i in range(1, len(r["steps"])):
                prev, cur = r["steps"][i - 1]["grades"], r["steps"][i]["grades"]
                regress += sum(1 for n, (p, t) in prev.items() if p == t > 0 and cur[n][0] < cur[n][1])
        wrong = sum(s["shipped"] and not s["step_correct"] for s in steps)
        held = sum(not s["shipped"] for s in steps)
        print(f"| {names[c]} | {len(rs)} | {sum(s['step_correct'] for s in steps)}/{len(steps)} | "
              f"{final_ok}/{6 * len(rs)} | {regress} | {wrong} | {held} |")
    print("\n### Long vertical (itemized receipts, 46 hidden exact + property tests)\n")
    print("| Configuration | Runs | Fully correct | Hidden tests passed | Wrong code shipped | Held back | Avg rounds |")
    print("|---|---|---|---|---|---|---|")
    for c in ("meta", "subq"):
        rs = [r for r in runs if r["suite"] == "vertical" and r["config"] == c]
        if not rs:
            continue
        s = [r["steps"][0] for r in rs]
        p = sum(x["grades"]["1"][0] for x in s)
        t = sum(x["grades"]["1"][1] for x in s)
        print(f"| {names[c]} | {len(rs)} | {sum(x['step_correct'] for x in s)}/{len(rs)} | {p}/{t} | "
              f"{sum(x['shipped'] and not x['step_correct'] for x in s)} | {sum(not x['shipped'] for x in s)} | "
              f"{sum(x['rounds'] for x in s) / len(s):.1f} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["horizon", "vertical"])
    ap.add_argument("--config", choices=["meta", "subq"])
    ap.add_argument("--trial", type=int, default=1)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
        return
    os.environ["RECEIPTS_TRIAL"] = f"long-{a.trial}"
    OUT.mkdir(parents=True, exist_ok=True)
    result = run(a.suite, a.config)
    (OUT / f"{a.suite}-{a.config}-t{a.trial}.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
