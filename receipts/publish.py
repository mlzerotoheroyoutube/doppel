"""Deliver an approved fix: push a branch and open a pull request.

Only runs after SubQ approved the final round and Doppel did not block it.
It never merges. Uses the signed-in `gh` CLI for credentials.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .tamper import py_files

GIT_AUTH = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


class PublishError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise PublishError(f"{' '.join(cmd[:3])} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout.strip()


def changed_files(base: Path, workdir: Path) -> list[str]:
    b, h = py_files(base), py_files(workdir)
    return sorted(rel for rel in h if b.get(rel) != h[rel])


def open_pr(owner: str, repo: str, issue_number: int, base: Path, workdir: Path, subdir: str,
            title: str, body: str) -> str:
    """Push the changed files from `workdir` on a new branch and open a PR. Returns its URL."""
    files = changed_files(base, workdir)
    if not files:
        raise PublishError("the approved change has no file differences to publish")
    info = json.loads(_run(["gh", "api", f"repos/{owner}/{repo}"]))
    default = info["default_branch"]
    branch = f"doppel/fix-issue-{issue_number}-{int(time.time()) % 100000}"

    clone = Path(tempfile.mkdtemp(prefix="receipts-pr-")) / repo
    _run(["git", *GIT_AUTH, "clone", "--quiet", "--depth", "1", "--branch", default,
          f"https://github.com/{owner}/{repo}.git", str(clone)])
    _run(["git", "checkout", "-q", "-b", branch], cwd=clone)
    root = clone / subdir if subdir else clone
    for rel in files:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(workdir / rel, dest)
    _run(["git", "add", "-A"], cwd=clone)
    _run(["git", "commit", "-q", "-m", f"{title}\n\nFixes #{issue_number}. Written by the Doppel work loop; "
                                         "approved by SubQ after Doppel's checks passed."], cwd=clone)
    _run(["git", *GIT_AUTH, "push", "-q", "-u", "origin", branch], cwd=clone)
    if len(body) > 60_000:
        body = body[:60_000] + "\n\n…(truncated)"
    return _run(["gh", "pr", "create", "-R", f"{owner}/{repo}", "--base", default, "--head", branch,
                 "--title", title, "--body", body])


# ---------------------------------------------------------------- reporting back to GitHub

STATE = {"BLOCK": "failure", "WARN": "success", "PASS": "success"}


def report_pr(owner: str, repo: str, number: int, sha: str, decision: str, summary: str, report_md: str) -> None:
    """Set the official 'Doppel's commit status on the PR's head and post the report card."""
    desc = f"{decision}: {summary}"[:139]
    _run(["gh", "api", "-X", "POST", f"repos/{owner}/{repo}/statuses/{sha}",
          "-f", f"state={STATE.get(decision, 'error')}", "-f", "context=Doppel",
          "-f", f"description={desc}",
          "-f", f"target_url=https://github.com/{owner}/{repo}/pull/{number}"])
    body = report_md if len(report_md) < 60_000 else report_md[:60_000] + "\n\n…(truncated)"
    body += f"\n\n<sub>Checked commit `{sha[:7]}` by Doppel · Meta + SubQ · nothing was merged.</sub>"
    proc = subprocess.run(["gh", "pr", "comment", str(number), "-R", f"{owner}/{repo}", "--body-file", "-"],
                          input=body, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise PublishError(f"gh pr comment failed: {proc.stderr.strip()[:300]}")


def comment_issue(owner: str, repo: str, number: int, body: str) -> None:
    proc = subprocess.run(["gh", "issue", "comment", str(number), "-R", f"{owner}/{repo}", "--body-file", "-"],
                          input=body[:60_000], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise PublishError(f"gh issue comment failed: {proc.stderr.strip()[:300]}")


def labeled_issues(owner: str, repo: str, labels: tuple = ("doppel-fix", "receipts-fix")) -> list[dict]:
    seen, out = set(), []
    for label in labels:  # the old label keeps working
        data = json.loads(_run(["gh", "api", f"repos/{owner}/{repo}/issues?state=open&labels={label}&per_page=20"]))
        for i in data:
            if "pull_request" not in i and i["number"] not in seen:
                seen.add(i["number"])
                out.append({"number": i["number"], "title": i["title"]})
    return out
