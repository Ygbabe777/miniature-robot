# Market Intelligence Engine — audit del repository e mappa delle integrazioni

Documento di Fase Zero. Fotografa lo stato **effettivo** del codice prima di costruire il Market Intelligence Engine (MIE); non descrive intenzioni.

## 1. Cosa esiste davvero

| Componente | Dove | Stato |
|---|---|---|
| Lab 3D voxel, Inspector, dipartimenti | `app/static/scene.js` (866 righe), `ui.js`, `lab.css` | esiste; 10 dipartimenti, eventi reali via `/api/events` |
| Researcher, Scout, Strategist, Planner, Auditor | `agents/`, `scout.py`, `papers.py`, `app/lab.py` | esistono; il Researcher propone `StrategySpec` da paper, brainstorm, ipotesi per famiglia, collegamenti tra concetti |
| Optimizer, Validator, Judge | `optimizer.py`, `judge.py`, `judges.py`, `evidence.py` | esistono; Judge deterministico a due teste; DSR coerente `dsr_holdout` attivo |
| Pipeline di ricerca | `optimizer.run_study` (5 stadi) | esiste; hypothesis→screening→validation→robustness→audit |
| Registro dei tentativi | `registry.py` (SQLite), `finalists.py`, `dedup.py` | esiste; trial counting, epoca congelata, finalisti immutabili, decisioni di dedup persistenti |
| Locked OOS | `optimizer.py`, `finalists.py` | esiste; blocco congelato, ≤ 3 finalisti, sguardo contato in `locked_looks` |
| Paper deployment | `deploy.py` | esiste; piano forward preregistrato, test sequenziale |
| Prop simulation / farming | `prop.py`, `lifecycle.py`, `farming.py`, `rulesets.py` | esiste |
| Obsidian | `obsidian.py` (339 righe), `knowledge.py` (214), `Lab._vault_notes/vault_brief/obsidian_sync` | esiste; **solo file system**, nessuna API |
| Negative Knowledge | `knowledge.dev_failed_gates`, `research_report.recurring_negatives` | **parziale**: contatori di gate falliti per concetto, nessuna nota dedicata, nessun archivio di risultati negativi con provenienza |
| Research Director | — | **non esiste** (nessun modulo). `Lab.pick_family` + allocatore di famiglie (`research_budget.rank`) ne fanno una parte |
| Candidate Pool / Tournament / Budget Controller | — | **non esistono** come componenti; `ResearchBudget` è l'unico controllo e i tetti giornalieri sono spenti |
| Embedding / RAG | — | **non esiste**. Il recupero è: grafo di concetti (`knowledge.build`) + `vault_brief` (note del proprietario) |
| Market Observer / Structure / Behavior | — | **non esistono**. Esistono solo i generatori di segnale dei template (`strategies.py`), non una descrizione del mercato |

Dati: 1-minuto OHLCV (`futures.py` costruisce il continuo con colonna `session`), tz-naive in ora di New York (le conversioni da UTC avvengono in `data.py`), una sessione = 18:00 ET → 17:00 ET. Il campione utente ≈ 2 anni di NQ. Delta/CVD/footprint/volume profile: **non disponibili**.

## 2. Obsidian: come funziona oggi

* **Scrittura** (`obsidian.sync`): cartella `<vault>/OFO/` con `Strategie`, `Pronte`, `Deployment`, `Paper`, `Concetti`, `Idee`, `Diario`, `Ideas` (input del proprietario), `OFO.md`, `Proposte AI.md`. Front matter YAML minimale (tag, verdetto, indicatori). Tutto sotto `<!-- ofo:end -->` è del proprietario e sopravvive alle riscritture. File non OFO mai toccati.
* **Lettura** (`read_notes`): solo `OFO/`, max 600 note, 200 kB ciascuna, titolo + link + testo del proprietario. Il testo è trattato come non fidato.
* **Recupero**: nessun indice, nessun punteggio. `Lab.vault_brief` mette nel prompt del Researcher le note del proprietario e le idee già archiviate; `knowledge.memory_text` mette fatti di sviluppo per concetto.
* **Collegamenti**: wikilink tra strategia, paper e concetti; `Idee/A x B.md` per le combinazioni.
* **Duplicati**: nomi file deterministici (`safe(name)`), riscrittura in place; deduplicazione delle *strategie* nel Lab (`dedup.py`), non delle note.
* **Provenienza**: debole. Nessun id stabile della nota, nessuna versione del dataset, nessuno stato dell'evidenza, nessuna distinzione tra osservazione, ipotesi, conclusione, risultato negativo, interpretazione LLM.

### Rischio di contaminazione già presente (da rendere esplicito)
Le note `Strategie/`, `Pronte/`, `Deployment/`, `Diario/` e le note `Concetto` contengono **verdetti e numeri del blocco hidden**. Oggi non arrivano al Researcher solo per una convenzione nel codice (`vault_brief` esclude le note generate; `knowledge.memory_text` usa solo fatti di sviluppo). È una protezione implicita e fragile: un nuovo retrieval che leggesse tutte le note la romperebbe. **Il MIE introduce etichette di accesso nel front matter, applicate nel codice prima di qualunque ranking**, con default `locked_derived` per le note legacy di quelle cartelle.

## 3. Decisioni di integrazione

1. **Nessun secondo vault.** Il MIE scrive nello stesso `OFO/` con una nuova sottocartella `Intelligence/` (sotto-cartelle: `Osservazioni`, `Strutture`, `Ipotesi`, `Missioni`, `Negativi`, `Domande`, `Report`), usando `obsidian.write_note` (marcatore di fine, annotazioni preservate). Senza vault configurato usa la stessa struttura in `data/memory/OFO/` (stesso formato, nessun secondo sistema): se il vault viene configurato dopo, la cartella è copiabile così com'è.
2. **Estensione, non sostituzione.** `obsidian.SUBDIRS` e `read_notes` vengono estesi; le note legacy non cambiano e ricevono un'etichetta di accesso derivata dalla cartella.
3. **Il MIE vede solo lo sviluppo.** Una vista (`DevView`) costruita dall'epoca congelata espone solo sessioni *prima* dell'inizio del blocco hidden; il codice del MIE non riceve mai l'intero DataFrame. Le sessioni *dopo* l'epoca sono "forward": servono solo a verificare previsioni già registrate, mai a generare ipotesi.
4. **Il Judge non cambia.** Il MIE produce `StrategySpec` che entrano nella pipeline esistente (`Lab._add_pending_all`, con deduplicazione). Nessuna nota può modificare soglie, protocollo o autorizzazioni.
5. **Retrieval lessicale** (BM25 su titolo, tag e corpo) con filtri di accesso applicati *prima* del ranking. Non ci sono embedding nel repository e non servono per queste dimensioni; se in futuro si aggiungono, il filtro resta a monte.
6. **Research Director**: non esisteva; viene introdotto come modulo minimo (`mie/director.py`) che genera missioni da lacune di conoscenza. Non sostituisce `pick_family`.
7. **Esplorazione contata.** Ogni studio di evento e ogni variante di definizione è registrato nel registro dei tentativi (kind `mie`), non come scoperta indipendente.
