import json
import random

import pytest

from src.agents import (AgentRunner, AllModelsFailed, LLMError, LLMResponse, OmniRouteClient,
                        backoff_delay, call_with_fallback, extract_json)
from src.clock import FakeClock
from src.config import OmniRouteCfg
from src.mock_llm import MockLLMClient

CFG = OmniRouteCfg()
MSG = [{"role": "user", "content": "{}"}]


class Scripted:
    """Client con risposte/errori in sequenza."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def complete(self, *, agent, model, messages, timeout):
        self.calls.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(item, model)


def e(code, retry=True):
    return LLMError(f"HTTP {code}", status_code=code, retryable=retry)


def test_backoff_formula_with_jitter():
    class R:
        def uniform(self, a, b):
            return 0.25
    assert backoff_delay(0, CFG, R()) == pytest.approx(1.25)
    assert backoff_delay(2, CFG, R()) == pytest.approx(4.25)
    assert backoff_delay(10, CFG, R()) == pytest.approx(CFG.backoff_max_seconds + 0.25)


def test_retry_then_success_records_sleeps():
    sleeps = []
    c = Scripted([e(429), e(500), "ok"])
    resp, rec = call_with_fallback(c, agent="a", models=["m1", "m2"], messages=MSG, cfg=CFG,
                                   sleep=sleeps.append, rng=random.Random(1))
    assert resp.content == "ok" and rec.retry_count == 2 and not rec.fallback_used
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] - 0.5


def test_max_three_retries_then_fallback():
    sleeps = []
    c = Scripted([e(503)] * 4 + ["ok"])
    resp, rec = call_with_fallback(c, agent="a", models=["m1", "m2"], messages=MSG, cfg=CFG,
                                   sleep=sleeps.append, rng=random.Random(1))
    assert c.calls == ["m1"] * 4 + ["m2"]  # 1 tentativo + 3 retry sul primo modello
    assert rec.fallback_used and rec.actual_model == "m2" and rec.requested_model == "m1"
    assert "m1" in rec.failure_reason and len(sleeps) == 3


def test_non_retryable_goes_straight_to_fallback():
    c = Scripted([e(400, retry=False), "ok"])
    _, rec = call_with_fallback(c, agent="a", models=["m1", "m2"], messages=MSG, cfg=CFG, sleep=lambda s: None)
    assert c.calls == ["m1", "m2"] and rec.retry_count == 0


def test_all_models_fail_is_explicit():
    c = Scripted([e(500)] * 8)
    with pytest.raises(AllModelsFailed) as ex:
        call_with_fallback(c, agent="a", models=["m1", "m2"], messages=MSG, cfg=CFG, sleep=lambda s: None)
    assert len(c.calls) == 8 and ex.value.record.failure_reason


def test_auth_error_not_retried_nor_fallback():
    c = Scripted([LLMError("chiave mancante", kind="auth")])
    with pytest.raises(AllModelsFailed):
        call_with_fallback(c, agent="a", models=["m1", "m2"], messages=MSG, cfg=CFG, sleep=lambda s: None)
    assert c.calls == ["m1"]


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Ecco:\n```json\n{"a": 2}\n```\nfine') == {"a": 2}
    assert extract_json('prosa {"a": 3} altra prosa') == {"a": 3}
    assert extract_json('{"a": ') is None and extract_json("") is None and extract_json("[1,2]") is None


class FakeSession:
    def __init__(self, status=200, raise_exc=None, body=None):
        self.status, self.raise_exc, self.body, self.last = status, raise_exc, body, None

    def post(self, url, json=None, timeout=None, headers=None):
        self.last = (url, headers)
        if self.raise_exc:
            raise self.raise_exc
        outer = self

        class Resp:
            status_code = outer.status

            def json(self):
                return outer.body or {"choices": [{"message": {"content": "{}"}}], "model": "x"}
        return Resp()


def test_client_never_leaks_key_in_errors(monkeypatch):
    import requests
    key = "super-secret-key-12345"
    monkeypatch.setenv("OMNIROUTE_API_KEY", key)
    for sess in (FakeSession(500), FakeSession(raise_exc=requests.ConnectionError(f"boom {key}")),
                 FakeSession(raise_exc=requests.Timeout(f"slow {key}"))):
        c = OmniRouteClient(CFG, key, sess)
        with pytest.raises(LLMError) as ex:
            c.complete(agent="a", model="m", messages=MSG, timeout=1)
        assert key not in str(ex.value)


def test_client_retryable_flags_and_missing_key():
    c = OmniRouteClient(CFG, "k", FakeSession(429))
    with pytest.raises(LLMError) as ex:
        c.complete(agent="a", model="m", messages=MSG, timeout=1)
    assert ex.value.retryable and ex.value.status_code == 429
    with pytest.raises(LLMError) as ex:
        OmniRouteClient(CFG, "k", FakeSession(404)).complete(agent="a", model="m", messages=MSG, timeout=1)
    assert not ex.value.retryable
    with pytest.raises(LLMError) as ex:
        OmniRouteClient(CFG, None, FakeSession()).complete(agent="a", model="m", messages=MSG, timeout=1)
    assert ex.value.kind == "auth"


def _runner(project, client):
    return AgentRunner(project, client, project.path("prompts_dir"), clock=FakeClock(), sleep=lambda s: None,
                       rng=random.Random(1))


def _payload(project):
    from src.data_loader import prepare_market_data
    from src.features import build_evidence, compute_features
    from tests.conftest import DATE
    d = project.path("data_dir")
    md = prepare_market_data(d / "levels.json", d / "ohlcv.csv", DATE, project.features)
    f = compute_features(md, project.features)
    return {"run": {}, "data_quality": "YELLOW", "warnings": [], "features": f, "evidence": build_evidence(md, f)}


def test_runner_valid_output(project):
    r = _runner(project, MockLLMClient("ok")).run("price_action", _payload(project))
    assert r.ok and r.meta.validation_status == "VALID" and not r.meta.json_correction_used
    assert r.meta.requested_model == "super-120b" and len(r.meta.input_hash) == 16


def test_runner_malformed_then_correction(project):
    r = _runner(project, MockLLMClient("malformed_json_recovers")).run("price_action", _payload(project))
    assert r.ok and r.meta.json_correction_used and r.meta.errors


def test_runner_malformed_twice_fails_without_fabrication(project):
    r = _runner(project, MockLLMClient("malformed_json_fails")).run("price_action", _payload(project))
    assert not r.ok and r.output is None and r.status.value == "FAILED"


def test_runner_schema_violation_fails(project):
    r = _runner(project, MockLLMClient("invalid_schema_fails")).run("options_flow", _payload(project))
    assert r.output is None and r.meta.validation_status == "INVALID"


def test_runner_fallback_recorded(project):
    r = _runner(project, MockLLMClient("server_error_fallback")).run("options_flow", _payload(project))
    assert r.ok and r.meta.fallback_used and r.meta.actual_model == "super-120b"
    assert r.meta.requested_model == "ultra-550b" and r.meta.failure_reason


def test_runner_total_failure(project):
    r = _runner(project, MockLLMClient("price_action_down")).run("price_action", _payload(project))
    assert r.output is None and r.meta.retry_count == 6 and r.meta.actual_model is None


def test_runner_identity_forced(project):
    class Wrong:
        def complete(self, *, agent, model, messages, timeout):
            body = json.loads(MockLLMClient("ok").complete(agent=agent, model=model, messages=messages, timeout=1).content)
            body["agent"] = "judge"
            return LLMResponse(json.dumps(body), model)
    r = _runner(project, Wrong()).run("price_action", _payload(project))
    assert r.output.agent == "price_action"


def test_config_paths_follow_config_location(project):
    from src.config import load_config
    cfg = load_config(project.root / "config.yaml")
    assert cfg.path("reports_dir") == project.root / "reports"
