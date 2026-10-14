"""Market Intelligence inside the Lab: the glue between the Lab and `ofo.mie`.

Nothing here invents data. Every view is read from what the MIE actually persisted (snapshot, studies, missions, notes, cycle); whatever is missing is
reported as NOT AVAILABLE. The service never touches the hidden block: it builds a `DevView` (development sessions strictly before the frozen epoch,
forward sessions strictly after it) and hands ONLY that to the engine. Hypotheses reach the existing pipeline through `Lab._add_pending_all`, so the
Judge and the protocol (finalists, epoch, DSR) are unchanged. What the pipeline later says about a MIE hypothesis is written back to the memory with the
access label that protects it: development facts are `dev`, anything derived from the hidden block is `locked_derived` and never retrievable by the Researcher.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import finalists as FIN
from .. import research_config as rcfg_mod
from ..mie import config as MC
from ..mie import observer as OB
from ..mie import questions as Q
from ..mie import structure as ST
from ..mie.cycle import LearningCycle
from ..mie.engine import Engine
from ..mie.guard import DevView
from ..mie.memory import Memory
from ..storage import load_study

NA = "NOT AVAILABLE"
AGENTS = ("market_observer", "structure_engine", "behavior_analyzer", "obsidian_memory", "research_director")


class MIEService:
    def __init__(self, lab):
        self.lab = lab
        self.cfg = MC.load()
        self.dir = Path(lab.data) / "mie"
        self.snap_file = self.dir / "snapshot.json"
        self._lock = threading.Lock()
        self._parts: tuple | None = None
        self.running = False
        self.last_tick_at = 0.0
        self.last_error: str | None = None
        sch = self.cfg.get("scheduler") or {}
        self.enabled = bool(sch.get("enabled", True))
        self.interval_s = float(sch.get("interval_min", 20)) * 60

    # ------------------------------------------------------------------ construction (lazy: nothing is built until the first use)
    def _root(self) -> Path:
        v = self.lab.obs.get("vault") or ""
        if v and Path(v).exists():
            return Path(v) / "OFO"                                    # the SAME memory the owner already uses: no second vault
        return Path(self.lab.data) / "memory" / "OFO"

    def parts(self) -> tuple[Memory, Engine, LearningCycle]:
        root = self._root()
        if self._parts is None or self._parts[0].root != root:
            self.dir.mkdir(parents=True, exist_ok=True)
            mem = Memory(root, Path(self.lab.data) / "mie_key", Path(self.lab.data) / "mie_state.json")

            def submit(spec: dict, h: dict) -> int:
                from ..specs import from_dict
                return self.lab._add_pending_all(from_dict(spec, require_card="lenient"), "mie")
            eng = Engine(mem, self.dir, self.cfg, self.lab.registry, self.lab.events, submit, market=self.lab.primary)
            cyc = LearningCycle(eng, self.dir / "cycle.json", self.cfg)
            self._parts = (mem, eng, cyc)
        return self._parts

    def view(self) -> DevView:
        c = self.lab.ctx(self.lab.primary)
        bars = c.bars()
        proto = rcfg_mod.get().get("protocol") or {}
        ep = FIN.Finalists(self.lab.registry).epoch_for(self.lab.primary, c.sessions, rcfg_mod.get()["holdout_fraction"], int(proto.get("max_finalists", 3)))
        return DevView(bars, ep["start"], ep["end"], self.lab.primary)

    # ------------------------------------------------------------------ the scheduler
    def due(self) -> bool:
        lab = self.lab
        # only a running STUDY (CPU heavy, shares the data) blocks it: the Researcher waiting for the AI is not a reason to stay idle
        return self.enabled and not self.running and time.time() - self.last_tick_at >= self.interval_s and lab.busy.get("job", 0) == 0 and lab.ap.get("enabled", True)

    def tick(self, force: bool = False) -> dict:
        """One MIE increment. Runs only when no study is running (they are CPU heavy and the data is shared). Returns what happened."""
        with self._lock:
            if self.running:
                return {"skipped": "già in esecuzione"}
            self.running = True
        out: dict = {}
        try:
            with self.lab.working("mie"):
                _, _, cyc = self.parts()
                view = self.view()
                out = cyc.tick(view)
                self._snapshot(view)
                del view
            self.last_error = None
        except Exception as e:                                       # a failure is shown, never swallowed
            self.last_error = str(e)[:300]
            self.lab.events.emit("research_director", "error", f"MIE: {self.last_error}")
            out = {"error": self.last_error}
        finally:
            self.last_tick_at = time.time()
            self.running = False
            for a in AGENTS:                                         # nobody stays "working" after a step, whatever happened inside it
                self.lab.events.emit(a, "idle", "passo MIE concluso")
        return out

    def tick_async(self) -> dict:
        if self.running:
            return {"skipped": "già in esecuzione"}
        threading.Thread(target=self.tick, kwargs={"force": True}, daemon=True).start()
        return {"started": True}

    def start_cycle(self) -> dict:
        _, _, cyc = self.parts()
        cyc.start()
        return cyc.status()

    def _snapshot(self, view: DevView) -> None:
        mem, eng, cyc = self.parts()
        prep = eng.prepare(view)
        snap = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "market": view.market, "data_version": view.data_version, "dq": prep["dq"], "regime_share": prep["regime_share"],
                "epoch": {"start": view.epoch[0], "end": view.epoch[1]}, "counts": cyc.counts(view),
                "forward_sessions": int(view.forward["session"].nunique()) if len(view.forward) else 0}
        try:
            snap["chart"] = self._chart(view, prep, eng)
        except Exception as e:                                       # the chart is a view: its failure never stops the research
            snap["chart"] = None
            self.lab.events.emit("market_observer", "error", f"chart MIE: {e}"[:200])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.snap_file.write_text(json.dumps(snap, ensure_ascii=False, default=str), encoding="utf-8")

    def _chart(self, view: DevView, prep: dict, eng: Engine, sessions: int = 2) -> dict:
        """The last development sessions as 5-minute candles with what the agents actually computed on them: session levels, confirmed swings and the events of
        each study. DEVELOPMENT data only (the view cannot contain the hidden block); times are the data's own timestamps."""
        dev = view.development
        last = sorted(dev["session"].unique())[-sessions:]
        b1 = dev[dev["session"].isin(last)]
        c5 = OB.derive(b1, "5min")
        k = int(self.cfg["structure"]["swing_k"]["5min"])
        sw = ST.swings(c5, k, "5min")
        ctx = prep["ctx"]
        ctx = ctx[ctx.index.isin(last)]
        row = ctx.iloc[-1] if len(ctx) else None
        levels = {n: float(row[n]) for n in ("pdh", "pdl", "onh", "onl", "orh", "orl") if row is not None and row[n] is not None and row[n] == row[n]}
        events = []
        for did, st in (eng.studies.get(view.data_version) or {}).items():
            q = next((x for x in (eng.questions or Q.standard_questions(self.cfg)) if x["definition"]["id"] == did), None)
            if q is None:
                continue
            ev = Q.events_for(q, b1, ctx, self.cfg)
            for _, e in ev.iterrows():
                t = e.get("breach_time") if "breach_time" in ev.columns else e["available_at"]
                events.append({"t": str(t), "available_at": str(e["available_at"]), "sign": int(e["sign"]), "study": q["text"][:60], "status": st["status"], "level": (None if "level" not in ev.columns else float(e["level"]))})
        return {"tf": "5min", "sessions": [str(x)[:10] for x in last], "candles": [[str(i), float(r.open), float(r.high), float(r.low), float(r.close)] for i, r in c5.iterrows()],
                "levels": levels, "swings": [{"kind": r.kind, "price": r.price, "t": str(r.event_time), "confirmed_at": str(r.confirm_time)} for r in sw.itertuples()][-40:], "events": events[-40:],
                "scope": "development"}

    def chart(self) -> dict:
        snap = self.snapshot()
        ch = (snap or {}).get("chart")
        if ch and ch["candles"]:
            return {"available": True, "reason": None, **ch}
        why = ("un passo MIE è in corso" if self.running else f"ultimo passo fallito: {self.last_error}" if self.last_error else
               "il MIE non ha ancora completato un passo: premi «Esegui un passo ora»" if not snap else "la chart non è stata costruita nell'ultimo passo")
        return {"available": False, "reason": NA, "why": why}

    def snapshot(self) -> dict | None:
        try:
            return json.loads(self.snap_file.read_text(encoding="utf-8", errors="ofo_legacy")) if self.snap_file.exists() else None
        except ValueError:
            return None

    def touched(self) -> bool:
        """True once the MIE has ever run here. Before that, views answer NOT AVAILABLE without building anything (no folders are created in the vault by merely opening the page)."""
        return self._parts is not None or self.snap_file.exists() or (self.dir / "cycle.json").exists()

    # ------------------------------------------------------------------ views (only what exists)
    def status(self) -> dict:
        """Cheap summary for /api/state and the 3D scene: never builds anything, never reads market data."""
        snap = self.snapshot()
        st = {"enabled": self.enabled, "running": self.running, "last_tick": self.last_tick_at or None, "error": self.last_error, "available": snap is not None}
        if self._parts is None and not (self.dir / "cycle.json").exists():
            return st | {"cycle": {"status": "idle"}}
        _, eng, cyc = self.parts()
        sm = eng.director.summary()
        st.update(cycle=cyc.status(), missions=sm["by_status"], studies=sum(len(v) for v in eng.studies.values()), hypotheses=len(eng.hyps),
                  data_version=(snap or {}).get("data_version"))
        return st

    def overview(self) -> dict:
        snap = self.snapshot()
        if snap is None or not self.touched():
            return {"available": False, "reason": NA, "note": "Nessuna osservazione ancora: il Market Observer non ha ancora analizzato i dati."}
        dq = snap["dq"]
        _, eng, _ = self.parts()
        return {"available": True, "at": snap["at"], "market": snap["market"], "data_version": snap["data_version"], "period": [dq["first"][:10], dq["last"][:10]],
                "sessions": dq["sessions"], "bars": dq["bars"], "missing_minutes": dq["missing_minutes_total"], "short_sessions": dq["short_sessions"],
                "early_close_sessions": dq["early_close_sessions"], "duplicates": dq["duplicate_timestamps"], "regime_share": snap["regime_share"],
                "epoch": snap["epoch"], "forward_sessions": snap["forward_sessions"], "order_flow": NA, "counts": snap["counts"]}

    def intelligence(self) -> dict:
        if not self.touched():
            return {"available": False, "reason": NA, "studies": [], "hypotheses": [], "missions": {"by_status": {}, "open": []}, "duplicates_hypotheses": 0}
        snap = self.snapshot()
        _, eng, _ = self.parts()
        dv = (snap or {}).get("data_version")
        studies = [{"id": did, "name": s["name"], "family": s.get("family"), "status": s["status"], "n_events": s["n_events"], "n_sessions": s["n_sessions"],
                    "horizon_min": s.get("horizon_min"), "mean_net": (s.get("distribution") or {}).get("mean_net"), "diff": (s.get("baseline") or {}).get("diff"),
                    "q_value": s.get("q_value"), "limits": s.get("limits", [])[:3]} for did, s in (eng.studies.get(dv) or {}).items()]
        studies.sort(key=lambda x: (-(x["n_events"] or 0)))
        hyps = [{"id": h["id"], "family": h["family"], "priority": h["priority"], "novelty": h["novelty"]["score"], "pipeline": h["pipeline"], "sent": h.get("sent"),
                 "prediction": h["testable_prediction"], "falsification": h["falsification"], "mechanism": h["mechanism"], "sources": h["source_observations"],
                 "obsidian_refs": h["obsidian_refs"]} for h in eng.hyps.values()]
        hyps.sort(key=lambda x: -x["priority"])
        sm = eng.director.summary()
        return {"available": bool(studies or hyps), "reason": None if (studies or hyps) else NA, "studies": studies, "hypotheses": hyps,
                "missions": {"by_status": sm["by_status"], "open": [{"id": m["id"], "type": m["type"], "question": m["question"], "gap": m["gap"], "priority": m["priority"]} for m in sm["open"]]},
                "duplicates_hypotheses": eng.counters["duplicates_hypotheses"]}

    def memory_view(self) -> dict:
        if not self.touched():
            return {"available": False, "reason": NA, "shared_vault": bool(self.lab.obs.get("vault")), "notes_total": 0, "notes_intelligence": 0, "by_access": {}, "by_kind": {}, "by_evidence": {},
                    "conflicts": [], "recent": [], "errors": []}
        mem, _, _ = self.parts()
        s = mem.stats()
        idx = mem.index()
        recent = sorted((n for n in idx if not n.get("quarantined")), key=lambda n: str(n["front"].get("ofo_written") or ""), reverse=True)[:12]
        return {"available": s["notes_total"] > 0, "root": s["root"], "shared_vault": bool(self.lab.obs.get("vault")), "notes_total": s["notes_total"], "notes_intelligence": s["notes_intelligence"],
                "by_kind": s["by_kind"], "by_evidence": s["by_evidence"], "by_access": s["by_access"], "quarantined": s.get("quarantined"), "last_write": s.get("last_write"),
                "last_read": s.get("last_read"), "written": s.get("written"), "retrieved": s.get("retrieved"), "errors": s["errors"],
                "conflicts": [{"question": c["question"], "differ_in": c.get("differ_in")} for c in mem.conflicts()[:8]],
                "recent": [{"title": n.get("title"), "kind": n["front"].get("ofo_kind"), "evidence": n["front"].get("ofo_evidence"), "access": n["access"]} for n in recent]}

    def cycle_view(self) -> dict:
        if not self.touched():
            return {"status": {"status": "idle"}, "counts": None, "reason": NA, "weeks": self.cfg["cycle"]["weekly_focus"], "days": self.cfg["cycle"]["days"], "done": [], "report": None}
        _, eng, cyc = self.parts()
        st = cyc.status()
        snap = self.snapshot()
        out = {"status": st, "counts": (snap or {}).get("counts"), "weeks": self.cfg["cycle"]["weekly_focus"], "days": cyc.state.get("days", self.cfg["cycle"]["days"]),
               "done": sorted((cyc.state.get("done") or {}).items())[-8:], "report": cyc.state.get("report")}
        if st.get("status") == "idle":
            out["reason"] = NA
        return out

    # ------------------------------------------------------------------ Observation -> Memory -> Hypothesis -> Study -> Validation -> Judge -> Forward
    def hypothesis_of(self, slug: str) -> dict | None:
        if not self.touched():
            return None
        _, eng, _ = self.parts()
        d = load_study(self.lab.work, slug)
        if d is None:
            return None
        src = str(d["report"].get("source") or "")
        if not src.startswith("MIE ") or not self.touched():
            return None
        return eng.hyps.get(src.split(" ", 1)[1].strip())

    def trace(self, slug: str) -> dict:
        """The research trace of one strategy. A step that does not exist is NOT AVAILABLE; the Judge step shows the verdict only (it is public), never locked numbers."""
        d = load_study(self.lab.work, slug)
        if d is None:
            return {"error": "studio non trovato"}
        h = self.hypothesis_of(slug)
        if h is None:
            return {"slug": slug, "from_mie": False, "steps": []}
        rep = d["report"]
        jd = self.lab.judgment(slug) or {}
        j = jd.get("judgment") or {}
        dev_stages = [{"stage": r.get("stage"), "passed": r.get("passed")} for r in (rep.get("stages") or [])
                      if not any(k in str(r.get("stage", "")).lower() for k in ("blocc", "lock", "hidden", "giudic", "judg"))]
        ft = jd.get("forward_test")
        steps = [
            {"id": "observation", "label": "Observation", "state": "ok", "detail": "; ".join(h["source_observations"][:2]) or NA},
            {"id": "memory", "label": "Memory", "state": "ok" if h["obsidian_refs"] else "na", "detail": ", ".join(h["obsidian_refs"][:3]) or NA},
            {"id": "hypothesis", "label": "Hypothesis", "state": "ok", "detail": f"{h['id']} — {h['testable_prediction'][:140]}"},
            {"id": "study", "label": "Study", "state": "ok", "detail": f"{rep.get('name')} (trial registrati: {rep.get('trials', {}).get('total', NA) if isinstance(rep.get('trials'), dict) else NA})"},
            {"id": "validation", "label": "Validation", "state": "ok" if dev_stages else "na", "detail": ", ".join(f"{s['stage']}{'✓' if s['passed'] else '✗'}" for s in dev_stages[:6]) or NA},
            {"id": "judge", "label": "Judge", "state": "ok" if j.get("verdict") else "na", "detail": j.get("verdict") or NA},
            {"id": "forward", "label": "Forward", "state": "ok" if ft else "na", "detail": (f"{ft.get('days', '?')} giorni" if isinstance(ft, dict) else NA)},
        ]
        return {"slug": slug, "from_mie": True, "hypothesis": h["id"], "steps": steps}

    # ------------------------------------------------------------------ the pipeline answers the memory
    def after_study(self, slug: str, name: str, source: str, verdict: str | None) -> None:
        """Write back what the existing pipeline concluded about a MIE hypothesis. The study's DEVELOPMENT outcome is a `dev` note (the Researcher may read
        it); the Judge's verdict depends on the hidden block, so it is a separate `locked_derived` note the Researcher can never retrieve."""
        if not source.startswith("MIE "):
            return
        mem, eng, _ = self.parts()
        hid = source.split(" ", 1)[1].strip()
        h = eng.hyps.get(hid)
        if h is None:
            return
        d = load_study(self.lab.work, slug)
        rep = (d or {}).get("report") or {}
        stages = [f"- {r.get('stage')}: {'superata' if r.get('passed') else 'non superata'}" for r in (rep.get("stages") or [])
                  if not any(k in str(r.get("stage", "")).lower() for k in ("blocc", "lock", "hidden", "giudic", "judg"))]
        mem.write("validation", f"{hid} esito di sviluppo", f"L'ipotesi {hid} è stata inviata alla pipeline come «{name}». Fasi di sviluppo:\n\n" + ("\n".join(stages) or "- nessun dato") +
                  "\n\nQuesto è solo l'esito sui dati di SVILUPPO. Il verdetto finale dipende dal blocco protetto ed è in una nota separata non leggibile dal Researcher.",
                  market=self.lab.primary, evidence="TESTED", note_class="empirical_observation", access="dev", definition=f"pipe:{hid}",
                  data_version=h.get("data_version", ""), hypothesis_id=hid, config_version=self.cfg["config_version"])
        mem.write("verdict", f"{hid} verdetto del Judge", f"Verdetto deterministico per «{name}»: **{verdict or NA}**.", market=self.lab.primary, evidence="TESTED",
                  note_class="validated_conclusion" if verdict in ("APPROVED", "PAPER_CANDIDATE") else "negative_result", access="locked_derived", definition=f"judge:{hid}",
                  data_version=h.get("data_version", ""), hypothesis_id=hid, config_version=self.cfg["config_version"])
        eng.say("obsidian_memory", "done", f"{hid}: esito della pipeline scritto in memoria (sviluppo: leggibile; verdetto: protetto)")
