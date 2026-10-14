"""Sensibilità delle soglie del Judge, SOLO su dati di sviluppo e su potenza analitica. Mai sul blocco bloccato.

Uso:  python scripts/judge_sensitivity.py [--work work] [--block 90]

Per ogni studio salvato legge la serie `dev_best` (sviluppo) e la spezza in blocchi cronologici non sovrapposti lunghi quanto un blocco OOS tipico.
Per ogni blocco calcola il Deflated Sharpe e mostra quanti blocchi supererebbero soglie/basi diverse. Serve a capire quanto una soglia dipende
dalla lunghezza del campione, NON a sceglierla per approvare di più: la `series["locked"]` non viene nemmeno aperta."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ofo.judges import power_needed                                   # noqa: E402
from ofo.stats import deflated_sharpe                                 # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--work", default="work"); ap.add_argument("--block", type=int, default=90)
    ap.add_argument("--trials", type=int, nargs="+", default=[1, 10, 100, 1000]); ap.add_argument("--var-sr", type=float, default=0.001)
    a = ap.parse_args()
    files = sorted((Path(a.work) / "reports").glob("*_series.json"))
    blocks = []
    for f in files:
        ser = json.loads(f.read_text()).get("dev_best") or []              # development only: the locked series is never read here
        x = np.array([v for _, v in ser], float)
        for i in range(0, len(x) - a.block + 1, a.block):
            blocks.append(x[i:i + a.block])
    print(f"{len(files)} studi, {len(blocks)} blocchi di sviluppo da {a.block} giorni (nessun dato bloccato letto)\n")
    print("Sharpe annuo richiesto in media per DSR>=p su", a.block, "giorni:")
    for n in a.trials:
        print(f"  N={n:<5} p=0.95 → {power_needed(a.block, n, a.var_sr, .95):.2f}   p=0.90 → {power_needed(a.block, n, a.var_sr, .90):.2f}   p=0.70 → {power_needed(a.block, n, a.var_sr, .70):.2f}")
    if not blocks:
        print("\nNessun blocco di sviluppo disponibile: solo la potenza analitica è stata calcolata.")
        return
    print("\nQuota di blocchi di sviluppo che superano la soglia (indica sensibilità, non una scelta):")
    for n in a.trials:
        d = np.array([deflated_sharpe(b, n, a.var_sr) for b in blocks])
        print(f"  N={n:<5} " + "  ".join(f"≥{p:.2f}: {(d >= p).mean():.0%}" for p in (0.95, 0.90, 0.80, 0.70)))
    print("\nLe soglie restano quelle in rules/research.json → judge. Cambiarle è una decisione tua, documentata, mai fatta sul blocco bloccato.")


if __name__ == "__main__":
    main()
