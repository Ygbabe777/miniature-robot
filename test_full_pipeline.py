"""Test di integrazione end-to-end.

    python test_full_pipeline.py --mock     # nessuna rete: LLM simulato deterministico (pytest)
    python test_full_pipeline.py            # modalita' reale: richiede OMNIROUTE_API_KEY in .env + OmniRoute attivo

In modalita' reale NON viene asserito nulla sul contenuto di mercato (non e' deterministico): si
verifica solo che la pipeline giri, produca artefatti validi e rispetti le regole di sicurezza.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_mock() -> int:
    import pytest
    return int(pytest.main([str(ROOT / "tests"), "-q", "-p", "no:cacheprovider"]))


def run_real(date: str) -> int:
    from src.config import load_config
    from src.pipeline import ConfigurationError, PipelineOptions, run_pipeline
    from src.security import contains_secret, get_api_key
    cfg = load_config()
    if not get_api_key(cfg.root):
        print("OMNIROUTE_API_KEY non trovata in .env: impossibile eseguire il test reale. Usare --mock.", file=sys.stderr)
        return 2
    try:
        res = run_pipeline(PipelineOptions(date=date, mock=False, cfg=cfg,
                                           progress=lambda s, t, label, st, ms: print(f"[{s}/{t}] {label} {st} {ms}ms")))
    except ConfigurationError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 2
    ok = True
    for name, p in res.paths.items():
        text = Path(p).read_text(encoding="utf-8")
        if name.endswith("json") or name == "transcript":
            json.loads(text)
        if contains_secret(text):
            print(f"FALLITO: possibile segreto in {p}", file=sys.stderr)
            ok = False
    print(f"Stato finale: {res.status.value} · artefatti: {', '.join(str(p) for p in res.paths.values())}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="usa LLM simulato (pytest)")
    ap.add_argument("--date", default="2026-10-08")
    a = ap.parse_args()
    sys.exit(run_mock() if a.mock else run_real(a.date))
