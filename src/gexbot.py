"""Connettore GexBot (docs.gexbot.com): livelli opzioni -> options del levels.json.

Endpoint usati (REST v2): GET /{ticker}/classic/{category} e GET /futures/conversion.
Auth: header `Authorization: Bearer <GEXBOT_API_KEY>` + `User-Agent`. La chiave sta SOLO in .env.

Mappatura (interpretazione, dichiarata anche in provenienza e report):
  zero_gamma                       -> options.gamma_flip
  major_pos_{vol|oi}               -> options.call_wall   (strike con GEX positivo massimo)
  major_neg_{vol|oi}               -> options.put_wall    (strike con GEX negativo massimo)
I livelli sono su NDX: con `convert=true` vengono portati su NQ con valore*multiplier + additive.
"""
from __future__ import annotations

import json
import random
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .config import GexBotCfg
from .security import redact

NY = ZoneInfo("America/New_York")
RETRY_CODES = (429, 500, 502, 503, 504)


class GexBotError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(redact(message))


class GexBotClient:
    def __init__(self, cfg: GexBotCfg, api_key: str | None, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> None:
        self.cfg, self._key = cfg, api_key
        self._s = session or requests.Session()
        self._sleep, self._rng = sleep, rng or random.Random()

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self._key:
            raise GexBotError("GEXBOT_API_KEY mancante: impostarla in .env")
        url = self.cfg.base_url.rstrip("/") + path
        headers = {"Authorization": f"Bearer {self._key}", "User-Agent": self.cfg.user_agent,
                   "Accept": "application/json"}
        last = ""
        for attempt in range(self.cfg.max_retries + 1):
            try:
                r = self._s.get(url, params=params, headers=headers, timeout=self.cfg.timeout_seconds)
            except requests.Timeout:
                last = "timeout"
            except requests.RequestException as exc:
                last = f"errore di rete ({type(exc).__name__})"
            else:
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        raise GexBotError("risposta GexBot non JSON") from None
                    if not isinstance(data, dict):
                        raise GexBotError("risposta GexBot inattesa (non è un oggetto JSON)")
                    return data
                if r.status_code not in RETRY_CODES:
                    hint = {401: " (chiave non valida)", 403: " (piano/permessi insufficienti)",
                            404: " (ticker/categoria non trovati)"}.get(r.status_code, "")
                    raise GexBotError(f"GexBot HTTP {r.status_code}{hint} su {path}")
                last = f"HTTP {r.status_code}"
            if attempt < self.cfg.max_retries:
                self._sleep(self.cfg.backoff_base_seconds * 2 ** attempt + self._rng.uniform(0, 0.5))
        raise GexBotError(f"GexBot non raggiungibile su {path}: {last} dopo {self.cfg.max_retries} retry")

    def classic(self, ticker: str, category: str) -> dict[str, Any]:
        return self._get(f"/{ticker}/classic/{category}")

    def conversion(self, ticker: str, future: str, model: str | None = None) -> dict[str, Any]:
        params = {"ticker": ticker, "future": future}
        if model:
            params["model"] = model
        return self._get("/futures/conversion", params)


@dataclass
class GexResult:
    options: dict[str, Any] = field(default_factory=dict)       # gamma_flip/call_wall/put_wall (+ gexbot)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _inst(cfg: GexBotCfg, instrument: str) -> tuple[str, str, str]:
    if instrument == "es":
        return cfg.es_ticker, cfg.es_conversion_ticker, cfg.es_future
    return cfg.ticker, cfg.conversion_ticker, cfg.future


def fetch_options_levels(client: Any, cfg: GexBotCfg, run_date: str, instrument: str = "nq") -> GexResult:
    """Scarica, converte sul future (NQ o ES) e valida i livelli. Non scrive nulla."""
    res = GexResult()
    ticker, conv_ticker, future = _inst(cfg, instrument)
    raw = client.classic(ticker, cfg.category)
    zg = _num(raw.get("zero_gamma"))
    pos = _num(raw.get(f"major_pos_{cfg.wall_basis}"))
    neg = _num(raw.get(f"major_neg_{cfg.wall_basis}"))
    for name, v in (("zero_gamma", zg), (f"major_pos_{cfg.wall_basis}", pos), (f"major_neg_{cfg.wall_basis}", neg)):
        if v is None:
            res.warnings.append(f"GexBot: campo {name} assente o non numerico (non inventato)")

    conv: dict[str, Any] | None = None
    mult, add = 1.0, 0.0
    if cfg.convert:
        c = client.conversion(conv_ticker, future, cfg.conversion_model)
        m, a = _num(c.get("multiplier")), _num(c.get("additive"))
        if m is None or a is None or m <= 0:
            raise GexBotError("conversione futures non valida: multiplier/additive mancanti (livelli NON convertiti, run annullato)")
        mult, add = m, a
        conv = {"future_contract": c.get("future_contract"), "multiplier": m, "additive": a,
                "model": cfg.conversion_model or "default"}

    def cv(v: float | None) -> float | None:
        return None if v is None else round(v * mult + add, 2)

    ts = raw.get("timestamp")
    fetched = None
    if isinstance(ts, (int, float)):
        fetched = datetime.fromtimestamp(float(ts), timezone.utc)
        cutoff = datetime.strptime(run_date, "%Y-%m-%d").replace(hour=9, minute=30, tzinfo=NY)
        if fetched > cutoff:
            res.warnings.append(f"GexBot: dati generati alle {fetched.astimezone(NY):%Y-%m-%d %H:%M} ET, "
                                f"DOPO le 09:30 del {run_date} (non sono dati pre-market)")
        elif cutoff - fetched > timedelta(hours=cfg.max_age_hours):
            res.warnings.append(f"GexBot: dati vecchi ({fetched.astimezone(NY):%Y-%m-%d %H:%M} ET, "
                                f"oltre {cfg.max_age_hours:g}h prima dell'analisi)")
    else:
        res.warnings.append("GexBot: timestamp assente, freschezza dei dati non verificabile")

    levels = {"gamma_flip": cv(zg), "call_wall": cv(pos), "put_wall": cv(neg)}
    res.options = {k: v for k, v in levels.items() if v is not None}
    detail = {"endpoint": f"/{ticker}/classic/{cfg.category}", "wall_basis": cfg.wall_basis,
              "timestamp_utc": fetched.isoformat() if fetched else None, "conversion": conv,
              "raw_ndx": {"zero_gamma": zg, f"major_pos_{cfg.wall_basis}": pos, f"major_neg_{cfg.wall_basis}": neg,
                          "spot": _num(raw.get("spot"))}}
    res.options["gexbot"] = detail
    res.options["source"] = "gexbot"
    for k in ("gamma_flip", "call_wall", "put_wall"):
        if k in res.options:
            res.provenance[("es." if instrument == "es" else "") + f"options.{k}"] = {"source": "gexbot", "method": "api", "detail": detail["endpoint"],
                                              "mapping": {"gamma_flip": "zero_gamma", "call_wall": f"major_pos_{cfg.wall_basis}",
                                                          "put_wall": f"major_neg_{cfg.wall_basis}"}[k],
                                              "converted": bool(conv), "data_time_utc": detail["timestamp_utc"]}
    return res


def merge_into_levels(levels_path: Path, res: GexResult, run_date: str, instrument: str = "nq",
                      now: datetime | None = None) -> dict[str, Any]:
    """Aggiorna options e provenance di levels.json (backup .bak). Solo campi realmente ricevuti."""
    doc = json.loads(levels_path.read_text(encoding="utf-8")) if levels_path.exists() else {
        "date": run_date, "instrument": "NQ", "spot": 0, "options": {}, "levels": {}}
    if levels_path.exists():
        shutil.copyfile(levels_path, levels_path.with_suffix(levels_path.suffix + ".bak"))
    holder = doc.setdefault("es", {}) if instrument == "es" else doc
    opts = holder.setdefault("options", {})
    prev = {k: opts.get(k) for k in ("gamma_flip", "call_wall", "put_wall") if opts.get(k)}
    opts.update(res.options)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    prov = doc.setdefault("provenance", {})
    for k, v in res.provenance.items():
        prov[k] = {**v, "fetched_at": stamp, "previous_value": prev.get(k.split(".")[-1])}
    levels_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


class MockGexBotClient:
    """Risposte SINTETICHE deterministiche (solo --mock/test). Mai presentate come dati GexBot reali."""

    def __init__(self, spot: float, ts: float | None = None, scale: float = 1.0) -> None:
        self.spot, self.ts, self.scale = spot, ts, scale

    def classic(self, ticker: str, category: str) -> dict[str, Any]:
        k = self.scale
        s = self.spot - 40.0 / k  # livelli fittizi sul sottostante, vicini allo spot
        return {"timestamp": self.ts if self.ts is not None else 0, "ticker": ticker, "spot": s,
                "zero_gamma": round(s - 60 / k, 2), "major_pos_vol": round(s + 250 / k, 2),
                "major_neg_vol": round(s - 150 / k, 2), "major_pos_oi": round(s + 300 / k, 2),
                "major_neg_oi": round(s - 200 / k, 2), "strikes": []}

    def conversion(self, ticker: str, future: str, model: str | None = None) -> dict[str, Any]:
        return {"future_contract": f"{future}Z6", "multiplier": 1.0, "additive": 40.0 / self.scale}


def refresh_levels(cfg: Any, run_date: str, levels_path: Path, *, mock: bool, client: Any = None,
                   instruments: tuple[str, ...] = ("nq", "es")) -> tuple[list[str], list[str]]:
    """Scarica NQ (+ES) da GexBot e aggiorna levels.json. Ritorna (messaggi, avvisi). Un errore non cancella nulla."""
    from .security import get_secret
    msgs: list[str] = []
    warns: list[str] = []
    if client is None:
        if mock:
            doc = json.loads(levels_path.read_text(encoding="utf-8")) if levels_path.exists() else {}
            spot = float(doc.get("spot") or 29000.0)
            clients = {"nq": MockGexBotClient(spot), "es": MockGexBotClient(float((doc.get("es") or {}).get("spot") or spot / 4.45), scale=4.45)}
            warns.append("GexBot MOCK: livelli opzioni SINTETICI, non dati GexBot reali")
            ts = datetime.strptime(run_date, "%Y-%m-%d").replace(hour=8, tzinfo=NY).timestamp()
            for c in clients.values():
                c.ts = ts
        else:
            key = get_secret("GEXBOT_API_KEY", cfg.root)
            if not key:
                raise GexBotError("GEXBOT_API_KEY mancante: impostarla in .env")
            c = GexBotClient(cfg.gexbot, key)
            clients = {"nq": c, "es": c}
    else:
        clients = {"nq": client, "es": client}
    for inst in instruments:
        try:
            res = fetch_options_levels(clients[inst], cfg.gexbot, run_date, inst)
        except GexBotError as exc:
            warns.append(f"GexBot {inst.upper()}: {exc} — livelli esistenti NON modificati")
            continue
        if mock:
            res.options["source"] = "gexbot_mock"
            for p in res.provenance.values():
                p["source"] = "gexbot_mock"
        if not any(k in res.options for k in ("gamma_flip", "call_wall", "put_wall")):
            warns.append(f"GexBot {inst.upper()}: nessun livello utilizzabile nella risposta")
            continue
        merge_into_levels(levels_path, res, run_date, inst)
        warns += [f"{inst.upper()}: {w}" for w in res.warnings]
        msgs.append(f"GexBot {inst.upper()}: " + ", ".join(f"{k}={v}" for k, v in res.options.items() if k in ("gamma_flip", "call_wall", "put_wall")))
    return msgs, warns
