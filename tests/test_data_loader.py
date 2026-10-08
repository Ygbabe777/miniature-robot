import json
from pathlib import Path

import pandas as pd

from src.data_loader import load_levels, load_ohlcv, prepare_market_data
from src.schemas import DataQuality
from tests.conftest import DATE


def codes(md):
    return {i.code for i in md.issues}


def test_valid_sample_is_yellow_only_for_synthetic(project):
    md = prepare_market_data(project.path("data_dir") / "levels.json", project.path("data_dir") / "ohlcv.csv",
                             DATE, project.features)
    assert md.quality == DataQuality.YELLOW and codes(md) == {"SYNTHETIC_DATA"}
    assert len(md.sessions) >= 3 and md.prev_session_date == "2026-10-07"


def test_no_lookahead(project):
    d = project.path("data_dir")
    df = pd.read_csv(d / "ohlcv.csv")
    extra = pd.DataFrame([[f"{DATE} 09:30:00", 1.0, 2.0, 1.0, 2.0, 10]], columns=df.columns)
    pd.concat([df, extra]).to_csv(d / "ohlcv.csv", index=False)
    md = prepare_market_data(d / "levels.json", d / "ohlcv.csv", DATE, project.features)
    assert "FUTURE_BARS_IGNORED" in codes(md)
    assert md.bars["timestamp"].max() < pd.Timestamp(f"{DATE} 09:30:00")


def test_missing_options_are_warnings_not_invented(project_no_options):
    d = project_no_options.path("data_dir")
    md = prepare_market_data(d / "levels.json", d / "ohlcv.csv", DATE, project_no_options.features)
    assert {"OPTIONS_GAMMA_FLIP_MISSING", "OPTIONS_CALL_WALL_MISSING", "OPTIONS_PUT_WALL_MISSING"} <= codes(md)
    assert md.levels.options.gamma_flip is None  # lo 0 del template non diventa un prezzo
    assert md.quality == DataQuality.YELLOW


def test_zero_spot_is_red(project):
    d = project.path("data_dir")
    lv = json.loads((d / "levels.json").read_text())
    lv["spot"] = 0
    (d / "levels.json").write_text(json.dumps(lv))
    md = prepare_market_data(d / "levels.json", d / "ohlcv.csv", DATE, project.features)
    assert md.quality == DataQuality.RED and "SPOT_MISSING" in codes(md)


def test_levels_date_mismatch_is_red(project):
    d = project.path("data_dir")
    lv = json.loads((d / "levels.json").read_text())
    lv["date"] = "2026-10-07"
    (d / "levels.json").write_text(json.dumps(lv))
    md = prepare_market_data(d / "levels.json", d / "ohlcv.csv", DATE, project.features)
    assert md.quality == DataQuality.RED and "LEVELS_DATE_MISMATCH" in codes(md)


def test_extra_fields_allowed(project):
    d = project.path("data_dir")
    lv = json.loads((d / "levels.json").read_text())
    lv["custom"] = {"x": 1}
    lv["options"]["extra"] = 5
    (d / "levels.json").write_text(json.dumps(lv))
    lvf, issues = load_levels(d / "levels.json", DATE)
    assert lvf is not None and lvf.model_extra["custom"] == {"x": 1}


def _mutate(project, fn):
    d = project.path("data_dir")
    df = pd.read_csv(d / "ohlcv.csv")
    fn(df)
    df.to_csv(d / "ohlcv.csv", index=False)
    return load_ohlcv(d / "ohlcv.csv", project.features)


def test_ohlcv_duplicates_error(project):
    df, issues = _mutate(project, lambda x: x.__setitem__("timestamp", x["timestamp"].where(x.index != 5, x["timestamp"].iloc[4])))
    assert df is None and any(i.code == "DUPLICATE_TIMESTAMPS" for i in issues)


def test_ohlcv_ohlc_inconsistent(project):
    df, issues = _mutate(project, lambda x: x.__setitem__("high", x["high"].where(x.index != 10, 1.0)))
    assert df is None and any(i.code == "OHLC_INCONSISTENT" for i in issues)


def test_ohlcv_negative_volume_and_nan(project):
    df, issues = _mutate(project, lambda x: x.__setitem__("volume", x["volume"].where(x.index != 3, -5)))
    assert df is None and any(i.code == "NEGATIVE_VOLUME" for i in issues)
    df, issues = _mutate(project, lambda x: x.__setitem__("close", x["close"].where(x.index != 3, float("nan"))))
    assert df is None and any(i.code == "MISSING_VALUES" for i in issues)


def test_ohlcv_not_chronological(project):
    d = project.path("data_dir")
    df = pd.read_csv(d / "ohlcv.csv")
    df.iloc[[1, 0] + list(range(2, len(df)))].to_csv(d / "ohlcv.csv", index=False)
    out, issues = load_ohlcv(d / "ohlcv.csv", project.features)
    assert out is None and any(i.code == "NOT_CHRONOLOGICAL" for i in issues)


def test_insufficient_history_is_red(project):
    d = project.path("data_dir")
    df = pd.read_csv(d / "ohlcv.csv")
    df = df[pd.to_datetime(df["timestamp"]) >= pd.Timestamp("2026-10-05 18:00:00")]
    df.to_csv(d / "ohlcv.csv", index=False)
    md = prepare_market_data(d / "levels.json", d / "ohlcv.csv", DATE, project.features)
    assert md.quality == DataQuality.RED and "INSUFFICIENT_HISTORY" in codes(md)


def test_missing_files_red(project, tmp_path):
    md = prepare_market_data(tmp_path / "no.json", tmp_path / "no.csv", DATE, project.features)
    assert md.quality == DataQuality.RED and {"LEVELS_FILE_MISSING", "OHLCV_FILE_MISSING"} <= codes(md)
