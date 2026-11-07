"""GitHub actions the agent can take from chat, through the signed-in `gh` CLI.

Reads work on any public repo. Writes (new issue, new repo) only touch repos owned by
the signed-in account, and creating a repo needs the user's explicit confirmation in chat.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import llm, sandbox
from .publish import GIT_AUTH, PublishError, _run

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def me() -> str:
    return _run(["gh", "api", "user", "--jq", ".login"])


def own_repo(full: str) -> bool:
    return full.split("/")[0].lower() == me().lower()


def list_repos(limit: int = 15) -> list[dict]:
    out = _run(["gh", "repo", "list", "--limit", str(limit), "--json", "nameWithOwner,description,updatedAt,isPrivate"])
    return json.loads(out)


def list_prs(full: str) -> list[dict]:
    out = _run(["gh", "pr", "list", "-R", full, "--state", "open", "--limit", "20",
                "--json", "number,title,author,url,headRefOid,statusCheckRollup"])
    return json.loads(out)


def list_issues(full: str) -> list[dict]:
    out = _run(["gh", "issue", "list", "-R", full, "--state", "open", "--limit", "20",
                "--json", "number,title,labels,url"])
    return json.loads(out)


def new_issue(full: str, title: str, body: str) -> str:
    if not own_repo(full):
        raise PublishError(f"I only open issues in your own repos ({me()}/…)")
    return _run(["gh", "issue", "create", "-R", full, "--title", title[:200], "--body", body or title])


# ---------------------------------------------------------------- new project

SCAFFOLD_SYSTEM = """You are a senior engineer starting a small, clean Python project. Keep it \
small (2 to 4 source files), standard library only, with a pytest suite that passes. Put the \
behavior rules in the main module's docstring. Reply only with JSON."""

SCAFFOLD_PROMPT = """Project idea: {idea}
Package name: {pkg}

Create the first version. Leave out the features listed as issues; they are future work for an
AI coding agent, so the starter code must NOT implement them yet.

Reply with one JSON object in a ```json block:
{{"description": "<one line>",
  "files": {{"README.md": "...", "{pkg}/__init__.py": "...", "{pkg}/<module>.py": "...",
             "tests/test_<module>.py": "..."}},
  "issues": [{{"title": "...", "body": "<what to build, exact expected behavior and examples>"}},
             ... 3 issues, from easy to hard]}}"""


def _json(reply: str) -> dict:
    text = llm.extract_block(reply, "json") if "```" in reply else reply
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])


def scaffold(idea: str, pkg: str) -> dict:
    """Ask Meta for a starter project and check its own tests pass. One retry."""
    feedback = ""
    for _ in range(2):
        data = _json(llm.complete(SCAFFOLD_SYSTEM, SCAFFOLD_PROMPT.format(idea=idea, pkg=pkg) + feedback,
                                  effort="medium", role="implementer"))
        work = Path(tempfile.mkdtemp(prefix="receipts-new-")) / "project"
        for rel, content in (data.get("files") or {}).items():
            path = (work / rel).resolve()
            if work.resolve() not in path.parents:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        run = sandbox.run_pytest(work)
        if run.green:
            data["dir"] = str(work)
            data["tests_passed"] = len(run.passed)
            return data
        feedback = f"\n\nYour last version's tests failed:\n{run.output[-1500:]}\nFix it."
    raise PublishError("the starter project's own tests didn't pass after two tries")


def create_project(name: str, idea: str) -> dict:
    """Create a public repo under the signed-in account, push a Meta-scaffolded project whose
    tests pass, and file its starter issues. Returns {"url", "issues": [urls], "tests_passed"}."""
    if not NAME.match(name):
        raise PublishError("repo names can use letters, digits, '.', '_' and '-'")
    owner = me()
    pkg = re.sub(r"\W", "_", name.lower()).strip("_") or "project"
    data = scaffold(idea, pkg)
    work = Path(data["dir"])
    (work / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    (work / "LICENSE").write_text("MIT License\n")
    _run(["git", "init", "-q", "-b", "main"], cwd=work)
    _run(["git", "add", "-A"], cwd=work)
    _run(["git", "commit", "-q", "-m", f"Start {name}: {data.get('description', idea)[:60]}\n\n"
                                        "Scaffolded by the Receipts agent (Meta), tests passing."], cwd=work)
    _run(["gh", "repo", "create", f"{owner}/{name}", "--public", "--description",
          (data.get("description") or idea)[:300]])
    _run(["git", "remote", "add", "origin", f"https://github.com/{owner}/{name}.git"], cwd=work)
    _run(["git", *GIT_AUTH, "push", "-q", "-u", "origin", "main"], cwd=work)
    issue_urls = []
    for issue in (data.get("issues") or [])[:3]:
        issue_urls.append(new_issue(f"{owner}/{name}", issue.get("title", "Next feature"), issue.get("body", "")))
    return {"url": f"https://github.com/{owner}/{name}", "issues": issue_urls,
            "tests_passed": data["tests_passed"], "description": data.get("description", "")}


def repo_exists(full: str) -> bool:
    proc = subprocess.run(["gh", "repo", "view", full, "--json", "name"], capture_output=True, text=True, timeout=30)
    return proc.returncode == 0
