"""Minimal OpenAI-compatible chat client (works with FreeLLMAPI, Ollama, OpenAI, ...).

Configuration comes from the environment, never from the repo:
  OFO_LLM_BASE_URL  e.g. http://localhost:3001/v1
  OFO_LLM_API_KEY   e.g. freellmapi-...
  OFO_LLM_MODEL     default "auto"
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 120, retries: int = 3):
        self.base_url = (base_url or os.environ.get("OFO_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("OFO_LLM_API_KEY", "")
        self.model = model or os.environ.get("OFO_LLM_MODEL", "auto")
        self.timeout, self.retries = timeout, retries
        if not self.base_url:
            raise LLMError("OFO_LLM_BASE_URL is not set")

    def chat(self, messages: list[dict], temperature: float = 0.7, **extra) -> str:
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": temperature, **extra}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise LLMError(f"HTTP {e.code}") from e   # do not retry client errors
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(2 ** attempt)                         # free tiers rate-limit: back off
        raise LLMError(f"LLM unreachable after {self.retries} attempts: {last}")
