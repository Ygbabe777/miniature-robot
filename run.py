"""CLI di OFO Council.  Solo ANALISI: nessun ordine viene mai inviato a un broker.

    python run.py --date 2026-10-08 [--mock] [--mock-scenario NOME] [--verbose] [--no-ui]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import load_config
from src.logging_utils import setup_logging
from src.mock_llm import SCENARIOS
from src.pipeline import ConfigurationError, PipelineOptions, run_pipeline
from src.security import redact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OFO Council — analisi pre-market NQ (nessun ordine automatico)")
    ap.add_argument("--date", required=True, help="Data analisi YYYY-MM-DD")
    ap.add_argument("--mock", action="store_true", help="LLM simulato deterministico (nessuna chiamata di rete)")
    ap.add_argument("--mock-scenario", default="ok", choices=sorted(SCENARIOS), help="Scenario di guasto/comportamento mock")
    ap.add_argument("--gexbot", action="store_true", help="Aggiorna prima i livelli opzioni NQ/ES da GexBot (richiede GEXBOT_API_KEY; con --mock usa dati sintetici)")
    ap.add_argument("--verbose", action="store_true", help="Log strutturati su stderr")
    ap.add_argument("--no-ui", action="store_true", help="Non mostrare il suggerimento per avviare la UI")
    ap.add_argument("--levels", type=Path, help="Percorso levels.json alternativo")
    ap.add_argument("--ohlcv", type=Path, help="Percorso ohlcv.csv alternativo")
    ap.add_argument("--ohlcv-es", type=Path, help="Percorso ohlcv ES alternativo (default data/ohlcv_es.csv)")
    ap.add_argument("--config", type=Path, help="Percorso config.yaml alternativo")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    setup_logging(a.verbose, cfg.path("logs_dir"))

    def progress(step: int, total: int, label: str, status: str, ms: int) -> None:
        print(f"[{step}/{total}] {label:<28} {status:<8} {ms} ms", flush=True)

    print("OFO COUNCIL — ANALISI AI — NESSUN ORDINE AUTOMATICO")
    print(f"Modalità: {'MOCK (' + a.mock_scenario + ')' if a.mock else 'REALE (OmniRoute)'} · data {a.date}\n")
    if a.gexbot:
        from src.gexbot import GexBotError, refresh_levels
        try:
            msgs, warns = refresh_levels(cfg, a.date, a.levels or cfg.path("data_dir") / "levels.json", mock=a.mock)
        except GexBotError as exc:
            print(f"ERRORE GexBot: {redact(str(exc))}", file=sys.stderr)
            return 2
        for m in msgs:
            print(m)
        for w in warns:
            print(f"AVVISO: {w}")
        print()
    try:
        res = run_pipeline(PipelineOptions(date=a.date, mock=a.mock, mock_scenario=a.mock_scenario, cfg=cfg,
                                           levels_path=a.levels, ohlcv_path=a.ohlcv, es_ohlcv_path=a.ohlcv_es, progress=progress))
    except ConfigurationError as exc:
        print(f"\nERRORE DI CONFIGURAZIONE: {redact(str(exc))}", file=sys.stderr)
        return 2
    print(f"\nRUN COMPLETE — {res.status.value}")
    print(f"Run ID: {res.run_id}")
    for name, p in res.paths.items():
        print(f"  {name:<12} {p}")
    print("Decisione umana: PENDING (da registrare nel journal; il sistema non esegue ordini)")
    if not a.no_ui:
        print("UI: streamlit run app_pixel.py   |   streamlit run app.py")
    return 3 if res.status.value == "DATA_ERROR" else 0


if __name__ == "__main__":
    sys.exit(main())
