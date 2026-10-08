"""OFO Council — Sala operativa pixel-art. Visualizza eventi REALI del Consiglio (solo analisi)."""
import streamlit as st
import streamlit.components.v1 as components

from src.room_view import build_room_html
from src.ui_common import (get_config, human_decision_panel, list_report_dates, load_report,
                           panic_proof_block, run_controls)

st.set_page_config(page_title="OFO Council — Trading Room", page_icon="🏛️", layout="wide")
cfg = get_config()
st.markdown("<style>.block-container{padding-top:1rem;max-width:1320px}</style>", unsafe_allow_html=True)

ran = run_controls(cfg, "px")
dates = list_report_dates(cfg)
if not dates:
    st.title("🏛️ OFO COUNCIL")
    st.info("Nessun report disponibile. Dalla sidebar esegui un'analisi (modalità MOCK per provare) oppure lancia "
            "`python run.py --date YYYY-MM-DD`.")
    st.stop()
default = dates.index(ran) if ran in dates else 0
date = st.sidebar.selectbox("Report", dates, index=default, key="px_report")
report = load_report(cfg, date)
if report is None:
    st.error("Report non leggibile.")
    st.stop()
autoplay = st.sidebar.checkbox("Autoplay", value=True, key="px_autoplay")
st.sidebar.caption("La sala ricostruisce gli eventi persistiti nel transcript: nessuna simulazione separata. "
                   "Usa PAUSA / NEXT EVENT per il debug passo-passo e la velocità 0.5x–4x.")

components.html(build_room_html(report, cfg.ui, autoplay=autoplay), height=1480, scrolling=True)

c1, c2 = st.columns([1, 1])
with c1:
    human_decision_panel(cfg, report, "px")
with c2:
    st.subheader("PANIC-PROOF")
    panic_proof_block(report)
