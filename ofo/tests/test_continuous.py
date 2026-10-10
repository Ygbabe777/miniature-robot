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
