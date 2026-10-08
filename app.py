"""OFO Council — interfaccia Streamlit standard (solo analisi, nessun ordine automatico)."""
import pandas as pd
import streamlit as st

from src.journal import ensure_journal, read_journal
from src.report_generator import DISCLAIMER
from src.scoreboard import build_scoreboard
from src.ui_common import (get_config, human_decision_panel, intake_panel, list_report_dates, load_report,
                           panic_proof_block, run_controls)

st.set_page_config(page_title="OFO Council", page_icon="📊", layout="wide")
cfg = get_config()
st.title("OFO COUNCIL — NQ Pre-Market")
st.caption(DISCLAIMER)

ran = run_controls(cfg, "std")
dates = list_report_dates(cfg)
tabs = st.tabs(["Dashboard", "Market Data", "Agent Council", "Scenarios", "Risk", "Judge", "PANIC-PROOF",
                "Historical Reports", "Journal", "Scoreboard", "Dati & Intake"])
report = None
if dates:
    date = st.sidebar.selectbox("Report", dates, index=dates.index(ran) if ran in dates else 0, key="std_report")
    report = load_report(cfg, date)

AG = {"price_action": "💙 Price Action", "options_flow": "💜 Options Flow", "strategist": "💚 Strategist",
      "risk_manager": "❤️ Risk Manager", "judge": "⚪ Judge"}

with tabs[0]:
    if not report:
        st.info("Nessun report: esegui un'analisi dalla sidebar (modalità MOCK per provare).")
    else:
        run, dec = report["run"], report["decision"]
        if run["mode"] == "MOCK":
            st.warning(f"MODALITÀ MOCK ({run.get('mock_scenario')}): risposte simulate, non di un LLM.")
        if any("SINTETICI" in i["message"] for i in report["data_quality"]["issues"]):
            st.warning("DATI SINTETICI DI ESEMPIO: non sono dati di mercato reali.")
        c = st.columns(4)
        c[0].metric("Data", run["date"])
        c[1].metric("Spot", (report["market_snapshot"] or {}).get("spot", "n/d"))
        c[2].metric("Qualità dati", report["data_quality"]["status"])
        c[3].metric("Verdetto", dec["status"])
        human_decision_panel(cfg, report, "std")
        with st.expander("Metriche per fase (osservabilità)"):
            st.dataframe(pd.DataFrame(report["stages"]).T)

with tabs[1]:
    if report and report["market_snapshot"]:
        snap = report["market_snapshot"]
        st.json({k: snap[k] for k in ("spot", "last_close", "prev_session", "overnight", "composite_3s", "atr",
                                      "distances", "options", "regime")})
        st.subheader("Registro evidenze")
        st.dataframe(pd.DataFrame(report["evidence"]).astype({"value": str}))
        st.subheader("Avvisi sui dati")
        for i in report["data_quality"]["issues"]:
            st.write(f"[{i['severity']}] {i['message']}")
        for w in report["data_quality"]["warnings"]:
            st.write(f"• {w}")
    else:
        st.info("Dati di mercato non disponibili.")

with tabs[2]:
    if report:
        for key, label in AG.items():
            a = report["agents"].get(key)
            if not a:
                continue
            with st.expander(f"{label} — {a['status']}", expanded=False):
                o, m = a.get("output"), a.get("meta")
                if m:
                    st.caption(f"Modello richiesto `{m['requested_model']}` → effettivo `{m['actual_model']}` · "
                               f"fallback {m['fallback_used']} · retry {m['retry_count']} · {m['duration_ms']} ms")
                if o:
                    st.write(f"Confidenza {o['confidence']}/100" + (f" · Bias **{o['bias']}**" if "bias" in o else ""))
                    for f in o.get("facts", []):
                        st.write(f"**FATTO** {f['text']} `{', '.join(f['evidence_ids'])}`")
                    for f in o.get("interpretations", []):
                        st.write(f"*INTERPRETAZIONE* {f['text']} `{', '.join(f['evidence_ids'])}`")
                    for msg in o.get("messages", []):
                        st.write(f"💬 → {msg['addressed_to']} [{msg['message_type']}] {msg['message']}")
                    for w in o.get("warnings", []):
                        st.warning(w)
                else:
                    st.error(a.get("reason") or "; ".join((m or {}).get("errors", [])) or "Agente non disponibile")

with tabs[3]:
    o = report and report["agents"].get("strategist", {}).get("output")
    if o:
        if o["no_trade"]:
            st.info(f"NO TRADE — {o['no_trade_reason']}")
        for s in o["scenarios"]:
            verdict = report["decision"]["scenario_verdicts"].get(s["id"], "n/d")
            st.markdown(f"### {s['id']} — {s['direction']} · {s['setup_type']} · **{verdict}**")
            st.write(f"Ingresso {s['entry_zone']} · Invalidazione {s['invalidation']} · T1 {s['target_1']} · "
                     f"T2 {s['target_2']} · R:R {s['expected_rr']} · Confidenza {s['confidence']}")
            st.write(f"**SE → ALLORA:** {s['thesis']}")
            st.write(f"**Fallimento:** {s['failure_condition']}")
            st.write("**Conferme:** " + "; ".join(s["confirmation_required"]))
    else:
        st.info("Nessuno scenario disponibile.")

with tabs[4]:
    if report and report["risk"]["assessments"]:
        st.dataframe(pd.DataFrame(report["risk"]["assessments"]))
        st.caption("Verdetto finale = il più severo tra motore di rischio deterministico e Risk Manager.")
        with st.expander("Pre-check deterministico"):
            st.json(report["risk"]["precheck"])
    else:
        st.info("Nessuna valutazione di rischio (nessuno scenario o agente non disponibile).")

with tabs[5]:
    if report:
        j = report["judge"]
        st.metric("Punteggio", f"{j['score']}/100" if j["score"] is not None else "n/d")
        for i in j["issues"]:
            st.write(f"[{i['severity']}] **{i['type']}** — {i['description']}")
        if not j["issues"] and j["score"] is not None:
            st.success("Nessun problema di ancoraggio rilevato.")

with tabs[6]:
    if report:
        panic_proof_block(report)

with tabs[7]:
    for d in dates:
        r = load_report(cfg, d)
        if r:
            st.write(f"**{d}** — {r['decision']['status']} · qualità {r['data_quality']['status']} · "
                     f"{r['run']['mode']} · `{r['run']['run_id']}`")
    if report:
        md = cfg.path("reports_dir") / f"{report['run']['date']}.md"
        if md.exists():
            with st.expander("Report Markdown completo"):
                st.markdown(md.read_text(encoding="utf-8"))

with tabs[8]:
    jp = cfg.path("journal")
    ensure_journal(jp)
    st.dataframe(pd.DataFrame(read_journal(jp)))

with tabs[9]:
    st.dataframe(pd.DataFrame(build_scoreboard(cfg)["agents"]))
    st.caption("Gli agenti non sono giudicati solo dal win rate: vedi README.")

with tabs[10]:
    intake_panel(cfg, "std_in")
