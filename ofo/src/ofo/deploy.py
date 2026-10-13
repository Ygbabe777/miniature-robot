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
            "params": dossier["params"], "direction": dossier.get("direction") or "both", "exit": (dossier.get("exits") or {}).get("chosen"),
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


def _scaled(value, factor):
    """value * factor, or None when the value is unknown. (Never `value and value * factor`: a legitimate 0.0 is not "missing".)"""
    return None if value is None else float(value) * factor


class SignalLog:
    """Append-only, hash-chained record of what a paper deployment did. Once written, a line is never changed: refreshing
    or rebuilding the dataset cannot rewrite the operating history. Every line carries the hash of the previous one, so
    an edit anywhere is detectable (`verify`). If a later recomputation disagrees with a recorded trade, the log gets a new
    `restatement` line next to it; a trade that appears in the PAST after the fact gets a `late_trade` line and is not
    counted. The original lines stay exactly as they were."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.records = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.records.append(json.loads(line))

    @staticmethod
    def _hash(prev: str, body: dict) -> str:
        import hashlib
        return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)).encode()).hexdigest()

    @property
    def last_hash(self) -> str:
        return self.records[-1]["hash"] if self.records else "genesis"

    def append(self, body: dict) -> dict:
        import os
        rec = {**body, "seq": len(self.records), "prev": self.last_hash}
        rec["hash"] = self._hash(rec["prev"], {k: v for k, v in rec.items() if k != "hash"})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.records.append(rec)
        return rec

    def verify(self) -> dict:
        prev = "genesis"
        for i, r in enumerate(self.records):
            body = {k: v for k, v in r.items() if k != "hash"}
            if r.get("seq") != i or r.get("prev") != prev or r.get("hash") != self._hash(prev, body):
                return {"ok": False, "first_bad_seq": i, "n": len(self.records)}
            prev = r["hash"]
        return {"ok": True, "first_bad_seq": None, "n": len(self.records)}

    def _by(self, kind: str) -> dict:
        return {r["id"]: r for r in self.records if r["kind"] == kind}

    def exits(self) -> list[dict]:
        return list(self._by("exit").values())

    def sync(self, slug: str, trades: list[dict], dataset_fp: str | None = None, tolerance: float = 0.5) -> list[dict]:
        """Record what is NEW: an `entry` line the first time a trade is seen, an `exit` line once it has closed. A closed
        trade that is recomputed differently later (more than `tolerance` dollars of P&L) adds a `restatement` line."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entries, exits = self._by("entry"), self._by("exit")
        seen_restate = {(r["id"], round(r["recomputed_pnl"], 2)) for r in self.records if r["kind"] == "restatement"}
        seen_late = {r["id"] for r in self.records if r["kind"] == "late_trade"}
        frontier = max((r["entry_t"] for r in entries.values()), default=None)       # the latest entry already on record
        added = []
        for t in trades:
            tid = f"{slug}:{t['entry_t']}:{t['side']}"
            if tid not in entries and frontier is not None and t["entry_t"] < frontier:
                # a trade OLDER than the latest recorded one that the log has never seen can only appear if past data changed
                # (a vendor correction, a rebuilt file). It is noted, not added to the operating history or to the P&L.
                if tid not in seen_late:
                    added.append(self.append({"kind": "late_trade", "slug": slug, "id": tid, "entry_t": t["entry_t"],
                                              "pnl_if_counted": t["pnl"], "dataset_fp": dataset_fp, "recorded_at": now}))
                continue
            if tid not in entries:
                added.append(self.append({"kind": "entry", "slug": slug, "id": tid, "entry_t": t["entry_t"], "side": t["side"],
                                          "entry_px": t["entry_px"], "entry_session": t["entry_session"],
                                          "dataset_fp": dataset_fp, "recorded_at": now}))
            if t.get("reason") == "open":
                continue
            if tid not in exits:
                added.append(self.append({"kind": "exit", "slug": slug, "id": tid, "exit_t": t["exit_t"], "exit_px": t["exit_px"],
                                          "pnl": t["pnl"], "session": t["session"], "reason": t.get("reason"),
                                          "dataset_fp": dataset_fp, "recorded_at": now}))
            elif abs(exits[tid]["pnl"] - t["pnl"]) > tolerance and (tid, round(t["pnl"], 2)) not in seen_restate:
                added.append(self.append({"kind": "restatement", "slug": slug, "id": tid, "recorded_pnl": exits[tid]["pnl"],
                                          "recomputed_pnl": t["pnl"], "dataset_fp": dataset_fp, "recorded_at": now}))
        return added


def forward_update(bars: pd.DataFrame, dep: dict, contract, base_contracts: int, rules: PropRules, log: SignalLog | None = None) -> dict:
    """Paper results of one deployment, built from the IMMUTABLE signal log: realised P&L of the exits recorded there,
    one number per session after the deployment cut (zero on sessions without an exit). Refreshing the data can add new
    lines to the log but never changes the ones already written. Without a log (tests, tools) the log is built in memory."""
    log = log if log is not None else _memory_log()
    from .optimizer import dataset_fingerprint
    log.sync(dep["slug"], new_signals(bars, dep, contract, base_contracts), dataset_fp=dataset_fingerprint(bars))
    key = pd.to_datetime(bars["session"]) if "session" in bars.columns else pd.Series(bars.index.normalize(), index=bars.index)
    sessions = sorted(d for d in key.unique() if d > pd.Timestamp(dep["cut"]))
    daily = {pd.Timestamp(d): 0.0 for d in sessions}
    for e in log.exits():
        d = pd.Timestamp(e["session"])
        if d in daily and e["id"].startswith(dep["slug"] + ":"):
            daily[d] += e["pnl"]
    fwd = pd.Series(daily, dtype=float).sort_index()
    days = len(fwd)
    cum = fwd.cumsum()
    out = {"days": days, "pnl": float(fwd.sum()) if days else 0.0,
           "equity": [[str(d)[:10], float(v)] for d, v in cum.items()][-250:],
           "last_session": str(fwd.index[-1])[:10] if days else None, "signal_log": log.verify()["ok"] if log.records else True}
    if days:
        o = simulate_account(fwd.values, rules)
        out["eval_if_traded"] = {"status": o.status, "days": o.days}
        out["max_drawdown"] = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum.values]))[1:] - cum.values))
    factor = dep["micro"] / base_contracts
    status, reason = assess(fwd.values, _scaled(dep.get("oos_mean"), factor), _scaled(dep.get("oos_std"), factor), rules.max_drawdown)
    return {"forward": out, "status": status, "reason": reason, "series": fwd}


def _memory_log() -> SignalLog:
    import tempfile
    return SignalLog(Path(tempfile.mkdtemp()) / "memory.jsonl")


def new_signals(bars: pd.DataFrame, dep: dict, contract, base_contracts: int) -> list[dict]:
    """Trades entered after the deployment cut, from the execution ledger, sized in the deployed contracts."""
    t = TEMPLATES[dep["template"]]
    tr = extract_trades(bars, t.fn, dep["params"], dep["tf"], contract, dep["micro"] if contract.symbol.startswith("M") else
                        max(1, round(dep["micro"] / 10)), 1.0, exit=dep.get("exit"), direction=dep.get("direction") or "both")
    return [x for x in tr if x["entry_session"] > dep["cut"]]


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
