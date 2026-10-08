# OFO COUNCIL — NQ Pre-Market Intelligence

Consiglio multi-agente che analizza i futures **NQ** prima della sessione cash USA e produce un report
strutturato, verificabile e riproducibile.

> **ANALISI AI — NESSUN ORDINE AUTOMATICO.** Il sistema fa **solo analisi**. Non piazza, modifica né cancella
> ordini e non contiene alcuna integrazione broker. Ogni decisione di trading è **umana**.

*(Il repository contiene anche, separati, `agx_dealer_mechanics.pine` e il sito `index.html`/`css`/`js`: non fanno parte di OFO Council.)*

## Indice
1. [Architettura](#architettura) · 2. [Installazione](#installazione) · 3. [Chiave API (.env)](#chiave-api-env) ·
4. [Formato dati](#formato-dati) · 5. [Uso](#uso) · 6. [Modalità mock](#modalità-mock) · 7. [Streamlit](#interfacce-streamlit) ·
8. [Report e transcript](#report-transcript-e-journal) · 9. [Test](#test) · 10. [Sicurezza](#sicurezza) ·
11. [Troubleshooting](#troubleshooting) · 12. [Limiti](#limiti)

## Architettura

```
 levels.json + ohlcv.csv
        │  validazione (no look-ahead: solo barre < 09:30 del giorno)
        ▼
 FEATURE DETERMINISTICHE  (VWAP, ATR, Volume Profile, sessione prec., overnight, regime + motivazioni)
        │  registro evidenze con ID stabili E001…
        ├──────────────┐
        ▼              ▼
  💙 PRICE ACTION   💜 OPTIONS FLOW      (round 1, in parallelo in modalità reale)
        └──────┬───────┘
               ▼
          💚 STRATEGIST      max 2 scenari condizionali (SE → ALLORA → INVALIDAZIONE) oppure NO TRADE
               ▼
          ❤️ RISK MANAGER    veto non scavalcabile; verdetto = il più severo tra LLM e motore deterministico
               ▼
          ⚪ JUDGE           ancoraggio alle evidenze, punteggio 0–100 (non crea fatti)
               ▼
   DECISIONE GERARCHICA: qualità dati → evidenza → convergenza PA+OF → strategist → veto risk → judge
               ▼
   report .md/.json · transcript · journal · eventi → UI
```

* **Gli LLM interpretano dati strutturati; non calcolano metriche.** VWAP, ATR, POC/VAH/VAL, HVN/LVN, regime, R:R
  sono calcolati in Python (`src/features.py`, `src/risk_engine.py`).
* **Output JSON strict** (Pydantic, `src/schemas.py`): estrazione del JSON → un solo retry di correzione → altrimenti
  l'agente è `FAILED`. Nessuna risposta viene mai inventata.
* **Anti-allucinazione**: ogni fatto/livello/scenario cita `evidence_ids`. `src/grounding.py` rileva
  `unsupported_claim`, `fabricated_level`, `missing_data`, `contradictory_claim`, `arithmetic_error`; i problemi
  critici respingono lo scenario, i maggiori lo declassano.
* **Degrado controllato**: se un agente fallisce l'esito finale è `REVIEW_REQUIRED` (mai un finto successo);
  Risk Manager non disponibile ⇒ tutti gli scenari `NOT_APPROVED`; `DATA_QUALITY=RED` ⇒ `DATA_ERROR`, nessuna approvazione.
* **Stati finali**: `APPROVED_SETUP`, `APPROVED_WITH_CAUTION`, `NO_TRADE`, `REVIEW_REQUIRED`, `DATA_ERROR`.
  `NO_TRADE` è un esito valido, non un errore.
* **Event-driven**: il motore emette eventi (`src/event_bus.py`, `src/council_events.py`) persistiti nel transcript;
  `src/ui_state.py` li riduce in frame deterministici usati da entrambe le UI.

Mappa dei moduli: `config`, `data_loader`, `features`, `schemas`, `agents` (client OmniRoute, retry/fallback, runner),
`mock_llm`, `risk_engine`, `grounding`, `council_decision`, `debate_engine`, `pipeline`, `report_generator`, `journal`,
`evaluator`, `scoreboard`, `security`, `logging_utils`, `clock`, `event_bus`, `council_events`, `ui_state`, `room_view`.

## Installazione

Python ≥ 3.11.

```powershell
# Windows
cd E:\ofo-council
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
```
```bash
# Linux / macOS
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

Dipendenze minime: pandas, numpy, pydantic, python-dotenv, PyYAML, requests, streamlit, pytest.

## Chiave API (.env)

L'**unico** posto ammesso per la chiave è `.env` nella radice del progetto:

```
OMNIROUTE_API_KEY=la-tua-chiave
```

Vedi `.env.example`. `.env` è in `.gitignore`. La chiave non viene mai stampata, loggata, salvata in report/transcript,
mostrata in Streamlit o inclusa in eccezioni (redazione automatica `[REDACTED]`, `src/security.py`).
Endpoint, timeout, retry e modelli stanno in `config.yaml` (nessun segreto).

## Formato dati

`data/levels.json` (campi extra ammessi; `0`/mancante = non disponibile, mai interpretato come prezzo):

```json
{
  "date": "2026-10-08", "instrument": "NQ", "spot": 28964.5,
  "options": {"gamma_flip": 28900, "call_wall": 29250, "put_wall": 28800, "notes": ""},
  "levels": {"previous_session_high": 28916, "previous_session_low": 28777,
             "overnight_high": 28980.5, "overnight_low": 28819,
             "previous_week_high": 28918.5, "previous_week_low": 28517.75}
}
```

`data/ohlcv.csv`: `timestamp,open,high,low,close,volume`, barre **5 minuti**, orario exchange (America/New_York,
timestamp *naive*), ordine cronologico, almeno **3 sessioni RTH complete** (09:30–16:00) prima della data, più le barre
overnight fino alle 09:25. Validazioni: timestamp, duplicati, valori mancanti, coerenza OHLC, volumi negativi, ordine,
timeframe, copertura storica. I dati non validi producono un errore esplicito (qualità `RED`); nulla viene riparato in silenzio.
Le barre dalle 09:30 del giorno in poi sono ignorate (no look-ahead).

`DATA_QUALITY`: **GREEN** dati completi · **YELLOW** mancano dati opzionali / ci sono avvisi · **RED** dati critici mancanti o non validi.

> I file in `data/` forniti con il repository sono **DATI SINTETICI DI ESEMPIO** (`"source": "SYNTHETIC_SAMPLE"`), generati da
> `python tools/sample_data.py`. Report e UI li segnalano con un avviso. Sostituiscili con i tuoi dati reali ogni giorno.

## Uso

```powershell
.\venv\Scripts\python run.py --date 2026-10-08            # modalità reale (OmniRoute)
.\venv\Scripts\python run.py --date 2026-10-08 --mock     # LLM simulato, nessuna rete
.\venv\Scripts\python run.py --date 2026-10-08 --mock --mock-scenario risk_veto --verbose
.\venv\Scripts\python scoreboard.py
.\venv\Scripts\python evaluate.py
```

Output: `[1/6] Data validation … [6/6] Report generation`, poi `RUN COMPLETE` e i percorsi degli artefatti.
Codici di uscita: `0` ok · `2` configurazione (es. chiave mancante in modalità reale) · `3` `DATA_ERROR` (artefatti comunque generati).
`--no-ui` omette il suggerimento di avvio UI (il CLI non avvia mai la UI da solo).

Workflow giornaliero: aggiorna `data/levels.json` e `data/ohlcv.csv` → `run.py --date …` → leggi il report →
registra **la tua** decisione (UI o `python evaluate.py --human DATA TAKE|SKIP|MODIFY|WATCH|NO_TRADE`) → a fine giornata
`python evaluate.py --set-result DATA WIN --pnl-r 1.8`.

## Modalità mock

`--mock` usa un LLM simulato **deterministico** (`src/mock_llm.py`): regole che leggono il payload e producono JSON valido
(o difettoso). Non è un modello: ogni artefatto è marcato `MOCK`. Scenari (`--mock-scenario`): `ok`, `malformed_json_*`,
`invalid_schema_fails`, `timeout_recovers`, `rate_limited`, `server_error_fallback`, `*_down` (ogni agente), `strategist_disagreement`,
`risk_veto`, `risk_veto_partial`, `judge_rejection`, `hallucinated_level`, `unsupported_evidence`, `wrong_rr`.
La mancanza di dati opzioni si simula semplicemente togliendoli da `levels.json` (l'agente risponde `INSUFFICIENT_DATA`).

## Interfacce Streamlit

```powershell
.\venv\Scripts\streamlit run app.py          # interfaccia standard a schede
.\venv\Scripts\streamlit run app_pixel.py    # sala operativa pixel-art
```

**app_pixel.py** è una *visualizzazione* del Consiglio, non una simulazione separata: gli avatar si muovono, parlano e
reagiscono **solo** in risposta agli eventi reali persistiti nel transcript (`AGENT_STARTED`, `AGENT_MESSAGE`,
`AGENT_CHALLENGE`, `RISK_VETO`, `JUDGE_STARTED`, `FINAL_DECISION`…). Ogni fumetto è testo reale di un agente o un evento di
sistema; il ticker mostra i dati statici pre-market (`DATA SOURCE: STATIC / PRE-MARKET`, nessun prezzo live simulato).
Funzioni: timeline cliccabile, ispezione agente, **LIVE TRANSCRIPT** espandibile (evidenze, confidenza, modello, tempi;
mai chiavi/header/prompt), **PAUSA / NEXT EVENT** passo-passo, velocità 0.5×–4× (cambia solo la riproduzione),
stato finale (`APPROVED SETUP` / `NO TRADE` / `HUMAN REVIEW REQUIRED` / `DATA ERROR`) e pulsanti
**TAKE SETUP / SKIP / MODIFY / WATCH / NO TRADE** che scrivono nel journal separatamente dall'output AI.
La UI resta utilizzabile se un agente fallisce (l'avatar passa a `FAILED`).

## Report, transcript e journal

| File | Contenuto |
|---|---|
| `reports/YYYY-MM-DD.md` | report leggibile: snapshot, 5 agenti, decisione, **PANIC-PROOF**, decisione umana (`PENDING`) |
| `reports/YYYY-MM-DD.json` | stesso contenuto strutturato + metriche per fase (`duration_ms`, `status`, `error`, `output_validation`) |
| `transcripts/YYYY-MM-DD.json` | chiamate (modello richiesto/effettivo, fallback, retry, hash input, validazione, errori, evidenze) ed eventi per ricostruire la decisione e l'animazione |
| `journal.csv` | una riga per giorno; `human_decision` = `PENDING` finché non decide l'umano |

Riproducibilità: ogni run ha un `run_id` (`2026-10-08_14-32-51_a8f31`) e salva hash dei dati e della config, versione dei prompt,
modelli e timestamp. In mock, a parità di orologio e run_id, il report è identico byte per byte.

## Test

```powershell
.\venv\Scripts\python -m pytest                       # suite completa (unit + integrazione + UI headless)
.\venv\Scripts\python test_full_pipeline.py --mock    # integrazione in mock
.\venv\Scripts\python test_full_pipeline.py           # integrazione reale (richiede chiave + OmniRoute)
```
I test usano progetti temporanei: non toccano `reports/`, `journal.csv` né i tuoi dati.

## Sicurezza

* Nessun codice di esecuzione ordini: un test automatico (`tests/test_security.py`) vieta funzioni/librerie broker.
* Segreti solo in `.env`; redazione su log, eventi, report, transcript e HTML della UI; test dedicati.
* `.gitignore`: `.env`, `__pycache__/`, `.venv/`, `venv/`, `transcripts/`, `reports/`, `logs/`, `journal.csv`.
* La UI pixel inserisce i dati come JSON (`textContent`, mai `innerHTML`) con escape di `</script>`.

## Troubleshooting

| Sintomo | Causa / rimedio |
|---|---|
| `OMNIROUTE_API_KEY non trovata` (exit 2) | crea `.env` (vedi `.env.example`) o usa `--mock` |
| Agente `FAILED`, esito `REVIEW_REQUIRED` | OmniRoute non raggiungibile / JSON non valido: vedi `errors` e `failure_reason` nel transcript; verifica `base_url` in `config.yaml` |
| `DATA_ERROR` | leggi gli errori in cima al report: `levels.json` datato diversamente da `--date`, spot mancante, <3 sessioni, OHLCV incoerente |
| `NO_TRADE` | esito valido: convergenza insufficiente, R:R < 1.5, target/invalidazione non definibili o opzioni mancanti |
| `streamlit` non trovato | usa `.\venv\Scripts\streamlit` oppure `python -m streamlit run app_pixel.py` |

## Limiti

* Nessun dato opzioni/GEX viene scaricato: `levels.json` è compilato dall'utente; il "proxy regime gamma" è solo spot vs gamma flip.
* Orari RTH configurabili ma pensati per NQ (America/New_York); festività/mezze sessioni sono segnalate come sessioni incomplete.
* Il volume profile usa distribuzione uniforme del volume su ogni barra 5m (approssimazione); HVN/LVN sono euristiche configurabili.
* Le metriche di scoreboard/valutazione richiedono esiti registrati a mano nel journal e sono deboli con pochi campioni.
* Le risposte reali degli LLM non sono deterministiche; solo i calcoli e la modalità mock lo sono.
* Questo non è un motore di previsione né consulenza finanziaria: cerca il miglior setup **condizionale** supportato dai dati, oppure conclude che non ce n'è.
