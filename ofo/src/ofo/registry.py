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
        self._migrate_schema()
        self._migrate_json()

    # -- schema ---------------------------------------------------------------------------
    NEW_COLUMNS = {"status": "TEXT", "stage": "TEXT", "family": "TEXT", "hyp_fp": "TEXT", "n_evals": "INTEGER", "executed": "INTEGER",
                   "compute_s": "REAL", "code_version": "TEXT", "config_version": "TEXT", "cost_model": "TEXT", "reject_reason": "TEXT",
                   "results": "TEXT"}

    def _migrate_schema(self) -> None:
        """Add the research-engine columns to a database created by an older version, never touching its rows' meaning:
        old experiments become status 'completed' with n_evals = n_trials."""
        cols = {r[1] for r in self._con.execute("PRAGMA table_info(experiments)")}
        for name, typ in self.NEW_COLUMNS.items():
            if name not in cols:
                self._con.execute(f"ALTER TABLE experiments ADD COLUMN {name} {typ}")
        self._con.execute("UPDATE experiments SET status='completed' WHERE status IS NULL")
        self._con.execute("UPDATE experiments SET n_evals=n_trials WHERE n_evals IS NULL")
        # a study blocks a duplicate only while it is running or finished: a crashed one (status 'failed') can be run again
        self._con.execute("DROP INDEX IF EXISTS uq_study")
        self._con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_study2 ON experiments(spec_fp, dataset_fp) WHERE kind='study' "
                          "AND status IN ('running','completed','early_stopped') AND spec_fp IS NOT NULL AND dataset_fp IS NOT NULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0)")

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
            r = self._con.execute("SELECT id FROM experiments WHERE kind='study' AND spec_fp=? AND dataset_fp=? "
                                  "AND status IN ('running','completed','early_stopped')", (spec_fp, dataset_fp)).fetchone()
        return r[0] if r else None

    def locked_looks_on(self, dataset_fp: str) -> int:
        with self._lock:
            return int(self._con.execute("SELECT COUNT(*) FROM locked_looks WHERE dataset_fp=?", (dataset_fp,)).fetchone()[0])

    def experiments(self, limit: int = 50) -> list[dict]:
        keys = ("id", "created", "kind", "label", "market", "spec_fp", "dataset_fp", "n_trials", "status", "stage", "family", "hyp_fp",
                "n_evals", "executed", "compute_s", "code_version", "config_version", "cost_model", "reject_reason")
        with self._lock:
            rows = self._con.execute(f"SELECT {','.join(keys)} FROM experiments ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(keys, r)) for r in rows]

    def experiment(self, eid: str) -> dict | None:
        keys = ("id", "created", "kind", "label", "market", "spec_fp", "dataset_fp", "n_trials", "status", "stage", "family", "hyp_fp",
                "n_evals", "executed", "compute_s", "code_version", "config_version", "cost_model", "reject_reason", "details", "results")
        with self._lock:
            r = self._con.execute(f"SELECT {','.join(keys)} FROM experiments WHERE id=?", (eid,)).fetchone()
        if not r:
            return None
        d = dict(zip(keys, r))
        for k in ("details", "results"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def usage(self, day: str | None = None, group: str = "stage") -> dict:
        """Budget actually consumed, from the registry itself (so it can never disagree with the trial count that feeds the DSR).
        group: stage | family | kind. Returns {group_value: {"trials", "evals", "compute_s", "n"}}; `day` = 'YYYY-MM-DD' of creation."""
        if group not in ("stage", "family", "kind"):
            raise ValueError(group)
        q = f"SELECT COALESCE({group},'?'), SUM(n_trials), SUM(COALESCE(n_evals,n_trials)), SUM(COALESCE(compute_s,0)), COUNT(*) FROM experiments WHERE kind!='legacy'"
        args = []
        if day:
            q += " AND created LIKE ?"; args.append(day + "%")
        with self._lock:
            rows = self._con.execute(q + f" GROUP BY COALESCE({group},'?')", args).fetchall()
        return {r[0]: {"trials": int(r[1] or 0), "evals": int(r[2] or 0), "compute_s": float(r[3] or 0), "n": int(r[4])} for r in rows}

    def deep_candidates_on(self, day: str) -> int:
        with self._lock:
            return int(self._con.execute("SELECT COUNT(*) FROM experiments WHERE kind='study' AND created LIKE ? AND stage IN ('validation','screening') "
                                         "AND details LIKE '%\"entered_validation\": true%'", (day + "%",)).fetchone()[0])

    def counter(self, key: str) -> int:
        with self._lock:
            r = self._con.execute("SELECT n FROM counters WHERE key=?", (key,)).fetchone()
        return int(r[0]) if r else 0

    def study_rows(self, limit: int = 2000) -> list[dict]:
        """Finished and failed studies with their stored result summaries (for the research report)."""
        with self._lock:
            rows = self._con.execute("SELECT id,created,label,market,status,family,hyp_fp,n_trials,executed,compute_s,reject_reason,results,details,config_version,code_version "
                                     "FROM experiments WHERE kind='study' ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        keys = ("id", "created", "label", "market", "status", "family", "hyp_fp", "n_trials", "executed", "compute_s", "reject_reason", "results", "details",
                "config_version", "code_version")
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            for k in ("results", "details"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    # -- writes (each one a transaction) ---------------------------------------------------
    def begin_experiment(self, label: str, n_trials: int, spec_fp: str | None = None, dataset_fp: str | None = None,
                         market: str | None = None, details: dict | None = None, dedupe: bool = False, stage: str = "screening",
                         family: str | None = None, hyp_fp: str | None = None, config_version: str | None = None,
                         code_version: str | None = None, cost_model: str | None = None) -> str:
        """Register a study BEFORE it runs (status 'running'). With dedupe=True the same spec on the same dataset raises
        DuplicateExperiment, unless the earlier attempt failed: a crash must not block the strategy for ever."""
        def work(con):
            if dedupe and spec_fp and dataset_fp:
                r = con.execute("SELECT id FROM experiments WHERE kind='study' AND spec_fp=? AND dataset_fp=? "
                                "AND status IN ('running','completed','early_stopped')", (spec_fp, dataset_fp)).fetchone()
                if r:
                    raise DuplicateExperiment(r[0])
            eid = uuid.uuid4().hex
            con.execute("INSERT INTO experiments(id,created,kind,label,market,spec_fp,dataset_fp,n_trials,details,status,stage,family,hyp_fp,n_evals,"
                        "code_version,config_version,cost_model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (eid, _now(), "study" if (spec_fp and dataset_fp and dedupe) else "trials", label, market,
                         spec_fp if dedupe else None, dataset_fp, int(n_trials), json.dumps(details, default=str) if details else None,
                         "running", stage, family, hyp_fp, int(n_trials), code_version, config_version, cost_model))
            return eid
        return self._tx(work)

    def finish_experiment(self, eid: str, status: str = "completed", executed: int | None = None, results: dict | None = None,
                          reject_reason: str | None = None, compute_s: float | None = None, details: dict | None = None) -> None:
        """Close a study. `executed` < planned (an early stop) RELEASES the unexecuted trials: they were never evaluated, so they
        cannot have inflated any selection. A failed study keeps its planned count: the candidates may have been looked at."""
        if status not in ("completed", "failed", "early_stopped"):
            raise ValueError(status)

        def work(con):
            row = con.execute("SELECT n_trials,details FROM experiments WHERE id=?", (eid,)).fetchone()
            if row is None:
                return
            n = row[0]
            if executed is not None and status != "failed" and executed < n:
                n = int(executed)
            d = json.loads(row[1]) if row[1] else {}
            d.update(details or {})
            con.execute("UPDATE experiments SET status=?, executed=?, n_trials=?, n_evals=?, results=?, reject_reason=?, compute_s=?, details=? WHERE id=?",
                        (status, executed, n, n, json.dumps(results, default=str) if results else None, reject_reason, compute_s,
                         json.dumps(d, default=str) if d else None, eid))
        self._tx(work)

    def set_executed(self, eid: str, executed: int) -> None:
        """Right after the sweep: the trials that were planned but not executed (staged search, early stop) are released."""
        def work(con):
            row = con.execute("SELECT n_trials FROM experiments WHERE id=?", (eid,)).fetchone()
            if row and executed < row[0]:
                con.execute("UPDATE experiments SET n_trials=?, n_evals=?, executed=? WHERE id=?", (int(executed), int(executed), int(executed), eid))
            else:
                con.execute("UPDATE experiments SET executed=? WHERE id=?", (int(executed), eid))
        self._tx(work)

    def recover_stale(self) -> int:
        """Studies still 'running' when the process died: mark them failed (they keep their trial count) so they can be re-run."""
        def work(con):
            cur = con.execute("UPDATE experiments SET status='failed', reject_reason='interrupted (process ended before the study finished)' "
                              "WHERE status='running' AND kind IN ('study','trials')")
            return cur.rowcount
        return self._tx(work)

    def bump(self, key: str, n: int = 1) -> None:
        def work(con):
            con.execute("INSERT INTO counters(key,n) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET n=n+?", (key, int(n), int(n)))
        self._tx(work)

    def note_duplicate(self, key: str = "duplicates_exact") -> None:
        self.bump(key, 1)

    def add_trials(self, n: int, label: str, experiment_id: str | None = None, dataset_fp: str | None = None,
                   market: str | None = None, kind: str = "trials", stage: str | None = None, family: str | None = None,
                   n_evals: int | None = None, compute_s: float | None = None, config_version: str | None = None,
                   code_version: str | None = None, details: dict | None = None) -> str:
        """Extra trials a study generates (exit grid, directions, ...) or compute-only tests (n=0 with n_evals/compute_s)."""
        def work(con):
            eid = uuid.uuid4().hex
            d = {"parent": experiment_id, **(details or {})} if (experiment_id or details) else None
            con.execute("INSERT INTO experiments(id,created,kind,label,market,dataset_fp,n_trials,details,status,stage,family,n_evals,compute_s,"
                        "config_version,code_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (eid, _now(), kind, label, market, dataset_fp, int(n), json.dumps(d, default=str) if d else None, "completed", stage, family,
                         int(n if n_evals is None else n_evals), compute_s, config_version, code_version))
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
