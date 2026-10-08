"""Gestione segreti: caricamento da .env e redazione di ogni output scritto."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REDACTED = "[REDACTED]"
ENV_KEY = "OMNIROUTE_API_KEY"

_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{6,}"),
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(?!\[REDACTED\])[^\s'\",}]+(?:\s+[^\s'\",}]+)?"),
    re.compile(r"(?i)(" + ENV_KEY + r"['\"]?\s*[:=]\s*['\"]?)(?!\[REDACTED\])[^\s'\",}]+"),
    re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)(?!\[REDACTED\])[^\s'\",}]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
]


def load_env(root: Path | None = None) -> None:
    """Carica .env (unico posto ammesso per la chiave) senza sovrascrivere l'ambiente."""
    env_path = (root or Path.cwd()) / ".env"
    load_dotenv(env_path if env_path.exists() else None, override=False)


def get_api_key(root: Path | None = None) -> str | None:
    load_env(root)
    key = os.getenv(ENV_KEY)
    return key.strip() if key and key.strip() else None


def _literal_secrets(extra: tuple[str, ...] = ()) -> list[str]:
    vals = [os.getenv(ENV_KEY, ""), *extra]
    return [v for v in vals if v and len(v) >= 6]


def redact(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """Sostituisce segreti noti e pattern tipici con [REDACTED]."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for secret in _literal_secrets(extra_secrets):
        out = out.replace(secret, REDACTED)
    for pat in _PATTERNS:
        if pat.groups:
            out = pat.sub(lambda m: m.group(1) + REDACTED, out)
        else:
            out = pat.sub(REDACTED, out)
    return out


def redact_obj(obj: Any, extra_secrets: tuple[str, ...] = ()) -> Any:
    """Redazione ricorsiva di stringhe in dict/list/tuple."""
    if isinstance(obj, str):
        return redact(obj, extra_secrets)
    if isinstance(obj, dict):
        return {k: redact_obj(v, extra_secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, extra_secrets) for v in obj]
    return obj


def contains_secret(text: str) -> bool:
    return isinstance(text, str) and redact(text) != text
