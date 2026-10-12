import numpy as np

from ofo.metrics import chart_pack, drawdown, equity, monte_carlo, perf_metrics, rolling_sharpe


def test_basic_numbers_by_hand():
    x = [100, -50, 200, -100, 50]
    m = perf_metrics(x)
    assert m["total_pnl"] == 200 and m["win_rate"] == 0.6
    assert abs(m["profit_factor"] - 350 / 150) < 1e-12
    assert abs(m["payoff_ratio"] - (350 / 3) / 75) < 1e-12
    assert m["max_drawdown"] == -100 and m["longest_losing_streak"] == 1
    assert list(drawdown(x)) == [0, -50, 0, -100, -50]
    assert list(equity(x)) == [100, 50, 250, 150, 200]


def test_sortino_ignores_upside_and_sharpe_does_not():
    calm = np.array([100, 100, -10, 100, 100, -10] * 20, float)
    wild_up = np.array([400, 100, -10, 100, 400, -10] * 20, float)       # same downside, bigger upside
    assert perf_metrics(wild_up)["sortino"] > perf_metrics(calm)["sortino"]


def test_profit_factor_none_when_no_losses_and_short_series_safe():
    assert perf_metrics([1, 2, 3])["profit_factor"] is None
    assert perf_metrics([5]) == {"days": 1}


def test_monte_carlo_fan_is_ordered_and_reflects_edge():
    rng = np.random.default_rng(0)
    good = monte_carlo(rng.normal(80, 300, 250), horizon=60, n_paths=800)
    bad = monte_carlo(rng.normal(-80, 300, 250), horizon=60, n_paths=800)
    p = good["percentiles"]
    assert all(p["5"][i] <= p["50"][i] <= p["95"][i] for i in range(60))
    assert good["final"]["p50"] > 0 > bad["final"]["p50"] and good["p_loss"] < bad["p_loss"]
    assert monte_carlo([1, 2, 3])["error"]


def test_chart_pack_is_json_serialisable_and_aligned():
    import json
    d = [f"2025-01-{i + 1:02d}" for i in range(30)]
    pack = chart_pack(d, np.random.default_rng(1).normal(10, 100, 30), horizon=20)
    json.dumps(pack)
    assert len(pack["dates"]) == len(pack["pnl"]) == len(pack["equity"]) == len(pack["rolling_sharpe"]) == 30
    assert rolling_sharpe([1.0] * 5, window=40) == [None] * 5
