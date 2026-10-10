import gzip

import pandas as pd

from ofo.futures import build_root, expiry_key

HEADER = "ticker,exchange,session_end_date,window_start,open,high,low,close,volume,dollar_volume,transactions\n"


def _row(t, ts, price, vol):
    ns = int(pd.Timestamp(ts, tz="UTC").value)
    return f"{t},4,2025-11-05,{ns},{price},{price+1},{price-1},{price},{vol},0,1\n"


def _write(path, rows):
    with gzip.open(path, "wt") as f:
        f.write(HEADER + "".join(rows))


def test_spreads_and_tas_dropped_and_roll_is_seamless(tmp_path):
    d1, d2 = tmp_path / "2025-11-05.csv.gz", tmp_path / "2025-11-06.csv.gz"
    # day 1: Z5 liquid at 100, H6 thin at 105; day 2: H6 takes over
    r1 = [_row("NQZ5", f"2025-11-05 15:0{m}", 100.0, 500) for m in range(3)]
    r1 += [_row("NQH6", f"2025-11-05 15:0{m}", 105.0, 1) for m in range(3)]
    r1 += [_row("NQZ5-NQH6", "2025-11-05 15:00", -5, 9), _row("NQTZ5", "2025-11-05 15:00", 99, 9)]
    r2 = [_row("NQH6", f"2025-11-06 15:0{m}", 105.0, 500) for m in range(3)]
    r2 += [_row("NQZ5", f"2025-11-06 15:0{m}", 100.0, 1) for m in range(3)]
    _write(d1, r1); _write(d2, r2)
    cont = build_root([d1, d2], "NQ")
    assert set(cont.columns) >= {"open", "close", "volume", "session"}
    assert cont["close"].diff().abs().max() < 1e-9      # 5-point spread is not P&L
    assert cont["close"].iloc[-1] == 105.0


def test_expiry_order_resolves_decade():
    t = pd.Timestamp("2025-11-05")
    assert expiry_key("NQZ5", t) < expiry_key("NQH6", t) < expiry_key("NQM6", t)
