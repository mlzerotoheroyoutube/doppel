"""The role lens: review a change the way *this* person would.

Meta reads the diff from the owner's role, focus areas and standards and raises concrete concerns.
SubQ then decides, concern by concern, whether each is real given the diff and the evidence.
Only confirmed concerns become receipts (L001, a warning; strict mode turns warnings into blocks).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .models import Receipt

SYSTEM = """You review code changes as a specific engineer, through their role and what they care about. \
Raise only concrete, checkable concerns that this person would actually raise about THIS diff. \
No generic advice, no style nits, no praise. Reply only with JSON."""

PROMPT = """# Reviewer
Role: {role}
Cares about: {focus}
Their standards: {standards}

# Issue being fixed
{issue}

# The change
```diff
{diff}
```

# What automated checks already found
{evidence}

List at most 4 concerns this reviewer would raise about this change, most important first.
Each must point at specific code in the diff. If there are none, return an empty list.
Reply with: {{"concerns": [{{"title": "<one sentence>", "where": "<file and code it refers to>",
"why": "<why it matters to this reviewer>"}}]}}"""


def _json(reply: str) -> dict:
    text = llm.extract_block(reply, "json") if "```" in reply else reply
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])


def review(persona: dict, issue: str, diff: str, evidence: str) -> list[Receipt]:
    role = (persona or {}).get("role", "").strip()
    if not role or not diff.strip():
        return []
    reply = llm.complete(SYSTEM, PROMPT.format(
        role=role, focus=persona.get("focus") or "(not stated)", standards=persona.get("standards") or "(not stated)",
        issue=issue.strip()[:3000], diff=diff[:30_000], evidence=evidence[:4000]), effort="medium", role="planner")
    try:
        concerns = [c for c in _json(reply).get("concerns", []) if isinstance(c, dict) and c.get("title")][:4]
    except (ValueError, json.JSONDecodeError):
        return []

    def confirm(c: dict) -> tuple[dict, float]:
        state = (f"# Reviewer role\n{role}; cares about: {persona.get('focus', '')}\n\n# Change\n```diff\n{diff[:30_000]}\n```"
                 f"\n\n# Concern raised\n{c['title']}\nWhere: {c.get('where', '')}\nWhy: {c.get('why', '')}")
        try:
            a = llm.decide(state, {"real": {
                "type": "noul", "instructions": "Is this concern real and supported by the code in the change?",
                "criteria": {"true": "The diff really has this problem", "false": "Speculative, generic, or not in this diff"}}})
            return c, float(a["real"]["noul"])
        except Exception:  # noqa: BLE001 - no SubQ: don't report unconfirmed concerns
            return c, 0.0

    with ThreadPoolExecutor(4) as pool:
        judged = list(pool.map(confirm, concerns))
    return [Receipt("lens", "L001", "warn", f"As a {role}: {c['title'].rstrip('.')}.",
                    f"{c.get('why', '')} (SubQ confirmed this concern: p = {p:.2f})",
                    evidence=c.get("where", ""))
            for c, p in judged if p >= 0.6]
