"""Append-only event log (JSON lines). The 3D Lab UI will tail this file: every agent
action, study result and verdict is one line, so what you watch is what really happened."""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._w = threading.Lock()          # many threads emit (scout, hypotheses, studies): lines must never interleave
        self.life: dict[str, str] = {}      # last working/done/idle/error per agent, for the live state view

    def emit(self, agent: str, type_: str, message: str = "", data: dict | None = None) -> None:
        line = {"t": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "agent": agent, "type": type_, "message": message, "data": data or {}}
        if type_ in ("working", "done", "idle", "error"):
            self.life[agent] = type_
        with self._w, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

    def alert(self, agent: str, key: str, message: str, cooldown_s: float = 1800.0) -> bool:
        """Something of EXTREME importance, said to the owner out loud (speech bubble + toast). The same key is not repeated within
        the cooldown: an agent that keeps failing must not shout all day. Everything routine stays a quiet event."""
        import time
        last = self.__dict__.setdefault("_alerts", {})
        if time.time() - last.get(key, 0.0) < cooldown_s:
            return False
        last[key] = time.time()
        self.emit(agent, "alert", message)
        return True

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for x in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not x.strip():
                continue
            try:
                out.append(json.loads(x))
            except ValueError:
                continue                    # one damaged line (crash, half-written) must not take the whole feed down with a 500
        return out

    def tail(self, n: int = 2000) -> list[dict]:
        """The last n events without parsing a log that may have grown for months."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="ofo_legacy").splitlines()[-n:]
        out = []
        for x in lines:
            try:
                out.append(json.loads(x))
            except ValueError:
                continue
        return out
