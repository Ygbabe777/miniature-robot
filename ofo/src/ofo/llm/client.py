"""OpenAI-compatible chat client (FreeLLMAPI, Angeld, Ollama, OpenAI, ...).

Configuration comes from the environment, never from the repo:
  OFO_LLM_BASE_URL  e.g. http://localhost:3001/v1
  OFO_LLM_API_KEY
  OFO_LLM_MODEL     default "auto"
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None, retryable: bool = False):
        super().__init__(msg)
        self.status, self.retryable = status, retryable


class RateLimited(LLMError):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg, 429, True)
        self.retry_after = retry_after


def _retry_after(headers) -> float | None:
    try:
        return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 120, retries: int = 3):
        self.base_url = (base_url or os.environ.get("OFO_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("OFO_LLM_API_KEY", "")
        self.model = model or os.environ.get("OFO_LLM_MODEL", "auto")
        self.timeout, self.retries = timeout, retries
        if not self.base_url:
            raise LLMError("OFO_LLM_BASE_URL is not set")

    def _request(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited("HTTP 429", _retry_after(e.headers)) from e
            raise LLMError(f"HTTP {e.code}", e.code, e.code in (500, 502, 503, 504)) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LLMError(f"unreachable: {e}", None, True) from e

    def chat_once(self, messages: list[dict], model: str | None = None,
                  temperature: float = 0.7, **extra) -> dict:
        """One attempt, no retry. Returns {content, model, prompt_tokens, completion_tokens, seconds}."""
        t = time.time()
        r = self._request("/chat/completions", {"model": model or self.model, "messages": messages,
                                                "temperature": temperature, **extra})
        u = r.get("usage") or {}
        return {"content": r["choices"][0]["message"]["content"], "model": r.get("model", model or self.model),
                "prompt_tokens": int(u.get("prompt_tokens", 0)),
                "completion_tokens": int(u.get("completion_tokens", 0)), "seconds": time.time() - t}

    def chat(self, messages: list[dict], temperature: float = 0.7, **extra) -> str:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self.chat_once(messages, temperature=temperature, **extra)["content"]
            except RateLimited as e:
                last, wait = e, e.retry_after or 2 ** attempt
            except LLMError as e:
                if not e.retryable:
                    raise
                last, wait = e, 2 ** attempt
            time.sleep(min(wait, 60))                      # free tiers rate-limit: back off
        raise LLMError(f"LLM unavailable after {self.retries} attempts: {last}")

    def list_models(self) -> list[str]:
        r = self._request("/models")
        return sorted(m["id"] for m in r.get("data", []) if "id" in m)
