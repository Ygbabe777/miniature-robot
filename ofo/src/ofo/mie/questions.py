"""The catalogue of market questions the Behavior Analyzer studies, and how each becomes events.

Every question has a DEFINITION (a dict, hashed into its identity), so a changed definition is a different study. Sign convention: +1 long, -1 short
for the hypothesised trade. Nothing here is a strategy: it is the measurement of what followed an event."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .observer import atr, derive, session_context
from .schema import fingerprint
from .sessions import in_window, session_labels
from .structure import liquidity_events

LEVELS = {"pdh": ("pdh", "high", "pd_available_at"), "pdl": ("pdl", "low", "pd_available_at"), "onh": ("onh", "high", "on_available_at"), "onl": ("onl", "low", "on_available_at"),
          "orh": ("orh", "high", "or_available_at"), "orl": ("orl", "low", "or_available_at")}


def define(level: str, outcome: str, trade: str, rule: str = "5min", reclaim_bars: int | None = None, cfg: dict | None = None) -> dict:
    d = {"event": "liquidity", "level": level, "outcome": outcome, "trade": trade, "rule": rule,
         "reclaim_bars": reclaim_bars if reclaim_bars is not None else (cfg or {}).get("structure", {}).get("reclaim_bars")}
    d["id"] = fingerprint(d)
    return d


def liquidity_events_table(bars1: pd.DataFrame, ctx: pd.DataFrame, cfg: dict, definition: dict) -> pd.DataFrame:
    """Events of one definition over all sessions. The search starts when BOTH the level and the cash open are knowable; an event is stamped at the end of its
    classification window (`available_at`); the hypothesised trade is a FADE (against the breach) or a CONTINUATION (with it)."""
    col, side, avail_col = LEVELS[definition["level"]]
    rule = definition.get("rule", "5min")
    cfg2 = {**cfg, "structure": {**cfg["structure"], "reclaim_bars": definition["reclaim_bars"] or cfg["structure"]["reclaim_bars"]}}
    b = derive(bars1, rule)
    lab = pd.to_datetime(b["session"]) if "session" in b.columns else session_labels(b)
    rows = []
    for d, r in ctx.iterrows():
        lvl, av, end = r[col], r[avail_col], r["rth_end"]
        if lvl is None or pd.isna(lvl) or av is None or pd.isna(av) or end is None or pd.isna(end):
            continue
        start = max(pd.Timestamp(av), r["on_available_at"] if r["on_available_at"] is not None else pd.Timestamp(av))
        seg = b[(lab.values == d)]
        for e in liquidity_events(seg, float(lvl), start, side, cfg2, rule, session_end=end):
            if e["kind"].endswith(definition["outcome"]) and e.get("complete", True):
                sign = (-1 if side == "high" else 1) * (1 if definition["trade"] == "fade" else -1)
                rows.append({"session": str(d)[:10], "available_at": e["available_at"], "breach_time": e["breach_time"], "sign": sign, "level": e["level"], "excursion": e["excursion"]})
    return pd.DataFrame(rows, columns=["session", "available_at", "breach_time", "sign", "level", "excursion"])


def gap_events_table(ctx: pd.DataFrame, definition: dict) -> pd.DataFrame:
    """Opening outside the overnight range: the hypothesised trade continues (or fades) the direction of the opening position."""
    rows = []
    for d, r in ctx.iterrows():
        if r["open_vs_on"] in ("above", "below") and r["open_available_at"] is not None and not pd.isna(r["open_available_at"]):
            s = 1 if r["open_vs_on"] == "above" else -1
            rows.append({"session": str(d)[:10], "available_at": r["open_available_at"], "sign": s if definition["trade"] == "continue" else -s})
    return pd.DataFrame(rows, columns=["session", "available_at", "sign"])


def range_return_rate(bars1: pd.DataFrame, ctx: pd.DataFrame, by: str = "12:00") -> dict:
    """Descriptive: how often does price that opened OUTSIDE the previous day's range close back inside it by `by`? (no claim, no trade)"""
    n = k = 0
    lab = session_labels(bars1)
    for d, r in ctx.iterrows():
        if r["open_vs_pd"] not in ("above", "below") or r["pdh"] is None or pd.isna(r["pdh"]):
            continue
        b = bars1[lab.values == d]
        w = b[in_window(b.index, "09:30", by)]
        if len(w) == 0:
            continue
        n += 1
        k += bool(((w["close"] <= r["pdh"]) & (w["close"] >= r["pdl"])).any())
    from .behavior import wilson
    lo, hi = wilson(k, n)
    return {"event": "open_outside_prior_range_returns_inside", "by": by, "n": n, "returned": k, "rate": (k / n) if n else None, "ci95": [lo, hi], "status": "OBSERVED"}


def day_types(bars1: pd.DataFrame, ctx: pd.DataFrame, er_trend: float = 0.35) -> dict:
    """Descriptive: directional (efficiency ratio of the RTH day >= er_trend) vs consolidation days."""
    lab = session_labels(bars1)
    ers = []
    for d in ctx.index:
        b = bars1[lab.values == d]
        w = b[in_window(b.index, "09:30", "16:00")]
        if len(w) < 60:
            continue
        path = w["close"].diff().abs().sum()
        ers.append(abs(w["close"].iloc[-1] - w["close"].iloc[0]) / path if path > 0 else np.nan)
    ers = np.array([e for e in ers if not np.isnan(e)])
    return {"event": "day_type", "n_days": int(len(ers)), "directional_share": float((ers >= er_trend).mean()) if len(ers) else None, "median_er": float(np.median(ers)) if len(ers) else None, "status": "OBSERVED"}


def standard_questions(cfg: dict) -> list[dict]:
    """The initial catalogue (NQ). Each item: id, text (Italian), definition, claim."""
    qs = []
    for lvl, name in (("onh", "massimo overnight"), ("onl", "minimo overnight"), ("pdh", "massimo del giorno precedente"), ("pdl", "minimo del giorno precedente")):
        qs.append({"text": f"Cosa accade dopo lo sweep del {name} se il prezzo rientra (reclaim)?", "definition": define(lvl, "reclaim", "fade", cfg=cfg), "claim": +1, "family": "sweep_reclaim"})
        qs.append({"text": f"Cosa accade quando il {name} viene accettato (chiusure oltre il livello)?", "definition": define(lvl, "acceptance", "continue", cfg=cfg), "claim": +1, "family": "level_acceptance"})
    for lvl, name in (("orh", "massimo dell'opening range"), ("orl", "minimo dell'opening range")):
        qs.append({"text": f"Il breakout del {name} continua quando viene accettato?", "definition": define(lvl, "acceptance", "continue", cfg=cfg), "claim": +1, "family": "orb"})
        qs.append({"text": f"Il breakout fallito del {name} (reclaim) inverte?", "definition": define(lvl, "reclaim", "fade", cfg=cfg), "claim": +1, "family": "orb_failure"})
    qs.append({"text": "Un'apertura fuori dal range overnight prosegue nella direzione dell'apertura?", "definition": {"event": "gap_outside_on", "trade": "continue", "id": fingerprint({"e": "gap_outside_on", "t": "continue"})}, "claim": +1, "family": "gap"})
    qs.append({"text": "Un'apertura fuori dal range overnight rientra nel range (fade)?", "definition": {"event": "gap_outside_on", "trade": "fade", "id": fingerprint({"e": "gap_outside_on", "t": "fade"})}, "claim": +1, "family": "gap"})
    return qs


def events_for(q: dict, bars1: pd.DataFrame, ctx: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    d = q["definition"]
    return gap_events_table(ctx, d) if d["event"] == "gap_outside_on" else liquidity_events_table(bars1, ctx, cfg, d)
