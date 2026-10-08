"""Intake di dati da messaggi, screenshot e PDF.

Principio: l'estrazione produce solo CANDIDATI con provenienza; nulla entra in levels.json finche' un umano
non li accetta (`apply`). Niente viene stimato: testo -> regex deterministica; immagini -> modello vision con
testo letterale verificato; PDF -> testo + immagini incorporate. OHLCV resta CSV (si importa/unisce con validazione).
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from .agents import AgentRunner, OmniRouteClient
from .clock import SystemClock
from .config import AppConfig
from .data_loader import load_ohlcv
from .mock_llm import MockLLMClient
from .schemas import AgentStatus, VisionOutput
from .security import get_api_key, redact

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
TEXT_EXT = {".txt", ".md", ".log", ".csv"}
ALLOWED_EXT = set(IMAGE_EXT) | TEXT_EXT | {".pdf"}

BASE_FIELDS = {
    "spot": "Spot", "options.gamma_flip": "Gamma flip", "options.call_wall": "Call wall", "options.put_wall": "Put wall",
    "levels.previous_session_high": "Max sessione precedente", "levels.previous_session_low": "Min sessione precedente",
    "levels.overnight_high": "Max overnight", "levels.overnight_low": "Min overnight",
    "levels.previous_week_high": "Max settimana precedente", "levels.previous_week_low": "Min settimana precedente",
}
ALL_FIELDS = {**BASE_FIELDS, **{f"es.{k}": f"ES {v}" for k, v in BASE_FIELDS.items()}}

_SEP = r"[\s_-]*"
LABELS: dict[str, str] = {
    "options.gamma_flip": rf"gamma{_SEP}flip|zero{_SEP}gamma|g{_SEP}flip|gflip|flip{_SEP}level",
    "options.call_wall": rf"call{_SEP}wall|major{_SEP}pos(?:itive)?(?:{_SEP}(?:vol|oi))?|call{_SEP}resistance|\bcw\b",
    "options.put_wall": rf"put{_SEP}wall|major{_SEP}neg(?:ative)?(?:{_SEP}(?:vol|oi))?|put{_SEP}support|\bpw\b",
    "levels.previous_session_high": rf"(?:prev(?:ious)?|prec(?:edente)?|yesterday(?:'?s)?|ieri){_SEP}(?:session{_SEP}|sessione{_SEP}|day{_SEP})?(?:high|max(?:imo)?)|\bpdh\b|\bmax{_SEP}(?:di{_SEP})?ieri",
    "levels.previous_session_low": rf"(?:prev(?:ious)?|prec(?:edente)?|yesterday(?:'?s)?|ieri){_SEP}(?:session{_SEP}|sessione{_SEP}|day{_SEP})?(?:low|min(?:imo)?)|\bpdl\b|\bmin{_SEP}(?:di{_SEP})?ieri",
    "levels.overnight_high": rf"(?:overnight|globex|\bon\b){_SEP}(?:high|max(?:imo)?)|\bonh\b",
    "levels.overnight_low": rf"(?:overnight|globex|\bon\b){_SEP}(?:low|min(?:imo)?)|\bonl\b",
    "levels.previous_week_high": rf"(?:prev(?:ious)?|last|prec(?:edente)?|scorsa){_SEP}(?:week|settimana){_SEP}(?:high|max(?:imo)?)|\bpwh\b",
    "levels.previous_week_low": rf"(?:prev(?:ious)?|last|prec(?:edente)?|scorsa){_SEP}(?:week|settimana){_SEP}(?:low|min(?:imo)?)|\bpwl\b",
    "spot": r"\bspot\b|\blast(?:\s+price)?\b|prezzo(?:\s+attuale)?",
}
NUM = r"\d{1,3}(?:[ ,.']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_PATTERNS = {f: re.compile(rf"(?P<label>{lab})(?P<gap>[^\d\n]{{0,16}}?)(?P<num>{NUM})", re.I) for f, lab in LABELS.items()}
_HINT_ES = re.compile(r"(?<![A-Za-z])(?:/?es|spx|s&p(?:\s*500)?)(?![A-Za-z])", re.I)
_HINT_NQ = re.compile(r"(?<![A-Za-z])(?:/?nq|ndx|nasdaq(?:[\s-]*100)?)(?![A-Za-z])", re.I)
_OHLCV_HEAD = re.compile(r"^\s*timestamp\s*,\s*open\s*,\s*high\s*,\s*low\s*,\s*close\s*,\s*volume", re.I | re.M)


class IntakeError(Exception):
    pass


class Candidate(BaseModel):
    id: int
    field: str
    value: float
    source_file: str
    method: str                      # regex | vision
    snippet: str
    confidence: int = 80
    instrument_hint: str = "none"    # nq | es | none
    status: str = "pending"          # pending | accepted | rejected | applied
    conflict: bool = False
    note: str = ""
    edited_value: float | None = None
    model: str | None = None


def parse_number(raw: str) -> float | None:
    """'29,250' -> 29250 ; '28.964,5' -> 28964.5 ; '6,473.75' -> 6473.75 ; '28964.5' -> 28964.5."""
    t = raw.strip().replace(" ", "").replace("'", "")
    if not t:
        return None
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        parts = t.split(sep)
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3):
            t = "".join(parts)  # separatore delle migliaia
        else:
            t = parts[0] + "." + parts[1]
    try:
        return float(t)
    except ValueError:
        return None


def _plausible(cfg: AppConfig, field: str, v: float) -> bool:
    ic = cfg.intake
    lo, hi = (ic.es_plausible_min, ic.es_plausible_max) if field.startswith("es.") else (ic.plausible_min, ic.plausible_max)
    return lo <= v <= hi


def extract_text_levels(cfg: AppConfig, text: str, source: str, start_id: int = 1) -> tuple[list[Candidate], list[str]]:
    """Estrae livelli etichettati da testo libero (IT/EN). Deterministico, nessun LLM."""
    cands: list[Candidate] = []
    warns: list[str] = []
    section = "none"
    for line in text.splitlines():
        has_es, has_nq = bool(_HINT_ES.search(line)), bool(_HINT_NQ.search(line))
        stripped = _HINT_ES.sub("", _HINT_NQ.sub("", line))
        if (has_es or has_nq) and not re.search(r"\d{3,}", stripped):
            section = "es" if has_es and not has_nq else "nq" if has_nq and not has_es else section
            continue
        hint = "es" if has_es and not has_nq else "nq" if has_nq and not has_es else section
        taken: list[tuple[int, int]] = []
        for field, pat in _PATTERNS.items():
            for m in pat.finditer(line):
                span = m.span("num")
                if any(a <= span[0] < b for a, b in taken):
                    continue  # stesso numero gia' attribuito a un'etichetta piu' specifica
                v = parse_number(m.group("num"))
                if v is None:
                    continue
                taken.append(span)
                f = f"es.{field}" if hint == "es" else field
                if not _plausible(cfg, f, v):
                    warns.append(f"{source}: valore {v:g} per «{ALL_FIELDS[f]}» fuori dall'intervallo plausibile: scartato")
                    continue
                cands.append(Candidate(id=start_id + len(cands), field=f, value=v, source_file=source, method="regex",
                                       snippet=line.strip()[:160], confidence=85 if hint != "none" or field != "spot" else 60,
                                       instrument_hint=hint,
                                       note="" if hint != "none" else "strumento non specificato: assunto NQ — verificare"))
    return cands, warns


def _mark_conflicts(cands: list[Candidate]) -> None:
    by: dict[str, list[Candidate]] = {}
    for c in cands:
        by.setdefault(c.field, []).append(c)
    for group in by.values():
        vals = {round(c.value, 2) for c in group}
        for c in group:
            c.conflict = len(vals) > 1


# --------------------------------------------------------------------------- file handling
def inbox_dir(cfg: AppConfig, date: str) -> Path:
    return cfg.root / cfg.intake.inbox_dir / date


def safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "file"
    return base[:120]


def save_upload(cfg: AppConfig, date: str, name: str, data: bytes) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise IntakeError(f"data non valida: {date}")
    n = safe_name(name)
    if Path(n).suffix.lower() not in ALLOWED_EXT:
        raise IntakeError(f"estensione non ammessa: {Path(n).suffix or n} (ammessi: {', '.join(sorted(ALLOWED_EXT))})")
    if len(data) > cfg.intake.max_file_mb * 1024 * 1024:
        raise IntakeError(f"file troppo grande (> {cfg.intake.max_file_mb:g} MB): {n}")
    d = inbox_dir(cfg, date)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / n
    if dest.exists() and dest.read_bytes() != data:
        dest = d / f"{dest.stem}_{hashlib.sha256(data).hexdigest()[:6]}{dest.suffix}"
    dest.write_bytes(data)
    return dest


def save_message(cfg: AppConfig, date: str, text: str) -> Path:
    stamp = datetime.now().strftime("%H%M%S%f")[:9]
    return save_upload(cfg, date, f"message_{stamp}.txt", text.encode("utf-8"))


def make_runner(cfg: AppConfig, mock: bool) -> AgentRunner | None:
    """Runner per la lettura di immagini. None se non disponibile (es. nessuna chiave in modalita' reale)."""
    if not cfg.intake.vision_enabled:
        return None
    if mock:
        client: Any = MockLLMClient("ok")
    else:
        key = get_api_key(cfg.root)
        if not key:
            return None
        client = OmniRouteClient(cfg.omniroute, key)
    return AgentRunner(cfg, client, cfg.path("prompts_dir"), clock=SystemClock(),
                       sleep=(lambda s: None) if mock else time.sleep, rng=random.Random())


def _data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def extract_vision(cfg: AppConfig, runner: AgentRunner, images: list[tuple[str, str, bytes]], source: str,
                   start_id: int) -> tuple[list[Candidate], list[str]]:
    """images: (nome, mime, bytes). Ogni livello deve avere un verbatim coerente col valore."""
    payload = {"files": [n for n, _, _ in images], "allowed_fields": ALL_FIELDS,
               "plausible": {"nq": [cfg.intake.plausible_min, cfg.intake.plausible_max],
                             "es": [cfg.intake.es_plausible_min, cfg.intake.es_plausible_max]},
               "sha256": [hashlib.sha256(b).hexdigest()[:16] for _, _, b in images]}
    res = runner.run("vision_extractor", payload,
                     attachments=[{"data_uri": _data_uri(m, b)} for _, m, b in images])
    warns: list[str] = []
    if res.output is None:
        return [], [f"{source}: lettura immagine fallita — {'; '.join(res.meta.errors) or 'errore'}"]
    out: VisionOutput = res.output  # type: ignore[assignment]
    warns += [f"{source}: illeggibile — {u}" for u in out.unreadable]
    cands: list[Candidate] = []
    for lv in out.levels:
        field = lv.field
        if lv.instrument == "es" and not field.startswith("es.") and field in BASE_FIELDS:
            field = "es." + field
        if field not in ALL_FIELDS:
            warns.append(f"{source}: campo sconosciuto «{lv.field}» scartato")
            continue
        digits = re.sub(r"\D", "", lv.verbatim)
        if str(int(lv.value)) not in digits:
            warns.append(f"{source}: «{lv.verbatim}» non contiene il valore {lv.value:g}: scartato (possibile allucinazione)")
            continue
        if not _plausible(cfg, field, lv.value):
            warns.append(f"{source}: valore {lv.value:g} per «{ALL_FIELDS[field]}» fuori dall'intervallo plausibile: scartato")
            continue
        cands.append(Candidate(id=start_id + len(cands), field=field, value=lv.value, source_file=source, method="vision",
                               snippet=lv.verbatim[:160], confidence=min(lv.confidence, 90), instrument_hint=lv.instrument,
                               model=res.meta.actual_model,
                               note="letto da immagine: verificare sul file originale" +
                                    ("" if lv.instrument != "unknown" else " · strumento non specificato")))
    return cands, warns


def read_pdf(path: Path, max_pages: int) -> list[tuple[int, str, list[tuple[str, str, bytes]]]]:
    """[(pagina, testo, immagini[(nome, mime, bytes)])]. Solo estrazione: nessuna esecuzione di contenuti."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        raise IntakeError(f"PDF illeggibile ({type(exc).__name__}): {path.name}") from None
    if reader.is_encrypted:
        raise IntakeError(f"PDF cifrato non supportato: {path.name}")
    pages = []
    for i, page in enumerate(reader.pages[:max_pages], start=1):
        text = page.extract_text() or ""
        imgs: list[tuple[str, str, bytes]] = []
        if len(text.strip()) < 20:  # pagina probabilmente scansionata
            try:
                for im in page.images:
                    ext = Path(im.name).suffix.lower()
                    if ext in IMAGE_EXT:
                        imgs.append((f"{path.stem}_p{i}{ext}", IMAGE_EXT[ext], im.data))
            except Exception:  # noqa: BLE001 - immagini non estraibili: pagina segnalata sotto
                pass
        pages.append((i, text, imgs))
    return pages


# --------------------------------------------------------------------------- scan / review / apply
def _cand_path(cfg: AppConfig, date: str) -> Path:
    return inbox_dir(cfg, date) / "candidates.json"


def load_state(cfg: AppConfig, date: str) -> dict[str, Any]:
    p = _cand_path(cfg, date)
    if not p.exists():
        return {"date": date, "files": [], "candidates": [], "warnings": [], "ohlcv_files": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_state(cfg: AppConfig, date: str, st: dict[str, Any]) -> None:
    p = _cand_path(cfg, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scan(cfg: AppConfig, date: str, runner: AgentRunner | None = None) -> dict[str, Any]:
    """Analizza tutti i file dell'inbox del giorno. Lo stato dei candidati gia' rivisti viene preservato."""
    d = inbox_dir(cfg, date)
    old = load_state(cfg, date)
    prev = {(c["field"], round(c["value"], 2), c["source_file"]): c for c in old["candidates"]}
    cands: list[Candidate] = []
    warns: list[str] = []
    files: list[dict[str, Any]] = []
    ohlcv_files: list[str] = []

    def add(new: list[Candidate]) -> None:
        for c in new:
            c.id = len(cands) + 1
            cands.append(c)

    for f in sorted(d.glob("*")) if d.exists() else []:
        if f.name == "candidates.json" or not f.is_file():
            continue
        ext = f.suffix.lower()
        info = {"name": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()[:16], "kind": "ignored"}
        try:
            if ext in TEXT_EXT:
                text = f.read_text(encoding="utf-8", errors="replace")
                if _OHLCV_HEAD.search(text):
                    info["kind"] = "ohlcv_csv"
                    ohlcv_files.append(f.name)
                else:
                    info["kind"] = "text"
                    c, w = extract_text_levels(cfg, text, f.name)
                    add(c)
                    warns += w
            elif ext in IMAGE_EXT:
                info["kind"] = "image"
                if runner is None:
                    warns.append(f"{f.name}: lettura immagini non disponibile (nessun modello vision/chiave): "
                                 f"inserisci i livelli come messaggio di testo")
                else:
                    c, w = extract_vision(cfg, runner, [(f.name, IMAGE_EXT[ext], f.read_bytes())], f.name, 1)
                    add(c)
                    warns += w
            elif ext == ".pdf":
                info["kind"] = "pdf"
                for page, text, imgs in read_pdf(f, cfg.intake.max_pdf_pages):
                    src = f"{f.name} p.{page}"
                    c, w = extract_text_levels(cfg, text, src)
                    add(c)
                    warns += w
                    if imgs:
                        if runner is None:
                            warns.append(f"{src}: pagina scansionata ma lettura immagini non disponibile")
                        else:
                            c, w = extract_vision(cfg, runner, imgs, src, 1)
                            add(c)
                            warns += w
                    elif len(text.strip()) < 20:
                        warns.append(f"{src}: nessun testo né immagine estraibile: esporta la pagina come immagine")
        except IntakeError as exc:
            warns.append(str(exc))
        files.append(info)
    _mark_conflicts(cands)
    for c in cands:  # preserva le revisioni precedenti
        o = prev.get((c.field, round(c.value, 2), c.source_file))
        if o:
            c.status, c.edited_value = o["status"], o.get("edited_value")
    state = {"date": date, "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files,
             "candidates": [c.model_dump() for c in cands], "warnings": [redact(w) for w in warns],
             "ohlcv_files": ohlcv_files}
    _save_state(cfg, date, state)
    return state


def review(cfg: AppConfig, date: str, ids: list[int] | None, status: str, value: float | None = None,
           all_pending: bool = False) -> int:
    if status not in ("accepted", "rejected", "pending"):
        raise IntakeError(f"stato non valido: {status}")
    st = load_state(cfg, date)
    n = 0
    for c in st["candidates"]:
        if c["status"] == "applied":
            continue
        if (ids and c["id"] in ids) or (all_pending and c["status"] == "pending"):
            if value is not None:
                if not _plausible(cfg, c["field"], value):
                    raise IntakeError(f"valore {value:g} fuori dall'intervallo plausibile per {c['field']}")
                c["edited_value"] = value
            c["status"] = status
            n += 1
    _save_state(cfg, date, st)
    return n


def _set_path(doc: dict[str, Any], field: str, value: float) -> float | None:
    cur = doc
    parts = field.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    prev = cur.get(parts[-1])
    cur[parts[-1]] = value
    return prev if isinstance(prev, (int, float)) and prev else None


def apply(cfg: AppConfig, date: str, levels_path: Path | None = None) -> list[str]:
    """Scrive in levels.json SOLO i candidati accettati dall'umano, con provenienza. Conflitti = errore."""
    st = load_state(cfg, date)
    acc = [c for c in st["candidates"] if c["status"] == "accepted"]
    if not acc:
        raise IntakeError("nessun candidato accettato: niente da applicare")
    final: dict[str, dict[str, Any]] = {}
    for c in acc:
        v = c["edited_value"] if c["edited_value"] is not None else c["value"]
        if c["field"] in final and abs(final[c["field"]]["v"] - v) > 0.01:
            raise IntakeError(f"conflitto su «{ALL_FIELDS[c['field']]}»: {final[c['field']]['v']:g} vs {v:g}. "
                              f"Rifiuta o correggi uno dei due candidati.")
        final[c["field"]] = {"v": v, "c": c}
    path = levels_path or cfg.path("data_dir") / "levels.json"
    msgs: list[str] = []
    doc: dict[str, Any] | None = None
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
        if doc.get("date") != date:  # levels di un altro giorno: archivia e riparte pulito (niente valori vecchi)
            arch = path.parent / "archive"
            arch.mkdir(exist_ok=True)
            shutil.copyfile(path, arch / f"levels_{doc.get('date', 'unknown')}.json")
            msgs.append(f"levels.json era datato {doc.get('date')}: archiviato in data/archive/, creato file nuovo per {date}")
            doc = None
    if doc is None:
        doc = {"date": date, "instrument": "NQ", "spot": 0, "options": {"gamma_flip": 0, "call_wall": 0, "put_wall": 0, "notes": ""},
               "levels": {}}
    prov = doc.setdefault("provenance", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for field, d in final.items():
        c = d["c"]
        prevv = _set_path(doc, field, d["v"])
        prov[field] = {"source": "intake", "method": c["method"], "file": c["source_file"], "detail": c["snippet"],
                       "confirmed_by": "human", "confirmed_at": now, "previous_value": prevv,
                       "edited_by_human": c["edited_value"] is not None, "model": c.get("model")}
        msgs.append(f"{ALL_FIELDS[field]} = {d['v']:g}  (da {c['source_file']}, {c['method']})")
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for c in st["candidates"]:
        if c["status"] == "accepted":
            c["status"] = "applied"
    _save_state(cfg, date, st)
    return msgs


# --------------------------------------------------------------------------- OHLCV
def merge_ohlcv(cfg: AppConfig, new_csv: Path, target: Path) -> str:
    """Unisce nuove barre a un ohlcv esistente. Barre identiche ok; barre diverse sullo stesso timestamp = errore."""
    new, issues = load_ohlcv(new_csv, cfg.features)
    if new is None:
        raise IntakeError("CSV da importare non valido: " + "; ".join(i.message for i in issues))
    if target.exists():
        old, oissues = load_ohlcv(target, cfg.features)
        if old is None:
            raise IntakeError("ohlcv esistente non valido: " + "; ".join(i.message for i in oissues))
        both = old.merge(new, on="timestamp", suffixes=("_old", "_new"))
        for c in ("open", "high", "low", "close", "volume"):
            if (both[f"{c}_old"] != both[f"{c}_new"]).any():
                raise IntakeError(f"barre in conflitto sullo stesso timestamp ({c}): importazione annullata, nessun file modificato")
        merged = pd.concat([old, new[~new["timestamp"].isin(old["timestamp"])]]).sort_values("timestamp").reset_index(drop=True)
        shutil.copyfile(target, target.with_suffix(target.suffix + ".bak"))
        added = len(merged) - len(old)
    else:
        merged, added = new, len(new)
    tmp = target.with_suffix(".tmp.csv")
    merged.to_csv(tmp, index=False)
    chk, cissues = load_ohlcv(tmp, cfg.features)
    if chk is None:
        tmp.unlink(missing_ok=True)
        raise IntakeError("il file unito non supera la validazione: " + "; ".join(i.message for i in cissues))
    tmp.replace(target)
    return f"{target.name}: +{added} barre ({len(merged)} totali)"
