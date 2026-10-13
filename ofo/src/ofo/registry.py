"""Persistent record of every hypothesis ever tested, in SQLite.

It only grows, and its total feeds the Deflated Sharpe: the more variants you try, the higher the bar for the next one.

Why SQLite instead of a JSON file: every write is a transaction (a crash cannot leave half a record), experiments have
unique ids, each study is tied to a fingerprint of the exact dataset it ran on, every look at the hidden
out-of-sample block is stored with who/when/which data, and the same strategy cannot be registered twice on the same
data. The old registry.json is imported once, entry by entry, and left untouched on disk as a backup.

Public API kept from the JSON version: n_trials, data["locked_looks"], add_trials(), add_locked_look()."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, label TEXT, market TEXT,
    spec_fp TEXT, dataset_fp TEXT, n_trials INTEGER NOT NULL, details TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_study ON experiments(spec_fp, dataset_fp)
    WHERE kind = 'study' AND spec_fp IS NOT NULL AND dataset_fp IS NOT NULL;
CREATE TABLE IF NOT EXISTS locked_looks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT, created TEXT NOT NULL, label TEXT, status TEXT,
    dataset_fp TEXT, oos_start TEXT);
"""


class DuplicateExperiment(RuntimeError):
    """This exact strategy was already tested on this exact dataset."""

    def __init__(self, experiment_id: str):
        super().__init__(f"already tested (experiment {experiment_id})")
        self.experiment_id = experiment_id


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class TrialRegistry:
    def __init__(self, path: str | Path):
        p = Path(path)
        self.json_path = p if p.suffix == ".json" else None
        self.db_path = p.with_suffix(".db") if p.suffix == ".json" else p
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None, timeout=30)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")          # a committed experiment survives a power cut
        self._con.executescript(SCHEMA)
        self._migrate_json()

    # -- helpers ------------------------------------------------------------------------
    def _tx(self, fn):
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self._con)
                self._con.execute("COMMIT")
                return out
            except BaseException:
                self._con.execute("ROLLBACK")
                raise

    def _migrate_json(self) -> None:
        """Import the old registry.json once, keeping its labels, counts and order. The file itself is never changed."""
        if self.json_path is None or not self.json_path.exists():
            return
        if self._con.execute("SELECT value FROM meta WHERE key='legacy_imported'").fetchone():
            return
        try:
            old = json.loads(self.json_path.read_text())
        except ValueError:
            return

        def work(con):
            counted = 0
            for e in old.get("log", []):
                if e.get("event") == "trials":
                    con.execute("INSERT INTO experiments(id,created,kind,label,n_trials,details) VALUES (?,?,?,?,?,?)",
                                (uuid.uuid4().hex, "legacy", "legacy", e.get("label"), int(e.get("n", 0)), None))
                    counted += int(e.get("n", 0))
                elif e.get("event") == "locked_look":
                    con.execute("INSERT INTO locked_looks(experiment_id,created,label,status) VALUES (?,?,?,?)",
                                (None, "legacy", e.get("label"), e.get("status")))
            rest = int(old.get("n_trials", 0)) - counted             # never lose a count the log did not itemise
            if rest > 0:
                con.execute("INSERT INTO experiments(id,created,kind,label,n_trials) VALUES (?,?,?,?,?)",
                            (uuid.uuid4().hex, "legacy", "legacy", "legacy (not itemised)", rest))
            looks = con.execute("SELECT COUNT(*) FROM locked_looks").fetchone()[0]
            for _ in range(max(0, int(old.get("locked_looks", 0)) - looks)):
                con.execute("INSERT INTO locked_looks(experiment_id,created,label,status) VALUES (NULL,'legacy','legacy','?')")
            con.execute("INSERT INTO meta(key,value) VALUES ('legacy_imported', ?)", (_now(),))
        self._tx(work)

    # -- reads --------------------------------------------------------------------------
    @property
    def n_trials(self) -> int:
        """Every candidate ever swept plus every peek at the hidden block (a peek is a trial too)."""
        with self._lock:
            n = self._con.execute("SELECT COALESCE(SUM(n_trials),0) FROM experiments").fetchone()[0]
            k = self._con.execute("SELECT COUNT(*) FROM locked_looks").fetchone()[0]
        return int(n) + int(k)

    @property
    def data(self) -> dict:
        """Read-only summary with the keys of the old JSON registry."""
        with self._lock:
            n = self._con.execute("SELECT COALESCE(SUM(n_trials),0) FROM experiments").fetchone()[0]
            k = self._con.execute("SELECT COUNT(*) FROM locked_looks").fetchone()[0]
        return {"n_trials": int(n), "locked_looks": int(k), "log": []}

    def seen(self, spec_fp: str, dataset_fp: str) -> str | None:
        with self._lock:
            r = self._con.execute("SELECT id FROM experiments WHERE kind='study' AND spec_fp=? AND dataset_fp=?",
                                  (spec_fp, dataset_fp)).fetchone()
        return r[0] if r else None

    def locked_looks_on(self, dataset_fp: str) -> int:
        with self._lock:
            return int(self._con.execute("SELECT COUNT(*) FROM locked_looks WHERE dataset_fp=?", (dataset_fp,)).fetchone()[0])

    def experiments(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._con.execute("SELECT id,created,kind,label,market,spec_fp,dataset_fp,n_trials FROM experiments "
                                     "ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        keys = ("id", "created", "kind", "label", "market", "spec_fp", "dataset_fp", "n_trials")
        return [dict(zip(keys, r)) for r in rows]

    # -- writes (each one a transaction) ---------------------------------------------------
    def begin_experiment(self, label: str, n_trials: int, spec_fp: str | None = None, dataset_fp: str | None = None,
                         market: str | None = None, details: dict | None = None, dedupe: bool = False) -> str:
        """Register a study BEFORE it runs. With dedupe=True the same spec on the same dataset raises DuplicateExperiment."""
        def work(con):
            if dedupe and spec_fp and dataset_fp:
                r = con.execute("SELECT id FROM experiments WHERE kind='study' AND spec_fp=? AND dataset_fp=?",
                                (spec_fp, dataset_fp)).fetchone()
                if r:
                    raise DuplicateExperiment(r[0])
            eid = uuid.uuid4().hex
            con.execute("INSERT INTO experiments(id,created,kind,label,market,spec_fp,dataset_fp,n_trials,details) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (eid, _now(), "study" if (spec_fp and dataset_fp and dedupe) else "trials", label, market,
                         spec_fp if dedupe else None, dataset_fp, int(n_trials), json.dumps(details) if details else None))
            return eid
        return self._tx(work)

    def add_trials(self, n: int, label: str, experiment_id: str | None = None, dataset_fp: str | None = None,
                   market: str | None = None, kind: str = "trials") -> str:
        def work(con):
            eid = uuid.uuid4().hex
            con.execute("INSERT INTO experiments(id,created,kind,label,market,dataset_fp,n_trials,details) VALUES (?,?,?,?,?,?,?,?)",
                        (eid, _now(), kind, label, market, dataset_fp, int(n),
                         json.dumps({"parent": experiment_id}) if experiment_id else None))
            return eid
        return self._tx(work)

    def add_locked_look(self, label: str, status: str, experiment_id: str | None = None, dataset_fp: str | None = None,
                        oos_start: str | None = None) -> int:
        """Every peek at the hidden block is itself a trial, recorded with the experiment, the data and the cut."""
        def work(con):
            cur = con.execute("INSERT INTO locked_looks(experiment_id,created,label,status,dataset_fp,oos_start) VALUES (?,?,?,?,?,?)",
                              (experiment_id, _now(), label, status, dataset_fp, oos_start))
            return cur.lastrowid
        return self._tx(work)

    def close(self) -> None:
        with self._lock:
            self._con.close()
