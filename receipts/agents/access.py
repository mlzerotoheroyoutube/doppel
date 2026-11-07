"""Who may do what. The agent is public on ASI:One, so anything that writes to GitHub,
runs code from a repository, or reads the owner's data is owner-only.

* The owner is the handle in RECEIPTS_OWNER (e.g. "sandeep").
* A chat channel becomes the owner's by `link <owner> <code>`, where the code is the
  secret RECEIPTS_LINK_CODE from .env. HTTP calls carry RECEIPTS_API_TOKEN and must use the
  owner's handle.
* Everyone else is a guest: demo PRs, explanations, and a static review of public PR links
  (their code is never executed), with a request budget.
"""

from __future__ import annotations

import hmac
import os
import time

from . import memory

GUEST_REQUESTS_PER_HOUR = int(os.environ.get("RECEIPTS_GUEST_PER_HOUR", "12"))


def owner() -> str:
    return os.environ.get("RECEIPTS_OWNER", "").strip().lower()


def is_owner(storage, channel: str) -> bool:
    o = owner()
    return bool(o) and memory.canonical(storage, channel).lower() == o


def check_link_code(code: str) -> bool:
    secret = os.environ.get("RECEIPTS_LINK_CODE", "")
    return bool(secret) and hmac.compare_digest(code.strip().encode(), secret.encode())


def guest_allowed(storage, channel: str) -> bool:
    """Sliding one-hour budget per guest channel, so a stranger can't run up the model bill."""
    key = f"budget:{channel}"
    now = time.time()
    recent = [t for t in (storage.get(key) or []) if now - t < 3600]
    if len(recent) >= GUEST_REQUESTS_PER_HOUR:
        storage.set(key, recent)
        return False
    storage.set(key, recent + [now])
    return True


GUEST_HELP = """I'm **Receipts**: I check whether AI-written pull requests really do what they claim.

As a guest you can:
- `demo list`, then `demo i1-bad-hardcoded`: watch me catch a cheating PR
- paste a public GitHub PR link: I'll review it statically (tamper rules + a SubQ judgment of the diff).
  For safety I never run a stranger's code.
- ask me what I do and how

Fixing issues, watching repos, opening PRs and planning are for my owner."""
