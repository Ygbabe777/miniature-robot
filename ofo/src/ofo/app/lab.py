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
from ..markets import MARKETS, root_of
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
from ..ledger import run_ledger
from ..budget import BudgetGovernor
from ..judge import Thresholds
from ..prop_intraday import arrays_from_ledger, describe as describe_intraday, intraday_report
from ..strategies import flatten_overnight
from ..dossier import build_dossier
from ..economics import load_assumptions, load_goal
from ..accounts import demo_accounts, load_accounts
from ..data import resample
from ..strategies import directional
from ..trades import extract_trades, summarize
from ..directives import Directives
from .. import farming as FM, fascicolo as FC
from ..optimizer import farming_context, farming_for_study
from ..research_budget import ResearchBudget
from .. import research_config as rcfg_mod
from ..search import spec_overlap
from ..storage import update_report
from ..scout import Scout
from ..sources import agent_reach_status, config_problem, load_config, missing_channels
from ..strategies import INDICATOR_HELP, TEMPLATES


LANGS = {"it": "Italian", "en": "English"}
MAX_CANDLES = 120000                    # about 85 sessions of 1-minute bars
TFS = ("1min", "5min", "15min", "30min", "60min", "1D")


class NoLLM:
    def chat(self, *a, **k):
        raise LLMError("AI non configurata: imposta OFO_LLM_BASE_URL e OFO_LLM_API_KEY e riavvia il Lab")


class MarketCtx:
    """One market: its own continuous 1-minute file, micro contract and research loop."""

    def __init__(self, lab, sym: str, path: str, contract, primary: bool):
        self.lab, self.sym, self.path, self.contract, self.primary = lab, sym, path, contract, primary
        self._bars = None
        self._sess = None
        self.sessions = None
        self._loop = None

    def bars(self) -> pd.DataFrame:
        with self.lab.lock:
            if self._bars is None:
                self._bars = pd.read_csv(self.path, index_col=0, parse_dates=True)
                key = pd.to_datetime(self._bars["session"]) if "session" in self._bars.columns \
                    else pd.Series(self._bars.index.normalize(), index=self._bars.index)
                self._sess = key
                self.sessions = sorted(key.unique())
        return self._bars

    def loop(self) -> ResearchLoop:
        if self._loop is None:
            lab = self.lab
            self._loop = ResearchLoop(self.bars, self.contract, lab.rules, lab.registry, lab.llm, lab.events, lab.work,
                                      contracts=lab.contracts, language=lab.language, market=self.sym,
                                      state_name="loop_state.json" if self.primary else f"loop_state_{self.sym}.json")
            self._loop.memory = lab.memory_text
        return self._loop

    def reset(self) -> None:
        self._bars = None


def spec_summary(sid: str, spec: StrategySpec, origin: str, registry_trials: int, market: str | None = None) -> dict:
    return {"id": sid, "market": market, "name": spec.name, "template": spec.template, "n_candidates": spec.n_candidates,
            "rationale": spec.rationale, "ranges": spec.ranges, "timeframes": spec.timeframes,
            "source": spec.source, "origin": origin, "registry_trials": registry_trials}


class Lab:
    def __init__(self, bars_path: str, data_dir: str = "data", ideas_dir: str = "ideas", contract=MNQ,
                 rules=None, contracts: int = 2, llm=None, pool=None, fetch=None, markets: dict | None = None,
                 channels: list | None = None):
        self.bars_path, self.ideas = bars_path, Path(ideas_dir)
        self.data = Path(data_dir)
        self.work = self.data / "loop"
        self.contract, self.rules, self.contracts = contract, rules or PRESETS["50k"], contracts
        self.llm, self.pool = llm if llm is not None else NoLLM(), pool
        self.events = EventLog(self.data / "events.jsonl")
        self.registry = TrialRegistry(self.data / "registry.json")
        self.registry.recover_stale()                  # studies that were running when the process died: failed, re-runnable, trials kept
        self.rbudget = ResearchBudget(self.registry)
        self.pending: dict[str, dict] = {}
        self._running_specs: dict[str, dict] = {}
        self.queue_file = self.data / "queue.json"
        self.jobs: dict[str, dict] = {}
        self.lock, self.run_lock = threading.Lock(), threading.Lock()
        self.primary = root_of(contract.symbol)
        self.mk: dict[str, MarketCtx] = {self.primary: MarketCtx(self, self.primary, bars_path, contract, True)}
        for root, path in (markets or {}).items():            # other markets: S&P 500, gold ... (files built like the main one)
            if root != self.primary and root in MARKETS and Path(path).exists():
                self.mk[root] = MarketCtx(self, root, path, MARKETS[root]["micro"], False)
        self.language = "Italian"
        self._trades_cache: dict = {}
        self._ind_cache: dict = {}
        self.requests_file = self.data / "template_requests.json"
        self.ideas.mkdir(parents=True, exist_ok=True)
        self._fetch, self._scout, self._scout_running = fetch, None, False
        self._channels = channels or []              # sources.build_channels(...): web / github / rss / exa / youtube / reddit / x
        self.busy = {"research": 0, "job": 0, "chat": 0, "review": 0}          # real activity, not event timeouts
        self.ap_file = self.data / "autopilot.json"
        first = not self.ap_file.exists()
        self.ap = {"v": 2, "markets": None, "enabled": True, "max_trials_per_day": 30000, "max_trials_total": 300000, "day": "", "trials_today": 0}
        if not first:
            saved = json.loads(self.ap_file.read_text())
            if saved.get("v", 1) < 2:                 # v1 capped at 4000/day: queued strategies sat there for hours
                saved.pop("max_trials_per_day", None); saved.pop("max_trials_total", None)
            self.ap.update(saved)
            self.ap["v"] = 2
        self.ap_first_run = first
        self.dossier_dir = self.work / "dossiers"
        self.obs_file = self.data / "obsidian.json"
        self.directives = Directives(self.data / "directives.json")
        self.obs = {"vault": os.environ.get("OFO_OBSIDIAN_VAULT") or "", "last": None, "error": None}
        if self.obs_file.exists() and not self.obs["vault"]:
            self.obs["vault"] = json.loads(self.obs_file.read_text()).get("vault", "")
        self.deployments = dep.Store(self.data / "deployments.json")
        self.busy["forward"] = 0
        self._last_forward = 0.0
        self._load_queue()

    # -- engine -------------------------------------------------------------------------
    def ctx(self, market: str | None = None) -> MarketCtx:
        return self.mk.get(market or self.primary) or self.mk[self.primary]

    def bars(self, market: str | None = None) -> pd.DataFrame:
        return self.ctx(market).bars()

    def loop(self, market: str | None = None) -> ResearchLoop:
        return self.ctx(market).loop()

    @property
    def _sessions(self):
        return self.mk[self.primary].sessions

    @property
    def _sess(self):
        return self.mk[self.primary]._sess

    @property
    def _loop_obj(self):
        return self.mk[self.primary]._loop

    def market_of(self, report: dict) -> str:
        m = report.get("market") or self.primary
        return m if m in self.mk else self.primary

    def set_lang(self, code: str | None) -> None:
        if code in LANGS:
            self.language = LANGS[code]
            for c in self.mk.values():
                if c._loop is not None:
                    c._loop.language = self.language

    def candles(self, tf: str = "15min", days: int | None = 10, before: str | None = None, market: str | None = None) -> dict:
        """OHLCV candles from the user's own historical file, for the Trader chart.
        Times are exchange (New York) clock time, sent as if UTC so the chart shows them unchanged."""
        if tf not in TFS:
            return {"error": "bad_tf"}
        c = self.ctx(market)
        df = c.bars()
        sessions = c.sessions
        if before:
            cut = pd.Timestamp(before)
            sessions = [x for x in sessions if x < cut]
        if days:
            sessions = sessions[-int(days):]
        if not sessions:
            return {"error": "no_data", "first": str(c.sessions[0])[:10], "last": str(c.sessions[-1])[:10]}
        mask = (c._sess >= sessions[0]) & (c._sess <= sessions[-1])
        sub = df[mask]
        if tf == "1D":
            g = sub.groupby(c._sess[mask].values)
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
                "n_sessions": len(sessions), "first_available": str(c.sessions[0])[:10],
                "last_available": str(c.sessions[-1])[:10], "symbol": c.contract.symbol, "market": c.sym}

    def enabled_markets(self) -> list[str]:
        """Markets strategies are tested on. Not chosen yet = the priority markets of the research config (NQ/MNQ first; ES/MES is a later
        phase, after the process is validated: switch it on in the market filter). Never empty."""
        sel = self.ap.get("markets")
        if sel is None:
            sel = rcfg_mod.get()["markets"]["priority"]
        out = [m for m in self.mk if sel is None or m in sel]
        return out or [self.primary]

    def set_markets(self, markets: list[str]) -> dict:
        """The market filter: e.g. ["NQ"] tests on Nasdaq only (NQ/MNQ prices, MNQ sizing), ["NQ","ES"] adds the S&P 500."""
        clean = [m for m in markets if m in self.mk]
        self.ap["markets"] = clean or [self.primary]
        self._save_ap()
        self.events.emit("lab", "markets", "testing on: " + ", ".join(self.enabled_markets()))
        return self.autopilot_state()

    # -- the research queue survives a restart ------------------------------------------------
    def _persist_queue(self) -> None:
        with self.lock:
            items = [{"id": i, "origin": v["origin"], "market": v.get("market"), "spec": dataclasses.asdict(v["spec"])} for i, v in self.pending.items()]
            run = [{"id": i, "origin": v["origin"], "market": v.get("market"), "spec": dataclasses.asdict(v["spec"])} for i, v in self._running_specs.items()]
        tmp = Path(str(self.queue_file) + ".tmp")
        tmp.write_text(json.dumps({"pending": items, "running": run}, ensure_ascii=False, default=str))
        tmp.replace(self.queue_file)

    def _load_queue(self) -> None:
        """Pending proposals AND the study that was running when the process ended come back (the registry has marked it failed)."""
        if not self.queue_file.exists():
            return
        try:
            d = json.loads(self.queue_file.read_text())
        except ValueError:
            return
        for it in d.get("pending", []) + d.get("running", []):
            try:
                self.pending[it["id"]] = {"spec": from_dict(it["spec"]), "origin": it["origin"], "market": it.get("market")}
            except Exception:
                continue

    def near_duplicate(self, spec: StrategySpec, market: str | None = None, threshold: float = 0.8) -> str | None:
        """An economically almost equivalent hypothesis: same template, same timeframes and parameter ranges overlapping >= threshold
        with something already tested or queued. Returns what it duplicates (a name) or None."""
        def same(rng, tfs, tmpl):
            return tmpl == spec.template and set(tfs or []) == set(spec.timeframes) and spec_overlap(rng, spec.ranges) >= threshold
        for v in list(self.pending.values()):
            if (v.get("market") or self.primary) == (market or self.primary) and v["spec"] is not spec and \
                    same(v["spec"].ranges, v["spec"].timeframes, v["spec"].template):
                return v["spec"].name
        for r in self.registry.study_rows(500):
            d = r.get("details") or {}
            if r["status"] in ("completed", "early_stopped") and (r.get("market") or self.primary) == (market or self.primary) and \
                    same(d.get("ranges"), d.get("timeframes"), d.get("template")):
                return r["label"].removeprefix("study:")
        return None

    def _add_pending_all(self, spec: StrategySpec, origin: str) -> int:
        """Queue a strategy once per market (skipping what that market already tested or has queued): the lab learns
        on which index each idea works. Returns how many queue entries were added."""
        n = 0
        fp = _fingerprint(spec)
        for m in self.enabled_markets():
            queued = any(v.get("market") == m and _fingerprint(v["spec"]) == fp for v in self.pending.values())
            if fp in self.loop(m).done_specs or queued:
                self.registry.note_duplicate("duplicates_exact")
                continue
            near = self.near_duplicate(spec, m)
            if near:                                           # same idea, cosmetically different: do not spend budget twice
                self.registry.note_duplicate("duplicates_near")
                self.events.emit("lab", "duplicate", f"{spec.name}: quasi equivalente a «{near}», non rieseguita")
                continue
            self._add_pending(spec, origin, m)
            n += 1
        return n

    def _add_pending(self, spec: StrategySpec, origin: str, market: str | None = None) -> dict:
        sid = uuid.uuid4().hex[:8]
        with self.lock:
            self.pending[sid] = {"spec": spec, "origin": origin, "market": market or self.primary}
        self._persist_queue()
        return spec_summary(sid, spec, origin, self.registry.n_trials, market or self.primary)

    @contextlib.contextmanager
    def working(self, kind: str):
        with self.lock:
            self.busy[kind] += 1
        try:
            yield
        finally:
            with self.lock:
                self.busy[kind] -= 1

    # -- token budget ---------------------------------------------------------------------
    def governor(self):
        return getattr(self.pool, "governor", None) if self.pool is not None else None

    def judge_thresholds(self) -> Thresholds:
        """The Judge's pass-rate rule comes from the owner's goal (rules/economics.json)."""
        g = load_goal(self._econ_path())
        return Thresholds(min_pass_rate=float(g.get("min_pass_rate", 0.30)), eval_days=int(g.get("max_eval_days", 5)),
                          base_contracts=self.contracts)

    def llm_allowed(self, role: str) -> bool:
        """Autonomous AI work asks here first. False = today's allowance is gone (or weekend / weekly reserve)."""
        g = self.governor()
        if g is None:
            return True
        ok, why = g.allow(role)
        if not ok and getattr(self, "_budget_msg", None) != why:
            self._budget_msg = why
            self.events.emit("lab", "budget", f"AI work paused: {why}")
        if ok:
            self._budget_msg = None
        return ok

    def economy(self) -> bool:
        g = self.governor()
        return bool(g and g.economy())

    def budget_state(self) -> dict | None:
        g = self.governor()
        return g.state() if g else None

    def set_budget(self, remaining_usd: float | None = None, weekly_usd: float | None = None,
                   usd_per_million: float | None = None) -> dict | None:
        g = self.governor()
        if g is None:
            return None
        if weekly_usd is not None:
            g.configure(weekly_usd=float(weekly_usd))
        if usd_per_million is not None:
            g.configure(usd_per_million_default=float(usd_per_million))
        if remaining_usd is not None:
            learned = g.set_remaining(float(remaining_usd))
            self.events.emit("lab", "budget", f"balance set to ${float(remaining_usd):.2f}" + (
                f"; learned price ${learned['learned_usd_per_million']:.2f} per million tokens" if learned["learned_usd_per_million"] else ""))
        return g.state()

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
        spec, market = item["spec"], item.get("market") or self.primary
        if market != self.primary and not spec.name.endswith(f"[{market}]"):
            spec = dataclasses.replace(spec, name=f"{spec.name} [{market}]")      # unique name per market
        job = {"id": uuid.uuid4().hex[:8], "name": spec.name, "market": market, "status": "queued", "slug": None,
               "verdict": None, "error": None, "n_candidates": spec.n_candidates}
        self.jobs[job["id"]] = job
        self._running_specs[job["id"]] = {"spec": spec, "origin": item.get("origin", "paper"), "market": market}
        self._persist_queue()
        threading.Thread(target=self._run_job, args=(job, spec, market), daemon=True).start()
        return job

    def _run_job(self, job: dict, spec: StrategySpec, market: str | None = None) -> None:
        with self.working("job"), self.run_lock:  # one study at a time: they are CPU heavy
            job["status"] = "running"
            try:
                lp = self.loop(market)
                lp.judge_th = self.judge_thresholds()
                job["status"], job["stage"] = "running", "starting"
                lp.on_stage = lambda name, detail="": job.update(stage=name, stage_detail=detail, stage_at=time.time())
                try:
                    res = lp.process(spec, 0)
                finally:
                    lp.on_stage = None
                job.update(status="done", slug=slugify(spec.name), verdict=res["verdict"])
                while lp.queue:                   # planner proposals: the autopilot runs them, otherwise they wait
                    nxt, _ = lp.queue.pop(0)
                    self._add_pending(nxt, "planner", market)
                self.farming_post(job["slug"])         # fascicolo + the board's discussion of the numbers (italian)
                if res["verdict"] == "APPROVED":
                    self.make_dossier(job["slug"])
                    self.deploy_ready()                # approved -> paper forward validation (gate 7); LIVE stays the owner's decision
            except Exception as e:
                job.update(status="error", error=str(e)[:300])
                self.events.emit("lab", "error", f"{spec.name}: {e}")
                for agent in ("optimizer", "validator", "judge"):      # nobody stays "working" after a crash
                    self.events.emit(agent, "idle", "stopped")
            finally:
                self._running_specs.pop(job["id"], None)
                self._persist_queue()

    def _ingest_now(self) -> None:
        if not self.llm_allowed("researcher"):
            return                                                      # the files stay unread until the budget allows
        with self.working("research"):
            lp = self.loop()
            lp.max_chunks = 2 if self.economy() else 6                  # economy: read less of each paper
            lp.ingest_ideas(self.ideas)
            vi = self._vault_ideas()
            if vi is not None:
                lp.ingest_ideas(vi)                       # your Obsidian notes in OFO/Ideas
            while lp.queue:
                spec, _ = lp.queue.pop(0)
                self._add_pending_all(spec, "paper")

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
            self._scout = Scout(self.data / "scout", self.ideas, llm, self.events, fetch=self._fetch, language=self.language,
                                channels=self._channels, directives=self.directives,
                                source_defaults=load_config("config/sources.json")["channels"])
        self._scout.language = self.language
        return self._scout

    def scout_run(self, lang: str | None = None, sync: bool = False) -> dict:
        self.set_lang(lang)
        if not self.llm_allowed("scout"):
            return {"started": False, "reason": "budget"}
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
        gap = 45 if (last.get("found") or last.get("downloaded")) else 240
        return gap * 4 if self.economy() else gap                      # saving tokens: search a quarter as often

    def _missing_channels(self) -> list[dict]:
        try:
            return missing_channels("config/sources.json")
        except Exception:
            return []

    def _agent_reach(self) -> dict:
        """What `agent-reach doctor` says (cached 10 minutes, fetched in the background: it can take a while). Optional."""
        c = getattr(self, "_ar", None)
        if c is None:
            c = self._ar = {"at": 0.0, "data": {"installed": None, "channels": {}}, "busy": False}
        if not c["busy"] and time.time() - c["at"] > 600:
            c["busy"] = True
            def go():
                try:
                    c["data"] = agent_reach_status()
                finally:
                    c["at"] = time.time(); c["busy"] = False
            threading.Thread(target=go, daemon=True).start()
        return c["data"]

    def _sources_view(self) -> list[dict]:
        """Every source the owner can pick, with whether it can run on this computer (tool installed) and its switch."""
        sc = self.scout()
        out = [{**x, "available": True, "hint": ""} for x in sc.sources()]
        have = {x["name"] for x in out}
        for m in self._missing_channels() + [{"name": n, "hint": h} for n, h in
                                              __import__("ofo.sources", fromlist=["HINTS"]).HINTS.items()]:
            if m["name"] not in have:
                have.add(m["name"]); out.append({"name": m["name"], "enabled": False, "available": False, "hint": m["hint"], "needs_account": m["name"] in ("reddit", "twitter")})
        return out

    def set_source(self, name: str, enabled: bool) -> dict:
        ok = self.scout().set_source(str(name), bool(enabled))
        if ok:
            self.events.emit("scout", "info", f"source {name}: {'ON' if enabled else 'OFF'}")
        return {"ok": ok, "sources": self._sources_view()}

    def tell_researcher(self, text: str, kind: str | None = None) -> dict:
        """The owner writes to the Researcher: what to search, keep searching, stop searching, or a general note."""
        try:
            item = self.directives.add(text, kind)
        except ValueError:
            return {"error": "empty"}
        msg = {"search": "Ricevuto: cerco subito", "keep": "Ricevuto: continuo a cercare", "stop": "Ricevuto: smetto di cercare",
               "note": "Ricevuto: ne terrò conto"}[item["kind"]]
        self.events.emit("researcher", "message", f"📌 {msg}: {item['topic'][:100]}", {"id": item["id"], "kind": item["kind"]})
        started = False
        if item["kind"] in ("search", "keep") and not self._scout_running:
            started = bool(self.scout_run().get("started", True)) if self.llm_allowed("scout") else False
        return {"item": item, "directives": self.directives.public(), "search_started": started}

    def forget_directive(self, did: str) -> dict:
        self.directives.remove(did)
        return {"directives": self.directives.public()}

    def web_activity(self, after: int = 0, limit: int = 120) -> dict:
        """Live feed of the Researcher's internet use: every search, result list, page read and save (newest last)."""
        ev = [e for e in self.events.tail(4000) if e.get("agent") == "scout" and e.get("type") == "web"]
        items = [{"i": k, **e["data"], "t": e["t"], "msg": e.get("message", "")} for k, e in enumerate(ev)]
        total = len(items)
        sc = self.scout()
        return {"total": total, "running": self._scout_running, "health": sc.state.get("health", {}),
                "channels": ["arxiv", "semanticscholar", "ssrn"] + [c.name for c in sc.channels],
                "sources": self._sources_view(), "missing": self._missing_channels(), "config_error": config_problem("config/sources.json"), "agent_reach": self._agent_reach(), "items": [x for x in items if x["i"] >= after][-limit:]}

    def scout_state(self) -> dict:
        st = self.scout().public_state(self._scout_running)
        st["channels"] = ["arxiv", "ssrn"] + [c.name for c in self.scout().channels]
        st["missing_channels"] = self._missing_channels()
        st["agent_reach"] = self._agent_reach()
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

    def research_report(self) -> dict:
        """The research engine in numbers: budget, trials per family, candidates per stage, elimination per gate, cost per test,
        duplicates avoided, promising hypotheses, recurring negatives. Screening, walk-forward, locked and forward results are
        kept apart: a screening result is never a statistical confirmation."""
        from collections import Counter
        c = getattr(self, "_rr_cache", None)
        if c and time.time() - c[0] < 15:
            return c[1]
        out = self._research_report_uncached()
        self._rr_cache = (time.time(), out)
        return out

    def _research_report_uncached(self) -> dict:
        from collections import Counter
        rb = self.rbudget.state()
        rows = self.registry.study_rows(3000)
        fams = rcfg_mod.get()["families"]
        fs = self.rbudget.family_stats()
        usage_f = self.registry.usage(None, "family")
        usage_s = self.registry.usage(None, "stage")
        stage_flow = {s: {"entered": 0, "passed": 0} for s in rcfg_mod.STAGES}
        gates, negs = Counter(), Counter()
        compute = evals = 0.0
        promising = []
        for r in rows:
            if r["status"] not in ("completed", "early_stopped"):
                continue
            res = r.get("results") or {}
            compute += float(r.get("compute_s") or 0); evals += float(res.get("n_evals") or 0)
            for st in res.get("stages", []):
                if st["stage"] in stage_flow:
                    stage_flow[st["stage"]]["entered"] += 1
                    stage_flow[st["stage"]]["passed"] += bool(st.get("passed"))
            if res.get("gate_failed"):
                gates[str(res["gate_failed"])[:70]] += 1
            if res.get("reject_reason") and res.get("locked_status") != "APPROVED":
                negs[str(res["reject_reason"])[:90]] += 1
            if res.get("validated") or res.get("approved"):
                promising.append({"name": r["label"].removeprefix("study:"), "family": r.get("family"), "locked_status": res.get("locked_status"),
                                  "best_tf": res.get("best_tf"), "evidence": "walk-forward" if not res.get("locked_status") or res.get("locked_status") == "REJECTED" else "blocco bloccato"})
        studies = list_studies(self.work)
        today = time.strftime("%Y-%m-%d")
        done_today = [r for r in rows if (r["created"] or "").startswith(today) and r["status"] in ("completed", "early_stopped")]
        return {"config_version": rb["config_version"], "budget": rb, "total_budget": {"trials_registered": self.registry.n_trials, "cap": self.ap["max_trials_total"],
                                                                                    "remaining": max(0, self.ap["max_trials_total"] - self.registry.n_trials)},
                "trials_by_family": {f: {"label": (fams.get(f) or {}).get("label", f), **{k: usage_f.get(f, {}).get(k, 0) for k in ("trials", "evals", "compute_s")},
                                         **{k: fs.get(f, {}).get(k, 0) for k in ("studies", "survivors", "validated", "approved", "false_positives")},
                                         "rate": fs.get(f, {}).get("rate"), "status": (fams.get(f) or {}).get("status", "active"), "note": (fams.get(f) or {}).get("note")}
                                     for f in sorted(set(list(fams) + list(usage_f)))},
                "stage_usage": usage_s, "stage_flow": stage_flow, "elimination_by_gate": gates.most_common(10),
                "mean_cost_per_test_s": round(compute / evals, 3) if evals else None, "mean_study_s": round(compute / max(1, len([r for r in rows if r["compute_s"]])), 1),
                "duplicates_avoided": {"exact": self.registry.counter("duplicates_exact"), "near": self.registry.counter("duplicates_near")},
                "promising": promising[:10], "recurring_negatives": negs.most_common(8),
                "in_queue": len(self.pending), "running": len(self._running_specs), "in_paper": sum(1 for x in self.deployments.d["items"] if x.get("status") in ("live", "confirmed")),
                "approved": sum(1 for x in studies if x.get("status") == "APPROVED"),
                "today": {"studies": len(done_today), "screened": rb["used"]["screening"], "deep": rb["deep_used"],
                          "approved": sum(1 for r in done_today if (r.get("results") or {}).get("approved")),
                          "thresholds_changed": False,
                          "note": "una giornata può chiudersi con zero candidati validi: le soglie non si abbassano mai per raggiungere una quota"},
                "evidence_note": "screening = dati di sviluppo (non è una conferma); walk-forward = sviluppo con finestre cronologiche; blocco bloccato = un solo sguardo; forward = paper",
                "blocked_families": {f: v for f, v in fams.items() if v.get("status") == "blocked_no_data"}}

    def researcher_status(self) -> dict:
        """Is the Researcher really using the AI? What it did with the last files, and why it may be waiting (budget, no AI, nothing to read)."""
        g = self.governor()
        ok, why = (g.allow("researcher") if g else (True, ""))
        done = self._loop_obj.processed_ideas if self._loop_obj else set()
        dirs = [self.ideas] + ([self._vault_ideas()] if self._vault_ideas() else [])
        unread = sum(1 for d in dirs for p in d.rglob("*") if p.is_file() and p.suffix.lower() in IDEA_SUFFIXES and not p.name.startswith("_")
                     and hashlib.sha256(p.read_bytes()).hexdigest() not in done)
        lp = self._loop_obj
        return {"ai": not isinstance(self.llm, NoLLM), "allowed": ok, "why": why, "budget_mode": g.mode() if g else None, "unread": unread,
                "queue": len(self.pending), "autopilot": bool(self.ap["enabled"]),
                "recent": [{**x, "at": time.strftime("%H:%M:%S", time.localtime(x["at"]))} for x in (lp.ingest_log[-5:] if lp else [])],
                "last_brainstorm": time.strftime("%H:%M:%S", time.localtime(self._last_brainstorm)) if getattr(self, "_last_brainstorm", 0) else None}

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

    def _ap_budget(self, spec) -> str | None:
        """Why this study may not start now, or None. Uses the Research Budget Manager (per stage, per day, from the registry) plus the
        total trial budget and the owner's own daily ceiling (the slider), whichever is lower."""
        if self.registry.n_trials >= self.ap["max_trials_total"]:
            return "total"
        ok, stage, detail = self.rbudget.can_start(spec)
        if ok and self.rbudget.used()["screening"] + spec.n_candidates > self.ap["max_trials_per_day"]:
            ok, stage = False, "screening"
        self._budget_detail = detail
        return None if ok else ("day" if stage in ("screening", "deep_candidates") else f"day:{stage}")

    def autopilot_tick(self) -> bool:
        """One step of the autonomous cycle. Deterministic rules still decide every verdict: the
        autopilot only removes the clicks. Returns True if it started a study."""
        if not self.ap["enabled"] or isinstance(self.llm, NoLLM):
            return False
        if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
            return False
        on = set(self.enabled_markets())

        def waiting():
            with self.lock:                            # entries for a market the filter switched off simply stay parked
                return [(i, v) for i, v in self.pending.items() if (v.get("market") or self.primary) in on]
        queue = waiting()
        if not queue:
            self._refill()                             # nothing waiting: seed starters, then brainstorm
            queue = waiting()
        if not queue:
            return False
        # first strategy that FITS the budget, in the order of the family allocator: one huge proposal must not block the small ones
        why = "day"
        by_id = dict(queue)
        for item in self.rbudget.rank([{"id": i, **v} for i, v in queue]):
            sid, item = item["id"], by_id[item["id"]]
            why = self._ap_budget(item["spec"])
            if not why:
                break
        else:
            if self.ap.get("paused") != why:
                self.ap["paused"] = why
                self.events.emit("lab", "budget", f"autopilot paused: {why} trial budget reached")
            return False
        self.ap["paused"] = None
        self.ap["trials_today"] = self.rbudget.used()["screening"] + item["spec"].n_candidates      # shown in the UI
        self._save_ap()
        self.events.emit("lab", "autostart", f"autopilot starts: {item['spec'].name}")
        return not self.start(sid).get("error")

    STARTER_FILES = ("starter.json", "two.json", "families.json")

    def seed_specs(self) -> int:
        """The classic families (opening range, VWAP, EMA, breakout, z-score) are tested first, so the lab never sits idle
        waiting for the first paper. Specs already tested or queued are skipped."""
        n = 0
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
                    n += self._add_pending_all(sp, "starter")
            break
        return n

    def brainstorm(self, now: float | None = None) -> int:
        """Starters exhausted: the Researcher proposes new hypotheses from what the lab learned (its memory),
        at most one request every 20 minutes. Every proposal still goes through the same validation and tests."""
        from ..papers import propose_specs
        now = now or time.time()
        if now - getattr(self, "_last_brainstorm", 0) < 20 * 60 or isinstance(self.llm, NoLLM):
            return 0
        if self.economy() or not self.llm_allowed("researcher"):
            return 0                                                    # brainstorming is the first thing to go when saving tokens
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
            for sp in specs:
                n += self._add_pending_all(sp, "brainstorm")
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
        if self.economy() or not self.llm_allowed("researcher"):
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
            return (f"- {c}: " + "; ".join(d.get("dev_lessons", [])) + (f" Example rationale: {ex}" if ex else "")
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
            for sp in specs:
                if self._add_pending_all(sp, "connection"):
                    n += 1; names.append(sp.name)
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
        self.ap["day"] = time.strftime("%Y-%m-%d")
        self.ap["trials_today"] = self.rbudget.used()["screening"]            # from the registry: it cannot disagree with the trial count
        enabled = self.ap["enabled"] and not isinstance(self.llm, NoLLM)
        running = any(j["status"] in ("queued", "running") for j in self.jobs.values())
        bm = self.governor().mode() if self.governor() else "normal"
        phase = ("off" if not enabled else "paused" if self.ap.get("paused") else "testing" if running
                 else "budget" if bm in ("paused_day", "paused_weekend", "blocked") and not self.pending
                 else "researching" if self.busy["research"] > 0 else "waiting")
        return {"enabled": enabled, "phase": phase, "paused": self.ap.get("paused"),
                "trials_today": self.ap["trials_today"], "max_trials_per_day": self.ap["max_trials_per_day"],
                "max_trials_total": self.ap["max_trials_total"], "queue": len(self.pending),
                "next_search": nxt, "searching": self._scout_running, "working": sorted(self.working_agents()),
                "ready": len(self.dossiers()), "budget_mode": (self.governor().mode() if self.governor() else None),
                "markets": [{"sym": k, "name": MARKETS[k]["name"], "micro": c.contract.symbol, "mini": MARKETS[k]["mini"].symbol,
                             "enabled": k in self.enabled_markets()} for k, c in self.mk.items()],
                "deployed": sum(1 for x in self.deployments.d["items"] if x.get("status") in ("live", "confirmed")),
                "fascicoli": self.fascicoli(), "farming_pending": len(self.farming_pending()), "researcher": self.researcher_status()}

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
        if b.get("review", 0) > 0:
            out.add("judge")                                          # the prop-farming re-review of an old study is the Judge's work, not the Optimizer's
        if b["chat"] > 0:
            out.add("strategist")
        if b.get("forward", 0) > 0:
            out.add("trader")
        return out

    # -- ready strategies -----------------------------------------------------------------
    # -- prop farming: fascicolo, board, decisions, legacy re-review ----------------------------------
    def _fasc_dir(self) -> Path:
        d = self.data / "fascicoli"; d.mkdir(parents=True, exist_ok=True)
        return d

    def decisions(self) -> dict:
        f = self.data / "decisions.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def decide(self, slug: str, decision: str, note: str = "") -> dict:
        """The owner's decision on an approved strategy. 'live' means YOU will trade it (OFO never sends orders): it keeps its paper
        forward tracking so you can compare. Nothing is decided automatically."""
        if decision not in ("live", "discarded", "hold"):
            return {"error": "decisione non valida"}
        d = self.decisions()
        d[slug] = {"decision": decision, "note": note[:300], "at": time.strftime("%Y-%m-%d %H:%M")}
        f = self.data / "decisions.json"; f.write_text(json.dumps(d, indent=1, ensure_ascii=False))
        label = {"live": "LIVE (decisione tua, nessun ordine inviato da OFO)", "discarded": "scartata", "hold": "in attesa"}[decision]
        self.events.emit("you", "decision", f"{slug}: {label}", {"slug": slug, "decision": decision})
        return {"ok": True, "decision": d[slug]}

    def farming_post(self, slug: str, discuss: bool = True) -> dict | None:
        """After a study that reached the Judge's look: store the fascicolo and let the board discuss (messages derived from the numbers)."""
        d = load_study(self.work, slug)
        if not d or not (d["report"].get("farming")):
            return None
        rep = {**d["report"], "series": d["series"] or {}}
        doc = FC.build(slug, rep)
        if doc is None:
            return None
        (self._fasc_dir() / f"{slug}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=str))
        if discuss:
            def talk():
                for line in doc["verbale"]:
                    self.events.emit(line["agent"], "board", f"{line['role']}: {line['text']}", {"slug": slug})
                    time.sleep(4.0)
            threading.Thread(target=talk, daemon=True).start()
        return doc

    @staticmethod
    def _stamp(directory: Path, pattern: str = "*.json") -> tuple:
        return tuple((f.name, f.stat().st_mtime_ns) for f in sorted(directory.glob(pattern))) if directory.exists() else ()

    def farming_pending(self) -> list[str]:
        key = self._stamp(self.work / "reports")                  # the state is polled every few seconds: recompute only on change
        c = getattr(self, "_fp_cache", None)
        if c and c[0] == key:
            return c[1]
        out = self._farming_pending_uncached()
        self._fp_cache = (key, out)
        return out

    def _farming_pending_uncached(self) -> list[str]:
        out = []
        for r in list_studies(self.work):
            try:
                d = load_study(self.work, r["slug"])
            except ValueError:                                     # a file being rewritten right now: look again next time
                continue
            if not d or not d.get("series"):
                continue
            rep = d["report"]
            lk = rep.get("locked") or {}
            if "checks" in lk and not rep.get("farming") and not lk.get("farming_review_error"):
                out.append(r["slug"])
        return out

    def farming_review(self, slug: str) -> dict:
        """Re-review a strategy the Judge already looked at, with the prop-farming criteria. The out-of-sample numbers are not
        altered: the old verdict is kept as `legacy_status` and the review is logged as one more look at the unseen block."""
        d = load_study(self.work, slug)
        if d is None or not d.get("series") or not (d["report"].get("locked") or {}).get("checks"):
            return {"error": "studio non rivedibile"}
        rep, ser = d["report"], d["series"]
        c = self.ctx(self.market_of(rep))
        try:
            fr = farming_for_study(c.bars(), rep, ser, c.contract, self.contracts, farming_context(), forward=None)
        except Exception as e:
            update_report(self.work, slug, lambda r: r["locked"].__setitem__("farming_review_error", f"{type(e).__name__}: {str(e)[:200]}"))
            self.events.emit("lab", "error", f"revisione prop farming {slug}: {e}")
            return {"error": str(e)[:200]}
        def mut(r):
            lk = r["locked"]
            lk.setdefault("legacy_status", lk.get("status"))
            lk["judge_status"] = lk["legacy_status"]
            lk["status"], lk["failed_gate"], lk["suited"] = fr["verdict"], fr["first_failed"], fr["suited"]
            lk["reviewed_with"] = "prop_farming_v1"; lk["reviewed_at"] = time.strftime("%Y-%m-%d %H:%M")
            r["farming"] = fr
        update_report(self.work, slug, mut)
        self.registry.add_locked_look(f"farming_review:{slug}", fr["verdict"], oos_start=str(ser["cut"])[:10])
        self.events.emit("judge", "verdict", f"{rep['name']}: {fr['verdict']} (rivista con il criterio prop farming)", {"slug": slug})
        self.farming_post(slug, discuss=False)
        if fr["verdict"] == "APPROVED":
            self.make_dossier(slug); self.deploy_ready()
        return {"slug": slug, "verdict": fr["verdict"], "was": d["report"]["locked"].get("status")}

    def farming_review_all(self) -> dict:
        """Background: one pending study at a time, so the Lab stays responsive."""
        if getattr(self, "_farm_busy", False):
            return {"started": False, "reason": "già in corso", "pending": len(self.farming_pending())}
        pend = self.farming_pending()
        if not pend:
            return {"started": False, "pending": 0}
        self._farm_busy = True
        def work():
            try:
                with self.working("review"):
                    for slug in pend:
                        while any(j["status"] in ("queued", "running") for j in self.jobs.values()):
                            time.sleep(2.0)                           # a study the owner is waiting for always goes first
                        with self.run_lock:
                            self.farming_review(slug)
            finally:
                self._farm_busy = False
        threading.Thread(target=work, daemon=True).start()
        return {"started": True, "pending": len(pend)}

    def fascicoli(self) -> list[dict]:
        key = (self._stamp(self._fasc_dir()), (self.data / "decisions.json").stat().st_mtime_ns if (self.data / "decisions.json").exists() else 0)
        c = getattr(self, "_fc_cache", None)
        if c and c[0] == key:
            return c[1]
        out = self._fascicoli_uncached()
        self._fc_cache = (key, out)
        return out

    def _fascicoli_uncached(self) -> list[dict]:
        dec, out = self.decisions(), []
        for f in sorted(self._fasc_dir().glob("*.json")):
            try:
                x = json.loads(f.read_text())
            except ValueError:
                continue
            if x.get("stato") != "APPROVED":
                continue
            n = x["risultati"]["npfv"]
            out.append({"slug": x["slug"], "nome": x["nome"], "mercato": x["mercato"], "ruleset": x["ruleset_migliore"], "npfv_lcb95": n.get("lcb95"),
                        "npfv_mean": n.get("mean"), "decisione": (dec.get(x["slug"]) or {}).get("decision"), "creato": x["creato"]})
        return sorted(out, key=lambda r: -(r["npfv_lcb95"] or -1e18))

    def fascicolo(self, slug: str) -> dict | None:
        f = self._fasc_dir() / f"{slugify(slug)}.json"
        if not f.exists():
            return None
        x = json.loads(f.read_text())
        x["decisione"] = self.decisions().get(x["slug"])
        dp = self.deployments.get(x["slug"]) or {}
        fw = (dp.get("forward") or {})
        mind = farming_context()["cfg"]["forward"]["min_days"]
        st = {"live": "PENDING", "confirmed": "PASS", "retired": "FAIL"}.get(dp.get("status"), "PENDING")
        g7 = {"id": 7, "name": "Validazione forward (paper)", "status": st, "reason":
              (f"{fw.get('days', 0)} giorni di paper trading, stato {dp.get('status', 'non avviato')}" +
               ("" if st != "PENDING" else f": servono almeno {mind} giorni") + (f" — {dp['reason']}" if dp.get("reason") else ""))}
        x["barriere"] = [b for b in x["barriere"] if b["id"] != 7] + [g7]
        x["forward"] = {"days": fw.get("days", 0), "status": dp.get("status"), "reason": dp.get("reason", "")}
        return x

    def ranking(self, ruleset_id: str | None = None) -> dict:
        cfgr = farming_context()
        ids = [r.id for r in cfgr["rulesets"]]
        rid = ruleset_id if ruleset_id in ids else (ids[0] if ids else None)
        entries = []
        for r in list_studies(self.work):
            d = load_study(self.work, r["slug"])
            fr = (d or {}).get("report", {}).get("farming")
            if not fr or rid not in (fr.get("by_ruleset") or {}):
                continue
            x = fr["by_ruleset"][rid]
            m = x["evaluation"]["metrics"]
            entries.append({"slug": r["slug"], "name": r["name"], "verdict": x["gates"]["verdict"], "npfv": m["npfv"],
                            "p_first_payout": m["p_first_payout"], "p_pass": m["p_pass"], "p_funded_survival": m["p_funded_survival"],
                            "e_payout_count": m["e_payout_count"], "e_time_to_first_payout": m["e_time_to_first_payout"],
                            "e_total_cost": m["e_total_cost"], "p_account_failure": m["p_account_failure"],
                            "provenance": x["evaluation"]["ruleset"]["provenance"], "first_failed": x["gates"]["first_failed"]})
        out = FM.rank(entries)
        out.update({"ruleset": rid, "rulesets": ids, "evaluated": len(entries), "rejected": [e for e in entries if e["verdict"] != "APPROVED"][:30]})
        return out

    def make_dossier(self, slug: str) -> dict | None:
        d = load_study(self.work, slug)
        if d is None or not d.get("series"):
            return None
        rep = {**d["report"], "series": d["series"]}
        lang = "it" if self.language == "Italian" else "en"
        c = self.ctx(self.market_of(rep))
        dos = build_dossier(rep, {k: PRESETS[k] for k in ("50k", "25k")}, self.contracts, self.registry.n_trials,
                            lang, TEMPLATES[rep["template"]].doc, d.get("auditor"),
                            economics=load_assumptions(self._econ_path()), goal=load_goal(self._econ_path()),
                            market=c.sym, micro=c.contract.symbol, point_value=c.contract.point_value)
        if dos is None:
            return None
        self._add_intraday(dos, rep, c, lang)
        dos["slug"], dos["created"] = slug, time.strftime("%Y-%m-%d %H:%M")
        self.dossier_dir.mkdir(parents=True, exist_ok=True)
        (self.dossier_dir / f"{slug}.json").write_text(json.dumps(dos, indent=1, ensure_ascii=False, default=str))
        self.events.emit("lab", "ready", f"READY: {dos['name']}", {"slug": slug})
        return dos

    def _add_intraday(self, dos: dict, rep: dict, c: "MarketCtx", lang: str) -> None:
        """Replay the unseen block bar by bar at each account's recommended size (unrealised P&L, intraday trailing)."""
        try:
            bp = rep["series"]["best_params"]
            params = {k: v for k, v in bp.items() if k != "tf"}
            b = c.bars() if bp["tf"] == "1min" else resample(c.bars(), bp["tf"])
            pos = flatten_overnight(directional(TEMPLATES[rep["template"]].fn, rep["series"].get("direction"))(b, **params))
            led = run_ledger(b, pos, c.contract, self.contracts, 1.0, rep["series"].get("exit"))
            arr = arrays_from_ledger(b, led, only_from=pd.Timestamp(rep["series"]["cut"]))
            for acct, entry in dos["accounts"].items():
                if entry.get("recommended"):
                    k = entry["recommended"]["micro_equiv"] / self.contracts
                    entry["intraday"] = intraday_report(arr, PRESETS[acct], scale=k)
                    entry["text"] += describe_intraday(entry["intraday"], "intraday", lang)
        except Exception as e:
            dos["intraday_error"] = str(e)[:200]

    @staticmethod
    def _econ_path():
        return next((p for p in (Path(__file__).resolve().parents[3] / "rules" / "economics.json", Path("rules/economics.json"))
                     if p.exists()), None)

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
            mem = knowledge.memory_text(self.knowledge_graph())
        except Exception:
            mem = ""
        return (self.directives.prompt_text() + "\n" + mem).strip()

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
            c = self.ctx(d.get("market"))
            c.bars()
            last = str(c.sessions[-1])[:10]
            item = dep.make_deployment(d, last, time.strftime("%Y-%m-%d %H:%M")) if d.get("fast_ok", True) else None
            if item is None and not d.get("fast_ok", True):
                item = {"slug": d["slug"], "name": d["name"], "status": "not_deployed", "forward": {"days": 0},
                        "reason": f"approved but too slow for the {d.get('goal', {}).get('max_eval_days', 5)}-day goal"}
                self.events.emit("trader", "skipped", f"{d['name']}: too slow for the goal, not deployed")
            elif item is None:
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
            for x in live:
                try:
                    c = self.ctx(x.get("market"))
                    bars = c.bars()
                    log = dep.SignalLog(self.data / "signals" / f"{x['slug']}.jsonl")      # append-only, hash-chained
                    before = len(log.records)
                    r = dep.forward_update(bars, x, c.contract, self.contracts, self.rules_for(x["account"]), log)
                    x["forward"] = r["forward"]
                    for rec in log.records[before:]:
                        if rec["kind"] == "exit":
                            self.events.emit("trader", "signal", f"{x['name']}: exit {rec['exit_px']:.2f} ({rec['pnl']:+.0f}$)")
                        elif rec["kind"] == "entry":
                            self.events.emit("trader", "signal", f"{x['name']}: {'LONG' if rec['side'] > 0 else 'SHORT'} at {rec['entry_px']:.2f}")
                        elif rec["kind"] == "restatement":
                            self.events.emit("trader", "restatement", f"{x['name']}: recomputed P&L differs from the recorded one")
                    x["n_signals"] = len(log.records)
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
        info = {"ok": True}
        for sym, c in self.mk.items():                       # the day files hold every product: one download, one rebuild per market
            r = dep.refresh_data(self.data, c.path, sym)
            if r.get("rebuilt"):
                with self.lock:                               # drop the cached frame: the next read gets the new days
                    c.reset(); self._trades_cache.clear(); self._ind_cache.clear()
                c.bars()
            if sym == self.primary:
                info = r
            elif not r.get("ok", True):
                info = {**info, "ok": False, "why": f"{sym}: {r.get('why')}"}
        return info

    def deployments_view(self) -> dict:
        return {"items": self.deployments.d["items"], "data": self.deployments.d.get("data", {}),
                "last_bar": str(self.bars().index[-1])[:10] if Path(self.bars_path).exists() else None,
                "markets": list(self.mk)}

    def recover_specs(self) -> int:
        """Proposals the Researcher wrote before a restart but that were never tested come back to the queue."""
        n = 0
        for f in sorted((self.work / "specs_auto").glob("*.json")) if (self.work / "specs_auto").exists() else []:
            try:
                for d in json.loads(f.read_text()).get("strategies", []):
                    n += self._add_pending_all(from_dict(d), "paper")
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
                            if self.busy.get("research", 0) == 0:
                                self.farming_review_all()
                            self.obsidian_sync()
                            if (self.obs["vault"] or self.ap["enabled"]) and self.busy["research"] == 0 and self.llm_allowed("researcher") and self._unread_ideas():
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
            pending = [spec_summary(i, v["spec"], v["origin"], self.registry.n_trials, v.get("market")) for i, v in self.pending.items()]
        reqs = json.loads(self.requests_file.read_text()) if self.requests_file.exists() else []
        return {"trials": self.registry.n_trials, "locked_looks": self.registry.data["locked_looks"],
                "calls_last_hour": self.pool.calls_last_hour() if self.pool else None,
                "calls_per_hour": self.pool.calls_per_hour if self.pool else None,
                "calls_last_minute": self.pool.calls_last_minute() if self.pool else None,
                "calls_per_minute": self.pool.calls_per_minute if self.pool else None,
                "ideas": ideas, "pending": pending, "jobs": list(self.jobs.values())[-10:],
                "strategies": list_studies(self.work), "template_requests": reqs,
                "templates": {k: t.doc for k, t in TEMPLATES.items() if not t.control}, "symbol": self.contract.symbol,
                "primary": self.primary, "markets": [{"sym": k, "name": MARKETS[k]["name"], "micro": c.contract.symbol} for k, c in self.mk.items()],
                "engine_busy_with": ("revisione prop farming" if getattr(self, "_farm_busy", False) else None),
                "ai": not isinstance(self.llm, NoLLM), "scout": self.scout_state(),
                "autopilot": self.autopilot_state(), "budget": self.budget_state(), "obsidian": self.obsidian_state(),
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
            c = self.ctx(self.market_of(rep))
            self._trades_cache[key] = extract_trades(c.bars(), TEMPLATES[rep["template"]].fn, params, tf,
                                                     c.contract, self.contracts, exit=ser.get("exit"),
                                                     direction=ser.get("direction") or "both")
        allt = self._trades_cache[key]
        looked = "checks" in rep["locked"]
        vis = allt if looked else [t for t in allt if t["session"] < ser["cut"]]
        tf = ser["best_params"]["tf"]
        for x in vis:                                    # R multiple: result over the initial risk (entry-stop distance)
            risk = abs(x["entry_px"] - x["stop"]) if x.get("stop") else 0
            x["r"] = round(x["side"] * (x["exit_px"] - x["entry_px"]) / risk, 2) if risk else None
        return {"trades": vis, "n_hidden": len(allt) - len(vis), "locked_visible": looked, "tf": tf,
                "params": {k: v for k, v in ser["best_params"].items() if k != "tf"}, "cut": ser["cut"], "exit": ser.get("exit"),
                "summary": summarize(vis), "name": rep["name"], "market": self.market_of(rep),
                "base_contracts": rep.get("base_contracts", self.contracts), "direction": ser.get("direction") or "both",
                "sizing": {a: v.get("recommended") for a, v in ((rep.get("locked") or {}).get("sizing") or rep.get("sizing") or {}).get("accounts", {}).items()}}

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
            mb = self.ctx(self.market_of(rep)).bars()
            b = mb if tf == "1min" else resample(mb, tf)
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

    def save_idea(self, name: str, data: bytes, for_id: str | None = None) -> dict:
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
        if for_id:                                           # the PDF the Researcher asked for: keep its source and mark it received
            rec = self.scout().state["papers"].get(for_id) or {}
            dest.with_suffix(".meta.json").write_text(json.dumps({"title": rec.get("title", dest.stem), "url": rec.get("url", ""),
                                                                  "source": "ssrn (provided by the owner)"}, ensure_ascii=False))
            if self.scout().provide(for_id, dest.name):
                self.events.emit("researcher", "message", f"PDF ricevuto: {rec.get('title', dest.name)[:90]}. Lo leggo.")
        return {"saved": dest.name}
