"""Motore LLM: client OpenAI-compatibile (OmniRoute), retry con backoff+jitter, fallback,
estrazione/validazione JSON strict. Mai risposte inventate: se fallisce, l'agente e' FAILED.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import requests
from pydantic import ValidationError

from .config import AppConfig, OmniRouteCfg
from .schemas import OUTPUT_MODELS, AgentStatus, _AgentBase
from .security import redact


# --------------------------------------------------------------------------- errori / tipi
class LLMError(Exception):
    """Errore del trasporto LLM. `retryable` guida il backoff; il messaggio e' gia' redatto."""

    def __init__(self, message: str, *, status_code: int | None = None, kind: str = "http",
                 retryable: bool = False) -> None:
        super().__init__(redact(message))
        self.status_code = status_code
        self.kind = kind
        self.retryable = retryable


@dataclass
class LLMResponse:
    content: str
    model: str


class LLMClient(Protocol):
    def complete(self, *, agent: str, model: str, messages: list[dict[str, str]],
                 timeout: float) -> LLMResponse: ...


class OmniRouteClient:
    """Client HTTP OpenAI-compatibile. L'API key non viene mai loggata ne' inclusa in errori."""

    def __init__(self, cfg: OmniRouteCfg, api_key: str | None, session: requests.Session | None = None) -> None:
        self._cfg = cfg
        self._key = api_key
        self._session = session or requests.Session()

    def complete(self, *, agent: str, model: str, messages: list[dict[str, str]],
                 timeout: float) -> LLMResponse:
        if not self._key:
            raise LLMError("OMNIROUTE_API_KEY mancante: impostarla in .env", kind="auth", retryable=False)
        url = self._cfg.base_url.rstrip("/") + self._cfg.chat_path
        body = {"model": model, "messages": messages, "temperature": 0.1,
                "response_format": {"type": "json_object"}}
        try:
            resp = self._session.post(url, json=body, timeout=timeout,
                                      headers={"Authorization": f"Bearer {self._key}",
                                               "Content-Type": "application/json"})
        except requests.Timeout:
            raise LLMError(f"timeout dopo {timeout}s", kind="timeout", retryable=True) from None
        except requests.ConnectionError:
            raise LLMError("connessione a OmniRoute non riuscita", kind="connection", retryable=True) from None
        except requests.RequestException as exc:
            raise LLMError(f"errore di rete: {type(exc).__name__}", kind="connection", retryable=True) from None
        if resp.status_code != 200:
            retryable = resp.status_code in self._cfg.retry_status_codes
            raise LLMError(f"HTTP {resp.status_code}", status_code=resp.status_code, kind="http",
                           retryable=retryable)
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            actual = data.get("model") or model
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("risposta OmniRoute malformata", kind="invalid", retryable=False) from None
        return LLMResponse(content=content or "", model=actual)


# --------------------------------------------------------------------------- retry / fallback
@dataclass
class Attempt:
    model: str
    attempt: int
    outcome: str  # "ok" | errore sintetico
    delay_s: float = 0.0


@dataclass
class CallRecord:
    requested_model: str
    actual_model: str | None = None
    fallback_used: bool = False
    failure_reason: str | None = None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def retry_count(self) -> int:
        return sum(1 for a in self.attempts if a.delay_s > 0)


class AllModelsFailed(Exception):
    def __init__(self, record: CallRecord) -> None:
        self.record = record
        super().__init__(record.failure_reason or "tutti i modelli hanno fallito")


def backoff_delay(attempt: int, cfg: OmniRouteCfg, rng: random.Random) -> float:
    """delay = base * 2^attempt + jitter, limitato a backoff_max."""
    return min(cfg.backoff_max_seconds, cfg.backoff_base_seconds * (2 ** attempt)) + rng.uniform(0, cfg.jitter_seconds)


def call_with_fallback(client: LLMClient, *, agent: str, models: list[str], messages: list[dict[str, str]],
                       cfg: OmniRouteCfg, sleep: Callable[[float], None] = time.sleep,
                       rng: random.Random | None = None) -> tuple[LLMResponse, CallRecord]:
    """requested -> fallback -> fallimento. Ogni passaggio e' registrato (mai silenzioso)."""
    rng = rng or random.Random()
    record = CallRecord(requested_model=models[0])
    reasons: list[str] = []
    for m_idx, model in enumerate(models):
        for attempt in range(cfg.max_retries + 1):
            try:
                resp = client.complete(agent=agent, model=model, messages=messages, timeout=cfg.timeout_seconds)
            except LLMError as exc:
                outcome = f"{exc.kind}:{exc.status_code or '-'}"
                if exc.kind == "auth":
                    record.attempts.append(Attempt(model, attempt, outcome))
                    record.failure_reason = redact(str(exc))
                    raise AllModelsFailed(record) from None  # inutile provare altri modelli
                if exc.retryable and attempt < cfg.max_retries:
                    delay = backoff_delay(attempt, cfg, rng)
                    record.attempts.append(Attempt(model, attempt, outcome, delay))
                    sleep(delay)
                    continue
                record.attempts.append(Attempt(model, attempt, outcome))
                reasons.append(f"{model}: {redact(str(exc))} ({outcome})")
                break
            else:
                record.attempts.append(Attempt(model, attempt, "ok"))
                record.actual_model = resp.model if resp.model else model
                record.fallback_used = m_idx > 0
                if record.fallback_used:
                    record.failure_reason = "; ".join(reasons)
                return resp, record
    record.failure_reason = "; ".join(reasons) or "nessun modello disponibile"
    raise AllModelsFailed(record)


# --------------------------------------------------------------------------- JSON
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any] | None:
    """Estrae il primo oggetto JSON valido da testo con fence/prosa. None se impossibile."""
    if not text:
        return None
    candidates = [text.strip()] + [m.strip() for m in _FENCE.findall(text)]
    for cand in candidates:
        try:
            val = json.loads(cand)
            if isinstance(val, dict):
                return val
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                val, _ = decoder.raw_decode(text[i:])
                if isinstance(val, dict):
                    return val
            except ValueError:
                continue
    return None


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- Prompt
@dataclass(frozen=True)
class Prompt:
    agent: str
    text: str
    version: str  # "1.0:abcdef012345"


def load_prompt(prompts_dir: Path, agent: str) -> Prompt:
    text = (prompts_dir / f"{agent}.txt").read_text(encoding="utf-8")
    m = re.match(r"#\s*version:\s*(\S+)", text)
    ver = m.group(1) if m else "0"
    return Prompt(agent, text, f"{ver}:{hashlib.sha256(text.encode()).hexdigest()[:12]}")


# --------------------------------------------------------------------------- Runner
@dataclass
class AgentRunMeta:
    agent: str
    requested_model: str
    actual_model: str | None
    fallback_used: bool
    failure_reason: str | None
    retry_count: int
    json_correction_used: bool
    validation_status: str  # VALID | INVALID | NOT_RUN
    input_hash: str
    duration_ms: int
    errors: list[str]
    prompt_version: str
    attempts: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class AgentResult:
    agent: str
    status: AgentStatus
    output: _AgentBase | None
    meta: AgentRunMeta

    @property
    def ok(self) -> bool:
        return self.output is not None and self.status != AgentStatus.FAILED


class AgentRunner:
    """Esegue un agente: chiamata -> parsing -> validazione -> 1 retry di correzione -> FAILED."""

    def __init__(self, cfg: AppConfig, client: LLMClient, prompts_dir: Path, *,
                 clock: Any, sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None) -> None:
        self.cfg, self.client, self.prompts_dir = cfg, client, prompts_dir
        self.clock, self.sleep, self.rng = clock, sleep, rng or random.Random()

    def run(self, agent: str, payload: dict[str, Any]) -> AgentResult:
        prompt = load_prompt(self.prompts_dir, agent)
        model = self.cfg.model_for(agent)
        models = [model] + ([fb] if (fb := self.cfg.fallback_for(agent)) else [])
        messages = [{"role": "system", "content": prompt.text},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]
        t0 = self.clock.monotonic()
        errors: list[str] = []
        correction = False
        record: CallRecord | None = None
        out: _AgentBase | None = None
        validation = "NOT_RUN"
        try:
            resp, record = call_with_fallback(self.client, agent=agent, models=models, messages=messages,
                                              cfg=self.cfg.omniroute, sleep=self.sleep, rng=self.rng)
            out, err = self._parse(agent, resp.content)
            validation = "VALID" if out else "INVALID"
            if out is None:
                errors.append(f"JSON non valido: {err}")
                correction = True
                fix = messages + [
                    {"role": "assistant", "content": resp.content[:4000]},
                    {"role": "user", "content": (
                        "La risposta precedente non è valida (" + err[:300] + "). Rispondi di nuovo SOLO con "
                        "un unico oggetto JSON valido che rispetti lo schema richiesto, senza testo aggiuntivo.")},
                ]
                resp2, rec2 = call_with_fallback(self.client, agent=agent, models=[record.actual_model or model],
                                                 messages=fix, cfg=self.cfg.omniroute, sleep=self.sleep, rng=self.rng)
                record.attempts.extend(rec2.attempts)
                out, err2 = self._parse(agent, resp2.content)
                validation = "VALID" if out else "INVALID"
                if out is None:
                    errors.append(f"JSON ancora non valido dopo correzione: {err2}")
        except AllModelsFailed as exc:
            record = exc.record
            errors.append(f"LLM non disponibile: {redact(str(exc))}")
        status = AgentStatus.FAILED if out is None else out.status
        if out is not None and out.agent != agent:
            out = out.model_copy(update={"agent": agent})  # l'identita' la decide il sistema
        meta = AgentRunMeta(
            agent=agent, requested_model=model,
            actual_model=record.actual_model if record else None,
            fallback_used=bool(record and record.fallback_used),
            failure_reason=redact(record.failure_reason) if record and record.failure_reason else None,
            retry_count=record.retry_count if record else 0,
            json_correction_used=correction, validation_status=validation,
            input_hash=canonical_hash(payload),
            duration_ms=int((self.clock.monotonic() - t0) * 1000),
            errors=[redact(e) for e in errors], prompt_version=prompt.version,
            attempts=[a.__dict__ for a in (record.attempts if record else [])],
        )
        return AgentResult(agent, status, out, meta)

    @staticmethod
    def _parse(agent: str, text: str) -> tuple[_AgentBase | None, str]:
        data = extract_json(text)
        if data is None:
            return None, "nessun oggetto JSON trovato"
        try:
            return OUTPUT_MODELS[agent].model_validate(data), ""
        except ValidationError as exc:
            first = exc.errors()[0]
            return None, f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
