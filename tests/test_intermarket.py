import numpy as np
import pandas as pd
import pytest

from src.config import IntermarketCfg
from src.intermarket import compute_intermarket, scenario_gate
from src.pipeline import run_pipeline
from tests.conftest import DATE, opts

CFG = IntermarketCfg()


def frames(n=400, corr_noise=0.0002, seed=1):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.0004, n)
    ts = pd.date_range("2026-10-05 09:30", periods=n, freq="5min")
    nq = pd.DataFrame({"timestamp": ts, "close": 28000 * np.exp(np.cumsum(r))})
    es = pd.DataFrame({"timestamp": ts, "close": 6300 * np.exp(np.cumsum(0.85 * r + rng.normal(0, corr_noise, n)))})
    return nq, es


def blocks(nq_o, nq_c, es_o, es_c, nq_hi=101, nq_lo=99, es_hi=101, es_lo=99):
    nq = {"last_close": nq_c, "prev_session": {"close": 100, "high": 100.5, "low": 98.5},
          "overnight": {"open": nq_o, "close": nq_c, "high": nq_hi, "low": nq_lo, "vwap": 100}}
    es = {"last_close": es_c, "prev_session": {"close": 100, "high": 100.5, "low": 98.5},
          "overnight": {"open": es_o, "close": es_c, "high": es_hi, "low": es_lo, "vwap": 100}}
    return nq, es


def test_confirmed_when_both_move_same_way():
    nq_df, es_df = frames()
    nq, es = blocks(100, 100.6, 100, 100.4)
    im = compute_intermarket(nq_df, es_df, nq, es, CFG)
    assert im["state"] == "CONFIRMED" and im["direction"] == "LONG" and im["correlation"] > 0.8


def test_divergent_when_opposite():
    nq_df, es_df = frames()
    nq, es = blocks(100, 100.6, 100, 99.6)
    im = compute_intermarket(nq_df, es_df, nq, es, CFG)
    assert im["state"] == "DIVERGENT" and im["es_dir"] == "SHORT" and im["nq_dir"] == "LONG"


def test_neutral_and_mixed():
    nq_df, es_df = frames()
    assert compute_intermarket(nq_df, es_df, *blocks(100, 100.02, 100, 99.98), CFG)["state"] == "NEUTRAL"
    assert compute_intermarket(nq_df, es_df, *blocks(100, 100.14, 100, 100.05), CFG)["state"] == "MIXED"
    assert compute_intermarket(nq_df, es_df, *blocks(100, 100.5, 100, 100.05), CFG)["state"] == "DIVERGENT"  # gap > soglia


def test_decorrelated_overrides():
    nq_df, es_df = frames(corr_noise=0.01)
    im = compute_intermarket(nq_df, es_df, *blocks(100, 100.6, 100, 100.4), CFG)
    assert im["state"] == "DECORRELATED" and im["correlation"] < 0.6


def test_smt_flags():
    nq_df, es_df = frames()
    im = compute_intermarket(nq_df, es_df, *blocks(100, 100.6, 100, 100.4, nq_hi=101.0, es_hi=100.2), CFG)
    assert im["smt_high"] == "NQ_ONLY" and any("SMT" in r for r in im["reasons"])
    assert im["smt_low"] == "NONE"


@pytest.mark.parametrize("direction,im,expected", [
    ("LONG", None, "WARN"),
    ("LONG", {"state": "UNKNOWN"}, "WARN"),
    ("LONG", {"state": "CONFIRMED", "direction": "LONG", "es_dir": "LONG", "smt_high": "NONE", "smt_low": "NONE"}, "PASS"),
    ("LONG", {"state": "CONFIRMED", "direction": "LONG", "es_dir": "LONG", "smt_high": "NQ_ONLY", "smt_low": "NONE"}, "WARN"),
    ("SHORT", {"state": "CONFIRMED", "direction": "LONG", "es_dir": "LONG", "smt_high": "NONE", "smt_low": "NONE"}, "FAIL"),
    ("LONG", {"state": "DIVERGENT", "direction": None, "es_dir": "FLAT", "smt_high": "NONE", "smt_low": "NONE"}, "FAIL"),
    ("LONG", {"state": "MIXED", "direction": None, "es_dir": "FLAT", "smt_high": "NONE", "smt_low": "NONE"}, "WARN"),
])
def test_scenario_gate(direction, im, expected):
    assert scenario_gate(direction, im)[0] == expected


# ------------------------------------------------------------------ pipeline
def test_es_features_and_evidence_present(project):
    rep = run_pipeline(opts(project, write_files=False)).report
    snap = rep["market_snapshot"]
    assert snap["es"]["last_close"] and snap["intermarket"]["state"] == "CONFIRMED"
    keys = {e["key"] for e in rep["evidence"]}
    assert {"es.last_close", "im.state", "im.correlation", "es.options.call_wall"} <= keys
    # ID evidenze originali invariati (le ES sono aggiunte in coda)
    ids = {e["key"]: e["id"] for e in rep["evidence"]}
    assert ids["spot"] == "E001" and ids["atr_ratio"] == "E034" and ids["es.spot"] > "E034"


def test_es_never_pollutes_nq_levels(project):
    rep = run_pipeline(opts(project, write_files=False)).report
    for a in rep["agents"]["strategist"]["output"]["scenarios"]:
        for v in (a["invalidation"], a["target_1"], *a["entry_zone"]):
            assert v > 20000  # livelli NQ, mai ES (~6000)
    assert not any(i["type"] == "fabricated_level" for i in rep["judge"]["issues"])


def test_missing_es_degrades_to_caution_and_is_declared(project_no_es):
    res = run_pipeline(opts(project_no_es, write_files=False))
    rep = res.report
    assert rep["data_quality"]["status"] == "YELLOW" and any(i["code"] == "ES_FILE_MISSING" for i in rep["data_quality"]["issues"])
    assert rep["market_snapshot"]["intermarket"]["state"] == "UNKNOWN" and rep["market_snapshot"]["es"] is None
    assert res.status.value == "APPROVED_WITH_CAUTION"  # senza conferma ES mai APPROVED_SETUP
    assert not any(e["key"].startswith("es.") for e in rep["evidence"])  # nessuna evidenza ES inventata
    assert "ES non disponibile" in " ".join(rep["agents"]["price_action"]["output"]["warnings"])


def test_es_divergence_blocks_trade(project):
    d = project.path("data_dir")
    es = pd.read_csv(d / "ohlcv_es.csv")
    ts = pd.to_datetime(es["timestamp"])
    on = ts >= pd.Timestamp("2026-10-07 16:00:00")
    p0 = float(es.loc[~on, "close"].iloc[-1])
    m = es.loc[on].copy()
    for c in ("open", "high", "low", "close"):
        m[c] = 2 * p0 - m[c]
    m["high"], m["low"] = m[["high", "low"]].max(axis=1), m[["high", "low"]].min(axis=1)
    es.loc[on] = m
    es.to_csv(d / "ohlcv_es.csv", index=False)
    project.features.intermarket.min_correlation = -1.0  # isola la divergenza dalla correlazione
    res = run_pipeline(opts(project, write_files=False))
    im = res.report["market_snapshot"]["intermarket"]
    assert im["state"] == "DIVERGENT", im
    assert res.status.value == "NO_TRADE"
    assert any(e["event_type"] == "AGENT_CHALLENGE" for e in res.report["events"])


def test_decorrelated_es_caps_to_caution(tmp_path):
    import shutil
    from src.config import load_config
    from tests.conftest import ROOT
    from tools.sample_data import write
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "data").mkdir()
    write(tmp_path / "data", DATE, decorrelate_es=True)
    cfg = load_config(tmp_path / "config.yaml", root=tmp_path)
    res = run_pipeline(opts(cfg, write_files=False))
    assert res.report["market_snapshot"]["intermarket"]["state"] in ("DECORRELATED", "MIXED", "NEUTRAL", "DIVERGENT")
    assert res.status.value != "APPROVED_SETUP"


def test_invalid_es_file_is_warning_not_error(project):
    (project.path("data_dir") / "ohlcv_es.csv").write_text("timestamp,open,high,low,close,volume\nnot-a-date,1,2,0.5,1,5\n")
    res = run_pipeline(opts(project, write_files=False))
    assert res.report["data_quality"]["status"] == "YELLOW"
    assert res.report["market_snapshot"]["intermarket"]["state"] == "UNKNOWN"


def test_es_in_report_and_ui(project):
    from src.ui_state import build_view
    res = run_pipeline(opts(project))
    md = res.paths["report_md"].read_text()
    assert "ES / inter-mercato" in md and "NQ/ES:" in md and "CONFIRMED" in md
    keys = [t["k"] for t in build_view(res.report)["ticker"]]
    assert {"ES", "NQ/ES", "CORR"} <= set(keys)
