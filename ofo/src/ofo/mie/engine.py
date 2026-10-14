"""The Market Intelligence Engine loop: observe -> structure -> behavior -> memory -> hypotheses -> director -> existing pipeline.

Every step writes what it did to the event log under the agent that did it (market_observer, structure_engine, behavior_analyzer, obsidian_memory, researcher,
research_director): the messages are emitted by the operations themselves, never scripted. Every study and every definition variant is counted in the trial registry
(kind 'mie'): exploration is multiple testing, and the false-discovery control runs over ALL the studies of a data version, not just the promising ones."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from . import behavior as BH
from . import hypotheses as HY
from . import observer as OB
from . import questions as Q
from . import structure as ST
from .director import Director
from .guard import DevView
from .memory import Memory
from .schema import fingerprint
from .sessions import data_quality


class Engine:
    def __init__(self, memory: Memory, state_dir: str | Path, cfg: dict, registry=None, events=None, submit=None, market: str = "NQ"):
        self.mem, self.cfg, self.reg, self.ev, self.submit, self.market = memory, cfg, registry, events, submit, market
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.director = Director(self.dir / "missions.json", cfg)
        self.studies_path = self.dir / "studies.json"
        self.studies: dict = json.loads(self.studies_path.read_text(encoding="utf-8", errors="ofo_legacy")) if self.studies_path.exists() else {}
        self.hyp_path = self.dir / "hypotheses.json"
        self.hyps: dict = json.loads(self.hyp_path.read_text(encoding="utf-8", errors="ofo_legacy")) if self.hyp_path.exists() else {}
        self.counters = {"duplicates_hypotheses": 0}
        self.questions = None                                   # None = the standard catalogue (tests and focused cycles may narrow it)
        self._cache: dict = {}

    # ------------------------------------------------------------------ helpers
    def say(self, agent: str, type_: str, msg: str, data: dict | None = None) -> None:
        if self.ev:
            self.ev.emit(agent, type_, msg, data)

    def _save(self) -> None:
        self.studies_path.write_text(json.dumps(self.studies, ensure_ascii=False, default=str), encoding="utf-8")
        self.hyp_path.write_text(json.dumps(self.hyps, ensure_ascii=False, default=str), encoding="utf-8")

    def prepare(self, view: DevView) -> dict:
        """Session context, regimes and data quality for ONE data version (cached): everything downstream starts from these."""
        if view.data_version in self._cache:
            return self._cache[view.data_version]
        bars = view.development
        self.say("market_observer", "working", f"audit dei dati {self.market}: {len(bars):,} barre a 1 minuto fino a {str(bars.index.max())[:10]}")
        ctx = OB.session_context(bars, self.cfg)
        dq = data_quality(bars, self.cfg)
        self.say("market_observer", "done", f"{dq['sessions']} sessioni, {dq['missing_minutes_total']} minuti mancanti (non riempiti), {len(dq['short_sessions'])} sessioni corte")
        self.say("structure_engine", "working", "classifico i regimi di mercato su barre a 5 minuti (solo informazioni passate)")
        reg = ST.regime_series(OB.derive(bars, "5min"), self.cfg)
        share = reg["labels"].str.split("|").explode().value_counts(normalize=True).round(3).to_dict()
        self.say("structure_engine", "done", f"regimi: {', '.join(f'{k} {v:.0%}' for k, v in sorted(share.items(), key=lambda kv: -kv[1])[:4])}")
        out = {"ctx": ctx, "dq": dq, "regime": reg, "regime_share": share}
        self._cache = {view.data_version: out}
        return out

    def baseline_notes(self, view: DevView, prep: dict) -> None:
        """Descriptive notes (OBSERVED): data audit, regime shares, the two descriptive questions. Idempotent per data version."""
        dv, bars, ctx = view.data_version, view.development, prep["ctx"]
        dq = prep["dq"]
        body = (f"Audit dei dati {self.market} (solo SVILUPPO, il blocco hidden non è accessibile).\n\n- sessioni: {dq['sessions']}\n- barre: {dq['bars']:,}\n- minuti mancanti (non riempiti): {dq['missing_minutes_total']}\n"
                f"- sessioni corte/festive rilevate dai dati: {', '.join(dq['short_sessions'][:10]) or 'nessuna'}\n- periodo: {dq['first'][:10]} → {dq['last'][:10]}\n- timestamp duplicati: {dq['duplicate_timestamps']}\n"
                "- dati di order flow (delta, CVD, footprint, volume profile): NON DISPONIBILI e non dedotti dalle candele.")
        self.mem.write("observation", f"Audit dati {self.market}", body, market=self.market, evidence="OBSERVED", note_class="empirical_observation", access="dev", definition="data_audit",
                       data_version=dv, period=(dq["first"][:10], dq["last"][:10]), config_version=self.cfg["config_version"])
        rs = prep["regime_share"]
        self.mem.write("observation", f"Regimi {self.market}", "Quota di barre a 5 minuti per etichetta di regime (multilabel; UNCERTAIN dichiarato quando mancano informazioni):\n\n" +
                       "\n".join(f"- {k}: {v:.1%}" for k, v in sorted(rs.items(), key=lambda kv: -kv[1])), market=self.market, evidence="OBSERVED", note_class="empirical_observation",
                       definition="regime_share", data_version=dv, config_version=self.cfg["config_version"])
        for desc in (Q.range_return_rate(bars, ctx), Q.day_types(bars, ctx, self.cfg["regime"]["er_trend"])):
            self.mem.write("observation", f"{desc['event']} {self.market}", json.dumps(desc, ensure_ascii=False, indent=1), market=self.market, evidence="OBSERVED",
                           note_class="empirical_observation", definition=desc["event"], data_version=dv, config_version=self.cfg["config_version"])
        self.say("obsidian_memory", "done", f"scritte le osservazioni di base ({self.mem.state['written']} note totali)")

    # ------------------------------------------------------------------ studies
    def run_question(self, view: DevView, prep: dict, q: dict) -> dict:
        dv, bars, ctx = view.data_version, view.development, prep["ctx"]
        d = q["definition"]
        self.say("behavior_analyzer", "working", f"studio: {q['text']}")
        events = Q.events_for(q, bars, ctx, self.cfg)
        variants = {}
        if d["event"] == "liquidity":
            for rb in sorted({max(2, (d["reclaim_bars"] or 6) - 2), (d["reclaim_bars"] or 6) + 2}):
                dd = {**d, "reclaim_bars": rb}; dd["id"] = fingerprint({k: v for k, v in dd.items() if k != "id"})
                ev2 = Q.liquidity_events_table(bars, ctx, self.cfg, dd)
                if len(ev2) >= 5:
                    variants[f"reclaim_bars={rb}"] = BH.study(ev2, bars, self.cfg, f"{q['text']} [{rb}]", dd, claim=q["claim"])
        res = BH.study(events, bars, self.cfg, q["text"], d, claim=q["claim"], regimes=prep["regime"], variants=variants)
        res.update(data_version=dv, family=q["family"], status_raw=res["status"], market=self.market, studied_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        if self.reg is not None:
            self.reg.add_trials(1 + len(variants), f"mie:{d['id']}", kind="mie", stage="info", market=self.market, details={"question": q["text"], "variants": len(variants)})
        self.studies.setdefault(dv, {})[d["id"]] = res
        self.say("behavior_analyzer", "done", f"{res['n_events']} eventi in {res['n_sessions']} sessioni → {res['status']}")
        return res

    def apply_multiplicity(self, dv: str) -> None:
        """BH over ALL studies of this data version; statuses are recomputed from the raw ones, so the control never depends on the order of arrival."""
        arr = list(self.studies.get(dv, {}).values())
        for s in arr:
            s["status"] = s["status_raw"]
            s.pop("q_value", None)
        BH.apply_fdr(arr, float(self.cfg["behavior"]["fdr_q"]))

    def note_study(self, res: dict, q: dict) -> dict:
        d = res["definition"]
        st = res["status"]
        cls = "validated_conclusion" if st == "SUPPORTED" else "negative_result" if st == "CONTRADICTED" else "empirical_observation"
        dist, base = res.get("distribution") or {}, res.get("baseline") or {}
        body = [f"# {q['text']}", "", f"Stato dell'evidenza: **{st}** (su dati di SVILUPPO; non dimostrato).", "", f"- eventi: {res['n_events']} in {res['n_sessions']} sessioni (orizzonte {res['horizon_min']} min)"]
        if dist:
            body += [f"- esito netto medio: {dist['mean_net']:+.3f} ATR (IC95 {dist['mean_ci95'][0]:+.3f}, {dist['mean_ci95'][1]:+.3f}); mediana {dist['median_net']:+.3f}; win rate {dist['win_rate']:.1%} "
                     f"(IC95 {dist['win_rate_ci95'][0]:.1%}-{dist['win_rate_ci95'][1]:.1%})", f"- asimmetria {dist['skew']:+.2f}, curtosi {dist['kurtosis']:+.2f}, MFE mediano {dist['mfe_median']:.2f}, MAE mediano {dist['mae_median']:.2f}"]
        if base:
            body += [f"- baseline stesso orario: {base['mean_net']:+.3f} ATR; differenza {base['diff']:+.3f} (IC95 {base['diff_ci95'][0]:+.3f}, {base['diff_ci95'][1]:+.3f}), p={base['p_value']:.3f}"
                     + (f", q={res['q_value']:.3f} su {res['tests_in_batch']} studi" if "q_value" in res else "")]
        if res.get("concentration"):
            c = res["concentration"]; body.append(f"- concentrazione: top 10% degli eventi = {(c['top10pct_share_of_gains'] or 0):.0%} dei guadagni; stabilità: metà concordi={res['stability']['halves_agree_in_sign']}")
        if res.get("cost_sensitivity"):
            body.append(f"- costi: x0 {res['cost_sensitivity']['x0']:+.3f}, x1 {res['cost_sensitivity']['x1']:+.3f}, x3 {res['cost_sensitivity']['x3']:+.3f}; pareggio ×{res['cost_sensitivity']['breakeven_cost_multiple']}")
        if res.get("by_regime"):
            body.append("- per regime: " + ", ".join(f"{k} n={v['n']} {v['mean_net']:+.3f}" for k, v in res["by_regime"].items()))
        if res.get("definition_sensitivity"):
            body.append(f"- sensibilità alla definizione: {res['definition_sensitivity']['min']:+.3f} … {res['definition_sensitivity']['max']:+.3f}")
        body += ["", "## Limiti dell'evidenza"] + [f"- {x}" for x in res["limits"]]
        kind = "negative_finding" if st == "CONTRADICTED" else "behavior_study"
        return self.mem.write(kind, q["text"][:70], "\n".join(body), market=self.market, evidence=st, note_class=cls, access="dev", definition=d["id"], data_version=res["data_version"],
                              family=q["family"], config_version=self.cfg["config_version"],
                              extra={"n_events": res["n_events"], "q_value": res.get("q_value"), "level": d.get("level"), "trade": d.get("trade"), "outcome": d.get("outcome")})

    # ------------------------------------------------------------------ one tick
    def step(self, view: DevView, max_missions: int | None = None) -> dict:
        """One increment: prepare, baseline notes, plan missions from gaps, run the allowed ones, hypotheses, conflicts, submission. Returns what actually happened."""
        out = {"missions_run": 0, "studies": 0, "hypotheses": 0, "duplicates": 0, "submitted": 0, "conflicts": 0}
        prep = self.prepare(view)
        self.baseline_notes(view, prep)
        dv = view.data_version
        qs = self.questions or Q.standard_questions(self.cfg)
        studied = {k: {"status": v["status"], "n_events": v["n_events"]} for k, v in self.studies.get(dv, {}).items()}
        conflicts = self.mem.conflicts()
        hyp_list = [h for h in self.hyps.values() if not h.get("sent")]
        new = self.director.plan(questions=qs, studied=studied, conflicts=conflicts, hypotheses=hyp_list, market=self.market, data_version=dv, min_events=int(self.cfg["behavior"]["min_events"]))
        if new:
            self.say("research_director", "done", f"{len(new)} nuove missioni da lacune di conoscenza (aperte: {self.director.summary()['by_status'].get('open', 0)})")
        todo = self.director.next(max_missions or 3)
        by_def = {q["definition"]["id"]: q for q in qs}
        for m in todo:
            self.director.start(m["id"])
            self.say("research_director", "working", f"missione {m['id']}: {m['question'][:120]}")
            if m["type"] in ("study_question", "sensitivity", "refresh_stale"):
                q = by_def.get(m["key"].get("def"))
                if q is None:
                    self.director.finish(m["id"], {"error": "definizione non più nel catalogo"}, "blocked"); continue
                res = self.run_question(view, prep, q)
                self.apply_multiplicity(dv)
                self.note_study(res, q)
                out["studies"] += 1
                self.director.finish(m["id"], {"status": res["status"], "n_events": res["n_events"]})
            elif m["type"] == "resolve_conflict":
                self.mem.record_conflicts()
                self.say("obsidian_memory", "done", f"conflitto registrato come domanda aperta: {m['question'][:100]}")
                self.director.finish(m["id"], {"recorded": True})
            elif m["type"] == "screen_hypothesis":
                h = self.hyps.get(m["key"]["h"])
                if h and not h.get("sent") and self.submit and h.get("spec"):
                    out["submitted"] += self._submit(h)
                self.director.finish(m["id"], {"submitted": bool(h and h.get("sent"))})
            out["missions_run"] += 1
        # statuses may have changed with the multiplicity control: rewrite every note of this data version (same ids, idempotent)
        for did, s in self.studies.get(dv, {}).items():
            q = by_def.get(did)
            if q:
                self.note_study(s, q)
        h_new, dups = self.derive_hypotheses(dv, by_def)
        out["hypotheses"], out["duplicates"] = h_new, dups
        out["conflicts"] = len(self.mem.record_conflicts())
        self._save()
        return out

    def derive_hypotheses(self, dv: str, by_def: dict) -> tuple[int, int]:
        fronts = [n["front"] for n in self.mem.index() if n["access"] in ("dev", "owner") and n["front"].get("ofo_kind") in ("hypothesis", "behavior_study", "negative_finding", "mission")
                  and not n.get("quarantined")]
        created = dups = 0
        for did, s in self.studies.get(dv, {}).items():
            q = by_def.get(did)
            if not q or did in {h["definition"] for h in self.hyps.values() if h.get("data_version") == dv}:
                continue
            study_note_id = fingerprint({"k": "negative_finding" if s["status"] == "CONTRADICTED" else "behavior_study", "d": did, "v": dv, "m": self.market})[:12]
            h = HY.make_hypothesis(s, q, [study_note_id], [f for f in fronts if f.get("ofo_definition") != did or f.get("ofo_kind") != "behavior_study"], self.market)
            if h is None:
                continue
            if h.get("duplicate"):
                dups += 1
                self.counters["duplicates_hypotheses"] += 1
                continue
            h["data_version"] = dv
            self.hyps[h["id"]] = h
            self.mem.write("hypothesis", f"{h['id']} {q['text'][:50]}", self._hyp_body(h), market=self.market, evidence="HYPOTHESIZED", note_class="untested_hypothesis", access="dev",
                           definition=did, data_version=dv, family=q["family"], hypothesis_id=h["id"], source_study=study_note_id, links=[], config_version=self.cfg["config_version"],
                           extra={"level": s["definition"].get("level"), "trade": s["definition"].get("trade"), "priority": h["priority"], "pipeline": h["pipeline"]})
            self.say("researcher", "proposal", f"💡 {h['id']}: {q['text'][:90]} (priorità {h['priority']}, novità {h['novelty']['score']})", {"hypothesis": h["id"], "family": q["family"]})
            created += 1
        return created, dups

    @staticmethod
    def _hyp_body(h: dict) -> str:
        c = h["card"]
        return "\n".join([f"# {h['id']} — {h['family']}", "", f"Mercato: {h['market']} · priorità {h['priority']} · novità {h['novelty']['score']} ({h['novelty']['reason']})", "",
                          f"## Meccanismo economico\n{h['mechanism']}", "", f"## Previsione verificabile\n{h['testable_prediction']}", "", f"## Criterio di falsificazione\n{h['falsification']}", "",
                          f"## Costi attesi\n{h['expected_costs']}", f"\n## Osservazioni di origine\n" + "\n".join(f"- {x}" for x in h["source_observations"]),
                          "\n## Riferimenti in memoria\n" + "\n".join(f"- [[{x}]]" for x in h["obsidian_refs"]), f"\n## Scheda ipotesi\n" + "\n".join(f"- **{k}**: {v}" for k, v in c.items()),
                          f"\nPercorso: {'specifica per la pipeline esistente' if h['pipeline'] == 'spec' else 'solo domanda di ricerca (nessun template)'}"])

    def _submit(self, h: dict) -> int:
        spec = {"name": f"MIE {h['id']} {h['family']}", "template": h["spec"]["template"], "ranges": h["spec"]["ranges"], "timeframes": ["5min", "15min"],
                "rationale": f"{h['mechanism']} (da {h['id']}; osservazioni: {'; '.join(h['source_observations'])}; note: {', '.join(h['obsidian_refs'])})", "source": f"MIE {h['id']}", "hypothesis": h["card"]}
        n = self.submit(spec, h)
        h["sent"] = time.strftime("%Y-%m-%d %H:%M:%S"); h["queued"] = n
        self.say("research_director", "done", f"{h['id']} inviata alla pipeline ({n} in coda)" if n else f"{h['id']}: la pipeline l'ha scartata come duplicata")
        return n
