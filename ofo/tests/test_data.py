import pandas as pd

from ofo.data import load_ohlcv, resample, split_blocked, validate


def test_ninjatrader_roundtrip_and_resample(tmp_path):
    p = tmp_path / "nq.txt"
    p.write_text("20250102 093000;100;101;99;100.5;10\n"
                 "20250102 093100;100.5;102;100;101;12\n"
                 "20250102 093500;101;103;100.5;102;8\n")
    df = load_ohlcv(str(p), "ninjatrader")
    assert validate(df)["inconsistent_ohlc_rows"] == 0
    r = resample(df, "5min")
    assert len(r) == 2 and r.iloc[0]["high"] == 102 and r.iloc[0]["volume"] == 22


def test_validate_flags_bad_rows():
    df = pd.DataFrame({"open": [10], "high": [9], "low": [8], "close": [10], "volume": [1]},
                      index=pd.to_datetime(["2025-01-02"]))
    assert validate(df)["inconsistent_ohlc_rows"] == 1


def test_split_is_chronological():
    df = pd.DataFrame({"close": range(10)}, index=pd.date_range("2025-01-01", periods=10))
    a, b = split_blocked(df, 0.3)
    assert len(a) == 7 and a.index.max() < b.index.min()


def test_walk_forward_test_windows_never_overlap_training():
    from ofo.data import walk_forward_windows
    idx = pd.date_range("2025-01-01", periods=100, freq="D")
    wins = list(walk_forward_windows(idx, train_days=40, test_days=20))
    assert len(wins) == 3
    for (tr0, tr1), (te0, te1) in wins:
        assert tr1 < te0
    assert wins[1][1][0] == wins[0][1][1] + pd.Timedelta(days=1)


def test_massive_format_filters_ticker_and_converts_tz(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("ticker,volume,open,close,high,low,window_start,transactions\n"
                 "AAPL,10,200.29,200.5,200.63,200.29,1744792500000000000,129\n"
                 "NQM5,5,100,101,102,99,1744792560000000000,3\n")
    df = load_ohlcv(str(p), "massive", ticker="NQM5")
    assert len(df) == 1 and df.iloc[0]["close"] == 101
    # 1744792560 s = 2025-04-16 08:36:00 UTC = 04:36 New York (EDT)
    assert df.index[0] == pd.Timestamp("2025-04-16 04:36:00")
