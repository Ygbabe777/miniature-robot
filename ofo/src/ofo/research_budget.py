"""Research Budget Manager: how the lab spends its research budget, per stage and per family, from the registry itself.

Units. A *trial* is a distinct configuration whose development performance was evaluated and could influence a selection
(a parameter set on a timeframe, an exit cell, a direction variant): those feed the Deflated Sharpe. An *eval* is any unit of
compute a stage consumed (trials plus robustness re-runs, walk-forward windows, stress tests). Both are stored with the experiment
that produced them, together with stage, family, hypothesis, code/config version and seconds, so the budget can never disagree with
the trial count behind the statistics. Related tests are NOT independent evidence: they are all counted, and the report also shows
the effective number of independent trials estimated from the correlation of the candidates (informational; the Judge uses the raw count).

Caps (rules/research.json) are per DAY and per STAGE, maxima rather than quotas: the manager never spends to reach them and never
lowers a standard to fill them. They are checked BEFORE every study with worst-case planned units; an overshoot caused by unplanned
work is recorded and blocks the next start. Raising a cap is an explicit decision (`allow_overage`), logged. The total trial budget
(autopilot max_trials_total) is untouched.

Allocation across families is a shrunk estimate, not a leaderboard: one extreme result barely moves it (strong prior), unexplored
families get a novelty bonus, a family can never take more than max_family_share of the day, and a family with less than
min_family_share is served first."""
from __future__ import annotations

import time

from . import research_config as rc

STAGE_OF_KIND = {"study": "screening", "exits": "validation", "direction": "validation", "trials": "screening", "compute": "robustness"}


def planned_units(spec, cfg: dict) -> dict:
    """Worst-case units per stage for one study of `spec` (robustness is an expectation: only a promising surface gets re-run)."""
    ntf = len(spec.timeframes)
    scr = spec.n_candidates
    val = 28 + 2 * ntf + int(cfg["walk_forward"]["min_windows"])
    rob = int(round(0.25 * scr)) + 5
    return {"hypothesis": 0, "screening": scr, "validation": val, "robustness": rob, "audit": 2}


class ResearchBudget:
    def __init__(self, registry, cfg: dict | None = None, now=time.time):
        self.reg, self.cfg, self.now = registry, cfg or rc.get(), now

    def day(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.now()))

    def caps(self) -> dict:
        return rc.stage_caps(self.cfg)

    def used(self, day: str | None = None) -> dict:
        u = self.reg.usage(day or self.day(), "stage")
        return {s: int(u.get(s, {}).get("evals", 0)) for s in rc.STAGES}

    def overage_allowed(self) -> int:
        return self.reg.counter("overage:" + self.day())

    def allow_overage(self, units: int, reason: str = "") -> int:
        """An EXPLICIT decision to exceed today's caps by `units` evals (screening cap included). Logged in the registry counters."""
        self.reg.bump("overage:" + self.day(), int(units))
        self.reg.bump("overage_log", 1)
        return self.overage_allowed()

    def can_start(self, spec) -> tuple[bool, str | None, dict]:
        caps, used, plan = self.caps(), self.used(), planned_units(spec, self.cfg)
        extra = self.overage_allowed()
        detail = {"caps": caps, "used": used, "planned": plan, "overage_allowed": extra}
        for s in ("screening", "validation", "robustness", "audit"):
            if used[s] + plan[s] > caps[s] + extra:
                return False, s, detail
        if self.reg.deep_candidates_on(self.day()) >= caps["deep_candidates"] + extra // 10:
            return False, "deep_candidates", detail
        return True, None, detail

    # ---------------------------------------------------------------- family allocation
    def family_stats(self) -> dict:
        a = self.cfg["allocation"]
        S, p0 = float(a["prior_strength"]), float(a["prior_rate"])
        stats: dict = {}
        for r in self.reg.study_rows():
            f = r.get("family") or "?"
            st = stats.setdefault(f, {"studies": 0, "survivors": 0, "validated": 0, "approved": 0, "false_positives": 0, "compute_s": 0.0, "trials": 0})
            if r["status"] not in ("completed", "early_stopped"):
                continue
            res = r.get("results") or {}
            st["studies"] += 1; st["trials"] += int(r.get("n_trials") or 0)
            st["survivors"] += bool(res.get("survived_screening")); st["validated"] += bool(res.get("validated"))
            st["approved"] += res.get("locked_status") == "APPROVED"; st["false_positives"] += bool(res.get("false_positive"))
            st["compute_s"] += float(r.get("compute_s") or 0.0)
        for f, st in stats.items():
            st["rate"] = (S * p0 + st["survivors"]) / (S + st["studies"])              # shrunk survival rate: one lucky study barely moves it
            st["mean_s"] = st["compute_s"] / st["studies"] if st["studies"] else 0.0
        return stats

    def shares_today(self) -> dict:
        u = self.reg.usage(self.day(), "family")
        tot = sum(v["evals"] for v in u.values()) or 0
        return {f: (v["evals"] / tot if tot else 0.0) for f, v in u.items()}

    def rank(self, items: list) -> list:
        """Order queue items (each with a `spec`) by family priority. Pure and deterministic."""
        a = self.cfg["allocation"]
        stats, shares = self.family_stats(), self.shares_today()
        times = sorted(s["mean_s"] for s in stats.values() if s["mean_s"] > 0)
        med = times[len(times) // 2] if times else 1.0
        base = {f: s["rate"] for f, s in stats.items()}
        prior = float(a["prior_rate"])

        def score(item) -> float:
            f = item["spec"].family
            st = stats.get(f)
            rate = st["rate"] if st else prior
            novelty = 1.0 / (1 + (st["studies"] if st else 0))
            cost = max(0.2, (st["mean_s"] / med) if (st and st["mean_s"] > 0) else 1.0)
            return rate * (1 + float(a["novelty_weight"]) * novelty) / cost

        def tier(item) -> int:
            sh = shares.get(item["spec"].family, 0.0)
            if sh >= float(a["max_family_share"]) and len(items) > 1:
                return 2                                          # already took its share today
            return 0 if (sh < float(a["min_family_share"]) and shares) else 1
        return sorted(items, key=lambda it: (tier(it), -score(it)))

    # ---------------------------------------------------------------- reporting
    def state(self) -> dict:
        caps, used = self.caps(), self.used()
        return {"day": self.day(), "config_version": self.cfg["config_version"], "caps": caps, "used": used,
                "remaining": {s: max(0, caps[s] - used[s]) for s in rc.STAGES},
                "deep_used": self.reg.deep_candidates_on(self.day()), "deep_cap": caps["deep_candidates"],
                "overage_allowed": self.overage_allowed()}
