"""'Meta proposes, SubQ picks' vs Meta alone on the 200 held-out MBPP test tasks (ids 311-510).

    python -m benchmark.select_bench
    python -m benchmark.select_bench --report
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from receipts import select

from . import mbpp_bench

OUT = Path(__file__).resolve().parent.parent / "benchmark" / "results" / "select.jsonl"


ALGO = {"v1": "propose_and_pick", "v2": "propose_v2"}


def run_task(task: dict, algo: str = "v1") -> dict:
    from receipts.__main__ import read_issue
    base = mbpp_bench.build(task)
    issue = read_issue(str(base / "ISSUES.md"), 1)
    row = {"task_id": task["task_id"]}
    try:
        sel = getattr(select, ALGO[algo])(issue, base)
        row["how"] = sel.how
        row["chosen"] = sel.chosen
        row["per_attempt"] = {}
        for a in sel.attempts:
            row["per_attempt"][a.label] = {"model": a.model, "exam": [a.exam_passed, a.exam_total],
                                           "correct": bool(a.workdir) and mbpp_bench.grade(Path(a.workdir), task),
                                           "error": a.error}
        row["correct"] = row["per_attempt"][sel.chosen]["correct"]
        row["subq"] = sel.subq
    except Exception as e:  # noqa: BLE001
        row.update(correct=False, error=str(e)[:200])
    return row


def report(path: Path | None = None) -> None:
    rows = [json.loads(l) for l in (path or OUT).read_text().splitlines()]
    rows = [r for r in rows if "error" not in r]
    meta_alone = {}
    for f in (OUT.parent / "mbpp").glob("meta-*.jsonl"):
        for l in f.read_text().splitlines():
            r = json.loads(l)
            meta_alone[r["task_id"]] = r["correct"]
    n = len(rows)
    sel_ok = sum(r["correct"] for r in rows)
    a_ok = sum(r["per_attempt"]["A"]["correct"] for r in rows)
    b_ok = sum(r["per_attempt"].get("B", {}).get("correct", False) for r in rows)
    oracle = sum(r["per_attempt"]["A"]["correct"] or r["per_attempt"].get("B", {}).get("correct", False) for r in rows)
    m_ok = sum(meta_alone.get(r["task_id"], False) for r in rows)
    print(f"| Configuration (same {n} held-out tasks) | Correct | Wrong shipped |\n|---|---|---|")
    print(f"| Meta alone (earlier run, Muse Wonder) | {m_ok} ({m_ok / n:.1%}) | {n - m_ok} |")
    print(f"| Muse Wonder attempt only | {a_ok} ({a_ok / n:.1%}) | {n - a_ok} |")
    print(f"| Muse Spark attempt only | {b_ok} ({b_ok / n:.1%}) | {n - b_ok} |")
    print(f"| **Meta proposes, evidence + SubQ pick** | **{sel_ok} ({sel_ok / n:.1%})** | **{n - sel_ok}** |")
    print(f"| Best possible pick (either attempt right) | {oracle} ({oracle / n:.1%}) | {n - oracle} |")
    how = Counter(r["how"] for r in rows)
    subq = [r for r in rows if r["how"] == "subq"]
    disagree = [r for r in subq if r["per_attempt"]["A"]["correct"] != r["per_attempt"].get("B", {}).get("correct")]
    print(f"\nHow the winner was picked: {dict(how)}. SubQ was asked {len(subq)} times; on the {len(disagree)} where "
          f"exactly one attempt was right it chose the right one {sum(r['correct'] for r in disagree)} times.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--algo", choices=["v1", "v2"], default="v1")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    out = OUT if a.algo == "v1" else OUT.with_name("select-v2.jsonl")
    if a.report:
        report(out)
        return
    os.environ["RECEIPTS_TRIAL"] = "mbpp"  # Wonder's attempt replays the earlier Meta-alone answer
    tasks = [t for t in mbpp_bench.tasks() if t["task_id"] >= 311]
    with ThreadPoolExecutor(a.workers) as pool:
        rows = list(pool.map(lambda t: run_task(t, a.algo), tasks))
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    report(out)


if __name__ == "__main__":
    main()
