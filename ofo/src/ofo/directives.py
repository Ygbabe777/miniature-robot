"""What the owner tells the Researcher, in plain words: what to search, what to keep searching, what to stop searching.

Four kinds (the first words decide, in Italian or English; the owner can also pick the kind in the UI):
  search  "cerca ..." / "search ..."      look for it on the next run (once)
  keep    "continua ..." / "keep ..."     keep looking for it on EVERY run
  stop    "smetti ..." / "stop ..." / "evita ..."   never search it again and drop papers about it
  note    anything else                   a standing instruction for the Researcher when it reads papers or brainstorms
The Scout (code) applies search/keep/stop deterministically; every directive is also written into the Researcher's prompt as
the owner's instruction. The text is the owner's own, but the Researcher's output still has to pass spec validation and the
deterministic tests: a directive can steer WHAT is looked at, never what the Judge accepts."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

KINDS = ("search", "keep", "stop", "note")
PREFIXES = {
    "stop": r"(?:smetti(?:\s+di\s+cercare)?|basta(?:\s+con)?|non\s+cercare|evita|ignora|stop|stop\s+searching|avoid|ignore|don'?t\s+search)",
    "keep": r"(?:continua(?:\s+a\s+cercare)?|continuare\s+a\s+cercare|tieni\s+d'?occhio|keep(?:\s+searching)?|continue(?:\s+searching)?)",
    "search": r"(?:cerca(?:mi)?|trova|ricerca|cercare|search(?:\s+for)?|look\s+for|find)",
}
FILLER = {"di", "a", "su", "sul", "sulla", "il", "la", "lo", "le", "i", "gli", "un", "una", "per", "con", "the", "of", "on", "for", "about", "and", "e", "in"}


def split_kind(text: str, kind: str | None = None) -> tuple[str, str]:
    """(kind, topic). An explicit kind wins; otherwise the leading words decide; otherwise it is a note."""
    t = text.strip()
    for k, pat in PREFIXES.items():
        m = re.match(rf"^{pat}\b[\s:,-]*", t, flags=re.I)
        if m:
            return (kind if kind in KINDS else k), t[m.end():].strip() or t
    return (kind if kind in KINDS else "note"), t


def words(topic: str) -> list[str]:
    return [w for w in re.findall(r"[\w&\-]+", topic.lower()) if w not in FILLER and len(w) > 1]


class Directives:
    def __init__(self, path: str | Path, now=time.time):
        self.path, self.now = Path(path), now
        self.items: list[dict] = json.loads(self.path.read_text(encoding="utf-8", errors="ofo_legacy")) if self.path.exists() else []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from .storage import atomic_write
        atomic_write(self.path, json.dumps(self.items, indent=1, ensure_ascii=False))

    def add(self, text: str, kind: str | None = None) -> dict:
        text = " ".join(str(text).split())[:400]
        if not text:
            raise ValueError("empty directive")
        k, topic = split_kind(text, kind)
        item = {"id": uuid.uuid4().hex[:10], "kind": k, "topic": topic[:200], "text": text, "created": self.now(), "active": True}
        self.items.append(item); self._save()
        return item

    def remove(self, did: str) -> bool:
        n = len(self.items)
        self.items = [i for i in self.items if i["id"] != did]
        self._save()
        return len(self.items) < n

    def active(self, kind: str | None = None) -> list[dict]:
        return [i for i in self.items if i["active"] and (kind is None or i["kind"] == kind)]

    def take_searches(self) -> list[str]:
        """Topics to search on the next run: every 'keep' plus the 'search' ones, which are then used up."""
        out = [i["topic"] for i in self.active("keep")]
        for i in self.active("search"):
            out.append(i["topic"]); i["active"] = False
        self._save()
        return list(dict.fromkeys(out))

    def blocked(self, text: str) -> bool:
        """True if the text is about something the owner told us to stop searching (all the stop words appear in it)."""
        low = text.lower()
        for i in self.active("stop"):
            w = words(i["topic"])
            if w and all(x in low for x in w):
                return True
        return False

    def prompt_text(self) -> str:
        act = [i for i in self.items if i["active"] or i["kind"] == "search"][-12:]
        if not act:
            return ""
        label = {"search": "LOOK FOR", "keep": "KEEP LOOKING FOR", "stop": "DO NOT PROPOSE / AVOID", "note": "NOTE"}
        return ("OWNER'S STANDING INSTRUCTIONS (follow them when choosing what to propose; they never relax the rules above):\n"
                + "\n".join(f"- {label[i['kind']]}: {i['topic']}" for i in act) + "\n")

    def public(self) -> list[dict]:
        return [{k: i[k] for k in ("id", "kind", "topic", "text", "active")} for i in self.items][-30:]
