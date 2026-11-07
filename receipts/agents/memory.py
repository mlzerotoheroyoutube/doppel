"""What Receipts remembers about each user, kept in the agent's storage.

* policy: strict mode (WARN counts as BLOCK) and rules the user muted
* history: every verdict it gave them
* feedback: when they say a verdict was wrong, which rules it blamed

Feedback changes behavior. When a user has marked the same rule wrong twice,
that rule only warns for them from then on, and the report says so. The user
can always restore it with "unmute T00x".
"""

from __future__ import annotations

import time
from collections import Counter

from ..models import Receipt

DOWNGRADE_AFTER = 2


def _blank() -> dict:
    return {"policy": {"strict": False, "muted": []}, "history": [], "feedback": [],
            # personal layer: what the user told us about their day and their work
            "targets": "", "schedule": "", "plan": {},
            "repos": {},     # "owner/repo" -> {"seen": {"<pr number>": "<head sha>"}}
            "inbox": [],     # verdicts the watcher produced
            "digest": []}    # verdicts held back during quiet hours


def load(storage, user: str) -> dict:
    profile = storage.get(f"user:{user}")
    blank = _blank()
    if not isinstance(profile, dict):
        return blank
    for k, v in blank.items():
        profile.setdefault(k, v)
    return profile


def save(storage, user: str, profile: dict) -> None:
    storage.set(f"user:{user}", profile)
    users = storage.get("users") or []
    if user not in users:
        storage.set("users", users + [user])


def all_users(storage) -> list[str]:
    return storage.get("users") or []


def learned_downgrades(profile: dict) -> set[str]:
    counts = Counter(rule for fb in profile["feedback"] if fb["verdict_was"] == "wrong" for rule in fb["rules"])
    return {rule for rule, n in counts.items() if n >= DOWNGRADE_AFTER}


def apply_policy(profile: dict, receipts: list[Receipt]) -> tuple[list[Receipt], list[str]]:
    """Adjust severities for this user. Returns (receipts, notes about what changed)."""
    notes = []
    muted = set(profile["policy"]["muted"])
    learned = learned_downgrades(profile) - muted
    for r in receipts:
        if r.rule in muted and r.severity != "info":
            r.severity = "info"
            notes.append(f"`{r.rule}` is muted for you, so it's only a note.")
        elif r.rule in learned and r.severity == "block":
            r.severity = "warn"
            notes.append(f"You've marked `{r.rule}` wrong {DOWNGRADE_AFTER}+ times, so for you it warns instead of blocking.")
        elif profile["policy"]["strict"] and r.severity == "warn":
            r.severity = "block"
            notes.append(f"Strict mode is on, so `{r.rule}` blocks.")
    return receipts, sorted(set(notes))


def record(profile: dict, label: str, decision: str, rules: list[str]) -> None:
    profile["history"].append({"target": label, "decision": decision, "rules": rules, "at": int(time.time())})
    profile["history"] = profile["history"][-50:]


def give_feedback(profile: dict, wrong: bool) -> str:
    if not profile["history"]:
        return "There's no verdict to give feedback on yet."
    last = profile["history"][-1]
    profile["feedback"].append({"target": last["target"], "verdict_was": "wrong" if wrong else "right",
                                "rules": last["rules"], "at": int(time.time())})
    if not wrong:
        return f"Noted: the {last['decision']} on {last['target']} was right."
    learned = learned_downgrades(profile)
    now = [r for r in last["rules"] if r in learned]
    msg = f"Noted: you think the {last['decision']} on {last['target']} was wrong (rules: {', '.join(last['rules']) or 'none'})."
    if now:
        msg += f" From now on {', '.join(now)} will warn instead of block for you. Say `unmute {now[0]}` to undo."
    return msg
