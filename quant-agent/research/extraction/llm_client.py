"""Pluggable LLM client for paper understanding / hypothesis generation.

Only one backend is implemented (Anthropic's Messages API via plain HTTP,
so this module has no hard dependency on the `anthropic` SDK being
installed). If no API key is configured, `get_llm_client()` returns `None`
and every caller in `research/extraction` and `agents/hypothesis_agent.py`
is required to fall back to a clearly-labeled rule-based path rather than
silently degrading output quality without saying so.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

import httpx

from config import get_settings

logger = logging.getLogger("quant_agent.llm")


class LLMClient(ABC):
    model: str

    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict:
        """Returns a parsed JSON object. Raises on malformed output rather
        than silently returning a best-effort guess."""
        raise NotImplementedError


class LLMResponseError(RuntimeError):
    pass


class AnthropicLLMClient(LLMClient):
    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        try:
            response = httpx.post(self.endpoint, headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMResponseError(f"LLM request failed: {exc}") from exc

        data = response.json()
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        text = "\n".join(text_blocks).strip()
        text = _strip_code_fence(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM did not return valid JSON: {exc}\nRaw output: {text[:500]}") from exc


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text


def get_llm_client() -> LLMClient | None:
    settings = get_settings()
    if not settings.has_llm:
        return None
    return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.llm_model)
