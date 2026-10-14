"""One versioned configuration for the whole research engine (rules/research.json).

Everything that decides how the lab spends its budget lives there: markets, execution/context timeframes, daily caps, the five
stage shares, walk-forward targets, sample targets, holdout, search-space design, early-stop rule, family allocation, the cost-model
version. The values are an initial baseline to validate, not universal requirements. Its `version` plus a hash of the content form
the config version stored with every experiment: change a number and the new experiments are tracked as a new configuration."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .strategies import TEMPLATES, TIMEFRAMES

HERE = Path(__file__).resolve().parent.parent.parent / "rules" / "research.json"
STAGES = ("hypothesis", "screening", "validation", "robustness", "audit")


class ResearchConfigError(ValueError):
    pass


def _default() -> dict:
    return {"version": "builtin", "markets": {"priority": ["NQ"], "later": ["ES"]},
            "timeframes": {"execution": ["1min", "5min"], "context": ["15min"]},
            "budget": {"screen_per_day": 1000, "deep_per_day": 100, "enforce_daily_caps": False,
                       "stage_split": {"hypothesis": 0.15, "screening": 0.35, "validation": 0.30, "robustness": 0.15, "audit": 0.05}},
            "walk_forward": {"min_windows": 6, "train_days": 120, "test_days": 20, "min_test_days": 10},
            "sample": {"min_oos_trades": 200, "min_backtest_trades": 200},
            "judge": {}, "near_miss": {"min_dsr": 0.70, "auto_paper": False}, "favorites": {"min_winning_days": 0.50, "max_losing_streak": 7},
            "holdout_fraction": 0.18,
            "search": {"coarse_step": 2, "random_extra_fraction": 0.25, "refine_top": 3, "refine_share": 0.25, "seed": 7},
            "early_stop": {"min_fraction": 0.3, "min_configs": 8, "z": 1.645},
            "allocation": {"prior_strength": 20, "prior_rate": 0.05, "novelty_weight": 1.0, "min_family_share": 0.10, "max_family_share": 0.40},
            "hypothesis": {"queue_low": 4, "min_gap_min": 8, "economy_gap_factor": 3, "per_call": 2},
            "cost_model_version": "1", "dsr_trials": "locked_looks", "families": {}, "search_space": {}}


def _merge(base: dict, user: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in user.items():
        if k.startswith("_"):
            continue
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("families", "search_space") else v
    return out


def validate(cfg: dict) -> dict:
    split = cfg["budget"]["stage_split"]
    if abs(sum(split.values()) - 1.0) > 1e-6 or set(split) != set(STAGES):
        raise ResearchConfigError(f"stage_split must cover {STAGES} and sum to 1, got {split}")
    for tf in cfg["timeframes"]["execution"] + cfg["timeframes"]["context"]:
        if tf not in TIMEFRAMES:
            raise ResearchConfigError(f"unknown timeframe {tf}")
    for tname, sp in cfg.get("search_space", {}).items():
        if tname not in TEMPLATES:
            raise ResearchConfigError(f"search_space names an unknown template {tname}")
        for pn, p in sp["params"].items():
            typ, lo, hi = TEMPLATES[tname].params[pn]
            if not (lo <= p["range"][0] <= p["range"][1] <= hi) or not (p["range"][0] <= p["init"] <= p["range"][1]):
                raise ResearchConfigError(f"{tname}.{pn}: range {p['range']} / init {p['init']} outside the template bounds [{lo}, {hi}]")
    return cfg


def load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else HERE
    cfg = _default()
    raw = ""
    if p.exists():
        raw = p.read_text(encoding="utf-8-sig", errors="ofo_legacy")
        try:
            cfg = _merge(cfg, json.loads(raw))
        except ValueError as e:
            raise ResearchConfigError(f"{p} is not valid JSON: {e}") from e
    validate(cfg)
    cfg["config_version"] = f"{cfg['version']}+{hashlib.sha256(raw.encode()).hexdigest()[:8]}"
    return cfg


def family_of(template: str, cfg: dict | None = None) -> str:
    cfg = cfg or load()
    for fid, f in cfg.get("families", {}).items():
        if template in f.get("templates", []):
            return fid
    return TEMPLATES[template].family or "?" if template in TEMPLATES else "?"


def execution_timeframes(cfg: dict | None = None) -> list[str]:
    return list((cfg or load())["timeframes"]["execution"])


def stage_caps(cfg: dict) -> dict:
    """Per-day caps for every stage. The screening cap is the owner's number; the others follow from the stage shares."""
    b = cfg["budget"]; split = b["stage_split"]
    total = b.get("total_per_day") or b["screen_per_day"] / split["screening"]
    return {"total": int(round(total)), **{s: int(round(total * split[s])) for s in STAGES}, "deep_candidates": int(b["deep_per_day"])}


_CACHE: dict = {}


def get(path: str | Path | None = None) -> dict:
    """load() cached on the file's modification time: cheap enough to call from properties."""
    p = Path(path) if path else HERE
    stamp = p.stat().st_mtime_ns if p.exists() else 0
    c = _CACHE.get(str(p))
    if c and c[0] == stamp:
        return c[1]
    cfg = load(p)
    _CACHE[str(p)] = (stamp, cfg)
    return cfg


def max_configs_for(template: str, cfg: dict | None = None) -> int:
    cfg = cfg or get()
    return int(cfg.get("search_space", {}).get(template, {}).get("max_configs", 10_000))
