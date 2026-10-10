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
