"""More places for the Scout to look, specifically for futures and the Nasdaq (NQ / MNQ): the Agent Reach bridge.

Agent Reach (github.com/Panniantong/Agent-Reach) is a toolbox for agents that can run shell commands: it installs and
health-checks upstream tools (Jina Reader, Exa search through mcporter, gh, yt-dlp, twitter-cli, rdt-cli, opencli ...) and
tells the agent how to call them. OFO's Researcher is deliberately NOT such an agent: it never runs commands, it only turns
text into validated JSON. So the integration lives one level below it, in the Scout, which is plain code:

    Scout (code) --allow-listed, argv-only commands / HTTP--> text --> ideas/web/*.md --> Researcher (text in, JSON out)

Safety rules, all enforced here:
  * no shell: commands are argument lists, the executable must be on a short allow-list, queries are sanitised (no leading
    dash, no control characters, bounded length), the child process gets a MINIMAL environment (never your API keys), a
    timeout and an output cap;
  * everything fetched is UNTRUSTED text: it can only ever reach the Researcher as data, and whatever the Researcher proposes
    still has to pass spec validation (templates and numeric bounds only) and the deterministic tests;
  * channels that need your accounts or cookies (Reddit, X/Twitter) are OFF by default and only run if you switch them on.

Nothing here needs Agent Reach to be installed: the Jina reader, GitHub search and RSS channels use plain HTTP. If you do
install Agent Reach (`pipx install https://github.com/Panniantong/agent-reach/archive/main.zip`, then `agent-reach install
--env=auto`), `agent_reach_status()` shows what its doctor says and the optional channels (Exa, YouTube, Reddit, X) can use
the tools it set up. Those command formats follow its documentation and are NOT tested against the real tools."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from .web import Polite, WebError

ALLOWED_BIN = {"mcporter", "gh", "yt-dlp", "twitter", "rdt", "opencli", "agent-reach"}
MAX_OUT = 120_000
MAX_DOC_CHARS = 40_000

# What we want more of: futures, and the Nasdaq-100 in particular. Searched first, before the generic topics.
FUTURES_TOPICS = [
    "NQ futures intraday strategy backtest", "Nasdaq-100 E-mini futures opening range breakout",
    "MNQ micro futures day trading strategy", "NQ futures VWAP mean reversion",
    "Nasdaq futures overnight gap fill statistics", "Nasdaq 100 futures volatility regime trading",
    "E-mini Nasdaq intraday momentum last half hour", "futures prop firm evaluation strategy drawdown",
    "index futures trend following drawdown control", "NQ ES futures session structure London New York open",
]
FUTURES_WORDS = ("nq", "mnq", "nasdaq", "e-mini", "emini", "futures", "es futures", "s&p 500 futures", "opening range",
                 "vwap", "prop firm", "intraday", "globex")

DEFAULT_RSS = [
    # Quant / systematic-trading blogs. URLs are from memory and NOT verified: a dead feed is skipped, never fatal.
    {"name": "Quantocracy", "url": "https://quantocracy.com/feed/"},
    {"name": "Robot Wealth", "url": "https://robotwealth.com/feed/"},
    {"name": "Alpha Architect", "url": "https://alphaarchitect.com/feed/"},
]
DEFAULT_CONFIG = {
    "channels": {"web_reader": True, "github": True, "rss": True, "exa": False, "youtube": False, "reddit": False, "twitter": False},
    "rss_feeds": DEFAULT_RSS, "max_doc_chars": MAX_DOC_CHARS,
}


class SourceError(RuntimeError):
    pass


def futures_score(text: str) -> int:
    low = text.lower()
    return sum(1 for w in FUTURES_WORDS if w in low)


def clean_query(q: str, limit: int = 160) -> str:
    q = re.sub(r"[^\w\s\-\+\.&/'\"]", " ", str(q), flags=re.U)
    q = " ".join(q.split())[:limit].lstrip("-+ ")
    if not q:
        raise SourceError("empty query")
    return q


def public_url(url: str) -> str:
    u = urllib.parse.urlparse(url.strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or host in ("localhost",) or host.endswith((".local", ".internal", ".lan")):
        raise SourceError(f"not a public web address: {url[:80]}")
    if re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):          # literal IPs: never (private ranges, metadata endpoints...)
        raise SourceError("IP addresses are not fetched")
    return url.strip()


def run_cli(argv: list[str], timeout: int = 60, max_bytes: int = MAX_OUT, runner=None) -> str:
    """Run an allow-listed program with arguments only (no shell), a minimal environment, a timeout and an output cap."""
    exe = Path(argv[0]).name
    if exe not in ALLOWED_BIN:
        raise SourceError(f"program not allowed: {exe}")
    if any("\x00" in a for a in argv):
        raise SourceError("bad argument")
    if runner is not None:
        return runner(argv)[:max_bytes]
    if shutil.which(argv[0]) is None:
        raise SourceError(f"{exe} is not installed")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "LANG", "TEMP", "TMP") if k in os.environ}
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, env=env, shell=False)
    except subprocess.TimeoutExpired as e:
        raise SourceError(f"{exe} timed out") from e
    out = (p.stdout or b"")[:max_bytes].decode("utf-8", errors="replace")
    if p.returncode != 0 and not out.strip():
        raise SourceError(f"{exe} failed: {(p.stderr or b'')[:200].decode('utf-8', errors='replace')}")
    return out


def _id(prefix: str, key: str) -> str:
    return f"{prefix}:{hashlib.sha256(key.encode()).hexdigest()[:14]}"


def _text_from_html_or_md(raw: bytes, limit: int) -> str:
    t = raw.decode("utf-8", errors="replace")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", t)).strip()[:limit]


class Channel:
    name = "channel"
    needs_account = False

    def __init__(self, fetch=None, runner=None, max_chars: int = MAX_DOC_CHARS):
        self.http = fetch or Polite(interval=1.5)
        self.runner, self.max_chars = runner, max_chars

    def available(self) -> bool:
        return True

    def search(self, query: str, n: int = 5) -> list[dict]:
        return []

    def read(self, cand: dict) -> str:
        """Full text of a candidate (already limited in size). Default: the snippet the search returned."""
        return cand.get("abstract", "")


class WebReader(Channel):
    """Any public page as clean text through Jina Reader (https://r.jina.ai/<url>): HTML in, markdown out, no key."""
    name = "web"

    def read(self, cand: dict) -> str:
        url = public_url(cand["url"])
        try:
            raw = self.http("https://r.jina.ai/" + url, max_bytes=2_000_000)
        except WebError as e:
            raise SourceError(str(e)) from e
        return _text_from_html_or_md(raw, self.max_chars)


class GitHubSearch(Channel):
    """Open-source strategy projects, through GitHub's public search API (no key; 10 requests a minute anonymously)."""
    name = "github"

    def search(self, query: str, n: int = 5) -> list[dict]:
        q = clean_query(query)
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q + " in:name,description,readme", "sort": "stars", "order": "desc", "per_page": n})
        try:
            items = json.loads(self.http(url, max_bytes=500_000)).get("items", [])
        except (WebError, ValueError) as e:
            raise SourceError(f"github search failed: {e}") from e
        return [{"id": _id("gh", i["full_name"]), "source": "github", "title": i["full_name"], "url": i["html_url"],
                 "abstract": (i.get("description") or "")[:600], "published": (i.get("pushed_at") or "")[:4], "pdf_url": "",
                 "repo": i["full_name"]} for i in items if i.get("full_name")]

    def read(self, cand: dict) -> str:
        repo = cand.get("repo", "")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise SourceError("bad repository name")
        try:
            raw = self.http(f"https://raw.githubusercontent.com/{repo}/HEAD/README.md", max_bytes=1_000_000)
        except WebError as e:
            raise SourceError(f"no README: {e}") from e
        return raw.decode("utf-8", errors="replace")[: self.max_chars]


class RssFeeds(Channel):
    """Quant blogs from a list of feeds you can edit (config/sources.json). Items whose text matches the query are offered."""
    name = "rss"

    def __init__(self, feeds=None, **kw):
        super().__init__(**kw)
        self.feeds = feeds if feeds is not None else DEFAULT_RSS

    def search(self, query: str, n: int = 5) -> list[dict]:
        words = [w for w in re.findall(r"[a-z0-9]+", clean_query(query).lower()) if len(w) > 2]
        out = []
        for f in self.feeds:
            try:
                raw = self.http(public_url(f["url"]), max_bytes=1_500_000)
                if b"<!DOCTYPE" in raw[:2000] or b"<!ENTITY" in raw[:5000]:
                    continue                                              # no entity tricks in feeds
                root = ET.fromstring(raw)
            except (WebError, SourceError, ET.ParseError):
                continue
            for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
                title = (it.findtext("title") or it.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if not link:
                    le = it.find("{http://www.w3.org/2005/Atom}link")
                    link = (le.get("href") if le is not None else "") or ""
                body = re.sub(r"<[^>]+>", " ", it.findtext("description") or it.findtext("{http://www.w3.org/2005/Atom}summary") or "")
                hay = (title + " " + body).lower()
                if link and sum(w in hay for w in words) >= max(1, len(words) // 3):
                    out.append({"id": _id("rss", link), "source": "rss", "title": title[:200], "url": link,
                                "abstract": " ".join(body.split())[:600], "published": "", "pdf_url": "", "feed": f["name"]})
        out.sort(key=lambda c: -futures_score(c["title"] + " " + c["abstract"]))
        return out[:n]

    def read(self, cand: dict) -> str:
        return WebReader(fetch=self.http, max_chars=self.max_chars).read(cand)


class ExaSearch(Channel):
    """Web search through Exa's MCP server using Agent Reach's documented command: `mcporter call exa.web_search_exa`."""
    name = "exa"

    def available(self) -> bool:
        return self.runner is not None or shutil.which("mcporter") is not None

    def search(self, query: str, n: int = 5) -> list[dict]:
        q = clean_query(query)
        out = run_cli(["mcporter", "call", "exa.web_search_exa", f"query={q}", f"numResults={int(n)}"], runner=self.runner)
        res = []
        for block in re.split(r"\n(?=Title:)", out):
            t, u = re.search(r"Title:\s*(.+)", block), re.search(r"URL:\s*(\S+)", block)
            if t and u:
                try:
                    url = public_url(u.group(1))
                except SourceError:
                    continue
                body = re.search(r"(?:Text|Highlights?|Summary):\s*(.+)", block, re.S)
                res.append({"id": _id("web", url), "source": "exa", "title": t.group(1).strip()[:200], "url": url,
                            "abstract": " ".join((body.group(1) if body else "").split())[:600], "published": "", "pdf_url": ""})
        return res[:n]

    def read(self, cand: dict) -> str:
        return WebReader(fetch=self.http, max_chars=self.max_chars).read(cand)


class YouTube(Channel):
    """Trading-video transcripts with yt-dlp (`ytsearchN:` + subtitles). Slow and token-hungry: off by default."""
    name = "youtube"

    def available(self) -> bool:
        return self.runner is not None or shutil.which("yt-dlp") is not None

    def search(self, query: str, n: int = 3) -> list[dict]:
        q = clean_query(query)
        out = run_cli(["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{int(n)}:{q}"], runner=self.runner)
        res = []
        for line in out.splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            vid = j.get("id", "")
            if re.fullmatch(r"[\w-]{6,20}", vid):
                res.append({"id": _id("yt", vid), "source": "youtube", "title": (j.get("title") or "")[:200],
                            "url": f"https://www.youtube.com/watch?v={vid}", "abstract": (j.get("description") or "")[:600],
                            "published": "", "pdf_url": "", "video": vid})
        return res

    def read(self, cand: dict) -> str:
        vid = cand.get("video", "")
        if not re.fullmatch(r"[\w-]{6,20}", vid):
            raise SourceError("bad video id")
        import tempfile
        d = tempfile.mkdtemp(prefix="ofo-yt-")
        run_cli(["yt-dlp", "--write-sub", "--write-auto-sub", "--sub-lang", "en", "--skip-download", "--no-warnings",
                 "-o", str(Path(d) / "%(id)s"), f"https://www.youtube.com/watch?v={vid}"], timeout=120, runner=self.runner)
        text = ""
        for f in Path(d).glob("*.vtt"):
            raw = f.read_text(encoding="utf-8", errors="replace")
            text = " ".join(l for l in raw.splitlines() if l and "-->" not in l and not l.startswith(("WEBVTT", "Kind:", "Language:")))
            break
        shutil.rmtree(d, ignore_errors=True)
        return re.sub(r"<[^>]+>", "", text)[: self.max_chars]


class SocialCLI(Channel):
    """Reddit (rdt-cli) and X/Twitter (twitter-cli) through the tools Agent Reach installs. They need YOUR login (cookies):
    off by default, read-only commands only, and the text is the lowest-trust source of all."""
    needs_account = True

    def __init__(self, kind: str, **kw):
        super().__init__(**kw)
        self.kind, self.name = kind, kind

    def available(self) -> bool:
        return self.runner is not None or shutil.which("rdt" if self.kind == "reddit" else "twitter") is not None

    def search(self, query: str, n: int = 8) -> list[dict]:
        q = clean_query(query)
        argv = ["rdt", "search", q, "--limit", str(int(n))] if self.kind == "reddit" else ["twitter", "search", q, "-n", str(int(n))]
        out = run_cli(argv, runner=self.runner).strip()
        if not out:
            return []
        return [{"id": _id(self.kind, q + out[:200]), "source": self.kind, "title": f"{self.kind}: {q}"[:200],
                 "url": "", "abstract": out[:600], "published": "", "pdf_url": "", "inline_text": out[: self.max_chars]}]

    def read(self, cand: dict) -> str:
        return cand.get("inline_text", cand.get("abstract", ""))


def load_config(path: str | Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and Path(path).exists():
        user = json.loads(Path(path).read_text())
        cfg["channels"].update(user.get("channels", {}))
        if user.get("rss_feeds") is not None:
            cfg["rss_feeds"] = user["rss_feeds"]
        cfg["max_doc_chars"] = int(user.get("max_doc_chars", cfg["max_doc_chars"]))
    return cfg


def build_channels(config_path: str | Path | None = None, fetch=None, runner=None) -> list[Channel]:
    cfg = load_config(config_path)
    on, kw = cfg["channels"], {"fetch": fetch, "runner": runner, "max_chars": cfg["max_doc_chars"]}
    chans: list[Channel] = []
    if on.get("github"):
        chans.append(GitHubSearch(**kw))
    if on.get("rss"):
        chans.append(RssFeeds(feeds=cfg["rss_feeds"], **kw))
    if on.get("exa"):
        chans.append(ExaSearch(**kw))
    if on.get("youtube"):
        chans.append(YouTube(**kw))
    for k in ("reddit", "twitter"):
        if on.get(k):
            chans.append(SocialCLI(k, **kw))
    return [c for c in chans if c.available()]


def agent_reach_status(runner=None) -> dict:
    """What `agent-reach doctor --json` reports, if Agent Reach is installed. Never raises."""
    try:
        out = run_cli(["agent-reach", "doctor", "--json"], timeout=45, runner=runner)
        data = json.loads(out)
        chans = data.get("channels", data) if isinstance(data, dict) else {}
        return {"installed": True, "channels": {k: {"status": v.get("status"), "message": str(v.get("message", ""))[:120],
                                                    "backend": v.get("active_backend")}
                                                for k, v in chans.items() if isinstance(v, dict)}}
    except (SourceError, ValueError):
        return {"installed": shutil.which("agent-reach") is not None, "channels": {}}


HINTS = {"exa": "install Agent Reach (it provides `mcporter`): pipx install https://github.com/Panniantong/agent-reach/archive/main.zip && agent-reach install --env=auto",
         "youtube": "install yt-dlp (pip install yt-dlp)", "reddit": "install Agent Reach's rdt-cli and log in", "twitter": "install Agent Reach's twitter-cli and log in"}


def missing_channels(config_path: str | Path | None = None, runner=None) -> list[dict]:
    """Channels the owner switched ON in config/sources.json whose tool is not installed on this computer."""
    cfg = load_config(config_path)
    on = cfg["channels"]
    out = []
    for k, cls in (("exa", ExaSearch), ("youtube", YouTube)):
        if on.get(k) and not cls(runner=runner).available():
            out.append({"name": k, "hint": HINTS[k]})
    for k in ("reddit", "twitter"):
        if on.get(k) and not SocialCLI(k, runner=runner).available():
            out.append({"name": k, "hint": HINTS[k]})
    return out
