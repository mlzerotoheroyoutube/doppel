"""Meta proposes, SubQ picks.

Two Meta models attempt the same task independently. Deterministic evidence scores each
attempt (the visible example plus Meta's hidden exam, written from the task alone). A clear
winner ships without asking anyone. When the evidence can't separate them, SubQ chooses which
attempt ships. Nothing is ever held back, so SubQ can only add right answers, never remove them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import blind, llm, loop, sandbox

PROPOSERS = [("A", "muse-wonder-1.0"), ("B", "muse-spark-1.3")]


@dataclass
class Attempt:
    label: str
    model: str
    workdir: str = ""
    code: str = ""
    exam_passed: int = 0
    exam_total: int = 0
    error: str = ""


@dataclass
class Selection:
    chosen: str
    how: str                     # only_one | evidence | subq | agree
    attempts: list[Attempt] = field(default_factory=list)
    subq: dict = field(default_factory=dict)

    def winner(self) -> Attempt:
        return next(a for a in self.attempts if a.label == self.chosen)


def _attempt(label: str, model: str, issue: str, base: Path, exam_dir: Path | None, dom: dict) -> Attempt:
    a = Attempt(label, model)
    work = sandbox.copy_project(base)
    try:
        _, files = loop.implement(issue, work, [], dom={**dom, "model": model})
        loop.apply_files(work, files)
        a.workdir = str(work)
        a.code = "\n\n".join(f"# {rel}\n{(work / rel).read_text()}" for rel in files if (work / rel).exists())[-6000:]
        if exam_dir is not None:
            run = sandbox.run_pytest(sandbox.copy_project(work), exam_dir, isolated=True, timeout=60)
            a.exam_passed, a.exam_total = len(run.passed), len(run.results)
    except Exception as e:  # noqa: BLE001
        a.error = str(e)[:200]
    return a


def propose_and_pick(issue: str, base: Path, dom: dict | None = None) -> Selection:
    from concurrent.futures import ThreadPoolExecutor
    dom = dom or {"lessons": []}
    try:
        exam_dir = blind.materialize(blind.write_tests(issue, base))
    except Exception:  # noqa: BLE001 - no exam: evidence is just "it ran"
        exam_dir = None
    with ThreadPoolExecutor(len(PROPOSERS)) as pool:
        attempts = list(pool.map(lambda p: _attempt(p[0], p[1], issue, base, exam_dir, dom), PROPOSERS))
    ok = [a for a in attempts if not a.error]
    if len(ok) <= 1:
        return Selection((ok or attempts)[0].label, "only_one", attempts)
    best = max(a.exam_passed for a in ok)
    top = [a for a in ok if a.exam_passed == best]
    if len(top) == 1:
        return Selection(top[0].label, "evidence", attempts)
    # Evidence can't separate them: SubQ picks which attempt ships.
    state = f"# Task\n{issue.strip()[:4000]}\n\n" + "\n\n".join(
        f"# Attempt {a.label} (passes {a.exam_passed}/{a.exam_total} independent checks)\n```python\n{a.code}\n```"
        for a in top)
    try:
        ans = llm.decide(state, {"ship": {
            "type": "choice", "instructions": "Which attempt correctly solves the task for every input it describes?",
            "criteria": {a.label: f"Attempt {a.label}" for a in top}}})
        return Selection(ans["ship"]["choice"], "subq", attempts, ans["ship"])
    except Exception as e:  # noqa: BLE001 - SubQ down: keep the primary proposer
        return Selection(top[0].label, "evidence", attempts, {"error": str(e)[:100]})


# ---------------------------------------------------------------- v2: evidence first, SubQ second

CONFIDENT = 0.8  # SubQ only overrides the primary attempt when at least this sure


def propose_v2(issue: str, base: Path, dom: dict | None = None) -> Selection:
    """1. The primary model (Muse Wonder) attempts the task.
    2. Clean evidence (hidden exam fully passes) ships immediately; nobody second-guesses it.
    3. Otherwise Muse Spark also attempts it; strictly better evidence wins.
    4. On a tie, the primary stays unless SubQ is at least CONFIDENT that the other is right."""
    dom = dom or {"lessons": []}
    try:
        exam_dir = blind.materialize(blind.write_tests(issue, base))
    except Exception:  # noqa: BLE001
        exam_dir = None
    a = _attempt(*PROPOSERS[0], issue, base, exam_dir, dom)
    if not a.error and a.exam_total and a.exam_passed == a.exam_total:
        return Selection(a.label, "clean_evidence", [a])
    b = _attempt(*PROPOSERS[1], issue, base, exam_dir, dom)
    ok = [x for x in (a, b) if not x.error]
    if len(ok) <= 1:
        return Selection((ok or [a])[0].label, "only_one", [a, b])
    if a.exam_passed != b.exam_passed:
        win = a if a.exam_passed > b.exam_passed else b
        return Selection(win.label, "better_evidence", [a, b])
    state = f"# Task\n{issue.strip()[:4000]}\n\n" + "\n\n".join(
        f"# Attempt {x.label} (passes {x.exam_passed}/{x.exam_total} independent checks)\n```python\n{x.code}\n```"
        for x in (a, b))
    try:
        ans = llm.decide(state, {"ship": {
            "type": "choice", "instructions": "Which attempt correctly solves the task for every input it describes?",
            "criteria": {"A": "Attempt A", "B": "Attempt B"}}})
        pick, p = ans["ship"]["choice"], float(ans["ship"]["probabilities"].get(ans["ship"]["choice"], 0))
        if pick == "B" and p >= CONFIDENT:
            return Selection("B", "subq_confident", [a, b], ans["ship"])
        return Selection("A", "tie_keep_primary", [a, b], ans["ship"])
    except Exception as e:  # noqa: BLE001
        return Selection("A", "tie_keep_primary", [a, b], {"error": str(e)[:100]})
