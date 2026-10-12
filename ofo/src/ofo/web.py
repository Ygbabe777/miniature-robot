"""Internet access for the Scout, kept deliberately narrow.

 - arXiv (q-fin) through its public API: stable, open, built for programs. Politeness rule: one
   request every 3 seconds.
 - Semantic Scholar as an optional second source (metadata only; it rate-limits anonymous use).
 - PDFs are downloaded ONLY from allow-listed hosts and must really be PDFs.
No scraping of blogs or forums: fragile, and often against their terms."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "OFO-Lab/0.1 (personal research tool)"
ATOM = "{http://www.w3.org/2005/Atom}"
ALLOWED_PDF_HOSTS = ("arxiv.org", "export.arxiv.org", "www.nber.org", "core.ac.uk", "openreview.net")
QFIN = "(cat:q-fin.TR OR cat:q-fin.ST OR cat:q-fin.CP OR cat:q-fin.PM OR cat:q-fin.GN OR cat:q-fin.MF)"


class WebError(RuntimeError):
    pass


def http_get(url: str, timeout: float = 30, max_bytes: int = 30 * 1024 * 1024) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise WebError(f"HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise WebError(f"unreachable: {e}") from e
    if len(data) > max_bytes:
        raise WebError("response too large")
    return data


class Polite:
    """Wraps a fetch function so successive requests are at least `interval` seconds apart."""
    def __init__(self, fetch=http_get, interval: float = 3.0, now=time.monotonic, sleep=time.sleep):
        self.fetch, self.interval, self.now, self.sleep, self.last = fetch, interval, now, sleep, None

    def __call__(self, url: str, **kw) -> bytes:
        if self.last is not None:
            wait = self.interval - (self.now() - self.last)
            if wait > 0:
                self.sleep(wait)
        try:
            return self.fetch(url, **kw)
        finally:
            self.last = self.now()


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def arxiv_id(url: str) -> str:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:v\d+)?(?:\.pdf)?$", url.strip())
    return m.group(1) if m else ""


def parse_arxiv(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall(ATOM + "entry"):
        url = _clean(e.findtext(ATOM + "id"))
        aid = arxiv_id(url)
        if not aid:
            continue
        pdf = next((l.get("href") for l in e.findall(ATOM + "link") if l.get("title") == "pdf"), f"https://arxiv.org/pdf/{aid}")
        out.append({"id": "arxiv:" + aid, "source": "arxiv", "title": _clean(e.findtext(ATOM + "title")),
                    "abstract": _clean(e.findtext(ATOM + "summary")), "url": f"https://arxiv.org/abs/{aid}",
                    "pdf_url": pdf, "published": _clean(e.findtext(ATOM + "published"))[:10],
                    "authors": [_clean(a.findtext(ATOM + "name")) for a in e.findall(ATOM + "author")][:6]})
    return out


def search_arxiv(query: str, fetch=None, max_results: int = 8, enough: int = 3) -> list[dict]:
    """A ladder of ever wider searches until there are `enough` hits: all words in q-fin, all words
    anywhere, then only the first two words in q-fin. arXiv's q-fin intraday corner is small."""
    fetch = fetch or Polite()
    def q(search):
        return ("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"search_query": search, "start": 0, "max_results": max_results, "sortBy": "relevance"}))
    terms = re.findall(r"[A-Za-z0-9\-]+", query)
    allw = "(" + " AND ".join(f"all:{t}" for t in terms) + ")"
    first = "(" + " AND ".join(f"all:{t}" for t in terms[:2]) + ")"
    ladder = [f"{allw} AND {QFIN}", allw] + ([f"{first} AND {QFIN}"] if len(terms) > 2 else [])
    seen, out = set(), []
    for search in ladder:
        for p in parse_arxiv(fetch(q(search))):
            if p["id"] not in seen:
                seen.add(p["id"]); out.append(p)
        if len(out) >= enough:
            break
    return out[:max_results]


def search_semantic_scholar(query: str, fetch=None, limit: int = 8) -> list[dict]:
    """Best effort: anonymous access is heavily rate-limited, so any failure just returns []."""
    fetch = fetch or Polite(interval=1.5)
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
        {"query": query, "limit": limit, "fields": "title,abstract,year,url,externalIds,openAccessPdf"}))
    try:
        data = json.loads(fetch(url))
    except (WebError, ValueError):
        return []
    out = []
    for p in data.get("data", []):
        if not p.get("title") or not p.get("abstract"):
            continue
        ax = (p.get("externalIds") or {}).get("ArXiv")
        pdf = f"https://arxiv.org/pdf/{ax}" if ax else ((p.get("openAccessPdf") or {}).get("url") or "")
        out.append({"id": ("arxiv:" + ax) if ax else "s2:" + str(p.get("paperId", p["title"][:40])), "source": "semanticscholar",
                    "title": _clean(p["title"]), "abstract": _clean(p["abstract"]), "url": p.get("url", ""),
                    "pdf_url": pdf, "published": str(p.get("year") or ""), "authors": []})
    return out


def search_ssrn(query: str, fetch=None, rows: int = 8) -> list[dict]:
    """SSRN papers through Crossref's official API (every SSRN item has a DOI with prefix 10.2139).
    Metadata only: SSRN offers no public API and its PDFs need a login and acceptance of its terms,
    so these are listed for the owner to download by hand. Best effort: failures return []."""
    fetch = fetch or Polite(interval=1.0)
    url = ("https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query": query, "filter": "prefix:10.2139", "rows": rows, "select": "DOI,title,abstract,issued"}))
    try:
        items = json.loads(fetch(url))["message"]["items"]
    except (WebError, ValueError, KeyError):
        return []
    out = []
    for it in items:
        doi, title = it.get("DOI", ""), _clean((it.get("title") or [""])[0])
        if not doi or not title:
            continue
        abstract = _clean(re.sub(r"<[^>]+>", " ", it.get("abstract") or "")).removeprefix("Abstract").strip()
        year = ((it.get("issued") or {}).get("date-parts") or [[""]])[0][0]
        out.append({"id": "ssrn:" + doi.lower().split("ssrn.")[-1], "source": "ssrn", "title": title, "abstract": abstract,
                    "url": f"https://doi.org/{doi}", "pdf_url": "", "published": str(year or ""), "authors": []})
    return out


def pdf_allowed(url: str) -> bool:
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and (u.hostname or "").lower() in ALLOWED_PDF_HOSTS


def download_pdf(url: str, fetch=None, max_bytes: int = 25 * 1024 * 1024) -> bytes:
    if not pdf_allowed(url):
        raise WebError(f"host not allowed for downloads: {url}")
    data = (fetch or Polite())(url, max_bytes=max_bytes)
    if not data.startswith(b"%PDF"):
        raise WebError("not a PDF")
    return data
