"""Versioned prop-firm rulesets. Every prop is configured separately and says how much its rules can be trusted.

provenance:
  VERIFIED_RULESET       checked against the firm's official rules (say when in `verified_on`)
  USER_SUPPLIED_RULESET  typed in by the owner from the rules they read
  ASSUMED_RULESET        placeholder values: shown everywhere with a warning, never presented as certain
Files live in rules/rulesets/*.json (the owner adds or edits them; update.py never overwrites an existing file)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROVENANCE = {"VERIFIED": "VERIFIED_RULESET", "USER": "USER_SUPPLIED_RULESET", "ASSUMED": "ASSUMED_RULESET"}


@dataclass(frozen=True)
class Phase:
    """The drawdown rules of one phase (evaluation or funded)."""
    start_balance: float
    max_loss: float
    trailing: str                       # static | eod | intraday
    lock_at_start: bool = True
    daily_loss_limit: float | None = None
    max_contracts_micro: int | None = None
    profit_target: float | None = None  # evaluation only
    min_days: int = 1                   # evaluation only
    consistency_pct: float | None = None


@dataclass(frozen=True)
class PayoutRules:
    min_winning_days: int = 5
    winning_day_min: float = 150.0
    min_payout: float = 500.0
    cap: float | None = 2000.0
    profit_split: float = 0.9
    buffer: float = 0.0                 # profit that must stay in the account after a payout
    floor_after_payout: str = "unchanged"   # unchanged | rebase
    delay_days: int = 3


@dataclass(frozen=True)
class Costs:
    eval_monthly: float = 0.0
    activation: float = 0.0
    reset: float = 0.0
    funded_monthly: float = 0.0


@dataclass(frozen=True)
class Ruleset:
    id: str
    version: str
    firm: str
    provenance: str
    label: str
    size: float
    evaluation: Phase
    funded: Phase | None
    payout: PayoutRules | None
    costs: Costs
    rules: dict = field(default_factory=dict)
    notes: str = ""
    source: str = ""
    verified_on: str | None = None

    @property
    def name(self) -> str:
        return f"{self.id} v{self.version}"

    @property
    def verified(self) -> bool:
        return self.provenance == "VERIFIED_RULESET"

    def warning(self, lang: str = "it") -> str:
        if self.verified:
            return ""
        what = "regole SUPPLIED dall'utente" if self.provenance == "USER_SUPPLIED_RULESET" else "regole ASSUNTE (segnaposto)"
        return f"Ruleset {self.name}: {what}, non verificate sul regolamento ufficiale: i numeri economici valgono solo con queste ipotesi."

    def to_prop_rules(self):
        """The simplified single-phase rules the older modules use (evaluation phase only)."""
        from .prop import PropRules, Trailing
        e = self.evaluation
        return PropRules(self.id, e.start_balance, e.profit_target or 0.0, e.max_loss, Trailing(e.trailing), e.lock_at_start,
                         e.daily_loss_limit, None, e.min_days, e.consistency_pct)


def _phase(d: dict, size: float, evaluation: bool) -> Phase:
    return Phase(start_balance=float(d.get("start_balance", size)), max_loss=float(d["max_loss"]), trailing=d.get("trailing", "eod"),
                 lock_at_start=bool(d.get("lock_at_start", True)), daily_loss_limit=d.get("daily_loss_limit"),
                 max_contracts_micro=d.get("max_contracts_micro"), profit_target=d.get("profit_target") if evaluation else None,
                 min_days=int(d.get("min_days", 1)) if evaluation else 1, consistency_pct=d.get("consistency_pct"))


def from_dict(d: dict) -> Ruleset:
    prov = d.get("provenance", "ASSUMED")
    prov = PROVENANCE.get(prov, prov if prov in PROVENANCE.values() else "ASSUMED_RULESET")
    size = float(d["account"]["size"])
    f = d.get("funded")
    po = (f or {}).get("payout")
    return Ruleset(id=d["id"], version=str(d.get("version", "1")), firm=d.get("firm", ""), provenance=prov, label=d["account"].get("label", str(int(size))),
                   size=size, evaluation=_phase(d["evaluation"], size, True), funded=_phase(f, size, False) if f else None,
                   payout=PayoutRules(**po) if po else None, costs=Costs(**d.get("costs", {})), rules=d.get("rules", {}),
                   notes=d.get("notes", ""), source=d.get("source", ""), verified_on=d.get("verified_on"))


def load_rulesets(directory: str | Path = "rules/rulesets") -> dict[str, Ruleset]:
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        try:
            r = from_dict(json.loads(p.read_text(encoding="utf-8-sig")))
            out[r.id] = r
        except (ValueError, KeyError, TypeError):
            continue                                  # a malformed file never stops the lab
    return out


def load_farming_config(path: str | Path = "rules/farming.json") -> dict:
    default = {"rulesets": ["GENERIC_EOD_50K", "GENERIC_EOD_25K"], "horizon_days": 126, "max_attempts": 3, "block": 3, "n_outer": 30,
               "n_inner": 150, "n_select": 100, "sizes": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "seed": 11,
               "gates": {"min_locked_sessions": 60, "min_locked_trades": 30, "min_npfv_lcb95": 0.0, "min_p_first_payout": 0.15,
                         "min_first_payout_given_pass": 0.5, "max_p_funded_fail_before_payout": 0.6, "stress_costs_multiple": 2.0,
                         "stress_npfv_min": 0.0, "max_es5_to_cost": 3.0},
               "forward": {"min_days": 20}, "payout_policies": ["A", "B", "C"]}
    try:
        user = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default
    cfg = {**default, **{k: v for k, v in user.items() if not k.startswith("_")}}
    cfg["gates"] = {**default["gates"], **(user.get("gates") or {})}
    return cfg
