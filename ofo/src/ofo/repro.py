"""Reproducibility: everything needed to recompute a result, stored with it, and a check that really recomputes it.

`code_version` is the git commit when the code runs from a repository, otherwise a hash of every source file (the owner updates
from a zip, not from git). `candidate_digest` recomputes a final candidate's daily P&L FROM THE STORED METADATA ONLY (template name,
parameters, timeframe, direction, exit, contract, costs, data): if any of those had been forgotten the digest would differ and the
strategy would not be promoted."""
from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def code_version() -> str:
    try:
        out = subprocess.run(["git", "-C", str(SRC), "rev-parse", "--short=10", "HEAD"], capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", str(SRC), "status", "--porcelain", "--", str(SRC)], capture_output=True, text=True, timeout=5).stdout.strip()
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip() + ("+dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        pass
    h = hashlib.sha256()
    for f in sorted(SRC.rglob("*.py")):
        h.update(f.name.encode()); h.update(f.read_bytes())
    return "src-" + h.hexdigest()[:10]


def cost_model_descriptor(contract, contracts: int, slippage_ticks: float, cfg_version: str) -> str:
    """Everything that decides what a trade costs and when it fills. A different descriptor = a different experiment."""
    return (f"{contract.symbol}|comm{contract.commission_per_side}|tick{contract.tick_value}|pv{contract.point_value}|n{contracts}|"
            f"slip{slippage_ticks}|fill=signal_bar_close|costmodel_v{cfg_version}")


def digest(series: pd.Series, decimals: int = 6) -> str:
    return hashlib.sha256(np.round(series.to_numpy(dtype="float64"), decimals).tobytes() + str(len(series)).encode()).hexdigest()[:16]


def candidate_digest(bars: pd.DataFrame, meta: dict, days: list) -> str:
    """Daily P&L of the candidate described by `meta`, rebuilt from scratch, hashed over `days`."""
    from .backtest import Contract
    from .data import resample
    from .research import daily_series
    from .strategies import TEMPLATES, version_fn
    c = meta["contract"]
    contract = Contract(c["symbol"], c["tick_size"], c["tick_value"], c["commission_per_side"])
    cand = {"fn": version_fn(TEMPLATES[meta["template"]].fn, meta.get("direction"), meta.get("schedule")), "params": dict(meta["params"]), "tf": meta["tf"]}
    s = daily_series({meta["tf"]: resample(bars, meta["tf"])}, cand, contract, int(meta["contracts"]), float(meta["slippage_ticks"]),
                     exit=meta.get("exit"))
    return digest(s.reindex(days).fillna(0.0))
