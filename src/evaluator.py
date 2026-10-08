"""Valutazione storica delle decisioni del Consiglio (journal + report)."""
from __future__ import annotations

import json
from statistics import mean
from typing import Any

from .config import AppConfig
from .journal import read_journal

TAKEN = ("TAKE", "MODIFY")


def load_reports(cfg: AppConfig) -> list[dict[str, Any]]:
    d = cfg.path("reports_dir")
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return out


def _f(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def max_drawdown(series: list[float]) -> float:
    peak = cum = dd = 0.0
    for r in series:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return round(dd, 3)


def trade_metrics(pnls: list[float]) -> dict[str, Any]:
    n = len(pnls)
    if n == 0:
        return {"trades": 0, "win_rate": None, "average_R": None, "expectancy": None,
                "profit_factor": None, "max_drawdown_R": None}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_loss = abs(sum(losses))
    wr = len(wins) / n
    exp = wr * (mean(wins) if wins else 0.0) + (len(losses) / n) * (mean(losses) if losses else 0.0)
    return {"trades": n, "win_rate": round(wr, 3), "average_R": round(mean(pnls), 3), "expectancy": round(exp, 3),
            "profit_factor": round(sum(wins) / gross_loss, 3) if gross_loss else None,
            "max_drawdown_R": max_drawdown(pnls)}


def selected_scenario(report: dict[str, Any]) -> dict[str, Any] | None:
    sid = report["decision"].get("selected_scenario_id")
    out = (report["agents"].get("strategist") or {}).get("output") or {}
    return next((s for s in out.get("scenarios", []) if s["id"] == sid), None)


def evaluate(cfg: AppConfig) -> dict[str, Any]:
    reports = load_reports(cfg)
    rows = {r["date"]: r for r in read_journal(cfg.path("journal"))}
    n = len(reports)
    assessed = [a for r in reports for a in r["risk"]["assessments"]]
    vetoes = [a for a in assessed if a["verdict"] in ("VETO", "NOT_APPROVED")]
    rrs = [s["expected_rr"] for r in reports if (s := selected_scenario(r))]
    # trade realmente presi dall'umano con P&L noto, in ordine cronologico
    pnls = [p for d in sorted(rows) if rows[d]["human_decision"] in TAKEN and (p := _f(rows[d]["pnl_r"])) is not None]
    known = [(r, rows.get(r["run"]["date"])) for r in reports]
    scen_res = [row["result"] for r, row in known if row and selected_scenario(r) and row["result"] in ("WIN", "LOSS")]
    return {
        "runs": n,
        "status_counts": {s: sum(r["decision"]["status"] == s for r in reports)
                          for s in ("APPROVED_SETUP", "APPROVED_WITH_CAUTION", "NO_TRADE", "REVIEW_REQUIRED", "DATA_ERROR")},
        "veto_rate": round(len(vetoes) / len(assessed), 3) if assessed else None,
        "no_trade_rate": round(sum(r["decision"]["status"] == "NO_TRADE" for r in reports) / n, 3) if n else None,
        "average_rr": round(mean(rrs), 3) if rrs else None,
        "scenario_accuracy": round(scen_res.count("WIN") / len(scen_res), 3) if scen_res else None,
        "scenario_outcomes_known": len(scen_res),
        "human_pending": sum(1 for r in reports if (rows.get(r["run"]["date"]) or {}).get("human_decision", "PENDING") == "PENDING"),
        **trade_metrics(pnls),
    }


def agent_evaluation(cfg: AppConfig) -> list[dict[str, Any]]:
    """Valutazione indipendente per agente. Non si usa il solo win rate."""
    reports = load_reports(cfg)
    rows = {r["date"]: r for r in read_journal(cfg.path("journal"))}
    out: list[dict[str, Any]] = []
    names = {"price_action": "💙 Price Action", "options_flow": "💜 Options Flow", "strategist": "💚 Strategist",
             "risk_manager": "❤️ Risk Manager", "judge": "⚪ Judge"}

    def realized(report: dict[str, Any]) -> str | None:
        row, sc = rows.get(report["run"]["date"]), selected_scenario(report)
        if not row or not sc or row["result"] not in ("WIN", "LOSS"):
            return None
        d = sc["direction"]
        return d if row["result"] == "WIN" else ("SHORT" if d == "LONG" else "LONG")

    for key, label in names.items():
        recs = [(r, r["agents"].get(key) or {}) for r in reports]
        ran = [(r, a) for r, a in recs if a.get("status") not in (None, "SKIPPED")]
        outs = [(r, a["output"]) for r, a in ran if a.get("output")]
        conf = [o["confidence"] for _, o in outs if o.get("confidence") is not None]
        fails = sum(1 for _, a in ran if a["status"] == "FAILED")
        item: dict[str, Any] = {"Agent": label, "Runs": len(ran), "Failure rate": round(fails / len(ran), 3) if ran else None,
                                "Avg confidence": round(mean(conf), 1) if conf else None, "Accuracy": None,
                                "Veto rate": None, "False positive rate": None, "Scenario performance (R)": None,
                                "Contribution (R)": None}
        if key in ("price_action", "options_flow", "strategist"):
            calls = [(o["bias"], realized(r)) for r, o in outs if o.get("bias") in ("LONG", "SHORT") and realized(r)]
            if calls:
                hit = sum(b == real for b, real in calls)
                item["Accuracy"] = round(hit / len(calls), 3)
                item["False positive rate"] = round(1 - hit / len(calls), 3)
        if key == "strategist":
            pn = [p for r, _ in outs if (row := rows.get(r["run"]["date"])) and selected_scenario(r)
                  and (p := _f(row["pnl_r"])) is not None]
            item["Scenario performance (R)"] = round(mean(pn), 3) if pn else None
        if key == "risk_manager":
            asx = [a2 for r, _ in recs for a2 in r["risk"]["assessments"]]
            item["Veto rate"] = round(sum(a2["verdict"] in ("VETO", "NOT_APPROVED") for a2 in asx) / len(asx), 3) if asx else None
            approved = [(r, rows.get(r["run"]["date"])) for r, _ in outs if r["decision"]["status"] in ("APPROVED_SETUP", "APPROVED_WITH_CAUTION")]
            res = [row["result"] for _, row in approved if row and row["result"] in ("WIN", "LOSS")]
            item["False positive rate"] = round(res.count("LOSS") / len(res), 3) if res else None
        if key == "judge":
            sc = [r["judge"]["score"] for r, _ in outs if r["judge"]["score"] is not None]
            item["Avg score"] = round(mean(sc), 1) if sc else None
        # contributo: R sommati sui trade presi dove l'agente era allineato con lo scenario operato
        tot, aligned = 0.0, 0
        for r, a in recs:
            row, sc = rows.get(r["run"]["date"]), selected_scenario(r)
            p = _f(row["pnl_r"]) if row else None
            if not (row and sc and p is not None and row["human_decision"] in TAKEN):
                continue
            o = a.get("output") or {}
            if key in ("price_action", "options_flow"):
                ok = o.get("bias") == sc["direction"]
            else:
                ok = bool(o)
            if ok:
                tot += p
                aligned += 1
        item["Contribution (R)"] = round(tot, 3) if aligned else None
        out.append(item)
    return out
