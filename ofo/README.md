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
3. `scripts/run_loop.py DATI.csv.gz` : ciclo continuo. Legge i nuovi PDF/.txt in `ideas/`, fa lavorare
   gli agenti (Researcher, Planner, Auditor; prompt in `src/ofo/agents/prompts/*.md`) e scrive ogni
   passo in `data/events.jsonl` (la UI 3D lo leggerà). Il verdetto lo dà sempre il codice.
   Prima prova il tuo LLM con `scripts/check_llm.py`.
4. `scripts/lab.py DATI.csv.gz` : **il Lab** (laboratorio 3D voxel scuro con scienziati, interfaccia IT/EN, chat con lo Strategist, scheda Trader con candele dei tuoi dati, grafici di
   P&L / Monte Carlo / Sharpe / Sortino / profit factor, upload dei PDF). Apre http://127.0.0.1:8765.
5. `scripts/account_ev.py reports/oos_pnl_X.csv` : valore atteso e dimensione ottimale per ogni piano prop.
   Le regole in `rules/firms.json` sono NON VERIFICATE e le tariffe sono vuote: vanno compilate.

## Regole non negoziabili
- Dati out-of-sample bloccati: l'optimizer non li vede mai.
- Ogni variante testata incrementa `n_trials`; la soglia DSR sale di conseguenza.
- Il giudice usa il limite *inferiore* dell'intervallo di confidenza della prob. di passare.
- I preset prop in `prop.py` sono segnaposto: verificare con il regolamento attuale.
- Le chiavi API stanno nei secret dell'ambiente, mai nel repo.

## Test
    pip install -e .[dev] && pytest

## Tema e logo
- Colori: **un solo file**, `src/ofo/app/static/theme.css` (nero / grigi / bianco del brand). Cambi gli esadecimali e ricarichi la pagina:
  la scena 3D, i grafici e le candele li leggono da lì. I colori "di significato" (utile/perdita, verdetti) sono separati (`--up`, `--down`, `--warn`).
- Logo: salva il tuo file come `src/ofo/app/static/brand/icon.png`: compare in alto a sinistra e come icona della scheda.
- Conti (evaluation / funded): `data/accounts.json` (modello in `rules/accounts.example.json`). Senza quel file il Lab mostra 5 conti di ESEMPIO.

## Scout (ricerca autonoma) e indicatori
- **Scout**: cerca su arXiv (finanza quantitativa; Semantic Scholar come fonte secondaria), filtra, un agente Curatore sceglie i paper
  utili con UNA chiamata AI, scarica i PDF solo da `arxiv.org` in `ideas/web/` e li passa alla pipeline normale. Pulsante "Cerca ora"
  o modalità automatica (ogni 30 min - 6 h). Le proposte arrivano in "Strategie in attesa": nessun test parte da solo.
- **Indicatori**: nella scheda Trader, scegliendo una strategia, si vedono sul grafico gli indicatori che usa davvero (medie, canali, bande, range di apertura, VWAP).
- **Limite chiamate AI**: 30 al MINUTO. In `config/llm.json`: `calls_per_minute` (28 lascia un margine) e `role_calls_per_minute` per agente.
