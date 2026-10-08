"""Calcoli deterministici: VWAP, ATR, Volume Profile, sessioni, regime, evidenze.

Gli LLM ricevono questi risultati: non devono ricalcolarli dalle candele.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .config import FeaturesCfg
from .data_loader import MarketData, _hhmm, rth_mask
from .intermarket import UNKNOWN as IM_UNKNOWN, compute_intermarket


# --------------------------------------------------------------------------- VWAP / ATR
def vwap(df: pd.DataFrame) -> float | None:
    """VWAP ancorato all'inizio di `df` (prezzo tipico (H+L+C)/3 pesato per volume)."""
    if df is None or df.empty:
        return None
    vol = df["volume"].to_numpy(dtype=float)
    total = vol.sum()
    if total <= 0:
        return None
    tp = ((df["high"] + df["low"] + df["close"]) / 3.0).to_numpy(dtype=float)
    return float((tp * vol).sum() / total)


def true_range(df: pd.DataFrame) -> np.ndarray:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    prev_close = np.concatenate(([np.nan], df["close"].to_numpy(dtype=float)[:-1]))
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    tr[0] = high[0] - low[0]
    return tr


def atr_series(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """ATR di Wilder. Le prime `period-1` posizioni sono NaN; seed = media semplice."""
    tr = true_range(df)
    out = np.full(len(tr), np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def atr(df: pd.DataFrame, period: int = 14) -> float | None:
    s = atr_series(df, period)
    return None if len(s) == 0 or np.isnan(s[-1]) else float(s[-1])


# --------------------------------------------------------------------------- Volume Profile
@dataclass
class VolumeProfile:
    poc: float
    vah: float
    val: float
    hvn: list[float]
    lvn: list[float]
    total_volume: float
    bin_size: float

    def to_dict(self) -> dict[str, Any]:
        return {"poc": self.poc, "vah": self.vah, "val": self.val, "hvn": self.hvn,
                "lvn": self.lvn, "total_volume": self.total_volume, "bin_size": self.bin_size}


def volume_profile(df: pd.DataFrame, bin_size: float = 5.0, value_area_pct: float = 0.70,
                   hvn_ratio: float = 1.3, lvn_ratio: float = 0.5) -> VolumeProfile | None:
    """Profilo di volume: ogni barra distribuisce il volume in modo uniforme sui bin toccati.

    I prezzi di POC/VAH/VAL/HVN/LVN sono i centri dei bin. Value Area = espansione dal POC
    a coppie di bin (metodo classico) fino a `value_area_pct` del volume.
    """
    if df is None or df.empty or df["volume"].sum() <= 0:
        return None
    lo_idx = np.floor(df["low"].to_numpy(dtype=float) / bin_size).astype(int)
    hi_idx = np.floor(df["high"].to_numpy(dtype=float) / bin_size).astype(int)
    vols = df["volume"].to_numpy(dtype=float)
    base = lo_idx.min()
    n = hi_idx.max() - base + 1
    prof = np.zeros(n)
    for lo, hi, v in zip(lo_idx, hi_idx, vols):
        span = hi - lo + 1
        prof[lo - base: hi - base + 1] += v / span
    centers = (np.arange(n) + base + 0.5) * bin_size
    total = prof.sum()

    # POC: bin massimo; a parita' il piu' vicino al centro del range (poi il piu' basso).
    mid = (df["high"].max() + df["low"].min()) / 2.0
    cands = np.flatnonzero(prof == prof.max())
    poc_i = int(min(cands, key=lambda i: (abs(centers[i] - mid), i)))

    lo_i = hi_i = poc_i
    acc = prof[poc_i]
    target = value_area_pct * total
    while acc < target and (lo_i > 0 or hi_i < n - 1):
        up = prof[hi_i + 1: hi_i + 3].sum() if hi_i < n - 1 else -1.0
        dn = prof[max(lo_i - 2, 0): lo_i].sum() if lo_i > 0 else -1.0
        if up >= dn:
            step = min(2, n - 1 - hi_i)
            acc += prof[hi_i + 1: hi_i + 1 + step].sum()
            hi_i += step
        else:
            step = min(2, lo_i)
            acc += prof[lo_i - step: lo_i].sum()
            lo_i -= step

    # HVN / LVN su profilo smussato (media mobile a 3 bin).
    k = np.array([1.0, 1.0, 1.0]) / 3.0
    sm = np.convolve(prof, k, mode="same") if n >= 3 else prof.copy()
    nz_mean = prof[prof > 0].mean() if (prof > 0).any() else 0.0
    hvn: list[tuple[float, float]] = []
    lvn: list[tuple[float, float]] = []

    def runs(flags: np.ndarray) -> list[tuple[int, int]]:
        out, start = [], None
        for i, f in enumerate(flags):
            if f and start is None:
                start = i
            if not f and start is not None:
                out.append((start, i - 1))
                start = None
        if start is not None:
            out.append((start, len(flags) - 1))
        return out

    def pick(lo: int, hi: int, best: str) -> int:
        seg = sm[lo: hi + 1]
        target = seg.max() if best == "max" else seg.min()
        idx = [lo + k for k, v in enumerate(seg) if v == target]
        centre = (lo + hi) / 2
        return int(min(idx, key=lambda i: (abs(i - centre), i)))

    # HVN: gruppi di bin sopra soglia -> il picco del gruppo (esclusi i gruppi dominati dal POC).
    for lo, hi in runs(sm >= hvn_ratio * nz_mean):
        i = pick(lo, hi, "max")
        if i != poc_i and lo <= i <= hi and not (lo <= poc_i <= hi):
            hvn.append((float(centers[i]), float(sm[i])))
    # LVN: gruppi di bin sotto soglia circondati da bin piu' alti (interni al profilo).
    for lo, hi in runs(sm <= lvn_ratio * nz_mean):
        if lo > 0 and hi < n - 1:
            i = pick(lo, hi, "min")
            lvn.append((float(centers[i]), float(sm[i])))
    hvn = sorted(sorted(hvn, key=lambda t: -t[1])[:3])
    lvn = sorted(sorted(lvn, key=lambda t: t[1])[:3])
    return VolumeProfile(
        poc=float(centers[poc_i]), vah=float(centers[hi_i]), val=float(centers[lo_i]),
        hvn=[p for p, _ in hvn], lvn=[p for p, _ in lvn], total_volume=float(total), bin_size=bin_size,
    )


# --------------------------------------------------------------------------- Sessioni
def _ohlc(df: pd.DataFrame) -> dict[str, float]:
    return {
        "open": float(df["open"].iloc[0]), "high": float(df["high"].max()),
        "low": float(df["low"].min()), "close": float(df["close"].iloc[-1]),
    }


def split_df(df: pd.DataFrame, sessions: list[str], prev_date: str, fcfg: FeaturesCfg
             ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """(sessione precedente RTH, overnight, tutte le RTH per data) per un qualunque strumento."""
    mask = rth_mask(df, fcfg)
    dates = df["timestamp"].dt.date.astype(str)
    rth_by_date = {d: df[mask & (dates == d)] for d in sessions}
    prev = rth_by_date[prev_date]
    prev_close_ts = pd.Timestamp.combine(datetime.strptime(prev_date, "%Y-%m-%d").date(), _hhmm(fcfg.rth_end))
    return prev, df[df["timestamp"] >= prev_close_ts], rth_by_date


def split_sessions(md: MarketData, fcfg: FeaturesCfg) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """(sessione precedente RTH, overnight, tutte le RTH valide per data)."""
    assert md.bars is not None and md.prev_session_date is not None
    return split_df(md.bars, md.sessions, md.prev_session_date, fcfg)


# --------------------------------------------------------------------------- Regime
@dataclass
class RegimeResult:
    state: str
    reasons: list[str]
    metrics: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "reasons": self.reasons, "metrics": self.metrics}


def classify_regime(*, spot: float | None, on_open: float | None, on_range: float | None,
                    on_vwap: float | None, prev_val: float | None, prev_vah: float | None,
                    atr_daily: float | None, atr_ratio: float | None, fcfg: FeaturesCfg) -> RegimeResult:
    """Classificatore deterministico con motivazioni esplicite."""
    rc = fcfg.regime
    metrics: dict[str, float | None] = {
        "displacement_atr": None, "atr_ratio": atr_ratio,
        "overnight_range_atr": None,
    }
    need = [spot, on_open, on_range, on_vwap, atr_daily, atr_ratio]
    if any(v is None for v in need) or not atr_daily:
        return RegimeResult("UNKNOWN", ["Dati insufficienti per classificare il regime."], metrics)
    disp = (spot - on_open) / atr_daily  # type: ignore[operator]
    on_rng = on_range / atr_daily  # type: ignore[operator]
    metrics["displacement_atr"] = round(float(disp), 3)
    metrics["overnight_range_atr"] = round(float(on_rng), 3)
    above_vwap = spot > on_vwap  # type: ignore[operator]
    in_va = prev_val is not None and prev_vah is not None and prev_val <= spot <= prev_vah  # type: ignore[operator]

    if atr_ratio >= rc.high_vol_ratio:  # type: ignore[operator]
        return RegimeResult("HIGH_VOLATILITY", [
            f"ATR 5m corrente = {atr_ratio:.2f}x la mediana storica (soglia {rc.high_vol_ratio}x)."], metrics)
    if on_rng >= rc.overnight_range_hv_atr:
        return RegimeResult("HIGH_VOLATILITY", [
            f"Range overnight = {on_rng:.2f} ATR giornalieri (soglia {rc.overnight_range_hv_atr})."], metrics)
    if disp >= rc.trend_displacement_atr and above_vwap:
        return RegimeResult("TREND_UP", [
            f"Spot {disp:.2f} ATR giornalieri sopra l'apertura overnight (soglia {rc.trend_displacement_atr}).",
            "Spot sopra il VWAP overnight."], metrics)
    if disp <= -rc.trend_displacement_atr and not above_vwap:
        return RegimeResult("TREND_DOWN", [
            f"Spot {abs(disp):.2f} ATR giornalieri sotto l'apertura overnight (soglia {rc.trend_displacement_atr}).",
            "Spot sotto il VWAP overnight."], metrics)
    if atr_ratio <= rc.low_vol_ratio:  # type: ignore[operator]
        return RegimeResult("LOW_VOLATILITY", [
            f"ATR 5m corrente = {atr_ratio:.2f}x la mediana storica (soglia {rc.low_vol_ratio}x)."], metrics)
    reasons = [f"Spostamento overnight contenuto ({disp:+.2f} ATR giornalieri), nessun trend direzionale."]
    reasons.append("Spot dentro la value area della sessione precedente." if in_va
                   else "Spot fuori dalla value area precedente ma senza direzionalita' confermata.")
    return RegimeResult("BALANCED", reasons, metrics)


# --------------------------------------------------------------------------- Feature set
def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(float(v), nd)


def compute_features(md: MarketData, fcfg: FeaturesCfg) -> dict[str, Any]:
    """Calcola tutte le feature deterministiche. Richiede `md.bars` valido."""
    df = md.bars
    if df is None or md.levels is None:
        raise ValueError("compute_features richiede dati validi")
    prev, on, rth_by_date = split_sessions(md, fcfg)
    bs, va = fcfg.profile_bin_size, fcfg.value_area_pct

    prev_ohlc = _ohlc(prev)
    prev_vp = volume_profile(prev, bs, va, fcfg.hvn_ratio, fcfg.lvn_ratio)
    last3 = pd.concat([rth_by_date[d] for d in md.sessions[-3:]])
    comp_vp = volume_profile(last3, bs, va, fcfg.hvn_ratio, fcfg.lvn_ratio)
    on_ohlc = _ohlc(on)
    on_vwap = vwap(on)
    prev_vwap = vwap(prev)

    series = atr_series(df, fcfg.atr_period)
    valid = series[~np.isnan(series)]
    atr5 = float(valid[-1]) if len(valid) else None
    ratio = float(valid[-1] / np.median(valid)) if len(valid) > 1 and np.median(valid) > 0 else None

    # ATR giornaliero sulle sessioni RTH disponibili (periodo ridotto se la storia e' corta).
    sess = [rth_by_date[d] for d in md.sessions]
    trs, pc = [], None
    for s in sess:
        h, l = float(s["high"].max()), float(s["low"].min())
        trs.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
        pc = float(s["close"].iloc[-1])
    dperiod = min(fcfg.atr_period, len(trs))
    atr_daily = float(np.mean(trs[-dperiod:])) if dperiod else None

    lv = md.levels
    spot = lv.spot
    last_close = float(df["close"].iloc[-1])
    on_range = on_ohlc["high"] - on_ohlc["low"]
    regime = classify_regime(
        spot=spot, on_open=on_ohlc["open"], on_range=on_range, on_vwap=on_vwap,
        prev_val=prev_vp.val if prev_vp else None, prev_vah=prev_vp.vah if prev_vp else None,
        atr_daily=atr_daily, atr_ratio=ratio, fcfg=fcfg)

    # Proxy di regime gamma: SOLO dalla posizione dello spot rispetto al gamma flip fornito.
    gamma_proxy, flip_dist = None, None
    if spot is not None and lv.options.gamma_flip is not None:
        flip_dist = spot - lv.options.gamma_flip
        gamma_proxy = "POSITIVE_GAMMA_PROXY" if flip_dist >= 0 else "NEGATIVE_GAMMA_PROXY"

    def d(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else round(a - b, 2)

    es_block, es_warns = compute_es_block(md, fcfg)
    base_nq = {"last_close": _r(last_close), "prev_session": {**{k: _r(v) for k, v in prev_ohlc.items()}},
               "overnight": {**{k: _r(v) for k, v in on_ohlc.items()}, "vwap": _r(on_vwap)}}
    im = (compute_intermarket(df, md.es_bars, base_nq, es_block, fcfg.intermarket)
          if es_block is not None and md.es_bars is not None else dict(IM_UNKNOWN))
    return {
        "es": es_block, "intermarket": im, "es_warnings": es_warns,
        "date": md.date,
        "spot": _r(spot),
        "last_close": _r(last_close),
        "prev_session_date": md.prev_session_date,
        "prev_session": {
            **{k: _r(v) for k, v in prev_ohlc.items()}, "vwap": _r(prev_vwap),
            "poc": _r(prev_vp.poc) if prev_vp else None, "vah": _r(prev_vp.vah) if prev_vp else None,
            "val": _r(prev_vp.val) if prev_vp else None,
            "hvn": [_r(x) for x in prev_vp.hvn] if prev_vp else [],
            "lvn": [_r(x) for x in prev_vp.lvn] if prev_vp else [],
        },
        "overnight": {
            **{k: _r(v) for k, v in on_ohlc.items()}, "range": _r(on_range), "vwap": _r(on_vwap),
            "bars": int(len(on)),
        },
        "composite_3s": {
            "poc": _r(comp_vp.poc) if comp_vp else None, "vah": _r(comp_vp.vah) if comp_vp else None,
            "val": _r(comp_vp.val) if comp_vp else None,
        },
        "atr": {
            "atr_5m": _r(atr5), "atr_period": fcfg.atr_period, "atr_daily": _r(atr_daily),
            "atr_daily_period_used": dperiod, "atr_ratio": _r(ratio, 3),
        },
        "distances": {
            "spot_vs_overnight_vwap": d(spot, on_vwap),
            "spot_vs_prev_poc": d(spot, prev_vp.poc if prev_vp else None),
            "spot_vs_prev_close": d(spot, prev_ohlc["close"]),
            "spot_vs_overnight_high": d(spot, on_ohlc["high"]),
            "spot_vs_overnight_low": d(spot, on_ohlc["low"]),
        },
        "options": {
            "gamma_flip": lv.options.gamma_flip, "call_wall": lv.options.call_wall,
            "put_wall": lv.options.put_wall, "notes": lv.options.notes,
            "gamma_regime_proxy": gamma_proxy, "spot_minus_gamma_flip": _r(flip_dist),
            "available": any(v is not None for v in (lv.options.gamma_flip, lv.options.call_wall, lv.options.put_wall)),
        },
        "regime": regime.to_dict(),
    }


def compute_es_block(md: MarketData, fcfg: FeaturesCfg) -> tuple[dict[str, Any] | None, list[str]]:
    """Feature ES (conferma). Ritorna (blocco|None, avvisi). Mai valori stimati."""
    warns: list[str] = []
    df = md.es_bars
    if df is None or md.prev_session_date is None:
        return None, warns
    have = set(df["timestamp"].dt.date.astype(str))
    if md.prev_session_date not in have or not set(md.sessions[-3:]) <= have:
        return None, ["ES: sessioni RTH recenti mancanti, ES ignorato"]
    try:
        prev, on, rth = split_df(df, [d for d in md.sessions if d in have], md.prev_session_date, fcfg)
    except KeyError:
        return None, ["ES: sessione precedente mancante, ES ignorato"]
    if prev.empty or on.empty:
        return None, ["ES: sessione precedente o overnight vuoti, ES ignorato"]
    bs, va = fcfg.intermarket.es_profile_bin_size, fcfg.value_area_pct
    vp = volume_profile(prev, bs, va, fcfg.hvn_ratio, fcfg.lvn_ratio)
    series = atr_series(df, fcfg.atr_period)
    valid = series[~np.isnan(series)]
    trs, pc = [], None
    for d in sorted(rth):
        sdf = rth[d]
        h, l = float(sdf["high"].max()), float(sdf["low"].min())
        trs.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
        pc = float(sdf["close"].iloc[-1])
    dper = min(fcfg.atr_period, len(trs))
    on_o, prev_o = _ohlc(on), _ohlc(prev)
    lv = md.levels.es if md.levels else None
    spot = lv.spot if lv else None
    last = float(df["close"].iloc[-1])
    atr_d = float(np.mean(trs[-dper:])) if dper else None
    ratio = float(valid[-1] / np.median(valid)) if len(valid) > 1 and np.median(valid) > 0 else None
    on_vwap = vwap(on)
    regime = classify_regime(spot=spot if spot is not None else last, on_open=on_o["open"], on_range=on_o["high"] - on_o["low"],
                             on_vwap=on_vwap, prev_val=vp.val if vp else None, prev_vah=vp.vah if vp else None,
                             atr_daily=atr_d, atr_ratio=ratio, fcfg=fcfg)
    if lv is None or lv.spot is None:
        warns.append("ES: spot non fornito in levels.json (es.spot): uso solo l'ultimo close OHLCV per le metriche")
    block = {
        "spot": _r(spot), "last_close": _r(last),
        "prev_session": {**{k: _r(v) for k, v in prev_o.items()}, "vwap": _r(vwap(prev)),
                         "poc": _r(vp.poc) if vp else None, "vah": _r(vp.vah) if vp else None,
                         "val": _r(vp.val) if vp else None},
        "overnight": {**{k: _r(v) for k, v in on_o.items()}, "range": _r(on_o["high"] - on_o["low"]), "vwap": _r(on_vwap), "bars": int(len(on))},
        "atr": {"atr_5m": _r(float(valid[-1]) if len(valid) else None), "atr_daily": _r(atr_d)},
        "regime": regime.to_dict(),
        "options": {"gamma_flip": lv.options.gamma_flip if lv else None, "call_wall": lv.options.call_wall if lv else None,
                    "put_wall": lv.options.put_wall if lv else None,
                    "available": bool(lv and any(v is not None for v in (lv.options.gamma_flip, lv.options.call_wall, lv.options.put_wall)))},
    }
    return block, warns


def cross_check(md: MarketData, feats: dict[str, Any], fcfg: FeaturesCfg) -> list[str]:
    """Confronta i livelli forniti in levels.json con quelli calcolati: solo avvisi."""
    out: list[str] = []
    lv = md.levels
    assert lv is not None
    tol = max(2.0, 0.5 * (feats["atr"]["atr_5m"] or 0))
    pairs = [
        ("previous_session_high", lv.levels.previous_session_high, feats["prev_session"]["high"]),
        ("previous_session_low", lv.levels.previous_session_low, feats["prev_session"]["low"]),
        ("overnight_high", lv.levels.overnight_high, feats["overnight"]["high"]),
        ("overnight_low", lv.levels.overnight_low, feats["overnight"]["low"]),
    ]
    for name, given, calc in pairs:
        if given is not None and calc is not None and abs(given - calc) > tol:
            out.append(f"levels.{name}={given} diverge dal valore calcolato dalle barre ({calc}); "
                       f"usato il calcolato, verificare i dati.")
    atr5 = feats["atr"]["atr_5m"]
    if lv.spot is not None and atr5 and abs(lv.spot - feats["last_close"]) > 2 * atr5:
        out.append(f"Spot ({lv.spot}) lontano dall'ultimo close OHLCV ({feats['last_close']}) "
                   f"oltre 2 ATR 5m: dati non allineati?")
    return out


# --------------------------------------------------------------------------- Evidence registry
# (chiave, sorgente, percorso nel dict di origine, etichetta, tipo)
EVIDENCE_SPEC: list[tuple[str, str, str, str, str]] = [
    ("spot", "levels.json", "spot", "Spot", "price"),
    ("options.gamma_flip", "levels.json", "options.gamma_flip", "Gamma flip", "price"),
    ("options.call_wall", "levels.json", "options.call_wall", "Call wall", "price"),
    ("options.put_wall", "levels.json", "options.put_wall", "Put wall", "price"),
    ("levels.previous_session_high", "levels.json", "levels.previous_session_high", "Max sessione prec. (fornito)", "price"),
    ("levels.previous_session_low", "levels.json", "levels.previous_session_low", "Min sessione prec. (fornito)", "price"),
    ("levels.overnight_high", "levels.json", "levels.overnight_high", "Max overnight (fornito)", "price"),
    ("levels.overnight_low", "levels.json", "levels.overnight_low", "Min overnight (fornito)", "price"),
    ("levels.previous_week_high", "levels.json", "levels.previous_week_high", "Max settimana prec.", "price"),
    ("levels.previous_week_low", "levels.json", "levels.previous_week_low", "Min settimana prec.", "price"),
    ("vwap_overnight", "features", "overnight.vwap", "VWAP overnight", "price"),
    ("vwap_prev_session", "features", "prev_session.vwap", "VWAP sessione precedente", "price"),
    ("atr_5m", "features", "atr.atr_5m", "ATR(14) 5m", "metric"),
    ("atr_daily", "features", "atr.atr_daily", "ATR giornaliero", "metric"),
    ("prev_high", "features", "prev_session.high", "Max sessione precedente", "price"),
    ("prev_low", "features", "prev_session.low", "Min sessione precedente", "price"),
    ("prev_close", "features", "prev_session.close", "Close sessione precedente", "price"),
    ("prev_poc", "features", "prev_session.poc", "POC sessione precedente", "price"),
    ("prev_vah", "features", "prev_session.vah", "VAH sessione precedente", "price"),
    ("prev_val", "features", "prev_session.val", "VAL sessione precedente", "price"),
    ("prev_hvn", "features", "prev_session.hvn", "HVN sessione precedente", "price"),
    ("prev_lvn", "features", "prev_session.lvn", "LVN sessione precedente", "price"),
    ("overnight_high", "features", "overnight.high", "Max overnight", "price"),
    ("overnight_low", "features", "overnight.low", "Min overnight", "price"),
    ("overnight_open", "features", "overnight.open", "Apertura overnight", "price"),
    ("overnight_last", "features", "overnight.close", "Ultimo prezzo overnight", "price"),
    ("overnight_range", "features", "overnight.range", "Range overnight", "metric"),
    ("composite_poc", "features", "composite_3s.poc", "POC composito 3 sessioni", "price"),
    ("composite_vah", "features", "composite_3s.vah", "VAH composito 3 sessioni", "price"),
    ("composite_val", "features", "composite_3s.val", "VAL composito 3 sessioni", "price"),
    ("regime", "features", "regime.state", "Regime di mercato", "text"),
    ("gamma_regime_proxy", "features", "options.gamma_regime_proxy", "Proxy regime gamma (spot vs flip)", "text"),
    ("last_close", "features", "last_close", "Ultimo close OHLCV", "price"),
    ("atr_ratio", "features", "atr.atr_ratio", "ATR 5m / mediana storica", "metric"),
    # --- ES e inter-mercato (aggiunte in coda: gli ID precedenti NON cambiano) ---
    ("es.spot", "features", "es.spot", "ES spot", "es_price"),
    ("es.last_close", "features", "es.last_close", "ES ultimo close OHLCV", "es_price"),
    ("es.vwap_overnight", "features", "es.overnight.vwap", "ES VWAP overnight", "es_price"),
    ("es.prev_high", "features", "es.prev_session.high", "ES max sessione precedente", "es_price"),
    ("es.prev_low", "features", "es.prev_session.low", "ES min sessione precedente", "es_price"),
    ("es.prev_close", "features", "es.prev_session.close", "ES close sessione precedente", "es_price"),
    ("es.prev_poc", "features", "es.prev_session.poc", "ES POC sessione precedente", "es_price"),
    ("es.prev_vah", "features", "es.prev_session.vah", "ES VAH sessione precedente", "es_price"),
    ("es.prev_val", "features", "es.prev_session.val", "ES VAL sessione precedente", "es_price"),
    ("es.overnight_high", "features", "es.overnight.high", "ES max overnight", "es_price"),
    ("es.overnight_low", "features", "es.overnight.low", "ES min overnight", "es_price"),
    ("es.options.gamma_flip", "features", "es.options.gamma_flip", "ES gamma flip", "es_price"),
    ("es.options.call_wall", "features", "es.options.call_wall", "ES call wall", "es_price"),
    ("es.options.put_wall", "features", "es.options.put_wall", "ES put wall", "es_price"),
    ("es.regime", "features", "es.regime.state", "Regime ES", "text"),
    ("im.state", "features", "intermarket.state", "Stato inter-mercato NQ/ES", "text"),
    ("im.correlation", "features", "intermarket.correlation", "Correlazione 5m NQ/ES", "metric"),
    ("im.nq_on_return_pct", "features", "intermarket.nq_on_return_pct", "Rendimento overnight NQ %", "metric"),
    ("im.es_on_return_pct", "features", "intermarket.es_on_return_pct", "Rendimento overnight ES %", "metric"),
    ("im.return_gap_bps", "features", "intermarket.return_gap_bps", "Gap rendimento NQ-ES (bps)", "metric"),
    ("im.smt_high", "features", "intermarket.smt_high", "SMT sui massimi overnight", "text"),
    ("im.smt_low", "features", "intermarket.smt_low", "SMT sui minimi overnight", "text"),
]


def _get(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def build_evidence(md: MarketData, feats: dict[str, Any]) -> list[dict[str, Any]]:
    """Registro evidenze con ID STABILI (E001...): una chiave mancante lascia il buco, non rinumera."""
    assert md.levels is not None
    lv = md.levels.model_dump()
    out: list[dict[str, Any]] = []
    for i, (key, src, path, label, kind) in enumerate(EVIDENCE_SPEC, start=1):
        value = _get(lv if src == "levels.json" else feats, path)
        if value is None or value == []:
            continue
        out.append({"id": f"E{i:03d}", "key": key, "source": f"{src}:{path}",
                    "label": label, "kind": kind, "value": value})
    return out


def evidence_by_key(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["key"]: e for e in evidence}


def price_levels(evidence: list[dict[str, Any]]) -> list[tuple[str, float, str]]:
    """Tutti i livelli di prezzo presenti nelle evidenze: (etichetta, valore, id)."""
    out: list[tuple[str, float, str]] = []
    for e in evidence:
        if e["kind"] != "price" or e["key"] == "spot" or e["key"] == "last_close":
            continue
        vals = e["value"] if isinstance(e["value"], list) else [e["value"]]
        out.extend((e["label"], float(v), e["id"]) for v in vals)
    return out
