"""Lab backend: pending strategies, background study jobs, chat, charts. No web code here."""
from __future__ import annotations

import contextlib
import os
import dataclasses
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

import pandas as pd

from ..agents.base import AgentError
from ..agents.loop import IDEA_SUFFIXES, ResearchLoop
from ..agents.roles import make_strategist
from ..backtest import MNQ
from ..events import EventLog
from ..llm.client import LLMError
from ..metrics import chart_pack
from ..prop import PRESETS
from ..registry import TrialRegistry
from ..agents.loop import _fingerprint
from ..specs import StrategySpec, from_dict
from ..storage import list_studies, load_study, slugify
from .. import deploy as dep
from .. import knowledge, obsidian
from ..dossier import build_dossier
from ..accounts import demo_accounts, load_accounts
from ..data import resample
from ..trades import extract_trades, summarize
from ..scout import Scout
from ..strategies import INDICATOR_HELP, TEMPLATES


LANGS = {"it": "Italian", "en": "English"}
MAX_CANDLES = 120000                    # about 85 sessions of 1-minute bars
TFS = ("1min", "5min", "15min", "30min", "60min", "1D")


class NoLLM:
    def chat(self, *a, **k):
        raise LLMError("AI non configurata: imposta OFO_LLM_BASE_URL e OFO_LLM_API_KEY e riavvia il Lab")


def spec_summary(sid: str, spec: StrategySpec, origin: str, registry_trials: int) -> dict:
    return {"id": sid, "name": spec.name, "template": spec.template, "n_candidates": spec.n_candidates,
            "rationale": spec.rationale, "ranges": spec.ranges, "timeframes": spec.timeframes,
            "source": spec.source, "origin": origin, "registry_trials": registry_trials}


class Lab:
    def __init__(self, bars_path: str, data_dir: str = "data", ideas_dir: str = "ideas", contract=MNQ,
                 rules=None, contracts: int = 2, llm=None, pool=None, fetch=None):
        self.bars_path, self.ideas = bars_path, Path(ideas_dir)
        self.data = Path(data_dir)
        self.work = self.data / "loop"
        self.contract, self.rules, self.contracts = contract, rules or PRESETS["50k"], contracts
        self.llm, self.pool = llm if llm is not None else NoLLM(), pool
        self.events = EventLog(self.data / "events.jsonl")
        self.registry = TrialRegistry(self.data / "registry.json")
        self.pending: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.lock, self.run_lock = threading.Lock(), threading.Lock()
        self._loop_obj: ResearchLoop | None = None
        self._bars: pd.DataFrame | None = None
        self._sessions = None
        self.language = "Italian"
        self._trades_cache: dict = {}
        self._ind_cache: dict = {}
        self.requests_file = self.data / "template_requests.json"
        self.ideas.mkdir(parents=True, exist_ok=True)
        self._fetch, self._scout, self._scout_running = fetch, None, False
        self.busy = {"research": 0, "job": 0, "chat": 0}          # real activity, not event timeouts
        self.ap_file = self.data / "autopilot.json"
        first = not self.ap_file.exists()
        self.ap = {"v": 2, "enabled": True, "max_trials_per_day": 30000, "max_trials_total": 300000, "day": "", "trials_today": 0}
        if not first:
            saved = json.loads(self.ap_file.read_text())
            if saved.get("v", 1) < 2:                 # v1 capped at 4000/day: queued strategies sat there for hours
                saved.pop("max_trials_per_day", None); saved.pop("max_trials_total", None)
            self.ap.update(saved)
            self.ap["v"] = 2
        self.ap_first_run = first
        self.dossier_dir = self.work / "dossiers"
        self.obs_file = self.data / "obsidian.json"
        self.obs = {"vault": os.environ.get("OFO_OBSIDIAN_VAULT") or "", "last": None, "error": None}
        if self.obs_file.exists() and not self.obs["vault"]:
            self.obs["vault"] = json.loads(self.obs_file.read_text()).get("vault", "")
        self.deployments = dep.Store(self.data / "deployments.json")
        self.busy["forward"] = 0
        self._last_forward = 0.0

    # -- engine -------------------------------------------------------------------------
    def bars(self) -> pd.DataFrame:
        with self.lock:
            if self._bars is None:
                self._bars = pd.read_csv(self.bars_path, index_col=0, parse_dates=True)
                key = pd.to_datetime(self._bars["session"]) if "session" in self._bars.columns \
                    else pd.Series(self._bars.index.normalize(), index=self._bars.index)
                self._sess = key
                self._sessions = sorted(key.unique())
        return self._bars

    def loop(self) -> ResearchLoop:
        if self._loop_obj is None:
            self._loop_obj = ResearchLoop(self.bars, self.contract, self.rules, self.registry, self.llm,
                                          self.events, self.work, contracts=self.contracts, language=self.language)
            self._loop_obj.memory = self.memory_text
        return self._loop_obj

    def set_lang(self, code: str | None) -> None:
        if code in LANGS:
            self.language = LANGS[code]
            if self._loop_obj is not None:
                self._loop_obj.language = self.language

    def candles(self, tf: str = "15min", days: int | None = 10, before: str | None = None) -> dict:
        """OHLCV candles from the user's own historical file, for the Trader chart.
        Times are exchange (New York) clock time, sent as if UTC so the chart shows them unchanged."""
        if tf not in TFS:
            return {"error": "bad_tf"}
        df = self.bars()
        sessions = self._sessions
        if before:
            cut = pd.Timestamp(before)
            sessions = [x for x in sessions if x < cut]
        if days:
            sessions = sessions[-int(days):]
        if not sessions:
            return {"error": "no_data", "first": str(self._sessions[0])[:10], "last": str(self._sessions[-1])[:10]}
        mask = (self._sess >= sessions[0]) & (self._sess <= sessions[-1])
        sub = df[mask]
        if tf == "1D":
            g = sub.groupby(self._sess[mask].values)
            d = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            rows = [[str(pd.Timestamp(i))[:10], float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume)]
                    for i, r in d.iterrows()]
        else:
            r = sub if tf == "1min" else resample(sub, tf)
            if len(r) > MAX_CANDLES:
                return {"error": "too_many", "count": int(len(r))}
            t = ((r.index - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).tolist()   # seconds, whatever the index unit
            rows = [[t[i], float(o), float(h), float(l), float(c), float(v)] for i, (o, h, l, c, v) in
                    enumerate(zip(r["open"], r["high"], r["low"], r["close"], r["volume"]))]
        return {"tf": tf, "candles": rows, "from": str(sessions[0])[:10], "to": str(sessions[-1])[:10],
                "n_sessions": len(sessions), "first_available": str(self._sessions[0])[:10],
                "last_available": str(self._sessions[-1])[:10], "symbol": self.contract.symbol}

    def _add_pending(self, spec: StrategySpec, origin: str) -> dict:
        sid = uuid.uuid4().hex[:8]
        with self.lock:
            self.pending[sid] = {"spec": spec, "origin": origin}
        return spec_summary(sid, spec, origin, self.registry.n_trials)

    @contextlib.contextmanager
    def working(self, kind: str):
        with self.lock:
            self.busy[kind] += 1
        try:
            yield
        finally:
            with self.lock:
                self.busy[kind] -= 1

    # -- chat ---------------------------------------------------------------------------
    def chat(self, message: str, history: list | None = None, lang: str | None = None) -> dict:
        self.set_lang(lang)
        message = (message or "").strip()
        if not message:
            return {"reply": "Scrivi un messaggio.", "spec": None, "template_request": None}
        self.events.emit("you", "message", message[:300])
        llm = self.llm["strategist"] if isinstance(self.llm, dict) else self.llm
        payload = {"message": message, "history": (history or [])[-6:],
                   "trials_so_far": self.registry.n_trials}
        try:
            with self.working("chat"):
                out = make_strategist(llm, self.events, self.language).run(payload)
        except (AgentError, LLMError) as e:
            self.events.emit("strategist", "error", str(e)[:300])
            return {"reply": f"Non sono riuscito a rispondere: {e}", "spec": None, "template_request": None, "error": True}
        res = {"reply": out["reply"], "spec": None, "template_request": None}
        if out["spec"] is not None:
            res["spec"] = self._add_pending(out["spec"], "chat")
            self.events.emit("strategist", "proposal", f"{out['spec'].name}: {out['spec'].n_candidates} combinazioni")
        elif out["template_request"]:
            self._save_request(out["template_request"], message)
            res["template_request"] = out["template_request"]
            self.events.emit("strategist", "template_request", out["template_request"]["name"])
        return res

    def _save_request(self, req: dict, message: str) -> None:
        cur = json.loads(self.requests_file.read_text()) if self.requests_file.exists() else []
        cur.append({**req, "asked": message[:300], "when": time.strftime("%Y-%m-%d %H:%M")})
        self.requests_file.write_text(json.dumps(cur, indent=1, ensure_ascii=False))

    # -- running studies ------------------------------------------------------------------
    def start(self, spec_id: str, lang: str | None = None) -> dict:
        self.set_lang(lang)
        with self.lock:
            item = self.pending.pop(spec_id, None)
        if item is None:
            return {"error": "strategia non trovata (già avviata?)"}
        spec = item["spec"]
        job = {"id": uuid.uuid4().hex[:8], "name": spec.name, "status": "queued", "slug": None,
               "verdict": None, "error": None, "n_candidates": spec.n_candidates}
        self.jobs[job["id"]] = job
        threading.Thread(target=self._run_job, args=(job, spec), daemon=True).start()
        return job

    def _run_job(self, job: dict, spec: StrategySpec) -> None:
        with self.working("job"), self.run_lock:  # one study at a time: they are CPU heavy
            job["status"] = "running"
            try:
                lp = self.loop()
                res = lp.process(spec, 0)
                job.update(status="done", slug=slugify(spec.name), verdict=res["verdict"])
                while lp.queue:                   # planner proposals: the autopilot runs them, otherwise they wait
                    nxt, _ = lp.queue.pop(0)
                    self._add_pending(nxt, "planner")
                if res["verdict"] == "APPROVED":
                    self.make_dossier(job["slug"])
                    self.deploy_ready()                # approved -> deployed (paper) with no click
            except Exception as e:
                job.update(status="error", error=str(e)[:300])
                self.events.emit("lab", "error", f"{spec.name}: {e}")
                for agent in ("optimizer", "validator", "judge"):      # nobody stays "working" after a crash
                    self.events.emit(agent, "idle", "stopped")

    def _ingest_now(self) -> None:
        with self.working("research"):
            lp = self.loop()
            lp.ingest_ideas(self.ideas)
            vi = self._vault_ideas()
            if vi is not None:
                lp.ingest_ideas(vi)                       # your Obsidian notes in OFO/Ideas
            while lp.queue:
                spec, _ = lp.queue.pop(0)
                self._add_pending(spec, "paper")

    def ingest(self) -> dict:
        def work():
            try:
                self._ingest_now()
            except Exception as e:
                self.events.emit("lab", "error", f"ingest: {e}")
        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    # -- Scout: autonomous literature search (arXiv) ---------------------------------------
    def scout(self) -> Scout:
        if self._scout is None:
            llm = self.llm.get("scout", self.llm.get("researcher")) if isinstance(self.llm, dict) else \
                (None if isinstance(self.llm, NoLLM) else self.llm)
            self._scout = Scout(self.data / "scout", self.ideas, llm, self.events, fetch=self._fetch, language=self.language)
        self._scout.language = self.language
        return self._scout

    def scout_run(self, lang: str | None = None, sync: bool = False) -> dict:
        self.set_lang(lang)
        with self.lock:
            if self._scout_running:
                return {"started": False, "reason": "already running"}
            self._scout_running = True

        def work():
            try:
                with self.working("research"):
                    res = self.scout().run_once()
                    if res["downloaded"] or res.get("abstracts") or self._unread_ideas():
                        self._ingest_now()                  # new PDFs -> Researcher -> proposals (the autopilot tests them)
            except Exception as e:
                self.events.emit("lab", "error", f"scout: {e}")
                self.events.emit("scout", "idle", "stopped")
            finally:
                self._scout_running = False
        if sync:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def scout_gap(self) -> int:
        """Seconds between searches. With the autopilot the work is continuous: a short pause after a run that
        found something, a few minutes after one that found nothing (new topics are invented meanwhile)."""
        sc = self.scout()
        last = sc.state["last_run"]
        if not self.ap["enabled"] or last is None:
            return sc.state["auto"]["minutes"] * 60
        return 45 if (last.get("found") or last.get("downloaded")) else 240

    def scout_state(self) -> dict:
        st = self.scout().public_state(self._scout_running)
        last = st["last_run"]
        if st["auto"]["enabled"] and last:
            st["next_run"] = last["at"] + self.scout_gap()
        return st

    def set_scout_auto(self, enabled: bool, minutes: int | None = None) -> dict:
        sc = self.scout()
        sc.state["auto"] = {"enabled": bool(enabled), "minutes": max(5, int(minutes or sc.state["auto"]["minutes"]))}
        sc._save()
        return self.scout_state()

    def scout_tick(self, now: float | None = None) -> bool:
        """Called every ~30 s by the background thread: starts a search when the schedule says so."""
        sc, now = self.scout(), now or time.time()
        a, last = sc.state["auto"], sc.state["last_run"]
        if a["enabled"] and not self._scout_running and (last is None or now - last["at"] >= self.scout_gap()):
            self.scout_run()
            return True
        return False

    def _unread_ideas(self) -> bool:
        done = self._loop_obj.processed_ideas if self._loop_obj else set()
        if self._loop_obj is None:
            f = self.work / "loop_state.json"
            done = set(json.loads(f.read_text()).get("processed_ideas", [])) if f.exists() else set()
        dirs = [self.ideas] + ([self._vault_ideas()] if self._vault_ideas() else [])
        return any(hashlib.sha256(p.read_bytes()).hexdigest() not in done for d in dirs for p in d.rglob("*")
                   if p.is_file() and p.suffix.lower() in IDEA_SUFFIXES and not p.name.startswith("_"))

    # -- Autopilot: search, test, judge and report without asking ---------------------------
    def _save_ap(self) -> None:
        self.ap_file.write_text(json.dumps(self.ap, indent=1))

    def set_autopilot(self, enabled: bool | None = None, max_trials_per_day: int | None = None) -> dict:
        if enabled is not None:
            self.ap["enabled"] = bool(enabled)
        if max_trials_per_day is not None:
            self.ap["max_trials_per_day"] = max(100, min(int(max_trials_per_day), 20000))
        self._save_ap()
        return self.autopilot_state()

    def _ap_budget(self, need: int) -> str | None:
        day = time.strftime("%Y-%m-%d")
        if self.ap["day"] != day:
            self.ap["day"], self.ap["trials_today"] = day, 0
        if self.registry.n_trials >= self.ap["max_trials_total"]:
            return "total"
        if self.ap["trials_today"] + need > self.ap["max_trials_per_day"]:
            return "day"
        return None

    def autopilot_tick(self) -> bool:
        """One step of the autonomous cycle. Deterministic rules still decide every verdict: the
        autopilot only removes the clicks. Returns True if it started a study."""
        if not self.ap["enabled"] or isinstance(self.llm, NoLLM):
            return False
        if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
            return False
        with self.lock:
            queue = list(self.pending.items())
        if not queue:
            self._refill()                             # nothing waiting: seed starters, then brainstorm
            with self.lock:
                queue = list(self.pending.items())
        if not queue:
            return False
        # first strategy that FITS the budget: one huge proposal must not block the small ones behind it
        why = "day"
        for sid, item in queue:
            why = self._ap_budget(item["spec"].n_candidates)
            if not why:
                break
        else:
            if self.ap.get("paused") != why:
                self.ap["paused"] = why
                self.events.emit("lab", "budget", f"autopilot paused: {why} trial budget reached")
            return False
        self.ap["paused"] = None
        self.ap["trials_today"] += item["spec"].n_candidates
        self._save_ap()
        self.events.emit("lab", "autostart", f"autopilot starts: {item['spec'].name}")
        return not self.start(sid).get("error")

    STARTER_FILES = ("starter.json", "two.json")

    def seed_specs(self) -> int:
        """The classic families (opening range, VWAP, EMA, breakout, z-score) are tested first, so the lab never sits idle
        waiting for the first paper. Specs already tested or queued are skipped."""
        n, lp = 0, self.loop()
        have = {_fingerprint(v["spec"]) for v in self.pending.values()}
        for base in (Path(__file__).resolve().parents[3] / "specs", Path("specs")):
            if not base.is_dir():
                continue
            for fn in self.STARTER_FILES:
                f = base / fn
                if not f.exists():
                    continue
                for d in json.loads(f.read_text()).get("strategies", []):
                    try:
                        sp = from_dict(d)
                    except Exception:
                        continue
                    fp = _fingerprint(sp)
                    if fp not in lp.done_specs and fp not in have:
                        have.add(fp); self._add_pending(sp, "starter"); n += 1
            break
        return n

    def brainstorm(self, now: float | None = None) -> int:
        """Starters exhausted: the Researcher proposes new hypotheses from what the lab learned (its memory),
        at most one request every 20 minutes. Every proposal still goes through the same validation and tests."""
        from ..papers import propose_specs
        now = now or time.time()
        if now - getattr(self, "_last_brainstorm", 0) < 20 * 60 or isinstance(self.llm, NoLLM):
            return 0
        self._last_brainstorm = now
        lp = self.loop()
        llm = self.llm["researcher"] if isinstance(self.llm, dict) else self.llm
        text = ("BRAINSTORM (no paper this time). Using the lab memory above, propose new, different, testable "
                "strategies for NQ/MNQ intraday futures: combine untested concepts, avoid what already failed, and give "
                "an economic mechanism for each. Explain it as if it came from the trading literature on opening "
                "range, volatility regimes, VWAP and session structure.")
        n = 0
        with self.working("research"):
            self.events.emit("researcher", "working", "brainstorming new hypotheses from the lab memory")
            try:
                specs, _ = propose_specs(llm, text, source="lab brainstorm", language=self.language, memory=self.memory_text())
            except Exception as e:
                self.events.emit("researcher", "idle", f"brainstorm failed: {e}"[:200])
                return 0
            have = {_fingerprint(v["spec"]) for v in self.pending.values()}
            for sp in specs:
                fp = _fingerprint(sp)
                if fp not in lp.done_specs and fp not in have:
                    have.add(fp); self._add_pending(sp, "brainstorm"); n += 1
            self.events.emit("researcher", "done", f"brainstorm: {n} new proposals")
        return n

    def connect_ideas(self, now: float | None = None) -> int:
        """The Researcher reads the knowledge network (and your Obsidian notes) and combines two concepts into new
        strategies: pairs you linked by hand first, then untested or successful concepts that were never combined.
        Each pair is tried once; the idea is written to the vault (OFO/Idee/) linked to both concepts."""
        from ..papers import propose_specs
        now = now or time.time()
        if now - getattr(self, "_last_connect", 0) < 15 * 60 or isinstance(self.llm, NoLLM):
            return 0
        notes = self._vault_notes()
        g = self.knowledge_graph(notes)
        f = self.data / "connections.json"
        done = set(json.loads(f.read_text())) if f.exists() else set()
        cands = knowledge.connection_candidates(g, obsidian.owner_pairs(notes), done, n=1)
        if not cands:
            return 0
        self._last_connect = now
        a, b, _ = cands[0]
        studies = {s["report"]["name"]: s["report"] for s in self._studies_full()}

        def describe(c):
            d = g["concepts"][c]
            ex = " | ".join((studies[x].get("rationale") or "")[:300] for x in d["strategies"][:2] if x in studies)
            return (f"- {c}: " + "; ".join(d["lessons"]) + (f" Example rationale: {ex}" if ex else "")
                    + (f" Owner's note: {' / '.join(d.get('owner_notes', []))}" if d.get("owner_notes") else ""))
        text = ("CONNECTION TASK (no paper this time). Two concepts from the lab's knowledge network:\n" + describe(a) + "\n"
                + describe(b) + f"\nPropose one or two NEW testable strategies that combine '{a}' and '{b}' (for example one as the "
                "signal and the other as a filter), with an economic mechanism. Do not repeat what already failed.")
        llm = self.llm["researcher"] if isinstance(self.llm, dict) else self.llm
        n, names = 0, []
        with self.working("research"):
            self.events.emit("researcher", "working", f"connecting ideas: {a} x {b}")
            try:
                specs, _ = propose_specs(llm, text, source=f"connection: {a} x {b}", language=self.language,
                                         memory=knowledge.memory_text(g))
            except Exception as e:
                self.events.emit("researcher", "idle", f"connection failed: {e}"[:200])
                return 0
            lp = self.loop()
            have = {_fingerprint(v["spec"]) for v in self.pending.values()}
            for sp in specs:
                fp = _fingerprint(sp)
                if fp not in lp.done_specs and fp not in have:
                    have.add(fp); self._add_pending(sp, "connection"); n += 1; names.append(sp.name)
            done.add(f"{a}|{b}")
            f.write_text(json.dumps(sorted(done)))
            if self.obs["vault"] and specs:
                try:
                    obsidian.idea_note(self.obs["vault"], a, b, names, specs[0].rationale,
                                       "it" if self.language == "Italian" else "en")
                except Exception as e:
                    self.obs["error"] = str(e)[:200]
            self.events.emit("researcher", "connection", f"{a} x {b}: {n} new proposals",
                             {"concepts": [a, b], "strategies": names})
            self.events.emit("researcher", "done", f"connected {a} x {b}")
        return n

    def _refill(self) -> None:
        if self.busy["research"] > 0:
            return
        if self.seed_specs() == 0 and self.connect_ideas() == 0:
            self.brainstorm()

    def autopilot_state(self) -> dict:
        sc = self.scout()
        last = sc.state["last_run"]
        nxt = (last["at"] + self.scout_gap()) if (sc.state["auto"]["enabled"] and last) else None
        self._ap_budget(0)
        enabled = self.ap["enabled"] and not isinstance(self.llm, NoLLM)
        running = any(j["status"] in ("queued", "running") for j in self.jobs.values())
        phase = ("off" if not enabled else "paused" if self.ap.get("paused") else "testing" if running
                 else "researching" if self.busy["research"] > 0 else "waiting")
        return {"enabled": enabled, "phase": phase, "paused": self.ap.get("paused"),
                "trials_today": self.ap["trials_today"], "max_trials_per_day": self.ap["max_trials_per_day"],
                "max_trials_total": self.ap["max_trials_total"], "queue": len(self.pending),
                "next_search": nxt, "searching": self._scout_running, "working": sorted(self.working_agents()),
                "ready": len(self.dossiers()),
                "deployed": sum(1 for x in self.deployments.d["items"] if x.get("status") in ("live", "confirmed"))}

    def working_agents(self) -> set[str]:
        """Who is really busy right now. Derived from live activity counters and the agents' own
        lifecycle events, never from a timeout: a long LLM wait must not look like 'switched off'."""
        life, b, out = self.events.life, self.busy, set()
        if b["research"] > 0:
            out.add("researcher")
            if life.get("scout") == "working" or self._scout_running:
                out.add("scout")
        if b["job"] > 0:
            for a in ("optimizer", "validator", "judge", "planner", "auditor"):
                if life.get(a) == "working":
                    out.add(a)
            if not out & {"optimizer", "validator", "judge", "planner", "auditor"}:
                out.add("optimizer")
        if b["chat"] > 0:
            out.add("strategist")
        if b.get("forward", 0) > 0:
            out.add("trader")
        return out

    # -- ready strategies -----------------------------------------------------------------
    def make_dossier(self, slug: str) -> dict | None:
        d = load_study(self.work, slug)
        if d is None or not d.get("series"):
            return None
        rep = {**d["report"], "series": d["series"]}
        lang = "it" if self.language == "Italian" else "en"
        dos = build_dossier(rep, {k: PRESETS[k] for k in ("50k", "25k")}, self.contracts, self.registry.n_trials,
                            lang, TEMPLATES[rep["template"]].doc, d.get("auditor"))
        if dos is None:
            return None
        dos["slug"], dos["created"] = slug, time.strftime("%Y-%m-%d %H:%M")
        self.dossier_dir.mkdir(parents=True, exist_ok=True)
        (self.dossier_dir / f"{slug}.json").write_text(json.dumps(dos, indent=1, ensure_ascii=False, default=str))
        self.events.emit("lab", "ready", f"READY: {dos['name']}", {"slug": slug})
        return dos

    def dossiers(self) -> list[dict]:
        if not self.dossier_dir.exists():
            return []
        out = []
        for f in sorted(self.dossier_dir.glob("*.json"), reverse=True):
            try:
                out.append(json.loads(f.read_text()))
            except ValueError:
                continue
        return out

    # -- knowledge network (the agents' memory) --------------------------------------------
    def _studies_full(self) -> list[dict]:
        out = []
        for r in list_studies(self.work):
            d = load_study(self.work, r["slug"])
            if d:
                out.append(d)
        return out

    def _vault_notes(self) -> dict:
        """The Researcher's access to the Obsidian memory: the vault's OFO/ notes, including what you wrote in them."""
        if not self.obs["vault"]:
            return {}
        try:
            return obsidian.read_notes(self.obs["vault"])
        except Exception:
            return {}

    def knowledge_graph(self, notes: dict | None = None) -> dict:
        g = knowledge.build(self._studies_full(), self.scout().state["papers"], self.work / "specs_auto",
                            notes if notes is not None else self._vault_notes())
        try:
            (self.data / "knowledge.json").write_text(json.dumps(g, indent=1, ensure_ascii=False, default=str))
        except OSError:
            pass
        return g

    def memory_text(self) -> str:
        """Facts from past tests, put in the Researcher's and Planner's prompts so they build on what was learned."""
        try:
            return knowledge.memory_text(self.knowledge_graph())
        except Exception:
            return ""

    # -- Obsidian ---------------------------------------------------------------------------
    def _vault_ideas(self) -> Path | None:
        v = self.obs["vault"]
        d = Path(v).expanduser() / "OFO" / "Ideas" if v else None
        return d if d is not None and d.is_dir() else None

    def set_obsidian(self, vault: str | None, sync: bool = True) -> dict:
        if vault is not None:
            vault = vault.strip().strip('"')
            if vault:
                obsidian.ensure_vault(vault)             # raises if the folder does not exist
            self.obs["vault"] = vault
            self.obs_file.write_text(json.dumps({"vault": vault}))
        if sync and self.obs["vault"]:
            self.obsidian_sync()
        return self.obsidian_state()

    def obsidian_state(self) -> dict:
        return {"vault": self.obs["vault"], "last": self.obs["last"], "error": self.obs["error"]}

    def obsidian_sync(self) -> dict | None:
        if not self.obs["vault"]:
            return None
        try:
            studies = []
            for r in list_studies(self.work):
                d = load_study(self.work, r["slug"])
                if d:
                    studies.append({**d, "saved_at": d.get("saved_at")})
            papers = {}
            for pid, p in self.scout().state["papers"].items():
                q = dict(p)
                if p.get("file"):
                    m = (self.ideas / p["file"]).with_suffix(".meta.json")
                    if m.exists():
                        try:
                            q["abstract"] = json.loads(m.read_text()).get("abstract", "")
                        except ValueError:
                            pass
                papers[pid] = q
            lang = "it" if self.language == "Italian" else "en"
            res = obsidian.sync(self.obs["vault"], studies, self.dossiers(), self.deployments.d["items"], papers,
                                self.events.tail(3000), lang, graph=self.knowledge_graph())
            self.obs["last"] = {"at": time.strftime("%Y-%m-%d %H:%M"), "wrote": res}
            self.obs["error"] = None
            return res
        except Exception as e:
            self.obs["error"] = str(e)[:200]
            return None

    # -- autonomous deployment (paper) ------------------------------------------------------
    def deploy_ready(self) -> int:
        """Every ready strategy that is not deployed yet goes live on paper. Idempotent."""
        n = 0
        for d in self.dossiers():
            if self.deployments.get(d["slug"]):
                continue
            self.bars()
            last = str(self._sessions[-1])[:10]
            item = dep.make_deployment(d, last, time.strftime("%Y-%m-%d %H:%M"))
            if item is None:
                item = {"slug": d["slug"], "name": d["name"], "status": "not_deployed",
                        "reason": "no safe size for any account", "forward": {"days": 0}}
                self.events.emit("trader", "skipped", f"{d['name']}: no safe size, not deployed")
            else:
                self.events.emit("trader", "deployed", f"DEPLOYED (paper): {d['name']} - {item['micro']} micro on {item['account']}",
                                 {"slug": d["slug"]})
            self.deployments.d["items"].append(item)
            self.deployments.save()
            n += 1
        return n

    def forward_tick(self, now: float | None = None, force: bool = False) -> bool:
        """Daily-ish: fetch new market days (if Massive keys are set), then update every live paper deployment."""
        now = now or time.time()
        live = [x for x in self.deployments.d["items"] if x.get("status") in ("live", "confirmed")]
        if not live or (not force and now - self._last_forward < 6 * 3600):
            return False
        self._last_forward = now
        with self.working("forward"):
            self.events.emit("trader", "working", "checking for new market data")
            info = self._refresh_data()
            self.deployments.d["data"] = {**info, "at": time.strftime("%Y-%m-%d %H:%M"),
                                          "last_bar": str(self.bars().index[-1])[:16]}
            bars = self.bars()
            for x in live:
                try:
                    r = dep.forward_update(bars, x, self.contract, self.contracts, self.rules_for(x["account"]))
                    x["forward"] = r["forward"]
                    sig = dep.new_signals(bars, x, self.contract, self.contracts)
                    fresh = sig[x["n_signals"]:]
                    dep.append_signals(self.data / "signals", x["slug"], fresh)
                    for t in fresh[-3:]:
                        self.events.emit("trader", "signal", f"{x['name']}: {'LONG' if t['side'] > 0 else 'SHORT'} {t['entry_px']:.2f} -> {t['exit_px']:.2f} ({t['pnl']:+.0f}$)")
                    x["n_signals"] = len(sig)
                    if r["status"] != x["status"] and not (x["status"] == "confirmed" and r["status"] == "live"):
                        x["status"], x["reason"] = r["status"], r["reason"]
                        self.events.emit("trader", x["status"], f"{x['name']}: {x['status'].upper()} - {x['reason']}")
                except Exception as e:
                    self.events.emit("lab", "error", f"forward {x['slug']}: {e}")
            self.deployments.save()
            self.events.emit("trader", "done", f"{len(live)} deployments updated")
        return True

    def rules_for(self, account: str):
        return PRESETS.get(account, self.rules)

    def _refresh_data(self) -> dict:
        import re
        root = (re.match(r"([A-Za-z]+)_", Path(self.bars_path).name) or [None, "NQ"])[1].upper()
        info = dep.refresh_data(self.data, self.bars_path, root)
        if info.get("rebuilt"):
            with self.lock:                                   # drop the cached 700k-row frame: the next read gets the new days
                self._bars = None; self._trades_cache.clear(); self._ind_cache.clear()
            self.bars()
        return info

    def deployments_view(self) -> dict:
        return {"items": self.deployments.d["items"], "data": self.deployments.d.get("data", {}),
                "last_bar": str(self.bars().index[-1])[:10] if Path(self.bars_path).exists() else None}

    def recover_specs(self) -> int:
        """Proposals the Researcher wrote before a restart but that were never tested come back to the queue."""
        n, lp = 0, self.loop()
        have = {_fingerprint(v["spec"]) for v in self.pending.values()}
        for f in sorted((self.work / "specs_auto").glob("*.json")) if (self.work / "specs_auto").exists() else []:
            try:
                for d in json.loads(f.read_text()).get("strategies", []):
                    sp = from_dict(d)
                    fp = _fingerprint(sp)
                    if fp not in lp.done_specs and fp not in have:
                        have.add(fp); self._add_pending(sp, "paper"); n += 1
            except Exception:
                continue
        return n

    def start_background(self) -> None:
        try:
            self.recover_specs()
        except Exception as e:
            self.events.emit("lab", "error", f"recover: {e}")
        if self.ap_first_run and not isinstance(self.llm, NoLLM):
            sc = self.scout()                          # first launch: the Researcher searches by itself every 30 min
            sc.state["auto"] = {"enabled": True, "minutes": 30}
            sc._save()
            self._save_ap()

        def loop():
            n = 0
            while True:
                time.sleep(5)
                n += 1
                try:
                    if self.ap["enabled"]:
                        if n % 3 == 0:
                            self.scout_tick()
                        self.autopilot_tick()
                        if n % 12 == 0:
                            self.deploy_ready()
                            self.forward_tick()
                            self.obsidian_sync()
                            if self.obs["vault"] and self.busy["research"] == 0 and self._unread_ideas():
                                threading.Thread(target=self._ingest_now, daemon=True).start()
                except Exception as e:
                    self.events.emit("lab", "error", f"scheduler: {e}")
        threading.Thread(target=loop, daemon=True).start()

    # -- views ----------------------------------------------------------------------------
    def state(self) -> dict:
        processed = self._loop_obj.processed_ideas if self._loop_obj else set()
        ideas = []
        for p in sorted(self.ideas.rglob("*")):
            if p.is_file() and p.suffix.lower() in IDEA_SUFFIXES:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                ideas.append({"name": str(p.relative_to(self.ideas)), "kb": round(p.stat().st_size / 1024),
                              "read": h in processed})
        with self.lock:
            pending = [spec_summary(i, v["spec"], v["origin"], self.registry.n_trials) for i, v in self.pending.items()]
        reqs = json.loads(self.requests_file.read_text()) if self.requests_file.exists() else []
        return {"trials": self.registry.n_trials, "locked_looks": self.registry.data["locked_looks"],
                "calls_last_hour": self.pool.calls_last_hour() if self.pool else None,
                "calls_per_hour": self.pool.calls_per_hour if self.pool else None,
                "calls_last_minute": self.pool.calls_last_minute() if self.pool else None,
                "calls_per_minute": self.pool.calls_per_minute if self.pool else None,
                "ideas": ideas, "pending": pending, "jobs": list(self.jobs.values())[-10:],
                "strategies": list_studies(self.work), "template_requests": reqs,
                "templates": {k: t.doc for k, t in TEMPLATES.items() if not t.control}, "symbol": self.contract.symbol,
                "ai": not isinstance(self.llm, NoLLM), "scout": self.scout_state(),
                "autopilot": self.autopilot_state(), "obsidian": self.obsidian_state(),
                "deployed": sum(1 for x in self.deployments.d["items"] if x.get("status") in ("live", "confirmed"))}

    def events_after(self, after: int) -> dict:
        ev = self.events.read()
        return {"events": ev[after:], "next": len(ev)}

    def detail(self, slug: str) -> dict | None:
        d = load_study(self.work, slug)
        if d is None:
            return None
        s = d["series"] or {}
        charts = {}
        for key in ("wf_oos", "dev_best", "locked"):      # "locked" exists only once the Judge looked
            if s.get(key):
                dates, pnl = zip(*s[key])
                charts[key] = chart_pack(dates, pnl)
        return {"report": d["report"], "planner": d["planner"], "auditor": d["auditor"],
                "charts": charts, "cut": s.get("cut"), "best_params": s.get("best_params"), "exit": s.get("exit")}

    def accounts(self) -> dict:
        real = load_accounts(self.data / "accounts.json")
        return {"accounts": real, "demo": False} if real else {"accounts": demo_accounts(), "demo": True}

    def trades(self, slug: str) -> dict | None:
        """Entries and exits of a study's best candidate. The hidden block's trades are shown
        only if the Judge already looked at it, exactly like the hidden block's charts."""
        d = load_study(self.work, slug)
        if d is None or not d["series"] or not d["series"].get("best_params"):
            return None
        rep, ser = d["report"], d["series"]
        key = (slug, d.get("saved_at"))
        if key not in self._trades_cache:
            params = dict(ser["best_params"]); tf = params.pop("tf")
            self._trades_cache[key] = extract_trades(self.bars(), TEMPLATES[rep["template"]].fn, params, tf,
                                                     self.contract, self.contracts, exit=ser.get("exit"))
        allt = self._trades_cache[key]
        looked = "checks" in rep["locked"]
        vis = allt if looked else [t for t in allt if t["session"] < ser["cut"]]
        tf = ser["best_params"]["tf"]
        return {"trades": vis, "n_hidden": len(allt) - len(vis), "locked_visible": looked, "tf": tf,
                "params": {k: v for k, v in ser["best_params"].items() if k != "tf"}, "cut": ser["cut"], "exit": ser.get("exit"),
                "summary": summarize(vis), "name": rep["name"]}

    def indicators(self, slug: str, start: int | None = None, end: int | None = None) -> dict | None:
        """The indicator lines the strategy's best candidate really uses, on its own timeframe."""
        d = load_study(self.work, slug)
        if d is None or not d["series"] or not d["series"].get("best_params"):
            return None
        rep = d["report"]
        t = TEMPLATES[rep["template"]]
        if t.indicators is None:
            return {"series": [], "tf": None, "template": rep["template"]}
        params = dict(d["series"]["best_params"]); tf = params.pop("tf")
        key = (slug, d.get("saved_at"))
        if key not in self._ind_cache:
            b = self.bars() if tf == "1min" else resample(self.bars(), tf)
            self._ind_cache[key] = (b.index, t.indicators(b, **params))
        index, lines = self._ind_cache[key]
        secs = ((index - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()
        lo = start if start is not None else secs[0]
        hi = end if end is not None else secs[-1]
        sel = (secs >= lo) & (secs <= hi)
        out = []
        for name, ser in lines.items():
            vals = ser.to_numpy()[sel]; ts = secs[sel]
            pts, prev_ok = [], False
            for tt, v in zip(ts.tolist(), vals.tolist()):
                ok = v == v                                          # not NaN
                if ok:
                    pts.append([tt, round(v, 4)])
                elif prev_ok:
                    pts.append([tt, None])                           # one gap marker: lines must not join across days
                prev_ok = ok
            out.append({"name": name, "points": pts})
        return {"series": out, "tf": tf, "template": rep["template"], "params": params,
                "help": INDICATOR_HELP.get(rep["template"], {})}

    def save_idea(self, name: str, data: bytes) -> dict:
        safe = Path(name).name
        if Path(safe).suffix.lower() not in IDEA_SUFFIXES:
            return {"error": "sono ammessi solo PDF, .txt e .md"}
        if len(data) > 40 * 1024 * 1024:
            return {"error": "file troppo grande (massimo 40 MB)"}
        dest = self.ideas / safe
        n = 1
        while dest.exists():
            dest = self.ideas / f"{Path(safe).stem}_{n}{Path(safe).suffix}"
            n += 1
        dest.write_bytes(data)
        self.events.emit("you", "upload", dest.name)
        return {"saved": dest.name}
