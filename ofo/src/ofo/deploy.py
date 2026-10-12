"""Autonomous deployment of approved strategies: PAPER trading on data the strategy has never seen.

Once the Judge approves a strategy it is deployed here with no click. "Deployed" means: from the last
session of the data on the deployment day, the strategy's rules (with its chosen stop/target) are applied to every
new day, in the recommended size, and the result is a paper ledger that is compared with what the Judge expected.
Two deterministic rules then decide, with no human in the loop:
  * RETIRED   - the paper drawdown reached 80% of the account's allowed drawdown, or (40+ days) the live results
                are statistically worse than the hidden-block results (z < -2.5);
  * CONFIRMED - 60+ days, profitable and consistent with what was expected.
No order ever leaves this computer: real execution needs a broker connection that only you can authorise
(see README). Every new trade is also appended to data/signals/<slug>.jsonl, the hook for a Discord or
broker bot."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .data import resample
from .prop import PropRules, simulate_account
from .research import daily_series
from .strategies import TEMPLATES
from .trades import extract_trades

DD_RETIRE = 0.80          # of the account's allowed drawdown
Z_RETIRE = -2.5
MIN_DAYS_RETIRE = 40
MIN_DAYS_CONFIRM = 60


def pick_account(dossier: dict) -> tuple[str, dict] | None:
    """The first account (50k before 25k) with a safe recommended size."""
    for acct in ("50k", "25k"):
        a = (dossier.get("accounts") or {}).get(acct)
        if a and a.get("recommended"):
            return acct, a["recommended"]
    return None


def make_deployment(dossier: dict, last_session: str, now: str) -> dict | None:
    pick = pick_account(dossier)
    if pick is None:
        return None
    acct, rec = pick
    return {"slug": dossier["slug"], "name": dossier["name"], "market": dossier.get("market"), "template": dossier["template"], "tf": dossier["tf"],
            "params": dossier["params"], "exit": (dossier.get("exits") or {}).get("chosen"),
            "account": acct, "micro": rec["micro_equiv"], "cut": last_session, "deployed_at": now,
            "status": "live", "reason": "", "oos_mean": dossier.get("oos_mean"), "oos_std": dossier.get("oos_std"),
            "n_signals": 0, "forward": {"days": 0}}


def assess(daily, exp_mean, exp_std, max_dd_allowed: float) -> tuple[str, str]:
    """('live'|'retired'|'confirmed', reason) from the forward daily P&L (already in the deployed size)."""
    x = np.asarray(daily, dtype=float)
    n = len(x)
    if n == 0:
        return "live", ""
    cum = np.cumsum(x)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum))
    if dd >= DD_RETIRE * max_dd_allowed:
        return "retired", f"paper drawdown ${dd:,.0f} reached {DD_RETIRE:.0%} of the allowed ${max_dd_allowed:,.0f}"
    z = None
    if exp_mean is not None and exp_std and n >= 2:
        z = (x.mean() - exp_mean) / (exp_std / np.sqrt(n))
        if n >= MIN_DAYS_RETIRE and z < Z_RETIRE:
            return "retired", f"live results far below the hidden-block results (z={z:.1f} after {n} days)"
    if n >= MIN_DAYS_CONFIRM and cum[-1] > 0 and (z is None or z > -1.0):
        return "confirmed", f"{n} paper days, profitable and consistent with expectations"
    return "live", ""


def forward_update(bars: pd.DataFrame, dep: dict, contract, base_contracts: int, rules: PropRules) -> dict:
    """Recompute the paper ledger of one deployment from the bars (history gives the indicators their warm-up)."""
    t = TEMPLATES[dep["template"]]
    cand = {"fn": t.fn, "params": dep["params"], "tf": dep["tf"]}
    s = daily_series({dep["tf"]: resample(bars, dep["tf"]) if dep["tf"] != "1min" else bars}, cand, contract,
                     base_contracts, 1.0, exit=dep.get("exit"))
    fwd = s[s.index > pd.Timestamp(dep["cut"])] * (dep["micro"] / base_contracts)
    days = len(fwd)
    cum = fwd.cumsum()
    out = {"days": days, "pnl": float(fwd.sum()) if days else 0.0,
           "equity": [[str(d)[:10], float(v)] for d, v in cum.items()][-250:],
           "last_session": str(fwd.index[-1])[:10] if days else None}
    if days:
        o = simulate_account(fwd.values, rules)
        out["eval_if_traded"] = {"status": o.status, "days": o.days}
        out["max_drawdown"] = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum.values]))[1:] - cum.values))
    status, reason = assess(fwd.values, dep.get("oos_mean") and dep["oos_mean"] * dep["micro"] / base_contracts,
                            dep.get("oos_std") and dep["oos_std"] * dep["micro"] / base_contracts, rules.max_drawdown)
    return {"forward": out, "status": status, "reason": reason, "series": fwd}


def new_signals(bars: pd.DataFrame, dep: dict, contract, base_contracts: int) -> list[dict]:
    """Trades entered after the deployment cut (the signal log). Sized in the deployed contracts."""
    t = TEMPLATES[dep["template"]]
    tr = extract_trades(bars, t.fn, dep["params"], dep["tf"], contract, dep["micro"] if contract.symbol.startswith("M") else
                        max(1, round(dep["micro"] / 10)), 1.0, exit=dep.get("exit"))
    return [x for x in tr if x["entry_session"] > dep["cut"]]


def append_signals(dirpath: Path, slug: str, trades: list[dict]) -> None:
    if not trades:
        return
    dirpath.mkdir(parents=True, exist_ok=True)
    with (dirpath / f"{slug}.jsonl").open("a", encoding="utf-8") as f:
        for x in trades:
            f.write(json.dumps({"slug": slug, **x}) + "\n")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.d = json.loads(self.path.read_text()) if self.path.exists() else {"items": [], "data": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d, indent=1, default=str))

    def get(self, slug: str) -> dict | None:
        return next((x for x in self.d["items"] if x["slug"] == slug), None)


def infer_prefix(data_dir: Path) -> str | None:
    """The S3 prefix the day files were downloaded from: their folder relative to the data dir."""
    import re
    from collections import Counter
    c = Counter()
    for p in Path(data_dir).rglob("*.csv.gz"):
        if re.search(r"\d{4}-\d{2}-\d{2}\.csv\.gz$", p.name):
            c[p.parent.relative_to(data_dir).as_posix()] += 1
    return (c.most_common(1)[0][0] + "/") if c else None


def refresh_data(data_dir: Path, bars_path: str, root: str, today=None, download=None, build=None) -> dict:
    """Download the day files published since the last one and rebuild the continuous 1-minute file.
    Needs MASSIVE_S3_ACCESS_KEY / MASSIVE_S3_SECRET_KEY in the environment. Returns what happened."""
    import datetime as dt
    import os
    if download is None and not (os.environ.get("MASSIVE_S3_ACCESS_KEY") and os.environ.get("MASSIVE_S3_SECRET_KEY")):
        return {"ok": False, "why": "no_keys"}
    prefix = infer_prefix(data_dir)
    if prefix is None:
        return {"ok": False, "why": "no_prefix"}
    from . import massive
    download = download or massive.download_range
    have = sorted(massive.key_date(p.name) for p in Path(data_dir).rglob("*.csv.gz") if massive.key_date(p.name))
    last = have[-1]
    today = today or dt.date.today()
    before = len(list(Path(data_dir).rglob("*.csv.gz")))
    try:
        download(prefix, last - dt.timedelta(days=2), today, str(data_dir))
    except Exception as e:
        return {"ok": False, "why": f"download: {e}"[:200]}
    after = len(list(Path(data_dir).rglob("*.csv.gz")))
    if after == before:
        return {"ok": True, "new_files": 0}
    import re
    from .data import validate
    from .futures import build_root
    paths = [str(p) for p in Path(data_dir).rglob("*.csv.gz") if re.search(r"\d{4}-\d{2}-\d{2}\.csv\.gz$", p.name)]
    df = (build or build_root)(paths, root)
    validate(df[["open", "high", "low", "close", "volume"]])
    tmp = Path(str(bars_path) + ".tmp")
    df.to_csv(tmp)
    tmp.replace(bars_path)
    return {"ok": True, "new_files": after - before, "rebuilt": True}
