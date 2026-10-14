"""Token budget: spend a fixed weekly allowance deliberately.

The owner's allowance is in DOLLARS per working week (e.g. $15 for Mon-Fri). The AI service reports tokens, so this module
turns tokens into dollars and splits what is left evenly over the working days that remain:

    daily allowance = (dollars left at the start of today) / (working days left, today included)

Autonomous work (Scout, Researcher, Planner, Auditor, connections, brainstorm) stops when today's allowance is used up and
does not run on weekends; the chat (Strategist) keeps a reserve so you can always talk to the lab. When about 60% of the
day is gone the lab switches to ECONOMY mode: fewer paper chunks read, slower searches, no brainstorming.

How tokens become dollars (most accurate first):
  1. per-model prices you put in config/budget.json ("prices": {"model": {"in": usd_per_million, "out": usd_per_million}});
  2. a price learned from your own reports: every time you tell the lab how many dollars are left, it compares with the tokens
     it counted since the previous report;
  3. a CONSERVATIVE default blended price (an overestimate on purpose: it under-spends rather than over-spends).
Nothing here is billing truth: your provider's dashboard is. Re-report your balance now and then to keep it honest."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from .llm.client import LLMError

DEFAULTS = {
    "weekly_usd": 15.0,
    "work_days": [0, 1, 2, 3, 4],            # Monday..Friday
    "usd_per_million_default": 3.0,          # blended in+out, deliberately high until calibrated
    "prices": {},                            # {model: {"in": usd per 1M prompt tokens, "out": usd per 1M completion tokens}}
    "chat_reserve_pct": 15,                  # share of each day kept for the chat (Strategist)
    "safety_reserve_pct": 5,                 # of the weekly allowance: below this nothing runs
    "economy_at_pct": 60,                    # share of the autonomous allowance after which the lab saves tokens (only with daily_limit)
    "daily_limit": False,                    # False: no per-day cap, only the weekly allowance counts (set True to split it over the days)
    "economy_weekly_pct": 25,                # without a daily limit: below this share of the weekly allowance the lab saves tokens
    "weekend_pause": True,
}
AUTONOMOUS_EXEMPT = {"strategist"}           # the chat is user-initiated


class BudgetBlocked(LLMError):
    """The token budget does not allow this call now. It is an LLMError: every agent already degrades gracefully on those."""


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


class BudgetGovernor:
    def __init__(self, state_path: str | Path, config: dict | None = None, now=time.time):
        self.path = Path(state_path)
        self.now = now
        self.cfg = {**DEFAULTS, **(config or {})}
        self.s = json.loads(self.path.read_text(encoding="utf-8", errors="ofo_legacy")) if self.path.exists() else {}
        self.s.setdefault("usage", {})
        self.s.setdefault("usd_total", 0.0)
        self.s.setdefault("calibrated", None)
        self.s.setdefault("checkpoint", None)
        if self.s.get("cfg"):                                     # the owner's later changes (UI) win over the first config
            self.cfg.update({k: v for k, v in self.s["cfg"].items() if k in DEFAULTS})
        if self.s["checkpoint"] is None:
            seed = (config or {}).get("remaining_usd")
            self._checkpoint(float(seed) if seed is not None else self.cfg["weekly_usd"])
        self._ensure_week()

    # -- time ---------------------------------------------------------------------------
    def today(self) -> dt.date:
        return dt.date.fromtimestamp(self.now())

    def _save(self) -> None:
        self.s["cfg"] = {k: self.cfg[k] for k in DEFAULTS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from .storage import atomic_write
        atomic_write(self.path, json.dumps(self.s, indent=1))

    def tokens_total(self) -> int:
        return sum(u["prompt"] + u["completion"] for u in self.s["usage"].values())

    def _checkpoint(self, remaining: float) -> None:
        self.s["checkpoint"] = {"date": self.today().isoformat(), "remaining_usd": float(remaining),
                                "usd_total": self.s["usd_total"], "tokens": self.tokens_total()}
        self.s["week_start"] = monday(self.today()).isoformat()

    def _ensure_week(self) -> None:
        if self.s.get("week_start") != monday(self.today()).isoformat():
            self._checkpoint(self.cfg["weekly_usd"])               # a new working week: the full allowance again
            self._save()

    # -- money ---------------------------------------------------------------------------
    def blended(self) -> float:
        return self.s["calibrated"] or self.cfg["usd_per_million_default"]

    def cost(self, model: str, prompt: int, completion: int) -> float:
        p = (self.cfg.get("prices") or {}).get(model)
        if p and p.get("in") is not None and p.get("out") is not None:
            return (prompt * p["in"] + completion * p["out"]) / 1e6
        return (prompt + completion) * self.blended() / 1e6

    def record(self, role: str, model: str, prompt: int, completion: int) -> float:
        self._ensure_week()
        usd = self.cost(model, prompt, completion)
        u = self.s["usage"].setdefault(self.today().isoformat(), {"prompt": 0, "completion": 0, "usd": 0.0, "roles": {}, "models": {}})
        u["prompt"] += prompt; u["completion"] += completion; u["usd"] += usd
        u["roles"][role] = u["roles"].get(role, 0.0) + usd
        u["models"][model] = u["models"].get(model, 0) + prompt + completion
        self.s["usd_total"] += usd
        self._save()
        return usd

    def remaining(self) -> float:
        c = self.s["checkpoint"]
        return max(0.0, c["remaining_usd"] - (self.s["usd_total"] - c["usd_total"]))

    def spent_today(self) -> float:
        return float(self.s["usage"].get(self.today().isoformat(), {}).get("usd", 0.0))

    def tokens_today(self) -> int:
        u = self.s["usage"].get(self.today().isoformat(), {})
        return int(u.get("prompt", 0) + u.get("completion", 0))

    def set_remaining(self, usd: float) -> dict:
        """The owner reads the balance on the provider's dashboard and reports it. Besides fixing the number, this teaches the
        lab the real price per token: dollars that disappeared since the last report / tokens counted since then."""
        usd = max(0.0, float(usd))
        c = self.s["checkpoint"]
        spend = c["remaining_usd"] - usd
        tokens = self.tokens_total() - c["tokens"]
        learned = None
        if tokens >= 200_000 and spend > 0:
            learned = spend / tokens * 1e6
            prev = self.s["calibrated"]
            self.s["calibrated"] = learned if prev is None else (prev + learned) / 2
        self._checkpoint(usd)
        self._save()
        return {"learned_usd_per_million": learned, "blended": self.blended()}

    def configure(self, **kw) -> None:
        for k, v in kw.items():
            if k in DEFAULTS and v is not None:
                self.cfg[k] = v
        self._save()

    # -- allowance -----------------------------------------------------------------------
    def is_work_day(self) -> bool:
        return self.today().weekday() in self.cfg["work_days"]

    def days_left(self) -> int:
        t = self.today()
        ws = monday(t)
        return sum(1 for i in range(7) if (ws + dt.timedelta(days=i)) >= t and i in self.cfg["work_days"])

    def daily_cap(self) -> float:
        """Total allowance of today (autonomous work + chat reserve). 0 on a non-working day."""
        if not self.is_work_day():
            return 0.0
        return (self.remaining() + self.spent_today()) / max(1, self.days_left())

    def autonomous_cap(self) -> float:
        return self.daily_cap() * (1 - self.cfg["chat_reserve_pct"] / 100)

    def mode(self) -> str:
        """normal | economy | paused_day | paused_weekend | blocked"""
        if self.remaining() <= self.cfg["weekly_usd"] * self.cfg["safety_reserve_pct"] / 100:
            return "blocked"
        if not self.is_work_day() and self.cfg["weekend_pause"]:
            return "paused_weekend"
        if not self.cfg["daily_limit"]:
            return "economy" if self.remaining() <= self.cfg["weekly_usd"] * self.cfg["economy_weekly_pct"] / 100 else "normal"
        cap = self.autonomous_cap()
        spent = self.spent_today()
        if spent >= cap:
            return "paused_day"
        if spent >= cap * self.cfg["economy_at_pct"] / 100:
            return "economy"
        return "normal"

    def allow(self, role: str) -> tuple[bool, str]:
        self._ensure_week()
        m = self.mode()
        if m == "blocked":
            return False, "weekly budget used up (safety reserve reached)"
        if role in AUTONOMOUS_EXEMPT:
            if self.cfg["daily_limit"] and self.daily_cap() and self.spent_today() >= self.daily_cap() and self.is_work_day():
                return False, "today's allowance (including the chat reserve) is used up"
            return True, ""
        if m == "paused_weekend":
            return False, "autonomous work is paused on non-working days"
        if m == "paused_day":
            return False, "today's autonomous allowance is used up"
        return True, ""

    def check(self, role: str) -> None:
        ok, why = self.allow(role)
        if not ok:
            raise BudgetBlocked(why)

    def economy(self) -> bool:
        return self.mode() in ("economy", "paused_day", "blocked")

    def state(self) -> dict:
        self._ensure_week()
        days = sorted(self.s["usage"].items())[-7:]
        return {"mode": self.mode(), "daily_limit": bool(self.cfg["daily_limit"]), "weekly_usd": self.cfg["weekly_usd"], "remaining_usd": round(self.remaining(), 4),
                "daily_cap_usd": round(self.daily_cap(), 4), "autonomous_cap_usd": round(self.autonomous_cap(), 4),
                "spent_today_usd": round(self.spent_today(), 4), "tokens_today": self.tokens_today(),
                "days_left": self.days_left(), "usd_per_million": round(self.blended(), 4),
                "price_source": ("per-model prices" if self.cfg.get("prices") else
                                 "learned from your balance reports" if self.s["calibrated"] else "conservative default (report your balance to calibrate)"),
                "week_start": self.s.get("week_start"),
                "history": [{"date": d, "usd": round(u["usd"], 4), "tokens": u["prompt"] + u["completion"]} for d, u in days]}
