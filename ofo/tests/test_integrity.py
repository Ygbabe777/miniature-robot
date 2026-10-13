import dataclasses
import json
import threading

import numpy as np
import pandas as pd
import pytest

from ofo.backtest import MNQ
from ofo.exits import exit_grid
from ofo.ledger import AccountingError
from ofo.optimizer import (SurfaceThresholds, _session_key, _sweep, causality_probe, dataset_fingerprint, guarded_full_series,
                           nested_walk_forward, quality_checks, run_study, surface_metrics)
from ofo.prop import PRESETS
from ofo.registry import DuplicateExperiment, TrialRegistry
from ofo.data import resample
from ofo.specs import from_dict
from ofo.strategies import flatten_overnight
from test_research import synth

SPEC = {"name": "bo", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60]},
        "timeframes": ["15min", "30min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"}


def alter_future(bars: pd.DataFrame, frac: float, seed: int = 99) -> pd.DataFrame:
    """Same data up to the cut, a different random future after it."""
    key = _session_key(bars)
    days = sorted(key.unique())
    cut = days[int(len(days) * frac)]
    mask = np.asarray(key >= cut)
    alt = bars.copy()
    rng = np.random.default_rng(seed)
    noise = np.cumsum(rng.normal(0, 3, mask.sum()))
    shift = (alt.loc[~mask, "close"].iloc[-1] + noise) - alt.loc[mask, "close"].to_numpy()
    for c in ("open", "high", "low", "close"):
        alt.loc[mask, c] = alt.loc[mask, c].to_numpy() + shift
    return alt


def dev_view(r: dict) -> str:
    keep = {"surface": r["surface"], "quality": r["quality"], "wf": (r["wf_kind"], r["wf_oos_days"], round(r["wf_oos_pnl"], 6)),
            "cut": r["series"]["cut"], "best": r["series"]["best_params"], "exit": r["series"]["exit"], "exits": r["exits"],
            "dev_best": r["series"]["dev_best"], "wf_series": r["series"]["wf_oos"],
            "detrended": r["surface_detrended"]}
    return json.dumps(keep, sort_keys=True, default=str)


def test_hidden_block_cannot_influence_anything_the_optimizer_or_the_agents_see(tmp_path):
    """Replace the final 25% of the data with a different random future: every development-side output (surface,
    walk-forward, stop/target table INCLUDING its trade statistics, quality checks, beta check) must be identical."""
    bars = synth(n_sessions=300, drift_ar=0.9, seed=5)
    alt = alter_future(bars, 0.75)
    spec = from_dict(SPEC)
    a = run_study(bars, spec, MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "a.json"))
    b = run_study(alt, spec, MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "b.json"))
    assert a["wf_kind"] == "nested" and a["quality"] is not None           # the deep path really ran
    assert dev_view(a) == dev_view(b)


def test_sweep_receives_no_locked_data_and_registry_records_the_single_look(tmp_path):
    bars = synth(n_sessions=520, drift_ar=0.9, seed=5)
    reg = TrialRegistry(tmp_path / "r.json")
    rep = run_study(bars, from_dict(SPEC), MNQ, PRESETS["50k"], reg)
    assert rep["ledger_check"]["ok"] and rep["locked"]["status"] in ("APPROVED", "REJECTED", "INSUFFICIENT_EVIDENCE")
    assert "checks" in rep["locked"] and rep["locked"]["prop_model"] == "daily_simplified"
    assert reg.data["locked_looks"] == 1 and reg.locked_looks_on(rep["dataset_fp"]) == 1
    row = reg._con.execute("SELECT experiment_id, dataset_fp, oos_start FROM locked_looks").fetchone()
    assert row == (rep["experiment_id"], rep["dataset_fp"], rep["series"]["cut"])
    ex = [e for e in reg.experiments() if e["kind"] == "exits"]
    assert ex and ex[0]["n_trials"] == len(exit_grid())


def test_nested_walk_forward_never_uses_the_future_to_choose_earlier_windows():
    bars = synth(n_sessions=300, drift_ar=0.9, seed=5)
    alt = alter_future(bars, 0.7)                               # change everything from 70% on
    spec = from_dict(SPEC)

    def run(b):
        key = _session_key(b); days = sorted(key.unique()); cut = days[int(len(days) * 0.75)]
        dev_days = [d for d in days if d < cut]
        dev = b[np.asarray(key < cut)]
        cands, series = _sweep(dev, spec, MNQ, 2, 1.0)
        tfs = {tf: resample(dev, tf) for tf in spec.timeframes}
        wf, picks = nested_walk_forward(tfs, cands, series, dev_days, MNQ, 2, 1.0, 60, 10)
        return wf, dev_days, days[int(len(days) * 0.7)]
    wa, dev_days, change = run(bars)
    wb, _, _ = run(alt)
    early_a, early_b = wa[wa.index < change], wb[wb.index < change]
    assert len(early_a) > 30 and np.allclose(early_a.to_numpy(), early_b.to_numpy())
    assert not np.allclose(wa[wa.index >= change].to_numpy(), wb[wb.index >= change].to_numpy())   # while the future part does differ


def test_nested_walk_forward_includes_exits_the_final_strategy_uses():
    """The stitched series must be able to differ from the entry-only walk-forward: exits are part of what is tested."""
    from ofo.research import walk_forward
    bars = synth(n_sessions=300, drift_ar=0.9, seed=5)
    spec = from_dict(SPEC)
    key = _session_key(bars); days = sorted(key.unique()); cut = days[int(len(days) * 0.75)]
    dev_days = [d for d in days if d < cut]; dev = bars[np.asarray(key < cut)]
    cands, series = _sweep(dev, spec, MNQ, 2, 1.0)
    tfs = {tf: resample(dev, tf) for tf in spec.timeframes}
    nested, picks = nested_walk_forward(tfs, cands, series, dev_days, MNQ, 2, 1.0, 60, 10)
    plain, _ = walk_forward(series, 60, 10)
    assert len(nested) == len(plain) and all(p[0] is not None for p in picks)
    assert set(p[1]["stop_atr"] for p in picks if p[1]) <= {g["stop_atr"] for g in exit_grid()}


# -- causality and guards ---------------------------------------------------------------------
def test_causality_probe_catches_lookahead_and_passes_the_real_templates():
    from ofo.strategies import TEMPLATES
    b = resample(synth(n_sessions=40, seed=2), "15min")
    assert causality_probe(TEMPLATES["breakout"].fn, {"lookback": 20}, b)
    assert causality_probe(TEMPLATES["orb"].fn, {"or_minutes": 30}, b)
    peek = lambda bars, **kw: (bars["close"].shift(-3) > bars["close"]).astype(float)       # uses the future
    assert not causality_probe(peek, {}, b)


def test_guard_raises_instead_of_judging_a_strategy_that_peeks(tmp_path):
    bars = synth(n_sessions=60, seed=2)
    peek = lambda b, **kw: (b["close"].shift(-3) > b["close"]).astype(float)
    cand = {"fn": peek, "params": {}, "tf": "15min"}
    key = _session_key(bars); days = sorted(key.unique())
    with pytest.raises(AccountingError):
        guarded_full_series(bars, cand, None, MNQ, 2, 1.0, days[:40], pd.Series(0.0, index=days[:40]), "peek")


# -- false-positive filters ---------------------------------------------------------------------
def staircase(n_sessions: int, gap: bool):
    """Each session: signal at bar 2's close, hold to bar 8's close. The price moves ~1.8 points over that span, worth
    about 1.6x the round-trip costs of 2 MNQ: profitable at 1x costs, not at 2x. With gap=True most of it is a jump at
    the OPEN of bar 3, which a same-close fill collects and a next-open fill cannot."""
    rng = np.random.default_rng(1)
    rows, sess = [], []
    for d in pd.bdate_range("2024-01-02", periods=n_sessions):
        base = 1000 + rng.normal(0, 5)
        z = rng.normal(0, 0.35)
        closes = [base, base, base, base + (1.5 if gap else 0.9) + z, base + 1.6 + z, base + 1.7, base + 1.9 + z,
                  base + 1.8 + z, base + 1.8 + z, base + 1.8 + z]
        opens = [base] + closes[:-1]
        if gap:
            opens[3] = base + 1.5
        for i, (o, c) in enumerate(zip(opens, closes)):
            ts = d + pd.Timedelta(hours=9, minutes=30 + 5 * i)
            rows.append((ts, o, max(o, c) + 0.05, min(o, c) - 0.05, c)); sess.append(d)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
    df["volume"] = 1.0; df["session"] = sess
    return df


def hold_fn(bars, **kw):
    pos = np.zeros(len(bars))
    for s in sorted(set(bars["session"])):
        ix = np.flatnonzero((bars["session"] == s).to_numpy())
        pos[ix[2]:ix[8]] = 1.0
    return pd.Series(pos, index=bars.index)


def test_quality_checks_flag_few_trades_cost_fragility_and_gap_only_edges():
    th = SurfaceThresholds()
    cand = {"fn": hold_fn, "params": {}, "tf": "5min"}
    thin = staircase(30, gap=False)
    days = sorted(thin["session"].unique())
    q = quality_checks({"5min": thin}, cand, None, MNQ, 2, 1.0, days, th)
    assert q["n_trades"] == 30 and not q["checks"]["min_trades"] and not q["ok"]            # 30 trades prove nothing
    assert q["sharpe_1x"] > 0 and q["sharpe_2x_costs"] < 0 and not q["checks"]["cost_2x"]  # edge smaller than 2x the costs
    gappy = staircase(150, gap=True)
    days = sorted(gappy["session"].unique())
    q2 = quality_checks({"5min": gappy}, cand, None, MNQ, 2, 1.0, days, th)
    assert q2["checks"]["min_trades"] and q2["sharpe_1x"] > 0 and q2["sharpe_next_open"] < 0 and not q2["checks"]["next_open_fill"]


# -- parameters vs timeframes -------------------------------------------------------------------
def test_timeframe_robustness_is_separate_from_parameter_robustness():
    rows = []
    for tf, level in (("15min", 0.10), ("30min", -0.05), ("60min", -0.06)):
        for a in range(6):
            rows.append({"a": a, "tf": tf, "sharpe": level - 0.001 * abs(a - 3)})
    m = surface_metrics(rows, ["a"])
    assert m["checks"]["plateau"] and m["checks"]["neighbours_positive"]                   # parameters: smooth plateau
    assert not m["checks"]["tf_consistency"] and m["tf_positive_frac"] < 0.5                # but it only works at one timeframe
    assert m["by_tf"]["15min"]["median_sharpe"] > 0 and m["by_tf"]["30min"]["median_sharpe"] < 0 and not m["robust"]
    # neighbours are looked for at the SAME timeframe only
    assert m["n_neighbours"] == 2
    one_tf = surface_metrics([{**r} for r in rows if r["tf"] == "15min"], ["a"])
    assert one_tf["checks"]["tf_consistency"]                                               # a single timeframe: nothing to compare


# -- registry ----------------------------------------------------------------------------------------
def test_registry_imports_the_old_json_history_and_leaves_the_file_alone(tmp_path):
    old = {"n_trials": 1747, "locked_looks": 3, "log": [
        {"event": "trials", "n": 1000, "label": "study:A"}, {"event": "trials", "n": 700, "label": "study:B"},
        {"event": "locked_look", "label": "A:{}", "status": "REJECTED"}, {"event": "locked_look", "label": "B:{}", "status": "REJECTED"}]}
    (tmp_path / "registry.json").write_text(json.dumps(old))
    before = (tmp_path / "registry.json").read_text()
    reg = TrialRegistry(tmp_path / "registry.json")
    assert reg.n_trials == 1747 + 3 and reg.data["locked_looks"] == 3                        # 47 trials + 1 look not itemised: kept
    assert (tmp_path / "registry.json").read_text() == before and (tmp_path / "registry.db").exists()
    reg.close()
    again = TrialRegistry(tmp_path / "registry.json")                                          # a restart does not import twice
    assert again.n_trials == 1750
    labels = [e["label"] for e in again.experiments(10)]
    assert "study:A" in labels and "study:B" in labels


def test_registry_dedupes_same_strategy_same_data_but_allows_new_data(tmp_path):
    reg = TrialRegistry(tmp_path / "r.json")
    e1 = reg.begin_experiment("s", 10, spec_fp="f1", dataset_fp="d1", dedupe=True)
    with pytest.raises(DuplicateExperiment) as e:
        reg.begin_experiment("s", 10, spec_fp="f1", dataset_fp="d1", dedupe=True)
    assert e.value.experiment_id == e1 and reg.seen("f1", "d1") == e1 and reg.n_trials == 10   # the duplicate added nothing
    reg.begin_experiment("s", 10, spec_fp="f1", dataset_fp="d2", dedupe=True)                  # more data: a new experiment
    assert reg.n_trials == 20


def test_registry_is_transactional_and_thread_safe(tmp_path):
    reg = TrialRegistry(tmp_path / "r.json")
    with pytest.raises(RuntimeError):
        def boom(con):
            con.execute("INSERT INTO experiments(id,created,kind,label,n_trials) VALUES ('x','now','trials','half',5)")
            raise RuntimeError("crash in the middle")
        reg._tx(boom)
    assert reg.n_trials == 0                                                                       # nothing half-written
    threads = [threading.Thread(target=lambda: [reg.add_trials(1, "t") for _ in range(25)]) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert reg.n_trials == 200
    reopened = TrialRegistry(tmp_path / "r.json")
    assert reopened.n_trials == 200                                                                # durable across reopen


def test_study_dedupe_through_the_loop_skips_a_repeat(tmp_path):
    from ofo.agents.loop import ResearchLoop
    from ofo.events import EventLog
    bars = synth(n_sessions=200, seed=4)
    reg = TrialRegistry(tmp_path / "r.json")
    spec = from_dict({**SPEC, "ranges": {"lookback": [10, 20]}})
    lp = ResearchLoop(bars, MNQ, PRESETS["50k"], reg, {"researcher": None, "planner": None, "auditor": None},
                      EventLog(tmp_path / "e.jsonl"), tmp_path / "w")
    first = lp.process(spec, 0)
    n = reg.n_trials
    lp.done_specs.clear()                                                                          # even if the loop forgot
    second = lp.process(spec, 0)
    assert first["verdict"] != "DUPLICATE" and second["verdict"] == "DUPLICATE" and reg.n_trials == n
