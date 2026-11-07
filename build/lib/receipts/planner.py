"""Day planning: Meta drafts the plan, SubQ judges it, Meta revises once.

The planner gets what the gate agent remembers about the user:
targets for today, schedule, watched repos, pending PR verdicts and how they
like to be notified.
"""

from __future__ import annotations

import json
from datetime import datetime

from . import llm

PLANNER_SYSTEM = """You are a calm, practical engineering chief of staff. You plan one person's \
working day around their fixed schedule. Never put work inside a meeting. Put the most \
important target first. Keep blocks realistic (25 to 90 minutes). Reply only with JSON."""

PLANNER_PROMPT = """Now: {now}

# Targets for today (from the user)
{targets}

# Fixed schedule (from the user)
{schedule}

# Work waiting in their repos
{inbox}

{feedback}Make today's plan. Reply with one JSON object:
{{"summary": "<two sentences>",
  "blocks": [{{"start": "HH:MM", "end": "HH:MM", "kind": "focus|review|agent|meeting|break",
              "task": "<what>", "why": "<one short reason>"}}],
  "agent_tasks": ["<work the coding agent should do on its own, e.g. fix issue #3 in owner/repo>"],
  "quiet_hours": [["HH:MM", "HH:MM"]],
  "notify_now_if": "<when to interrupt the user instead of waiting for a digest>"}}"""

# SubQ Snap answers typed questions about the plan; each failed check becomes a problem
# the planner must fix.
PLAN_CHECKS = {
    "respects_schedule": ("Does the plan keep every fixed commitment (meetings, deadlines) free of other work?",
                          "Work is scheduled inside a meeting or after a deadline it is needed for"),
    "targets_first": ("Does the plan put the user's stated targets before less important work?",
                      "The stated targets are not prioritized"),
    "fits_time": ("Does the plan fit in the time left today, with realistic block lengths?",
                  "The plan does not fit the time left or has unrealistic blocks"),
    "uses_agent": ("Does the plan hand suitable coding or review work to the agent?",
                   "Work the agent could do on its own is left to the user"),
}


def _json(reply: str) -> dict:
    text = llm.extract_block(reply, "json") if "```" in reply else reply
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])


def make_plan(targets: str, schedule: str, inbox: str, now: datetime | None = None) -> dict:
    """Returns {"plan": {...}, "judge": {...}, "revised": bool}."""
    now_s = (now or datetime.now()).strftime("%A %H:%M")
    args = dict(now=now_s, targets=targets or "(none given)", schedule=schedule or "(none given)",
                inbox=inbox or "(nothing waiting)")
    plan = _json(llm.complete(PLANNER_SYSTEM, PLANNER_PROMPT.format(feedback="", **args),
                              effort="medium", role="planner"))
    verdict = judge_plan(plan, args)
    revised = False
    if verdict["decision"] != "APPROVE":
        fb = "# The plan reviewer rejected your first draft\n" + "\n".join(
            f"- {p}" for p in verdict["problems"]) + "\nFix every problem.\n\n"
        plan = _json(llm.complete(PLANNER_SYSTEM, PLANNER_PROMPT.format(feedback=fb, **args),
                                  effort="medium", role="planner"))
        verdict = judge_plan(plan, args)
        revised = True
    return {"plan": plan, "judge": verdict, "revised": revised}


def judge_plan(plan: dict, args: dict) -> dict:
    state = (f"Now: {args['now']}\nTargets: {args['targets']}\nFixed schedule: {args['schedule']}\n"
             f"Waiting work: {args['inbox']}\n\n# Proposed plan\n```json\n{json.dumps(plan, indent=1)}\n```")
    questions = {name: {"type": "noul", "instructions": q,
                        "criteria": {"true": "Yes, clearly", "false": problem}}
                 for name, (q, problem) in PLAN_CHECKS.items()}
    a = llm.decide(state, questions, role="judge")
    scores = {name: float(a[name]["noul"]) for name in PLAN_CHECKS}
    problems = [PLAN_CHECKS[n][1] + f" (SubQ p = {scores[n]:.2f})" for n in ("respects_schedule", "targets_first",
                                                                            "fits_time") if scores[n] < 0.5]
    return {"decision": "APPROVE" if not problems else "REJECT", "problems": problems, "scores": scores}


def in_quiet_hours(plan: dict, now: datetime | None = None) -> bool:
    hhmm = (now or datetime.now()).strftime("%H:%M")
    return any(a <= hhmm < b for a, b in plan.get("quiet_hours", []) if isinstance(a, str) and isinstance(b, str))


def to_markdown(result: dict) -> str:
    plan, j = result["plan"], result["judge"]
    ok = str(j.get("decision", "")).upper() == "APPROVE"
    lines = [f"## 🗓️ Today's plan · {'approved' if ok else 'flagged'} by {llm.who('judge')}"
             + (" after one revision" if result["revised"] else ""), "", plan.get("summary", ""), ""]
    icons = {"focus": "🎯", "review": "🔍", "agent": "🤖", "meeting": "📅", "break": "☕"}
    for b in plan.get("blocks", []):
        lines.append(f"- **{b.get('start')}–{b.get('end')}** {icons.get(b.get('kind'), '•')} {b.get('task')}"
                     + (f" · _{b['why']}_" if b.get("why") else ""))
    if plan.get("agent_tasks"):
        lines += ["", "**I'll do these on my own:**"] + [f"- {t}" for t in plan["agent_tasks"]]
    if plan.get("quiet_hours"):
        lines += ["", "Quiet hours (digest only): " + ", ".join(f"{a}–{b}" for a, b in plan["quiet_hours"])]
    if not ok and j.get("problems"):
        lines += ["", "**Judge's remaining concerns:**"] + [f"- {p}" for p in j["problems"]]
    lines += ["", f"_Drafted by {llm.who('planner')}, reviewed by {llm.who('judge')}._"]
    return "\n".join(lines) + "\n"
