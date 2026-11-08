"""The improvement loop: Meta works, SubQ stops bad results, Meta learns, and a change is
adopted only if held-out validation confirms it. Everything else is rolled back.

For each domain (code = MBPP, decisions = cross-domain business tasks):
  1. Solve a batch of TRAINING tasks with the current playbook.
  2. SubQ judges every result. Flagged results are stopped (never shipped) and queued.
  3. Meta reflects on the stopped and failed training results and writes up to three general
     lessons. A second candidate switches the domain's Meta model.
  4. Each candidate playbook is scored on the VALIDATION set. The best one is adopted only if
     it beats the current score; otherwise it is rolled back. Every attempt is logged.
  5. At the end, the first and final playbooks are compared on the TEST set, which was never
     used for learning or validation.

    python -m benchmark.improve --domain code
    python -m benchmark.improve --domain decisions
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from receipts import llm, loop, playbook, sandbox

from . import decisions, mbpp_bench

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark" / "results" / "improve"
MODELS = ["muse-wonder-1.0", "muse-spark-1.3"]

# ---------------------------------------------------------------- domain: code (MBPP)

CODE_ERRORS = {"none": "Correct for every input the task describes",
               "wrong_logic": "The algorithm or formula is wrong",
               "edge_case": "Misses edge cases (empty input, duplicates, negatives, boundaries)",
               "wrong_signature": "Wrong function name, arguments or return type",
               "misread": "Solves a different problem than the one described",
               "incomplete": "Unfinished, crashes, or does not run"}


def code_split():
    ts = mbpp_bench.tasks()
    return ([t for t in ts if t["task_id"] <= 210], [t for t in ts if 211 <= t["task_id"] <= 310],
            [t for t in ts if t["task_id"] >= 311])


def code_grade_detail(project: Path, task: dict) -> tuple[bool, str]:
    setup = task.get("test_setup_code") or ""
    body = "\n".join(f"def test_{i}():\n    {a}" for i, a in enumerate(task["test_list"]))
    gdir = Path(tempfile.mkdtemp(prefix="imp-grade-"))
    (gdir / "test_hidden.py").write_text(f"from solution import *\n{setup}\n\n{body}\n")
    env = {**os.environ, "PYTHONPATH": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "-x",
                            str(gdir)], capture_output=True, text=True, env=env, timeout=60, cwd=gdir)
        return p.returncode == 0, p.stdout[-800:]
    except subprocess.TimeoutExpired:
        return False, "timed out"


def code_solve(task: dict, dom: dict) -> dict:
    from receipts.__main__ import read_issue
    base = mbpp_bench.build(task)
    issue = read_issue(str(base / "ISSUES.md"), 1)
    work = sandbox.copy_project(base)
    try:
        _, files = loop.implement(issue, work, [], dom=dom)
        loop.apply_files(work, files)
        code = (work / "solution.py").read_text() if (work / "solution.py").exists() else ""
        ok, detail = code_grade_detail(work, task)
    except Exception as e:  # noqa: BLE001
        code, ok, detail = "", False, f"error: {str(e)[:200]}"
    return {"id": task["task_id"], "text": task["text"], "example": task["test_list"][0], "attempt": code[-3000:],
            "correct": ok, "check": detail}


def code_judge(r: dict) -> tuple[bool, str]:
    state = (f"# Task\n{r['text']}\nExample: {r['example']}\n\n# Proposed solution\n```python\n{r['attempt']}\n```")
    a = llm.decide(state, {"right": {"type": "noul", "instructions": "Does this code correctly solve the task for "
                                     "every input the task describes?",
                                     "criteria": {"true": "Correct", "false": "Wrong for some described input"}},
                           "error": {"type": "choice", "instructions": "What is the main problem, if any?",
                                     "criteria": CODE_ERRORS}})
    return (float(a["right"]["noul"]) < 0.5 or a["error"]["choice"] != "none"), a["error"]["choice"]


# ---------------------------------------------------------------- domain: cross-domain decisions

def decision_split():
    ts = decisions.tasks(per=25)
    idx = lambda t: int(t["id"].rsplit("-", 1)[1])  # noqa: E731
    return ([t for t in ts if idx(t) < 10], [t for t in ts if 10 <= idx(t) < 17], [t for t in ts if idx(t) >= 17])


def decision_solve(task: dict, dom: dict) -> dict:
    try:
        reply, ans = decisions.ask(task, [], dom=dom)
        ok = decisions.correct(task, ans)
    except Exception as e:  # noqa: BLE001
        reply, ans, ok = f"error: {e}", {}, False
    return {"id": task["id"], "text": task["text"], "attempt": reply[-3000:], "correct": ok,
            "check": "right" if ok else "the final choice or number was wrong"}


def decision_judge(r: dict) -> tuple[bool, str]:
    ok, err, _ = decisions.judge({"text": r["text"]}, r["attempt"])
    return (not ok), err


DOMAINS = {"code": (code_split, code_solve, code_judge, "writing Python functions from a short description"),
           "decisions": (decision_split, decision_solve, decision_judge,
                         "multi-domain business decisions (finance, trade, tax, operations)")}

# ---------------------------------------------------------------- learning

REFLECT_SYSTEM = """You maintain a short playbook of lessons for an AI that does {what}. \
Lessons must be general rules that prevent whole classes of mistakes. Never include task-specific \
names, numbers or answers. Reply only with JSON."""

REFLECT_PROMPT = """# Current lessons
{lessons}

# Results that an independent judge stopped or that failed their checks
{failures}

Write at most 3 NEW lessons (one sentence each, imperative, under 30 words) that would have prevented
these mistakes and will help on future, different tasks. Do not repeat current lessons.
Reply with: {{"lessons": ["...", "..."]}}"""


def reflect(what: str, dom: dict, failures: list[dict]) -> list[str]:
    lines = []
    for f in failures[:12]:
        lines.append(f"## Task\n{f['text'][:600]}\n## Attempt (end)\n{f['attempt'][-900:]}\n"
                     f"## Judge's diagnosis: {f.get('subq_error', '?')}\n## Check: {f['check'][-300:]}")
    reply = llm.complete(REFLECT_SYSTEM.format(what=what), REFLECT_PROMPT.format(
        lessons="\n".join(f"- {x}" for x in dom.get("lessons", [])) or "(none yet)",
        failures="\n\n".join(lines)), effort="medium", role="planner")
    try:
        text = llm.extract_block(reply, "json") if "```" in reply else reply
        data = json.loads(text if text.strip().startswith("{") else reply[reply.index("{"): reply.rindex("}") + 1])
        return [str(x).strip() for x in data.get("lessons", []) if str(x).strip()][:3]
    except (ValueError, json.JSONDecodeError):
        return []


def run_set(solve, tasks: list[dict], dom: dict, workers: int) -> list[dict]:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda t: solve(t, dom), tasks))


def score(results: list[dict]) -> float:
    return sum(r["correct"] for r in results) / max(1, len(results))


def improve(name: str, rounds: int, workers: int, train_batch: int) -> dict:
    split, solve, judge, what = DOMAINS[name]
    train, val, test = split()
    random.Random(7).shuffle(train)
    pb = playbook.load()
    start = copy.deepcopy(playbook.domain(name, pb))
    current = copy.deepcopy(start)
    log = {"domain": name, "sizes": {"train": len(train), "val": len(val), "test": len(test)}, "rounds": []}
    t0 = time.time()
    cur_val = score(run_set(solve, val, current, workers))
    log["start_val"] = cur_val
    print(f"[{name}] start: validation {cur_val:.1%}", flush=True)

    for rnd in range(1, rounds + 1):
        batch = train[(rnd - 1) * train_batch: rnd * train_batch] or train[:train_batch]
        results = run_set(solve, batch, current, workers)
        with ThreadPoolExecutor(workers) as pool:  # SubQ judges every result; flagged ones are stopped
            verdicts = list(pool.map(lambda r: _safe_judge(judge, r), results))
        for r, (flag, err) in zip(results, verdicts):
            r["stopped"], r["subq_error"] = flag, err
        wrong = [r for r in results if not r["correct"]]
        stopped = [r for r in results if r["stopped"]]
        caught = sum(r["stopped"] for r in wrong)
        # v2: learn only from failures the checks confirm; SubQ-only flags produced lessons that hurt validation
        learn_from = [r for r in results if not r["correct"]]
        entry = {"round": rnd, "train_acc": score(results), "train_wrong": len(wrong), "subq_stopped": len(stopped),
                 "subq_caught_wrong": caught, "subq_false_alarms": len(stopped) - caught, "candidates": []}
        print(f"[{name}] round {rnd}: train {score(results):.1%}; SubQ stopped {len(stopped)} "
              f"(caught {caught}/{len(wrong)} wrong, {len(stopped) - caught} false alarms)", flush=True)

        cands = []
        new = reflect(what, current, learn_from) if learn_from else []
        if new:
            c = copy.deepcopy(current)
            c["lessons"] = (c.get("lessons", []) + new)[-8:]
            cands.append(("lessons", c, new))
        if rnd == 1:  # also try the other Meta model for this domain
            other = next(m for m in MODELS if m != (current.get("model") or (MODELS[0] if name == "code" else MODELS[1])))
            c = copy.deepcopy(current)
            c["model"] = other
            cands.append((f"model -> {other}", c, []))

        best = None
        for kind, cand, lessons in cands:
            v = score(run_set(solve, val, cand, workers))
            entry["candidates"].append({"change": kind, "lessons": lessons, "val": v})
            print(f"[{name}]   candidate {kind}: validation {v:.1%} (current {cur_val:.1%})", flush=True)
            if v > cur_val and (best is None or v > best[0]):
                best = (v, kind, cand)
        if best:
            current, cur_val = best[2], best[0]
            pb = playbook.load()
            pb["domains"][name] = current
            pb["version"] += 1
            pb["history"].append({"version": pb["version"], "domain": name, "change": best[1], "val": best[0],
                                  "at": int(time.time())})
            playbook.save(pb)
            entry["adopted"] = best[1]
            print(f"[{name}]   ADOPTED {best[1]} -> playbook v{pb['version']}", flush=True)
        else:
            entry["adopted"] = None
            print(f"[{name}]   no candidate beat the current playbook: rolled back", flush=True)
        log["rounds"].append(entry)

    first = score(run_set(solve, test, start, workers))
    final = score(run_set(solve, test, current, workers)) if current != start else first
    log.update(test_first=first, test_final=final, final=current, minutes=round((time.time() - t0) / 60, 1))
    print(f"[{name}] HELD-OUT TEST: first playbook {first:.1%} -> final playbook {final:.1%}", flush=True)
    return log


def _safe_judge(judge, r):
    try:
        return judge(r)
    except Exception as e:  # noqa: BLE001
        return False, f"judge unavailable: {str(e)[:60]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", choices=list(DOMAINS), required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--train-batch", type=int, default=0)
    a = ap.parse_args()
    os.environ["RECEIPTS_TRIAL"] = "mbpp" if a.domain == "code" else "decisions"  # reuse cached v0 answers
    OUT.mkdir(parents=True, exist_ok=True)
    batch = a.train_batch or (70 if a.domain == "code" else 20)
    log = improve(a.domain, a.rounds, a.workers, batch)
    (OUT / f"{a.domain}.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
