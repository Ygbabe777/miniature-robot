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


def dev_failed_gates(rep: dict) -> list[str]:
    """Why a strategy failed, using ONLY development-stage evidence (surface, walk-forward, quality, beta check). What the
    hidden block said is never part of what agents are told: a hypothesis generator that learns from the hidden block's
    verdicts turns that block into training data for the next hypotheses."""
    gates = [k for k, ok in (rep.get("surface") or {}).get("checks", {}).items() if not ok]
    if rep.get("wf_oos_pnl", 1) <= 0:
        gates.append("walk_forward_pnl")
    gates += [f"quality_{k}" for k, ok in ((rep.get("quality") or {}).get("checks") or {}).items() if not ok]
    sd = rep.get("surface_detrended")
    if sd is not None and not sd.get("robust", True):
        gates.append("fragile_without_market_drift")
    return gates


def build(studies: list[dict], papers: dict, specs_dir: Path, notes: dict | None = None) -> dict:
    """studies: [{report,...}]; papers: scout state papers {id: rec}; specs_dir: <work>/specs_auto."""
    by_file, strat_file = {}, {}
    for f in sorted(Path(specs_dir).glob("*.json")) if Path(specs_dir).exists() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
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
                                                           "verdicts": Counter(), "bym": defaultdict(Counter),
                                                           "dev_gates": Counter(), "dev_passed": 0, "dev_bym": defaultdict(Counter)})
    tmpl_market: dict = defaultdict(lambda: defaultdict(Counter))      # template -> market -> verdict counts (humans)
    tmpl_dev: dict = defaultdict(lambda: defaultdict(Counter))         # template -> market -> development-stage counts (agents)
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
        dgates = dev_failed_gates(rep)
        tmpl_market[rep["template"]][mk][status] += 1
        tmpl_dev[rep["template"]][mk]["tested"] += 1
        tmpl_dev[rep["template"]][mk]["dev_passed"] += (not dgates)
        for c in cs:
            concepts[c]["bym"][mk][status] += 1
            concepts[c]["strategies"].append(rep["name"])
            concepts[c]["verdicts"][status] += 1
            for g in failed_gates(rep):
                concepts[c]["gates"][g] += 1
            for g in dgates:
                concepts[c]["dev_gates"][g] += 1
            concepts[c]["dev_passed"] += (not dgates)
            concepts[c]["dev_bym"][mk]["tested"] += 1
            concepts[c]["dev_bym"][mk]["dev_passed"] += (not dgates)
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
        dev_l = []
        if n:
            dev_l.append(f"{n} strategies tested on development data, {d['dev_passed']} passed the development checks" + (
                "; most common failed checks: " + ", ".join(f"{g} ({k})" for g, k in d["dev_gates"].most_common(3)) if d["dev_gates"] else ""))
        elif d["papers"]:
            dev_l.append("read in papers but no strategy tested yet")
        out[c] = {"papers": sorted(set(d["papers"])), "strategies": sorted(set(d["strategies"])),
                  "tested": n, "approved": ok, "gates": dict(d["gates"]),
                  "dev_passed": d["dev_passed"], "dev_gates": dict(d["dev_gates"]), "dev_lessons": dev_l,
                  "dev_by_market": {m: dict(v) for m, v in d["dev_bym"].items()},
                  "related": [x for x, _ in co[c].most_common(6)], "lessons": lessons,
                  "owner_notes": [t[:300] for t in ann.get(c, [])][:3],
                  "by_market": {m: {"tested": sum(v.values()), "approved": v.get("APPROVED", 0)} for m, v in d["bym"].items()}}
    templates = {t: {m: {"tested": sum(v.values()), "approved": v.get("APPROVED", 0)} for m, v in ms.items()}
                 for t, ms in tmpl_market.items()}
    return {"concepts": out, "paper_concepts": paper_concepts, "strategy_concepts": strategy_concepts, "templates": templates,
            "dev_templates": {t: {m: dict(v) for m, v in ms.items()} for t, ms in tmpl_dev.items()}}


def memory_text(graph: dict, limit: int = 1800) -> str:
    """What the lab has learned so far, for the Researcher's and Planner's prompts. DEVELOPMENT-STAGE facts only: how
    many strategies of each concept were tested and passed the checks that use development data. Verdicts and checks of
    the hidden block are deliberately absent (see dev_failed_gates)."""
    rows = []
    for c, d in sorted(graph["concepts"].items(), key=lambda kv: -kv[1]["tested"]):
        if d["tested"]:
            gates = ", ".join(list(d.get("dev_gates", {}))[:2])
            rows.append(f"- {c}: {d['tested']} tested, {d.get('dev_passed', 0)} passed the development checks"
                        + (f"; usually fails: {gates}" if gates and not d.get("dev_passed") else ""))
    mk_rows = [f"- {t}: " + ", ".join(f"{m} {v['tested']} tested/{v['dev_passed']} passed" for m, v in sorted(ms.items()))
               for t, ms in graph.get("dev_templates", {}).items() if len(ms) > 1 or any(v["dev_passed"] for v in ms.values())][:8]
    unexplored = [c for c, d in graph["concepts"].items() if not d["tested"]][:8]
    owner = [f"- {c}: \"{t}\"" for c, d in graph["concepts"].items() for t in d.get("owner_notes", [])[:1]][:5]
    if not rows and not unexplored:
        return ""
    txt = "WHAT THIS LAB ALREADY LEARNED (development-stage facts from past tests; use them):\n" + "\n".join(rows[:12])
    if unexplored:
        txt += "\nConcepts read but not yet tested: " + ", ".join(unexplored)
    if mk_rows:
        txt += "\nResults by market (an idea can pass on one index and fail on another):\n" + "\n".join(mk_rows)
    if owner:
        txt += "\nNotes written by the OWNER in Obsidian (their opinion, not a test result):\n" + "\n".join(owner)
    txt += ("\nDo not re-propose an idea that failed here unless the text brings a NEW mechanism; "
            "prefer untested concepts and ideas that connect two concepts.")
    return txt[:limit]


def connection_candidates(graph: dict, owner_pairs: list[tuple[str, str]] | None = None, done: set | None = None,
                          n: int = 2) -> list[tuple[str, str, float]]:
    """Pairs of concepts worth combining. A pair the owner linked by hand scores highest; then pairs that include an
    untested concept or one that already passed the development checks; pairs already combined in a paper or a
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
                sc += (1.0 if not cs[c]["tested"] else 0.0) + (2.0 if cs[c].get("dev_passed") else 0.0) + (1.0 if cs[c].get("owner_notes") else 0.0)
            if b in cs[a]["related"] and (a, b) not in owner:      # combined before: demoted, unless the owner asks for it
                sc -= 3.0
            if sc > 0:
                out.append((a, b, sc))
    out.sort(key=lambda x: (-x[2], x[0], x[1]))
    return out[:n]
