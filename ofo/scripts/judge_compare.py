"""Confronto SHADOW tra le tre formulazioni del DSR: raw, locked_looks, dsr_holdout. SOLO DIAGNOSTICO.

Uso:  python scripts/judge_compare.py [--work work] [--reps 3000] [--out docs/JUDGE_COMPARE.md]

* Parte 1 - scenari sintetici (seed fisso): tasso di promozione di ciascuna formulazione per classe (edge nullo, debole, moderato, forte, overfittata).
* Parte 2 - dossier storici in `<work>/reports`: per ogni studio con blocco hidden letto, il DSR ricalcolato dalla serie salvata con le tre formulazioni, accanto al
  verdetto storico. Nessun verdetto storico viene modificato, nessuna soglia viene tarata su questi studi: è una tabella di confronto.
Nessun parametro del Judge è letto da questo script per essere cambiato.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ofo import evidence as EV                                           # noqa: E402
from ofo.stats import deflated_sharpe, probabilistic_sharpe               # noqa: E402

ANN = math.sqrt(252)


def synthetic(reps: int, T: int, K: int, var_sr: float, n_raw: int, looks: int, seed: int = 21) -> list[str]:
    rng = np.random.default_rng(seed)
    L = [f"### Blocco mai visto: {T} sessioni — K preregistrati = {K}, tentativi raw = {n_raw}, accessi al blocco = {looks}, V sweep = {var_sr}", "",
         "| classe | raw ≥0,95 | locked_looks ≥0,95 | **dsr_holdout ≥0,95 (ufficiale)** | PSR₀ ≥0,95 (nessuna correzione) |", "|---|---|---|---|---|"]
    classes = [("edge nullo (SR 0)", 0.0), ("edge debole (0,5)", 0.5), ("edge moderato (1,0)", 1.0), ("edge forte (2,0)", 2.0), ("edge molto forte (3,0)", 3.0)]
    for name, s in classes:
        X = rng.standard_normal((reps, T)) + s / ANN
        raw = np.mean([deflated_sharpe(x, n_raw, var_sr) >= 0.95 for x in X])
        ll = np.mean([deflated_sharpe(x, looks + 1, var_sr) >= 0.95 for x in X])
        ho = np.mean([(EV.dsr_holdout(x, K)["value"] or 0) >= 0.95 for x in X])
        p0 = np.mean([probabilistic_sharpe(x, 0.0) >= 0.95 for x in X])
        L.append(f"| {name} | {raw:.1%} | {ll:.1%} | **{ho:.1%}** | {p0:.1%} |")
    # overfitted: best of 50 nulls on development, then judged on the block (true SR 0)
    M = 50
    dev = rng.standard_normal((reps, M, 300))
    pick = (dev.mean(2) / dev.std(2, ddof=1)).argmax(1)
    X = rng.standard_normal((reps, T))
    raw = np.mean([deflated_sharpe(x, n_raw, var_sr) >= 0.95 for x in X]); ll = np.mean([deflated_sharpe(x, looks + 1, var_sr) >= 0.95 for x in X])
    ho = np.mean([(EV.dsr_holdout(x, K)["value"] or 0) >= 0.95 for x in X]); p0 = np.mean([probabilistic_sharpe(x, 0.0) >= 0.95 for x in X])
    L.append(f"| overfittata (migliore di {M} nulle) | {raw:.1%} | {ll:.1%} | **{ho:.1%}** | {p0:.1%} |")
    L += ["", "Per «edge nullo» e «overfittata» la cifra è un **falso positivo**; per le altre è la **potenza**. Nessuna metrica qui è il numero di strategie approvate.", ""]
    return L


def historical(work: Path) -> list[str]:
    rep_dir = work / "reports"
    L = ["## Dossier storici (diagnostico, nessun verdetto ricalcolato)", ""]
    files = sorted(f for f in rep_dir.glob("*.json") if not f.name.endswith("_series.json")) if rep_dir.exists() else []
    rows = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            r = d["report"]
            lk = r.get("locked") or {}
            if "checks" not in lk:
                continue
            ser = json.loads((rep_dir / f"{f.stem}_series.json").read_text(encoding="utf-8", errors="replace"))
            x = np.array([v for _, v in (ser.get("locked") or [])], float)
            if len(x) < 10:
                continue
            old = lk.get("dsr") or {}
            var_sr = float(old.get("var_sr") or 0.001)
            n_raw = int(old.get("n_trials_raw") or old.get("n_trials") or 1000)
            looks = int(((r.get("trials") or {}).get("locked_looks")) or 5)
            m = EV.dsr_modes(x, var_sr=var_sr, n_raw=n_raw, locked_looks=looks, k_planned=3)
            rows.append((r.get("name", f.stem), len(x), (lk.get("verdict") or lk.get("status")), m, float(x.mean() / x.std(ddof=1) * ANN) if x.std(ddof=1) > 0 else 0.0))
        except Exception:
            continue
    if not rows:
        L += ["Nessun studio con blocco hidden letto trovato in `%s`. Esegui lo script sul tuo computer: `python scripts/judge_compare.py --work work`." % rep_dir, ""]
        return L
    L += ["| studio | sessioni | verdetto storico | raw | locked_looks | **dsr_holdout** | Sharpe annuo OOS |", "|---|---|---|---|---|---|---|"]
    for name, T, verdict, m, sr in rows:
        L.append(f"| {name} | {T} | {verdict} | {m['raw']['value']:.2f} | {m['locked_looks']['value']:.2f} | **{m['holdout_null']['value']:.2f}** | {sr:.2f} |")
    L += ["", "I valori «raw» e «locked_looks» sono ricalcolati con la V e i conteggi salvati nello studio quando disponibili, altrimenti con valori di ripiego (V=0,001, accessi=5): sono indicativi."]
    return L


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--work", default="work"); ap.add_argument("--reps", type=int, default=3000); ap.add_argument("--out", default="docs/JUDGE_COMPARE.md")
    a = ap.parse_args()
    L = ["# Confronto shadow: raw, locked_looks, dsr_holdout", "", "**Solo diagnostico.** Nessuna soglia è stata tarata su questi numeri e nessun verdetto storico è stato ricalcolato.", "",
         "## Scenari sintetici", ""]
    L += synthetic(a.reps, 100, 3, 0.001, 2000, 5)
    L += synthetic(a.reps, 250, 3, 0.001, 2000, 5)
    L += historical(Path(a.work))
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
