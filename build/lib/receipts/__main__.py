"""receipts verify --base DIR --head DIR --issue FILE [--issue-number N]
receipts verify --github-pr https://github.com/owner/repo/pull/N [--gate]"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .verify import to_markdown, verify


def read_issue(path: str, number: int | None) -> str:
    text = Path(path).read_text()
    if number is None:
        return text
    parts = re.split(r"(?m)^## ", text)
    for part in parts:
        if part.startswith(f"#{number} "):
            return "## " + part.strip()
    raise SystemExit(f"issue #{number} not found in {path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="receipts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="verify a change")
    v.add_argument("--github-pr", help="a GitHub pull request link; base, head and spec are read from GitHub")
    v.add_argument("--base", type=Path, help="project directory before the change")
    v.add_argument("--head", type=Path, help="project directory after the change")
    v.add_argument("--issue", help="markdown file describing the issue")
    v.add_argument("--issue-number", type=int)
    v.add_argument("--gate", action="store_true", help="SubQ also votes ship / don't ship")
    v.add_argument("--no-model", action="store_true", help="static and mutation checks only")
    v.add_argument("--out", type=Path, default=Path(".receipts"), help="where to write report files")
    v.add_argument("--mutants", type=int, default=40)
    args = ap.parse_args(argv)

    title = ""
    if args.github_pr:
        from .agents.targets import from_github
        t = from_github(args.github_pr)
        base, head, issue, title = t.base, t.head, t.issue, t.label
    else:
        if not (args.base and args.head and args.issue):
            ap.error("give --github-pr, or all of --base, --head and --issue")
        base, head, issue = args.base.resolve(), args.head.resolve(), read_issue(args.issue, args.issue_number)

    verdict = verify(base, head, issue, use_model=not args.no_model, mutants=args.mutants,
                     out_dir=args.out, gate=args.gate)
    args.out.mkdir(parents=True, exist_ok=True)
    md = to_markdown(verdict, title)
    (args.out / "report.md").write_text(md)
    (args.out / "verdict.json").write_text(json.dumps(verdict.to_dict(), indent=2))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):  # GitHub Actions job summary
        with open(summary, "a") as f:
            f.write(md)
    print(md)
    return {"PASS": 0, "WARN": 0, "BLOCK": 1}[verdict.decision]


if __name__ == "__main__":
    sys.exit(main())
