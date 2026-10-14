"""Model pool: spend tokens deliberately.

 - roles map to ordered lists of models (cheap/fast for bulk reading, stronger for judgement)
 - requests rotate round-robin inside a role to spread load across free-tier quotas
 - a model that hits a rate limit / error is put on cooldown and the next one is used
 - identical low-temperature requests are served from a disk cache (zero tokens)
 - a daily token ledger per model and per role, with optional per-role budgets
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path

from .client import LLMClient, LLMError, RateLimited


class BudgetExceeded(LLMError):
    pass


class CallLimitReached(LLMError):
    """The hourly call allowance is used up and waiting would take longer than allowed."""


class ModelPool:
    def __init__(self, client: LLMClient, roles: dict[str, list[str]], ledger_path: str | Path,
                 cache_dir: str | Path | None = None, daily_token_budget: dict[str, int] | None = None,
                 cooldown_base: float = 30.0, now=time.time, sleep=time.sleep, max_wait: float = 120.0,
                 calls_per_hour: int | None = None, role_calls_per_hour: dict[str, int] | None = None,
                 call_wait_max: float = 0.0, calls_per_minute: int | None = None,
                 role_calls_per_minute: dict[str, int] | None = None, governor=None):
        self.client, self.roles = client, roles
        self.governor = governor                 # budget.BudgetGovernor: dollars per working week, split over the days
        self.ledger_path = Path(ledger_path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.budget = daily_token_budget or {}
        self.cooldown_base, self.now, self.sleep, self.max_wait = cooldown_base, now, sleep, max_wait
        self.calls_per_hour, self.role_calls = calls_per_hour, role_calls_per_hour or {}
        self.calls_per_minute, self.role_calls_min = calls_per_minute, role_calls_per_minute or {}
        self.call_wait_max = call_wait_max      # 0 = fail fast (interactive); 3700 = wait (background loop)
        self.cooldown: dict[str, float] = {}
        self.fails: dict[str, int] = {}
        self.rr: dict[str, int] = {}
        today = dt.date.today().isoformat()
        led = json.loads(self.ledger_path.read_text(encoding="utf-8", errors="ofo_legacy")) if self.ledger_path.exists() else {}
        self.calls: list[list] = [c for c in led.get("calls", []) if c[0] > self.now() - 3600]
        self.ledger = led if led.get("date") == today else {"date": today, "models": {}, "roles": {}}

    # --- accounting -------------------------------------------------------------------
    def _save(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger["calls"] = self.calls
        self.ledger_path.write_text(json.dumps(self.ledger, indent=1), encoding="utf-8")

    # --- hourly call allowance (every attempt counts, successful or not) -------------------
    def calls_last_hour(self, role: str | None = None) -> int:
        return self._count(3600, role)

    def calls_last_minute(self, role: str | None = None) -> int:
        return self._count(60, role)

    def _count(self, seconds: int, role: str | None) -> int:
        cut = self.now() - seconds
        return sum(1 for t, r in self.calls if t > cut and (role is None or r == role))

    def _limits(self, role: str) -> list[tuple[int, int, str | None]]:
        """(window seconds, max calls, role filter). Every attempt counts, successful or not."""
        out = []
        if self.calls_per_minute is not None:
            out.append((60, self.calls_per_minute, None))
        if self.role_calls_min.get(role) is not None:
            out.append((60, self.role_calls_min[role], role))
        if self.calls_per_hour is not None:
            out.append((3600, self.calls_per_hour, None))
        if self.role_calls.get(role) is not None:
            out.append((3600, self.role_calls[role], role))
        return out

    def _wait_for_call_slot(self, role: str) -> None:
        while True:
            self.calls = [c for c in self.calls if c[0] > self.now() - 3600]
            waits = []
            for seconds, cap, who in self._limits(role):
                rel = [c for c in self.calls if c[0] > self.now() - seconds and (who is None or c[1] == who)]
                if len(rel) >= cap:
                    waits.append(rel[-cap][0] + seconds - self.now())
            if not waits:
                return
            wait = max(waits)
            if wait > self.call_wait_max:
                eta = f"{wait:.0f} s" if wait < 120 else f"{wait / 60:.0f} min"
                raise CallLimitReached(f"call allowance used up; next call possible in {eta}")
            self.sleep(wait + 1)

    def _record(self, role: str, model: str, r: dict | None, failed: bool = False) -> None:
        m = self.ledger["models"].setdefault(model, {"calls": 0, "prompt": 0, "completion": 0, "failures": 0, "seconds": 0.0})
        if failed:
            m["failures"] += 1
        else:
            m["calls"] += 1
            m["prompt"] += r["prompt_tokens"]; m["completion"] += r["completion_tokens"]; m["seconds"] += r["seconds"]
            if self.governor is not None:
                self.governor.record(role, model, r["prompt_tokens"], r["completion_tokens"])
            self.ledger["roles"][role] = self.ledger["roles"].get(role, 0) + r["prompt_tokens"] + r["completion_tokens"]
        self._save()

    def tokens_today(self, role: str) -> int:
        return self.ledger["roles"].get(role, 0)

    # --- cache --------------------------------------------------------------------------
    def _cache_path(self, role: str, messages, temperature) -> Path | None:
        if self.cache_dir is None or temperature > 0.3:
            return None
        key = hashlib.sha256(json.dumps([role, messages], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    # --- main entry ---------------------------------------------------------------------
    def chat(self, role: str, messages: list[dict], temperature: float = 0.2, **extra) -> str:
        cp = self._cache_path(role, messages, temperature)
        if cp is not None and cp.exists():
            return json.loads(cp.read_text(encoding="utf-8", errors="ofo_legacy"))["content"]
        cap = self.budget.get(role)
        if cap is not None and self.tokens_today(role) >= cap:
            raise BudgetExceeded(f"role {role!r} used its daily budget of {cap} tokens")
        if self.governor is not None:
            self.governor.check(role)               # raises BudgetBlocked (an LLMError) when today's allowance is gone
        models = self.roles[role]
        waited = 0.0
        while True:
            start = self.rr.get(role, 0)
            order = [models[(start + i) % len(models)] for i in range(len(models))]
            ready = [m for m in order if self.cooldown.get(m, 0) <= self.now()]
            for model in ready:
                self.rr[role] = (models.index(model) + 1) % len(models)
                self._wait_for_call_slot(role)
                self.calls.append([self.now(), role])
                try:
                    r = self.client.chat_once(messages, model=model, temperature=temperature, **extra)
                except RateLimited as e:
                    self._cool(model, e.retry_after)
                    self._record(role, model, None, failed=True)
                    continue
                except LLMError as e:
                    # retryable (5xx / network): short cooldown. Client errors (bad model id,
                    # bad key): long cooldown so we stop wasting calls on it.
                    self._cool(model, None if e.retryable else 3600.0)
                    self._record(role, model, None, failed=True)
                    continue
                self.fails[model] = 0
                self._record(role, model, r)
                if cp is not None:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_text(json.dumps({"content": r["content"], "model": model}), encoding="utf-8")
                return r["content"]
            soonest = min(self.cooldown.get(m, 0) for m in models) - self.now()
            if waited + max(soonest, 0) > self.max_wait or soonest > self.max_wait:
                raise LLMError(f"all models for role {role!r} are cooling down or failing")
            wait = max(soonest, 1.0)
            self.sleep(wait); waited += wait

    def _cool(self, model: str, retry_after: float | None) -> None:
        n = self.fails[model] = self.fails.get(model, 0) + 1
        self.cooldown[model] = self.now() + (retry_after if retry_after else self.cooldown_base * 2 ** (n - 1))

    def for_role(self, role: str) -> "RoleLLM":
        return RoleLLM(self, role)


class RoleLLM:
    """Adapter: exposes .chat(messages, temperature) so agents need not know about roles."""
    def __init__(self, pool: ModelPool, role: str):
        self.pool, self.role = pool, role

    def chat(self, messages, temperature: float = 0.2, **kw) -> str:
        return self.pool.chat(self.role, messages, temperature, **kw)


def build_pool(config_path: str | Path, ledger_path="data/llm_usage.json", cache_dir="data/llm_cache",
               budget_state="data/budget.json"):
    """config/llm.json: {"roles": {"researcher": [models], "planner": [...], "auditor": [...]},
    "daily_token_budget": {"researcher": 300000}, "cooldown_seconds": 30}. Keys stay in env vars."""
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8", errors="ofo_legacy"))
    bcfg = cfg.get("budget")
    bfile = Path(config_path).with_name("budget.json")          # config/budget.json: weekly dollars, prices, remaining balance
    if bcfg is None and bfile.exists():
        bcfg = json.loads(bfile.read_text(encoding="utf-8", errors="ofo_legacy"))
    from ..budget import BudgetGovernor
    governor = BudgetGovernor(budget_state, bcfg) if bcfg is not None else None
    pool = ModelPool(LLMClient(), cfg["roles"], ledger_path, cache_dir, cfg.get("daily_token_budget"), governor=governor,
                     cooldown_base=cfg.get("cooldown_seconds", 30.0),
                     calls_per_hour=cfg.get("calls_per_hour"), role_calls_per_hour=cfg.get("role_calls_per_hour"),
                     calls_per_minute=cfg.get("calls_per_minute"), role_calls_per_minute=cfg.get("role_calls_per_minute"),
                     call_wait_max=cfg.get("wait_for_call_slot_seconds", 0.0))
    return pool, {role: pool.for_role(role) for role in cfg["roles"]}
