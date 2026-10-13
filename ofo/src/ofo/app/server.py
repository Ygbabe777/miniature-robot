"""Tiny local HTTP server (standard library only, bound to 127.0.0.1)."""
from __future__ import annotations

import json
import mimetypes
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

STATIC = Path(__file__).parent / "static"


def make_server(lab, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, TimeoutError):
                pass                                    # the browser went away (refresh, closed tab): nothing to answer, nothing to report

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
                if u.path == "/api/diag":
                    return self._send(200, json.dumps(lab.diag(), ensure_ascii=False, indent=1, default=str).encode(), "application/json; charset=utf-8")
                if u.path == "/api/state":
                    return self._json(lab.state())
                if u.path == "/api/events":
                    after = int(parse_qs(u.query).get("after", ["0"])[0])
                    return self._json(lab.events_after(after))
                if u.path == "/api/fascicoli":
                    return self._json({"fascicoli": lab.fascicoli(), "pending_review": len(lab.farming_pending())})
                if u.path.startswith("/api/fascicolo/"):
                    d = lab.fascicolo(unquote(u.path[len("/api/fascicolo/"):]))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                if u.path == "/api/research":
                    return self._json(lab.research_report())
                if u.path == "/api/ranking":
                    return self._json(lab.ranking(parse_qs(u.query).get("ruleset", [None])[0]))
                if u.path == "/api/directives":
                    return self._json({"directives": lab.directives.public()})
                if u.path == "/api/web":
                    return self._json(lab.web_activity(int(parse_qs(u.query).get("after", ["0"])[0])))
                if u.path == "/api/candles":
                    q = parse_qs(u.query)
                    days = q.get("days", ["10"])[0]
                    return self._json(lab.candles(q.get("tf", ["15min"])[0], None if days == "all" else int(days),
                                                  q.get("before", [None])[0], q.get("market", [None])[0]))
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
                    q = parse_qs(u.query)
                    d = lab.trades(unquote(u.path[len("/api/trades/"):]), (q.get("account") or [None])[0], int(q["micro"][0]) if q.get("micro") else None)
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
                print(f"[OFO] errore in {self.command} {self.path}:\n{traceback.format_exc()}", flush=True)    # the REAL cause, in the terminal
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
                if u.path == "/api/obsidian":
                    try:
                        return self._json(lab.set_obsidian(data.get("vault")))
                    except ValueError as e:
                        return self._json({"error": str(e)}, 400)
                if u.path == "/api/budget":
                    return self._json(lab.set_budget(data.get("remaining_usd"), data.get("weekly_usd"), data.get("usd_per_million")) or {})
                if u.path == "/api/markets":
                    return self._json(lab.set_markets(list(data.get("markets") or [])))
                if u.path == "/api/farming/review":
                    return self._json(lab.farming_review_all())
                if u.path == "/api/fascicolo/decision":
                    return self._json(lab.decide(str(data.get("slug", "")), str(data.get("decision", "")), str(data.get("note", ""))))
                if u.path == "/api/sources":
                    return self._json(lab.set_source(str(data.get("name", "")), bool(data.get("enabled"))))
                if u.path == "/api/directives":
                    return self._json(lab.tell_researcher(str(data.get("text", "")), data.get("kind")))
                if u.path == "/api/directives/remove":
                    return self._json(lab.forget_directive(str(data.get("id", ""))))
                if u.path == "/api/retest":
                    return self._json(lab.retest(str(data.get("slug") or "")))
                if u.path == "/api/autopilot":
                    return self._json(lab.set_autopilot(data.get("enabled"), data.get("max_trials_per_day")))
                self._json({"error": "not found"}, 404)
            except Exception as e:
                print(f"[OFO] errore in {self.command} {self.path}:\n{traceback.format_exc()}", flush=True)    # the REAL cause, in the terminal
                self._json({"error": str(e)[:300]}, 500)

        def do_PUT(self):
            if not self._guard():
                return
            u = urlparse(self.path)
            try:
                if u.path.startswith("/api/ideas/"):
                    return self._json(lab.save_idea(unquote(u.path[len("/api/ideas/"):]), self._body(), parse_qs(u.query).get("for", [None])[0]))
                self._json({"error": "not found"}, 404)
            except Exception as e:
                print(f"[OFO] errore in {self.command} {self.path}:\n{traceback.format_exc()}", flush=True)    # the REAL cause, in the terminal
                self._json({"error": str(e)[:300]}, 500)

    class Srv(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            import sys
            if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                return                                  # a closed browser connection is not an error of the Lab
            super().handle_error(request, client_address)

    return Srv((host, port), H)


def serve_background(lab, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = make_server(lab, port=port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"
