"""Turn what a user typed into (base dir, head dir, issue text, title).

Supports public GitHub pull requests and the built-in benchmark PRs.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MAX_REPO_KB = 50_000        # GitHub reports repo size in KB
MAX_CHANGED_FILES = 60
PR_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
LINKED_ISSUE = re.compile(r"(?i)\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)")


@dataclass
class Target:
    label: str
    base: Path
    head: Path
    issue: str
    spec_source: str  # where the spec came from, shown to the user
    gh: dict | None = None  # owner, repo, number, sha when the target is a GitHub PR


class TargetError(Exception):
    pass


def _gh_api(path: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com{path}", headers=gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        raise TargetError(f"GitHub API call {path} failed: {e}") from e


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise TargetError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _export(repo: Path, sha: str, dest: Path) -> None:
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", sha], cwd=repo, capture_output=True, timeout=120)
    if archive.returncode != 0:
        raise TargetError(f"could not export {sha[:8]}")
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)


def _project_root(tree: Path) -> Path:
    """The repo root, or the single subfolder that holds the Python project."""
    if (tree / "tests").is_dir() or list(tree.glob("test_*.py")):
        return tree
    candidates = [d for d in tree.iterdir() if d.is_dir() and (d / "tests").is_dir()]
    return candidates[0] if len(candidates) == 1 else tree


def from_github(url: str) -> Target:
    m = PR_URL.search(url)
    if not m:
        raise TargetError("that doesn't look like a GitHub pull request link")
    owner, repo, num = m.group(1), m.group(2), m.group(3)
    pr = _gh_api(f"/repos/{owner}/{repo}/pulls/{num}")
    if (pr.get("base", {}).get("repo", {}).get("size") or 0) > MAX_REPO_KB:
        raise TargetError(f"that repository is over {MAX_REPO_KB // 1000} MB; I only check small and mid-size repos")
    if (pr.get("changed_files") or 0) > MAX_CHANGED_FILES:
        raise TargetError(f"that PR changes {pr['changed_files']} files; I check PRs of up to {MAX_CHANGED_FILES} files")
    body = pr.get("body") or ""
    if (linked := LINKED_ISSUE.search(body)):
        issue = _gh_api(f"/repos/{owner}/{repo}/issues/{linked.group(1)}")
        spec = f"## #{issue['number']} {issue['title']}\n\n{issue.get('body') or ''}"
        source = f"issue #{issue['number']} (written before the PR)"
    else:
        spec = f"## {pr['title']}\n\n{body}"
        source = "the PR description (no linked issue, so the spec came from the PR's author)"

    work = Path(tempfile.mkdtemp(prefix="receipts-gh-"))
    clone = work / "repo"
    _git("clone", "--quiet", "--filter=blob:none", f"https://github.com/{owner}/{repo}.git", str(clone))
    _git("fetch", "--quiet", "origin", f"pull/{num}/head", cwd=clone)
    base_sha = _git("merge-base", pr["base"]["sha"], "FETCH_HEAD", cwd=clone).strip()
    head_sha = _git("rev-parse", "FETCH_HEAD", cwd=clone).strip()
    _export(clone, base_sha, work / "base")
    _export(clone, head_sha, work / "head")
    base, head = _project_root(work / "base"), _project_root(work / "head")
    if not any(base.rglob("*.py")):
        raise TargetError("that repository has no Python code; Receipts checks Python projects tested with pytest")
    head = work / "head" / base.relative_to(work / "base")
    return Target(f"{owner}/{repo}#{num} · {pr['title']}", base, head, spec, source,
                  {"owner": owner, "repo": repo, "number": int(num), "sha": head_sha})


def from_demo(case_id: str) -> Target:
    from benchmark.cases import CASES
    from benchmark.run import STORE, build_head
    from receipts.__main__ import read_issue

    case = next((c for c in CASES if c["id"] == case_id), None)
    if case is None:
        raise TargetError(f"no demo PR called {case_id!r}")
    issue = read_issue(str(STORE / "ISSUES.md"), case["issue"])
    return Target(f"demo PR `{case_id}` · {case['title']}", STORE, build_head(case), issue,
                  f"demo_store issue #{case['issue']}")


def demo_ids() -> list[tuple[str, str]]:
    from benchmark.cases import CASES
    return [(c["id"], c["title"]) for c in CASES]


# ---------------------------------------------------------------- issues and watching

ISSUE_REF = re.compile(r"issue\s*#?(\d+)(?:\s+(?:in|on|of)\s+([\w.-]+)/([\w.-]+))?", re.I)
REPO_REF = re.compile(r"\b([\w.-]+)/([\w.-]+)\b")


_TOKEN: list[str] = []


def _token() -> str:
    """GITHUB_TOKEN, else the signed-in `gh` CLI's token (higher rate limit than anonymous)."""
    import os
    if not _TOKEN:
        tok = os.environ.get("GITHUB_TOKEN", "")
        if not tok:
            try:
                tok = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                tok = ""
        _TOKEN.append(tok)
    return _TOKEN[0]


def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "receipts-verifier"}
    if tok := _token():
        h["Authorization"] = f"Bearer {tok}"
    return h


def issue_target(text: str) -> tuple[str, Path, str, dict]:
    """(label, base project dir, issue text, repo info) for 'fix issue 3 in owner/repo' or
    'fix demo issue 1'. Repo info is {} for the demo, else owner/repo/number/subdir."""
    m = ISSUE_REF.search(text)
    if not m:
        raise TargetError("say which issue, e.g. `fix demo issue 1` or `fix issue 3 in owner/repo`")
    num = int(m.group(1))
    if m.group(2) is None:
        from benchmark.run import STORE
        from receipts.__main__ import read_issue
        try:
            return f"demo issue #{num}", STORE, read_issue(str(STORE / "ISSUES.md"), num), {}
        except SystemExit as e:
            raise TargetError(str(e)) from e
    owner, repo = m.group(2), m.group(3)
    issue = _gh_api(f"/repos/{owner}/{repo}/issues/{num}")
    work = Path(tempfile.mkdtemp(prefix="receipts-fix-"))
    _git("clone", "--quiet", "--depth", "1", f"https://github.com/{owner}/{repo}.git", str(work / "repo"))
    tree = work / "tree"
    _export(work / "repo", "HEAD", tree)
    spec = f"## #{issue['number']} {issue['title']}\n\n{issue.get('body') or ''}"
    base = _project_root(tree)
    subdir = "" if base == tree else base.relative_to(tree).as_posix()
    info = {"owner": owner, "repo": repo, "number": num, "subdir": subdir, "title": issue["title"]}
    return f"{owner}/{repo} issue #{num}", base, spec, info


def open_prs(owner: str, repo: str) -> list[dict]:
    """[{number, title, sha, url}] for open PRs, newest first."""
    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=20",
                                 headers=gh_headers())
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return [{"number": p["number"], "title": p["title"], "sha": p["head"]["sha"], "url": p["html_url"]} for p in data]
