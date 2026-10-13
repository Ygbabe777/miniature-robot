"""Obsidian integration: the lab's knowledge as plain Markdown notes inside your vault.

A vault is just a folder of .md files, so no plugin or API is needed (the Dataview plugin only makes the
dashboard nicer). Everything OFO writes lives in <vault>/OFO/ and nowhere else:

  OFO.md                 dashboard (counts, links, Dataview tables)
  Strategie/<name>.md    one note per tested strategy: verdict, surface, risk/reward, links to paper and dossier
  Pronte/<name>.md       dossier of every Judge-approved strategy (size, risk, pass probability)
  Deployment/<name>.md   paper-trading ledger summary
  Concetti/<concept>.md  the knowledge network: every concept links the papers and strategies about it, with what was learned
  Paper/<title>.md       papers the Scout found (abstract, why chosen, strategies that came from it)
  Proposte AI.md         what the agents think is worth your attention (updated by them, you annotate below the marker)
  Idee/<A x B>.md        new ideas the Researcher built by connecting two concepts (with links to both)
  Diario/<date>.md       what happened each day
  Ideas/                 YOU drop notes here; the Researcher reads them like PDFs (treated as untrusted text)

Anything you write BELOW the line `<!-- ofo:end -->` of a generated note is kept when OFO rewrites it, so you can
annotate strategies and papers. Files that are not OFO's are never touched or deleted."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

END = "<!-- ofo:end -->"
SUBDIRS = ("Strategie", "Pronte", "Deployment", "Paper", "Concetti", "Idee", "Diario", "Ideas")
KEY_EVENTS = {("judge", "verdict"), ("lab", "ready"), ("trader", "deployed"), ("trader", "retired"),
              ("trader", "confirmed"), ("trader", "signal"), ("scout", "download"), ("lab", "error"),
              ("planner", "queued"), ("lab", "budget"), ("trader", "skipped")}

T = {
    "it": {"rationale": "Ipotesi", "source": "Fonte", "verdict": "Verdetto", "surface": "Robustezza dei parametri",
           "exits": "Rischio / rendimento", "planner": "Planner", "auditor": "Auditor", "dossier": "Strategia pronta",
           "notes": "Le mie note", "papers": "Strategie nate da questo paper", "none": "nessuna ancora",
           "ready": "Pronte", "deployed": "In linea (paper)", "recent": "Ultimi test", "events": "Eventi del giorno",
           "idea_hint": "Metti qui le tue note: il Researcher le legge come i PDF.", "abstract": "Abstract",
           "why": "Perché è stato scelto", "prop_title": "Proposte dell'AI", "prop_ok": "Approvate dal Giudice",
           "prop_prom": "Promettenti (reggono i controlli sui parametri ma non il Giudice)", "prop_ideas": "Nuove idee da collegamenti",
           "prop_next": "Prossimo passo", "prop_hint": "Scritto dagli agenti. Annota sotto la riga di fine: resta.", "concepts": "Concetti", "related": "Concetti collegati", "learned": "Cosa abbiamo imparato",
           "tested": "Strategie testate", "ledger": "Registro paper trading", "size": "Dimensione", "empty": "–"},
    "en": {"rationale": "Hypothesis", "source": "Source", "verdict": "Verdict", "surface": "Parameter robustness",
           "exits": "Risk / reward", "planner": "Planner", "auditor": "Auditor", "dossier": "Ready strategy",
           "notes": "My notes", "papers": "Strategies from this paper", "none": "none yet",
           "ready": "Ready", "deployed": "Deployed (paper)", "recent": "Latest tests", "events": "Events of the day",
           "idea_hint": "Drop your notes here: the Researcher reads them like PDFs.", "abstract": "Abstract",
           "why": "Why it was picked", "prop_title": "AI proposals", "prop_ok": "Approved by the Judge",
           "prop_prom": "Promising (pass the parameter checks but not the Judge)", "prop_ideas": "New ideas from connections",
           "prop_next": "Next step", "prop_hint": "Written by the agents. Annotate below the end line: it stays.", "concepts": "Concepts", "related": "Related concepts", "learned": "What we learned",
           "tested": "Strategies tested", "ledger": "Paper-trading ledger", "size": "Size", "empty": "–"},
}


def safe(name: str, limit: int = 80) -> str:
    """A file name Obsidian and every OS accept; also the wikilink target."""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]+', "-", str(name)).strip(" .-")
    return (s or "nota")[:limit]


def _yaml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(round(v, 4)) if isinstance(v, float) else str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_yaml(x) for x in v) + "]"
    if v is None:
        return "null"
    return json.dumps(str(v), ensure_ascii=False)


def render(front: dict, body: str, keep: str = "", notes_title: str = "Le mie note") -> str:
    fm = "\n".join(f"{k}: {_yaml(v)}" for k, v in front.items())
    tail = keep if keep else f"\n## {notes_title}\n\n"
    return f"---\n{fm}\n---\n\n{body.rstrip()}\n\n{END}\n{tail}"


def read_kept(path: Path) -> str:
    if not path.exists():
        return ""
    txt = path.read_text(encoding="utf-8", errors="replace")
    return txt.split(END, 1)[1].lstrip("\n") if END in txt else ""


def write_note(path: Path, front: dict, body: str, notes_title: str) -> bool:
    """Write only if the generated part changed; keep the owner's own text below the end marker."""
    if path.exists() and END not in path.read_text(encoding="utf-8", errors="replace"):
        return False                                    # not our file (or the marker was deleted): leave it alone
    out = render({"ofo_generated": True, **front}, body, read_kept(path), notes_title)
    if path.exists() and path.read_text(encoding="utf-8") == out:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8")
    return True


def ensure_vault(vault: str | Path) -> Path:
    v = Path(vault).expanduser()
    if not v.is_dir():
        raise ValueError(f"vault folder not found: {v}")
    root = v / "OFO"
    for d in SUBDIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def _pct(x):
    return "–" if x is None else f"{x:.0%}"


def strategy_note(rep: dict, planner, auditor, papers_by_title: dict, dossier_names: set, L: dict) -> tuple[dict, str]:
    s, lk = rep["surface"], rep["locked"]
    ex = rep.get("exits")
    paper = next((t for t in papers_by_title if t and t in (rep.get("source") or "")), None)
    front = {"tags": ["ofo/strategy", f"ofo/{lk['status'].lower()}"], "template": rep["template"],
             "verdict": lk["status"], "candidates": rep["n_candidates"], "median_sharpe": s["median_sharpe"],
             "robust": s["robust"], "plateau": s["plateau_ratio"], "wf_oos_pnl": rep["wf_oos_pnl"],
             "p_pass": lk.get("p_pass"), "paper": paper, "ready": rep["name"] in dossier_names,
             "market": rep.get("market") or "NQ"}
    b = [f"# {rep['name']}", "", f"## {L['rationale']}", rep.get("rationale") or L["empty"], ""]
    if paper:
        b += [f"{L['source']}: [[Paper - {safe(paper)}]]", ""]
    elif rep.get("source"):
        b += [f"{L['source']}: {rep['source']}", ""]
    b += [f"## {L['verdict']}: {lk['status']}", ""]
    if lk.get("checks"):
        b += [f"- {'✅' if v else '❌'} {k}" for k, v in lk["checks"].items()]
    if lk.get("reason"):
        b += [lk["reason"]]
    if lk.get("p_pass") is not None:
        b += ["", f"P(pass) {_pct(lk['p_pass'])} · bust {_pct(lk.get('p_bust'))} · P&L {lk.get('pnl', 0):+,.0f}$"]
    b += ["", f"## {L['surface']}", f"median Sharpe {s['median_sharpe']:+.3f} · positive {_pct(s['frac_positive'])} · "
          f"plateau {s['plateau_ratio']:.2f} · robust={s['robust']}"]
    if ex:
        b += ["", f"## {L['exits']}", f"baseline Sharpe {ex['baseline']['sharpe']:+.3f} · chosen={ex['chosen']}"
              + (f" · stop {ex['best']['stop_atr']}x ATR, rr {ex['best']['rr']}" if ex["chosen"] else ""), "",
              "| stop ATR | rr | Sharpe | win | n |", "|---|---|---|---|---|"]
        b += [f"| {c['stop_atr']} | {c['rr'] if c['rr'] else '–'} | {c['sharpe']:+.3f} | {_pct(c.get('win_rate'))} | {c.get('n_trades')} |"
              for c in ex["grid"]]
    tm_ = (rep.get("locked") or {}).get("timing") or rep.get("timing")
    if tm_:
        b += ["", "## Days to pass / Giorni per passare" + (" (in-sample)" if tm_.get("in_sample") else ""), "",
              "| account | micro | fastest | median | within window |", "|---|---|---|---|---|"]
        b += [f"| {a} | {r['n_micro']} | {r['min_days_seen'] if r['min_days_seen'] is not None else '–'} | "
              f"{('%.0f' % r['median_days']) if r['median_days'] is not None else '–'} | {r['p_pass_within'].get(str(tm_['window_days']), 0):.0%} |"
              for a, r in tm_["accounts"].items()]
    if rep["name"] in dossier_names:
        b += ["", f"## {L['dossier']}", f"[[{safe(rep['name'])} (pronta)]]"]
    if planner and planner.get("reason"):
        b += ["", f"## {L['planner']}", f"{planner.get('decision')}: {planner['reason']}"]
    if auditor and auditor.get("summary"):
        b += ["", f"## {L['auditor']}", auditor["summary"]] + [f"- ⚠ {f}" for f in auditor.get("risk_flags", [])]
    return front, "\n".join(b)


def dossier_note(d: dict, L: dict) -> tuple[dict, str]:
    front = {"tags": ["ofo/ready"], "template": d["template"], "tf": d["tf"], "oos_days": d["n_oos_days"],
             "created": d.get("created")}
    b = [f"# {d['headline']}", "", d["strategy_text"], ""]
    for acct, a in d["accounts"].items():
        b += [f"## {acct}", a["text"], "", f"| micro | pass | lower | bust |", "|---|---|---|---|"]
        b += [f"| {r['micro_equiv']} | {_pct(r['p_pass'])} | {_pct(r['p_pass_lo'])} | {_pct(r['p_bust'])} |" for r in a["table"]]
        if a.get("multi"):
            b += ["| accounts | P(≥1 pass) | P(≥1 payout) | cost |", "|---|---|---|---|"]
            b += [f"| {r['accounts']} | {_pct(r['p_pass_any'])} | {_pct(r['p_payout_any'])} | ${r['expected_cost']:,.0f} |" for r in a["multi"]["table"]]
        b += [""]
    if d.get("comparison"):
        b += [d["comparison"], ""]
    b += [f"> {d['caveat']}", "", f"Strategia: [[{safe(d['name'])}]]"]
    return front, "\n".join(b)


def deployment_note(x: dict, L: dict) -> tuple[dict, str]:
    f = x.get("forward") or {}
    front = {"tags": ["ofo/deployment", f"ofo/{x['status']}"], "status": x["status"], "account": x.get("account"),
             "micro": x.get("micro"), "days": f.get("days", 0), "pnl": f.get("pnl", 0.0), "since": x.get("cut")}
    b = [f"# {x['name']} — {x['status'].upper()}", ""]
    if x.get("reason"):
        b += [x["reason"], ""]
    b += [f"{L['size']}: {x.get('micro')} micro · {x.get('account')}", f"## {L['ledger']}",
          f"{f.get('days', 0)} days · P&L {f.get('pnl', 0):+,.0f}$" + (f" · max DD {f['max_drawdown']:,.0f}$" if f.get("max_drawdown") else ""), ""]
    eq = f.get("equity") or []
    if eq:
        b += ["| date | equity |", "|---|---|"] + [f"| {d} | {v:+,.0f} |" for d, v in eq[-30:]]
    b += ["", f"Strategia: [[{safe(x['name'])}]]"]
    return front, "\n".join(b)


def paper_note(pid: str, p: dict, strategies: list[str], L: dict) -> tuple[dict, str]:
    front = {"tags": ["ofo/paper"], "source": p.get("source"), "status": p.get("status"), "published": p.get("published"),
             "url": p.get("url")}
    b = [f"# {p.get('title')}", "", f"{p.get('url') or ''}", "", f"## {L['why']}", p.get("why") or L["empty"], ""]
    if p.get("abstract"):
        b += [f"## {L['abstract']}", p["abstract"], ""]
    b += [f"## {L['papers']}"] + ([f"- [[{safe(s)}]]" for s in strategies] or [L["none"]])
    return front, "\n".join(b)


def concept_link(c: str) -> str:
    return f"[[Concetto - {safe(c)}|{c}]]"


def concept_note(name: str, d: dict, L: dict) -> tuple[dict, str]:
    front = {"tags": ["ofo/concept"], "tested": d["tested"], "approved": d["approved"], "papers": len(d["papers"])}
    b = [f"# {name}", "", f"## {L['learned']}"] + ([f"- {x}" for x in d["lessons"]] or [f"- {L['none']}"])
    b += ["", "## Paper"] + ([f"- [[Paper - {safe(t)}|{t}]]" for t in d["papers"]] or [f"- {L['none']}"])
    b += ["", f"## {L['tested']}"] + ([f"- [[{safe(x)}]]" for x in d["strategies"]] or [f"- {L['none']}"])
    if d.get("by_market"):
        b += ["", "## Per mercato / by market"] + [f"- {m}: {v['tested']} tested, {v['approved']} approved" for m, v in sorted(d["by_market"].items())]
    if d["related"]:
        b += ["", f"## {L['related']}"] + [f"- {concept_link(x)}" for x in d["related"]]
    return front, "\n".join(b)


def sync(vault: str | Path, studies: list[dict], dossiers: list[dict], deployments: list[dict], papers: dict,
         events: list[dict], lang: str = "it", now: dt.datetime | None = None, graph: dict | None = None) -> dict:
    """studies: [{report, planner, auditor}]. Returns counts of notes written."""
    L = T["it" if str(lang).lower().startswith(("it", "ital")) else "en"]
    root = ensure_vault(vault)
    now = now or dt.datetime.now()
    wrote = {"concepts": 0, "strategies": 0, "ready": 0, "deployments": 0, "papers": 0, "diary": 0, "dashboard": 0}
    dnames = {d["name"] for d in dossiers}
    titles = {p.get("title"): pid for pid, p in papers.items() if p.get("title") and p.get("status") in ("downloaded", "abstract")}
    by_paper: dict[str, list[str]] = {}
    for st in studies:
        rep = st["report"]
        front, body = strategy_note(rep, st.get("planner"), st.get("auditor"), titles, dnames, L)
        cs = (graph or {}).get("strategy_concepts", {}).get(rep["name"], [])
        if cs:
            front["concepts"] = cs
            body += f"\n\n## {L['concepts']}\n" + " · ".join(concept_link(c) for c in cs)
        wrote["strategies"] += write_note(root / "Strategie" / f"{safe(rep['name'])}.md", front, body, L["notes"])
        if front.get("paper"):
            by_paper.setdefault(front["paper"], []).append(rep["name"])
    for d in dossiers:
        front, body = dossier_note(d, L)
        wrote["ready"] += write_note(root / "Pronte" / f"{safe(d['name'])} (pronta).md", front, body, L["notes"])
    for x in deployments:
        if x.get("status") == "not_deployed":
            continue
        front, body = deployment_note(x, L)
        wrote["deployments"] += write_note(root / "Deployment" / f"{safe(x['name'])}.md", front, body, L["notes"])
    for pid, p in papers.items():
        if p.get("status") not in ("downloaded", "abstract"):
            continue
        front, body = paper_note(pid, p, by_paper.get(p.get("title"), []), L)
        cs = (graph or {}).get("paper_concepts", {}).get(p.get("title"), [])
        if cs:
            front["concepts"] = cs
            body += f"\n\n## {L['concepts']}\n" + " · ".join(concept_link(c) for c in cs)
        wrote["papers"] += write_note(root / "Paper" / f"Paper - {safe(p.get('title') or pid)}.md", front, body, L["notes"])
    for c, d in ((graph or {}).get("concepts") or {}).items():
        front, body = concept_note(c, d, L)
        wrote["concepts"] += write_note(root / "Concetti" / f"Concetto - {safe(c)}.md", front, body, L["notes"])
    ok = [st for st in studies if st["report"]["locked"]["status"] == "APPROVED"]
    prom = [st for st in studies if st not in ok and st["report"]["surface"]["robust"] and st["report"]["wf_oos_pnl"] > 0]
    ideas = sorted(p.stem for p in (root / "Idee").glob("*.md"))

    def entry(st):
        r, a, pl = st["report"], st.get("auditor") or {}, st.get("planner") or {}
        nxt = a.get("next_step") or pl.get("reason") or ""
        return [f"- [[{safe(r['name'])}]] — {r['locked']['status']}", f"  - {r.get('rationale', '')[:200]}"] + (
            [f"  - {L['prop_next']}: {nxt[:200]}"] if nxt else [])
    prop = [f"# {L['prop_title']}", "", L["prop_hint"], "", f"## {L['prop_ok']}"] + (sum((entry(x) for x in ok), []) or [f"- {L['none']}"])
    prop += ["", f"## {L['prop_prom']}"] + (sum((entry(x) for x in prom), []) or [f"- {L['none']}"])
    prop += ["", f"## {L['prop_ideas']}"] + ([f"- [[{x}]]" for x in ideas] or [f"- {L['none']}"])
    wrote["proposals"] = write_note(root / "Proposte AI.md", {"tags": ["ofo/proposals"], "approved": len(ok), "promising": len(prom)},
                                    "\n".join(prop), L["notes"])
    today = now.strftime("%Y-%m-%d")
    todays = [e for e in events if e.get("t", "")[:10] == today and (e["agent"], e["type"]) in KEY_EVENTS]
    if todays:
        lines = [f"- `{e['t'][11:16]}` **{e['agent']}** {e['type']}: {str(e.get('message', ''))[:200]}" for e in todays[-200:]]
        wrote["diary"] += write_note(root / "Diario" / f"{today}.md", {"tags": ["ofo/diary"], "date": today},
                                     f"# {today}\n\n## {L['events']}\n" + "\n".join(lines), L["notes"])
    recent = sorted(studies, key=lambda s: s.get("saved_at") or "", reverse=True)[:10]
    dash = [f"# OFO", "", f"{L['idea_hint']} → `OFO/Ideas/`", "", f"[[Proposte AI]]", "",
            f"## {L['ready']} ({len(dossiers)})"] + ([f"- [[{safe(d['name'])} (pronta)]]" for d in dossiers] or [f"- {L['none']}"])
    dash += ["", f"## {L['deployed']}"] + ([f"- [[{safe(x['name'])}]] — {x['status']}" for x in deployments if x.get("status") != "not_deployed"] or [f"- {L['none']}"])
    dash += ["", f"## {L['recent']}"] + [f"- [[{safe(s['report']['name'])}]] — {s['report']['locked']['status']}" for s in recent]
    if graph and graph.get("concepts"):
        top = sorted(graph["concepts"].items(), key=lambda kv: -(len(kv[1]["papers"]) + kv[1]["tested"]))[:15]
        dash += ["", f"## {L['concepts']}"] + [f"- {concept_link(c)} — {d['tested']} test, {d['approved']} ok, {len(d['papers'])} paper" for c, d in top]
    dash += ["", "```dataview", 'TABLE verdict, median_sharpe, p_pass, ready FROM "OFO/Strategie" SORT file.mtime DESC', "```"]
    wrote["dashboard"] += write_note(root / "OFO.md", {"tags": ["ofo/dashboard"], "updated": now.strftime("%Y-%m-%d")},
                                     "\n".join(dash), L["notes"])
    return wrote


WIKILINK = re.compile(r"\[\[([^\]|#]+)")
MAX_NOTES = 600


def read_notes(vault: str | Path) -> dict:
    """The vault's OFO/ notes as the Researcher may read them: title, links, what the OWNER wrote (below the end
    marker, or the whole note for notes OFO did not generate). Only OFO/ is read, never the rest of the vault,
    because this text goes to an AI service. All of it is untrusted text for the model."""
    root = Path(vault).expanduser() / "OFO"
    out: dict = {}
    for folder in SUBDIRS:
        d = root / folder
        for p in sorted(d.glob("*.md")) if d.is_dir() else []:
            if len(out) >= MAX_NOTES or p.stat().st_size > 200_000:
                continue
            txt = p.read_text(encoding="utf-8", errors="replace")
            gen = END in txt
            body, ann = txt.split(END, 1) if gen else (txt, "")
            ann = re.sub(r"^\s*## [^\n]*\n", "", ann.strip()).strip()
            head = body.split("---", 2)[-1].strip() if body.startswith("---") else body.strip()
            out[p.stem] = {"folder": folder, "generated": gen, "annotation": ann[:600], "head": head[:500],
                           "links": sorted({m.strip() for m in WIKILINK.findall(txt)})}
    return out


def owner_pairs(notes: dict) -> list[tuple[str, str]]:
    """Concept pairs the OWNER linked together: a note of theirs (or an annotation) mentioning two concept notes.
    That is the strongest hint of an idea worth exploring."""
    pairs = []
    for name, n in notes.items():
        text_links = n["links"] if not n["generated"] else sorted({m.strip() for m in WIKILINK.findall(n["annotation"])})
        cs = {x[len("Concetto - "):] for x in text_links if x.startswith("Concetto - ")}
        if name.startswith("Concetto - ") and cs:               # an annotation on a concept note links THAT concept to the others
            cs.add(name[len("Concetto - "):])
        cs = sorted(cs)
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                pairs.append((cs[i], cs[j]))
    return sorted(set(pairs))


def idea_note(vault: str | Path, a: str, b: str, strategies: list[str], why: str, lang: str = "it") -> Path:
    L = T["it" if str(lang).lower().startswith(("it", "ital")) else "en"]
    root = ensure_vault(vault)
    path = root / "Idee" / f"{safe(a)} x {safe(b)}.md"
    front = {"tags": ["ofo/idea"], "concepts": [a, b], "strategies": strategies}
    body = "\n".join([f"# {a} × {b}", "", f"{concept_link(a)} + {concept_link(b)}", "",
                      f"## {L['rationale']}", why or L["empty"], "", "## " + L["tested"]] +
                     ([f"- [[{safe(x)}]]" for x in strategies] or [f"- {L['none']}"]))
    write_note(path, front, body, L["notes"])
    return path
