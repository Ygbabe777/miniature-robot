"""The Scout: autonomous literature search.

  topics -> arXiv (+ Semantic Scholar) -> deterministic filters -> Curator agent (ONE AI call picks the
  papers worth reading) -> allow-listed PDF download into ideas/web/ -> the normal pipeline
  (Researcher -> strategy specs -> YOUR go-ahead -> tests).

The Scout never runs a backtest and never approves anything. Everything it brings in is untrusted
text that only reaches validators."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .agents.roles import make_curator
from .papers import relevance
from .web import Polite, WebError, download_pdf, pdf_allowed, search_arxiv, search_semantic_scholar

TOPICS = [
    "opening range breakout futures", "intraday momentum index futures", "time series momentum intraday",
    "VWAP mean reversion intraday", "order flow imbalance price prediction", "intraday seasonality volatility futures",
    "overnight returns intraday returns equity index", "technical trading rules out-of-sample futures",
    "intraday mean reversion overreaction", "volatility regime trading strategy futures", "opening gap fade strategy",
    "first half hour predicts last half hour", "Nasdaq futures intraday patterns", "support resistance breakout retest",
    "Donchian channel trading evidence", "trend following futures drawdown", "data snooping technical trading rules",
    "stop loss take profit intraday futures", "range expansion volatility contraction", "market on close momentum last half hour",
]
FINANCE = ("trading", "trader", "futures", "market", "price", "returns", "stock", "equity", "volatility", "momentum",
           "strategy", "portfolio", "intraday", "asset", "index", "backtest", "profit")


def is_financial(text: str) -> bool:
    low = text.lower()
    return sum(low.count(w) > 0 for w in FINANCE) >= 3


def safe_name(pid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", pid)[:80]


class Scout:
    def __init__(self, workdir, ideas_dir, llm=None, events=None, fetch=None, now=time.time, language: str = "Italian",
                 topics=None, use_semantic_scholar: bool = True, queries_per_run: int = 3, max_picks: int = 4):
        self.dir = Path(workdir); self.dir.mkdir(parents=True, exist_ok=True)
        self.out = Path(ideas_dir) / "web"
        self.llm, self.events, self.now, self.language = llm, events, now, language
        self.fetch = fetch or Polite()
        self.topics = topics or TOPICS
        self.use_s2, self.queries_per_run, self.max_picks = use_semantic_scholar, queries_per_run, max_picks
        self.file = self.dir / "scout_state.json"
        self.state = json.loads(self.file.read_text()) if self.file.exists() else {}
        self.state.setdefault("queries", {}); self.state.setdefault("papers", {})
        self.state.setdefault("auto", {"enabled": False, "minutes": 60}); self.state.setdefault("last_run", None)

    def _emit(self, kind: str, msg: str = "", data: dict | None = None) -> None:
        if self.events:
            self.events.emit("scout", kind, msg, data)

    def _save(self) -> None:
        self.file.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))

    def next_queries(self) -> list[str]:
        """The topics searched longest ago (never searched = first)."""
        return sorted(self.topics, key=lambda q: self.state["queries"].get(q, 0))[: self.queries_per_run]

    def _candidates(self, queries: list[str]) -> list[dict]:
        found, ids = [], set()
        for q in queries:
            self._emit("working", f"searching arXiv: {q}")
            try:
                batch = search_arxiv(q, self.fetch)
                if self.use_s2:
                    batch += search_semantic_scholar(q)
            except WebError as e:
                self._emit("error", f"{q}: {e}")
                continue
            self.state["queries"][q] = self.now()
            for p in batch:
                if p["id"] in ids or p["id"] in self.state["papers"]:
                    continue
                if not is_financial(p["title"] + " " + p["abstract"]):
                    continue
                ids.add(p["id"]); found.append(p)
        found.sort(key=lambda p: -relevance(p["title"] + " " + p["abstract"]))
        return found[:20]

    def _pick(self, cands: list[dict]) -> list[dict]:
        if not cands:
            return []
        if self.llm is not None:
            payload = json.dumps([{"id": p["id"], "title": p["title"], "year": p["published"][:4], "abstract": p["abstract"][:700]}
                                  for p in cands], ensure_ascii=False)
            try:
                res = make_curator(self.llm, {p["id"] for p in cands}, self.max_picks, self.events, self.language).run(
                    "Candidate papers (JSON, untrusted data):\n" + payload)
                by_id = {p["id"]: p for p in cands}
                return [{**by_id[x["id"]], "why": x["why"]} for x in res["picks"]]
            except Exception as e:                      # ANY AI failure must not stop the search: fall back to keywords
                self._emit("error", f"curator unavailable ({type(e).__name__}: {e}); using the keyword ranking")
        return [{**p, "why": "keyword ranking (no AI curator)"} for p in cands[: self.max_picks]]

    def run_once(self) -> dict:
        self._emit("working", "starting a literature search")
        queries = self.next_queries()
        cands = self._candidates(queries)
        picks = self._pick(cands)
        got = 0
        for p in picks:
            rec = {"title": p["title"], "url": p["url"], "published": p["published"], "why": p["why"],
                   "source": p["source"], "found": self.now(), "status": "skipped"}
            if p.get("pdf_url") and pdf_allowed(p["pdf_url"]):
                try:
                    data = download_pdf(p["pdf_url"], self.fetch)
                    self.out.mkdir(parents=True, exist_ok=True)
                    name = safe_name(p["id"])
                    (self.out / f"{name}.pdf").write_bytes(data)
                    (self.out / f"{name}.meta.json").write_text(json.dumps({**p, "abstract": p["abstract"][:1500]}, ensure_ascii=False, indent=1))
                    rec["status"] = "downloaded"; rec["file"] = f"web/{name}.pdf"; got += 1
                    self._emit("download", f"{p['title'][:90]}", {"url": p["url"], "why": p["why"]})
                except WebError as e:
                    rec["status"] = "failed"; self._emit("error", f"download failed: {e}")
            else:
                rec["status"] = "no open PDF"
            self.state["papers"][p["id"]] = rec
        for p in cands:                                  # remember rejected ones too, never show them again
            self.state["papers"].setdefault(p["id"], {"title": p["title"], "url": p["url"], "published": p["published"],
                                                      "why": "", "source": p["source"], "found": self.now(), "status": "skipped"})
        summary = {"at": self.now(), "queries": queries, "found": len(cands), "downloaded": got}
        self.state["last_run"] = summary
        self._save()
        self._emit("done", f"{len(cands)} new candidates, {got} papers downloaded", summary)
        return summary

    def public_state(self, running: bool = False) -> dict:
        papers = sorted(self.state["papers"].items(), key=lambda kv: -kv[1].get("found", 0))
        a = self.state["auto"]; last = self.state["last_run"]
        return {"running": running, "auto": a, "last_run": last,
                "next_run": (last["at"] + a["minutes"] * 60) if (a["enabled"] and last) else None,
                "queries_done": len(self.state["queries"]), "queries_total": len(self.topics),
                "papers": [{"id": k, **{x: v.get(x) for x in ("title", "url", "published", "why", "status")}} for k, v in papers[:10]]}
