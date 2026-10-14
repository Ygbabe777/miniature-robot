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

2000 ripetizioni per cella, rendimenti normali con Sharpe vero noto, una strategia per ripetizione, sul blocco mai visto. Il tasso di promozione per «edge nullo» e «overfittata» è un **falso positivo**; per le altre classi è la **potenza** (il complemento è il falso negativo). Non c'è nessuna metrica basata sul numero di strategie approvate.

### Blocco mai visto: 91 sessioni, K_eff = 1 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.7% | 4.7% | 10.2% | 19.4% | 10.2% |
| edge debole (SR 0.5) | 8.6% | 8.6% | 16.6% | 29.5% | 16.6% |
| edge moderato (SR 1) | 15.3% | 15.3% | 25.4% | 41.1% | 25.4% |
| edge forte (SR 2) | 32.6% | 32.6% | 46.3% | 64.5% | 46.3% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 5.0% | 5.0% | 9.6% | 19.1% | 9.6% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **93.2%** dei casi: è il motivo per cui il blocco separato esiste.

### Blocco mai visto: 91 sessioni, K_eff = 3 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 6.2% | 0.5% | 1.8% | 5.8% | 9.8% |
| edge debole (SR 0.5) | 8.3% | 1.6% | 2.7% | 7.7% | 13.5% |
| edge moderato (SR 1) | 15.2% | 3.4% | 6.5% | 14.3% | 22.4% |
| edge forte (SR 2) | 33.5% | 9.8% | 17.7% | 32.0% | 43.9% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 5.0% | 0.7% | 1.9% | 4.5% | 8.3% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **92.0%** dei casi: è il motivo per cui il blocco separato esiste.

### Blocco mai visto: 125 sessioni, K_eff = 1 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.8% | 4.8% | 10.4% | 20.0% | 10.4% |
| edge debole (SR 0.5) | 10.3% | 10.3% | 17.4% | 31.2% | 17.4% |
| edge moderato (SR 1) | 17.2% | 17.2% | 27.5% | 42.8% | 27.5% |
| edge forte (SR 2) | 40.7% | 40.7% | 55.4% | 70.5% | 55.4% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 4.9% | 4.9% | 9.1% | 19.6% | 9.1% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **92.9%** dei casi: è il motivo per cui il blocco separato esiste.

### Blocco mai visto: 125 sessioni, K_eff = 3 finalisti

| classe | PSR₀≥0,95 (senza molteplicità) | DSR≥0,95 | DSR≥0,90 | DSR≥0,80 | PAPER_CANDIDATE (DSR≥0,70 e PSR₀≥0,90) |
|---|---|---|---|---|---|
| edge nullo (1 strategia preregistrata) (SR 0) | 4.7% | 0.7% | 1.8% | 4.3% | 8.2% |
| edge debole (SR 0.5) | 9.6% | 1.8% | 3.4% | 8.8% | 14.8% |
| edge moderato (SR 1) | 17.1% | 3.5% | 8.1% | 16.4% | 25.8% |
| edge forte (SR 2) | 38.8% | 12.9% | 21.9% | 37.1% | 50.7% |
| **overfittata** (migliore di 50 nulle, SR sviluppo ≈ 2.1, vero 0) | 5.2% | 0.9% | 1.9% | 4.8% | 9.2% |

Giudicata SUL SOLO sviluppo con PSR₀≥0,95 (senza holdout), la stessa classe overfittata viene promossa nel **92.2%** dei casi: è il motivo per cui il blocco separato esiste.

## 8. Evidenza prospettica: dal candidato alla conferma, senza riusare dati già visti

Protocollo: alla promozione a PAPER_CANDIDATE si registra una data di taglio (`cutoff`). Contano **solo** le sessioni successive. 4 controlli preregistrati (60, 120, 180, 250 sessioni in avanti), confine di Šidák per controllo (z ≥ 2.23), in modo che guardare più volte non gonfi l'errore. Le sessioni già osservate (sviluppo, validazione, holdout) non entrano mai nel test in avanti.

| classe | P(PAPER_CANDIDATE) con 125 sessioni, K=3 | P(FORWARD_CONFIRMED \| candidato) | P(percorso intero) |
|---|---|---|---|
| edge nullo | 9.2% | 5.5% | 0.50% |
| edge debole (0,5) | 16.0% | 7.3% | 1.17% |
| edge moderato (1,0) | 26.8% | 9.3% | 2.50% |
| edge forte (2,0) | 47.5% | 51.6% | 24.50% |

Lettura: il percorso intero filtra molto bene i falsi positivi (la colonna dell'edge nullo) ma, con 125 sessioni di holdout e 250 in avanti, promuove anche solo una parte degli edge reali (potenza piena per Sharpe 1 richiede ~1.500 sessioni, sezione 2). Il candidato a paper è anche una selezione debole: a K=3 passa il ~7% dei nulli contro il ~26% di uno Sharpe 1 (rapporto ~3,5): non va letto come una conferma. I controlli aggiuntivi si preregistrano **prima**, non dopo aver visto i dati.

## 11. Falsi positivi in condizioni avverse (item 6)

1500 ripetizioni, 3 finalisti preregistrati nulli (vero Sharpe 0) su 100 sessioni; tasso con cui **almeno uno** supera DSR≥0,95 sul blocco. «Formula nuda» = senza correzione per autocorrelazione, senza t di Student, senza margine; «ufficiale» = `dsr_holdout` con tutte le correzioni (margine 0,30 z). Il margine è stato scelto da questa griglia sintetica, non da dati reali.

| scenario | formula nuda | **ufficiale** |
|---|---|---|
| gaussiano, finalisti indipendenti | 1.1% | **0.4%** |
| gaussiano, finalisti correlati ρ=0,9 | 1.5% | **0.7%** |
| autocorrelazione AR(1) φ=0,2, ρ=0 | 5.9% | **1.7%** |
| autocorrelazione AR(1) φ=0,4, ρ=0 | 13.7% | **3.1%** |
| code spesse t(3), ρ=0 | 3.0% | **0.9%** |
| zero-inflated (70% giorni a zero) φ=0,2 | 8.7% | **3.8%** |
| asimmetria negativa (piccole vincite, rare perdite grosse) | 0.0% | **0.0%** |

Lettura: la formula nuda supera il 5% con autocorrelazione forte (φ=0,4) anche con K=3; con le correzioni tutti gli scenari stanno sotto il 5% (i finalisti correlati rendono il test più conservativo di Šidák, non meno). Con un solo finalista il caso peggiore (φ=0,4) resta intorno al 5%, ed è il motivo per cui K ufficiale è sempre il massimo preregistrato (3), mai il conteggio corrente.

## 12. Protocollo intero: blocco → paper → forward (item 10)

500 epoche simulate. Per ogni epoca: 3 finalisti giudicati sul blocco con il DSR ufficiale (100 sessioni); chi supera la linea PAPER_CANDIDATE va in forward (4 controlli preregistrati a 60/120/180/250 sessioni, confine di Šidák z≥2,23, correzione per autocorrelazione). Si misura la probabilità che **almeno uno** dei tre finalisti raggiunga ciascun livello: sono tassi family-wise dell'intero percorso.

| mondo | P(≥1 RESEARCH_APPROVED) | P(≥1 PAPER_CANDIDATE) | P(≥1 FORWARD_CONFIRMED) |
|---|---|---|---|
| nullo, ρ=0,9 | 1.0% | 7.4% | 0.6% |
| nullo, φ=0,4, ρ=0 | 3.6% | 23.8% | 1.8% |
| nullo, t(3) | 0.4% | 6.6% | 0.2% |
| nullo, zero-inflated φ=0,2 | 1.0% | 11.0% | 0.8% |
| edge debole 0,5 | 2.2% | 17.8% | 1.8% |
| edge moderato 1,0 | 4.4% | 27.6% | 5.2% |
| edge forte 2,0 | 12.0% | 53.0% | 32.6% |
| edge molto forte 3,0 | 30.0% | 78.4% | 70.8% |

Nei mondi nulli l'errore dell'intero percorso (colonna FORWARD_CONFIRMED) resta sotto il 2%: il candidato a paper è una selezione debole (le sue percentuali non sono errori finali), la conferma in avanti no. Per Sharpe 1,0 la conferma richiede molto più dei 250 sessioni in avanti previsti (sezione 2): il protocollo è onesto sulla propria potenza.

## 13. Il protocollo preregistrato in pratica

- **Epoca congelata.** Il blocco hidden è un intervallo fisso di sessioni. Dati nuovi aggiungono sessioni *dopo* l'epoca (riservate al forward); il taglio non si sposta più.
- **≤ 3 finalisti per epoca**, registrati *prima* di calcolare qualsiasi cosa sul blocco: fingerprint, parametri, versione del codice, dataset (limitato all'epoca), regola di sizing, ruleset, timestamp, K. Tabella append-only con catena di hash e trigger che rifiutano UPDATE/DELETE. Un nuovo test identico è una *ripetizione*, non un nuovo finalista.
- **Nessuna modifica dopo lo sguardo.** Una variante di una linea (template + nome senza numeri di versione) già giudicata sul blocco viene rifiutata: lo studio finisce `INSUFFICIENT_EVIDENCE` con cancello E7, senza toccare il blocco. Le diagnosi basate sul blocco non vengono più passate al Researcher durante l'epoca.
- **K ufficiale = massimo preregistrato**, non un conteggio che cresce: con meno di 3 finalisti il test resta conservativo.
- **`locked_looks`** resta il contatore separato di *tutti* gli sguardi al blocco (qualunque motivo); non è né i candidati dello sviluppo né K.
- **Slot pieni.** Con il limite a 3, dopo tre finalisti gli studi successivi che superano la validazione vengono messi in attesa (E7) finché non si apre una nuova epoca (servono ≥ 100 sessioni nuove; il vecchio blocco entra nello sviluppo). È il prezzo dell'integrità; l'attuale selezione dei finalisti è per ordine di arrivo tra gli studi che superano tutti i gate di sviluppo (screening, walk-forward annidato, robustezza): non esiste ancora un round di selezione per punteggio.
- **Validazione.** Il ruolo della validazione lo svolge il walk-forward annidato *dentro* lo sviluppo; non c'è un blocco di validazione separato nel motore (la suddivisione della sezione 6 resta una proposta).

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
