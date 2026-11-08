"""Tiny local receiver: the app page POSTs a snapshot of its current screen here (video production only)."""
import http.server
from pathlib import Path

OUT = Path(__file__).with_name("frames")


class H(http.server.BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8080")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        name = "".join(c for c in self.path.strip("/") if c.isalnum() or c in "-_") or "snap"
        body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 5_000_000))
        OUT.mkdir(exist_ok=True)
        (OUT / f"{name}.html").write_bytes(body)
        self.send_response(200); self._cors(); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


http.server.HTTPServer(("127.0.0.1", 8090), H).serve_forever()
