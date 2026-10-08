"""Aggiorna i livelli opzioni (gamma flip, call/put wall) di NQ ed ES da GexBot in data/levels.json.

    python fetch_gex.py --date 2026-10-08            # richiede GEXBOT_API_KEY in .env
    python fetch_gex.py --date 2026-10-08 --mock     # livelli SINTETICI (solo prova)
    python fetch_gex.py --date 2026-10-08 --only nq
Viene creato un backup levels.json.bak; origine e conversione NDX->NQ / SPX->ES sono registrate in `provenance`.
"""
import argparse
import sys
from pathlib import Path

from src.config import load_config
from src.gexbot import GexBotError, refresh_levels
from src.security import redact


def main() -> int:
    ap = argparse.ArgumentParser(description="Livelli opzioni da GexBot")
    ap.add_argument("--date", required=True)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--only", choices=["nq", "es"])
    ap.add_argument("--levels", type=Path)
    a = ap.parse_args()
    cfg = load_config()
    try:
        msgs, warns = refresh_levels(cfg, a.date, a.levels or cfg.path("data_dir") / "levels.json", mock=a.mock,
                                     instruments=(a.only,) if a.only else ("nq", "es"))
    except GexBotError as exc:
        print(f"ERRORE: {redact(str(exc))}", file=sys.stderr)
        return 2
    print("\n".join(msgs) or "Nessun livello aggiornato.")
    for w in warns:
        print(f"AVVISO: {w}")
    return 0 if msgs else 1


if __name__ == "__main__":
    sys.exit(main())
