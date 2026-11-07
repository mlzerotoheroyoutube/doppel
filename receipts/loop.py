"""The work loop: implement → collect evidence → judge → rework.

    implementer (Meta Muse Wonder)  writes or reworks the code
    evidence    (Receipts)          suite, tamper rules, frozen blind exam, planted bugs
    judge       (SubQ)              reads the whole repo, the issue, every round and the evidence
                                    → APPROVE, or REJECT with feedback

Rules that keep it honest:
* The exam (blind tests) is written once from the issue, before round 1, and frozen.
* The implementer never sees the exam. It only gets the judge's feedback, with any
  test code stripped out, so it has to fix the behavior rather than fit the tests.
* A round ships only if Receipts doesn't BLOCK and the judge APPROVES.
* At most `max_rounds` rounds, then it stops and hands the history to the user.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import blind, llm, sandbox
from .tamper import py_files
from .verify import to_markdown, verify

IMPLEMENTER_SYSTEM = """You are a careful software engineer fixing one issue in a small Python \
project. Make the smallest correct change. Keep every documented rule working. Add or update \
tests for the behavior you changed. Never delete, skip or weaken existing tests, and never \
special-case test inputs."""

IMPLEMENTER_PROMPT = """# Issue
{issue}

# Repository (current state)
{repo}

{feedback}
# Reply format
Reply with one JSON object in a ```json block:
{{"summary": "<one sentence>", "files": {{"<relative path>": "<complete new file content>"}}}}
Include only files you change, each with its complete new content."""

# SubQ Snap is a decision model: it reads everything as `state` and answers typed
# questions with probabilities. It writes no text, so it cannot leak the exam.
JUDGE_QUESTIONS = {
    "ship": {"type": "noul",
             "instructions": "Should this change be merged as the fix for the issue?",
             "criteria": {"true": "It resolves the issue for every input the issue and documented rules cover, "
                                  "the evidence shows no blocking problem, and nothing that worked before is broken",
                          "false": "The issue is not fully resolved, or the evidence shows a blocking problem, "
                                   "a regression, test tampering or special-cased inputs"}},
    "problem": {"type": "choice",
                "instructions": "What is the main problem with this change, if any?",
                "criteria": {"none": "No problem; ready to merge",
                             "not_fixed": "The issue's behavior is still wrong",
                             "partial": "Fixes the reported example but misses cases such as quantities, "
                                        "boundaries, rounding or combinations",
                             "regression": "Breaks a documented rule or behavior that worked before",
                             "tampering": "Deletes, skips or weakens tests, or changes test configuration",
                             "hard_coded": "Special-cases the exact inputs or answers from the issue or tests",
                             "broken": "The code does not run or the change could not be applied"}},
    "regressed": {"type": "noul",
                  "instructions": "Did this round undo or break something an earlier round had already fixed?",
                  "criteria": {"true": "An earlier round's fix is lost or broken", "false": "No earlier fix was lost"}},
    "quality": {"type": "score", "instructions": "How safe is this change to merge?",
                "criteria": ["Unsafe", "Needs work", "Good", "Excellent"]},
}
SHIP_THRESHOLD = 0.6

PROBLEM_ADVICE = {
    "not_fixed": "The behavior the issue describes is still wrong.",
    "partial": "The fix handles the reported example but not the general case. Re-read every rule the issue and "
               "the module docstring state (quantities, boundaries, rounding, combinations with other features).",
    "regression": "The change breaks a documented rule that used to work. Keep every existing rule working.",
    "tampering": "Do not delete, skip or weaken tests or change test configuration. Fix the code instead.",
    "hard_coded": "Do not special-case specific inputs or answers. Implement the general rule.",
    "broken": "The change does not run. Return complete, valid file contents.",
}


@dataclass
class Round:
    n: int
    summary: str
    files: list[str]
    diff: str
    receipts_decision: str
    receipts_report: str
    judge: dict
    error: str = ""


@dataclass
class LoopResult:
    status: str                 # shipped | gave_up | failed
    rounds: list[Round] = field(default_factory=list)
    workdir: str = ""
    exam_tests: int = 0

    def to_dict(self) -> dict:
        return {"status": self.status, "workdir": self.workdir, "exam_tests": self.exam_tests,
                "rounds": [asdict(r) for r in self.rounds]}


def repo_snapshot(root: Path, max_chars: int = 400_000) -> str:
    parts = []
    for rel, src in py_files(root).items():
        parts.append(f"### {rel}\n```\n{src}\n```")
    for md in sorted(root.glob("*.md")):
        parts.append(f"### {md.name}\n```\n{md.read_text()}\n```")
    return "\n\n".join(parts)[:max_chars]


def cumulative_diff(base: Path, head: Path) -> str:
    b, h = py_files(base), py_files(head)
    out = []
    for rel in sorted(set(b) | set(h)):
        if b.get(rel) != h.get(rel):
            out += difflib.unified_diff((b.get(rel) or "").splitlines(True), (h.get(rel) or "").splitlines(True),
                                        f"a/{rel}", f"b/{rel}")
    return "".join(out)


def _json(reply: str) -> dict:
    block = llm.extract_block(reply, "json") if "```" in reply else reply
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])


def sanitize_feedback(text: str) -> str:
    """Keep the exam hidden: drop code blocks and anything naming the blind tests."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"\S*test_receipts_blind\S*", "a hidden check", text)
    text = re.sub(r"`?\btest_(issue|contract)_\w+`?", "a hidden check", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def apply_files(workdir: Path, files: dict[str, str]) -> list[str]:
    written = []
    for rel, content in files.items():
        target = (workdir / rel).resolve()
        if workdir.resolve() not in target.parents:
            raise ValueError(f"refusing to write outside the project: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(rel)
    return written


def implement(issue: str, workdir: Path, feedback: list[str]) -> tuple[str, dict[str, str]]:
    fb = ""
    if feedback:
        fb = "# Feedback from the release judge on your earlier attempts\n" + "\n".join(
            f"- Round {i}: {f}" for i, f in enumerate(feedback, 1)) + "\n\n"
    reply = llm.complete(IMPLEMENTER_SYSTEM, IMPLEMENTER_PROMPT.format(
        issue=issue.strip(), repo=repo_snapshot(workdir), feedback=fb), effort="high", role="implementer")
    data = _json(reply)
    files = data.get("files") or {}
    if not isinstance(files, dict) or not files:
        raise ValueError("the implementer returned no file changes")
    return str(data.get("summary", "")), {str(k): str(v) for k, v in files.items()}


def judge(issue: str, base: Path, workdir: Path, verdict, rounds: list[Round]) -> dict:
    """SubQ reads the issue, the whole repo, the diff, the evidence and every earlier round."""
    history = "\n".join(f"- Round {r.n}: Receipts {r.receipts_decision}; judge {r.judge.get('decision')} "
                        f"({r.judge.get('problem', '')}); diff:\n{r.diff[:4000]}" for r in rounds) or "none"
    state = (f"# Issue\n{issue.strip()}\n\n# Evidence from Receipts (deterministic checks and hidden exam)\n"
             f"{to_markdown(verdict)}\n# Change (cumulative diff from the original)\n```diff\n"
             f"{cumulative_diff(base, workdir)[:60_000]}\n```\n\n# Earlier rounds\n{history}\n\n"
             f"# Repository after this round\n{repo_snapshot(workdir, 250_000)}")
    a = llm.decide(state, JUDGE_QUESTIONS, role="judge")
    p_ship = float(a["ship"]["noul"])
    problem = a["problem"]["choice"]
    decision = "APPROVE" if p_ship >= SHIP_THRESHOLD and problem == "none" else "REJECT"
    reasons = [f"P(ship) = {p_ship:.2f} (needs ≥ {SHIP_THRESHOLD})",
               f"Main problem: {problem} (p = {a['problem']['probabilities'].get(problem, 0):.2f}, "
               f"confidence {a['problem'].get('confidence', 0):.2f})",
               f"Merge safety: {a['quality']['score']:.1f} / 3"]
    blocks = [r.title for r in verdict.receipts if r.severity == "block"]
    feedback = " ".join(filter(None, [PROBLEM_ADVICE.get(problem, "") if decision == "REJECT" else "",
                                      ("Evidence: " + " ".join(blocks)) if blocks else ""]))
    return {"decision": decision, "p_ship": p_ship, "problem": problem, "reasons": reasons,
            "feedback": sanitize_feedback(feedback), "regressed_from_earlier_round": a["regressed"]["noul"] >= 0.5,
            "raw": a}


def evidence_only_judge(verdict) -> dict:
    """The 'no SubQ' configuration: ship whenever Receipts doesn't block."""
    blocks = [r.title for r in verdict.receipts if r.severity == "block"]
    ok = verdict.decision != "BLOCK"
    return {"decision": "APPROVE" if ok else "REJECT", "p_ship": 1.0 if ok else 0.0,
            "problem": "none" if ok else "evidence", "reasons": blocks or ["Receipts did not block"],
            "feedback": sanitize_feedback("Evidence: " + " ".join(blocks)) if blocks else "",
            "regressed_from_earlier_round": False}


def run(issue: str, base: Path, max_rounds: int = 3,
        on_event: Callable[[str], None] = lambda s: None, use_subq: bool = True,
        mutants: int = 40) -> LoopResult:
    workdir = sandbox.copy_project(base)
    result = LoopResult("gave_up", workdir=str(workdir))

    on_event(f"Writing the hidden exam with {llm.who('exam')} (from the issue only)…")
    exam = blind.write_tests(issue, base)
    result.exam_tests = exam.count("\ndef test_")
    on_event(f"Exam frozen: {result.exam_tests} tests. The coding agent will never see them.")

    feedback: list[str] = []
    for n in range(1, max_rounds + 1):
        on_event(f"Round {n}: {llm.who('implementer')} is {'reworking' if feedback else 'writing'} the fix…")
        try:
            summary, files = implement(issue, workdir, feedback)
            written = apply_files(workdir, files)
        except Exception as e:  # noqa: BLE001 - a bad round is evidence too
            result.rounds.append(Round(n, "", [], "", "BLOCK", "", {"decision": "REJECT", "reasons": [str(e)],
                                       "problem": "broken", "p_ship": 0.0,
                                       "feedback": f"Your last reply could not be applied: {e}"}, error=str(e)))
            feedback.append(f"Your last reply could not be applied: {e}")
            on_event(f"Round {n}: the attempt couldn't be applied ({e}). Asking for another.")
            continue

        on_event(f"Round {n}: changed {', '.join(written)}. Collecting evidence…")
        verdict = verify(base, workdir, issue, blind_src=exam, judge=use_subq, mutants=mutants)
        report = to_markdown(verdict)
        if use_subq:
            on_event(f"Round {n}: Receipts says {verdict.decision}. {llm.who('judge')} is judging…")
            j = judge(issue, base, workdir, verdict, result.rounds)
        else:
            j = evidence_only_judge(verdict)
        rnd = Round(n, summary, written, cumulative_diff(base, workdir), verdict.decision, report, j)
        result.rounds.append(rnd)

        if verdict.decision != "BLOCK" and j["decision"] == "APPROVE":
            result.status = "shipped"
            on_event(f"Round {n}: ✅ approved by {llm.who('judge')} (P(ship) = {j['p_ship']:.2f}).")
            return result
        fb = j.get("feedback") or "Receipts' evidence still blocks this change."
        feedback.append(fb)
        on_event(f"Round {n}: ❌ rejected by {llm.who('judge')}: {j['problem']}, P(ship) = {j['p_ship']:.2f}. {fb[:300]}")
    return result


def to_markdown_summary(res: LoopResult, label: str) -> str:
    icon = {"shipped": "✅", "gave_up": "✋", "failed": "💥"}[res.status]
    head = {"shipped": "Shipped", "gave_up": "Stopped after the round limit; needs you", "failed": "Failed"}[res.status]
    lines = [f"## {icon} {head} · {label}", "",
             f"Hidden exam: **{res.exam_tests} tests** by {llm.who('exam')} · "
             f"coder: {llm.who('implementer')} · judge: {llm.who('judge')}", ""]
    for r in res.rounds:
        j = r.judge
        lines.append(f"**Round {r.n}**: Receipts {r.receipts_decision} · judge {j.get('decision')}"
                     + (" · ⚠️ regressed an earlier fix" if j.get("regressed_from_earlier_round") else ""))
        if r.summary:
            lines.append(f"- Change: {r.summary}")
        for reason in j.get("reasons", [])[:3]:
            lines.append(f"- {reason}")
        lines.append("")
    if res.rounds and res.rounds[-1].diff:
        lines += ["<details><summary>Final diff</summary>", "", "```diff", res.rounds[-1].diff[:6000], "```",
                  "", "</details>"]
    return "\n".join(lines) + "\n"
