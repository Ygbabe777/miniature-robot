"""Continuous learning: the 30-day research cycle.

Thirty days is an OPERATING period of the lab, not a statistical guarantee. The cycle keeps five quantities apart and reports each one:
  lab days elapsed | market sessions actually available (development) | historical data volume | events observed | forward (post-epoch) observations.
Weekly focus (baseline -> discovery -> hypothesis -> consolidation) shifts how many missions run and which tasks are due, but observation never stops.
State is persisted: a restart resumes the same cycle without repeating work. At the end a MARKET INTELLIGENCE REPORT is written once. Statuses used:
OBSERVED, HYPOTHESIZED, TESTED, SUPPORTED, INCONCLUSIVE, CONTRADICTED. Never 'proven'. A cycle with too few sessions or events reports itself inconclusive."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from . import behavior as BH
from . import questions as Q
from .engine import Engine
from .guard import DevView
from .observer import session_context
from .sessions import session_labels

FOCUS_MISSIONS = {"baseline": 1, "discovery": 4, "hypothesis": 3, "consolidation": 1}


def _ts(now: float | None) -> float:
    return time.time() if now is None else now


class LearningCycle:
    def __init__(self, engine: Engine, path: str | Path, cfg: dict):
        self.e, self.path, self.cfg = engine, Path(path), cfg
        self.state = {"id": None, "status": "idle"}
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8", errors="ofo_legacy"))
            except ValueError:
                pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # ------------------------------------------------------------------ lifecycle
    def start(self, now: float | None = None, days: int | None = None) -> dict:
        n = int(self.state.get("sequence", 0)) + 1
        self.state = {"id": f"C{n:03d}", "sequence": n, "status": "active", "started_at": _ts(now), "days": int(days or self.cfg["cycle"]["days"]),
                      "done": {}, "forward_sessions_seen": 0, "studies_run": 0, "events_observed": 0, "history": self.state.get("history", [])}
        self._save()
        self.e.say("research_director", "working", f"ciclo di apprendimento {self.state['id']} avviato ({self.state['days']} giorni di attività del Lab)")
        return self.state

    def elapsed_days(self, now: float | None = None) -> float:
        return max(0.0, (_ts(now) - float(self.state.get("started_at", _ts(now)))) / 86400)

    def focus(self, now: float | None = None) -> str:
        names = self.cfg["cycle"]["weekly_focus"]
        d = self.elapsed_days(now)
        span = max(1.0, float(self.state.get("days", 30)) / len(names))
        return names[min(len(names) - 1, int(d // span))]

    def _once(self, key: str) -> bool:
        """True the first time a task key is seen in this cycle (persisted): restarts and repeated ticks do not redo or re-announce work."""
        if key in self.state["done"]:
            return False
        self.state["done"][key] = time.strftime("%Y-%m-%d %H:%M:%S")
        return True

    # ------------------------------------------------------------------ one tick
    def tick(self, view: DevView, now: float | None = None) -> dict:
        if self.state.get("status") != "active":
            if self.state.get("status") in ("idle", None):
                self.start(now)
            else:
                return {"status": self.state["status"]}
        focus = self.focus(now)
        out = {"focus": focus}
        res = self.e.step(view, max_missions=FOCUS_MISSIONS[focus])
        out.update(res)
        self.state["studies_run"] += res["studies"]
        # weekly consolidation of the memory: once per focus/week key, never silently rewriting evidence
        wk = f"{self.state['id']}:consolidate:{int(self.elapsed_days(now) // 7)}"
        if focus == "consolidation" and self._once(wk):
            r = self.e.mem.consolidate()
            self.e.say("obsidian_memory", "done", f"consolidamento: {r['notes']} note, {len(r['obsolete'])} possibili obsolete, originali intatti")
        out["forward"] = self.track_forward(view)
        self.state["events_observed"] = sum(s["n_events"] for s in self.e.studies.get(view.data_version, {}).values())
        self.state["last_tick"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if self.elapsed_days(now) >= float(self.state["days"]) and self._once(f"{self.state['id']}:report"):
            out["report"] = self.finish(view, now)
        self._save()
        return out

    # ------------------------------------------------------------------ forward (prospective) observations
    def track_forward(self, view: DevView) -> dict:
        """Re-run the SAME frozen definitions of studies already registered on the sessions AFTER the hidden epoch. This verifies earlier predictions; it never
        creates hypotheses. Each forward session is counted once."""
        fwd = view.forward
        if len(fwd) == 0:
            return {"sessions": 0}
        n_sess = int(session_labels(fwd).nunique())
        new = n_sess - int(self.state.get("forward_sessions_seen", 0))
        if new <= 0:
            return {"sessions": n_sess, "new": 0}
        tail = view.development[session_labels(view.development).isin(sorted(session_labels(view.development).unique())[-3:]).to_numpy()]
        bars = pd.concat([tail, fwd])
        ctx = session_context(bars, self.cfg)
        ctx = ctx[ctx.index.isin(session_labels(fwd).unique())]
        by_def = {q["definition"]["id"]: q for q in (self.e.questions or Q.standard_questions(self.cfg))}
        written = 0
        for did, s in self.e.studies.get(view.data_version, {}).items():
            if s["status"] not in ("SUPPORTED", "INCONCLUSIVE") or did not in by_def:
                continue
            ev = Q.events_for(by_def[did], bars, ctx, self.cfg)
            r = BH.study(ev, bars, self.cfg, by_def[did]["text"] + " [forward]", s["definition"], claim=by_def[did]["claim"]) if len(ev) >= 3 else None
            body = (f"Verifica prospettica della definizione {did} sulle {n_sess} sessioni successive al blocco hidden (dati mai usati per formulare l'ipotesi).\n\n" +
                    (f"- eventi: {r['n_events']}, esito netto medio {r['distribution']['mean_net']:+.3f} ATR, stato {r['status']}\n" if r and r.get("distribution") else "- eventi insufficienti per una misura (<3).\n") +
                    "- ambito: FORWARD (non è sviluppo, non è locked).")
            self.e.mem.write("forward_observation", f"Forward {by_def[did]['text'][:50]}", body, market=self.e.market, evidence=(r["status"] if r else "INCONCLUSIVE"),
                             note_class="empirical_observation", access="dev", definition=f"fwd:{did}", data_version=view.forward_version(), family=by_def[did]["family"],
                             extra={"scope": "forward", "n_events": r["n_events"] if r else 0}, config_version=self.cfg["config_version"])
            written += 1
        self.state["forward_sessions_seen"] = n_sess
        if written:
            self.e.say("behavior_analyzer", "done", f"{written} verifiche forward su {n_sess} sessioni successive all'epoca")
        return {"sessions": n_sess, "new": new, "written": written}

    # ------------------------------------------------------------------ the report
    def counts(self, view: DevView, now: float | None = None) -> dict:
        dq = self.e.prepare(view)["dq"]
        rows = dq["bars"]
        studies = list(self.e.studies.get(view.data_version, {}).values())
        return {"lab_days_elapsed": round(self.elapsed_days(now), 1), "lab_days_planned": self.state.get("days"), "market_sessions_development": dq["sessions"],
                "historical_bars_1min": rows, "historical_years": round(dq["sessions"] / 252, 2), "events_observed": sum(s["n_events"] for s in studies),
                "studies": len(studies), "forward_sessions": int(self.state.get("forward_sessions_seen", 0)), "hypotheses": len(self.e.hyps)}

    def finish(self, view: DevView, now: float | None = None, pipeline: dict | None = None) -> dict:
        """The MARKET INTELLIGENCE REPORT. `pipeline`: {hypothesis id: {'stage': 'queued'|'tested', 'dev': {...development-stage fields only...}}}."""
        c = self.counts(view, now)
        studies = self.e.studies.get(view.data_version, {})
        by = {k: [] for k in ("OBSERVED", "HYPOTHESIZED", "TESTED", "SUPPORTED", "INCONCLUSIVE", "CONTRADICTED")}
        for s in studies.values():
            by[s["status"] if s["status"] in by else "INCONCLUSIVE"].append(s)
        min_s = int(self.cfg["cycle"]["min_sessions_for_claim"])
        thin = c["market_sessions_development"] < min_s or c["events_observed"] < int(self.cfg["behavior"]["min_events"])
        pipeline = pipeline or {}
        hyp_open = [h for h in self.e.hyps.values() if h["id"] not in pipeline]
        tested = [h for h in self.e.hyps.values() if h["id"] in pipeline]
        L = [f"# Market Intelligence Report — ciclo {self.state['id']}", "", f"Generato {time.strftime('%Y-%m-%d %H:%M')} · mercato {self.e.market} · configurazione {self.cfg['config_version']}", ""]
        if thin:
            L += ["> **REPORT INCONCLUDENTE.** Il periodo contiene troppe poche sessioni o troppi pochi eventi per sostenere conclusioni: nessun risultato è presentato come scoperta.", ""]
        L += ["## Cosa è stato misurato (quantità distinte)", "", f"- giorni di attività del Lab: {c['lab_days_elapsed']} su {c['lab_days_planned']}", f"- sessioni di mercato disponibili (sviluppo): {c['market_sessions_development']}",
              f"- storico: {c['historical_bars_1min']:,} barre a 1 minuto (~{c['historical_years']} anni)", f"- eventi osservati negli studi: {c['events_observed']} in {c['studies']} studi",
              f"- sessioni forward (successive al blocco hidden): {c['forward_sessions']}", f"- ipotesi formulate: {c['hypotheses']}", "",
              "Trenta giorni di attività non equivalgono a trenta sessioni di mercato né a campioni sufficienti: le conclusioni dipendono dal numero di eventi e di sessioni indipendenti.", ""]
        def section(title, items, fmt):
            L.extend([f"## {title} ({len(items)})", ""] + ([fmt(i) for i in items] or ["- nessuno"]) + [""])
        st = lambda s: f"- {s['name']} — {s['n_events']} eventi/{s['n_sessions']} sessioni" + (f", differenza vs baseline {s['baseline']['diff']:+.3f} ATR (IC95 {s['baseline']['diff_ci95'][0]:+.3f}, {s['baseline']['diff_ci95'][1]:+.3f})" if s.get('baseline') else "") + (f", q={s['q_value']:.2f}" if 'q_value' in s else "")
        section("OBSERVED — misurato, nessuna ipotesi verificata", by["OBSERVED"] + [{"name": "audit dati, regimi, tipi di giornata", "n_events": 0, "n_sessions": c["market_sessions_development"]}], lambda s: f"- {s['name']}")
        section("HYPOTHESIZED — ipotesi formulate, non ancora verificate", hyp_open, lambda h: f"- {h['id']} ({h['family']}, priorità {h['priority']}): {h['source_observations'][0]}")
        section("TESTED — inviate alla pipeline di ricerca", tested, lambda h: f"- {h['id']}: {pipeline[h['id']]['stage']}" + (f", sviluppo: {json.dumps(pipeline[h['id']].get('dev'), ensure_ascii=False)}" if pipeline[h['id']].get("dev") else ""))
        section("SUPPORTED — supportato su dati di sviluppo (non dimostrato)", [] if thin else by["SUPPORTED"], st)
        section("INCONCLUSIVE — non decidibile con questi dati", by["INCONCLUSIVE"] + (by["SUPPORTED"] if thin else []), st)
        section("CONTRADICTED — contraddetto", by["CONTRADICTED"], st)
        conflicts = self.e.mem.conflicts()
        L += [f"## Contraddizioni in memoria ({len(conflicts)})", ""] + ([f"- {x['question']} (differiscono per {', '.join(x['differ_in']) or '—'}; non risolto)" for x in conflicts] or ["- nessuna"]) + [""]
        nxt = self.e.director.summary()
        L += ["## Prossimo ciclo", ""] + ([f"- [{m['type']}] {m['question'][:140]} — {m['gap']}" for m in nxt["open"][:8]] or ["- nessuna lacuna aperta"]) + [""]
        if c["forward_sessions"] < 20:
            L += ["Osservazioni forward ancora scarse: l'evidenza prospettica è ai primi passi.", ""]
        body = "\n".join(L)
        note = self.e.mem.write("monthly_report", f"Market Intelligence Report {self.state['id']}", body, market=self.e.market, evidence="INCONCLUSIVE" if thin else "OBSERVED",
                                note_class="empirical_observation", access="dev", definition=f"report:{self.state['id']}", data_version=view.data_version, config_version=self.cfg["config_version"],
                                extra={"cycle": self.state["id"], "thin": thin})
        self.state["report"] = {"path": note["path"], "thin": thin, "counts": c, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.state["status"] = "completed"
        self.state["history"] = self.state.get("history", []) + [{"id": self.state["id"], "report": note["path"], "thin": thin}]
        self._save()
        self.e.say("research_director", "done", f"Market Intelligence Report {self.state['id']} pronto{' (inconcludente: campione insufficiente)' if thin else ''}")
        return {"path": note["path"], "thin": thin, "counts": c}

    def status(self, now: float | None = None) -> dict:
        if self.state.get("status") != "active":
            return {"status": self.state.get("status", "idle"), "report": self.state.get("report"), "history": self.state.get("history", [])}
        return {"status": "active", "id": self.state["id"], "focus": self.focus(now), "lab_days_elapsed": round(self.elapsed_days(now), 1), "lab_days_planned": self.state["days"],
                "studies_run": self.state["studies_run"], "events_observed": self.state["events_observed"], "forward_sessions": self.state.get("forward_sessions_seen", 0),
                "hypotheses": len(self.e.hyps), "missions": self.e.director.summary()["by_status"]}
