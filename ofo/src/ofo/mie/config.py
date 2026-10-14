"""rules/mie.json merged over built-in defaults; a short hash of the content is the config version stored with every observation."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[3] / "rules" / "mie.json"
_CACHE: dict = {}


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if k.startswith("_"):
            continue
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else HERE
    stamp = p.stat().st_mtime_ns if p.exists() else 0
    c = _CACHE.get(str(p))
    if c and c[0] == stamp:
        return c[1]
    base = json.loads(HERE.read_text(encoding="utf-8")) if HERE.exists() else {}
    cfg = _merge(base, json.loads(p.read_text(encoding="utf-8", errors="ofo_legacy")) if p.exists() and p != HERE else {})
    cfg["config_version"] = str(cfg.get("version", "1")) + "+" + hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:8]
    _CACHE[str(p)] = (stamp, cfg)
    return cfg
