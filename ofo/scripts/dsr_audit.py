"""Audit matematico del DSR e dell'evidenza statistica di OFO (due anni di dati).

Uso:  python scripts/dsr_audit.py [--out docs/DSR_AUDIT.md] [--reps 4000]

Solo aritmetica e simulazioni SINTETICHE con seed fisso: nessun blocco bloccato reale viene letto, e nessuna metrica qui è il numero di
strategie approvate (si misurano tassi di errore e potenza per classe di strategia). Le formule sono in src/ofo/evidence.py e sono
verificate in tests/test_evidence.py contro riferimenti indipendenti.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ofo import evidence as EV                                         # noqa: E402
from ofo.stats import expected_max_sharpe                              # noqa: E402

ANN = math.sqrt(252)


def psr_v(X: np.ndarray, b: float) -> np.ndarray:
    """Vectorised PSR over rows (same formula as stats.probabilistic_sharpe)."""
    T = X.shape[1]
    m, s = X.mean(1), X.std(1, ddof=1)
    sr = m / s
    z = (X - m[:, None]) / X.std(1, ddof=0)[:, None]
    g3, g4 = (z ** 3).mean(1), (z ** 4).mean(1)
    den = 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2
    return st.norm.cdf((sr - b) * math.sqrt(T - 1) / np.sqrt(np.maximum(den, 1e-12)))


def sim(rng, reps, T, annual):
    return rng.standard_normal((reps, T)) + annual / ANN


def table_requirements(L):
    L += ["## 2. Sharpe annuo richiesto: giorni × finalisti (formulazione coerente sul blocco)", "",
          "Due numeri per cella: **Sharpe OSSERVATO minimo** per DSR≥0,95 (= Sharpe vero che passa nel 50% dei casi) / **Sharpe VERO** che passa nell'80% dei casi (potenza 80%).", "",
          "| sessioni mai viste | K=1 | K=2 | K=3 | K=5 | K=10 |", "|---|---|---|---|---|---|"]
    for T in (60, 91, 125, 180, 250, 500, 750):
        L.append(f"| {T} | " + " | ".join(f"{EV.required_observed_sharpe(T, k):.2f} / {EV.required_true_sharpe(T, k):.2f}" for k in (1, 2, 3, 5, 10)) + " |")
    L += ["", "Per DSR≥0,70 (linea PAPER_CANDIDATE), Sharpe osservato minimo:", "", "| sessioni | K=1 | K=3 | K=5 |", "|---|---|---|---|"]
    for T in (91, 125, 250, 500):
        L.append(f"| {T} | " + " | ".join(f"{EV.required_observed_sharpe(T, k, 0.70):.2f}" for k in (1, 3, 5)) + " |")
    L += ["", "### Sessioni di dati NUOVI per approvare uno Sharpe vero dato (K=1, DSR≥0,95, potenza 80%)", "", "| Sharpe vero annuo | 0,5 | 0,75 | 1,0 | 1,5 | 2,0 | 3,0 |", "|---|---|---|---|---|---|---|",
          "| sessioni | " + " | ".join(str(EV.sessions_needed(s)) for s in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)) + " |",
          "| anni (252 sess.) | " + " | ".join(f"{EV.sessions_needed(s) / 252:.1f}" for s in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)) + " |", ""]


def table_mixed_vs_coherent(L):
    L += ["## 3. Formulazione attuale (V dello sweep + N) contro formulazione coerente sul blocco", "",
          "Sharpe annuo OSSERVATO minimo per DSR≥0,95 su T sessioni. «Mista» = `expected_max_sharpe(N, V_sweep)` con V_sweep=0,001 (ordine di grandezza dello sweep di sviluppo); «coerente» = V del blocco stesso, 1/(T−1).", "",
          "«Raw» = come prima del cambio: N = tutti i tentativi registrati (qui 2000), stessa V. Colonna K = finalisti sul blocco.", "",
          "| T | K | mista (V sweep, N=K) | raw (V sweep, N=2000) | coerente (V blocco, K) |", "|---|---|---|---|---|"]
    for T in (91, 125, 250):
        for k in (3, 10, 30):
            mixed = (expected_max_sharpe(k, 0.001) + st.norm.ppf(0.95) / math.sqrt(T - 1)) * ANN
            coh = EV.required_observed_sharpe(T, k)
            raw = (expected_max_sharpe(2000, 0.001) + st.norm.ppf(0.95) / math.sqrt(T - 1)) * ANN
            L.append(f"| {T} | {k} | {mixed:.2f} | {raw:.2f} | {coh:.2f} |")
    L += ["", "Tre fatti, verificabili dalla tabella:",
          "1. La formulazione **mista** (V dello sweep con N = accessi al blocco, cioè `locked_looks`) è la più indulgente delle tre. La varianza dello sweep (~0,001–0,003) è molto minore di quella dello stimatore sul blocco (1/(T−1) ≈ 0,011 con 91 sessioni): il benchmark di fortuna è sottostimato.",
          "2. La base **raw** (N = migliaia) NON è «strutturalmente impossibile» rispetto al protocollo coerente: a T=91 richiede ~4,5 di Sharpe annuo osservato, quanto il protocollo coerente con 4–5 finalisti sul blocco (4,2–4,7). La difficoltà nasce dal fatto che 91 sessioni dànno una deviazione standard dello Sharpe annuo di ~1,7 (2,75 anche con K=1), non da quale N si conti.",
          "3. Conclusione onesta: passare da `raw` a `locked_looks` NON si giustifica con questa matematica; l'avevo motivato con un argomento incompleto (non distinguevo V dello sweep da V del blocco). Raccomando di tornare a `raw` finché il Judge non adotta `dsr_holdout` con K_eff e una regola di processo che limiti i finalisti sul blocco (≤ 2–3).", ""]


def sensitivity(L, reps, seed=11):
    rng = np.random.default_rng(seed)
    classes = [("edge nullo (1 strategia preregistrata)", 0.0), ("edge debole", 0.5), ("edge moderato", 1.0), ("edge forte", 2.0)]
    L += ["## 9. Sensibilità delle soglie su simulazioni sintetiche", "",
          f"{reps} ripetizioni per cella, rendimenti normali con Sharpe vero noto, una strategia per ripetizione, sul blocco mai visto. "
          "Il tasso di promozione per «edge nullo» e «overfittata» è un **falso positivo**; per le altre classi è la **potenza** (il complemento è il falso negativo). "
          "Non c'è nessuna metrica basata sul numero di strategie approvate.", ""]
    for T in (91, 125):
        for K in (1, 3):
            L += [f"### Blocco mai visto: {T} sessioni, K_eff = {K} finalisti", "",
                  "| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |", "|---|---|---|---|---|---|"]
            for name, s in classes:
                X = sim(rng, reps, T, s)
                b = EV.sr0_holdout(T, K)
                p0, pd = psr_v(X, 0.0), psr_v(X, b)
                paper = ((pd >= 0.70) & (p0 >= 0.90) & (X.sum(1) > 0)).mean()
                L.append(f"| {name} (SR {s:g}) | {(p0 >= .95).mean():.1%} | {(pd >= .95).mean():.1%} | {(pd >= .90).mean():.1%} | {(pd >= .80).mean():.1%} | {paper:.1%} |")
            # overfitted: best of M=50 null strategies on a 300-session development sample, then judged on the hidden block (true SR still 0)
            M, Td = 50, 300
            dev = rng.standard_normal((reps, M, Td))
            best = dev.mean(2) / dev.std(2, ddof=1)
            pick = best.argmax(1)
            dev_sel = dev[np.arange(reps), pick]
            dev_psr = psr_v(dev_sel, 0.0)
            X = sim(rng, reps, T, 0.0)
            b = EV.sr0_holdout(T, K)
            pd, p0 = psr_v(X, b), psr_v(X, 0.0)
            L.append(f"| **overfittata** (migliore di {M} nulle, SR sviluppo ≈ {best.max(1).mean() * ANN:.1f}, vero 0) | {(p0 >= .95).mean():.1%} | {(pd >= .95).mean():.1%} | {(pd >= .90).mean():.1%} | {(pd >= .80).mean():.1%} | "
                     f"{((pd >= .70) & (p0 >= .90)).mean():.1%} |")
            L += ["", f"Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **{(dev_psr >= .95).mean():.1%}** dei casi: è il motivo per cui il blocco separato esiste.", ""]
    return rng


def forward_pipeline(L, reps, rng):
    L += ["## 8. Evidenza prospettica: dal candidato alla conferma, senza riusare dati già visti", "",
          "Protocollo: alla promozione a PAPER_CANDIDATE si registra una data di taglio (`cutoff`). Contano **solo** le sessioni successive. "
          "4 controlli preregistrati (60, 120, 180, 250 sessioni in avanti), confine di Šidák per controllo (z ≥ "
          f"{EV.look_boundary(4):.2f}), in modo che guardare più volte non gonfi l'errore. Le sessioni già osservate (sviluppo, validazione, holdout) non entrano mai nel test in avanti.", "",
          "| classe | P(PAPER_CANDIDATE) con 125 sessioni, K=3 | P(FORWARD_CONFIRMED \\| candidato) | P(percorso intero) |", "|---|---|---|---|"]
    looks = [60, 120, 180, 250]
    for name, s in (("edge nullo", 0.0), ("edge debole (0,5)", 0.5), ("edge moderato (1,0)", 1.0), ("edge forte (2,0)", 2.0)):
        X = sim(rng, reps, 125, s)
        b = EV.sr0_holdout(125, 3)
        cand = (psr_v(X, b) >= .70) & (psr_v(X, 0.0) >= .90) & (X.sum(1) > 0)
        n_c = int(cand.sum())
        conf = 0
        for _ in range(n_c):
            conf += EV.forward_test(np.random.default_rng(int(rng.integers(1 << 30))).standard_normal(250) + s / ANN, looks)["status"] == "CONFIRMED"
        pc = n_c / reps
        pf = conf / n_c if n_c else float("nan")
        L.append(f"| {name} | {pc:.1%} | {pf:.1%} | {pc * (pf if n_c else 0):.2%} |")
    L += ["", "Lettura: il percorso intero filtra molto bene i falsi positivi (la colonna dell'edge nullo) ma, con 125 sessioni di holdout e 250 in avanti, promuove anche solo una parte degli edge reali (potenza piena per Sharpe 1 richiede ~1.500 sessioni, sezione 2). "
          "Il candidato a paper è anche una selezione debole: a K=3 passa il ~7% dei nulli contro il ~26% di uno Sharpe 1 (rapporto ~3,5): non va letto come una conferma. I controlli aggiuntivi si preregistrano **prima**, non dopo aver visto i dati.", ""]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="docs/DSR_AUDIT.md"); ap.add_argument("--reps", type=int, default=4000)
    a = ap.parse_args()
    L = ["# Audit matematico del DSR e dell'evidenza statistica", "",
         "Tutto riproducibile: `python scripts/dsr_audit.py`. Formule in `src/ofo/evidence.py`, verificate in `tests/test_evidence.py` contro (i) una re-implementazione dal paper, "
         "(ii) integrazione numerica del massimo atteso di N normali, (iii) Monte Carlo. Nessun blocco bloccato reale è stato letto.", "",
         "## 1. Formulazione esatta oggi in uso", "",
         "- **Rendimenti:** P&L giornaliero per sessione (frequenza giornaliera). Lo Sharpe è **per sessione**, non annualizzato: `sharpe = mean/std(ddof=1)`. Annualizzato = ×√252.",
         "- **PSR** (`stats.probabilistic_sharpe`): `Φ( (SR − SR*)·√(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²) )`, con γ₃ = skewness e γ₄ = curtosi **non in eccesso** (scipy, momenti campionari con bias). È la formula di Bailey & López de Prado (2012). Ipotesi: rendimenti i.i.d. con momenti finiti; l'autocorrelazione non è corretta (con autocorrelazione positiva l'errore standard è sottostimato).",
         "- **Benchmark di selezione multipla** (`stats.expected_max_sharpe`): `SR₀ = √V · ((1−γ)Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)))`, γ = Euler-Mascheroni. V è una **varianza** (si applica `√`), in unità per-sessione al quadrato. Il codice la passa correttamente: `np.var([r['sharpe'] for r in rows], ddof=1)`, con `r['sharpe']` Sharpe per sessione dello sviluppo. Se N ≤ 1 il benchmark è 0 (DSR = PSR₀).",
         f"- **Precisione dell'approssimazione di N:** la formula chiusa sottostima il massimo atteso esatto del ~8% per N=2 e dell'1–3% per N≥5 (confronto con integrazione numerica): lievemente indulgente per pochi finalisti. `evidence.py` usa il valore esatto.",
         "- **Distribuzione normale dello stimatore:** per T≈90 sessioni, con strategie che tradano di rado (molti giorni a zero) lo stimatore è lontano da normale; gli intervalli sono approssimati.", ""]
    table_requirements(L)
    table_mixed_vs_coherent(L)
    L += ["## 4. Cosa significano N, trial effettivi e accessi al blocco hidden", "",
          "Sono tre quantità diverse e non intercambiabili:", "",
          "| quantità | simbolo | dove entra | formula |", "|---|---|---|---|",
          "| candidati dello sweep (configurazioni provate) | N_dev | **bias di selezione sullo sviluppo**: il migliore di N_dev ha uno Sharpe gonfiato | `SR₀ = √V_sweep · maxZ(N_dev,eff)` sul campione di sviluppo |",
          "| prove indipendenti effettive | N_eff ≈ (Σλ)²/Σλ² (rapporto di partecipazione degli autovalori della correlazione) | sostituisce N_dev quando le varianti sono correlate (griglie vicine non sono prove indipendenti) | idem, con N_eff |",
          "| accessi al blocco hidden | K (strategie/varianti che lo hanno visto) | **errore family-wise sul blocco**: K test sugli stessi giorni | `SR₀ = √(1/(T−1)) · maxZ(K_eff)` |", "",
          "**Perché `locked_looks` è difendibile ma incompleto.** Il candidato finale è scelto sullo sviluppo; sul blocco mai visto non c'è bias di selezione per *quel* candidato (il suo Sharpe è una stima non distorta), quindi N_dev non va usato lì. Il rischio sul blocco è il numero K di candidati mostrati agli stessi giorni, ed è ciò che `locked_looks` conta. "
          "**Ma** la varianza V deve essere quella dello stimatore sul blocco (≈ 1/(T−1)), non quella dello sweep: l'implementazione attuale combina `V_sweep` con `N = locked_looks+1`, che nella sezione 3 risulta più indulgente della formulazione coerente. "
          "Inoltre K cresce con ogni sguardo, anche su strategie diverse e correlate: serve K_eff, non il contatore grezzo, altrimenti l'errore family-wise è sovrastimato. Raccomandazione: ripristinare la base `raw` (che nella pratica equivale a 4–5 finalisti sul blocco, sezione 3) finché il Judge non adotta `dsr_holdout` (K_eff), e limitare per processo i finalisti sul blocco (≤ 2–3).", "",
          "## 5. Dispersione dello sweep (varianza, deviazione standard, correlazione)", "",
          "- Il codice usa la **varianza** campionaria (`ddof=1`) degli Sharpe per sessione di tutte le righe dello sweep (anche di timeframe diversi). `expected_max_sharpe` applica `√V`: nessuno scambio varianza/deviazione standard (verificato dal test `sqrt(V)`: quadruplicare V raddoppia il benchmark).",
          "- Righe fortemente correlate (griglie vicine, stessi segnali a parametri contigui) riducono sia la dispersione sia le prove indipendenti: usare V sull'intero sweep **con** N grezzo sovrastima la fortuna, usare V grezza con N_eff la sottostima. L'uso coerente è (V, N_eff) calcolati insieme sulle stesse serie, o raggruppare per cluster e prendere un rappresentante per cluster.",
          "- La varianza dello sweep misura però la dispersione *tra configurazioni sullo sviluppo*; non è la varianza dello stimatore sul blocco hidden. Per la protezione sul blocco serve la 1/(T−1) (sezione 3).", ""]
    s = EV.propose_split(500)
    L += ["## 6. Suddivisione cronologica per ~500 sessioni", "",
          "| blocco | sessioni | indici | uso |", "|---|---|---|---|",
          f"| SVILUPPO | {s['sizes']['dev']} | {s['dev']} | sweep, ottimizzatore, walk-forward annidato, scelta della taglia |",
          f"| embargo | 2 | {s['embargo_1']} | scartato |",
          f"| VALIDAZIONE | {s['sizes']['validation']} | {s['validation']} | sceglie i ≤ 2–3 finalisti (uso contato come prove); non si usa per dichiarare un edge |",
          f"| embargo | 2 | {s['embargo_2']} | scartato |",
          f"| HOLDOUT bloccato | {s['sizes']['holdout']} | {s['holdout']} | una sola lettura per finalista, mai usato per scegliere o tarare |", "",
          "Onestà sulla potenza: passare da 91 a 100 sessioni di holdout aumenta lo z medio solo di √(100/91) ≈ +5%. **Nessuna suddivisione di 500 sessioni può rendere dimostrabile uno Sharpe ≈ 1** (potenza ~15–20%, sezione 2): il blocco serve da *veto* contro l'overfitting e da conferma per effetti forti; la prova di effetti moderati viene dall'accumulo in avanti (sezione 8).", ""]
    rng = sensitivity(L, a.reps)
    forward_pipeline(L, max(600, a.reps // 4), rng)
    L += ["## 7. Livelli di evidenza separati", "",
          "| livello | cosa significa | cosa NON significa |", "|---|---|---|",
          "| `RESEARCH_APPROVED` | blocco hidden: DSR coerente ≥ 0,95 con K_eff finalisti, P&L > 0 dopo costi, cancelli di integrità e robustezza superati, ≥ 1 ruleset compatibile | non è tradabile: è un risultato di ricerca su un campione corto |",
          "| `PAPER_CANDIDATE` | plausibile: DSR coerente ≥ 0,70, P(SR>0) ≥ 90%, P&L > 0, campione sufficiente, nessuna incompatibilità prop | non è un'approvazione; la probabilità che uno SR nullo ci arrivi non è trascurabile (sezione 9) |",
          "| `FORWARD_CONFIRMED` | solo sessioni **dopo** la data di taglio superano il test sequenziale preregistrato, e non peggiorano rispetto al blocco | non usa nessun dato visto prima del taglio |",
          "| `PROP_ELIGIBLE` | `FORWARD_CONFIRMED` + almeno un ruleset COMPATIBLE (conformità, primo payout, NPFV con intervallo, costi ×2) | OFO non invia ordini: «LIVE» resta una decisione manuale del proprietario |", "",
          "La funzione `evidence.stage_of(judgment, forward)` calcola il livello dai soli dati prospettici e dal giudizio; i dati in avanti non possono declassare retroattivamente ma neppure promuovere senza il test sequenziale.", "",
          "## 10. Risposta alla domanda: quale evidenza è realistica con due anni?", "",
          "1. Con ~100 sessioni mai viste e un solo finalista, DSR≥0,95 richiede uno Sharpe **osservato** di ~2,6 annuo; per passare in 8 casi su 10 serve uno Sharpe **vero** di ~4. Un edge reale di Sharpe 0,5–2 viene approvato nel 9–40% dei casi a K=1 e nel 2–14% con K=3 (sezione 9): la soglia è corretta contro i falsi positivi (≈5% a K=1, <1% con più finalisti) ma ha potenza bassa.",
          "2. Quindi con due anni si può **pretendere**: (a) nessun falso positivo evidente (veto sull'holdout), (b) evidenza *plausibile* (PAPER_CANDIDATE), (c) la prova vera dall'accumulo prospettico, che per uno Sharpe 1 richiede circa 6 anni a potenza 80% (circa 1 anno per Sharpe 2,5).",
          "3. «Non approvata» ≠ «senza edge»: finché l'intervallo del Sharpe include sia 0 sia valori utili, il verdetto è `INSUFFICIENT_EVIDENCE`/`PAPER_CANDIDATE`, mai `STATISTICALLY_REJECTED`. Il Judge può approvare zero strategie: è l'esito corretto quando la potenza non basta.",
          "4. Integrità: nessuna soglia è stata tarata guardando il blocco; i parametri della sezione 9 sono scelti da questa analisi sintetica e dall'aritmetica, non da quante strategie passano."]
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
