# Confronto shadow: raw, locked_looks, dsr_holdout

**Solo diagnostico.** Nessuna soglia è stata tarata su questi numeri e nessun verdetto storico è stato ricalcolato.

## Scenari sintetici

### Blocco mai visto: 100 sessioni — K preregistrati = 3, tentativi raw = 2000, accessi al blocco = 5, V sweep = 0.001

| classe | raw ≥0,95 | locked_looks ≥0,95 | **dsr_holdout ≥0,95 (ufficiale)** | PSR₀ ≥0,95 (nessuna correzione) |
|---|---|---|---|---|
| edge nullo (SR 0) | 0.5% | 2.1% | **0.4%** | 4.3% |
| edge debole (0,5) | 0.7% | 3.3% | **0.5%** | 7.7% |
| edge moderato (1,0) | 1.7% | 7.3% | **1.1%** | 14.9% |
| edge forte (2,0) | 6.5% | 20.3% | **5.5%** | 32.1% |
| edge molto forte (3,0) | 19.1% | 43.2% | **15.2%** | 59.1% |
| overfittata (migliore di 50 nulle) | 0.9% | 2.2% | **0.6%** | 4.9% |

Per «edge nullo» e «overfittata» la cifra è un **falso positivo**; per le altre è la **potenza**. Nessuna metrica qui è il numero di strategie approvate.

### Blocco mai visto: 250 sessioni — K preregistrati = 3, tentativi raw = 2000, accessi al blocco = 5, V sweep = 0.001

| classe | raw ≥0,95 | locked_looks ≥0,95 | **dsr_holdout ≥0,95 (ufficiale)** | PSR₀ ≥0,95 (nessuna correzione) |
|---|---|---|---|---|
| edge nullo (SR 0) | 0.1% | 1.3% | **0.5%** | 3.9% |
| edge debole (0,5) | 0.0% | 3.4% | **0.6%** | 12.1% |
| edge moderato (1,0) | 1.5% | 10.5% | **3.8%** | 26.3% |
| edge forte (2,0) | 8.9% | 39.1% | **21.3%** | 63.4% |
| edge molto forte (3,0) | 34.3% | 74.1% | **54.3%** | 90.3% |
| overfittata (migliore di 50 nulle) | 0.0% | 1.0% | **0.1%** | 4.7% |

Per «edge nullo» e «overfittata» la cifra è un **falso positivo**; per le altre è la **potenza**. Nessuna metrica qui è il numero di strategie approvate.

## Dossier storici (diagnostico, nessun verdetto ricalcolato)

Nessun studio con blocco hidden letto trovato in `/nonexistent/reports`. Esegui lo script sul tuo computer: `python scripts/judge_compare.py --work work`.

