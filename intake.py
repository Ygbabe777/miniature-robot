"""Intake di livelli da messaggi, screenshot e PDF — sempre con conferma umana prima dell'uso.

    python intake.py add   --date 2026-10-08 shot1.png report.pdf messaggio.txt
    python intake.py text  --date 2026-10-08 "NQ gamma flip 28900 call wall 29250 put wall 28800"
    python intake.py scan  --date 2026-10-08 [--mock]
    python intake.py list  --date 2026-10-08
    python intake.py accept --date 2026-10-08 --id 1 --id 2 [--value 28905]   |   --all
    python intake.py reject --date 2026-10-08 --id 3
    python intake.py apply --date 2026-10-08        # scrive SOLO i candidati accettati in data/levels.json
    python intake.py import-ohlcv --instrument nq|es barre.csv
"""
import argparse
import sys
from pathlib import Path

from src import intake
from src.config import load_config
from src.security import redact


def show(st: dict) -> None:
    for w in st.get("warnings", []):
        print(f"AVVISO: {w}")
    if not st["candidates"]:
        print("Nessun candidato.")
    for c in st["candidates"]:
        v = c["edited_value"] if c["edited_value"] is not None else c["value"]
        flag = " ⚠CONFLITTO" if c["conflict"] else ""
        print(f"#{c['id']:<3} [{c['status']:<8}] {intake.ALL_FIELDS[c['field']]:<28} {v:>12g}  "
              f"{c['method']:<6} {c['source_file']}  «{c['snippet'][:50]}»{flag}")
    if st.get("ohlcv_files"):
        print("CSV OHLCV rilevati (usa import-ohlcv): " + ", ".join(st["ohlcv_files"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Intake livelli (messaggi/screenshot/PDF) con conferma umana")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "text", "scan", "list", "accept", "reject", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--date", required=True)
        if name == "add":
            p.add_argument("files", nargs="+", type=Path)
        if name == "text":
            p.add_argument("message")
        if name == "scan":
            p.add_argument("--mock", action="store_true", help="vision simulato (non legge le immagini)")
        if name in ("accept", "reject"):
            p.add_argument("--id", type=int, action="append", default=[])
            p.add_argument("--all", action="store_true", help="tutti i candidati in attesa")
            p.add_argument("--value", type=float, help="correzione umana del valore")
    p = sub.add_parser("import-ohlcv")
    p.add_argument("--instrument", choices=["nq", "es"], required=True)
    p.add_argument("file", type=Path)
    a = ap.parse_args()
    cfg = load_config()
    try:
        if a.cmd == "add":
            for f in a.files:
                print("salvato:", intake.save_upload(cfg, a.date, f.name, f.read_bytes()))
        elif a.cmd == "text":
            print("salvato:", intake.save_message(cfg, a.date, a.message))
        elif a.cmd == "scan":
            show(intake.scan(cfg, a.date, intake.make_runner(cfg, a.mock)))
        elif a.cmd == "list":
            show(intake.load_state(cfg, a.date))
        elif a.cmd in ("accept", "reject"):
            if not a.id and not a.all:
                raise intake.IntakeError("specifica --id N (ripetibile) oppure --all")
            n = intake.review(cfg, a.date, a.id, "accepted" if a.cmd == "accept" else "rejected", a.value, a.all)
            print(f"{n} candidati aggiornati")
        elif a.cmd == "apply":
            for m in intake.apply(cfg, a.date):
                print("applicato:", m)
        elif a.cmd == "import-ohlcv":
            target = cfg.path("data_dir") / ("ohlcv.csv" if a.instrument == "nq" else "ohlcv_es.csv")
            print(intake.merge_ohlcv(cfg, a.file, target))
    except intake.IntakeError as exc:
        print(f"ERRORE: {redact(str(exc))}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
