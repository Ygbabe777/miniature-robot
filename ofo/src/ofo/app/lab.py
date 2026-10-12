"""Lab backend: pending strategies, background study jobs, chat, charts. No web code here."""
from __future__ import annotations

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
from ..specs import StrategySpec
from ..storage import list_studies, load_study, slugify
from ..accounts import demo_accounts, load_accounts
from ..data import resample
from ..trades import extract_trades, summarize
from ..scout import Scout
from ..strategies import INDICATOR_HELP, TEMPLATES


LANGS = {"it": "Italian", "en": "English"}
MAX_CANDLES = 20000
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
        with self.run_lock:                       # one study at a time: they are CPU heavy
            job["status"] = "running"
            try:
                lp = self.loop()
                res = lp.process(spec, 0)
                job.update(status="done", slug=slugify(spec.name), verdict=res["verdict"])
                while lp.queue:                   # planner proposals wait for the owner's go-ahead
                    nxt, _ = lp.queue.pop(0)
                    self._add_pending(nxt, "planner")
            except Exception as e:
                job.update(status="error", error=str(e)[:300])
                self.events.emit("lab", "error", f"{spec.name}: {e}")
                for agent in ("optimizer", "validator", "judge"):      # nobody stays "working" after a crash
                    self.events.emit(agent, "idle", "stopped")

    def _ingest_now(self) -> None:
        lp = self.loop()
        lp.ingest_ideas(self.ideas)
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
                res = self.scout().run_once()
                if res["downloaded"]:
                    self._ingest_now()                  # new PDFs -> Researcher -> proposals waiting for your go-ahead
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

    def scout_state(self) -> dict:
        return self.scout().public_state(self._scout_running)

    def set_scout_auto(self, enabled: bool, minutes: int | None = None) -> dict:
        sc = self.scout()
        sc.state["auto"] = {"enabled": bool(enabled), "minutes": max(5, int(minutes or sc.state["auto"]["minutes"]))}
        sc._save()
        return self.scout_state()

    def scout_tick(self, now: float | None = None) -> bool:
        """Called every ~30 s by the background thread: starts a search when the schedule says so."""
        sc, now = self.scout(), now or time.time()
        a, last = sc.state["auto"], sc.state["last_run"]
        if a["enabled"] and not self._scout_running and (last is None or now - last["at"] >= a["minutes"] * 60):
            self.scout_run()
            return True
        return False

    def start_background(self) -> None:
        def loop():
            while True:
                time.sleep(30)
                try:
                    self.scout_tick()
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
                "ai": not isinstance(self.llm, NoLLM), "scout": self.scout_state()}

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
                "charts": charts, "cut": s.get("cut"), "best_params": s.get("best_params")}

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
                                                     self.contract, self.contracts)
        allt = self._trades_cache[key]
        looked = "checks" in rep["locked"]
        vis = allt if looked else [t for t in allt if t["session"] < ser["cut"]]
        tf = ser["best_params"]["tf"]
        return {"trades": vis, "n_hidden": len(allt) - len(vis), "locked_visible": looked, "tf": tf,
                "params": {k: v for k, v in ser["best_params"].items() if k != "tf"}, "cut": ser["cut"],
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
