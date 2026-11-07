"""The playbook: what Receipts has learned, versioned.

Per domain ("code", "decisions") it holds a list of short lessons Meta reads before working
and an optional Meta model choice. A change is only adopted after it beats the current
version on held-out validation work (benchmark/improve.py); every attempt, adopted or
rolled back, is kept in `history`. SubQ rejections from real use are queued in the inbox
as raw material for the next round of lessons.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "playbook"
FILE = DIR / "playbook.json"
INBOX = DIR / "inbox.jsonl"

EMPTY = {"version": 0, "domains": {"code": {"model": None, "lessons": []},
                                   "decisions": {"model": None, "lessons": []}},
         "history": []}


def load(path: Path = FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return copy.deepcopy(EMPTY)


def save(pb: dict, path: Path = FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pb, indent=1))


def domain(name: str, pb: dict | None = None) -> dict:
    pb = pb if pb is not None else load()
    return pb["domains"].get(name, {"model": None, "lessons": []})


def lessons_text(dom: dict | None) -> str:
    """Prompt section with the adopted lessons; empty when there are none (prompt unchanged)."""
    if not dom or not dom.get("lessons"):
        return ""
    return "# Lessons learned from earlier mistakes (follow them)\n" + "\n".join(
        f"- {lesson}" for lesson in dom["lessons"]) + "\n\n"


def models(dom: dict | None) -> list[str] | None:
    return [dom["model"]] if dom and dom.get("model") else None


def record_rejection(domain_name: str, item: dict) -> None:
    """Queue a SubQ rejection from real use as material for the next improvement round."""
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a") as f:
        f.write(json.dumps({"domain": domain_name, "at": int(time.time()), **item})[:20000] + "\n")
