"""Study reports on disk: <work>/reports/<slug>.json (+ <slug>_series.json for the charts)."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

_PATH_LOCKS: dict[str, threading.Lock] = {}
_PL = threading.Lock()


def atomic_write(path: str | Path, text: str) -> None:
    """Write a file so a reader never sees half of it, safely from many threads and on Windows. A fixed '<name>.tmp' shared by two writers,
    or a replace() while the browser/antivirus/another thread has the target open, raises PermissionError on Windows (never on Linux):
    here every writer has its own temp name, writers of one file take turns, and the replace is retried."""
    path = Path(path)
    with _PL:
        lk = _PATH_LOCKS.setdefault(str(path), threading.Lock())
    with lk:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        for i in range(25):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                time.sleep(0.04 * (i + 1))
        tmp.replace(path)                         # last try raises for real


def slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:60] or "study"


def save_study(work: str | Path, report: dict, planner: dict | None = None, auditor: dict | None = None) -> str:
    rep = {k: v for k, v in report.items() if k not in ("wf_oos_series", "series")}
    slug = slugify(rep["name"])
    d = Path(work) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    atomic = atomic_write                         # the UI reads these files while a study is being saved: never a half-written file
    if report.get("series"):
        atomic(d / f"{slug}_series.json", json.dumps(report["series"], default=str))
    atomic(d / f"{slug}.json", json.dumps(
        {"report": rep, "planner": planner, "auditor": auditor,
         "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
        indent=1, default=str))
    return slug


def load_study(work: str | Path, slug: str) -> dict | None:
    d = Path(work) / "reports"
    f = d / f"{slugify(slug)}.json"
    if not f.exists():
        return None
    out = json.loads(f.read_text(encoding="utf-8", errors="ofo_legacy"))
    sf = d / f"{slugify(slug)}_series.json"
    out["series"] = json.loads(sf.read_text(encoding="utf-8", errors="ofo_legacy")) if sf.exists() else None
    return out


def list_studies(work: str | Path) -> list[dict]:
    rows = []
    for f in sorted((Path(work) / "reports").glob("*.json")) if (Path(work) / "reports").exists() else []:
        if f.name.endswith("_series.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="ofo_legacy"))
            r, s = d["report"], d["report"]["surface"]
            rows.append({"slug": f.stem, "name": r["name"], "template": r["template"],
                         "status": r["locked"]["status"], "robust": s["robust"],
                         "median_sharpe": s["median_sharpe"], "n_candidates": r["n_candidates"], "market": r.get("market"),
                         "role": (r.get("roles") or {}).get("label"), "verdict": (r["locked"] or {}).get("verdict"), "stage": ((r.get("judgment") or {}).get("stage") or {}).get("stage"), "near_miss": bool(r.get("near_miss")), "favorite": bool(r.get("favorite")),
                         "saved_at": d.get("saved_at")})
        except (KeyError, ValueError):
            continue
    return sorted(rows, key=lambda r: r["saved_at"] or "", reverse=True)


def update_report(work: str | Path, slug: str, mutate) -> bool:
    """Atomically rewrite one study's report json with `mutate(report_dict)`. The series file is not touched."""
    f = Path(work) / "reports" / f"{slugify(slug)}.json"
    if not f.exists():
        return False
    d = json.loads(f.read_text(encoding="utf-8", errors="ofo_legacy"))
    mutate(d["report"])
    atomic_write(f, json.dumps(d, indent=1, default=str))
    return True
