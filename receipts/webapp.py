"""A standalone web page for Doppel: no ASI:One or Agentverse needed.

    python -m receipts.webapp            # http://127.0.0.1:8080
    python -m receipts.webapp --lan      # also reachable from your phone on the same Wi-Fi (asks for a passcode)

The page talks to this small server; the server talks to the Doppel gate's HTTP API with the
token from .env, so the token never reaches the browser. Standard library only.
"""

from __future__ import annotations

import argparse
import hmac
import http.server
import json
import os
import secrets
import urllib.request
from pathlib import Path

from . import connections, google_oauth, llm  # noqa: F401  (llm loads .env)
import urllib.parse

PAGE = Path(__file__).with_name("web") / "index.html"
GATE = os.environ.get("RECEIPTS_URL", "http://127.0.0.1:8001")
TOKEN = os.environ.get("RECEIPTS_API_TOKEN", "")
OWNER = os.environ.get("RECEIPTS_OWNER", "")
SESSIONS: set[str] = set()


def gate(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(GATE + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Handler(http.server.BaseHTTPRequestHandler):
    passcode = ""

    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict, extra: dict | None = None):
        self._send(code, json.dumps(obj).encode(), extra=extra)

    def _authed(self) -> bool:
        if not self.passcode:
            return True
        cookie = self.headers.get("Cookie", "")
        sid = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("doppel=")), "")
        return sid in SESSIONS

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path in ("/memory", "/connections"):
            if not self._authed():
                return self._json(401, {"ok": False, "error": "enter the passcode first"})
            try:
                if self.path == "/memory":
                    self._json(200, gate("/api/profile", {"token": TOKEN, "user": OWNER}))
                else:
                    self._json(200, {"ok": True, **connections.public_status()})
            except Exception as e:  # noqa: BLE001
                self._json(200, {"ok": False, "error": str(e)[:120]})
        elif self.path == "/oauth/start":
            if not self._authed():
                return self._json(401, {"ok": False})
            try:
                self._send(302, b"", "text/plain", {"Location": google_oauth.auth_url()})
            except google_oauth.GoogleError as e:
                self._send(302, b"", "text/plain", {"Location": "/#connections?error=" + urllib.parse.quote(str(e))})
        elif self.path.startswith("/oauth/callback"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "error" in q:
                return self._send(302, b"", "text/plain", {"Location": "/#connections?error=" + urllib.parse.quote(q["error"][0])})
            try:
                email = google_oauth.finish(q.get("code", [""])[0], q.get("state", [""])[0])
                connections.save({"calendar_mode": "google", "gmail_mode": "google"})
                self._send(302, b"", "text/plain", {"Location": "/#connections?google=" + urllib.parse.quote(email)})
            except google_oauth.GoogleError as e:
                self._send(302, b"", "text/plain", {"Location": "/#connections?error=" + urllib.parse.quote(str(e))})
        elif self.path == "/health":
            try:
                h = gate("/api/health", timeout=5)
                self._json(200, {"ok": True, "agent": h.get("agent", ""), "roles": h.get("roles", {}),
                                 "owner": OWNER, "locked": bool(self.passcode) and not self._authed()})
            except Exception as e:  # noqa: BLE001
                self._json(200, {"ok": False, "error": f"Doppel isn't running ({str(e)[:80]})"})
        else:
            self._send(404, b"{}")

    def do_POST(self):
        length = min(int(self.headers.get("Content-Length") or 0), 64_000)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "bad request"})
        if self.path == "/login":
            if self.passcode and hmac.compare_digest(str(body.get("passcode", "")).encode(), self.passcode.encode()):
                sid = secrets.token_urlsafe(24)
                SESSIONS.add(sid)
                return self._json(200, {"ok": True}, {"Set-Cookie": f"doppel={sid}; HttpOnly; SameSite=Strict; Path=/"})
            return self._json(200, {"ok": not self.passcode})
        if not self._authed():
            return self._json(401, {"ok": False, "error": "enter the passcode first"})
        try:
            if self.path == "/chat":
                text = str(body.get("text", ""))[:8000]
                out = gate("/api/chat", {"token": TOKEN, "user": OWNER, "text": text})
            elif self.path == "/poll":
                out = gate("/api/messages", {"token": TOKEN, "user": OWNER})
            elif self.path == "/connections":
                allowed = {"calendar_mode", "calendar_ics_url", "gmail_mode", "gmail_address", "gmail_app_password"}
                update = {k: v for k, v in body.items() if k in allowed}
                if update.get("calendar_mode") not in (None, "simulated", "real", "off", "google") or \
                        update.get("gmail_mode") not in (None, "simulated", "real", "off", "google"):
                    return self._json(400, {"ok": False, "error": "bad mode"})
                connections.save(update)
                out = {"ok": True, **connections.public_status()}
            elif self.path == "/connections/google/signout":
                google_oauth.sign_out()
                connections.save({"calendar_mode": "simulated", "gmail_mode": "simulated"})
                out = {"ok": True, **connections.public_status()}
            elif self.path == "/connections/test":
                out = connections.test(str(body.get("which", "")))
            else:
                return self._send(404, b"{}")
            self._json(200, out)
        except Exception as e:  # noqa: BLE001
            self._json(200, {"ok": False, "error": f"Doppel didn't answer ({str(e)[:80]})", "messages": []})


def main() -> None:
    ap = argparse.ArgumentParser(prog="receipts.webapp")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--lan", action="store_true", help="listen on the local network (requires a passcode)")
    a = ap.parse_args()
    if not (TOKEN and OWNER):
        raise SystemExit("Set RECEIPTS_API_TOKEN and RECEIPTS_OWNER in .env first.")
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        Handler.passcode = os.environ.get("RECEIPTS_WEB_PASSCODE") or secrets.token_hex(3)
        print(f"Passcode for other devices: {Handler.passcode}")
    print(f"Doppel web: http://{'<this-laptop-ip>' if a.lan else '127.0.0.1'}:{a.port}", flush=True)
    http.server.ThreadingHTTPServer((host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
