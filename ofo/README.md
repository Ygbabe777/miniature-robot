# OFO — Prop Project

Piattaforma di ricerca automatica di strategie su futures, con validazione statistica
rigorosa e simulazione delle regole prop (account 25k / 50k).

## Architettura (a livelli)
1. **Core deterministico** (`src/ofo`): backtest vettoriale, statistica anti-overfitting
   (PSR/DSR), simulatore regole prop + Monte Carlo, giudice a checklist. *Nessun LLM qui.*
2. **Agenti** (fase 1): Researcher, Optimizer, Validator, Judge. Gli LLM generano ipotesi;
   il verdetto lo danno i numeri.
3. **Gate NinjaTrader**: le strategie che passano il core vengono esportate in NinjaScript,
   testate nello Strategy Analyzer (sul PC dell'utente) e reimportate.
4. **UI 3D "The Lab"** (fase 2): laboratorio stile voxel/LEGO, dati live via WebSocket.
5. **Bot** (fase 3): segnali Discord, stato account, probabilità di passare.

## Flusso di lavoro
1. `scripts/ingest_paper.py PAPER.pdf` : PDF -> spec di strategie validate (serve FreeLLMAPI acceso).
2. `scripts/study.py DATI.csv.gz specs/starter.json` : sweep di centinaia di parametri per spec,
   giudizio sulla *robustezza della superficie*, walk-forward, un solo sguardo al blocco bloccato.
3. `scripts/account_ev.py reports/oos_pnl_X.csv` : valore atteso e dimensione ottimale per ogni piano prop.
   Le regole in `rules/firms.json` sono NON VERIFICATE e le tariffe sono vuote: vanno compilate.

## Regole non negoziabili
- Dati out-of-sample bloccati: l'optimizer non li vede mai.
- Ogni variante testata incrementa `n_trials`; la soglia DSR sale di conseguenza.
- Il giudice usa il limite *inferiore* dell'intervallo di confidenza della prob. di passare.
- I preset prop in `prop.py` sono segnaposto: verificare con il regolamento attuale.
- Le chiavi API stanno nei secret dell'ambiente, mai nel repo.

## Test
    pip install -e .[dev] && pytest
