"""Caricamento e validazione di levels.json e ohlcv.csv. Nessun dato inventato."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import FeaturesCfg
from .schemas import DataQuality

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class DataValidationError(Exception):
    def __init__(self, issues: list["DataIssue"]) -> None:
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues if i.severity == "ERROR"))


@dataclass(frozen=True)
class DataIssue:
    severity: str  # "ERROR" | "WARNING"
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


class OptionsLevels(BaseModel):
    model_config = ConfigDict(extra="allow")
    gamma_flip: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    notes: str = ""


class LevelsBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    previous_session_high: float | None = None
    previous_session_low: float | None = None
    overnight_high: float | None = None
    overnight_low: float | None = None
    previous_week_high: float | None = None
    previous_week_low: float | None = None


class LevelsFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    date: str
    instrument: str = "NQ"
    spot: float | None = None
    source: str | None = None
    options: OptionsLevels = Field(default_factory=OptionsLevels)
    levels: LevelsBlock = Field(default_factory=LevelsBlock)


def _clean(v: float | None) -> float | None:
    """0 / None = valore mancante (il template usa 0): mai interpretato come prezzo."""
    return None if v is None or v == 0 else float(v)


def load_levels(path: Path, run_date: str) -> tuple[LevelsFile | None, list[DataIssue]]:
    issues: list[DataIssue] = []
    if not path.exists():
        return None, [DataIssue("ERROR", "LEVELS_FILE_MISSING", f"File mancante: {path.name}")]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        lv = LevelsFile(**raw)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return None, [DataIssue("ERROR", "LEVELS_INVALID", f"levels.json non valido: {str(exc)[:200]}")]

    # Normalizza gli zeri del template in valori mancanti.
    lv.spot = _clean(lv.spot)
    for name in ("gamma_flip", "call_wall", "put_wall"):
        setattr(lv.options, name, _clean(getattr(lv.options, name)))
    for name in LevelsBlock.model_fields:
        setattr(lv.levels, name, _clean(getattr(lv.levels, name)))

    if lv.date != run_date:
        issues.append(DataIssue("ERROR", "LEVELS_DATE_MISMATCH",
                                f"levels.json e' datato {lv.date}, richiesta analisi per {run_date}"))
    if lv.instrument.upper() != "NQ":
        issues.append(DataIssue("WARNING", "INSTRUMENT_NOT_NQ", f"Strumento atteso NQ, trovato {lv.instrument}"))
    if lv.spot is None:
        issues.append(DataIssue("ERROR", "SPOT_MISSING", "Spot mancante o pari a 0 in levels.json"))
    for name in ("gamma_flip", "call_wall", "put_wall"):
        if getattr(lv.options, name) is None:
            issues.append(DataIssue("WARNING", f"OPTIONS_{name.upper()}_MISSING",
                                    f"Dato opzioni mancante: options.{name} (non verra' inventato)"))
    for name in LevelsBlock.model_fields:
        if getattr(lv.levels, name) is None:
            issues.append(DataIssue("WARNING", f"LEVEL_{name.upper()}_MISSING",
                                    f"Livello non fornito in levels.json: levels.{name}"))
    if (lv.source or "").upper().startswith("SYNTHETIC"):
        issues.append(DataIssue("WARNING", "SYNTHETIC_DATA",
                                "DATI SINTETICI DI ESEMPIO: non sono dati di mercato reali"))
    return lv, issues


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def load_ohlcv(path: Path, fcfg: FeaturesCfg) -> tuple[pd.DataFrame | None, list[DataIssue]]:
    """Valida l'OHLCV senza riparare nulla in silenzio. Ritorna (df, issues)."""
    if not path.exists():
        return None, [DataIssue("ERROR", "OHLCV_FILE_MISSING", f"File mancante: {path.name}")]
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - errore di parsing riportato esplicitamente
        return None, [DataIssue("ERROR", "OHLCV_UNREADABLE", f"CSV illeggibile: {str(exc)[:200]}")]
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return None, [DataIssue("ERROR", "OHLCV_COLUMNS_MISSING", f"Colonne mancanti: {', '.join(missing)}")]
    issues: list[DataIssue] = []
    df = df[REQUIRED_COLUMNS].copy()

    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=False)
    if ts.isna().any():
        issues.append(DataIssue("ERROR", "TIMESTAMP_INVALID", f"{int(ts.isna().sum())} timestamp non validi"))
        return None, issues
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("America/New_York").dt.tz_localize(None)
    df["timestamp"] = ts

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        n = int(df[["open", "high", "low", "close", "volume"]].isna().any(axis=1).sum())
        issues.append(DataIssue("ERROR", "MISSING_VALUES", f"{n} righe con valori mancanti/non numerici"))
    if df["timestamp"].duplicated().any():
        issues.append(DataIssue("ERROR", "DUPLICATE_TIMESTAMPS",
                                f"{int(df['timestamp'].duplicated().sum())} timestamp duplicati"))
    if not df["timestamp"].is_monotonic_increasing:
        issues.append(DataIssue("ERROR", "NOT_CHRONOLOGICAL", "Timestamp non in ordine cronologico"))
    if (df["volume"] < 0).any():
        issues.append(DataIssue("ERROR", "NEGATIVE_VOLUME", "Volume negativo presente"))
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        issues.append(DataIssue("ERROR", "NON_POSITIVE_PRICE", "Prezzi <= 0 presenti"))
    bad = (df["high"] < df[["open", "close", "low"]].max(axis=1)) | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    if bad.any():
        issues.append(DataIssue("ERROR", "OHLC_INCONSISTENT", f"{int(bad.sum())} barre con OHLC incoerente"))
    if any(i.severity == "ERROR" for i in issues):
        return None, issues

    modal = df["timestamp"].diff().dropna().mode()
    if modal.empty or modal.iloc[0] != pd.Timedelta(minutes=fcfg.timeframe_minutes):
        issues.append(DataIssue("ERROR", "TIMEFRAME_MISMATCH",
                                f"Timeframe atteso {fcfg.timeframe_minutes}m, rilevato "
                                f"{modal.iloc[0] if not modal.empty else 'n/d'}"))
        return None, issues
    return df.reset_index(drop=True), issues


def rth_mask(df: pd.DataFrame, fcfg: FeaturesCfg) -> pd.Series:
    t = df["timestamp"].dt.time
    return (t >= _hhmm(fcfg.rth_start)) & (t < _hhmm(fcfg.rth_end))


@dataclass
class MarketData:
    date: str
    levels: LevelsFile | None
    bars: pd.DataFrame | None
    prev_session_date: str | None
    sessions: list[str]
    issues: list[DataIssue]
    data_hash: str
    quality: DataQuality
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[DataIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> list[DataIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]


def _file_hash(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def prepare_market_data(levels_path: Path, ohlcv_path: Path, run_date: str, fcfg: FeaturesCfg) -> MarketData:
    """Valida i due file e restituisce i dati utilizzabili per `run_date` (no look-ahead)."""
    levels, issues = load_levels(levels_path, run_date)
    df, ohlcv_issues = load_ohlcv(ohlcv_path, fcfg)
    issues = issues + ohlcv_issues
    dhash = _file_hash(levels_path, ohlcv_path)

    def finish(bars: pd.DataFrame | None, prev: str | None, sessions: list[str]) -> MarketData:
        if any(i.severity == "ERROR" for i in issues):
            q = DataQuality.RED
        elif issues:
            q = DataQuality.YELLOW
        else:
            q = DataQuality.GREEN
        return MarketData(run_date, levels, bars, prev, sessions, issues, dhash, q)

    if df is None:
        return finish(None, None, [])

    try:
        run_dt = datetime.strptime(run_date, "%Y-%m-%d")
    except ValueError:
        issues.append(DataIssue("ERROR", "DATE_INVALID", f"Data non valida: {run_date}"))
        return finish(None, None, [])
    cutoff = pd.Timestamp(datetime.combine(run_dt.date(), _hhmm(fcfg.rth_start)))
    future = int((df["timestamp"] >= cutoff).sum())
    if future:
        issues.append(DataIssue("WARNING", "FUTURE_BARS_IGNORED",
                                f"{future} barre dalle {fcfg.rth_start} del {run_date} in poi ignorate (no look-ahead)"))
    df = df[df["timestamp"] < cutoff].reset_index(drop=True)
    if df.empty:
        issues.append(DataIssue("ERROR", "NO_BARS_BEFORE_DATE", "Nessuna barra precedente alla data richiesta"))
        return finish(None, None, [])

    mask = rth_mask(df, fcfg)
    expected = int((pd.Timestamp.combine(run_dt.date(), _hhmm(fcfg.rth_end))
                    - pd.Timestamp.combine(run_dt.date(), _hhmm(fcfg.rth_start))).total_seconds()
                   // 60 // fcfg.timeframe_minutes)
    counts = df[mask].groupby(df["timestamp"].dt.date).size()
    valid_sessions: list[str] = []
    for d, n in counts.items():
        if n >= 0.9 * expected:
            valid_sessions.append(str(d))
        else:
            issues.append(DataIssue("WARNING", "SESSION_INCOMPLETE",
                                    f"Sessione {d} incompleta ({n}/{expected} barre): non conteggiata"))
    if len(valid_sessions) < fcfg.min_sessions:
        issues.append(DataIssue("ERROR", "INSUFFICIENT_HISTORY",
                                f"Sessioni complete disponibili: {len(valid_sessions)}, minimo richiesto {fcfg.min_sessions}"))
        return finish(None, valid_sessions[-1] if valid_sessions else None, valid_sessions)

    prev = valid_sessions[-1]
    prev_close_ts = pd.Timestamp.combine(datetime.strptime(prev, "%Y-%m-%d").date(), _hhmm(fcfg.rth_end))
    if not ((df["timestamp"] >= prev_close_ts) & (df["timestamp"] < cutoff)).any():
        issues.append(DataIssue("ERROR", "OVERNIGHT_MISSING",
                                f"Nessuna barra overnight tra {prev} {fcfg.rth_end} e {run_date} {fcfg.rth_start}"))
        return finish(None, prev, valid_sessions)
    return finish(df, prev, valid_sessions)
