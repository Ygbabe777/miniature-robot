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
