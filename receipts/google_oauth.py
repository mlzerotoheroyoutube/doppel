"""Sign in with Google (OAuth 2.0 authorization code + PKCE), read-only Calendar and Gmail.

Setup (once, by the owner): create an OAuth client of type "Web application" in Google Cloud with
redirect URI http://127.0.0.1:8080/oauth/callback, and save its JSON as .doppel/google_client.json.
Tokens are stored in .doppel/google_token.json (git-ignored, mode 600). Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / ".doppel"
CLIENT = DIR / "google_client.json"
TOKEN = DIR / "google_token.json"
REDIRECT = "http://127.0.0.1:8080/oauth/callback"
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/gmail.readonly"]
_PENDING: dict[str, str] = {}  # state -> PKCE verifier


class GoogleError(RuntimeError):
    pass


def _client() -> dict:
    try:
        data = json.loads(CLIENT.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise GoogleError("Google sign-in isn't set up: save your OAuth client JSON as .doppel/google_client.json") from e
    c = data.get("web") or data.get("installed") or data
    if not (c.get("client_id") and c.get("client_secret")):
        raise GoogleError("the OAuth client JSON has no client_id/client_secret")
    return c


def configured() -> bool:
    try:
        _client()
        return True
    except GoogleError:
        return False


def _post(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise GoogleError(f"Google said {e.code}: {e.read().decode(errors='replace')[:200]}") from e


def auth_url() -> str:
    c = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = verifier
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "include_granted_scopes": "true"})


def finish(code: str, state: str) -> str:
    """Exchange the authorization code; returns the signed-in email."""
    verifier = _PENDING.pop(state, None)
    if not verifier:
        raise GoogleError("sign-in expired or didn't start here; try again")
    c = _client()
    tok = _post("https://oauth2.googleapis.com/token", {
        "code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
        "redirect_uri": REDIRECT, "grant_type": "authorization_code", "code_verifier": verifier})
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    _save(tok)
    info = _get("https://openidconnect.googleapis.com/v1/userinfo")
    tok["email"] = info.get("email", "")
    _save(tok)
    return tok["email"]


def _save(tok: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps(tok))
    TOKEN.chmod(0o600)


def _token() -> dict:
    try:
        return json.loads(TOKEN.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise GoogleError("not signed in with Google") from e


def status() -> dict:
    try:
        t = _token()
        return {"configured": configured(), "signed_in": True, "email": t.get("email", "")}
    except GoogleError:
        return {"configured": configured(), "signed_in": False, "email": ""}


def sign_out() -> None:
    try:
        t = _token()
        urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/revoke?" + urllib.parse.urlencode({"token": t.get("refresh_token") or t["access_token"]}),
            method="POST"), timeout=10)
    except Exception:  # noqa: BLE001 - revoke is best effort
        pass
    TOKEN.unlink(missing_ok=True)


def _access_token() -> str:
    t = _token()
    if time.time() < t.get("expires_at", 0):
        return t["access_token"]
    if not t.get("refresh_token"):
        raise GoogleError("Google sign-in expired; sign in again")
    c = _client()
    new = _post("https://oauth2.googleapis.com/token", {
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    t.update(access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 3600)) - 60)
    _save(t)
    return t["access_token"]


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise GoogleError(f"Google API {e.code}: {e.read().decode(errors='replace')[:200]}") from e


# ---------------------------------------------------------------- read-only data

def calendar_today() -> list[dict]:
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = urllib.parse.urlencode({"timeMin": start.isoformat(), "timeMax": (start + timedelta(days=1)).isoformat(),
                                "singleEvents": "true", "orderBy": "startTime", "maxResults": "50"})
    data = _get(f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{q}")
    out = []
    for ev in data.get("items", []):
        if ev.get("status") == "cancelled":
            continue
        s, e = ev.get("start", {}), ev.get("end", {})
        all_day = "date" in s
        fmt = lambda x: datetime.fromisoformat(x["dateTime"].replace("Z", "+00:00")).astimezone().strftime("%H:%M")  # noqa: E731
        out.append({"start": "all day" if all_day else fmt(s), "end": "" if all_day else fmt(e),
                    "title": ev.get("summary") or "(busy)", "all_day": all_day})
    return out


def gmail_unread(limit: int = 15) -> list[dict]:
    q = urllib.parse.urlencode({"q": "is:unread in:inbox", "maxResults": str(limit)})
    ids = [m["id"] for m in _get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{q}").get("messages", [])]
    out = []
    for mid in ids:
        meta = _get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?format=metadata"
                    "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
        h = {x["name"]: x["value"] for x in meta.get("payload", {}).get("headers", [])}
        out.append({"from": h.get("From", "")[:80], "subject": h.get("Subject", "")[:140], "date": h.get("Date", "")[:31]})
    return out
