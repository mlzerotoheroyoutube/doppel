"""Talk to the Doppel gate over HTTP from any device that can make a POST request.

    python -m receipts.client --user sandeep "today: ship the calendar fixes, demo at 5pm"
    python -m receipts.client --user sandeep --wait 300 "fix issue 2 in owner/repo"

Environment: RECEIPTS_URL (default http://127.0.0.1:8001) and RECEIPTS_API_TOKEN
(read from the repo's .env if not set). The same two calls work from curl, an
Android or iOS Shortcut, a web page or a chat bot:

    POST /api/chat      {"token", "user", "text"}  -> {"ok", "messages", "error"}
    POST /api/messages  {"token", "user"}          -> new messages since the last call
    GET  /api/health                               -> agent address and model roles
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

from . import llm  # noqa: F401  (loads .env)


def post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser(prog="receipts.client")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--user", required=True, help="your name; use the same one on every device")
    ap.add_argument("--url", default=os.environ.get("RECEIPTS_URL", "http://127.0.0.1:8001"))
    ap.add_argument("--wait", type=int, default=0, help="keep collecting replies for this many seconds")
    args = ap.parse_args()
    token = os.environ.get("RECEIPTS_API_TOKEN", "")

    out = post(args.url, "/api/chat", {"token": token, "user": args.user, "text": args.text}) if args.text \
        else post(args.url, "/api/messages", {"token": token, "user": args.user})
    if not out.get("ok"):
        print("error:", out.get("error"), file=sys.stderr)
        return 1
    for m in out.get("messages", []):
        print(m, "\n")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(3)
        for m in post(args.url, "/api/messages", {"token": token, "user": args.user}).get("messages", []):
            print(m, "\n")
            if any(k in m for k in ("Doppel:", "Shipped", "Stopped after", "Today's plan", "failed")):
                deadline = min(deadline, time.time() + 5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
