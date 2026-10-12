"""The lab's knowledge network: concepts that connect papers, strategies and results.

Nothing here calls an LLM. Concepts come from three places:
  * the Researcher names 2-6 concepts for every paper it reads (stored next to its proposals);
  * the Scout search topic that found the paper;
  * the strategy template family (opening range, VWAP deviation...).
From them this module builds (1) a graph that Obsidian shows as linked notes and (2) a short MEMORY TEXT
fed back to the Researcher and the Planner, so the agents learn from what the lab already tested: which
families keep failing and at which gate, which concepts are still unexplored."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .strategies import TEMPLATES

MAX_CONCEPT = 40


def norm(name: str) -> str | None:
    s = re.sub(r"[^a-z0-9àèéìòù \-/&]", " ", str(name).lower())
    s = " ".join(s.split())[:MAX_CONCEPT].strip(" -/")
    return s if len(s) >= 3 else None


def clean_concepts(raw, limit: int = 6) -> list[str]:
    out = []
    for c in raw if isinstance(raw, list) else []:
        n = norm(c) if isinstance(c, str) else None
        if n and n not in out:
            out.append(n)
    return out[:limit]


def failed_gates(rep: dict) -> list[str]:
    lk = rep.get("locked") or {}
    if lk.get("checks"):
        return [k for k, v in lk["checks"].items() if not v]
    return re.findall(r"'([a-z_]+)'", lk.get("reason") or "")


def build(studies: list[dict], papers: dict, specs_dir: Path) -> dict:
    """studies: [{report,...}]; papers: scout state papers {id: rec}; specs_dir: <work>/specs_auto."""
    by_file, strat_file = {}, {}
    for f in sorted(Path(specs_dir).glob("*.json")) if Path(specs_dir).exists() else []:
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        by_file[f.stem] = clean_concepts(d.get("concepts"))
        for s in d.get("strategies", []):
            strat_file[s.get("name")] = f.stem
    paper_concepts, file_title = {}, {}
    for pid, p in papers.items():
        if p.get("status") not in ("downloaded", "abstract") or not p.get("title"):
            continue
        stem = re.sub(r"[^A-Za-z0-9]", "_", Path(p.get("file") or "").stem)[:60]
        cs = list(by_file.get(stem, []))
        q = norm(p.get("query") or "")
        if q and q not in cs:
            cs.append(q)
        paper_concepts[p["title"]] = cs
        file_title[stem] = p["title"]
    strategy_concepts, concepts = {}, defaultdict(lambda: {"papers": [], "strategies": [], "gates": Counter(),
                                                           "verdicts": Counter()})
    for title, cs in paper_concepts.items():
        for c in cs:
            concepts[c]["papers"].append(title)
    for st in studies:
        rep = st["report"]
        cs = list(by_file.get(strat_file.get(rep["name"]), []))
        t = TEMPLATES.get(rep["template"])
        tc = norm(t.doc.split(":", 1)[-1]) if t else None
        if tc and tc not in cs:
            cs.append(tc)
        strategy_concepts[rep["name"]] = cs
        status = rep["locked"]["status"]
        for c in cs:
            concepts[c]["strategies"].append(rep["name"])
            concepts[c]["verdicts"][status] += 1
            for g in failed_gates(rep):
                concepts[c]["gates"][g] += 1
    co = defaultdict(Counter)
    for cs in list(paper_concepts.values()) + list(strategy_concepts.values()):
        for a in cs:
            for b in cs:
                if a != b:
                    co[a][b] += 1
    out = {}
    for c, d in concepts.items():
        n = len(d["strategies"])
        ok = d["verdicts"].get("APPROVED", 0)
        lessons = []
        if n and not ok:
            lessons.append(f"{n} strategies tested, none approved" + (
                "; most common failed gates: " + ", ".join(f"{g} ({k})" for g, k in d["gates"].most_common(3)) if d["gates"] else ""))
        elif ok:
            lessons.append(f"{ok} of {n} tested strategies approved by the Judge")
        if not n and d["papers"]:
            lessons.append("read in papers but no strategy tested yet")
        out[c] = {"papers": sorted(set(d["papers"])), "strategies": sorted(set(d["strategies"])),
                  "tested": n, "approved": ok, "gates": dict(d["gates"]),
                  "related": [x for x, _ in co[c].most_common(6)], "lessons": lessons}
    return {"concepts": out, "paper_concepts": paper_concepts, "strategy_concepts": strategy_concepts}


def memory_text(graph: dict, limit: int = 1800) -> str:
    """What the lab has learned so far, for the Researcher's and Planner's prompts. Facts only."""
    rows = []
    for c, d in sorted(graph["concepts"].items(), key=lambda kv: -kv[1]["tested"]):
        if d["tested"]:
            gates = ", ".join(f"{g}" for g in list(d["gates"])[:2])
            rows.append(f"- {c}: {d['tested']} tested, {d['approved']} approved" + (f"; usually fails: {gates}" if gates and not d["approved"] else ""))
    unexplored = [c for c, d in graph["concepts"].items() if not d["tested"]][:8]
    if not rows and not unexplored:
        return ""
    txt = "WHAT THIS LAB ALREADY LEARNED (facts from past tests; use them):\n" + "\n".join(rows[:12])
    if unexplored:
        txt += "\nConcepts read but not yet tested: " + ", ".join(unexplored)
    txt += ("\nDo not re-propose an idea that failed here unless the text brings a NEW mechanism; "
            "prefer untested concepts and ideas that connect two concepts.")
    return txt[:limit]
