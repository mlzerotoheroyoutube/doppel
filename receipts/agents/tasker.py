"""Muse plans the work: from a free-form request ("fix all the issues in recipts-demo-store",
"do them one by one", "just the shipping bug"), pick the repo, then the issues and their order.

Two Meta calls: the first picks the repo from the user's own repos, the second reads that repo's
open issues and PRs and decides what to work on. Nothing here is pattern-matched.
"""

from __future__ import annotations

import json

from .. import github_ops, llm

SYSTEM = """You plan engineering work for Doppel, an agent that fixes GitHub issues and opens a PR for each. \
Read the user's request in context and decide what to do. Reply only with JSON."""

PICK_REPO = """# User's request
{message}

# Their repos (owner/name: description)
{repos}

# Repo they worked on last
{last}

Which repo does the request mean? Allow for typos and shorthand. If the request names no repo,
it continues earlier work: use the last repo. Ask only if it names none and there is no last repo.
Reply with: {{"repo": "<owner/name from the list, or empty>", "stop": <true if they want to stop queued work>,
"question": "<a short question to ask if the repo is unclear, else empty>"}}"""

PICK_ISSUES = """# User's request
{message}

# Open issues in {repo}
{issues}

# Open pull requests in {repo}
{prs}

Decide which issues to work on now, in a sensible order (dependencies and quick wins first).
Leave out issues that already have an open PR fixing them, or that the request excludes; say why.
Reply with: {{"issues": [<issue numbers, in order>], "skipped": [{{"number": <n>, "reason": "<short>"}}],
"note": "<one sentence to the user about the plan>"}}"""


def _json(reply: str) -> dict:
    text = llm.extract_block(reply, "json") if "```" in reply else reply
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])


def plan(message: str, last_repo: str = "") -> dict:
    """Return {"repo", "issues": [{"number", "title"}], "skipped", "note"} or {"stop": True} or {"question"}."""
    repos = github_ops.list_repos(50)
    listing = "\n".join(f"- {r.get('nameWithOwner') or r.get('name')}: {(r.get('description') or '')[:80]}" for r in repos)
    first = _json(llm.complete(SYSTEM, PICK_REPO.format(message=message, repos=listing, last=last_repo or "(none)"),
                               effort="low", role="planner"))
    if first.get("stop"):
        return {"stop": True}
    repo = (first.get("repo") or "").strip()
    known = {(r.get("nameWithOwner") or "").lower(): r.get("nameWithOwner") for r in repos}
    if repo.lower() not in known:
        return {"question": first.get("question") or "Which repo should I work on?"}
    repo = known[repo.lower()]

    issues = github_ops.list_issues(repo)
    if not issues:
        return {"repo": repo, "issues": [], "skipped": [], "note": f"{repo} has no open issues."}
    prs = github_ops.list_prs(repo)
    second = _json(llm.complete(SYSTEM, PICK_ISSUES.format(
        message=message, repo=repo,
        issues="\n".join(f"- #{i['number']} {i['title']} (labels: {', '.join(lb['name'] for lb in i.get('labels', [])) or 'none'})"
                         for i in issues),
        prs="\n".join(f"- PR #{p['number']} {p['title']}" for p in prs) or "(none)"), effort="medium", role="planner"))
    by_num = {i["number"]: i for i in issues}
    chosen = [by_num[int(n)] for n in second.get("issues", []) if str(n).isdigit() and int(n) in by_num]
    return {"repo": repo, "issues": [{"number": i["number"], "title": i["title"]} for i in chosen],
            "skipped": [s for s in second.get("skipped", []) if isinstance(s, dict)],
            "note": second.get("note", "")}
