"""Conferma inter-mercato NQ/ES: correlazione, rendimento overnight, divergenze SMT. Solo calcoli deterministici.

ES e' strumento di CONFERMA: gli scenari restano su NQ. Se ES manca lo stato e' UNKNOWN (mai stimato).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import IntermarketCfg

UNKNOWN: dict[str, Any] = {
    "state": "UNKNOWN", "direction": None, "nq_dir": None, "es_dir": None, "correlation": None, "beta_nq_on_es": None,
    "nq_on_return_pct": None, "es_on_return_pct": None, "return_gap_bps": None, "ratio_nq_es": None,
    "ratio_change_pct": None, "smt_high": None, "smt_low": None, "vwap_agreement": None,
    "reasons": ["ES non disponibile: nessuna conferma inter-mercato."],
}


def _dir(ret_pct: float, thr: float) -> str:
    return "LONG" if ret_pct > thr else "SHORT" if ret_pct < -thr else "FLAT"


def _smt(nq_sweep: bool, es_sweep: bool) -> str:
    return "BOTH" if nq_sweep and es_sweep else "NQ_ONLY" if nq_sweep else "ES_ONLY" if es_sweep else "NONE"


def compute_intermarket(nq_df: pd.DataFrame, es_df: pd.DataFrame, nq: dict[str, Any], es: dict[str, Any],
                        icfg: IntermarketCfg) -> dict[str, Any]:
    """`nq`/`es` sono i blocchi feature (prev_session, overnight, last_close)."""
    reasons: list[str] = []
    a = nq_df[["timestamp", "close"]].merge(es_df[["timestamp", "close"]], on="timestamp", suffixes=("_nq", "_es"))
    corr = beta = None
    if len(a) > 30:
        a = a.tail(icfg.corr_bars + 1)
        r = np.log(a[["close_nq", "close_es"]]).diff().dropna()
        if r["close_nq"].std() > 0 and r["close_es"].std() > 0:
            corr = float(r["close_nq"].corr(r["close_es"]))
            beta = float(r["close_nq"].cov(r["close_es"]) / r["close_es"].var())
    nq_on, es_on = nq["overnight"], es["overnight"]
    nq_ret = (nq_on["close"] / nq_on["open"] - 1) * 100
    es_ret = (es_on["close"] / es_on["open"] - 1) * 100
    nd, ed = _dir(nq_ret, icfg.direction_threshold_pct), _dir(es_ret, icfg.direction_threshold_pct)
    gap_bps = (nq_ret - es_ret) * 100
    ratio_now = nq["last_close"] / es["last_close"]
    ratio_prev = nq["prev_session"]["close"] / es["prev_session"]["close"]
    smt_h = _smt(nq_on["high"] > nq["prev_session"]["high"], es_on["high"] > es["prev_session"]["high"])
    smt_l = _smt(nq_on["low"] < nq["prev_session"]["low"], es_on["low"] < es["prev_session"]["low"])
    vw = None
    if nq_on.get("vwap") and es_on.get("vwap"):
        vw = (nq["last_close"] > nq_on["vwap"]) == (es["last_close"] > es_on["vwap"])

    direction = None
    if corr is not None and corr < icfg.min_correlation:
        state = "DECORRELATED"
        reasons.append(f"Correlazione 5m NQ/ES {corr:.2f} sotto la soglia {icfg.min_correlation:.2f}: conferma poco affidabile.")
    elif nd == ed and nd != "FLAT":
        state, direction = "CONFIRMED", nd
        reasons.append(f"NQ ({nq_ret:+.2f}%) ed ES ({es_ret:+.2f}%) si muovono nella stessa direzione ({nd}) overnight.")
    elif nd == ed:
        state = "NEUTRAL"
        reasons.append(f"NQ ({nq_ret:+.2f}%) ed ES ({es_ret:+.2f}%) piatti overnight (soglia ±{icfg.direction_threshold_pct}%).")
    elif "FLAT" not in (nd, ed) or abs(gap_bps) > icfg.divergence_bps:
        state = "DIVERGENT"
        reasons.append(f"NQ {nd} ({nq_ret:+.2f}%) vs ES {ed} ({es_ret:+.2f}%): divergenza overnight (gap {gap_bps:+.0f} bps).")
    else:
        state = "MIXED"
        reasons.append(f"Un solo mercato direzionale: NQ {nd} ({nq_ret:+.2f}%), ES {ed} ({es_ret:+.2f}%).")
    if smt_h in ("NQ_ONLY", "ES_ONLY"):
        reasons.append(f"SMT sui massimi: nuovo massimo overnight solo su {smt_h[:2]} (l'altro non conferma).")
    if smt_l in ("NQ_ONLY", "ES_ONLY"):
        reasons.append(f"SMT sui minimi: nuovo minimo overnight solo su {smt_l[:2]} (l'altro non conferma).")
    if vw is False:
        reasons.append("NQ ed ES su lati opposti del proprio VWAP overnight.")
    return {
        "state": state, "direction": direction, "nq_dir": nd, "es_dir": ed,
        "correlation": None if corr is None else round(corr, 3), "beta_nq_on_es": None if beta is None else round(beta, 3),
        "nq_on_return_pct": round(nq_ret, 3), "es_on_return_pct": round(es_ret, 3), "return_gap_bps": round(gap_bps, 1),
        "ratio_nq_es": round(ratio_now, 3), "ratio_change_pct": round((ratio_now / ratio_prev - 1) * 100, 3),
        "smt_high": smt_h, "smt_low": smt_l, "vwap_agreement": vw, "reasons": reasons,
    }


def scenario_gate(direction: str, im: dict[str, Any] | None) -> tuple[str, str]:
    """(PASS|WARN|FAIL, motivazione) per uno scenario NQ della direzione data."""
    if not im or im.get("state") == "UNKNOWN":
        return "WARN", "ES non disponibile: scenario non confermato dall'altro indice"
    opp = "SHORT" if direction == "LONG" else "LONG"
    if im["es_dir"] == opp:
        return "FAIL", f"ES si muove {im['es_dir']} contro lo scenario {direction}"
    if im["state"] == "DIVERGENT":
        return "FAIL", "NQ/ES divergenti: nessuna conferma inter-mercato"
    smt = im["smt_high"] if direction == "LONG" else im["smt_low"]
    if im["state"] == "CONFIRMED" and im["direction"] == direction:
        if smt in ("NQ_ONLY", "ES_ONLY"):
            return "WARN", "NQ/ES concordi ma con divergenza SMT sul livello estremo"
        return "PASS", f"ES conferma la direzione {direction}"
    return "WARN", f"conferma ES parziale (stato {im['state']})"
