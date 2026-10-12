"""Study reports on disk: <work>/reports/<slug>.json (+ <slug>_series.json for the charts)."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path


def slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:60] or "study"


def save_study(work: str | Path, report: dict, planner: dict | None = None, auditor: dict | None = None) -> str:
    rep = {k: v for k, v in report.items() if k not in ("wf_oos_series", "series")}
    slug = slugify(rep["name"])
    d = Path(work) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.json").write_text(json.dumps(
        {"report": rep, "planner": planner, "auditor": auditor,
         "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
        indent=1, default=str))
    if report.get("series"):
        (d / f"{slug}_series.json").write_text(json.dumps(report["series"], default=str))
    return slug


def load_study(work: str | Path, slug: str) -> dict | None:
    d = Path(work) / "reports"
    f = d / f"{slugify(slug)}.json"
    if not f.exists():
        return None
    out = json.loads(f.read_text())
    sf = d / f"{slugify(slug)}_series.json"
    out["series"] = json.loads(sf.read_text()) if sf.exists() else None
    return out


def list_studies(work: str | Path) -> list[dict]:
    rows = []
    for f in sorted((Path(work) / "reports").glob("*.json")) if (Path(work) / "reports").exists() else []:
        if f.name.endswith("_series.json"):
            continue
        try:
            d = json.loads(f.read_text())
            r, s = d["report"], d["report"]["surface"]
            rows.append({"slug": f.stem, "name": r["name"], "template": r["template"],
                         "status": r["locked"]["status"], "robust": s["robust"],
                         "median_sharpe": s["median_sharpe"], "n_candidates": r["n_candidates"], "market": r.get("market"),
                         "saved_at": d.get("saved_at")})
        except (KeyError, ValueError):
            continue
    return sorted(rows, key=lambda r: r["saved_at"] or "", reverse=True)
