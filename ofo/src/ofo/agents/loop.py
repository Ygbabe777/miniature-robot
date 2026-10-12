"""The continuous research loop: ideas in, verified verdicts out, every step logged.

  ideas/ (PDF, .txt, .md)  -> Researcher agent -> specs -> deterministic study
  -> (if the surface survives) Planner agent: abandon / extend / new hypothesis
  -> Auditor agent writes the commentary -> reports/<name>.json  + events.jsonl for the UI.

The verdict always comes from code (judge.py). Agents only propose and explain."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path

from ..optimizer import run_study
from ..papers import extract_pdf_text, propose_specs
from ..specs import StrategySpec, from_dict
from ..storage import save_study
from ..llm.client import LLMError
from .base import AgentError
from .roles import dev_summary, make_auditor, make_planner

IDEA_SUFFIXES = {".pdf", ".txt", ".md"}


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name)[:60]


def _fingerprint(spec: StrategySpec) -> str:
    return json.dumps([spec.template, spec.ranges, spec.timeframes], sort_keys=True)


def _llm(llm, role: str):
    if not isinstance(llm, dict):
        return llm
    return llm[role] if role in llm else llm["researcher"]      # roles missing from the config share the researcher's models


class ResearchLoop:
    @property
    def bars(self):
        """A DataFrame, or a function returning one: reading papers must not load 700k rows of market data."""
        return self._bars() if callable(self._bars) else self._bars

    def __init__(self, bars, contract, rules, registry, llm, events, workdir, contracts: int = 2,
                 max_cycle_trials: int = 3000, max_depth: int = 2, language: str = "Italian"):
        self._bars, self.contract, self.rules, self.registry = bars, contract, rules, registry
        self.llm, self.events, self.contracts = llm, events, contracts   # one LLM, or {role: LLM}
        self.max_cycle_trials, self.max_depth, self.language = max_cycle_trials, max_depth, language
        self.work = Path(workdir)
        self.work.mkdir(parents=True, exist_ok=True)
        self.state_file = self.work / "loop_state.json"
        st = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.processed_ideas: set = set(st.get("processed_ideas", []))
        self.done_specs: set = set(st.get("done_specs", []))
        self.queue: list[tuple[StrategySpec, int]] = []
        self.trials_spent = 0

    def _save(self) -> None:
        self.state_file.write_text(json.dumps({"processed_ideas": sorted(self.processed_ideas),
                                               "done_specs": sorted(self.done_specs)}))

    def enqueue(self, spec: StrategySpec, depth: int = 0) -> bool:
        fp = _fingerprint(spec)
        if fp in self.done_specs or any(_fingerprint(s) == fp for s, _ in self.queue):
            return False
        self.queue.append((spec, depth))
        return True

    def ingest_ideas(self, ideas_dir: str | Path) -> int:
        """Run the Researcher on every new file in ideas_dir. Returns specs enqueued."""
        added = 0
        d = Path(ideas_dir)
        for p in sorted(d.rglob("*")) if d.exists() else []:
            if not p.is_file() or p.suffix.lower() not in IDEA_SUFFIXES:
                continue
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            if h in self.processed_ideas:
                continue
            source = p.name
            meta = p.with_suffix(".meta.json")
            if meta.exists():
                try:
                    m = json.loads(meta.read_text()); source = f"{m.get('title', p.name)} ({m.get('url', '')})"
                except ValueError:
                    pass
            self.events.emit("researcher", "reading", f"reading {p.name}")
            self.events.emit("researcher", "working", f"reading {p.name}")
            try:
                text = extract_pdf_text(str(p)) if p.suffix.lower() == ".pdf" else p.read_text(errors="replace")
                specs, rejected = propose_specs(_llm(self.llm, "researcher"), text, source=source, language=self.language)
            except Exception as e:                       # unreadable file or LLM down: retry later
                self.events.emit("researcher", "error", f"{p.name}: {e}")
                self.events.emit("researcher", "idle", "stopped")
                continue
            self.events.emit("researcher", "done", f"{p.name}: {len(specs)} proposals")
            self.processed_ideas.add(h)
            (self.work / "specs_auto").mkdir(exist_ok=True)
            (self.work / "specs_auto" / f"{_slug(p.stem)}.json").write_text(json.dumps(
                {"strategies": [dataclasses.asdict(s) for s in specs], "rejected": rejected}, indent=1))
            for s in specs:
                if self.enqueue(s):
                    added += 1
                    self.events.emit("researcher", "proposal", f"{s.name}: {s.template}, {s.n_candidates} candidates",
                                     {"rationale": s.rationale, "source": s.source})
            for r in rejected:
                self.events.emit("researcher", "rejected", r["reason"][:200])
            self._save()
        return added

    def process(self, spec: StrategySpec, depth: int = 0) -> dict:
        ev = self.events
        ev.emit("optimizer", "study_started", f"{spec.name}: {spec.n_candidates} candidates")
        rep = run_study(self.bars, spec, self.contract, self.rules, self.registry, contracts=self.contracts,
                        progress=lambda agent, state, msg='': ev.emit(agent, state, msg))
        self.trials_spent += rep["n_candidates"]
        self.done_specs.add(_fingerprint(spec))
        s = rep["surface"]
        ev.emit("validator", "surface", f"{spec.name}: median Sharpe {s['median_sharpe']:+.3f}, "
                f"{s['frac_positive']:.0%} positive, plateau {s['plateau_ratio']:.2f}, robust={s['robust']}",
                {"checks": s["checks"]})
        gate = (s["robust"] and rep["wf_oos_pnl"] > 0 and (rep.get("surface_detrended") or {}).get("robust"))
        decision = {"decision": "abandon", "reason": "failed a deterministic gate (surface, walk-forward or beta)", "spec": None}
        if gate:
            left = self.max_cycle_trials - self.trials_spent
            try:
                decision = make_planner(_llm(self.llm, "planner"), spec, left, depth, ev, self.language).run(
                    dev_summary(rep, {"trials_total": self.registry.n_trials, "trials_left": left}, depth))
            except (AgentError, LLMError) as e:       # an AI outage must never lose a finished study
                decision["reason"] = f"planner failed: {e}"
            ev.emit("planner", decision["decision"], decision["reason"][:300])
        locked = rep["locked"]
        ev.emit("judge", "verdict", f"{spec.name}: {locked['status']}", {k: v for k, v in locked.items() if k != "checks"})
        audit = None
        if "checks" in locked or s["robust"]:
            payload = {k: v for k, v in rep.items() if k not in ("wf_oos_series", "series")}
            payload["trials_total"] = self.registry.n_trials
            try:
                audit = make_auditor(_llm(self.llm, "auditor"), ev, self.language).run(payload)
                ev.emit("auditor", "commentary", audit["summary"][:300], {"risk_flags": audit["risk_flags"]})
            except (AgentError, LLMError) as e:
                ev.emit("auditor", "error", str(e)[:300])
        dec = {**decision, "spec": dataclasses.asdict(decision["spec"]) if decision["spec"] else None}
        save_study(self.work, rep, dec, audit)
        nxt = decision["spec"]
        if nxt is not None and depth < self.max_depth and self.enqueue(nxt, depth + 1):
            ev.emit("planner", "queued", f"next: {nxt.name} ({nxt.n_candidates} candidates)")
        self._save()
        return {"spec": spec.name, "verdict": locked["status"], "decision": decision["decision"], "audit": audit}

    def run_once(self, ideas_dir: str | Path | None = None) -> list[dict]:
        self.trials_spent = 0
        if ideas_dir:
            self.ingest_ideas(ideas_dir)
        results = []
        while self.queue:
            spec, depth = self.queue.pop(0)
            if self.trials_spent + spec.n_candidates > self.max_cycle_trials:
                self.events.emit("optimizer", "budget", f"trial budget reached, {spec.name} postponed")
                self.queue.insert(0, (spec, depth))
                break
            results.append(self.process(spec, depth))
        return results

    def run_forever(self, ideas_dir, poll_seconds: int = 600, sleep=time.sleep, stop=lambda: False) -> None:
        while not stop():
            self.run_once(ideas_dir)
            sleep(poll_seconds)
