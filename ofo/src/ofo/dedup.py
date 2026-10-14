"""Persistent memory of deduplication DECISIONS.

A strategy is dropped as a duplicate for a reason: it is the same as something queued/tested (exact) or almost equivalent to a tested/queued
hypothesis (near). That decision is a fact worth recording ONCE. Before this module every poll of the Lab (every 5 s while the queue was empty) re-met the
same starter, re-emitted the event and incremented the counter, so `duplicates_exact/near` measured polling, not research.

A decision is identified by (market, spec fingerprint, rule version, kind, equivalent). It is stored in the registry database (survives restarts).
The memory only answers "have I already told you about this exact decision?": the decision itself is always recomputed against the CURRENT state, so
nothing is blocked on the strength of an old note:

* the rule version is part of the key: changing the near-duplicate criterion (threshold or logic) makes every decision new again;
* the spec fingerprint and the market are part of the key: a changed spec or another market is a new question;
* the equivalent candidate is part of the key: if what it duplicated is gone (or is another study), it is not the same decision (and if it is gone the
  strategy is simply accepted, because the live check no longer finds a duplicate);
* an explicit, authorised retest bypasses deduplication altogether and also invalidates the remembered decisions that referred to the retested study.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter

RULE = "v1"                       # bump when the NEAR-DUPLICATE LOGIC changes (the threshold is added automatically)

SCHEMA = """
CREATE TABLE IF NOT EXISTS dedup_decisions (
    market TEXT NOT NULL, spec_fp TEXT NOT NULL, rule TEXT NOT NULL, kind TEXT NOT NULL, equivalent TEXT NOT NULL,
    created TEXT NOT NULL, spec_name TEXT, equivalent_name TEXT, origin TEXT,
    PRIMARY KEY (market, spec_fp, rule, kind, equivalent));
"""


def rule_version(threshold: float) -> str:
    return f"{RULE}|th={threshold:g}"


def equivalent_fp(template, timeframes, ranges) -> str:
    """Identity of the hypothesis a strategy was found equivalent to (template, timeframes, parameter ranges)."""
    body = json.dumps([template, sorted(timeframes or []), ranges or {}], sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


class DedupMemory:
    def __init__(self, registry):
        self.reg = registry
        self.suppressed: Counter = Counter()          # repeats of a known decision that were NOT re-announced (diagnostics only, not persisted)
        with registry._lock:
            registry._con.executescript(SCHEMA)
            registry._con.commit()

    def record(self, market: str, spec_fp: str, rule: str, kind: str, equivalent: str, spec_name: str = "", equivalent_name: str = "", origin: str = "") -> bool:
        """True if this decision is NEW (announce it, count it); False if it was already on record (stay silent)."""
        with self.reg._lock:
            cur = self.reg._con.execute("INSERT OR IGNORE INTO dedup_decisions(market,spec_fp,rule,kind,equivalent,created,spec_name,equivalent_name,origin) VALUES (?,?,?,?,?,?,?,?,?)",
                                        (market, spec_fp, rule, kind, equivalent, time.strftime("%Y-%m-%d %H:%M:%S"), spec_name, equivalent_name, origin))
            self.reg._con.commit()
            new = cur.rowcount > 0
        if not new:
            self.suppressed[kind] += 1
        return new

    def invalidate(self, market: str | None = None, spec_fp: str | None = None, equivalent: str | None = None) -> int:
        """Forget decisions (all of them, or filtered). Returns how many. Used when a retest is authorised and when the owner asks."""
        where, args = [], []
        for col, val in (("market", market), ("spec_fp", spec_fp), ("equivalent", equivalent)):
            if val is not None:
                where.append(f"{col}=?"); args.append(val)
        with self.reg._lock:
            cur = self.reg._con.execute("DELETE FROM dedup_decisions" + (" WHERE " + " AND ".join(where) if where else ""), args)
            self.reg._con.commit()
        return cur.rowcount

    def count(self, rule: str | None = None) -> dict:
        q, a = "SELECT kind, COUNT(*) FROM dedup_decisions", []
        if rule:
            q += " WHERE rule=?"; a.append(rule)
        with self.reg._lock:
            rows = self.reg._con.execute(q + " GROUP BY kind", a).fetchall()
        return {k: int(n) for k, n in rows}

    def recent(self, limit: int = 8) -> list[dict]:
        with self.reg._lock:
            rows = self.reg._con.execute("SELECT created,market,kind,spec_name,equivalent_name,rule,origin FROM dedup_decisions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("at", "market", "kind", "spec", "equivalent", "rule", "origin"), r)) for r in rows]
