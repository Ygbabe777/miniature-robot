"""Preregistered finalists and the frozen holdout epoch.

The hidden block is a scarce resource. This module makes its use auditable and its error control arithmetic honest:

* EPOCH. The hidden block is a FIXED range of sessions, chosen once (from `holdout_fraction`) and then frozen. Refreshing the data adds
  sessions AFTER the epoch (reserved for forward evidence); it never moves the cut, so sessions a finalist has already been judged on can
  never silently turn into development data. A new epoch can be opened only with enough fresh sessions; the closed block then joins development.
* FINALISTS. At most `max_finalists` (2-3) strategies may look at one epoch. They are chosen by the development stages (screening, nested walk-forward,
  robustness) BEFORE the look, and each is REGISTERED first: fingerprint, parameters, code version, dataset, sizing rule, rulesets, timestamp,
  number of finalists. Rows are append-only (triggers refuse UPDATE of identity columns and DELETE) and hash-chained, so an edit is detectable.
* LINEAGE. A variant of a line of research that has already been judged on the epoch cannot be registered: changing a strategy in response to the
  hidden block and testing it again on the same block is not an independent test. The same fingerprint again is a REPEAT, not a new finalist.
* `locked_looks` stays a separate counter in the registry (every peek, any reason). It is neither the number of development candidates nor K.
"""
from __future__ import annotations

import hashlib
import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS epochs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL, created TEXT NOT NULL, start_session TEXT NOT NULL, end_session TEXT NOT NULL,
    n_sessions INTEGER NOT NULL, max_finalists INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', holdout_fraction REAL);
CREATE TABLE IF NOT EXISTS finalists (
    id INTEGER PRIMARY KEY AUTOINCREMENT, epoch_id INTEGER NOT NULL, created TEXT NOT NULL, fingerprint TEXT NOT NULL, lineage TEXT NOT NULL,
    k_index INTEGER NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL, verdict TEXT, judged_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_finalist ON finalists(epoch_id, fingerprint);
CREATE TRIGGER IF NOT EXISTS finalists_no_delete BEFORE DELETE ON finalists BEGIN SELECT RAISE(ABORT, 'finalists are append-only'); END;
CREATE TRIGGER IF NOT EXISTS finalists_identity_frozen BEFORE UPDATE ON finalists
  WHEN OLD.fingerprint != NEW.fingerprint OR OLD.payload != NEW.payload OR OLD.row_hash != NEW.row_hash OR OLD.prev_hash != NEW.prev_hash OR OLD.epoch_id != NEW.epoch_id
       OR OLD.created != NEW.created OR OLD.lineage != NEW.lineage OR OLD.k_index != NEW.k_index OR (OLD.verdict IS NOT NULL AND OLD.verdict != NEW.verdict)
  BEGIN SELECT RAISE(ABORT, 'a registered finalist cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS epochs_frozen BEFORE UPDATE ON epochs
  WHEN OLD.start_session != NEW.start_session OR OLD.end_session != NEW.end_session OR OLD.max_finalists != NEW.max_finalists OR OLD.created != NEW.created
  BEGIN SELECT RAISE(ABORT, 'a holdout epoch is frozen'); END;
"""


class BlockFull(RuntimeError):
    """The epoch already has its maximum number of finalists: the hidden block is not available for another strategy."""


class LineageBlocked(RuntimeError):
    """A variant of a line of research already judged on this epoch."""


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def fingerprint(payload: dict) -> str:
    """Identity of a finalist: what it IS (strategy, parameters, code, data, sizing rule, rulesets), not when or how often it is run."""
    ident = {k: payload[k] for k in ("spec", "params", "tf", "direction", "schedule", "exit", "contracts", "slippage_ticks", "contract", "code_version",
                                      "config_version", "dataset_fp", "sizing", "rulesets", "epoch") if k in payload}
    return hashlib.sha256(canonical(ident).encode()).hexdigest()


class Finalists:
    def __init__(self, registry):
        self.reg = registry
        with registry._lock:
            registry._con.executescript(SCHEMA)
            registry._con.commit()

    # ---------------------------------------------------------------- epoch
    def epoch_for(self, market: str, sessions: list, holdout_fraction: float, max_finalists: int = 3) -> dict:
        """The open epoch of this market, created from `holdout_fraction` of `sessions` the first time and frozen afterwards. If the stored epoch does not
        fit these sessions (other data), it is retired as 'orphan' and a new one is made."""
        labels = [str(s)[:10] for s in sessions]
        with self.reg._lock:
            row = self.reg._con.execute("SELECT id,start_session,end_session,n_sessions,max_finalists,created FROM epochs WHERE market=? AND status='open' ORDER BY id DESC LIMIT 1", (market,)).fetchone()
            if row and row[1] in labels and row[2] in labels:
                return self._epoch(row)
            if row:
                self.reg._con.execute("UPDATE epochs SET status='orphan' WHERE id=?", (row[0],))
            i = int(len(labels) * (1 - holdout_fraction))
            start, end = labels[i], labels[-1]
            cur = self.reg._con.execute("INSERT INTO epochs(market,created,start_session,end_session,n_sessions,max_finalists,holdout_fraction) VALUES (?,?,?,?,?,?,?)",
                                        (market, time.strftime("%Y-%m-%d %H:%M:%S"), start, end, len(labels) - i, int(max_finalists), holdout_fraction))
            self.reg._con.commit()
            row = self.reg._con.execute("SELECT id,start_session,end_session,n_sessions,max_finalists,created FROM epochs WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._epoch(row)

    @staticmethod
    def _epoch(r) -> dict:
        return {"id": r[0], "start": r[1], "end": r[2], "n_sessions": r[3], "max_finalists": r[4], "created": r[5]}

    def open_next(self, market: str, sessions: list, min_new: int, max_finalists: int = 3) -> dict:
        """Close the current epoch and open a new one on the sessions AFTER it. Refused unless at least `min_new` fresh sessions exist.
        The closed block then belongs to development (it has been seen by at most `max_finalists` strategies, which are on record)."""
        labels = [str(s)[:10] for s in sessions]
        cur = self.current(market)
        if cur is None:
            return {"error": "nessuna epoca aperta"}
        fresh = [d for d in labels if d > cur["end"]]
        if len(fresh) < min_new:
            return {"error": f"servono almeno {min_new} sessioni nuove dopo il blocco attuale (ce ne sono {len(fresh)})"}
        with self.reg._lock:
            self.reg._con.execute("UPDATE epochs SET status='closed' WHERE id=?", (cur["id"],))
            c = self.reg._con.execute("INSERT INTO epochs(market,created,start_session,end_session,n_sessions,max_finalists) VALUES (?,?,?,?,?,?)",
                                      (market, time.strftime("%Y-%m-%d %H:%M:%S"), fresh[0], fresh[-1], len(fresh), int(max_finalists)))
            self.reg._con.commit()
            r = self.reg._con.execute("SELECT id,start_session,end_session,n_sessions,max_finalists,created FROM epochs WHERE id=?", (c.lastrowid,)).fetchone()
        return self._epoch(r)

    def current(self, market: str) -> dict | None:
        with self.reg._lock:
            r = self.reg._con.execute("SELECT id,start_session,end_session,n_sessions,max_finalists,created FROM epochs WHERE market=? AND status='open' ORDER BY id DESC LIMIT 1", (market,)).fetchone()
        return self._epoch(r) if r else None

    # ---------------------------------------------------------------- finalists
    def rows(self, epoch_id: int) -> list[dict]:
        with self.reg._lock:
            rs = self.reg._con.execute("SELECT id,created,fingerprint,lineage,k_index,payload,row_hash,verdict,judged_at FROM finalists WHERE epoch_id=? ORDER BY id", (epoch_id,)).fetchall()
        return [{"id": r[0], "created": r[1], "fingerprint": r[2], "lineage": r[3], "k_index": r[4], "payload": json.loads(r[5]), "row_hash": r[6], "verdict": r[7], "judged_at": r[8]} for r in rs]

    def register(self, epoch: dict, payload: dict, lineage: str) -> dict:
        """Register a finalist BEFORE it sees the hidden block. Returns {finalist, repeat}. Raises BlockFull / LineageBlocked."""
        payload = {**payload, "epoch": {"id": epoch["id"], "start": epoch["start"], "end": epoch["end"]}}
        fp = fingerprint(payload)
        with self.reg._lock:
            self.reg._con.execute("BEGIN IMMEDIATE")
            try:
                same = self.reg._con.execute("SELECT id,k_index,verdict FROM finalists WHERE epoch_id=? AND fingerprint=?", (epoch["id"], fp)).fetchone()
                if same:
                    self.reg._con.execute("COMMIT")
                    return {"finalist": {"id": same[0], "k_index": same[1], "fingerprint": fp, "verdict": same[2]}, "repeat": True}
                rows = self.reg._con.execute("SELECT lineage,verdict FROM finalists WHERE epoch_id=? ORDER BY id", (epoch["id"],)).fetchall()
                if any(r[0] == lineage and r[1] is not None for r in rows):
                    raise LineageBlocked(f"la linea «{lineage}» è già stata giudicata su questo blocco: una variante non è una prova indipendente")
                if len(rows) >= int(epoch["max_finalists"]):
                    raise BlockFull(f"il blocco ha già {len(rows)} finalisti registrati (massimo {epoch['max_finalists']})")
                prev = self.reg._con.execute("SELECT row_hash FROM finalists ORDER BY id DESC LIMIT 1").fetchone()
                prev_hash = prev[0] if prev else "0" * 64
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                k = len(rows) + 1
                payload = {**payload, "registered_at": now, "k_index": k, "max_finalists": int(epoch["max_finalists"])}
                body = canonical(payload)
                row_hash = hashlib.sha256((prev_hash + fp + body + now).encode()).hexdigest()
                cur = self.reg._con.execute("INSERT INTO finalists(epoch_id,created,fingerprint,lineage,k_index,payload,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?)",
                                            (epoch["id"], now, fp, lineage, k, body, prev_hash, row_hash))
                self.reg._con.execute("COMMIT")
            except BaseException:
                try:
                    self.reg._con.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        return {"finalist": {"id": cur.lastrowid, "k_index": k, "fingerprint": fp, "row_hash": row_hash, "registered_at": now}, "repeat": False}

    def record_verdict(self, finalist_id: int, verdict: str) -> None:
        """The outcome is written once. Nothing else about the row can change."""
        with self.reg._lock:
            self.reg._con.execute("UPDATE finalists SET verdict=?, judged_at=? WHERE id=? AND verdict IS NULL", (verdict, time.strftime("%Y-%m-%d %H:%M:%S"), finalist_id))
            self.reg._con.commit()

    def verify_chain(self) -> dict:
        """Recompute the hash chain. `ok` False means a row was edited or removed outside the triggers."""
        with self.reg._lock:
            rs = self.reg._con.execute("SELECT id,created,fingerprint,payload,prev_hash,row_hash FROM finalists ORDER BY id").fetchall()
        prev = "0" * 64
        for r in rs:
            exp = hashlib.sha256((prev + r[2] + r[3] + r[1]).encode()).hexdigest()
            if r[4] != prev or r[5] != exp:
                return {"ok": False, "broken_at": r[0]}
            prev = r[5]
        return {"ok": True, "n": len(rs)}
