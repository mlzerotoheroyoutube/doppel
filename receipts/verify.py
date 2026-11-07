"""Combine every check into one verdict with receipts.

Each step is a separate function so the multi-agent runtime (receipts/agents)
can run them on different agents in parallel. `verify()` runs them in order.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from . import blind, mutation, sandbox, tamper
from .models import SEVERITY_ORDER, Receipt, Verdict

MUTATION_WARN_BELOW = 0.5


def _doc(src: str) -> str:
    try:
        fn = ast.parse(src).body[0]
        return (ast.get_docstring(fn) or fn.name).strip().rstrip(".")
    except (SyntaxError, IndexError):
        return ""


# ------------------------------------------------------------------ steps

def suite_step(head_dir: Path) -> tuple[list[Receipt], dict, bool]:
    """The claim: does the PR's own suite pass? Returns (receipts, stats, green)."""
    suite = sandbox.run_pytest(sandbox.copy_project(head_dir))
    stats = {"pr_tests_passed": len(suite.passed), "pr_tests_failed": len(suite.failed)}
    receipts = []
    if not suite.green:
        receipts.append(Receipt("suite", "S001", "block",
                                f"The PR's own tests are red ({len(suite.failed)} failing).",
                                evidence="\n\n".join(f"{r.nodeid}\n{r.message[:400]}" for r in suite.failed[:5])
                                or suite.output[-1500:], repro="python -m pytest -q"))
    return receipts, stats, suite.green


def blind_receipts(outcomes: list[blind.BlindOutcome], err: str) -> tuple[list[Receipt], dict]:
    receipts: list[Receipt] = []
    if err:
        receipts.append(Receipt("blind", "B000", "warn", "Doppel could not run its blind tests.", evidence=err))
    stats = {"blind_tests": len(outcomes),
             "blind_passed_head": sum(o.head == "passed" for o in outcomes),
             "blind_failed_base": sum(o.base == "failed" for o in outcomes)}
    for o in outcomes:
        if o.head == "passed":
            continue
        rule = _doc(o.source)
        repro = f"python -m pytest -q test_receipts_blind.py::{o.name}"
        if o.judged_valid is False:
            receipts.append(Receipt("blind", "B009", "info",
                                    f"Discarded one of Doppel's own tests (`{o.name}`): {o.judge_reason}",
                                    evidence=o.source))
        elif o.base == "passed":
            receipts.append(Receipt("blind", "B001", "block",
                                    f"The PR broke something that worked before: {rule}.",
                                    "This check passed on the code before the PR and fails after it.",
                                    evidence=o.source + "\n\n# failure on the PR:\n" + o.message[:900], repro=repro))
        elif o.kind == "issue":
            receipts.append(Receipt("blind", "B002", "block",
                                    f"The issue is not actually fixed: {rule}.",
                                    "This check of the issue's requirement fails before and after the PR.",
                                    evidence=o.source + "\n\n# failure on the PR:\n" + o.message[:900], repro=repro))
        else:
            receipts.append(Receipt("blind", "B003", "info",
                                    f"Pre-existing problem, not caused by this PR: {rule}.",
                                    evidence=o.message[:600], repro=repro))
    return receipts, stats


def mutation_receipts(pr_mutants: list[mutation.Mutant] | None,
                      blind_mutants: list[mutation.Mutant] | None) -> tuple[list[Receipt], dict]:
    receipts, stats = [], {}
    if pr_mutants:
        killed = sum(bool(m.killed) for m in pr_mutants)
        stats["pr_tests_mutants"] = [killed, len(pr_mutants)]
        if killed / len(pr_mutants) < MUTATION_WARN_BELOW:
            survivors = [m for m in pr_mutants if not m.killed][:4]
            receipts.append(Receipt("mutation", "M001", "warn",
                                    f"The PR's tests missed {len(pr_mutants) - killed} of {len(pr_mutants)} bugs "
                                    "we planted in the changed code, so their passing proves little.",
                                    "Each line below is a bug we planted. The PR's tests still passed with it.",
                                    evidence="\n".join(f"{m.file}:{m.line}  {m.before}   ->   {m.after}"
                                                       for m in survivors)))
    if blind_mutants:
        stats["blind_tests_mutants"] = [sum(bool(m.killed) for m in blind_mutants), len(blind_mutants)]
    return receipts, stats


def decide(receipts: list[Receipt], stats: dict) -> Verdict:
    receipts = sorted(receipts, key=lambda r: SEVERITY_ORDER[r.severity])
    if any(r.severity == "block" for r in receipts):
        decision = "BLOCK"
    elif any(r.severity == "warn" for r in receipts):
        decision = "WARN"
    else:
        decision = "PASS"
    return Verdict(decision, receipts, stats)


# ------------------------------------------------------------------ all in one

def subq_gate(base_dir: Path, head_dir: Path, issue: str, verdict: Verdict) -> Receipt | None:
    """SubQ reads the issue, the diff and all evidence, and votes on shipping. Returns a block receipt
    when it votes no."""
    from . import loop
    from .llm import decide
    state = (f"# Issue\n{issue.strip()}\n\n# Evidence from Doppel\n{to_markdown(verdict)}\n"
             f"# Change\n```diff\n{loop.cumulative_diff(base_dir, head_dir)[:60_000]}\n```\n\n"
             f"# Repository after the change\n{loop.repo_snapshot(head_dir, 250_000)}")
    q = {k: v for k, v in loop.JUDGE_QUESTIONS.items() if k in ("ship", "problem")}
    a = decide(state, q, role="judge")
    p, problem = float(a["ship"]["noul"]), a["problem"]["choice"]
    if p >= loop.SHIP_THRESHOLD and problem == "none":
        return None
    return Receipt("judge", "J001", "block",
                   f"SubQ votes not to ship: {problem.replace('_', ' ')} (P(ship) = {p:.2f}).",
                   loop.PROBLEM_ADVICE.get(problem, ""),
                   evidence=json.dumps(a, indent=1)[:1500])


def verify(base_dir: Path, head_dir: Path, issue: str, *, use_model: bool = True,
           blind_src: str | None = None, mutants: int = 40, out_dir: Path | None = None,
           judge: bool = True, gate: bool = False) -> Verdict:
    receipts, stats, green = suite_step(head_dir)
    receipts += tamper.check(base_dir, head_dir, issue)

    blind_dir = None
    if use_model or blind_src:
        src, outcomes, err = blind.run(issue, base_dir, head_dir, test_src=blind_src, judge=use_model and judge)
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "test_receipts_blind.py").write_text(src)
        blind_dir = blind.materialize(src) if not err else None
        r, s = blind_receipts(outcomes, err)
        receipts += r
        stats.update(s)

    targets = tamper.changed_functions(tamper.py_files(base_dir), tamper.py_files(head_dir))
    stats["changed_functions"] = [f"{f}:{n}" for f, n in targets]
    pr_m = mutation.run(head_dir, targets, limit=mutants) if targets and green else None
    b_m = mutation.run(head_dir, targets, tests=blind_dir, limit=mutants) if targets and green and blind_dir else None
    r, s = mutation_receipts(pr_m, b_m)
    receipts += r
    stats.update(s)
    if gate and use_model:
        if (j := subq_gate(base_dir, head_dir, issue, decide(list(receipts), dict(stats)))) is not None:
            receipts.append(j)
    return decide(receipts, stats)


def to_markdown(v: Verdict, title: str = "") -> str:
    icon = {"BLOCK": "🛑", "WARN": "⚠️", "PASS": "✅"}[v.decision]
    lines = [f"## {icon} Doppel: {v.decision}" + (f" · {title}" if title else ""), ""]
    s = v.stats
    facts = [f"The PR's tests: **{s.get('pr_tests_passed', 0)} passing**"
             + (f", {s['pr_tests_failed']} failing" if s.get("pr_tests_failed") else "")]
    if s.get("blind_tests"):
        facts.append(f"Doppel's blind tests: **{s['blind_passed_head']}/{s['blind_tests']} passing**")
    if "pr_tests_mutants" in s:
        k, n = s["pr_tests_mutants"]
        line = f"Planted bugs caught by the PR's tests: **{k}/{n}**"
        if "blind_tests_mutants" in s:
            bk, bn = s["blind_tests_mutants"]
            line += f" · by Doppel's tests: **{bk}/{bn}**"
        facts.append(line)
    lines += [" · ".join(facts), ""]
    shown = [r for r in v.receipts if r.severity != "info"]
    if not shown:
        lines.append("No problems found. Every claim checked out.")
    for i, r in enumerate(shown, 1):
        tag = "BLOCKS" if r.severity == "block" else "WARNING"
        lines.append(f"### {i}. {r.title}")
        lines.append(f"`{r.rule}` · {tag}" + (f" · `{r.file}`" + (f" line {r.line}" if r.line else "") if r.file else ""))
        if r.detail:
            lines += ["", r.detail]
        if r.evidence:
            lines += ["", "<details><summary>Evidence</summary>", "", "```", r.evidence.strip(), "```", "", "</details>"]
        if r.repro:
            lines += ["", f"Reproduce: `{r.repro}`"]
        lines.append("")
    info = [r for r in v.receipts if r.severity == "info"]
    if info:
        lines += ["<details><summary>Notes</summary>", ""] + [f"- {r.title}" for r in info] + ["", "</details>"]
    return "\n".join(lines) + "\n"
