"""Natural language in, action out. Meta Muse Spark reads a free-form message plus what
the agent remembers about the person, and either turns it into one exact command or
answers directly. The gate then runs that command like any other.
"""

from __future__ import annotations

import json
import re

from .. import llm
from . import targets

SYSTEM = """You are the front desk of Receipts, an engineering teammate that verifies \
AI-written pull requests, fixes GitHub issues in a checked loop (Meta writes the code, \
Receipts collects evidence, SubQ judges), plans the user's day and watches their repos. \
Turn the user's message into exactly one command when one fits; otherwise answer briefly \
and helpfully from the context. Never invent repos, issue numbers or schedule items that \
are not in the context. Reply only with JSON."""

PROMPT = """# Commands you can run (use the exact syntax)
- `today: <targets, meetings, deadlines>`   plan the day (use when the user describes their day)
- `plan`                                    show today's plan
- `watch <owner>/<repo>` / `unwatch <owner>/<repo>`
- `inbox`                                   what the repo watcher found
- `fix issue <n> in <owner>/<repo>`         fix a GitHub issue and open a PR
- `fix demo issue <n>`                      fix a demo store issue (1, 2 or 3)
- `<GitHub PR link>`                        verify a pull request
- `demo list` / `demo <name>`               built-in demo PRs
- `my repos`                                list the user's GitHub repos
- `prs <owner>/<repo>` / `issues <owner>/<repo>` / `check prs in <owner>/<repo>`
- `new issue in <owner>/<repo>: <title> -- <details>`
- `new project <repo-name>: <project idea>`   start a new repo with a tested starter project
- `history`, `me`, `strict on|off`, `mute T00x`, `unmute T00x`, `wrong`, `right`, `link <name>`, `help`

# What you know about this user
{profile}

# Open issues in repos the message mentions or the user watches
{issues}

# Message
{message}

Reply with one JSON object:
{{"command": "<one exact command, or empty>",
  "answer": "<short markdown reply when no command fits, or a one-line preface for the command>"}}"""

REPO = re.compile(r"\b([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\b")


def _profile_text(profile: dict) -> str:
    plan = (profile.get("plan") or {}).get("plan", {})
    blocks = "; ".join(f"{b.get('start')}-{b.get('end')} {b.get('task')}" for b in plan.get("blocks", [])[:12])
    inbox = "; ".join(f"{i['decision']} {i['target']}" for i in profile.get("inbox", [])[-5:])
    hist = "; ".join(f"{h['decision']} {h['target']}" for h in profile.get("history", [])[-5:])
    return "\n".join([
        f"Targets today: {profile.get('targets') or 'not told yet'}",
        f"Schedule: {profile.get('schedule') or 'not told yet'}",
        f"Today's plan: {blocks or 'none yet'}",
        f"Watched repos: {', '.join(profile.get('repos', {})) or 'none'}",
        f"Watcher inbox: {inbox or 'empty'}",
        f"Recent verdicts: {hist or 'none'}",
    ])


def _issues_for(message: str, profile: dict) -> str:
    repos = {f"{a}/{b}" for a, b in REPO.findall(message) if "." not in a and not a.isdigit()}
    repos |= set(profile.get("repos", {}))
    lines = []
    for repo in sorted(repos)[:3]:
        try:
            issues = targets._gh_api(f"/repos/{repo}/issues?state=open&per_page=20")
        except targets.TargetError:
            continue
        lines += [f"- {repo} #{i['number']}: {i['title']}" for i in issues if "pull_request" not in i]
    return "\n".join(lines) or "(none)"


def route(message: str, profile: dict) -> dict:
    """Returns {"command": str, "answer": str}."""
    reply = llm.complete(SYSTEM, PROMPT.format(profile=_profile_text(profile),
                                               issues=_issues_for(message, profile),
                                               message=message.strip()[:4000]),
                         effort="low", role="router")
    text = llm.extract_block(reply, "json") if "```" in reply else reply
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
        except ValueError:
            return {"command": "", "answer": reply.strip()[:2000]}
    return {"command": str(data.get("command") or "").strip().strip("`"), "answer": str(data.get("answer") or "")}


# ---------------------------------------------------------------- full chat answers (Meta)

CHAT_SYSTEM = """You are Receipts, a friendly, expert engineering teammate powered by Meta Muse. \
You can talk about anything: code, product, business, planning, or general questions. Be concise and \
concrete, use markdown, and answer the actual question. You also have tools the user can trigger in plain \
English: verifying GitHub pull requests, fixing issues and opening PRs, watching repos, planning the day, \
listing repos, PRs and issues, and starting new projects. When one of those would help, say so in one line."""


def chat(message: str, profile: dict, history: list[dict]) -> str:
    """A full answer from Meta Muse Spark, with the last few turns and what we remember."""
    turns = "\n".join(f"{h['role']}: {h['text'][:1500]}" for h in history[-8:]) or "(new conversation)"
    prompt = (f"# What you know about this user\n{_profile_text(profile)}\n\n"
              f"# Conversation so far\n{turns}\n\n# User\n{message.strip()[:6000]}")
    return llm.complete(CHAT_SYSTEM, prompt, effort="medium", role="router").strip()
