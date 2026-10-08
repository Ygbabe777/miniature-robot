import json

from src.journal import ensure_journal, read_journal, record_ai_run, record_human_decision, record_result
from src.pipeline import run_pipeline
from src.report_generator import DISCLAIMER
from tests.conftest import DATE, opts

import pytest


def full(project, scenario="ok"):
    return run_pipeline(opts(project, scenario))


def test_artifacts_written_and_valid(project):
    res = full(project)
    for k in ("report_md", "report_json", "transcript", "journal"):
        assert res.paths[k].exists(), k
    rep = json.loads(res.paths["report_json"].read_text())
    tr = json.loads(res.paths["transcript"].read_text())
    assert rep["run"]["run_id"] == "TEST-RUN" == tr["run"]["run_id"]
    assert rep["disclaimer"] == DISCLAIMER
    assert {"data_hash", "config_hash", "prompt_versions", "models", "started_at", "finished_at"} <= set(rep["run"])


def test_markdown_structure_and_panic_proof(project):
    md = full(project).paths["report_md"].read_text()
    for h in ("# OFO COUNCIL", "## NQ PRE-MARKET ANALYSIS", "## 1. Market Snapshot", "## 2. 💙 Price Action",
              "## 3. 💜 Options Flow", "## 4. 💚 Strategist", "## 5. ❤️ Risk Manager", "## 6. ⚪ Judge",
              "## 7. Final Council Decision", "## 8. PANIC-PROOF", "## 9. Human Decision"):
        assert h in md, h
    for k in ("MARKET REGIME", "PRIMARY BIAS", "SECONDARY BIAS", "KEY LEVELS", "INVALIDATION", "NO-TRADE CONDITIONS",
              "MAX DAILY RISK", "BEST SETUP", "WHAT WOULD INVALIDATE THE THESIS", "HUMAN DECISION REQUIRED"):
        assert k in md, k
    assert DISCLAIMER in md and "MODALITÀ MOCK" in md and "DATI SINTETICI" in md
    assert "PENDING" in md  # decisione umana separata


def test_panic_proof_present_even_on_data_error(project):
    (project.path("data_dir") / "levels.json").write_text('{"date": "2026-10-08", "spot": 0}')
    res = full(project)
    md = res.paths["report_md"].read_text()
    assert "PANIC-PROOF" in md and "DATA_ERROR" in md and "NESSUNO" in md


def test_transcript_has_full_reconstruction_data(project):
    tr = json.loads(full(project).paths["transcript"].read_text())
    assert {c["agent"] for c in tr["calls"]} == {"price_action", "options_flow", "strategist", "risk_manager", "judge"}
    c = tr["calls"][0]
    for k in ("requested_model", "actual_model", "fallback_used", "failure_reason", "input_hash", "output",
              "validation_status", "retry_count", "errors", "evidence_refs", "duration_ms"):
        assert k in c, k
    assert tr["events"] and tr["stages"]
    assert all(e["event_id"].startswith("EVT-") for e in tr["events"])


def test_fallback_recorded_in_transcript(project):
    tr = json.loads(full(project, "server_error_fallback").paths["transcript"].read_text())
    of = next(c for c in tr["calls"] if c["agent"] == "options_flow")
    assert of["fallback_used"] and of["requested_model"] == "ultra-550b" and of["actual_model"] == "super-120b"


def test_no_secret_in_any_artifact(project, monkeypatch):
    key = "sk-test-SECRET-key-123456789"
    monkeypatch.setenv("OMNIROUTE_API_KEY", key)
    res = full(project)
    for p in res.paths.values():
        text = p.read_text(encoding="utf-8")
        assert key not in text and "Bearer " not in text, p


def test_secret_in_error_text_is_redacted(project, monkeypatch):
    from src.agents import LLMError, LLMResponse
    key = "leaky-key-abcdef123456"
    monkeypatch.setenv("OMNIROUTE_API_KEY", key)

    class Leaky:
        def complete(self, *, agent, model, messages, timeout):
            raise LLMError(f"Authorization: Bearer {key} rifiutata", status_code=400, retryable=False)
    o = opts(project, "ok")
    o.client = Leaky()
    res = run_pipeline(o)
    for p in res.paths.values():
        assert key not in p.read_text(encoding="utf-8")
    assert res.status.value == "REVIEW_REQUIRED"


def test_mock_mode_is_deterministic(project, tmp_path):
    a = full(project).report
    b = full(project).report
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_journal_human_decision_never_invented(project):
    res = full(project)
    jp = project.path("journal")
    rows = read_journal(jp)
    assert len(rows) == 1 and rows[0]["human_decision"] == "PENDING" and rows[0]["date"] == DATE
    assert rows[0]["council_status"] == res.status.value and rows[0]["selected_scenario"] == "S1"


def test_journal_preserves_human_fields_on_rerun(project):
    full(project)
    jp = project.path("journal")
    record_human_decision(jp, DATE, "TAKE", notes="ok")
    record_result(jp, DATE, "WIN", 1.5)
    full(project)  # nuova analisi dello stesso giorno
    row = read_journal(jp)[0]
    assert (row["human_decision"], row["result"], row["pnl_r"], row["notes"]) == ("TAKE", "WIN", "1.5", "ok")


def test_journal_validation(project):
    full(project)
    jp = project.path("journal")
    with pytest.raises(ValueError):
        record_human_decision(jp, DATE, "PENDING")
    with pytest.raises(ValueError):
        record_human_decision(jp, DATE, "YOLO")
    with pytest.raises(ValueError):
        record_human_decision(jp, "1999-01-01", "SKIP")
    with pytest.raises(ValueError):
        record_result(jp, DATE, "MAYBE", 1.0)


def test_ensure_journal_creates_header(tmp_path):
    p = tmp_path / "j.csv"
    ensure_journal(p)
    assert p.read_text().startswith("date,instrument,council_bias,selected_scenario,human_decision,entry,stop,target,result,pnl_r,notes")
