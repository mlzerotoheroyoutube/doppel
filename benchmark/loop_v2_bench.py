"""Loop v2 (evidence first, SubQ vetoes only when confident) vs loop v1 and Meta alone,
on the 200 held-out MBPP test tasks (ids 311-510).

    python -m benchmark.loop_v2_bench
    python -m benchmark.loop_v2_bench --report
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import mbpp_bench

OUT = Path(__file__).resolve().parent.parent / "benchmark" / "results" / "mbpp-loop-v2.jsonl"


def report() -> None:
    v2 = {r["task_id"]: r for r in map(json.loads, OUT.read_text().splitlines()) if "error" not in r}
    v1, meta = {}, {}
    for f in (OUT.parent / "mbpp").glob("*.jsonl"):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            (v1 if r["config"] == "subq" else meta)[r["task_id"]] = r
    ids = [t for t in v2 if t in v1 and t in meta]
    n = len(ids)

    def row(name, rs):
        ok = sum(r["shipped"] and r["correct"] for r in rs)
        bad = sum(r["shipped"] and not r["correct"] for r in rs)
        held = sum(not r["shipped"] for r in rs)
        held_right = sum((not r["shipped"]) and meta[r["task_id"]]["correct"] for r in rs)
        print(f"| {name} | {ok} ({ok / n:.1%}) | {bad} | {held} | {held_right} |")

    print(f"| Configuration (same {n} held-out tasks) | Correct & shipped | Wrong shipped | Held back | "
          "Held back but Meta had it right |\n|---|---|---|---|---|")
    row("Meta alone", [meta[t] for t in ids])
    row("Loop v1 (SubQ must approve)", [v1[t] for t in ids])
    row("**Loop v2 (evidence first, SubQ vetoes only when confident)**", [v2[t] for t in ids])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    if a.report:
        report()
        return
    os.environ["RECEIPTS_TRIAL"] = "mbpp"  # replays the cached exams and Meta attempts from the v1 run
    tasks = [t for t in mbpp_bench.tasks() if t["task_id"] >= 311]
    with ThreadPoolExecutor(a.workers) as pool:
        rows = list(pool.map(lambda t: mbpp_bench.run_task("subq", t), tasks))
    OUT.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    report()


if __name__ == "__main__":
    main()
