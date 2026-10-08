"""Journal CSV. La decisione umana NON viene mai inventata: default PENDING."""
from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Any

from .schemas import HUMAN_DECISIONS

FIELDS = ["date", "instrument", "council_bias", "selected_scenario", "human_decision", "entry", "stop",
          "target", "result", "pnl_r", "notes", "council_status", "run_id"]
RESULTS = ("", "WIN", "LOSS", "BE", "NOT_TAKEN")


def read_journal(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [{k: (row.get(k) or "") for k in FIELDS} for row in csv.DictReader(fh)]


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def ensure_journal(path: Path) -> None:
    if not path.exists():
        _write(path, [])


def record_ai_run(path: Path, report: dict[str, Any]) -> None:
    """Upsert della riga del giorno con i soli campi AI; i campi umani sono preservati."""
    rows = read_journal(path)
    date = report["run"]["date"]
    dec = report["decision"]
    sel = dec.get("selected_scenario_id") or ""
    scen = next((s for s in (report["agents"].get("strategist", {}).get("output") or {}).get("scenarios", [])
                 if s["id"] == sel), None)
    ai = {"date": date, "instrument": "NQ", "council_bias": dec.get("primary_bias", ""),
          "selected_scenario": sel, "council_status": dec["status"], "run_id": report["run"]["run_id"]}
    if scen:
        ai.update(entry=f"{scen['entry_zone'][0]}-{scen['entry_zone'][1]}", stop=str(scen["invalidation"]),
                  target=str(scen["target_1"]))
    existing = next((r for r in rows if r["date"] == date), None)
    if existing is None:
        row = {k: "" for k in FIELDS}
        row.update(ai)
        row["human_decision"] = "PENDING"
        rows.append(row)
    else:
        human_kept = {k: existing[k] for k in ("human_decision", "result", "pnl_r", "notes")}
        # entry/stop/target umani (MODIFY/TAKE) non vengono sovrascritti se la decisione non e' PENDING
        if existing["human_decision"] not in ("", "PENDING"):
            for k in ("entry", "stop", "target"):
                ai.pop(k, None)
        existing.update(ai)
        existing.update(human_kept)
    _write(path, sorted(rows, key=lambda r: r["date"]))


def record_human_decision(path: Path, date: str, decision: str, *, entry: str = "", stop: str = "",
                          target: str = "", notes: str = "") -> None:
    if decision not in HUMAN_DECISIONS or decision == "PENDING":
        raise ValueError(f"Decisione umana non valida: {decision}")
    rows = read_journal(path)
    row = next((r for r in rows if r["date"] == date), None)
    if row is None:
        raise ValueError(f"Nessuna analisi in journal per {date}: eseguire prima run.py")
    row["human_decision"] = decision
    for k, v in (("entry", entry), ("stop", stop), ("target", target)):
        if v:
            row[k] = v
    if notes:
        row["notes"] = notes
    _write(path, rows)


def record_result(path: Path, date: str, result: str, pnl_r: float | None, notes: str = "") -> None:
    if result not in RESULTS:
        raise ValueError(f"Risultato non valido: {result}")
    rows = read_journal(path)
    row = next((r for r in rows if r["date"] == date), None)
    if row is None:
        raise ValueError(f"Nessuna riga journal per {date}")
    row["result"] = result
    row["pnl_r"] = "" if pnl_r is None else f"{pnl_r:g}"
    if notes:
        row["notes"] = notes
    _write(path, rows)
