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


def build(studies: list[dict], papers: dict, specs_dir: Path, notes: dict | None = None) -> dict:
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
                                                           "verdicts": Counter(), "bym": defaultdict(Counter)})
    tmpl_market: dict = defaultdict(lambda: defaultdict(Counter))      # template -> market -> verdict counts
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
        mk = rep.get("market") or "NQ"
        tmpl_market[rep["template"]][mk][status] += 1
        for c in cs:
            concepts[c]["bym"][mk][status] += 1
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
    ann = defaultdict(list)                              # what the OWNER wrote in Obsidian, attached to concepts
    for name, n in (notes or {}).items():
        text = n["annotation"] if n["generated"] else n["head"]
        if not text:
            continue
        if name.startswith("Concetto - "):
            ann[name[len("Concetto - "):]].append(text)
        elif name in strategy_concepts:
            for c in strategy_concepts[name]:
                ann[c].append(text)
        else:
            for c in {x[len("Concetto - "):] for x in n["links"] if x.startswith("Concetto - ")}:
                ann[c].append(text)
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
                  "related": [x for x, _ in co[c].most_common(6)], "lessons": lessons,
                  "owner_notes": [t[:300] for t in ann.get(c, [])][:3],
                  "by_market": {m: {"tested": sum(v.values()), "approved": v.get("APPROVED", 0)} for m, v in d["bym"].items()}}
    templates = {t: {m: {"tested": sum(v.values()), "approved": v.get("APPROVED", 0)} for m, v in ms.items()}
                 for t, ms in tmpl_market.items()}
    return {"concepts": out, "paper_concepts": paper_concepts, "strategy_concepts": strategy_concepts, "templates": templates}


def memory_text(graph: dict, limit: int = 1800) -> str:
    """What the lab has learned so far, for the Researcher's and Planner's prompts. Facts only."""
    rows = []
    for c, d in sorted(graph["concepts"].items(), key=lambda kv: -kv[1]["tested"]):
        if d["tested"]:
            gates = ", ".join(f"{g}" for g in list(d["gates"])[:2])
            rows.append(f"- {c}: {d['tested']} tested, {d['approved']} approved" + (f"; usually fails: {gates}" if gates and not d["approved"] else ""))
    mk_rows = [f"- {t}: " + ", ".join(f"{m} {v['tested']} tested/{v['approved']} approved" for m, v in sorted(ms.items()))
               for t, ms in graph.get("templates", {}).items() if len(ms) > 1 or any(v["approved"] for v in ms.values())][:8]
    unexplored = [c for c, d in graph["concepts"].items() if not d["tested"]][:8]
    owner = [f"- {c}: \"{t}\"" for c, d in graph["concepts"].items() for t in d.get("owner_notes", [])[:1]][:5]
    if not rows and not unexplored:
        return ""
    txt = "WHAT THIS LAB ALREADY LEARNED (facts from past tests; use them):\n" + "\n".join(rows[:12])
    if unexplored:
        txt += "\nConcepts read but not yet tested: " + ", ".join(unexplored)
    if mk_rows:
        txt += "\nResults by market (an idea can work on one index and fail on another):\n" + "\n".join(mk_rows)
    if owner:
        txt += "\nNotes written by the OWNER in Obsidian (their opinion, not a test result):\n" + "\n".join(owner)
    txt += ("\nDo not re-propose an idea that failed here unless the text brings a NEW mechanism; "
            "prefer untested concepts and ideas that connect two concepts.")
    return txt[:limit]


def connection_candidates(graph: dict, owner_pairs: list[tuple[str, str]] | None = None, done: set | None = None,
                          n: int = 2) -> list[tuple[str, str, float]]:
    """Pairs of concepts worth combining. A pair the owner linked by hand scores highest; then pairs that include an
    untested concept or one that already produced an approved strategy; pairs already combined in a paper or a
    strategy score lower. Returns [(a, b, score)], best first, skipping pairs tried before."""
    cs = graph["concepts"]
    owner = {tuple(sorted(p)) for p in (owner_pairs or [])}
    done = done or set()
    out = []
    names = sorted(cs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            key = f"{a}|{b}"
            if key in done:
                continue
            sc = 5.0 if (a, b) in owner else 0.0
            for c in (a, b):
                sc += (1.0 if not cs[c]["tested"] else 0.0) + (2.0 if cs[c]["approved"] else 0.0) + (1.0 if cs[c].get("owner_notes") else 0.0)
            if b in cs[a]["related"] and (a, b) not in owner:      # combined before: demoted, unless the owner asks for it
                sc -= 3.0
            if sc > 0:
                out.append((a, b, sc))
    out.sort(key=lambda x: (-x[2], x[0], x[1]))
    return out[:n]
