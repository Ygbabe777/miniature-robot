"""The Planner and Auditor agents, with strict validation of everything they return."""
from __future__ import annotations

from ..specs import SpecError, StrategySpec, from_dict
from .base import Agent, load_prompt

DECISIONS = ("abandon", "extend", "new_hypothesis")


def dev_summary(report: dict, budget: dict, depth: int) -> dict:
    """What the Planner may see: development-period facts ONLY. The locked block is never
    included, so the Planner cannot tune towards it."""
    s = report["surface"]
    d = report.get("surface_detrended")
    keep = ("n_combos", "median_sharpe", "frac_positive", "best_sharpe", "best",
            "plateau_ratio", "neighbours_positive", "checks", "robust")
    return {
        "spec": {k: report[k] for k in ("name", "template", "rationale")},
        "surface": {k: s[k] for k in keep},
        "surface_detrended": {k: d[k] for k in keep} if d else None,
        "walk_forward": {"days": report["wf_oos_days"], "pnl": report["wf_oos_pnl"],
                         "sharpe_daily": report["wf_oos_sharpe_daily"]},
        "budget": budget,
        "depth": depth,
    }


def make_planner(llm, parent: StrategySpec, trials_left: int, depth: int, events=None) -> Agent:
    def validate(d: dict) -> dict:
        dec = d.get("decision")
        if dec not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        out = {"decision": dec, "reason": str(d.get("reason", ""))[:800], "spec": None}
        if dec == "abandon":
            return out
        if depth >= 2 and dec == "extend":
            raise ValueError("depth >= 2: only abandon or new_hypothesis are allowed")
        if not isinstance(d.get("spec"), dict):
            raise ValueError(f"{dec} requires a spec object")
        spec = from_dict({**d["spec"], "source": d["spec"].get("source") or "planner"})
        if dec == "extend" and spec.template != parent.template:
            raise ValueError("extend must keep the same template")
        if dec == "new_hypothesis" and (spec.template, spec.ranges) == (parent.template, parent.ranges):
            raise ValueError("new_hypothesis must differ from the parent")
        if spec.n_candidates > parent.n_candidates or spec.n_candidates > trials_left:
            raise ValueError(f"{spec.n_candidates} candidates exceeds the allowance "
                             f"(parent {parent.n_candidates}, budget {trials_left})")
        out["spec"] = spec
        return out
    return Agent("planner", llm, load_prompt("planner"), validate, events, max_attempts=2)


def make_auditor(llm, events=None) -> Agent:
    def validate(d: dict) -> dict:
        summary = str(d.get("summary", "")).strip()
        if not summary:
            raise ValueError("summary is required")
        flags = d.get("risk_flags", [])
        if not isinstance(flags, list):
            raise ValueError("risk_flags must be a list")
        # only these keys survive: the auditor can never carry a verdict or an approval
        return {"summary": summary[:2000], "risk_flags": [str(f)[:200] for f in flags][:8],
                "next_step": str(d.get("next_step", ""))[:500]}
    return Agent("auditor", llm, load_prompt("auditor"), validate, events, max_attempts=2)
