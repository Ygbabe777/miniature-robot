"""Valutazione storica del Consiglio e registrazione degli esiti nel journal.

    python evaluate.py
    python evaluate.py --set-result 2026-10-08 WIN --pnl-r 1.8
    python evaluate.py --human 2026-10-08 TAKE --notes "ingresso su conferma"
"""
import argparse
import json
import sys

from src.config import load_config
from src.evaluator import evaluate
from src.journal import RESULTS, record_human_decision, record_result


def main() -> int:
    ap = argparse.ArgumentParser(description="Valutazione storica OFO Council")
    ap.add_argument("--set-result", nargs=2, metavar=("DATA", "RISULTATO"), help=f"Risultato: {', '.join(r for r in RESULTS if r)}")
    ap.add_argument("--pnl-r", type=float, default=None)
    ap.add_argument("--human", nargs=2, metavar=("DATA", "DECISIONE"), help="TAKE|SKIP|MODIFY|WATCH|NO_TRADE")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    cfg = load_config()
    jp = cfg.path("journal")
    try:
        if a.human:
            record_human_decision(jp, a.human[0], a.human[1], notes=a.notes)
            print(f"Decisione umana registrata: {a.human[0]} -> {a.human[1]}")
        if a.set_result:
            record_result(jp, a.set_result[0], a.set_result[1], a.pnl_r, a.notes)
            print(f"Risultato registrato: {a.set_result[0]} -> {a.set_result[1]} ({a.pnl_r}R)")
    except ValueError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(evaluate(cfg), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
