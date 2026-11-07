"""Personal connections: GitHub (via the gh login), Google Calendar (private iCal link, read-only)
and Gmail (IMAP with a Google app password, read-only).

Settings live in .doppel/connections.json, which is git-ignored and only written by the local
web server. Secrets are never returned to the browser.
"""

from __future__ import annotations

import email
import email.header
import imaplib
import json
import re
import subprocess
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / ".doppel" / "connections.json"


def load() -> dict:
    try:
        return json.loads(FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save(update: dict) -> dict:
    cur = load()
    for k, v in update.items():
        if v is None:
            cur.pop(k, None)
        elif isinstance(v, str) and v.strip():
            cur[k] = v.strip()
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(cur, indent=1))
    try:
        FILE.chmod(0o600)
    except OSError:
        pass
    return cur


def mode(which: str) -> str:
    """'simulated' (the default, for demos), 'real' when credentials are set, or 'off'."""
    c = load()
    m = c.get(f"{which}_mode")
    if m in ("simulated", "real", "off"):
        return m
    if which == "calendar" and c.get("calendar_ics_url"):
        return "real"
    if which == "gmail" and c.get("gmail_address") and c.get("gmail_app_password"):
        return "real"
    return "simulated"


def public_status() -> dict:
    """What the page may show: mode and whether real credentials exist, never the secrets."""
    c = load()
    return {"calendar": {"mode": mode("calendar"), "has_real": bool(c.get("calendar_ics_url"))},
            "gmail": {"mode": mode("gmail"), "has_real": bool(c.get("gmail_address") and c.get("gmail_app_password")),
                      "address": c.get("gmail_address", "")},
            "github": github_status()}


# ---------------------------------------------------------------- simulated data (clearly labelled)

def simulated_events() -> list[dict]:
    now = datetime.now()
    def at(h, m=0):
        return now.replace(hour=h, minute=m, second=0, microsecond=0)
    plan = [(9, 30, 9, 45, "Team standup"), (11, 0, 12, 0, "Design review: Doppel onboarding"),
            (13, 0, 14, 0, "Lunch with judges"), (15, 30, 16, 0, "1:1 with mentor"),
            (17, 0, 17, 30, "Hackathon demo"), (18, 0, 19, 0, "Dinner")]
    return [{"start": at(a, b).strftime("%H:%M"), "end": at(c, d).strftime("%H:%M"), "title": t, "all_day": False}
            for a, b, c, d, t in plan]


def simulated_emails() -> list[dict]:
    return [
        {"from": "GitHub <notifications@github.com>",
         "subject": "[mlzerotoheroyoutube/receipts-demo-store] Fix SAVE10 on multi-item carts (PR #4)", "date": "today"},
        {"from": "GitHub <notifications@github.com>",
         "subject": "[mlzerotoheroyoutube/receipts-demo-calendar] Run failed: tests - main", "date": "today"},
        {"from": "Priya (teammate)", "subject": "Can you review my calendar PR before the demo?", "date": "today"},
        {"from": "Google Calendar", "subject": "Invitation: Hackathon demo @ 5:00pm", "date": "today"},
        {"from": "Cerebral Valley", "subject": "Reminder: submissions close at 5:00 PM PT", "date": "today"},
    ]


# ---------------------------------------------------------------- GitHub

def github_status() -> dict:
    try:
        login = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True,
                               timeout=15).stdout.strip()
        return {"connected": bool(login), "account": login}
    except (OSError, subprocess.SubprocessError):
        return {"connected": False, "account": ""}


# ---------------------------------------------------------------- Google Calendar (iCal)

def _ics_time(value: str) -> datetime | None:
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone()
        if "T" in value:
            return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").astimezone()
        return datetime.strptime(value[:8], "%Y%m%d").astimezone()
    except ValueError:
        return None


def calendar_events(day: date | None = None) -> list[dict]:
    """Today's events: simulated, or from the private iCal link. [{start, end, title, all_day}], local time."""
    if mode("calendar") == "simulated":
        return simulated_events()
    if mode("calendar") == "off":
        raise RuntimeError("Calendar is turned off")
    url = load().get("calendar_ics_url")
    if not url:
        raise RuntimeError("Calendar isn't connected")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "doppel"}), timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold folded lines
    day = day or date.today()
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        fields = {}
        for line in block.split("END:VEVENT")[0].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields.setdefault(k.split(";")[0], (k, v))
        if "DTSTART" not in fields:
            continue
        start = _ics_time(fields["DTSTART"][1])
        end = _ics_time(fields.get("DTEND", fields["DTSTART"])[1])
        if not start:
            continue
        all_day = "VALUE=DATE" in fields["DTSTART"][0]
        if start.date() <= day <= ((end or start) - timedelta(seconds=1)).date() or start.date() == day:
            title = fields.get("SUMMARY", ("", "(busy)"))[1].replace("\\,", ",").strip() or "(busy)"
            out.append({"start": "all day" if all_day else start.strftime("%H:%M"),
                        "end": "" if all_day else (end or start).strftime("%H:%M"), "title": title,
                        "all_day": all_day})
    return sorted(out, key=lambda e: (not e["all_day"], e["start"]))


def calendar_text() -> str:
    ev = calendar_events()
    if not ev:
        return "No events on your calendar today."
    return ("(simulated calendar) " if mode("calendar") == "simulated" else "") + "; ".join(f"{e['start']}" + (f"-{e['end']}" if e["end"] else "") + f" {e['title']}" for e in ev)


# ---------------------------------------------------------------- Gmail (IMAP, read-only)

def _decode(h: str | None) -> str:
    if not h:
        return ""
    return "".join(p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p
                   for p, enc in email.header.decode_header(h))


def unread_emails(limit: int = 15) -> list[dict]:
    if mode("gmail") == "simulated":
        return simulated_emails()[:limit]
    if mode("gmail") == "off":
        raise RuntimeError("Gmail is turned off")
    c = load()
    addr, pw = c.get("gmail_address"), c.get("gmail_app_password")
    if not (addr and pw):
        raise RuntimeError("Gmail isn't connected")
    box = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
    try:
        box.login(addr, pw.replace(" ", ""))
        box.select("INBOX", readonly=True)  # read-only: never marks anything as read
        _, data = box.search(None, "UNSEEN")
        ids = data[0].split()[-limit:]
        out = []
        for i in reversed(ids):
            _, msg = box.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            m = email.message_from_bytes(msg[0][1])
            out.append({"from": _decode(m.get("From"))[:80], "subject": _decode(m.get("Subject"))[:140],
                        "date": (m.get("Date") or "")[:31]})
        return out
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass


def test(which: str) -> dict:
    try:
        if which == "calendar":
            ev = calendar_events()
            return {"ok": True, "detail": f"{len(ev)} event(s) today" + (" (simulated)" if mode("calendar") == "simulated" else "")}
        if which == "gmail":
            mails = unread_emails(5)
            return {"ok": True, "detail": f"{len(mails)} recent unread" + (" (simulated)" if mode("gmail") == "simulated" else "")}
        if which == "github":
            s = github_status()
            return {"ok": s["connected"], "detail": f"signed in as {s['account']}" if s["connected"] else "not signed in"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)[:160]}
    return {"ok": False, "detail": "unknown connection"}
