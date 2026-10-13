import numpy as np

from ofo.prop import PRESETS
from ofo.sizing import DD_HEADROOM, max_drawdown, size_study


def test_max_drawdown_and_scale_invariance_of_the_ratio():
    assert max_drawdown([100, -50, -70, 200]) == 120
    rng = np.random.default_rng(0)
    pnl = rng.normal(40, 120, 250)
    r = size_study(pnl, {"50k": PRESETS["50k"]}, 2, n_sims=40)["accounts"]["50k"]["table"]
    ratios = [x["profit_dd"] for x in r]
    assert max(ratios) - min(ratios) < 1e-9                 # profit/drawdown cannot choose a size: it is the same at every size


def test_recommended_size_is_the_largest_within_the_drawdown_allowance():
    rng = np.random.default_rng(1)
    pnl = rng.normal(30, 80, 250)
    out = size_study(pnl, {"25k": PRESETS["25k"], "50k": PRESETS["50k"]}, 2, n_sims=40)
    for acct, rules in (("25k", PRESETS["25k"]), ("50k", PRESETS["50k"])):
        a = out["accounts"][acct]
        rec = a["recommended"]
        if rec is None:
            continue
        row = next(r for r in a["table"] if r["micro"] == rec)
        assert row["max_dd"] <= DD_HEADROOM * rules.max_drawdown
        bigger = [r for r in a["table"] if r["micro"] > rec]
        assert all(not r["within_limits"] for r in bigger)
    assert out["accounts"]["50k"]["recommended"] >= out["accounts"]["25k"]["recommended"]   # a bigger allowance never means a smaller size
