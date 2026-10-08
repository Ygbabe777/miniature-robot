"""Helper condivisi dalle due UI Streamlit (nessuna logica di analisi qui)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from .config import AppConfig, load_config
from .journal import ensure_journal, read_journal, record_human_decision
from .mock_llm import SCENARIOS
from .pipeline import ConfigurationError, PipelineOptions, run_pipeline
from .report_generator import render_panic_proof
from .schemas import HUMAN_DECISIONS

DECISION_LABEL = {"TAKE": "TAKE SETUP", "SKIP": "SKIP", "MODIFY": "MODIFY", "WATCH": "WATCH", "NO_TRADE": "NO TRADE"}


@st.cache_resource
def get_config() -> AppConfig:
    root = os.environ.get("OFO_ROOT")  # usato dai test per un progetto temporaneo
    return load_config(root=root) if root else load_config()


def list_report_dates(cfg: AppConfig) -> list[str]:
    d = cfg.path("reports_dir")
    return sorted((p.stem for p in d.glob("*.json")), reverse=True) if d.exists() else []


def load_report(cfg: AppConfig, date: str) -> dict[str, Any] | None:
    p = cfg.path("reports_dir") / f"{date}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def run_controls(cfg: AppConfig, key: str = "run") -> str | None:
    """Pannello di avvio analisi in sidebar. Ritorna la data dell'ultimo run eseguito, se presente."""
    st.sidebar.subheader("Nuova analisi")
    date = st.sidebar.text_input("Data (YYYY-MM-DD)", value="2026-10-08", key=f"{key}_date")
    mock = st.sidebar.checkbox("Modalità MOCK (LLM simulato)", value=True, key=f"{key}_mock",
                               help="Senza chiave API/OmniRoute: risposte deterministiche, marcate MOCK nel report.")
    scenario = st.sidebar.selectbox("Scenario mock", sorted(SCENARIOS), index=sorted(SCENARIOS).index("ok"),
                                    key=f"{key}_scn", disabled=not mock)
    if st.sidebar.button("▶ Esegui analisi", key=f"{key}_go", use_container_width=True):
        try:
            with st.spinner("Il Consiglio sta lavorando…"):
                res = run_pipeline(PipelineOptions(date=date, mock=mock, mock_scenario=scenario, cfg=cfg))
            st.sidebar.success(f"Completata: {res.status.value}")
            return date
        except ConfigurationError as exc:
            st.sidebar.error(str(exc))
        except Exception as exc:  # noqa: BLE001 - mostrata all'utente, mai nascosta
            st.sidebar.error(f"Errore: {type(exc).__name__}: {exc}")
    return None


def human_decision_panel(cfg: AppConfig, report: dict[str, Any], key: str = "hd") -> None:
    """Verdetto AI e decisione umana, registrate separatamente."""
    date, dec = report["run"]["date"], report["decision"]
    st.subheader("AI COUNCIL VERDICT")
    colors = {"APPROVED_SETUP": "success", "APPROVED_WITH_CAUTION": "warning", "NO_TRADE": "info",
              "REVIEW_REQUIRED": "warning", "DATA_ERROR": "error"}
    getattr(st, colors.get(dec["status"], "info"))(f"**{dec['status']}** — " + " ".join(dec["reasons"]))
    st.caption("ANALISI AI — NESSUN ORDINE AUTOMATICO. La decisione sottostante è esclusivamente umana.")
    st.subheader("HUMAN DECISION")
    jp = cfg.path("journal")
    ensure_journal(jp)
    row = next((r for r in read_journal(jp) if r["date"] == date), None)
    st.write(f"Stato registrato: **{row['human_decision'] if row else 'PENDING'}**")
    notes = st.text_input("Note (opzionale)", key=f"{key}_notes")
    cols = st.columns(5)
    for col, d in zip(cols, ("TAKE", "SKIP", "MODIFY", "WATCH", "NO_TRADE")):
        if col.button(DECISION_LABEL[d], key=f"{key}_{d}", use_container_width=True):
            try:
                record_human_decision(jp, date, d, notes=notes)
                st.success(f"Decisione umana registrata: {DECISION_LABEL[d]}")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    assert set(HUMAN_DECISIONS) >= set(DECISION_LABEL)


def panic_proof_block(report: dict[str, Any]) -> None:
    st.code(render_panic_proof(report["panic_proof"]), language="text")


def intake_panel(cfg: AppConfig, key: str = "in") -> None:
    """Carica messaggi/screenshot/PDF, rivedi i candidati ed applica SOLO quelli accettati. Include GexBot."""
    import pandas as pd

    from . import intake as it
    from .gexbot import GexBotError, refresh_levels

    st.markdown("Carica screenshot, PDF o incolla messaggi con i livelli. **Nulla viene usato finché non lo accetti**: "
                "ogni valore mostra il testo da cui è stato letto. L'OHLCV (barre) va fornito come CSV.")
    date = st.text_input("Data analisi (YYYY-MM-DD)", value="2026-10-08", key=f"{key}_date")
    mock = st.checkbox("MOCK (lettura immagini e GexBot simulati)", value=False, key=f"{key}_mock")
    files = st.file_uploader("Screenshot / PDF / testo / CSV", accept_multiple_files=True, key=f"{key}_up",
                             type=[e.lstrip(".") for e in sorted(it.ALLOWED_EXT)])
    msg = st.text_area("Oppure incolla un messaggio", key=f"{key}_msg", height=100,
                       placeholder="NQ gamma flip 28900 call wall 29250 put wall 28800\nES gamma flip 6460 ...")
    c1, c2 = st.columns(2)
    if c1.button("Salva e analizza", key=f"{key}_scan", use_container_width=True):
        try:
            for f in files or []:
                it.save_upload(cfg, date, f.name, f.getvalue())
            if msg.strip():
                it.save_message(cfg, date, msg)
            state = it.scan(cfg, date, it.make_runner(cfg, mock))
            st.success(f"{len(state['candidates'])} candidati trovati")
        except it.IntakeError as exc:
            st.error(str(exc))
    if c2.button("Aggiorna livelli da GexBot (NQ+ES)", key=f"{key}_gex", use_container_width=True):
        try:
            msgs, warns = refresh_levels(cfg, date, cfg.path("data_dir") / "levels.json", mock=mock)
            for m in msgs:
                st.success(m)
            for w in warns:
                st.warning(w)
        except GexBotError as exc:
            st.error(str(exc))
    state = it.load_state(cfg, date)
    for w in state.get("warnings", []):
        st.warning(w)
    if state["candidates"]:
        rows = [{"id": c["id"], "accetta": c["status"] in ("accepted", "applied"), "campo": it.ALL_FIELDS[c["field"]],
                 "valore": c["edited_value"] if c["edited_value"] is not None else c["value"], "metodo": c["method"],
                 "fonte": c["source_file"], "testo letto": c["snippet"], "stato": c["status"],
                 "conflitto": "⚠" if c["conflict"] else "", "nota": c["note"]} for c in state["candidates"]]
        edited = st.data_editor(pd.DataFrame(rows), key=f"{key}_ed", hide_index=True, use_container_width=True,
                                disabled=["id", "campo", "metodo", "fonte", "testo letto", "stato", "conflitto", "nota"])
        if st.button("Applica i candidati accettati a levels.json", key=f"{key}_apply", type="primary"):
            try:
                for (_, row), c in zip(edited.iterrows(), state["candidates"]):
                    if c["status"] == "applied":
                        continue
                    new = float(row["valore"])
                    val = new if abs(new - c["value"]) > 1e-9 else None
                    it.review(cfg, date, [c["id"]], "accepted" if row["accetta"] else "rejected", val)
                for m in it.apply(cfg, date):
                    st.success(m)
            except it.IntakeError as exc:
                st.error(str(exc))
    if state.get("ohlcv_files"):
        st.info("CSV OHLCV rilevati: " + ", ".join(state["ohlcv_files"]))
        inst = st.radio("Strumento", ["nq", "es"], horizontal=True, key=f"{key}_inst")
        pick = st.selectbox("File", state["ohlcv_files"], key=f"{key}_ohlcv")
        if st.button("Unisci all'OHLCV (con validazione)", key=f"{key}_merge"):
            try:
                target = cfg.path("data_dir") / ("ohlcv.csv" if inst == "nq" else "ohlcv_es.csv")
                st.success(it.merge_ohlcv(cfg, it.inbox_dir(cfg, date) / pick, target))
            except it.IntakeError as exc:
                st.error(str(exc))
