# Audit matematico del DSR e dell'evidenza statistica

Tutto riproducibile: `python scripts/dsr_audit.py`. Formule in `src/ofo/evidence.py`, verificate in `tests/test_evidence.py` contro (i) una re-implementazione dal paper, (ii) integrazione numerica del massimo atteso di N normali, (iii) Monte Carlo. Nessun blocco bloccato reale è stato letto.

## 1. Formulazione esatta oggi in uso

- **Rendimenti:** P&L giornaliero per sessione (frequenza giornaliera). Lo Sharpe è **per sessione**, non annualizzato: `sharpe = mean/std(ddof=1)`. Annualizzato = ×√252.
- **PSR** (`stats.probabilistic_sharpe`): `Φ( (SR − SR*)·√(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²) )`, con γ₃ = skewness e γ₄ = curtosi **non in eccesso** (scipy, momenti campionari con bias). È la formula di Bailey & López de Prado (2012). Ipotesi: rendimenti i.i.d. con momenti finiti; l'autocorrelazione non è corretta (con autocorrelazione positiva l'errore standard è sottostimato).
- **Benchmark di selezione multipla** (`stats.expected_max_sharpe`): `SR₀ = √V · ((1−γ)Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)))`, γ = Euler-Mascheroni. V è una **varianza** (si applica `√`), in unità per-sessione al quadrato. Il codice la passa correttamente: `np.var([r['sharpe'] for r in rows], ddof=1)`, con `r['sharpe']` Sharpe per sessione dello sviluppo. Se N ≤ 1 il benchmark è 0 (DSR = PSR₀).
- **Precisione dell'approssimazione di N:** la formula chiusa sottostima il massimo atteso esatto del ~8% per N=2 e dell'1–3% per N≥5 (confronto con integrazione numerica): lievemente indulgente per pochi finalisti. `evidence.py` usa il valore esatto.
- **Distribuzione normale dello stimatore:** per T≈90 sessioni, con strategie che tradano di rado (molti giorni a zero) lo stimatore è lontano da normale; gli intervalli sono approssimati.

## 2. Sharpe annuo richiesto: giorni × finalisti (formulazione coerente sul blocco)

Due numeri per cella: **Sharpe OSSERVATO minimo** per DSR≥0,95 (= Sharpe vero che passa nel 50% dei casi) / **Sharpe VERO** che passa nell'80% dei casi (potenza 80%).

| sessioni mai viste | K=1 | K=2 | K=3 | K=5 | K=10 |
|---|---|---|---|---|---|
| 60 | 3.40 / 5.14 | 4.57 / 6.30 | 5.15 / 6.89 | 5.80 / 7.54 | 6.58 / 8.32 |
| 91 | 2.75 / 4.16 | 3.70 / 5.10 | 4.17 / 5.58 | 4.70 / 6.11 | 5.33 / 6.74 |
| 125 | 2.34 / 3.54 | 3.15 / 4.35 | 3.55 / 4.75 | 4.00 / 5.20 | 4.54 / 5.74 |
| 180 | 1.95 / 2.95 | 2.62 / 3.62 | 2.96 / 3.95 | 3.33 / 4.33 | 3.78 / 4.78 |
| 250 | 1.65 / 2.50 | 2.22 / 3.07 | 2.51 / 3.35 | 2.82 / 3.67 | 3.20 / 4.05 |
| 500 | 1.17 / 1.77 | 1.57 / 2.17 | 1.77 / 2.37 | 2.00 / 2.59 | 2.26 / 2.86 |
| 750 | 0.95 / 1.44 | 1.28 / 1.77 | 1.44 / 1.93 | 1.63 / 2.12 | 1.85 / 2.33 |

Per DSR≥0,70 (linea PAPER_CANDIDATE), Sharpe osservato minimo:

| sessioni | K=1 | K=3 | K=5 |
|---|---|---|---|
| 91 | 0.88 | 2.29 | 2.82 |
| 125 | 0.75 | 1.95 | 2.41 |
| 250 | 0.53 | 1.38 | 1.70 |
| 500 | 0.37 | 0.97 | 1.20 |

### Sessioni di dati NUOVI per approvare uno Sharpe vero dato (K=1, DSR≥0,95, potenza 80%)

| Sharpe vero annuo | 0,5 | 0,75 | 1,0 | 1,5 | 2,0 | 3,0 |
|---|---|---|---|---|---|---|
| sessioni | 6234 | 2771 | 1560 | 694 | 391 | 175 |
| anni (252 sess.) | 24.7 | 11.0 | 6.2 | 2.8 | 1.6 | 0.7 |

## 3. Formulazione attuale (V dello sweep + N) contro formulazione coerente sul blocco

Sharpe annuo OSSERVATO minimo per DSR≥0,95 su T sessioni. «Mista» = `expected_max_sharpe(N, V_sweep)` con V_sweep=0,001 (ordine di grandezza dello sweep di sviluppo); «coerente» = V del blocco stesso, 1/(T−1).

«Raw» = come prima del cambio: N = tutti i tentativi registrati (qui 2000), stessa V. Colonna K = finalisti sul blocco.

| T | K | mista (V sweep, N=K) | raw (V sweep, N=2000) | coerente (V blocco, K) |
|---|---|---|---|---|
| 91 | 3 | 3.18 | 4.48 | 4.17 |
| 91 | 10 | 3.54 | 4.48 | 5.33 |
| 91 | 30 | 3.79 | 4.48 | 6.17 |
| 125 | 3 | 2.77 | 4.08 | 3.55 |
| 125 | 10 | 3.14 | 4.08 | 4.54 |
| 125 | 30 | 3.39 | 4.08 | 5.26 |
| 250 | 3 | 2.08 | 3.39 | 2.51 |
| 250 | 10 | 2.45 | 3.39 | 3.20 |
| 250 | 30 | 2.70 | 3.39 | 3.71 |

Tre fatti, verificabili dalla tabella:
1. La formulazione **mista** (V dello sweep con N = accessi al blocco, cioè `locked_looks`) è la più indulgente delle tre. La varianza dello sweep (~0,001–0,003) è molto minore di quella dello stimatore sul blocco (1/(T−1) ≈ 0,011 con 91 sessioni): il benchmark di fortuna è sottostimato.
2. La base **raw** (N = migliaia) NON è «strutturalmente impossibile» rispetto al protocollo coerente: a T=91 richiede ~4,5 di Sharpe annuo osservato, quanto il protocollo coerente con 4–5 finalisti sul blocco (4,2–4,7). La difficoltà nasce dal fatto che 91 sessioni dànno una deviazione standard dello Sharpe annuo di ~1,7 (2,75 anche con K=1), non da quale N si conti.
3. Conclusione onesta: passare da `raw` a `locked_looks` NON si giustifica con questa matematica; l'avevo motivato con un argomento incompleto (non distinguevo V dello sweep da V del blocco). Raccomando di tornare a `raw` finché il Judge non adotta `dsr_holdout` con K_eff e una regola di processo che limiti i finalisti sul blocco (≤ 2–3).

## 4. Cosa significano N, trial effettivi e accessi al blocco hidden

Sono tre quantità diverse e non intercambiabili:

| quantità | simbolo | dove entra | formula |
|---|---|---|---|
| candidati dello sweep (configurazioni provate) | N_dev | **bias di selezione sullo sviluppo**: il migliore di N_dev ha uno Sharpe gonfiato | `SR₀ = √V_sweep · maxZ(N_dev,eff)` sul campione di sviluppo |
| prove indipendenti effettive | N_eff ≈ (Σλ)²/Σλ² (rapporto di partecipazione degli autovalori della correlazione) | sostituisce N_dev quando le varianti sono correlate (griglie vicine non sono prove indipendenti) | idem, con N_eff |
| accessi al blocco hidden | K (strategie/varianti che lo hanno visto) | **errore family-wise sul blocco**: K test sugli stessi giorni | `SR₀ = √(1/(T−1)) · maxZ(K_eff)` |

**Perché `locked_looks` è difendibile ma incompleto.** Il candidato finale è scelto sullo sviluppo; sul blocco mai visto non c'è bias di selezione per *quel* candidato (il suo Sharpe è una stima non distorta), quindi N_dev non va usato lì. Il rischio sul blocco è il numero K di candidati mostrati agli stessi giorni, ed è ciò che `locked_looks` conta. **Ma** la varianza V deve essere quella dello stimatore sul blocco (≈ 1/(T−1)), non quella dello sweep: l'implementazione attuale combina `V_sweep` con `N = locked_looks+1`, che nella sezione 3 risulta più indulgente della formulazione coerente. Inoltre K cresce con ogni sguardo, anche su strategie diverse e correlate: serve K_eff, non il contatore grezzo, altrimenti l'errore family-wise è sovrastimato. Raccomandazione: ripristinare la base `raw` (che nella pratica equivale a 4–5 finalisti sul blocco, sezione 3) finché il Judge non adotta `dsr_holdout` (K_eff), e limitare per processo i finalisti sul blocco (≤ 2–3).

## 5. Dispersione dello sweep (varianza, deviazione standard, correlazione)

- Il codice usa la **varianza** campionaria (`ddof=1`) degli Sharpe per sessione di tutte le righe dello sweep (anche di timeframe diversi). `expected_max_sharpe` applica `√V`: nessuno scambio varianza/deviazione standard (verificato dal test `sqrt(V)`: quadruplicare V raddoppia il benchmark).
- Righe fortemente correlate (griglie vicine, stessi segnali a parametri contigui) riducono sia la dispersione sia le prove indipendenti: usare V sull'intero sweep **con** N grezzo sovrastima la fortuna, usare V grezza con N_eff la sottostima. L'uso coerente è (V, N_eff) calcolati insieme sulle stesse serie, o raggruppare per cluster e prendere un rappresentante per cluster.
- La varianza dello sweep misura però la dispersione *tra configurazioni sullo sviluppo*; non è la varianza dello stimatore sul blocco hidden. Per la protezione sul blocco serve la 1/(T−1) (sezione 3).

## 6. Suddivisione cronologica per ~500 sessioni

| blocco | sessioni | indici | uso |
|---|---|---|---|
| SVILUPPO | 296 | [0, 296] | sweep, ottimizzatore, walk-forward annidato, scelta della taglia |
| embargo | 2 | [296, 298] | scartato |
| VALIDAZIONE | 100 | [298, 398] | sceglie i ≤ 2–3 finalisti (uso contato come prove); non si usa per dichiarare un edge |
| embargo | 2 | [398, 400] | scartato |
| HOLDOUT bloccato | 100 | [400, 500] | una sola lettura per finalista, mai usato per scegliere o tarare |

Onestà sulla potenza: passare da 91 a 100 sessioni di holdout aumenta lo z medio solo di √(100/91) ≈ +5%. **Nessuna suddivisione di 500 sessioni può rendere dimostrabile uno Sharpe ≈ 1** (potenza ~15–20%, sezione 2): il blocco serve da *veto* contro l'overfitting e da conferma per effetti forti; la prova di effetti moderati viene dall'accumulo in avanti (sezione 8).

## 9. Sensibilità delle soglie su simulazioni sintetiche

3000 ripetizioni per cella, rendimenti normali con Sharpe vero noto, una strategia per ripetizione, sul blocco mai visto. Il tasso di promozione per «edge nullo» e «overfittata» è un **falso positivo**; per le altre classi è la **potenza** (il complemento è il falso negativo). Non c'è nessuna metrica basata sul numero di strategie approvate.

### Blocco mai visto: 91 sessioni, K_eff = 1 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.9% | 4.9% | 9.9% | 19.1% | 9.9% |
| edge debole (SR 0.5) | 9.0% | 9.0% | 17.6% | 30.1% | 17.6% |
| edge moderato (SR 1) | 15.2% | 15.2% | 25.5% | 41.3% | 25.5% |
| edge forte (SR 2) | 32.9% | 32.9% | 46.7% | 65.2% | 46.7% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 4.3% | 4.3% | 9.5% | 20.8% | 9.5% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **92.0%** dei casi: è il motivo per cui il blocco separato esiste.

### Blocco mai visto: 91 sessioni, K_eff = 3 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.8% | 0.8% | 1.6% | 4.3% | 8.4% |
| edge debole (SR 0.5) | 9.0% | 1.6% | 3.6% | 8.6% | 14.5% |
| edge moderato (SR 1) | 14.6% | 3.2% | 6.7% | 13.7% | 21.8% |
| edge forte (SR 2) | 32.2% | 9.4% | 17.2% | 31.2% | 42.8% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 5.1% | 0.5% | 1.5% | 4.6% | 9.0% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **92.5%** dei casi: è il motivo per cui il blocco separato esiste.

### Blocco mai visto: 125 sessioni, K_eff = 1 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.6% | 4.6% | 9.2% | 20.0% | 9.2% |
| edge debole (SR 0.5) | 9.6% | 9.6% | 17.3% | 30.8% | 17.3% |
| edge moderato (SR 1) | 17.2% | 17.2% | 28.4% | 46.2% | 28.4% |
| edge forte (SR 2) | 39.3% | 39.3% | 53.6% | 70.0% | 53.6% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 5.4% | 5.4% | 10.2% | 19.7% | 10.2% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **93.1%** dei casi: è il motivo per cui il blocco separato esiste.

### Blocco mai visto: 125 sessioni, K_eff = 3 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.6% | 0.5% | 1.5% | 4.2% | 7.4% |
| edge debole (SR 0.5) | 9.7% | 1.8% | 4.1% | 9.1% | 14.8% |
| edge moderato (SR 1) | 17.3% | 3.8% | 8.3% | 16.5% | 26.4% |
| edge forte (SR 2) | 39.4% | 14.1% | 24.1% | 38.1% | 50.7% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 5.3% | 0.6% | 1.9% | 5.0% | 8.7% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **92.2%** dei casi: è il motivo per cui il blocco separato esiste.

## 8. Evidenza prospettica: dal candidato alla conferma, senza riusare dati già visti

Protocollo: alla promozione a PAPER_CANDIDATE si registra una data di taglio (`cutoff`). Contano **solo** le sessioni successive. 4 controlli preregistrati (60, 120, 180, 250 sessioni in avanti), confine di Šidák per controllo (z ≥ 2.23), in modo che guardare più volte non gonfi l'errore. Le sessioni già osservate (sviluppo, validazione, holdout) non entrano mai nel test in avanti.

| classe | P(PAPER_CANDIDATE) con 125 sessioni, K=3 | P(FORWARD_CONFIRMED \| candidato) | P(percorso intero) |
|---|---|---|---|
| edge nullo | 7.1% | 1.9% | 0.13% |
| edge debole (0,5) | 16.5% | 8.1% | 1.33% |
| edge moderato (1,0) | 25.9% | 15.5% | 4.00% |
| edge forte (2,0) | 50.5% | 50.1% | 25.33% |

Lettura: il percorso intero filtra molto bene i falsi positivi (la colonna dell'edge nullo) ma, con 125 sessioni di holdout e 250 in avanti, promuove anche solo una parte degli edge reali (potenza piena per Sharpe 1 richiede ~1.500 sessioni, sezione 2). Il candidato a paper è anche una selezione debole: a K=3 passa il ~7% dei nulli contro il ~26% di uno Sharpe 1 (rapporto ~3,5): non va letto come una conferma. I controlli aggiuntivi si preregistrano **prima**, non dopo aver visto i dati.

## 7. Livelli di evidenza separati

| livello | cosa significa | cosa NON significa |
|---|---|---|
| `RESEARCH_APPROVED` | blocco hidden: DSR coerente ≥ 0,95 con K_eff finalisti, P&L > 0 dopo costi, cancelli di integrità e robustezza superati, ≥ 1 ruleset compatibile | non è tradabile: è un risultato di ricerca su un campione corto |
| `PAPER_CANDIDATE` | plausibile: DSR coerente ≥ 0,70, P(SR>0) ≥ 90%, P&L > 0, campione sufficiente, nessuna incompatibilità prop | non è un'approvazione; la probabilità che uno SR nullo ci arrivi non è trascurabile (sezione 9) |
| `FORWARD_CONFIRMED` | solo sessioni **dopo** la data di taglio superano il test sequenziale preregistrato, e non peggiorano rispetto al blocco | non usa nessun dato visto prima del taglio |
| `PROP_ELIGIBLE` | `FORWARD_CONFIRMED` + almeno un ruleset COMPATIBLE (conformità, primo payout, NPFV con intervallo, costi ×2) | OFO non invia ordini: «LIVE» resta una decisione manuale del proprietario |

La funzione `evidence.stage_of(judgment, forward)` calcola il livello dai soli dati prospettici e dal giudizio; i dati in avanti non possono declassare retroattivamente ma neppure promuovere senza il test sequenziale.

## 10. Risposta alla domanda: quale evidenza è realistica con due anni?

1. Con ~100 sessioni mai viste e un solo finalista, DSR≥0,95 richiede uno Sharpe **osservato** di ~2,6 annuo; per passare in 8 casi su 10 serve uno Sharpe **vero** di ~4. Un edge reale di Sharpe 0,5–2 viene approvato nel 9–40% dei casi a K=1 e nel 2–14% con K=3 (sezione 9): la soglia è corretta contro i falsi positivi (≈5% a K=1, <1% con più finalisti) ma ha potenza bassa.
2. Quindi con due anni si può **pretendere**: (a) nessun falso positivo evidente (veto sull'holdout), (b) evidenza *plausibile* (PAPER_CANDIDATE), (c) la prova vera dall'accumulo prospettico, che per uno Sharpe 1 richiede circa 6 anni a potenza 80% (circa 1 anno per Sharpe 2,5).
3. «Non approvata» ≠ «senza edge»: finché l'intervallo del Sharpe include sia 0 sia valori utili, il verdetto è `INSUFFICIENT_EVIDENCE`/`PAPER_CANDIDATE`, mai `STATISTICALLY_REJECTED`. Il Judge può approvare zero strategie: è l'esito corretto quando la potenza non basta.
4. Integrità: nessuna soglia è stata tarata guardando il blocco; i parametri della sezione 9 sono scelti da questa analisi sintetica e dall'aritmetica, non da quante strategie passano.
