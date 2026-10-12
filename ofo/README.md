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

## Autopilota (ciclo completamente autonomo)
All'avvio del Lab l'**Autopilota** è acceso (se l'AI è configurata): lo Scout cerca paper ogni 30 minuti (quando gli argomenti
finiscono, il modello ne inventa di nuovi), il Researcher li legge, le proposte vengono testate **senza chiedere il via**
(anche dopo un riavvio: `data/loop/specs_auto/`), il Giudice decide con regole deterministiche.
- **Limiti** (in `data/autopilot.json`, modificabili): 4000 prove al giorno, 60000 in totale. Servono: ogni prova in più
  rende il Giudice più severo (Sharpe deflazionato); senza tetto il ciclo si auto-sabota.
- **Strategie pronte**: solo quelle APPROVATE dal Giudice. Tab Strategie > "Strategie pronte" (e `data/loop/dossiers/*.json`):
  descrizione, dimensione consigliata (micro o mini), probabilità di passare con intervallo, stop giornaliero, drawdown.
  Le regole dei conti usate sono quelle di esempio (50k/25k) e NON sono verificate.
- Aspettati che quasi tutto venga respinto: finora nessuna famiglia di strategie ha superato il blocco nascosto.
- Gli scienziati restano alla loro scrivania: "al lavoro" = digitano veloci, "in attesa" = battito lento (non sono mai "spenti"
  se l'Autopilota è acceso). Lo stato "al lavoro" viene dal server (attività reale), non da timeout.

## Rischio / rendimento (stop e target)
Dopo aver scelto la migliore combinazione, l'Optimizer prova 20 coppie stop (1-3× ATR) × target (1R-3R o nessuno) sul solo
periodo di sviluppo e mostra la tabella nel dettaglio della strategia. La coppia migliore viene adottata solo se batte
l'uscita propria della strategia di almeno 0.03 di Sharpe giornaliero; il Giudice la valuta poi sul blocco nascosto.
Regole prudenti: se stop e target cadono nella stessa barra vale lo stop; i gap riempiono all'apertura. Le 20 celle contano
come prove nel registro. Il fascicolo della strategia pronta riporta stop, target e rischio per operazione in dollari.

## Deployment automatico (paper trading)
Quando il Giudice approva una strategia, l'Autopilota la **mette in linea da solo** (nessun clic): dal giorno dopo l'ultimo
dato disponibile applica le sue regole (con stop/target scelti) ai giorni nuovi, nella dimensione consigliata, e confronta
il risultato con quello atteso. Si vede in Strategie > "Deployment automatici".
- **Dati nuovi**: con `MASSIVE_S3_ACCESS_KEY` e `MASSIVE_S3_SECRET_KEY` nell'ambiente, ogni 6 ore il Lab scarica i giorni
  nuovi e ricostruisce `NQ_1min.csv.gz` da solo. Senza chiavi il test avanza solo quando aggiorni tu il file.
- **Regole automatiche**: RITIRATA se il drawdown paper arriva all'80% del massimo consentito o, dopo 40 giorni, i risultati
  sono statisticamente peggiori del blocco nascosto (z < -2.5); CONFERMATA dopo 60 giorni in profitto e coerenti.
- **Segnali**: ogni nuovo trade finisce in `data/signals/<strategia>.jsonl` (aggancio per un bot Discord o per il broker).
- **Nessun ordine reale parte dal computer.** Operare su un conto vero richiede un collegamento al broker (credenziali,
  limiti di rischio) che puoi autorizzare solo tu: non lo attivo di nascosto.

## Obsidian
Collega il tuo vault dal pannello "Obsidian" (o con la variabile `OFO_OBSIDIAN_VAULT`). Il Lab scrive note Markdown in
`<vault>/OFO/`: `Strategie/`, `Pronte/`, `Deployment/`, `Paper/`, `Diario/` e il cruscotto `OFO.md` (con una tabella
Dataview se hai il plugin). Le note sono collegate tra loro (paper → strategia → pronta → deployment), quindi la vista
Grafo mostra come è nata ogni strategia. Quello che scrivi **sotto la riga `<!-- ofo:end -->`** resta quando la nota viene
aggiornata. Le tue note in `OFO/Ideas/` le legge il Researcher come i PDF (testo non fidato: passa sempre dai controlli).
Il Lab non tocca mai file che non ha creato lui e non scrive fuori da `OFO/`.
