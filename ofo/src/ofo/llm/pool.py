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


class ModelPool:
    def __init__(self, client: LLMClient, roles: dict[str, list[str]], ledger_path: str | Path,
                 cache_dir: str | Path | None = None, daily_token_budget: dict[str, int] | None = None,
                 cooldown_base: float = 30.0, now=time.time, sleep=time.sleep, max_wait: float = 120.0):
        self.client, self.roles = client, roles
        self.ledger_path = Path(ledger_path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.budget = daily_token_budget or {}
        self.cooldown_base, self.now, self.sleep, self.max_wait = cooldown_base, now, sleep, max_wait
        self.cooldown: dict[str, float] = {}
        self.fails: dict[str, int] = {}
        self.rr: dict[str, int] = {}
        today = dt.date.today().isoformat()
        led = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {}
        self.ledger = led if led.get("date") == today else {"date": today, "models": {}, "roles": {}}

    # --- accounting -------------------------------------------------------------------
    def _save(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(self.ledger, indent=1))

    def _record(self, role: str, model: str, r: dict | None, failed: bool = False) -> None:
        m = self.ledger["models"].setdefault(model, {"calls": 0, "prompt": 0, "completion": 0, "failures": 0, "seconds": 0.0})
        if failed:
            m["failures"] += 1
        else:
            m["calls"] += 1
            m["prompt"] += r["prompt_tokens"]; m["completion"] += r["completion_tokens"]; m["seconds"] += r["seconds"]
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
            return json.loads(cp.read_text())["content"]
        cap = self.budget.get(role)
        if cap is not None and self.tokens_today(role) >= cap:
            raise BudgetExceeded(f"role {role!r} used its daily budget of {cap} tokens")
        models = self.roles[role]
        waited = 0.0
        while True:
            start = self.rr.get(role, 0)
            order = [models[(start + i) % len(models)] for i in range(len(models))]
            ready = [m for m in order if self.cooldown.get(m, 0) <= self.now()]
            for model in ready:
                self.rr[role] = (models.index(model) + 1) % len(models)
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
                    cp.write_text(json.dumps({"content": r["content"], "model": model}))
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


def build_pool(config_path: str | Path, ledger_path="data/llm_usage.json", cache_dir="data/llm_cache"):
    """config/llm.json: {"roles": {"researcher": [models], "planner": [...], "auditor": [...]},
    "daily_token_budget": {"researcher": 300000}, "cooldown_seconds": 30}. Keys stay in env vars."""
    cfg = json.loads(Path(config_path).read_text())
    pool = ModelPool(LLMClient(), cfg["roles"], ledger_path, cache_dir, cfg.get("daily_token_budget"),
                     cooldown_base=cfg.get("cooldown_seconds", 30.0))
    return pool, {role: pool.for_role(role) for role in cfg["roles"]}
