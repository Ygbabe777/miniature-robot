# Audit del Judge

Misurazione del comportamento effettivo del Judge **prima** di modificare qualunque soglia. Dati sintetici (Sharpe vero noto, code spesse t4, 600 ripetizioni per cella, var_sr tra tentativi = 0.001). Nessun blocco OOS reale è stato letto.

## 1. Potenza statistica: il blocco mai visto è corto

Sharpe annuo **che serve** perché il Deflated Sharpe medio raggiunga la soglia (approssimazione PSR):

| giorni OOS | N=1 DSR≥0.95 | N=100 | N=1000 | N=5000 | N=1 DSR≥0.70 | N=100 |
|---|---|---|---|---|---|---|
| 60 | 3.40 | 4.67 | 5.03 | 5.25 | 1.08 | 2.35 |
| 90 | 2.77 | 4.04 | 4.40 | 4.62 | 0.88 | 2.15 |
| 120 | 2.39 | 3.66 | 4.03 | 4.24 | 0.76 | 2.03 |
| 180 | 1.95 | 3.22 | 3.59 | 3.80 | 0.62 | 1.89 |
| 250 | 1.65 | 2.93 | 3.29 | 3.51 | 0.53 | 1.80 |

Con ~90 giorni mai visti la soglia 0.95 richiede Sharpe annuo ≈ 2.8 anche con UN solo tentativo, ≈ 4 con 100. Sharpe ≥ 2.8 è raro e quasi mai reale su futures retail: **un Judge che chiede questo non distingue i buoni dai cattivi, li scarta tutti** (falso negativo sistematico). Non è severità: è assenza di potenza.

### Probabilità di superare DSR≥0.95 (prima cifra) e DSR≥0.70 (seconda), per Sharpe vero × giorni OOS, con N tentativi

**N = 1**

| giorni \ Sharpe vero | 0 | 0.5 | 1 | 1.5 | 2 | 3 |
|---|---|---|---|---|---|---|
| 60 | 6% / 31% | 11% / 43% | 13% / 48% | 21% / 61% | 27% / 66% | 49% / 84% |
| 90 | 7% / 31% | 12% / 45% | 20% / 54% | 26% / 66% | 35% / 77% | 58% / 90% |
| 120 | 7% / 31% | 11% / 44% | 18% / 56% | 25% / 70% | 42% / 81% | 62% / 94% |
| 180 | 4% / 27% | 13% / 48% | 24% / 60% | 34% / 77% | 54% / 87% | 80% / 98% |
| 250 | 4% / 32% | 12% / 49% | 27% / 69% | 48% / 83% | 62% / 94% | 90% / 99% |

**N = 100**

| giorni \ Sharpe vero | 0 | 0.5 | 1 | 1.5 | 2 | 3 |
|---|---|---|---|---|---|---|
| 60 | 2% / 13% | 3% / 22% | 4% / 25% | 7% / 38% | 12% / 44% | 28% / 68% |
| 90 | 2% / 12% | 2% / 19% | 5% / 28% | 8% / 36% | 12% / 48% | 34% / 72% |
| 120 | 1% / 10% | 2% / 15% | 4% / 25% | 7% / 36% | 16% / 50% | 34% / 74% |
| 180 | 1% / 5% | 2% / 13% | 4% / 25% | 7% / 37% | 18% / 56% | 46% / 83% |
| 250 | 0% / 3% | 1% / 8% | 2% / 23% | 11% / 42% | 19% / 58% | 55% / 89% |

**N = 5000**

| giorni \ Sharpe vero | 0 | 0.5 | 1 | 1.5 | 2 | 3 |
|---|---|---|---|---|---|---|
| 60 | 1% / 8% | 2% / 14% | 3% / 18% | 4% / 27% | 6% / 34% | 21% / 59% |
| 90 | 0% / 6% | 1% / 12% | 3% / 19% | 4% / 26% | 8% / 35% | 22% / 61% |
| 120 | 0% / 4% | 0% / 8% | 2% / 14% | 3% / 20% | 8% / 36% | 24% / 59% |
| 180 | 0% / 2% | 1% / 6% | 2% / 12% | 3% / 19% | 8% / 34% | 29% / 68% |
| 250 | 0% / 0% | 0% / 2% | 0% / 10% | 4% / 21% | 9% / 37% | 36% / 74% |

Lettura: la colonna Sharpe 0 è il **tasso di falsi positivi** (strategia senza edge approvata): è ≈5–7% con N=1 (code spesse) e ≤2% già con N≥100: il rigore contro i falsi positivi non dipende da N=5000. Attenzione: la linea 0.70 NON è un'approvazione, ha 31% di falsi positivi con N=1 e ~12% con N=100: da sola non basta mai. La colonna Sharpe 1.5–2 (edge reale e ottimo) mostra la **potenza**: con N alto e poche settimane è quasi nulla.

## 2. Conteggio dei tentativi (multiple testing)

- Oggi il Judge deflaziona per `registry.n_trials + 1` = **tutti** i tentativi mai registrati (migliaia). È la scelta più severa e non è sbagliata come garanzia, ma è **sovra-conservativa**: il blocco OOS blindato viene guardato solo per i candidati finali, e i tentativi dello sweep sono già pagati dentro lo screening/walk-forward sul development.
- Il rischio di selezione sul blocco bloccato è quello dei *locked looks* (strategie che lo hanno visto), non quello dei tentativi sul development. Il DSR copre il caso 'miglior Sharpe tra N su UN campione'; qui il campione OOS è diverso da quello su cui si è scelto → deflazionare per N globale conta due volte.
- Decisione: la base resta **configurabile** (`dsr_trials`: raw | locked_looks | effective). Il default NON è stato cambiato dall'audit. La separazione che propongo è architetturale: il giudizio sull'edge usa un **test a due livelli** (vedi `judges.py`): soglia severa per APPROVED, ma esito 'evidenza insufficiente' (non 'respinta') quando l'intervallo di incertezza include ancora l'edge utile.

## 3. Valori non finiti / campioni degenerati

- serie con un NaN: REJECTED (checks={'enough_days': True, 'dsr': False, 'pass_rate': True, 'bust_risk': True})
- serie con inf: REJECTED (checks={'enough_days': True, 'dsr': False, 'pass_rate': True, 'bust_risk': True})
- serie vuota: INSUFFICIENT_EVIDENCE (checks={'enough_days': False})
- varianza zero: REJECTED (checks={'enough_days': True, 'dsr': False, 'pass_rate': False, 'bust_risk': True})

Difetto: un NaN/inf produce una *bocciatura* (confronti con NaN → False) o un'eccezione; non è un'evidenza contro la strategia, è un **errore di contabilità**. Nel nuovo sistema diventa `EXECUTION_FAILED` con causa esplicita.

## 4. Rischio di bust: taglia base vs taglia raccomandata

| Sharpe vero | p_bust a taglia base (2 contratti) | taglia scelta (micro) | p(pass in 5 gg) | stato legacy |
|---|---|---|---|---|
| 1 | 45% | 20 | 50% | REJECTED |
| 2 | 61% | 20 | 40% | REJECTED |
| 3 | 10% | 20 | 59% | APPROVED |

Il check legacy `bust_risk ≤ 40%` è calcolato alla taglia **base**, non a quella che OFO poi raccomanda. Una strategia con edge credibile ma volatilità alta alla taglia base viene bocciata per un parametro di *sizing* che l'ottimizzatore di taglia avrebbe corretto. È un'incompatibilità di sizing, non assenza di edge → classe `EDGE_VALID_PROP_INCOMPATIBLE`/`NEEDS_ROBUSTNESS`.

## 5. Passare la valutazione ≠ sostenibilità da funded

| profilo (stessa idea di media) | media €/giorno | P(pass) | P(primo payout) | P(sopravvive funded) | NPFV medio | NPFV LCB95 |
|---|---|---|---|---|---|---|
| regolare (rumore moderato) | 43 | 89% | 78% | 43% | 1636 | 15 |
| lotteria (rari colpi grossi) | 93 | 90% | 64% | 40% | 2403 | 811 |
| pennica (vince poco, perde raro e molto) | 9 | 74% | 59% | 9% | 1051 | -80 |

La probabilità di passare la valutazione non è un proxy affidabile del primo payout né della sopravvivenza da funded: il legacy `pass_rate ≥ 30% in 5 giorni` è un criterio del **proprietario** (velocità di pass) mescolato al giudizio sull'edge. Nel nuovo sistema vive solo nel PropJudge, mai nell'EdgeJudge.

## 6. Conclusioni dell'audit

1. **Falsi negativi per mancanza di potenza**: DSR≥0.95 su ~90 giorni è irraggiungibile per edge reali; la causa principale delle bocciature 'ingiuste' è statistica, non di qualità.
2. Il rigore contro i **falsi positivi** (Sharpe 0) si mantiene anche con soglie più basse e N moderato: la soglia di approvazione NON si abbassa; cambia cosa si conclude quando non è raggiunta.
3. Il verdetto legacy mescola tre domande (edge, compatibilità con la prop, velocità di pass). Vanno separate: EdgeJudge / PropJudge / FinalJudge.
4. NaN/inf e AccountingError non devono diventare 'strategia respinta' né far fallire il job: `EXECUTION_FAILED`.
5. `bust_risk` alla taglia base e `pass_rate` entro 5 giorni sono criteri di sizing/obiettivo, non di edge.
6. Nessuna soglia è stata modificata sulla base di questo audit; le calibrazioni usano solo potenza analitica/sintetica e dati di development (`scripts/judge_sensitivity.py`).
