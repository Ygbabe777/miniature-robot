"""Stato UI derivato ESCLUSIVAMENTE dagli eventi del Consiglio (nessuna simulazione separata).

`build_view(report)` trasforma gli eventi persistiti in una sequenza di frame deterministici:
stesso report -> stessi frame. Le UI (app.py, app_pixel.py) si limitano a visualizzarli.
"""
from __future__ import annotations

import copy
from typing import Any

AGENTS = ["price_action", "options_flow", "strategist", "risk_manager", "judge"]

AGENT_META: dict[str, dict[str, Any]] = {
    "price_action": {"name": "PRICE ACTION", "emoji": "💙", "color": "#4aa3ff", "role": "Struttura di prezzo",
                     "personality": "tecnico · metodico · focalizzato sulla struttura",
                     "workstation": "Grafici · VWAP · Volume Profile", "monitor": "chart"},
    "options_flow": {"name": "OPTIONS FLOW", "emoji": "💜", "color": "#b57bff", "role": "Gamma & dealer",
                     "personality": "analitico · orientato alle opzioni · probabilistico",
                     "workstation": "Gamma / options board", "monitor": "gamma"},
    "strategist": {"name": "STRATEGIST", "emoji": "💚", "color": "#3ddc84", "role": "Sintesi tattica",
                   "personality": "sintetizzatore · tattico · deciso ma prudente",
                   "workstation": "Scenario board", "monitor": "scenario"},
    "risk_manager": {"name": "RISK MANAGER", "emoji": "❤️", "color": "#ff5c5c", "role": "Difesa del capitale",
                     "personality": "difensivo · scettico · rigoroso",
                     "workstation": "Risk monitor", "monitor": "risk"},
    "judge": {"name": "JUDGE", "emoji": "⚪", "color": "#e8e8f0", "role": "Verifica & ancoraggio",
              "personality": "neutrale · oggettivo · critico",
              "workstation": "Council review terminal", "monitor": "review"},
}

# Posizioni in % della stanza (centro dell'avatar).
LOCATIONS: dict[str, dict[str, list[float]]] = {
    "price_action": {"desk": [14, 24], "council": [33, 43]},
    "options_flow": {"desk": [50, 18], "council": [67, 43]},
    "strategist": {"desk": [86, 24], "council": [50, 37]},
    "risk_manager": {"desk": [14, 82], "council": [33, 68]},
    "judge": {"desk": [86, 82], "council": [67, 68], "center": [50, 67]},
}

FINAL_LABEL = {
    "APPROVED_SETUP": "COUNCIL VERDICT: APPROVED SETUP",
    "APPROVED_WITH_CAUTION": "COUNCIL VERDICT: APPROVED WITH CAUTION",
    "NO_TRADE": "NO TRADE",
    "REVIEW_REQUIRED": "HUMAN REVIEW REQUIRED",
    "DATA_ERROR": "DATA ERROR",
}
ROOM_MODE_BY_FINAL = {"APPROVED_SETUP": "positive", "APPROVED_WITH_CAUTION": "caution", "NO_TRADE": "neutral",
                      "REVIEW_REQUIRED": "review", "DATA_ERROR": "warning"}

STEP_DEFS = [
    ("data_validation", "DATA VALIDATION", None), ("features", "FEATURE ENGINE", None),
    ("price_action", "💙 PRICE ACTION", "price_action"), ("options_flow", "💜 OPTIONS FLOW", "options_flow"),
    ("strategist", "💚 STRATEGIST", "strategist"), ("risk_manager", "❤️ RISK MANAGER", "risk_manager"),
    ("judge", "⚪ JUDGE", "judge"), ("report", "FINAL REPORT", None),
]


def _fresh_agents() -> dict[str, dict[str, Any]]:
    return {a: {"state": "IDLE", "loc": "desk", "activity": "In attesa", "confidence": None, "model": None,
                "status": None, "bias": None, "bubble": None, "duration_ms": None, "key_evidence": [],
                "moving": False, "facing": None, "last_message": None} for a in AGENTS}


def _pos(agent: str, loc: str) -> list[float]:
    return LOCATIONS[agent].get(loc) or LOCATIONS[agent]["council"]


def _hhmmss(ts: str) -> str:
    return ts[11:19] if len(ts) >= 19 else ts


class _Reducer:
    def __init__(self, report: dict[str, Any]) -> None:
        self.rep = report
        snap = report.get("market_snapshot") or {}
        self.s: dict[str, Any] = {
            "agents": _fresh_agents(), "arrows": [], "persist_arrows": [], "monitors": False,
            "room_mode": "idle", "final": None, "step": {k: "waiting" for k, _, _ in STEP_DEFS},
            "table": {"price": snap.get("spot"), "regime": (snap.get("regime") or {}).get("state"),
                      "scenarios": [], "status": "IN ATTESA", "mode": "idle", "verdict": None},
            "risk_worst": None,
        }

    def _set(self, a: str, **kw: Any) -> None:
        ag = self.s["agents"][a]
        if "loc" in kw and kw["loc"] != ag["loc"]:
            ag["moving"] = True
        ag.update(kw)

    def apply(self, ev: dict[str, Any]) -> None:
        S = self.s
        for ag in S["agents"].values():
            ag["moving"] = False
            if ag["state"] in ("SPEAKING", "LISTENING", "AGREEING", "CHALLENGING", "MOVING"):
                ag["state"] = "WAITING"
            ag["facing"] = None
        S["arrows"] = list(S["persist_arrows"])
        t, a, tg, d = ev["event_type"], ev.get("agent"), ev.get("target_agent"), ev.get("data") or {}
        ua = ev.get("ui_action")

        if t == "PIPELINE_STARTED":
            for x in AGENTS:
                self._set(x, state="WAITING", activity="Pronto")
            S["room_mode"] = "wake"
        elif t == "DATA_VALIDATED":
            S["monitors"] = True
            S["step"]["data_validation"] = "failed" if d.get("quality") == "RED" else "done"
            S["table"]["status"] = f"DATI {d.get('quality', '')}"
            if d.get("quality") == "RED":
                S["room_mode"] = "warning"
        elif t == "FEATURES_COMPUTED":
            S["step"]["features"] = "done"
            S["table"]["regime"] = d.get("regime")
            S["table"]["status"] = "FEATURE PRONTE"
        elif t == "AGENT_STARTED" and a:
            loc = "desk" if ua == "MOVE_TO_DESK" else "council"
            state = "READING_DATA" if a == "strategist" else "ANALYZING"
            self._set(a, loc=loc, state=state, activity=ev.get("message") or d.get("task") or "Analisi",
                      model=d.get("model"))
            S["step"][a] = "active"
            S["table"]["status"] = f"{AGENT_META[a]['name']} AL LAVORO"
        elif t == "AGENT_THINKING" and a:
            self._set(a, state="THINKING", activity="Elaborazione in corso")
        elif t in ("AGENT_MESSAGE", "AGENT_CHALLENGE", "AGENT_AGREEMENT") and a:
            mt = ev.get("message_type") or "ANALYSIS"
            st = {"AGENT_CHALLENGE": "CHALLENGING", "AGENT_AGREEMENT": "AGREEING"}.get(t, "SPEAKING")
            if mt == "VETO":
                st = "VETO"
            elif mt == "WARNING":
                st = "WARNING"
            self._set(a, state=st, activity=f"{mt}", last_message=ev.get("message"), facing=tg if tg in AGENTS else None,
                      bubble=self._bubble(ev))
            if tg in AGENTS:
                self._set(tg, state="LISTENING", facing=a)
                kind = {"AGREEMENT": "agree", "DISAGREEMENT": "disagree", "CHALLENGE": "disagree",
                        "VETO": "veto", "QUESTION": "question", "WARNING": "warn"}.get(mt, "info")
                S["arrows"].append({"from": a, "to": tg, "kind": kind})
            elif tg == "all":
                for x in AGENTS:
                    if x != a:
                        S["arrows"].append({"from": a, "to": x, "kind": "info"})
        elif t == "AGENT_FINISHED" and a:
            status = d.get("status")
            if status == "SKIPPED":
                self._set(a, state="IDLE", activity=ev.get("message") or "Non necessario", status="SKIPPED")
                S["step"][a] = "skipped"
            else:
                self._set(a, state="WAITING", activity="Analisi completata", confidence=d.get("confidence"),
                          model=d.get("model") or S["agents"][a]["model"], status=status, bias=d.get("bias"),
                          duration_ms=d.get("duration_ms"), key_evidence=d.get("key_evidence") or [])
                S["step"][a] = "done"
        elif t == "AGENT_FAILED" and a:
            self._set(a, state="FAILED", activity="Agente non disponibile", status="FAILED",
                      bubble=self._bubble(ev, "WARNING"))
            S["step"][a] = "failed"
        elif t == "ROUND_STARTED":
            S["room_mode"] = "debate"
            S["table"]["mode"] = "debate"
            S["table"]["status"] = "ROUND 2 — DIBATTITO"
            for x in ("price_action", "options_flow"):
                if S["agents"][x]["state"] != "FAILED":
                    self._set(x, loc="council", state="MOVING", activity="Verso il tavolo del Consiglio")
            S["arrows"] += [{"from": "price_action", "to": "strategist", "kind": "merge"},
                            {"from": "options_flow", "to": "strategist", "kind": "merge"}]
        elif t == "SCENARIO_CREATED" and a:
            sc = d.get("scenario") or {}
            S["table"]["scenarios"].append(ev.get("message"))
            self._set("strategist", state="SPEAKING", activity=f"Scenario {sc.get('id', '')}",
                      last_message=ev.get("message"), bubble=self._bubble(ev, "DECISION"))
        elif t in ("RISK_VETO", "RISK_VERDICT"):
            verdict = d.get("verdict")
            rank = {"APPROVED": 0, "APPROVED_WITH_CAUTION": 1, "NO_TRADE": 2, "VETO": 3, "NOT_APPROVED": 3}
            if S["risk_worst"] is None or rank.get(verdict, 0) > rank.get(S["risk_worst"], 0):
                S["risk_worst"] = verdict
            worst = S["risk_worst"]
            state = "VETO" if worst in ("VETO", "NOT_APPROVED") else "APPROVED" if worst == "APPROVED" else "WARNING"
            self._set("risk_manager", loc="council", state=state, activity=ev.get("message"),
                      last_message=ev.get("message"), bubble=self._bubble(ev))
            S["arrows"].append({"from": "strategist", "to": "risk_manager",
                                "kind": "veto" if verdict in ("VETO", "NOT_APPROVED") else
                                "agree" if verdict == "APPROVED" else "warn"})
            S["table"]["status"] = "VETO" if state == "VETO" else f"RISK: {worst}"
        elif t == "JUDGE_STARTED":
            S["room_mode"] = "judging"
            self._set("judge", loc="center", state="ANALYZING", activity=ev.get("message"), model=d.get("model"))
            S["step"]["judge"] = "active"
            S["persist_arrows"] = [{"from": x, "to": "judge", "kind": "converge"}
                                   for x in AGENTS[:4] if S["agents"][x]["state"] != "FAILED"]
            S["arrows"] = list(S["persist_arrows"])
            S["table"]["status"] = "GIUDIZIO IN CORSO"
        elif t == "JUDGE_FINISHED":
            self._set("judge", state="SPEAKING" if d.get("score") is not None else "FAILED",
                      activity=ev.get("message"), confidence=S["agents"]["judge"]["confidence"],
                      bubble=self._bubble(ev, "DECISION"))
            S["table"]["judge_score"] = d.get("score")
            S["step"]["judge"] = "done" if d.get("score") is not None else "failed"
        elif t == "FINAL_DECISION":
            st = d.get("status", "")
            S["persist_arrows"] = []
            S["arrows"] = []
            S["final"] = {"status": st, "label": FINAL_LABEL.get(st, st), "reasons": d.get("reasons", [])}
            S["room_mode"] = ROOM_MODE_BY_FINAL.get(st, "neutral")
            S["table"].update(mode="final", status=FINAL_LABEL.get(st, st), verdict=st)
            if st != "DATA_ERROR":
                for x in AGENTS:
                    ag = S["agents"][x]
                    loc = "center" if x == "judge" else "council"
                    if ag["state"] == "FAILED" or ag["status"] == "FAILED":
                        self._set(x, loc=loc)
                        continue
                    state = ag["state"]
                    if state not in ("VETO",):
                        state = {"APPROVED_SETUP": "AGREEING", "APPROVED_WITH_CAUTION": "WARNING"}.get(st, "WAITING")
                    if x == "judge":
                        state = "SPEAKING"
                    self._set(x, loc=loc, state=state, activity=FINAL_LABEL.get(st, st))
        elif t == "REPORT_GENERATED":
            S["step"]["report"] = "done"
            for x in AGENTS:
                ag = S["agents"][x]
                keep = ag["state"] in ("FAILED", "VETO")
                self._set(x, loc="desk", state=ag["state"] if keep else "IDLE",
                          activity=ag["activity"] if keep else "Report completato")
            S["monitors"] = False

    @staticmethod
    def _bubble(ev: dict[str, Any], force_type: str | None = None) -> dict[str, Any]:
        return {"text": ev.get("message") or "", "type": ev.get("message_type") or force_type or "ANALYSIS",
                "ts": _hhmmss(ev["timestamp"]), "target": ev.get("target_agent"),
                "evidence_ids": ev.get("evidence_ids") or [], "event_id": ev["event_id"],
                "source": ev.get("source", "system")}

    def snapshot(self, idx: int, ev: dict[str, Any]) -> dict[str, Any]:
        snap = copy.deepcopy({k: v for k, v in self.s.items() if k not in ("persist_arrows",)})
        for a, ag in snap["agents"].items():
            ag["pos"] = _pos(a, ag["loc"])
        a, tg = ev.get("agent"), ev.get("target_agent")
        label = f"{AGENT_META[a]['emoji']} {AGENT_META[a]['name']}" if a in AGENT_META else "SISTEMA"
        if tg in AGENT_META:
            label += f" → {AGENT_META[tg]['emoji']} {AGENT_META[tg]['name']}"
        return {"i": idx, "event_id": ev["event_id"], "event_type": ev["event_type"], "ts": ev["timestamp"],
                "caption": f"{ev['event_id']} · {ev['event_type']} · {label}",
                "agents": snap["agents"], "arrows": snap["arrows"], "monitors": snap["monitors"],
                "room_mode": snap["room_mode"], "final": snap["final"], "table": snap["table"],
                "steps": snap["step"]}


def build_view(report: dict[str, Any], ui_cfg: Any = None) -> dict[str, Any]:
    """Costruisce frames, timeline, transcript e dettagli agente da un report persistito."""
    events = report.get("events") or []
    red = _Reducer(report)
    frames = []
    for i, ev in enumerate(events):
        red.apply(ev)
        frames.append(red.snapshot(i, ev))

    # indici di salto per la timeline (evento che chiude ciascun passo)
    jump: dict[str, int] = {}
    finished: dict[str, int] = {}
    for i, ev in enumerate(events):
        t, a = ev["event_type"], ev.get("agent")
        if t == "DATA_VALIDATED":
            jump["data_validation"] = i
        elif t == "FEATURES_COMPUTED":
            jump["features"] = i
        elif t in ("AGENT_FINISHED", "AGENT_FAILED") and a:
            jump[a] = i
            finished[a] = i
        elif t == "JUDGE_FINISHED":
            jump["judge"] = i
            finished["judge"] = i
        elif t == "REPORT_GENERATED":
            jump["report"] = i
    steps = [{"key": k, "label": lbl, "agent": ag, "jump": jump.get(k, 0)} for k, lbl, ag in STEP_DEFS]

    transcript = []
    for i, ev in enumerate(events):
        d = ev.get("data") or {}
        transcript.append({
            "i": i, "id": ev["event_id"], "ts": _hhmmss(ev["timestamp"]), "type": ev["event_type"],
            "agent": ev.get("agent"), "target": ev.get("target_agent"), "mtype": ev.get("message_type"),
            "message": ev.get("message") or "", "evidence": ev.get("evidence_ids") or [],
            "source": ev.get("source", "system"), "confidence": d.get("confidence"),
            "status": d.get("status"), "model": d.get("model"), "duration_ms": d.get("duration_ms"),
        })

    details: dict[str, Any] = {}
    for a in AGENTS:
        ag = report.get("agents", {}).get(a) or {}
        o, meta = ag.get("output"), ag.get("meta")
        details[a] = {
            "status": ag.get("status"), "finished_idx": finished.get(a), "reason": ag.get("reason"),
            "model": (meta or {}).get("actual_model"), "requested_model": (meta or {}).get("requested_model"),
            "fallback_used": (meta or {}).get("fallback_used"), "duration_ms": (meta or {}).get("duration_ms"),
            "retry_count": (meta or {}).get("retry_count"), "errors": (meta or {}).get("errors", []),
            "confidence": (o or {}).get("confidence"), "bias": (o or {}).get("bias"),
            "summary": (o or {}).get("reasoning_summary"), "warnings": (o or {}).get("warnings", []),
            "facts": [c["text"] for c in (o or {}).get("facts", [])],
            "interpretations": [c["text"] for c in (o or {}).get("interpretations", [])],
            "scenarios": (o or {}).get("scenarios", []),
            "assessments": report.get("risk", {}).get("assessments", []) if a == "risk_manager" else [],
            "issues": report.get("judge", {}).get("issues", []) if a == "judge" else [],
            "score": report.get("judge", {}).get("score") if a == "judge" else None,
        }

    snap = report.get("market_snapshot") or {}
    ps, on, at, op = (snap.get(k) or {} for k in ("prev_session", "overnight", "atr", "options"))
    ticker = [("NQ", snap.get("spot")), ("SPOT", snap.get("spot")), ("VWAP", on.get("vwap")),
              ("ATR", at.get("atr_5m")), ("ON HIGH", on.get("high")), ("ON LOW", on.get("low")),
              ("GAMMA FLIP", op.get("gamma_flip")), ("CALL WALL", op.get("call_wall")),
              ("PUT WALL", op.get("put_wall")), ("REGIME", (snap.get("regime") or {}).get("state"))]
    run = report["run"]
    return {
        "frames": frames, "steps": steps, "transcript": transcript, "details": details,
        "meta": AGENT_META, "locations": LOCATIONS,
        "ticker": [{"k": k, "v": v} for k, v in ticker],
        "evidence": {e["id"]: {"label": e["label"], "value": e["value"]} for e in report.get("evidence", [])},
        "run": {"run_id": run["run_id"], "date": run["date"], "mode": run["mode"],
                "mock_scenario": run.get("mock_scenario"),
                "synthetic": any("SINTETICI" in i["message"] for i in report["data_quality"]["issues"]),
                "quality": report["data_quality"]["status"], "disclaimer": report["disclaimer"]},
        "ui": {"bubble_seconds": getattr(ui_cfg, "bubble_seconds", 6.0),
               "event_seconds": getattr(ui_cfg, "event_seconds", 1.6),
               "speeds": getattr(ui_cfg, "speeds", [0.5, 1.0, 2.0, 4.0]),
               "default_speed": getattr(ui_cfg, "default_speed", 1.0)},
    }
