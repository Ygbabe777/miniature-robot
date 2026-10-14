"""Audit del Judge: misura il comportamento EFFETTIVO prima di toccare qualsiasi soglia.

Uso:  python scripts/judge_audit.py [--out docs/JUDGE_AUDIT.md] [--reps 1500]

Tutto qui è SINTETICO (serie simulate con Sharpe vero noto): serve a misurare potenza statistica e tassi di falsi positivi/negativi
del Judge, NON a dire nulla su una strategia reale. Nessun blocco OOS reale viene letto. Output riproducibile (seed fisso).
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ofo import farming as F                                            # noqa: E402
from ofo.judge import Thresholds, judge                                  # noqa: E402
from ofo.lifecycle import SessionPool                                    # noqa: E402
from ofo.prop import PRESETS                                             # noqa: E402
from ofo.rulesets import load_farming_config, load_rulesets              # noqa: E402
from ofo.stats import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe   # noqa: E402

ANN = math.sqrt(252)
VAR_SR = 0.001                                   # varianza dello Sharpe per-periodo tra i tentativi (ordine di grandezza osservato)


def sim(rng, annual_sharpe: float, days: int, daily_sd: float = 300.0, df: int = 4) -> np.ndarray:
    """Rendimenti giornalieri con code spesse (t di Student), Sharpe annuo vero noto."""
    t = rng.standard_t(df, days) / math.sqrt(df / (df - 2))
    return annual_sharpe / ANN * daily_sd + daily_sd * t


def power_table(reps: int, seed: int = 1):
    rng = np.random.default_rng(seed)
    days_l = [60, 90, 120, 180, 250]
    sharpes = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
    trials = [1, 10, 100, 1000, 5000]
    res = {}
    for d in days_l:
        for s in sharpes:
            X = [sim(rng, s, d) for _ in range(reps)]
            for n in trials:
                dsr = np.array([deflated_sharpe(x, n, VAR_SR) for x in X])
                res[(d, s, n)] = (float((dsr >= 0.95).mean()), float((dsr >= 0.70).mean()))
    return days_l, sharpes, trials, res


def needed_sharpe(days: int, n: int, p: float) -> float:
    """Sharpe annuo (normale, approssimazione PSR) che serve perché DSR sia in media >= p."""
    from scipy.stats import norm
    b = expected_max_sharpe(n, VAR_SR)
    return (b + norm.ppf(p) / math.sqrt(days - 1)) * ANN


def nan_behaviour():
    th = Thresholds(); rules = PRESETS["50k"]; rng = np.random.default_rng(3)
    base = list(sim(rng, 2.0, 150))
    out = {}
    for name, x in {"serie con un NaN": base[:70] + [float("nan")] + base[71:], "serie con inf": base[:10] + [float("inf")] + base[11:],
                    "serie vuota": [], "varianza zero": [0.0] * 150}.items():
        try:
            v = judge(x, rules, 100, VAR_SR, th)
            out[name] = f"{v.status} (checks={v.checks})"
        except Exception as e:
            out[name] = f"ECCEZIONE {type(e).__name__}: {str(e)[:70]}"
    return out


def pool(close, worst, best, trades=1.0, cost=8.0):
    n = len(close)
    return SessionPool(np.array(close, float), np.array(worst, float), np.array(best, float), np.full(n, trades), np.full(n, float(cost)),
                       [f"2025-{i:05d}" for i in range(n)], 5.0)


def eval_vs_payout():
    """Strategie con la stessa media ma profili diversi: la probabilità di passare la valutazione NON predice il primo payout."""
    cfg = {**load_farming_config(), "n_outer": 10, "n_inner": 80}
    rs = load_rulesets()["GENERIC_EOD_50K"]
    rng = np.random.default_rng(5); n = 160
    prof = {}
    c = rng.normal(70, 250, n); prof["regolare (rumore moderato)"] = pool(c, np.minimum(c, 0) - 60, np.maximum(c, 0) + 60)
    c = np.where(rng.random(n) < 0.12, rng.normal(1500, 300, n), rng.normal(-150, 60, n)); prof["lotteria (rari colpi grossi)"] = pool(c, np.minimum(c, 0) - 80, np.maximum(c, 0) + 80)
    c = np.where(rng.random(n) < 0.04, -1800, rng.normal(110, 90, n)); prof["pennica (vince poco, perde raro e molto)"] = pool(c, np.minimum(c, 0) - 40, np.maximum(c, 0) + 40)
    out = {}
    for k, p in prof.items():
        m = F.estimate(p, rs, 1.0, "A", cfg, seed=2)
        out[k] = {"media_giorno": float(p.close.mean()), "p_pass": m["p_pass"], "p_first_payout": m["p_first_payout"], "p_funded_survival": m["p_funded_survival"],
                  "npfv_mean": m["npfv"]["mean"], "npfv_lcb95": m["npfv"]["lcb95"]}
    return out


def bust_at_base_vs_chosen():
    """Il Judge legacy controlla p_bust a taglia BASE (2 contratti). Il farming sceglie la taglia sul development: i due non coincidono."""
    rng = np.random.default_rng(8); rules = PRESETS["50k"]; th = Thresholds()
    rows = []
    for s in (1.0, 2.0, 3.0):
        x = sim(rng, s, 150, daily_sd=420)
        v = judge(x, rules, 50, VAR_SR, th)
        rows.append((s, v.mc.get("p_bust"), (v.mc.get("best_size") or {}).get("micro_equiv"), (v.mc.get("best_size") or {}).get("p_fast"), v.status))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="docs/JUDGE_AUDIT.md"); ap.add_argument("--reps", type=int, default=1500)
    a = ap.parse_args()
    days_l, sharpes, trials, res = power_table(a.reps)
    L = ["# Audit del Judge", "",
         "Misurazione del comportamento effettivo del Judge **prima** di modificare qualunque soglia. Dati sintetici (Sharpe vero noto, code spesse t4, "
         f"{a.reps} ripetizioni per cella, var_sr tra tentativi = {VAR_SR}). Nessun blocco OOS reale è stato letto.", "",
         "## 1. Potenza statistica: il blocco mai visto è corto", "",
         "Sharpe annuo **che serve** perché il Deflated Sharpe medio raggiunga la soglia (approssimazione PSR):", "",
         "| giorni OOS | N=1 DSR≥0.95 | N=100 | N=1000 | N=5000 | N=1 DSR≥0.70 | N=100 |", "|---|---|---|---|---|---|---|"]
    for d in days_l:
        L.append(f"| {d} | {needed_sharpe(d, 1, .95):.2f} | {needed_sharpe(d, 100, .95):.2f} | {needed_sharpe(d, 1000, .95):.2f} | {needed_sharpe(d, 5000, .95):.2f} | "
                 f"{needed_sharpe(d, 1, .70):.2f} | {needed_sharpe(d, 100, .70):.2f} |")
    L += ["", "Con ~90 giorni mai visti la soglia 0.95 richiede Sharpe annuo ≈ 2.8 anche con UN solo tentativo, ≈ 4 con 100. Sharpe ≥ 2.8 è raro e quasi mai reale su futures retail: "
          "**un Judge che chiede questo non distingue i buoni dai cattivi, li scarta tutti** (falso negativo sistematico). Non è severità: è assenza di potenza.", "",
          "### Probabilità di superare DSR≥0.95 (prima cifra) e DSR≥0.70 (seconda), per Sharpe vero × giorni OOS, con N tentativi", ""]
    for n in (1, 100, 5000):
        L += [f"**N = {n}**", "", "| giorni \\ Sharpe vero | " + " | ".join(f"{s:g}" for s in sharpes) + " |", "|---|" + "---|" * len(sharpes)]
        for d in days_l:
            L.append(f"| {d} | " + " | ".join(f"{res[(d, s, n)][0]:.0%} / {res[(d, s, n)][1]:.0%}" for s in sharpes) + " |")
        L.append("")
    L += ["Lettura: la colonna Sharpe 0 è il **tasso di falsi positivi** (strategia senza edge approvata): è ≈5–7% con N=1 (code spesse) e ≤2% già con N≥100: il rigore contro i falsi positivi non dipende da N=5000. Attenzione: la linea 0.70 NON è un'approvazione, ha 31% di falsi positivi con N=1 e ~12% con N=100: da sola non basta mai. "
          "La colonna Sharpe 1.5–2 (edge reale e ottimo) mostra la **potenza**: con N alto e poche settimane è quasi nulla.", "",
          "## 2. Conteggio dei tentativi (multiple testing)", "",
          "- Oggi il Judge deflaziona per `registry.n_trials + 1` = **tutti** i tentativi mai registrati (migliaia). È la scelta più severa e non è sbagliata come garanzia, "
          "ma è **sovra-conservativa**: il blocco OOS blindato viene guardato solo per i candidati finali, e i tentativi dello sweep sono già pagati dentro lo screening/walk-forward sul development.",
          "- Il rischio di selezione sul blocco bloccato è quello dei *locked looks* (strategie che lo hanno visto), non quello dei tentativi sul development. Il DSR copre il caso 'miglior Sharpe tra N su UN campione'; "
          "qui il campione OOS è diverso da quello su cui si è scelto → deflazionare per N globale conta due volte.",
          "- Decisione: la base resta **configurabile** (`dsr_trials`: raw | locked_looks | effective). Il default NON è stato cambiato dall'audit. La separazione che propongo è architetturale: "
          "il giudizio sull'edge usa un **test a due livelli** (vedi `judges.py`): soglia severa per APPROVED, ma esito 'evidenza insufficiente' (non 'respinta') quando l'intervallo di incertezza include ancora l'edge utile.", "",
          "## 3. Valori non finiti / campioni degenerati", ""]
    for k, v in nan_behaviour().items():
        L.append(f"- {k}: {v}")
    L += ["", "Difetto: un NaN/inf produce una *bocciatura* (confronti con NaN → False) o un'eccezione; non è un'evidenza contro la strategia, è un **errore di contabilità**. "
          "Nel nuovo sistema diventa `EXECUTION_FAILED` con causa esplicita.", "",
          "## 4. Rischio di bust: taglia base vs taglia raccomandata", "",
          "| Sharpe vero | p_bust a taglia base (2 contratti) | taglia scelta (micro) | p(pass in 5 gg) | stato legacy |", "|---|---|---|---|---|"]
    for s, pb, sz, pf, st in bust_at_base_vs_chosen():
        L.append(f"| {s:g} | {pb if pb is None else f'{pb:.0%}'} | {sz} | {pf if pf is None else f'{pf:.0%}'} | {st} |")
    L += ["", "Il check legacy `bust_risk ≤ 40%` è calcolato alla taglia **base**, non a quella che OFO poi raccomanda. Una strategia con edge credibile ma volatilità alta alla taglia base viene bocciata "
          "per un parametro di *sizing* che l'ottimizzatore di taglia avrebbe corretto. È un'incompatibilità di sizing, non assenza di edge → classe `EDGE_VALID_PROP_INCOMPATIBLE`/`NEEDS_ROBUSTNESS`.", "",
          "## 5. Passare la valutazione ≠ sostenibilità da funded", "",
          "| profilo (stessa idea di media) | media €/giorno | P(pass) | P(primo payout) | P(sopravvive funded) | NPFV medio | NPFV LCB95 |", "|---|---|---|---|---|---|---|"]
    for k, m in eval_vs_payout().items():
        f = lambda v: "n/d" if v is None else f"{v:.0%}"
        L.append(f"| {k} | {m['media_giorno']:.0f} | {f(m['p_pass'])} | {f(m['p_first_payout'])} | {f(m['p_funded_survival'])} | {m['npfv_mean']:.0f} | {m['npfv_lcb95'] if m['npfv_lcb95'] is None else round(m['npfv_lcb95'])} |")
    L += ["", "La probabilità di passare la valutazione non è un proxy affidabile del primo payout né della sopravvivenza da funded: il legacy `pass_rate ≥ 30% in 5 giorni` è un criterio del **proprietario** "
          "(velocità di pass) mescolato al giudizio sull'edge. Nel nuovo sistema vive solo nel PropJudge, mai nell'EdgeJudge.", "",
          "## 6. Conclusioni dell'audit", "",
          "1. **Falsi negativi per mancanza di potenza**: DSR≥0.95 su ~90 giorni è irraggiungibile per edge reali; la causa principale delle bocciature 'ingiuste' è statistica, non di qualità.",
          "2. Il rigore contro i **falsi positivi** (Sharpe 0) si mantiene anche con soglie più basse e N moderato: la soglia di approvazione NON si abbassa; cambia cosa si conclude quando non è raggiunta.",
          "3. Il verdetto legacy mescola tre domande (edge, compatibilità con la prop, velocità di pass). Vanno separate: EdgeJudge / PropJudge / FinalJudge.",
          "4. NaN/inf e AccountingError non devono diventare 'strategia respinta' né far fallire il job: `EXECUTION_FAILED`.",
          "5. `bust_risk` alla taglia base e `pass_rate` entro 5 giorni sono criteri di sizing/obiettivo, non di edge.",
          "6. Nessuna soglia è stata modificata sulla base di questo audit; le calibrazioni usano solo potenza analitica/sintetica e dati di development (`scripts/judge_sensitivity.py`)."]
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
