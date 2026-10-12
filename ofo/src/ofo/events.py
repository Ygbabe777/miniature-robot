"""Append-only event log (JSON lines). The 3D Lab UI will tail this file: every agent
action, study result and verdict is one line, so what you watch is what really happened."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, agent: str, type_: str, message: str = "", data: dict | None = None) -> None:
        line = {"t": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "agent": agent, "type": type_, "message": message, "data": data or {}}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]
