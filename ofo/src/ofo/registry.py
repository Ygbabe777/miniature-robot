"""Persistent count of every hypothesis ever tested. It only grows, and it feeds the
Deflated Sharpe: the more variants you try, the higher the bar for the next one."""
from __future__ import annotations

import json
from pathlib import Path


class TrialRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "n_trials": 0, "locked_looks": 0, "log": []}

    @property
    def n_trials(self) -> int:
        return self.data["n_trials"] + self.data["locked_looks"]

    def add_trials(self, n: int, label: str) -> None:
        self.data["n_trials"] += n
        self.data["log"].append({"event": "trials", "n": n, "label": label})
        self._save()

    def add_locked_look(self, label: str, status: str) -> None:
        """Every peek at the locked set is itself a trial."""
        self.data["locked_looks"] += 1
        self.data["log"].append({"event": "locked_look", "label": label, "status": status})
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))
