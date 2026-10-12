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
        return len(self.combos()) * len(self.timeframes)


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


def from_dict(d: dict, allow_control: bool = False) -> StrategySpec:
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
    tfs = d.get("timeframes") or list(TIMEFRAMES)
    bad = [x for x in tfs if x not in TIMEFRAMES]
    if bad:
        raise SpecError(f"timeframes {bad} not allowed; use {list(TIMEFRAMES)}")
    spec = StrategySpec(str(d.get("name") or tname), tname, ranges, list(tfs),
                        str(d.get("rationale", "")), str(d.get("source", "")))
    n = spec.n_candidates
    if n == 0:
        raise SpecError("no valid parameter combination")
    if n > MAX_COMBOS:
        raise SpecError(f"{n} candidates exceeds the limit of {MAX_COMBOS}")
    if not spec.rationale.strip() and not allow_control:
        raise SpecError("a rationale is required: say WHY this should have an edge")
    return spec
