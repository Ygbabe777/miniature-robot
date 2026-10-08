"""Modalità REALE contro un server locale OpenAI-compatibile (finto OmniRoute): verifica HTTP, header,
retry su 429, parsing e assenza di segreti negli artefatti, senza rete esterna."""
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.mock_llm import MockLLMClient
from src.pipeline import PipelineOptions, run_pipeline
from tests.conftest import DATE

KEY = "local-test-key-NEVER-LEAK-987654"
AGENT_BY_TEXT = [("PRICE ACTION", "price_action"), ("OPTIONS FLOW", "options_flow"), ("STRATEGIST", "strategist"),
                 ("RISK MANAGER", "risk_manager"), ("GIUDICE", "judge")]


@pytest.fixture()
def server():
    state = {"auth": [], "count": 0, "paths": [], "fail_first": True}
    mock = MockLLMClient("ok")
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                state["auth"].append(self.headers.get("Authorization"))
                state["paths"].append(self.path)
                state["count"] += 1
                first = state["fail_first"]
                state["fail_first"] = False
            if first:
                self.send_response(429)
                self.end_headers()
                return
            sys_text = body["messages"][0]["content"]
            agent = next(a for t, a in AGENT_BY_TEXT if f"agente {t}" in sys_text or f"lo {t}" in sys_text or f"il {t}" in sys_text)
            with lock:
                out = mock.complete(agent=agent, model=body["model"], messages=body["messages"], timeout=1)
            payload = json.dumps({"choices": [{"message": {"content": out.content}}], "model": body["model"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, state
    srv.shutdown()


def test_real_mode_end_to_end_against_local_server(project, server, monkeypatch):
    srv, state = server
    monkeypatch.setenv("OMNIROUTE_API_KEY", KEY)
    project.omniroute.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    project.omniroute.backoff_base_seconds = 0.01
    project.omniroute.jitter_seconds = 0.01
    res = run_pipeline(PipelineOptions(date=DATE, mock=False, cfg=project, rng=random.Random(3)))
    assert res.report["run"]["mode"] == "REAL" and res.report["run"]["mock_scenario"] is None
    assert res.status.value == "APPROVED_SETUP"
    assert state["count"] >= 6 and set(state["paths"]) == {"/v1/chat/completions"}
    assert set(state["auth"]) == {f"Bearer {KEY}"}  # la chiave viene inviata solo come header
    assert sum(a["meta"]["retry_count"] for a in res.report["agents"].values()) == 1  # il 429 iniziale
    for p in res.paths.values():
        assert KEY not in p.read_text(encoding="utf-8")


def test_real_mode_server_down_fails_safely(project, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", KEY)
    project.omniroute.base_url = "http://127.0.0.1:9"  # porta chiusa
    project.omniroute.backoff_base_seconds = 0.001
    project.omniroute.jitter_seconds = 0.001
    project.omniroute.max_retries = 1
    res = run_pipeline(PipelineOptions(date=DATE, mock=False, cfg=project, rng=random.Random(3)))
    assert res.status.value == "REVIEW_REQUIRED"
    assert all(a["status"] in ("FAILED", "SKIPPED") for a in res.report["agents"].values())
    for p in res.paths.values():
        assert KEY not in p.read_text(encoding="utf-8")
