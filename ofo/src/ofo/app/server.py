"""Tiny local HTTP server (standard library only, bound to 127.0.0.1)."""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

STATIC = Path(__file__).parent / "static"


def make_server(lab, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode())

        def _guard(self) -> bool:
            # custom header forces a CORS preflight, so other websites cannot drive this app
            if self.headers.get("X-OFO") != "1":
                self._json({"error": "forbidden"}, 403)
                return False
            return True

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/api/state":
                    return self._json(lab.state())
                if u.path == "/api/events":
                    after = int(parse_qs(u.query).get("after", ["0"])[0])
                    return self._json(lab.events_after(after))
                if u.path == "/api/candles":
                    q = parse_qs(u.query)
                    days = q.get("days", ["10"])[0]
                    return self._json(lab.candles(q.get("tf", ["15min"])[0], None if days == "all" else int(days),
                                                  q.get("before", [None])[0]))
                if u.path == "/api/scout":
                    return self._json(lab.scout_state())
                if u.path == "/api/deployments":
                    return self._json(lab.deployments_view())
                if u.path == "/api/dossiers":
                    return self._json({"dossiers": lab.dossiers()})
                if u.path == "/api/accounts":
                    return self._json(lab.accounts())
                if u.path.startswith("/api/indicators/"):
                    q = parse_qs(u.query)
                    g = lambda k: int(q[k][0]) if k in q else None
                    d = lab.indicators(unquote(u.path[len("/api/indicators/"):]), g("from"), g("to"))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                if u.path.startswith("/api/trades/"):
                    d = lab.trades(unquote(u.path[len("/api/trades/"):]))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                if u.path.startswith("/api/strategy/"):
                    d = lab.detail(unquote(u.path[len("/api/strategy/"):]))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                rel = "index.html" if u.path in ("/", "") else unquote(u.path).lstrip("/")
                f = (STATIC / rel).resolve()
                if STATIC.resolve() not in f.parents or not f.is_file():
                    return self._json({"error": "not found"}, 404)
                self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            except Exception as e:
                self._json({"error": str(e)[:300]}, 500)

        def do_POST(self):
            if not self._guard():
                return
            u = urlparse(self.path)
            try:
                data = json.loads(self._body() or b"{}")
                if u.path == "/api/chat":
                    return self._json(lab.chat(data.get("message", ""), data.get("history"), data.get("lang")))
                if u.path == "/api/run":
                    return self._json(lab.start(str(data.get("id", "")), data.get("lang")))
                if u.path == "/api/ingest":
                    return self._json(lab.ingest())
                if u.path == "/api/scout/run":
                    return self._json(lab.scout_run(data.get("lang")))
                if u.path == "/api/scout/auto":
                    return self._json(lab.set_scout_auto(bool(data.get("enabled")), data.get("minutes")))
                if u.path == "/api/autopilot":
                    return self._json(lab.set_autopilot(data.get("enabled"), data.get("max_trials_per_day")))
                self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": str(e)[:300]}, 500)

        def do_PUT(self):
            if not self._guard():
                return
            u = urlparse(self.path)
            try:
                if u.path.startswith("/api/ideas/"):
                    return self._json(lab.save_idea(unquote(u.path[len("/api/ideas/"):]), self._body()))
                self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": str(e)[:300]}, 500)

    return ThreadingHTTPServer((host, port), H)


def serve_background(lab, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = make_server(lab, port=port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"
