"""Scoreboard degli agenti: metriche multiple, non solo win rate."""
from __future__ import annotations

from typing import Any

from .config import AppConfig
from .evaluator import agent_evaluation, evaluate


def build_scoreboard(cfg: AppConfig) -> dict[str, Any]:
    return {"agents": agent_evaluation(cfg), "council": evaluate(cfg)}


def render_text(sb: dict[str, Any]) -> str:
    cols = ["Agent", "Runs", "Failure rate", "Accuracy", "Avg confidence", "Veto rate", "False positive rate",
            "Scenario performance (R)", "Contribution (R)"]
    fmt = lambda v: "n/d" if v is None else str(v)  # noqa: E731
    widths = [max(len(c), *(len(fmt(a.get(c))) for a in sb["agents"])) for c in cols] if sb["agents"] else [len(c) for c in cols]
    lines = [" | ".join(c.ljust(w) for c, w in zip(cols, widths)), "-+-".join("-" * w for w in widths)]
    for a in sb["agents"]:
        lines.append(" | ".join(fmt(a.get(c)).ljust(w) for c, w in zip(cols, widths)))
    c = sb["council"]
    lines += ["", "CONSIGLIO", f"  Analisi totali: {c['runs']}  |  decisione umana in attesa: {c['human_pending']}",
              f"  Stati finali: {c['status_counts']}",
              f"  Veto rate {fmt(c['veto_rate'])} · No-trade rate {fmt(c['no_trade_rate'])} · R:R medio {fmt(c['average_rr'])}",
              f"  Scenario accuracy {fmt(c['scenario_accuracy'])} (esiti noti: {c['scenario_outcomes_known']})",
              f"  Trade presi: {c['trades']} · win rate {fmt(c['win_rate'])} · avg R {fmt(c['average_R'])} · "
              f"expectancy {fmt(c['expectancy'])} · PF {fmt(c['profit_factor'])} · max DD {fmt(c['max_drawdown_R'])}R",
              "", "Nota: gli agenti non sono giudicati solo dal win rate (accuratezza direzionale, veto, falsi positivi,",
              "contributo ai trade profittevoli). Con pochi esiti registrati le metriche sono statisticamente deboli."]
    return "\n".join(lines)
