import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ofo.llm.client import LLMClient, LLMError


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1"


def test_chat_roundtrip_sends_auth_and_model():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers["Authorization"]
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"choices": [{"message": {"content": "ciao"}}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(out)
        def log_message(self, *a): pass

    srv, url = _serve(H)
    c = LLMClient(url, "k123", "auto")
    assert c.chat([{"role": "user", "content": "hi"}]) == "ciao"
    assert seen["auth"] == "Bearer k123" and seen["body"]["model"] == "auto"
    srv.shutdown()


def test_client_error_is_not_retried():
    calls = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(1); self.send_response(401); self.end_headers()
        def log_message(self, *a): pass

    srv, url = _serve(H)
    with pytest.raises(LLMError):
        LLMClient(url, "bad").chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1
    srv.shutdown()


# ---- model pool -------------------------------------------------------------------------
import time as _time

from ofo.llm.pool import BudgetExceeded, ModelPool
from ofo.llm.client import RateLimited


class Server:
    """Fake endpoint: model 'limited' answers 429 + Retry-After, everything else answers 200."""
    def __init__(self):
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"data": [{"id": "b"}, {"id": "a"}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(req["model"])
                if req["model"] == "limited":
                    self.send_response(429); self.send_header("Retry-After", "7"); self.end_headers(); return
                body = json.dumps({"model": req["model"], "choices": [{"message": {"content": f"hi from {req['model']}"}}],
                                   "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"


def _pool(tmp_path, roles, **kw):
    s = Server()
    clock = {"t": 1000.0}
    p = ModelPool(LLMClient(s.url, "k"), roles, tmp_path / "led.json", now=lambda: clock["t"],
                  sleep=lambda dt: clock.__setitem__("t", clock["t"] + dt), **kw)
    return s, p, clock


def test_list_models_and_round_robin_spreads_load(tmp_path):
    s, p, _ = _pool(tmp_path, {"r": ["a", "b"]})
    assert LLMClient(s.url, "k").list_models() == ["a", "b"]
    for _ in range(4):
        p.chat("r", [{"role": "user", "content": "x"}])
    assert s.calls == ["a", "b", "a", "b"]


def test_rate_limited_model_goes_on_cooldown_and_next_one_is_used(tmp_path):
    s, p, clock = _pool(tmp_path, {"r": ["limited", "b"]})
    assert p.chat("r", [{"role": "user", "content": "x"}]) == "hi from b"
    assert p.cooldown["limited"] == clock["t"] + 7                 # honoured Retry-After
    s.calls.clear()
    p.chat("r", [{"role": "user", "content": "y"}]); p.chat("r", [{"role": "user", "content": "z"}])
    assert "limited" not in s.calls                                 # not retried while cooling down
    clock["t"] += 8
    p.chat("r", [{"role": "user", "content": "w"}]); p.chat("r", [{"role": "user", "content": "v"}])
    assert "limited" in s.calls                                     # back after the cooldown


def test_ledger_counts_tokens_and_budget_stops_the_role(tmp_path):
    s, p, _ = _pool(tmp_path, {"r": ["b"]}, daily_token_budget={"r": 200})
    p.chat("r", [{"role": "user", "content": "1"}])
    p.chat("r", [{"role": "user", "content": "2"}])                 # 120 + 120 = 240 >= 200
    assert p.tokens_today("r") == 240
    with pytest.raises(BudgetExceeded):
        p.chat("r", [{"role": "user", "content": "3"}])
    assert json.loads((tmp_path / "led.json").read_text())["models"]["b"]["calls"] == 2


def test_identical_requests_are_served_from_cache_without_tokens(tmp_path):
    s, p, _ = _pool(tmp_path, {"r": ["b"]}, cache_dir=tmp_path / "cache")
    msg = [{"role": "user", "content": "same"}]
    assert p.chat("r", msg, temperature=0.2) == p.chat("r", msg, temperature=0.2)
    assert s.calls == ["b"] and p.tokens_today("r") == 120
    p.chat("r", msg, temperature=0.9); p.chat("r", msg, temperature=0.9)
    assert s.calls.count("b") == 3                                  # creative calls are never cached


def test_all_models_failing_raises_instead_of_looping_forever(tmp_path):
    s, p, _ = _pool(tmp_path, {"r": ["limited"]}, max_wait=5)
    with pytest.raises(Exception):
        p.chat("r", [{"role": "user", "content": "x"}])
