"""Logging strutturato JSON con redazione dei segreti."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .security import redact


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "module": record.name,
            "severity": record.levelname,
            "run_id": getattr(record, "run_id", None),
            "agent": getattr(record, "agent", None),
            "event": getattr(record, "event", None),
            "message": redact(record.getMessage()),
        }
        return json.dumps(payload, ensure_ascii=False)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        record.args = ()
        return True


def setup_logging(verbose: bool = False, log_dir: Path | None = None) -> None:
    root = logging.getLogger("ofo")
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False
    fmt = RedactingJsonFormatter()
    if verbose:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.addFilter(RedactingFilter())
        root.addHandler(sh)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "ofo.log", encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(RedactingFilter())
        root.addHandler(fh)
    if not root.handlers:
        root.addHandler(logging.NullHandler())


class RunLogger:
    """Adapter che aggiunge run_id/agent/event a ogni record."""

    def __init__(self, module: str, run_id: str | None = None) -> None:
        self._log = logging.getLogger(f"ofo.{module}")
        self.run_id = run_id

    def log(self, level: int, event: str, message: str = "", agent: str | None = None) -> None:
        self._log.log(level, message or event,
                      extra={"run_id": self.run_id, "agent": agent, "event": event})

    def info(self, event: str, message: str = "", agent: str | None = None) -> None:
        self.log(logging.INFO, event, message, agent)

    def warning(self, event: str, message: str = "", agent: str | None = None) -> None:
        self.log(logging.WARNING, event, message, agent)

    def error(self, event: str, message: str = "", agent: str | None = None) -> None:
        self.log(logging.ERROR, event, message, agent)

    def debug(self, event: str, message: str = "", agent: str | None = None) -> None:
        self.log(logging.DEBUG, event, message, agent)
