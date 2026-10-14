"""Research Director (minimal): turns gaps in the lab's knowledge into prioritised, budgeted missions.

It did not exist before the MIE. It does not replace the family allocator (`Lab.pick_family`): it decides WHICH QUESTION about the market to study next, from
what the memory does not know. Missions are persisted, idempotent (the same gap is one mission, ever) and cheap: studies are computation on development data, not LLM calls."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .schema import fingerprint

TYPES = ("study_question", "sensitivity", "resolve_conflict", "screen_hypothesis", "refresh_stale")


class Director:
    def __init__(self, path: str | Path, cfg: dict):
        self.path, self.cfg = Path(path), cfg
        self.missions: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.missions = json.loads(self.path.read_text(encoding="utf-8", errors="ofo_legacy"))
            except ValueError:
                self.missions = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.missions, ensure_ascii=False, indent=1), encoding="utf-8")

    def _add(self, mtype: str, key: dict, question: str, gap: str, priority: float, market: str, data_version: str, links: list | None = None) -> dict | None:
        mid = "M-" + fingerprint({"t": mtype, "k": key, "m": market, "v": data_version})[:8]
        if mid in self.missions:
            return None                                                    # the same gap is one mission
        m = {"id": mid, "type": mtype, "question": question, "gap": gap, "priority": round(priority, 3), "market": market, "data_version": data_version, "status": "open",
             "created": time.strftime("%Y-%m-%d %H:%M:%S"), "links": links or [], "cost": "compute (sviluppo), nessuna chiamata LLM", "key": key}
        self.missions[mid] = m
        return m

    def plan(self, *, questions: list[dict], studied: dict[str, dict], conflicts: list[dict], hypotheses: list[dict], market: str, data_version: str, min_events: int,
             stale: list[dict] | None = None) -> list[dict]:
        """Open missions from the gaps. studied: definition id -> latest study summary {status, n_events, data_version}."""
        new = []
        for q in questions:
            did = q["definition"]["id"]
            s = studied.get(did)
            w = {"sweep_reclaim": 1.0, "orb": 0.9, "orb_failure": 0.8, "gap": 0.8, "level_acceptance": 0.6}.get(q["family"], 0.5)
            if s is None:
                m = self._add("study_question", {"def": did}, q["text"], "domanda mai studiata su questi dati", 0.5 + 0.4 * w, market, data_version)
            elif s["status"] == "INCONCLUSIVE" and s.get("n_events", 0) < min_events:
                m = self._add("sensitivity", {"def": did}, q["text"], f"pochi eventi ({s.get('n_events')}): provare varianti di definizione per capire se il campione è il limite", 0.3 + 0.2 * w, market, data_version)
            else:
                m = None
            if m:
                new.append(m)
        for c in conflicts:
            m = self._add("resolve_conflict", {"s": c["supported"], "c": c["contradicted"]}, c["question"], "note in contraddizione non risolte", 0.85, market, data_version, [c["supported"], c["contradicted"]])
            if m:
                new.append(m)
        for h in hypotheses:
            if h.get("pipeline") == "spec":
                m = self._add("screen_hypothesis", {"h": h["id"]}, f"Screening in pipeline dell'ipotesi {h['id']} ({h['family']})", "ipotesi con meccanismo e specifica non ancora in pipeline", 0.4 + 0.5 * h["priority"], market, data_version, [h["id"]])
                if m:
                    new.append(m)
        for s in stale or []:
            m = self._add("refresh_stale", {"def": s["definition"]}, f"Rivalutare {s['definition']} su dati più recenti", "conoscenza su dati precedenti", 0.35, market, data_version)
            if m:
                new.append(m)
        if new:
            self._save()
        return new

    def next(self, k: int = 3, today: str | None = None) -> list[dict]:
        """Highest-priority open missions, at most `max_missions_per_day` started per day."""
        today = today or time.strftime("%Y-%m-%d")
        started = sum(1 for m in self.missions.values() if str(m.get("started", ""))[:10] == today)
        room = max(0, int(self.cfg["director"]["max_missions_per_day"]) - started)
        opn = sorted((m for m in self.missions.values() if m["status"] == "open"), key=lambda m: -m["priority"])
        return opn[:min(k, room)]

    def start(self, mid: str) -> None:
        self.missions[mid].update(status="running", started=time.strftime("%Y-%m-%d %H:%M:%S"))
        self._save()

    def finish(self, mid: str, result: dict, status: str = "done") -> None:
        self.missions[mid].update(status=status, finished=time.strftime("%Y-%m-%d %H:%M:%S"), result=result)
        self._save()

    def summary(self) -> dict:
        from collections import Counter
        c = Counter(m["status"] for m in self.missions.values())
        return {"total": len(self.missions), "by_status": dict(c), "open": sorted((m for m in self.missions.values() if m["status"] == "open"), key=lambda m: -m["priority"])[:10]}
