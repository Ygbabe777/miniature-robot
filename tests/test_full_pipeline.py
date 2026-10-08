"""Integrazione end-to-end (MOCK: nessuna rete). Eseguibile anche con `python test_full_pipeline.py --mock`."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.evaluator import evaluate, max_drawdown, trade_metrics
from src.event_bus import EventBus
from src.mock_llm import SCENARIOS
from src.pipeline import ConfigurationError, PipelineOptions, run_pipeline
from src.room_view import build_room_html
from src.scoreboard import build_scoreboard, render_text
from src.ui_state import AGENTS, build_view
from tests.conftest import DATE, ROOT, opts

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_mock_scenario_completes_and_writes_artifacts(project, scenario):
    res = run_pipeline(opts(project, scenario))
    assert res.status.value in ("APPROVED_SETUP", "APPROVED_WITH_CAUTION", "NO_TRADE", "REVIEW_REQUIRED", "DATA_ERROR")
    for k in ("report_md", "report_json", "transcript"):
        assert res.paths[k].exists()
    rep = json.loads(res.paths["report_json"].read_text())
    assert rep["human_decision"]["status"] == "PENDING"
    assert rep["panic_proof"]["human_decision"].startswith("RICHIESTA")


def test_happy_path_is_approved(project):
    res = run_pipeline(opts(project, "ok"))
    assert res.status.value == "APPROVED_SETUP" and res.report["data_quality"]["status"] == "YELLOW"
    assert res.report["judge"]["score"] == 92


def test_stage_observability(project):
    res = run_pipeline(opts(project))
    for name in ("data_validation", "features", "price_action", "options_flow", "strategist", "risk_manager", "judge"):
        st = res.report["stages"][name]
        assert {"status", "duration_ms", "error", "output_validation"} <= set(st)


def test_progress_callback_shows_six_steps(project):
    seen = []
    o = opts(project)
    o.progress = lambda step, total, label, status, ms: seen.append((step, total, label))
    run_pipeline(o)
    assert [s for s, _, _ in seen] == [1, 2, 3, 4, 5, 6] and all(t == 6 for _, t, _ in seen)


def test_real_mode_without_key_is_explicit_error(project, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    o = PipelineOptions(date=DATE, mock=False, cfg=project)
    with pytest.raises(ConfigurationError) as ex:
        run_pipeline(o)
    assert "OMNIROUTE_API_KEY" in str(ex.value)


def test_no_lookahead_in_features(project):
    import pandas as pd
    d = project.path("data_dir")
    df = pd.read_csv(d / "ohlcv.csv")
    fut = pd.DataFrame([[f"{DATE} 09:30:00", 99999.0, 99999.5, 99998.0, 99999.0, 999]], columns=df.columns)
    base = run_pipeline(opts(project, write_files=False)).report["market_snapshot"]
    pd.concat([df, fut]).to_csv(d / "ohlcv.csv", index=False)
    after = run_pipeline(opts(project, write_files=False)).report["market_snapshot"]
    assert base == after


# --------------------------------------------------------------------------- eventi / UI
def test_events_are_complete_and_ordered(project):
    ev = run_pipeline(opts(project)).report["events"]
    types = [e["event_type"] for e in ev]
    assert types[0] == "PIPELINE_STARTED" and types[-1] == "REPORT_GENERATED" and "FINAL_DECISION" in types
    assert [e["event_id"] for e in ev] == [f"EVT-{i:03d}" for i in range(1, len(ev) + 1)]
    for need in ("AGENT_STARTED", "AGENT_THINKING", "AGENT_MESSAGE", "AGENT_FINISHED", "SCENARIO_CREATED",
                 "JUDGE_STARTED", "JUDGE_FINISHED", "AGENT_AGREEMENT"):
        assert need in types, need
    order = {a: next(i for i, e in enumerate(ev) if e["event_type"] == "AGENT_STARTED" and e["agent"] == a)
             for a in ("price_action", "options_flow", "strategist", "risk_manager")}
    assert order["strategist"] > max(order["price_action"], order["options_flow"]) and order["risk_manager"] > order["strategist"]
    assert next(i for i, e in enumerate(ev) if e["event_type"] == "JUDGE_STARTED") > order["risk_manager"]


def test_every_agent_message_event_comes_from_real_output(project):
    rep = run_pipeline(opts(project)).report
    texts = {m["message"] for a in rep["agents"].values() if a["output"] for m in a["output"]["messages"]}
    for e in rep["events"]:
        if e["source"] == "agent" and e["event_type"] != "SCENARIO_CREATED":
            assert e["message"] in texts, e


def test_ui_frames_track_real_events(project):
    rep = run_pipeline(opts(project)).report
    view = build_view(rep)
    fr = view["frames"]
    assert len(fr) == len(rep["events"])
    assert fr[0]["room_mode"] == "wake"
    started = next(i for i, e in enumerate(rep["events"]) if e["event_type"] == "AGENT_STARTED" and e["agent"] == "price_action")
    assert fr[started]["agents"]["price_action"]["state"] == "ANALYZING" and fr[started]["agents"]["price_action"]["loc"] == "desk"
    rnd = next(i for i, e in enumerate(rep["events"]) if e["event_type"] == "ROUND_STARTED")
    assert fr[rnd]["agents"]["price_action"]["loc"] == "council" and fr[rnd]["agents"]["price_action"]["moving"]
    js = next(i for i, e in enumerate(rep["events"]) if e["event_type"] == "JUDGE_STARTED")
    assert fr[js]["agents"]["judge"]["loc"] == "center"
    fin = next(i for i, e in enumerate(rep["events"]) if e["event_type"] == "FINAL_DECISION")
    assert all(fr[fin]["agents"][a]["loc"] in ("council", "center") for a in AGENTS)
    assert fr[fin]["final"]["label"] == "COUNCIL VERDICT: APPROVED SETUP" and fr[fin]["room_mode"] == "positive"
    assert all(fr[-1]["agents"][a]["loc"] == "desk" for a in AGENTS)
    assert all(v == "done" for v in fr[-1]["steps"].values())


def test_ui_frames_deterministic(project):
    rep = run_pipeline(opts(project)).report
    assert json.dumps(build_view(rep), sort_keys=True) == json.dumps(build_view(rep), sort_keys=True)


def test_ui_veto_state_and_arrow(project):
    rep = run_pipeline(opts(project, "risk_veto")).report
    fr = build_view(rep)["frames"]
    i = next(i for i, e in enumerate(rep["events"]) if e["event_type"] == "RISK_VETO")
    assert fr[i]["agents"]["risk_manager"]["state"] == "VETO"
    assert {"from": "strategist", "to": "risk_manager", "kind": "veto"} in fr[i]["arrows"]
    assert fr[-1]["final"] is not None and fr[-1]["final"]["label"] == "NO TRADE"


def test_ui_failure_keeps_ui_usable(project):
    rep = run_pipeline(opts(project, "options_flow_down")).report
    fr = build_view(rep)["frames"]
    assert any(f["agents"]["options_flow"]["state"] == "FAILED" for f in fr)
    assert fr[-1]["final"]["label"] == "HUMAN REVIEW REQUIRED" and fr[-1]["room_mode"] == "review"
    assert "FAILED" == fr[-1]["agents"]["options_flow"]["status"]


def test_ui_data_error_warning_room(project):
    (project.path("data_dir") / "levels.json").write_text('{"date": "2026-10-08", "spot": 0}')
    rep = run_pipeline(opts(project)).report
    fr = build_view(rep)["frames"]
    assert fr[-1]["final"]["label"] == "DATA ERROR" and fr[-1]["room_mode"] == "warning"


def test_room_html_is_self_contained_and_secret_free(project, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "room-secret-key-xyz789")
    rep = run_pipeline(opts(project)).report
    html = build_room_html(rep, project.ui)
    assert "<script" in html and "DATA SOURCE: STATIC / PRE-MARKET" in html
    assert "room-secret-key-xyz789" not in html and "Authorization" not in html
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "") and "https://" not in html  # nessuna risorsa esterna


def test_room_html_escapes_script_breakout(project):
    rep = run_pipeline(opts(project)).report
    rep["events"][3]["message"] = "</script><img src=x onerror=alert(1)>"
    html = build_room_html(rep, project.ui)
    assert "</script><img" not in html


def test_event_bus_is_thread_safe_and_redacts(monkeypatch):
    import threading
    monkeypatch.setenv("OMNIROUTE_API_KEY", "bus-secret-key-0000")
    from src.council_events import EventType
    bus = EventBus("R")
    ts = [threading.Thread(target=lambda: [bus.emit(EventType.AGENT_MESSAGE, message="x bus-secret-key-0000") for _ in range(50)])
          for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [e.event_id for e in bus.events]
    assert len(ids) == 200 and len(set(ids)) == 200 and all("bus-secret" not in e.message for e in bus.events)


def test_parallel_analysts_real_threading(project):
    import random
    from src.clock import FakeClock
    project.debate.parallel_analysts = True
    from src.mock_llm import MockLLMClient
    o = PipelineOptions(date=DATE, mock=True, cfg=project, clock=FakeClock(), rng=random.Random(1), write_files=False,
                        client=MockLLMClient("ok"), run_id="P")
    # il flag parallel e' disattivato in mock da pipeline; qui forziamo il motore con parallel=True
    from src import pipeline as pl
    orig = pl.DebateEngine

    class Forced(orig):
        def __init__(self, *a, **k):
            k["parallel"] = True
            super().__init__(*a, **k)
    pl.DebateEngine = Forced
    try:
        par = run_pipeline(o).report
    finally:
        pl.DebateEngine = orig
    seq = run_pipeline(opts(project, write_files=False)).report
    assert par["decision"] == seq["decision"]
    assert [e["event_type"] for e in par["events"]] == [e["event_type"] for e in seq["events"]]


# --------------------------------------------------------------------------- evaluator / scoreboard
def test_trade_metrics_known_values():
    m = trade_metrics([2.0, -1.0, 1.0, -1.0, 3.0])
    assert m["trades"] == 5 and m["win_rate"] == 0.6 and m["average_R"] == 0.8 and m["expectancy"] == 0.8
    assert m["profit_factor"] == 3.0 and m["max_drawdown_R"] == 1.0
    assert trade_metrics([])["win_rate"] is None


def test_max_drawdown():
    assert max_drawdown([1, 1, -2, -1, 3]) == 3.0 and max_drawdown([1, 2]) == 0.0


def test_scoreboard_and_evaluate_with_journal(project):
    from src.journal import record_human_decision, record_result
    run_pipeline(opts(project, "ok"))
    jp = project.path("journal")
    record_human_decision(jp, DATE, "TAKE")
    record_result(jp, DATE, "WIN", 2.0)
    ev = evaluate(project)
    assert ev["runs"] == 1 and ev["trades"] == 1 and ev["win_rate"] == 1.0 and ev["scenario_accuracy"] == 1.0
    sb = build_scoreboard(project)
    pa = next(a for a in sb["agents"] if "Price Action" in a["Agent"])
    assert pa["Accuracy"] == 1.0 and pa["Contribution (R)"] == 2.0
    assert "CONSIGLIO" in render_text(sb)


def test_scoreboard_empty_project(project):
    sb = build_scoreboard(project)
    assert sb["council"]["runs"] == 0 and "n/d" in render_text(sb)


# --------------------------------------------------------------------------- CLI
def run_cli(*args, cwd=ROOT, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, env=e, timeout=120)


def test_cli_mock_run_in_temp_project(project):
    # il CLI legge config.yaml della radice; usiamo la copia in tmp tramite --config e percorsi dati
    r = run_cli("run.py", "--date", DATE, "--mock", "--no-ui", "--config", str(project.root / "config.yaml"),
                "--levels", str(project.root / "data/levels.json"), "--ohlcv", str(project.root / "data/ohlcv.csv"))
    assert r.returncode == 0, r.stderr
    for s in ("[1/6]", "[2/6]", "[3/6]", "[4/6]", "[5/6]", "[6/6]", "RUN COMPLETE", "NESSUN ORDINE AUTOMATICO"):
        assert s in r.stdout, s


def test_cli_real_mode_without_key_exits_2(project):
    r = run_cli("run.py", "--date", DATE, "--config", str(project.root / "config.yaml"),
                "--levels", str(project.root / "data/levels.json"), "--ohlcv", str(project.root / "data/ohlcv.csv"),
                env={"OMNIROUTE_API_KEY": ""})
    assert r.returncode == 2 and "OMNIROUTE_API_KEY" in r.stderr


def test_cli_data_error_exit_code(project):
    (project.root / "data/levels.json").write_text('{"date": "2026-10-08", "spot": 0}')
    r = run_cli("run.py", "--date", DATE, "--mock", "--config", str(project.root / "config.yaml"),
                "--levels", str(project.root / "data/levels.json"), "--ohlcv", str(project.root / "data/ohlcv.csv"))
    assert r.returncode == 3 and "DATA_ERROR" in r.stdout
