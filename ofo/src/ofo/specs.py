"""Strategy specs: a strategy is a TEMPLATE plus parameter RANGES, never free-form code.

Specs come from people, PDFs or LLM agents. Because they only name a registered template
and numeric ranges inside its hard bounds, an agent can propose anything it likes
without being able to run arbitrary code, and every spec is testable and reproducible."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .strategies import TEMPLATES, TIMEFRAMES

MAX_COMBOS = 3000
MAX_VALUES_PER_PARAM = 40
CARD_FIELDS = {                       # the hypothesis card every NEW strategy needs (written in the owner's language)
    "fenomeno": "fenomeno di mercato ipotizzato", "persistenza": "perché potrebbe persistere", "condizioni_funziona": "condizioni in cui dovrebbe funzionare",
    "condizioni_fallisce": "condizioni in cui dovrebbe fallire", "segnale": "segnale osservabile", "regola_ingresso": "regola d'ingresso",
    "regola_uscita": "regola d'uscita iniziale", "costi_limiti": "costi e limiti di esecuzione", "previsione_falsificabile": "previsione falsificabile",
    "criterio_rigetto": "criterio di rigetto"}


class SpecError(ValueError):
    pass


@dataclass
class StrategySpec:
    name: str
    template: str
    ranges: dict                       # param -> list of values
    timeframes: list = field(default_factory=lambda: list(TIMEFRAMES))
    rationale: str = ""
    source: str = ""
    hypothesis: dict = field(default_factory=dict)      # the hypothesis card (CARD_FIELDS)
    max_configs: int | None = None                      # cap on distinct parameter combinations tested (None: the template's cap)
    hypothesis_missing: list = field(default_factory=list)   # card fields the proposer left out (flagged to the owner, never invented)

    @property
    def family(self) -> str:
        from .research_config import family_of
        return family_of(self.template)

    def planned_configs(self) -> int:
        """Parameter combinations the search will test at most: the whole grid if it fits the cap, otherwise the cap (staged search)."""
        from .research_config import max_configs_for
        return min(len(self.combos()), self.max_configs or max_configs_for(self.template))

    def combos(self) -> list[dict]:
        t = TEMPLATES[self.template]
        names = list(self.ranges)
        out = []
        for vals in itertools.product(*[self.ranges[n] for n in names]):
            p = dict(zip(names, vals))
            if t.valid(p):
                out.append(p)
        return out

    @property
    def n_candidates(self) -> int:
        """Candidates the sweep will evaluate AT MOST (configurations x timeframes): the number registered as trials up front."""
        return self.planned_configs() * len(self.timeframes)


def _values(name: str, spec, typ, lo, hi) -> list:
    if isinstance(spec, dict) and "values" in spec:
        vals = list(spec["values"])
    elif isinstance(spec, dict) and {"min", "max"} <= spec.keys():
        step = spec.get("step", 1 if typ is int else (spec["max"] - spec["min"]) / 5)
        if step <= 0:
            raise SpecError(f"{name}: step must be > 0")
        vals = list(np.arange(spec["min"], spec["max"] + step / 2, step))
    elif isinstance(spec, list):
        vals = spec
    else:
        raise SpecError(f"{name}: use {{'min','max','step'}}, {{'values':[...]}} or a list")
    if not vals or len(vals) > MAX_VALUES_PER_PARAM:
        raise SpecError(f"{name}: need 1..{MAX_VALUES_PER_PARAM} values, got {len(vals)}")
    out = []
    for v in vals:
        v = typ(round(float(v), 6)) if typ is float else int(round(float(v)))
        if not lo <= v <= hi:
            raise SpecError(f"{name}={v} outside allowed bounds [{lo}, {hi}]")
        out.append(v)
    return sorted(set(out))


def check_card(card) -> dict:
    if not isinstance(card, dict):
        raise SpecError("hypothesis must be an object with the 10 card fields")
    missing = [k for k in CARD_FIELDS if len(str(card.get(k, "")).strip()) < 12]
    if missing:
        raise SpecError("hypothesis card incomplete: missing or too short " + ", ".join(missing))
    return {k: str(card[k]).strip()[:600] for k in CARD_FIELDS}


def lenient_card(card) -> tuple[dict, list]:
    """A card with what the proposer wrote; every missing or too-short field becomes an explicit placeholder and is listed. Nothing is invented."""
    card = card if isinstance(card, dict) else {}
    missing = [k for k in CARD_FIELDS if len(str(card.get(k, "")).strip()) < 12]
    return {k: (str(card[k]).strip()[:600] if k not in missing else (str(card.get(k, "")).strip() or "(non dichiarato dal Researcher)")) for k in CARD_FIELDS}, missing


def from_dict(d: dict, allow_control: bool = False, require_card=False) -> StrategySpec:
    """require_card: False (optional), True/"strict" (all 10 fields or SpecError), "lenient" (incomplete cards are accepted and flagged)."""
    if not isinstance(d, dict):
        raise SpecError("spec must be an object")
    tname = d.get("template")
    if tname not in TEMPLATES:
        raise SpecError(f"unknown template {tname!r}; allowed: {[k for k, t in TEMPLATES.items() if not t.control]}")
    t = TEMPLATES[tname]
    if t.control and not allow_control:
        raise SpecError("control templates cannot be proposed")
    raw = d.get("ranges", {})
    if set(raw) != set(t.params):
        raise SpecError(f"{tname} needs exactly the parameters {sorted(t.params)}, got {sorted(raw)}")
    ranges = {n: _values(n, raw[n], *t.params[n]) for n in t.params}
    from .research_config import execution_timeframes
    tfs = d.get("timeframes") or execution_timeframes()
    bad = [x for x in tfs if x not in TIMEFRAMES]
    if bad:
        raise SpecError(f"timeframes {bad} not allowed; use {list(TIMEFRAMES)}")
    missing: list = []
    if require_card == "lenient":
        card, missing = lenient_card(d.get("hypothesis"))
    else:
        card = check_card(d.get("hypothesis")) if (require_card or d.get("hypothesis")) else {}
    mc = d.get("max_configs")
    spec = StrategySpec(str(d.get("name") or tname), tname, ranges, list(tfs),
                        str(d.get("rationale", "")), str(d.get("source", "")), card, int(mc) if mc else None, missing)
    n = len(spec.combos()) * len(spec.timeframes)
    if n == 0:
        raise SpecError("no valid parameter combination")
    if n > MAX_COMBOS:
        raise SpecError(f"{n} candidates exceeds the limit of {MAX_COMBOS}")
    if not spec.rationale.strip() and not allow_control:
        raise SpecError("a rationale is required: say WHY this should have an edge")
    return spec
