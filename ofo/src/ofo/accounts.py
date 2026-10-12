"""Prop-firm accounts (evaluation / funded) as the Lab shows them: one specimen flask each.

For now the accounts live in data/accounts.json, edited by hand (see rules/accounts.example.json).
A broker/prop connector can write the same file later: nothing else has to change."""
from __future__ import annotations

import json
from pathlib import Path

KINDS = ("evaluation", "funded")


def derive(a: dict) -> dict:
    """Health, progress and a display state computed from the raw numbers."""
    start = float(a["start_balance"]); bal = float(a["balance"]); dd = float(a["max_drawdown"])
    floor = float(a.get("floor", start - dd))
    target = float(a.get("profit_target") or 0)
    health = max(0.0, min(1.0, (bal - floor) / dd)) if dd > 0 else 1.0       # 1 = far from the floor, 0 = at it
    progress = max(0.0, min(1.0, (bal - start) / target)) if target > 0 and a["kind"] == "evaluation" else None
    status = a.get("status", "active")
    if status == "busted" or bal <= floor:
        state = "busted"
    elif status == "passed" or (progress is not None and progress >= 1):
        state = "passed"
    elif health < 0.25:
        state = "danger"
    else:
        state = "ok"
    return {**a, "floor": floor, "health": round(health, 3), "progress": None if progress is None else round(progress, 3),
            "state": state, "payouts": int(a.get("payouts", 0))}


def validate(a: dict) -> dict:
    for k in ("id", "firm", "plan", "kind", "start_balance", "balance", "max_drawdown"):
        if k not in a:
            raise ValueError(f"account is missing {k!r}")
    if a["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if float(a["max_drawdown"]) <= 0:
        raise ValueError("max_drawdown must be > 0")
    return a


def load_accounts(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text())
    return [derive(validate(a)) for a in (raw["accounts"] if isinstance(raw, dict) else raw)][:8]


DEMO = [
    {"id": "demo1", "firm": "Topstep", "plan": "50K", "kind": "evaluation", "start_balance": 50000, "balance": 51420, "profit_target": 3000, "max_drawdown": 2000},
    {"id": "demo2", "firm": "Lucid", "plan": "50K", "kind": "evaluation", "start_balance": 50000, "balance": 48350, "profit_target": 3000, "max_drawdown": 2000},
    {"id": "demo3", "firm": "MFFU", "plan": "50K", "kind": "funded", "start_balance": 50000, "balance": 52300, "max_drawdown": 2000, "floor": 50000, "payouts": 2},
    {"id": "demo4", "firm": "Tradeify", "plan": "25K", "kind": "evaluation", "start_balance": 25000, "balance": 26580, "profit_target": 1500, "max_drawdown": 1500, "status": "passed"},
    {"id": "demo5", "firm": "Old account", "plan": "50K", "kind": "evaluation", "start_balance": 50000, "balance": 48000, "profit_target": 3000, "max_drawdown": 2000, "status": "busted"},
]


def demo_accounts() -> list[dict]:
    return [{**derive(a), "demo": True} for a in DEMO]
