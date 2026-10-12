import pandas as pd

from ofo.continuous import build_continuous


def _contract(start, end, price, vol_by_day):
    idx = pd.date_range(start, end, freq="h")
    df = pd.DataFrame({"open": price, "high": price + 1, "low": price - 1,
                       "close": price, "volume": 1.0}, index=idx)
    df["volume"] = [vol_by_day(t) for t in idx]
    return df


def test_no_jump_at_roll_and_additive_adjustment():
    # near contract trades at 100, far at 105: a 5-point spread that must not become P&L
    near = _contract("2025-03-01", "2025-03-10 23:00", 100.0, lambda t: 100 if t.day < 6 else 10)
    far = _contract("2025-03-03", "2025-03-20 23:00", 105.0, lambda t: 10 if t.day < 6 else 100)
    cont = build_continuous([near, far])
    assert not cont.index.duplicated().any()
    assert cont["close"].diff().abs().max() < 1e-9      # flat series stays flat
    assert cont["close"].iloc[-1] == 105.0               # newest contract unadjusted
    assert cont["close"].iloc[0] == 105.0                # old data shifted by +5


def test_thin_contract_crossing_early_does_not_skip_the_middle_contract():
    # B is thin (1) while A is front; C is slightly less thin early (2), so b>a "crosses" at listing;
    # C only really overtakes B on day 18. The noise "crossing" at listing must be ignored.
    def vol_a(t): return 1000 if t.day < 10 else 5
    def vol_b(t): return 1 if t.day < 10 else (1000 if t.day < 18 else 5)
    def vol_c(t): return 2 if t.day < 18 else 1000
    a = _contract("2025-03-01", "2025-03-12 23:00", 100.0, vol_a)
    b = _contract("2025-03-01", "2025-03-20 23:00", 105.0, vol_b)
    c = _contract("2025-03-01", "2025-03-28 23:00", 112.0, vol_c)
    cont = build_continuous([a, b, c], names=["A", "B", "C"])
    mid = cont.loc["2025-03-14 12:00"]
    assert mid["volume"] == 1000                      # served by B, not by the thin C
    assert [r[:2] for r in cont.attrs["rolls"]] == [("A", "B"), ("B", "C")]
    assert cont["close"].diff().abs().max() < 1e-9    # still seamless
