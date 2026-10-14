"""Agent plumbing: load a prompt file, call the LLM, validate the JSON, and when a weak
model gets it wrong, show it the exact error and let it retry. Replies that never validate
raise AgentError; they are never executed or trusted."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

PROMPT_DIR = Path(__file__).parent / "prompts"


class AgentError(RuntimeError):
    pass


def load_prompt(name: str, **subs: str) -> str:
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8", errors="ofo_legacy")
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        raise ValueError(f"prompt {name!r} has unfilled placeholders")
    return text


def parse_json(reply: str) -> dict:
    a, b = reply.find("{"), reply.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in reply")
    return json.loads(reply[a:b + 1])


class Agent:
    def __init__(self, name: str, llm, system: str, validate: Callable[[dict], dict],
                 events=None, max_attempts: int = 3, temperature: float = 0.2):
        self.name, self.llm, self.system, self.validate = name, llm, system, validate
        self.events, self.max_attempts, self.temperature = events, max_attempts, temperature

    @staticmethod
    def _preview(text: str, n: int = 90) -> str:
        t = " ".join(str(text).replace('"', " ").replace("{", " ").replace("}", " ").split())
        return t[:n] + ("…" if len(t) > n else "")

    @staticmethod
    def _brief(out) -> str:
        if isinstance(out, dict):
            for k in ("name", "summary", "title", "decision", "verdict", "reply", "text"):
                if isinstance(out.get(k), str) and out[k]:
                    return Agent._preview(out[k], 100)
            if isinstance(out.get("spec"), object) and getattr(out.get("spec"), "name", None):
                return Agent._preview(out["spec"].name, 100)
        return ""

    def run(self, payload, topic: str | None = None) -> dict:
        user = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        last = None
        if self.events:
            self.events.emit(self.name, "working", "thinking: " + (topic or self._preview(user)))
        for attempt in range(1, self.max_attempts + 1):
            try:
                reply = self.llm.chat(messages, temperature=self.temperature)
            except Exception:
                if self.events:
                    self.events.emit(self.name, "idle", "llm unavailable")      # never stay "working" after a crash
                raise
            try:
                out = self.validate(parse_json(reply))
                if self.events:
                    self.events.emit(self.name, "reply", f"valid reply (attempt {attempt})" + (": " + self._brief(out) if self._brief(out) else ""))
                    self.events.emit(self.name, "done", "ready")
                return out
            except Exception as e:                       # weak models: bad JSON, bad values
                last = e
                if self.events:
                    self.events.emit(self.name, "invalid_reply", str(e)[:300])
                messages += [{"role": "assistant", "content": reply},
                             {"role": "user", "content": f"Your reply was rejected: {e}. "
                              "Reply again with ONLY the corrected JSON object."}]
        if self.events:
            self.events.emit(self.name, "idle", "no valid reply")
        raise AgentError(f"{self.name}: no valid reply after {self.max_attempts} attempts: {last}")
