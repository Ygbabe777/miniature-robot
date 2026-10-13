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
Dopo aver scelto la migliore combinazione, l'Optimizer prova 28 coppie stop (1-3× ATR) × target (0.5R-3R o nessuno; sotto 1R il target è più vicino dello stop: molte piccole vincite, rare perdite grandi) sul solo
periodo di sviluppo e mostra la tabella nel dettaglio della strategia. La coppia migliore viene adottata solo se batte
l'uscita propria della strategia di almeno 0.03 di Sharpe giornaliero; il Giudice la valuta poi sul blocco nascosto.
Regole prudenti: se stop e target cadono nella stessa barra vale lo stop; i gap riempiono all'apertura. Le 28 celle contano
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

### Rete di conoscenza (gli agenti imparano)
Per ogni paper il Researcher nomina 2-6 **concetti** (es. "opening range", "volatility regime"); si aggiungono l'argomento
con cui lo Scout l'ha trovato e la famiglia della strategia. Ogni concetto diventa una nota in `OFO/Concetti/` che collega
tutti i paper e le strategie su quell'idea, con cosa è stato imparato ("5 testate, nessuna approvata; falliscono quasi sempre
il controllo dsr"). Lo stesso riassunto torna ai **prompt del Researcher e del Planner**: non ripropongono idee già fallite
senza un meccanismo nuovo e preferiscono i concetti letti ma non ancora testati. Funziona anche senza Obsidian
(`data/knowledge.json`); Obsidian lo mostra come Grafo.

### Il Researcher legge la memoria Obsidian e collega le idee
Con il vault collegato, il Researcher legge le note di `OFO/` (non il resto del vault: quel testo va a un servizio AI),
**comprese le tue annotazioni** sotto `<!-- ofo:end -->` e le tue note in `OFO/Ideas/`. Quando la coda è vuota (dopo le
famiglie classiche) cerca **collegamenti tra concetti**: prima le coppie che hai collegato tu con `[[Concetto - ...]]`, poi
concetti mai testati o che hanno già prodotto una strategia approvata, mai ancora combinati. Per ogni coppia propone strategie
ibride (es. una come segnale, l'altra come filtro) che l'Autopilota testa, e scrive una nota in `OFO/Idee/` collegata ai due
concetti. Ogni coppia si prova una sola volta (`data/connections.json`), al massimo una ogni 15 minuti.

## Probabilità per conto, più conti, payout e costi (approssimati)
Il fascicolo di ogni strategia pronta contiene, per il conto 50k e per il 25k:
- la probabilità di passare **e di arrivare a un payout**, con costi medi per tentativo e valore atteso;
- la **tabella dei tentativi**: probabilità di passarne almeno uno con 1, 2 … 10 conti (uno dopo l'altro, a date diverse) e
  quanti ne servono per l'80% / 90% / 95%. Conti aperti insieme con gli stessi trade non sono indipendenti: lì non si moltiplica;
- il confronto **50k contro 25k** (payout più probabile, valore atteso).
Le commissioni e le regole di payout sono **ipotesi indicative**: sostituiscile con quelle vere in `rules/economics.json`.

## Obiettivo: passare la valutazione in una settimana
Il Researcher, il Planner e lo Strategist sanno che operano solo sui futures Nasdaq-100 (NQ/MNQ) e che l'obiettivo è passare
la valutazione in circa 5 giorni di borsa. Il fascicolo di ogni strategia approvata dice, per il 50k e il 25k: la dimensione
(micro/mini) che massimizza la probabilità di passare **entro 5 giorni** (con intervallo al 95%), il tempo mediano, il costo
atteso per ottenere un passaggio, quanti conti servono per l'80% / 90% se non passi al primo, e la dimensione ridotta da usare
sul funded dopo il passaggio. **"Va bene" solo se il limite inferiore dell'intervallo è ≥ 25%**; altrimenti il fascicolo dice
"approvata ma troppo lenta" e la strategia NON viene messa in paper trading. Giorni e soglia si cambiano in `rules/economics.json`
(`goal`). Attenzione: passare in 5 giorni richiede dimensioni aggressive, quindi il rischio di bruciare l'account in quella
settimana è alto: il numero è nel fascicolo.

## Più mercati: Nasdaq, S&P 500 e oro
I file giornalieri di Massive contengono **tutti** i ticker CME: non serve scaricare altro. Costruisci un file continuo per
ogni mercato (stessa cartella `data/`):
```
python scripts/add_markets.py
```
(costruisce ES e GC; equivale a `build_dataset.py data ES data/ES_1min.csv.gz` e `... GC ...`). MES e MGC non servono: hanno
lo stesso prezzo di ES e GC, il Lab usa le serie dei mini, più liquide, con il valore del tick del micro.
Al riavvio il Lab trova i file `ES_1min.csv.gz` e `GC_1min.csv.gz` accanto a quello principale e li aggiunge da solo
("Mercati attivi" nel terminale). Ogni strategia (da paper, brainstorm, collegamenti, famiglie classiche) viene messa in coda
**una volta per mercato** e testata con il suo contratto micro (MNQ, MES, MGC) e le stesse regole del Giudice; le strategie sugli
altri mercati si chiamano `nome [ES]` / `nome [GC]`. Le prove di tutti i mercati contano nel registro. I report, il fascicolo, il
deployment in paper e le note Obsidian riportano il mercato; la memoria degli agenti dice per ogni famiglia su quale indice è
stata testata e con che esito ("orb: NQ 3 testate/0 approvate, ES 3/1"), così i Researcher/Planner la usano. Nel tab Trader c'è il
selettore del mercato. Se i file giornalieri non contengono l'oro (COMEX), `build_dataset.py` dà "no outright GC contracts found".


## Affidabilità quantitativa (revisione esterna)
Cosa è stato verificato e cambiato, punto per punto:
1. **Contabilità del backtest.** Il motore vettoriale (`backtest.py` + `exits.py`) è confrontato barra per barra con un
   *execution ledger* indipendente (`ledger.py`: segnali, fill con prezzo/slippage/commissione, uscite intrabar). 300 scenari
   casuali, 0 discordanze. Prima di ogni verdetto lo studio riconcilia i due motori e verifica che la strategia sia causale
   (`AccountingError` altrimenti). Ingressi e uscite del grafico e del paper trading vengono dal ledger. In più, un test "fill
   all'apertura successiva" (`run_ledger_next_open`) e un test al doppio dei costi sono filtri dello studio.
2. **Isolamento del blocco finale.** L'ultimo 25% delle sessioni viene tagliato prima di tutto: sweep, superficie, uscite,
   walk-forward, qualità e controllo del trend ricevono solo barre di sviluppo. Il blocco finale compare solo, per l'unico
   candidato finale, nel look del Giudice. Un test sostituisce il blocco con prezzi casuali e verifica che ogni output di sviluppo
   sia identico. La memoria degli agenti contiene solo fatti di sviluppo, mai verdetti del blocco finale.
3. **Walk-forward annidato.** In ogni finestra l'addestramento sceglie parametri, timeframe e stop/target; la finestra
   successiva testa la configurazione congelata. Si calcola per le superfici già robuste (le altre vengono respinte comunque).
4. **Registro in SQLite** (`data/registry.db`, transazioni, id degli esperimenti, impronta del dataset, log degli accessi al blocco
   finale, deduplica strategia+dati). Il vecchio `registry.json` è importato una volta e resta intatto come copia.
5. **Prop firm intraday** (`prop_intraday.py`): drawdown static / EOD / intraday con P&L non realizzato e violazioni a posizione
   aperta, su equity a barre. Il simulatore giornaliero resta, etichettato `daily_simplified`. Il fascicolo riporta anche il
   confronto tra i due.
6. **Paper trading**: registro dei segnali a sola aggiunta con catena di hash (`data/signals/*.jsonl`); un aggiornamento dei
   dati non riscrive la storia (restatement / late_trade annotati, mai applicati).
7. **Robustezza**: robustezza dei parametri (vicini allo stesso timeframe) separata da quella tra timeframe; minimo 100
   operazioni; Sharpe positivo al doppio dei costi e con fill all'apertura successiva.

## Filtro dei mercati
Nel pannello Autopilota, "Mercati su cui testare": solo Nasdaq (NQ/MNQ) oppure anche S&P 500 (ES/MES) e oro (GC/MGC).
Le voci già in coda per un mercato disattivato restano ferme finché non lo riattivi.

## Budget dei token (dollari a settimana lavorativa)
`config/budget.json` (creato una volta, poi tuo: `update.py` non lo sovrascrive): `weekly_usd` 15, `remaining_usd` quanto ti resta
quando avvii il Lab, `chat_reserve_pct` 15, `prices` (opzionale, prezzo per milione di token in/out di ogni modello).
- **Tetto giornaliero** = saldo / giorni di lavoro rimasti (lun-ven, oggi compreso). Esempio: 12$ il martedì → 4 giorni → 3$ al
  giorno, di cui 15% riservato alla chat con lo Strategist.
- **Risparmio** a 60% del tetto: leggo 2 blocchi per paper invece di 6, ricerche 4 volte più rare, niente brainstorm né collegamenti.
- **Pausa** a 100%: Scout, Researcher, Planner, Auditor si fermano (i test deterministici no: non usano token). La chat resta fino al tetto
  totale. Nel weekend il lavoro autonomo è fermo. Sotto il 5% del budget settimanale si ferma tutto.
- **Dollari**: senza prezzi per modello il Lab usa un prezzo medio volutamente ALTO (3$ per milione di token) e **impara quello vero**
  ogni volta che scrivi il saldo dalla dashboard del provider (card "Budget AI": confronta i dollari scomparsi con i token contati).
  Il saldo del provider resta la verità: riscrivilo ogni tanto.
- Le chiamate in cache e quelle rifiutate non costano.

## Fonti (ponte con Agent Reach)
[Agent Reach](https://github.com/Panniantong/Agent-Reach) installa e controlla strumenti per agenti che eseguono comandi di shell.
Il Researcher di OFO non è un agente così (non esegue comandi: trasforma testo in JSON validato), e **non gli si dà accesso
libero a internet o alla shell**: un testo ostile letto sul web potrebbe fargli eseguire comandi o rubare chiavi. Il ponte sta nello
Scout (codice normale, `src/ofo/sources.py`): comandi in lista bianca, solo argomenti (niente shell), query ripulite, ambiente
minimo (le tue chiavi non arrivano mai al processo figlio), timeout, tetto sull'output, testo salvato come NON FIDATO.
- Attivi di default: **github** (progetti open source) e **rss** (blog quant, lista modificabile in `config/sources.json`).
- Opzionali (spenti): **exa** (ricerca web, `mcporter`), **youtube** (trascrizioni, `yt-dlp`, molti token), **reddit** e **twitter**
  (richiedono il TUO login tramite gli strumenti di Agent Reach; testo di bassa qualità; rispetta i termini dei siti).
- L'installazione di Agent Reach è facoltativa: `pipx install https://github.com/Panniantong/agent-reach/archive/main.zip` poi
  `agent-reach install --env=auto`. I comandi seguono la sua documentazione ma NON sono stati provati sugli strumenti veri.
- Focus futures/Nasdaq: 10 argomenti dedicati (NQ, MNQ, opening range, VWAP, gap, prop firm) vengono cercati prima di quelli generici
  e i risultati con parole come NQ/Nasdaq/futures salgono in cima.

## Pass rate, tentativi e giorni minimi (Judge / dossier / Trader)
- **Regola pass rate**: una strategia con pass rate della challenge ≥ 30% entro 5 sessioni (taglia migliore) è "da considerare", ≥ 35% "forte". Resta obbligatorio il Deflated Sharpe: un pass rate a 5 giorni da solo si può gonfiare aumentando la taglia (varianza).
- **Tentativi**: il dossier mostra la probabilità cumulata di passare almeno una evaluation al 1°, 2°, 3°… account, per 25k e per 50k, con costo atteso e costo se falliscono tutti.
- **Giorni minimi** (`timing.py`): per ogni strategia, per 25k e 50k, i giorni minimi/mediani/p10–p90 per passare, la probabilità entro 1/2/3/5/10 giorni e il minimo storico. In studio (in-campione) e sul blocco non visto dopo il Giudice. Gli studi vecchi non hanno `timing`: rilanciali.
- **Trader**: ingressi (triangolo al prezzo reale), uscite (cerchio con P&L e motivo stop/target/segnale), linee SL/TP, lista trade cliccabile con ◀ ▶, filtri e export CSV, multiplo R.

## Pannelli, notifiche e vignette
- I pannelli laterali si nascondono con le linguette ◀ ▶ (o i tasti `[` e `]`); lo stato è ricordato. Un pallino rosso con il conteggio avvisa di novità (verdetti, strategie avviate, download, budget) sulla linguetta e sulla scheda interessata.
- Sopra ogni agente una vignetta mostra l'ultimo pensiero/ricerca (💭 💡 ⚖ 🔎 📖 💾): luminosa se recente, attenuata dopo 30 s, sparisce dopo 5 min se l'agente è fermo. Il tasto 💬 le nasconde.

## Tutte le timeframe, salute delle fonti web, pensieri degli agenti
- L'Optimizer prova ogni studio su **tutte** le timeframe (5/15/30/60 min) anche se la proposta ne indicava meno; ogni timeframe conta come tentativi nel Deflated Sharpe. Il dettaglio mostra la tabella "Prova su tutte le timeframe" (miglior versione per timeframe, Sharpe, P&L, pass rate 25k/50k) e la versione finale è quella sulla timeframe migliore (`report["by_timeframe"]`). La 1 min e la giornaliera non sono incluse: i parametri in barre non sono confrontabili e sulla 1 min i costi dominano.
- Il pannello "Internet in tempo reale" mostra per ogni fonte ✔ (con n. risultati) / ✘ (errore) / non provato, le fonti attivate ma non installate sul PC (con il comando per installarle) e se Agent Reach è installato. github, rss, arXiv, SSRN usano HTTP semplice: Agent Reach serve solo per exa/youtube/reddit/x.
- Le vignette degli agenti mostrano su cosa stanno ragionando ("thinking: …") e il risultato.

## Timeframe scelta con un punteggio, taglia consigliata, contratti per trade
- La timeframe migliore non si sceglie più solo col profitto: punteggio = probabilità di passare 25k/50k entro 5 giorni (30%), profitto/max drawdown (20%), giorni vincenti (20%), % di sessioni con almeno un trade (15%), win rate dei trade (10%), velocità di passaggio (5%). Una timeframe in perdita non viene scelta se un'altra guadagna. Solo dati di sviluppo. La scelta precede il test di robustezza (plateau calcolato sul candidato scelto).
- `sizing.py`: tabella per 25k e 50k con P&L, max drawdown, profitto/drawdown, quota di drawdown consentito usata, giorno peggiore, pass/bust entro 5 giorni. Il rapporto profitto/drawdown è identico a ogni taglia (scala linearmente): la taglia consigliata è la più grande il cui drawdown storico usa al massimo il 60% del consentito (giorno peggiore ≤ 40%).
- Trader: ogni ingresso mostra i contratti (×N) e un selettore passa dalla taglia simulata alla taglia consigliata 25k/50k, riscalando i P&L; il CSV include i contratti.

## SSRN: testo completo
SSRN non offre un'API e i suoi PDF richiedono login e accettazione dei termini: scaricarli in automatico con un robot li aggira e viola le regole del sito, quindi OFO non lo fa. Per ogni articolo SSRN che sceglie, lo Scout cerca invece una **copia gratuita legale** dello stesso lavoro (Semantic Scholar `openAccessPdf`, e Unpaywall se imposti `OFO_UNPAYWALL_EMAIL` con la TUA email: non viene mai presa da altrove) e la scarica da qualsiasi indirizzo https pubblico (niente IP/localhost, solo PDF veri, tetto di dimensione). Se non esiste, salva l'abstract e mette l'articolo nell'elenco "da scaricare a mano": scaricalo con il tuo account SSRN e mettilo in `ideas/` — l'autopilota lo legge da solo (anche senza Obsidian).

## Diagnosi delle fonti web
`python scripts/check_sources.py` prova ogni canale da questo PC e stampa l'errore esatto (nessuna chiave nell'output): controlla `config/sources.json` (un `True` maiuscolo o una virgola in più lo rende JSON non valido: ora lo segnala invece di bloccarsi), quali programmi sono installati (`yt-dlp`, `mcporter`...) e una ricerca di prova per canale. Il pannello "Internet in tempo reale" ha il tasto ⤢ per una vista grande.

## Parla al Researcher, fonti selezionabili, PDF richiesti, long-only / short-only
- **Parla al Researcher** (pannello sinistro): scrivi "cerca …" (una volta), "continua …" (a ogni ricerca), "smetti …" (non cercarlo più e scarta gli articoli che ne parlano) o una nota libera. Scout e filtri sono deterministici; le istruzioni finiscono anche nel prompt del Researcher, ma non cambiano mai cosa accetta il Giudice.
- **Fonti**: nel pannello "Internet in tempo reale" spunti arXiv, Semantic Scholar, SSRN, GitHub, RSS, Exa, YouTube, Reddit, X. Quelle senza lo strumento installato sono grigie col comando per installarlo. La scelta è salvata. Un errore di una fonte non ferma più le altre.
- **PDF richiesti**: quando il Researcher ha bisogno di un PDF (SSRN senza copia gratuita) compare una notifica (avviso + pallino rosso) con il link e il tasto "Carica PDF": il file viene associato all'articolo e letto.
- **Direzione**: per ogni timeframe l'Optimizer prova anche solo-long e solo-short. Una versione a un lato sostituisce quella a due lati solo se supera il punteggio di almeno 0,02 E il lato scartato perde soldi nel complesso e in almeno 2 fasi su 3 dei giorni di sviluppo. Le varianti extra contano come tentativi nel Deflated Sharpe. La direzione è applicata ovunque (Trader, dossier, deployment paper).

## OFO come PROP FARMING STRATEGY FACTORY
Domanda centrale: *quale strategia ha la migliore evidenza di trasformare il costo di acquisto di un account in payout netti sostenibili, senza overfitting, rischio eccessivo o esecuzione irrealistica?*
- **Ruleset versionati** (`rules/rulesets/*.json`, mai sovrascritti da `update.py`): ogni prop è configurata a parte e dichiara `VERIFIED_RULESET`, `USER_SUPPLIED_RULESET` o `ASSUMED_RULESET`. Quelli forniti sono ASSUNTI (segnaposto): verificali e cambia `provenance`. Ogni report mostra nome e versione del ruleset e un avviso se non è verificato.
- **Ciclo di vita dell'account** (`lifecycle.py`): EVALUATION_ACTIVE → … → PAYOUT_RECEIVED / FUNDED_FAILED / ACCOUNT_CLOSED, con drawdown static / EOD / intraday, daily loss limit, consistenza, giorni vincenti richiesti, minimo/cap del payout, profit split, buffer, ritardo, costi (valutazione, attivazione, reset). Un ruleset senza fase funded finisce all'evaluation. Dentro la sessione l'ordine degli estremi è sconosciuto: ipotesi conservativa (prima il favorevole, poi l'avverso); la granularità delle barre è riportata come limite.
- **NPFV** = payout netti incassati − costi dei conti, su orizzonte configurabile (`rules/farming.json`). **LCB95** da bootstrap a due livelli: replica esterna dei DATI (incertezza dei parametri) + Monte Carlo interno (solo errore di campionamento, `mc_se`). Il Monte Carlo usa blocchi di sessioni (preserva il clustering delle perdite), non trade i.i.d.
- **Gate gerarchici** 1-7 (integrità, edge/DSR, fuori campione, robustezza con stress dei costi, conformità intraday, economia + FIRST PAYOUT GATE, forward paper). Un gate critico fallito non è compensabile; campione insufficiente = INSUFFICIENT_EVIDENCE; si possono approvare ZERO strategie. Probabilità del primo payout dalla simulazione completa del ciclo, non da probabilità marginali moltiplicate.
- **Size e politica di payout** scelte sui giorni di sviluppo; economia, stress e conformità misurati una volta sul blocco mai visto. Politiche A (massimo), B (parziale), C (dinamica) confrontate sul valore netto.
- **Portafoglio multi-account**: N account che copiano gli stessi trade falliscono insieme (esiti correlati); mostrato anche il falso risultato "indipendente". Avvisi se il ruleset vieta copy trading.
- **Classifica per ruleset** per NPFV_LCB95, con strategie dichiarate statisticamente indistinguibili quando gli intervalli si sovrappongono.
- **Revisione delle strategie già giudicate**: quelle che il Giudice ha già visto vengono rivalutate con questo criterio (pulsante "Rivedi ora" o automatica in background). I numeri fuori campione non cambiano: il vecchio verdetto resta in `legacy_status` e la revisione è registrata come un ulteriore sguardo al blocco nascosto.
- **Fascicoli sulla scrivania del Trader**: ogni strategia approvata ha un fascicolo (cartella 3D cliccabile) con nome, logica d'ingresso, risultati, barriere, perché, scenari avversi, cosa potrebbe invalidarla, verbale della discussione degli agenti (Risk Manager, Strategist, Auditor, Planner, Judge: testi ricavati dai numeri realmente calcolati) e la tua decisione: **LIVE** (la esegui tu: OFO non invia mai ordini), in attesa o scartata.
- Timeframe e direzione sono scelte per NPFV medio di sviluppo (vita completa dell'account), non più con una media pesata di rapporti.
