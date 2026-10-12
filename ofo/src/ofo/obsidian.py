"""Obsidian integration: the lab's knowledge as plain Markdown notes inside your vault.

A vault is just a folder of .md files, so no plugin or API is needed (the Dataview plugin only makes the
dashboard nicer). Everything OFO writes lives in <vault>/OFO/ and nowhere else:

  OFO.md                 dashboard (counts, links, Dataview tables)
  Strategie/<name>.md    one note per tested strategy: verdict, surface, risk/reward, links to paper and dossier
  Pronte/<name>.md       dossier of every Judge-approved strategy (size, risk, pass probability)
  Deployment/<name>.md   paper-trading ledger summary
  Paper/<title>.md       papers the Scout found (abstract, why chosen, strategies that came from it)
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
SUBDIRS = ("Strategie", "Pronte", "Deployment", "Paper", "Diario", "Ideas")
KEY_EVENTS = {("judge", "verdict"), ("lab", "ready"), ("trader", "deployed"), ("trader", "retired"),
              ("trader", "confirmed"), ("trader", "signal"), ("scout", "download"), ("lab", "error"),
              ("planner", "queued"), ("lab", "budget"), ("trader", "skipped")}

T = {
    "it": {"rationale": "Ipotesi", "source": "Fonte", "verdict": "Verdetto", "surface": "Robustezza dei parametri",
           "exits": "Rischio / rendimento", "planner": "Planner", "auditor": "Auditor", "dossier": "Strategia pronta",
           "notes": "Le mie note", "papers": "Strategie nate da questo paper", "none": "nessuna ancora",
           "ready": "Pronte", "deployed": "In linea (paper)", "recent": "Ultimi test", "events": "Eventi del giorno",
           "idea_hint": "Metti qui le tue note: il Researcher le legge come i PDF.", "abstract": "Abstract",
           "why": "Perché è stato scelto", "ledger": "Registro paper trading", "size": "Dimensione", "empty": "–"},
    "en": {"rationale": "Hypothesis", "source": "Source", "verdict": "Verdict", "surface": "Parameter robustness",
           "exits": "Risk / reward", "planner": "Planner", "auditor": "Auditor", "dossier": "Ready strategy",
           "notes": "My notes", "papers": "Strategies from this paper", "none": "none yet",
           "ready": "Ready", "deployed": "Deployed (paper)", "recent": "Latest tests", "events": "Events of the day",
           "idea_hint": "Drop your notes here: the Researcher reads them like PDFs.", "abstract": "Abstract",
           "why": "Why it was picked", "ledger": "Paper-trading ledger", "size": "Size", "empty": "–"},
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
             "p_pass": lk.get("p_pass"), "paper": paper, "ready": rep["name"] in dossier_names}
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
        b += [""]
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


def sync(vault: str | Path, studies: list[dict], dossiers: list[dict], deployments: list[dict], papers: dict,
         events: list[dict], lang: str = "it", now: dt.datetime | None = None) -> dict:
    """studies: [{report, planner, auditor}]. Returns counts of notes written."""
    L = T["it" if str(lang).lower().startswith(("it", "ital")) else "en"]
    root = ensure_vault(vault)
    now = now or dt.datetime.now()
    wrote = {"strategies": 0, "ready": 0, "deployments": 0, "papers": 0, "diary": 0, "dashboard": 0}
    dnames = {d["name"] for d in dossiers}
    titles = {p.get("title"): pid for pid, p in papers.items() if p.get("title") and p.get("status") in ("downloaded", "abstract")}
    by_paper: dict[str, list[str]] = {}
    for st in studies:
        rep = st["report"]
        front, body = strategy_note(rep, st.get("planner"), st.get("auditor"), titles, dnames, L)
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
        wrote["papers"] += write_note(root / "Paper" / f"Paper - {safe(p.get('title') or pid)}.md", front, body, L["notes"])
    today = now.strftime("%Y-%m-%d")
    todays = [e for e in events if e.get("t", "")[:10] == today and (e["agent"], e["type"]) in KEY_EVENTS]
    if todays:
        lines = [f"- `{e['t'][11:16]}` **{e['agent']}** {e['type']}: {str(e.get('message', ''))[:200]}" for e in todays[-200:]]
        wrote["diary"] += write_note(root / "Diario" / f"{today}.md", {"tags": ["ofo/diary"], "date": today},
                                     f"# {today}\n\n## {L['events']}\n" + "\n".join(lines), L["notes"])
    recent = sorted(studies, key=lambda s: s.get("saved_at") or "", reverse=True)[:10]
    dash = [f"# OFO", "", f"{L['idea_hint']} → `OFO/Ideas/`", "",
            f"## {L['ready']} ({len(dossiers)})"] + ([f"- [[{safe(d['name'])} (pronta)]]" for d in dossiers] or [f"- {L['none']}"])
    dash += ["", f"## {L['deployed']}"] + ([f"- [[{safe(x['name'])}]] — {x['status']}" for x in deployments if x.get("status") != "not_deployed"] or [f"- {L['none']}"])
    dash += ["", f"## {L['recent']}"] + [f"- [[{safe(s['report']['name'])}]] — {s['report']['locked']['status']}" for s in recent]
    dash += ["", "```dataview", 'TABLE verdict, median_sharpe, p_pass, ready FROM "OFO/Strategie" SORT file.mtime DESC', "```"]
    wrote["dashboard"] += write_note(root / "OFO.md", {"tags": ["ofo/dashboard"], "updated": now.strftime("%Y-%m-%d")},
                                     "\n".join(dash), L["notes"])
    return wrote
