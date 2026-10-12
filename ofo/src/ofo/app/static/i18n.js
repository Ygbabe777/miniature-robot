/* Italian / English. Switching reloads the page (the preference is remembered). */
(function () {
  const D = {
    it: {
      feed_title: 'Diario del Lab', papers_title: 'Studi e PDF', drop: 'Trascina qui i PDF dei research paper', drop_sub: 'oppure mettili nella cartella',
      ingest: 'Fai leggere i nuovi file al Researcher', tab_chat: 'Chat', tab_strat: 'Strategie', tab_trader: 'Trader', tip_wide: 'Allarga / riduci il pannello',
      send: 'Invia', chat_ph: 'Es.: creami una strategia ORB con i retest', close: '✕ Chiudi',
      chip_trials: 'Tentativi', chip_looks: 'Blocco guardato', chip_calls: 'Chiamate AI/h',
      tip_trials: 'Ogni combinazione testata alza la soglia per le prossime approvazioni', tip_looks: 'Quante volte il Judge ha guardato il blocco di dati nascosto', tip_calls: "Chiamate AI nell'ultima ora",
      ai_on: 'attiva', ai_off: 'spenta',
      greeting: 'Ciao! Sono lo Strategist. Dimmi che strategia vuoi (es. "ORB con i retest") e la preparo al volo. Poi decidi tu se avviare il test: non parte mai da solo.',
      sugg1: "Creami una strategia basata sull'ORB con i retest", sugg2: 'Reversione al VWAP con soglie diverse', sugg3: "Cosa fa l'orb_retest?", sugg4: 'Quali modelli di strategia esistono?',
      thinking: '…', no_reply: 'Nessuna risposta',
      c_model: 'modello', c_combos: 'combinazioni', c_tf: 'timeframe', c_params: 'Parametri e motivazione', c_values: 'valori', c_run: 'Avvia il test ({n} tentativi)', c_started: 'avviato…',
      c_warn: 'Ogni combinazione testata viene contata e alza la soglia per le prossime approvazioni (ora: {n}).',
      req_title: 'Manca un modello: {n}', req_saved: 'salvato tra le richieste', req_warn: 'Questo modello non esiste ancora: va implementato nel codice. Chiedilo a Claude e poi aggiorna il programma.',
      pending_title: 'Strategie in attesa del tuo via', origin: 'origine', o_chat: 'chat', o_paper: 'research paper', o_planner: 'Planner',
      studies_title: 'Strategie testate', none_yet: 'Nessuna ancora. Chiedi una strategia nella chat.', robust: 'superficie robusta', fragile: 'superficie fragile', combos: 'combinazioni', med_sharpe: 'Sharpe mediano',
      job_run: 'Test in corso: {n} ({c} combinazioni)', job_err: 'Errore in {n}: {e}', reqs_title: 'Modelli richiesti (da implementare)', no_files: 'nessun file', read_ok: '✔ letto',
      uploaded: 'Caricato: {n}', up_err: 'Errore: {e}', reading: 'Il Researcher sta leggendo i nuovi file…',
      v_APPROVED: 'APPROVATA', v_REJECTED: 'RESPINTA', v_INSUFFICIENT_EVIDENCE: 'PROVE INSUFFICIENTI',
      k_enough_days: 'Giorni sufficienti', k_dsr: 'Sharpe deflazionato (DSR) sufficiente', k_pass_prob_ci_lower: 'Probabilità di passare (limite inferiore) sufficiente', k_bust_risk: 'Rischio di bust accettabile',
      w_plateau: 'stabilità dei parametri (plateau)', w_median_sharpe: 'Sharpe mediano positivo', w_frac_positive: 'almeno il 55% di combinazioni in profitto', w_neighbours_positive: 'vicini del migliore in profitto', w_walk_forward_pnl: 'walk-forward in profitto',
      r_fragile: 'Senza il trend del mercato la superficie dei parametri non regge ({l}). Il blocco nascosto non è stato toccato.', r_failed: 'Non ha superato i controlli: {l}. Il blocco nascosto non è stato toccato.',
      pl_gate: 'Fermata dai controlli matematici (superficie, walk-forward o trend): nessuna chiamata AI necessaria.', pl_abandon: 'abbandona', pl_extend: 'estende', pl_new: 'nuova ipotesi',
      d_model: 'modello', d_tested: 'combinazioni testate', d_judge: 'Il Judge (calcolo, non AI)', d_pass: 'Probabilità di passare la valutazione', d_interval: 'intervallo', d_bust: 'rischio di bust', d_pnl_block: 'P&L sul blocco',
      kp_med: 'Sharpe mediano (tutte le combinazioni)', kp_pos: 'Combinazioni positive', kp_plat: 'Plateau (vicini del migliore)', kp_rob: 'Superficie robusta', kp_wf: 'Walk-forward P&L', yes: 'sì', no: 'no',
      d_best: 'Migliore combinazione in sviluppo: {p} (attenzione: è il massimo di {n}, quindi ottimistico)',
      s_wf: 'Walk-forward (fuori campione)', s_locked: 'Blocco nascosto', s_dev: 'In-campione (ottimizzato)',
      m_total: 'P&L totale', m_sharpe: 'Sharpe (annualizzato)', m_sortino: 'Sortino', m_pf: 'Profit factor', m_payoff: 'Payoff (media vinc./perd.)', m_win: '% giorni vincenti', m_dd: 'Max drawdown', m_calmar: 'Calmar', m_bw: 'Giorno migliore / peggiore', m_days: 'Giorni',
      mc_med: 'Monte Carlo: mediana finale', mc_rng: 'MC: 5° – 95° percentile', mc_loss: 'MC: probabilità di perdita',
      ch_eq: 'P&L cumulato', ch_mc: 'Monte Carlo: 2.000 futuri possibili (120 giorni)', ch_dd: 'Drawdown', ch_hist: 'Distribuzione del P&L giornaliero', ch_roll: 'Sharpe mobile',
      ch_wf: 'Walk-forward (fuori campione)', ch_lk: 'Blocco nascosto (visto dal Judge)', ch_de: 'In-campione, ottimizzato (NON affidabile)', ch_days: 'Giorni', ch_rs: 'Sharpe mobile (40 giorni, annualizzato)', ch_med: 'Mediana',
      warn_dev: 'Questa curva è IN-CAMPIONE: è la migliore di tutte le combinazioni sugli stessi dati usati per sceglierla. Sembra sempre bella e non prova nulla. Giudica dal walk-forward e dal blocco nascosto.',
      warn_lk: 'Blocco nascosto: dati che nessuna ottimizzazione ha mai visto. Il Judge lo ha già guardato per questa strategia.',
      aud_title: "Commento dell'Auditor (AI)", aud_next: 'Prossimo passo', planner: 'Planner',
      tr_wait: ['OFO TRADING DESK', 'in attesa di', 'strategie approvate'], tr_desk: 'TRADING DESK', tr_ready: 'pronte', tr_head: 'APPROVATE', tr_none: 'nessuna',
      t_all: 'TUTTO', t_note: 'Dati: il tuo file storico {s} (orari di New York). Rotella per lo zoom, trascina per spostarti. Qui non c\'è ancora trading automatico.',
      t_loading: 'Carico le candele…', t_coarser: 'Troppe candele: uso il timeframe {tf}', t_sessions: '{n} sessioni · {a} → {b}', t_err: 'Dati non disponibili: {e}',
      t_vol: 'Vol',
      agents_title: 'Agenti al lavoro', st_idle: 'spento', st_working: 'al lavoro', st_done: 'finito',
      t_strat: 'Strategia sul grafico', t_none: '— nessuna —', t_trades: '{n} trade · {w} vincenti · P&L {p}', t_hidden: '{n} trade nel blocco nascosto non mostrati',
      t_notrades: 'Nessun trade da mostrare', t_legend: 'Frecce = ingressi (↑ long, ↓ short) · cerchi = uscite (valore = P&L del trade). Sono i trade della migliore combinazione (in-campione): non provano un profitto.',
      acct_title: 'Conti', acct_demo_note: 'DATI DI ESEMPIO. Per i tuoi conti: copia rules/accounts.example.json in data/accounts.json e modificalo.', acct_eval: 'EVAL', acct_funded: 'FUNDED', acct_demo: 'DEMO', acct_sign: ['CONTI', 'DATI ESEMPIO'], acct_sign_real: ['CONTI'],
      as_ok: 'in corso', as_danger: 'vicino al limite', as_passed: 'superato', as_busted: 'saltato', acct_payouts: 'payout: {n}', acct_prog: 'target {p}', acct_buf: 'margine {p}'
    },
    en: {
      feed_title: 'Lab log', papers_title: 'Studies & PDFs', drop: 'Drop research paper PDFs here', drop_sub: 'or put them in the folder',
      ingest: 'Let the Researcher read new files', tab_chat: 'Chat', tab_strat: 'Strategies', tab_trader: 'Trader', tip_wide: 'Widen / narrow the panel',
      send: 'Send', chat_ph: 'E.g.: build me an ORB strategy with retests', close: '✕ Close',
      chip_trials: 'Trials', chip_looks: 'Locked looks', chip_calls: 'AI calls/h',
      tip_trials: 'Every combination tested raises the bar for future approvals', tip_looks: 'How many times the Judge has looked at the hidden data block', tip_calls: 'AI calls in the last hour',
      ai_on: 'on', ai_off: 'off',
      greeting: 'Hi! I am the Strategist. Tell me what strategy you want (e.g. "ORB with retests") and I will prepare it on the spot. You decide whether to run the test: it never starts by itself.',
      sugg1: 'Build me a strategy based on the ORB with retests', sugg2: 'VWAP reversion with different thresholds', sugg3: 'What does orb_retest do?', sugg4: 'Which strategy models exist?',
      thinking: '…', no_reply: 'No reply',
      c_model: 'model', c_combos: 'combinations', c_tf: 'timeframe', c_params: 'Parameters and rationale', c_values: 'values', c_run: 'Run the test ({n} trials)', c_started: 'started…',
      c_warn: 'Every combination tested is counted and raises the bar for future approvals (now: {n}).',
      req_title: 'Missing model: {n}', req_saved: 'saved to the requests', req_warn: 'This model does not exist yet: it has to be implemented in the code. Ask Claude, then update the program.',
      pending_title: 'Strategies waiting for your go-ahead', origin: 'origin', o_chat: 'chat', o_paper: 'research paper', o_planner: 'Planner',
      studies_title: 'Tested strategies', none_yet: 'None yet. Ask for a strategy in the chat.', robust: 'robust surface', fragile: 'fragile surface', combos: 'combinations', med_sharpe: 'median Sharpe',
      job_run: 'Running test: {n} ({c} combinations)', job_err: 'Error in {n}: {e}', reqs_title: 'Requested models (to implement)', no_files: 'no files', read_ok: '✔ read',
      uploaded: 'Uploaded: {n}', up_err: 'Error: {e}', reading: 'The Researcher is reading the new files…',
      v_APPROVED: 'APPROVED', v_REJECTED: 'REJECTED', v_INSUFFICIENT_EVIDENCE: 'NOT ENOUGH EVIDENCE',
      k_enough_days: 'Enough days', k_dsr: 'Deflated Sharpe (DSR) high enough', k_pass_prob_ci_lower: 'Pass probability (lower bound) high enough', k_bust_risk: 'Acceptable bust risk',
      w_plateau: 'parameter stability (plateau)', w_median_sharpe: 'positive median Sharpe', w_frac_positive: 'at least 55% of combinations profitable', w_neighbours_positive: 'neighbours of the best are profitable', w_walk_forward_pnl: 'profitable walk-forward',
      r_fragile: 'Without the market trend the parameter surface does not hold up ({l}). The hidden block was not touched.', r_failed: 'It failed the checks: {l}. The hidden block was not touched.',
      pl_gate: 'Stopped by the mathematical checks (surface, walk-forward or trend): no AI call needed.', pl_abandon: 'abandons', pl_extend: 'extends', pl_new: 'new hypothesis',
      d_model: 'model', d_tested: 'combinations tested', d_judge: 'The Judge (computation, not AI)', d_pass: 'Probability of passing the evaluation', d_interval: 'interval', d_bust: 'bust risk', d_pnl_block: 'P&L on the block',
      kp_med: 'Median Sharpe (all combinations)', kp_pos: 'Profitable combinations', kp_plat: 'Plateau (neighbours of the best)', kp_rob: 'Robust surface', kp_wf: 'Walk-forward P&L', yes: 'yes', no: 'no',
      d_best: 'Best combination in development: {p} (careful: it is the maximum of {n}, so optimistic)',
      s_wf: 'Walk-forward (out of sample)', s_locked: 'Hidden block', s_dev: 'In-sample (optimised)',
      m_total: 'Total P&L', m_sharpe: 'Sharpe (annualised)', m_sortino: 'Sortino', m_pf: 'Profit factor', m_payoff: 'Payoff (avg win / avg loss)', m_win: '% winning days', m_dd: 'Max drawdown', m_calmar: 'Calmar', m_bw: 'Best / worst day', m_days: 'Days',
      mc_med: 'Monte Carlo: median final', mc_rng: 'MC: 5th – 95th percentile', mc_loss: 'MC: probability of loss',
      ch_eq: 'Cumulative P&L', ch_mc: 'Monte Carlo: 2,000 possible futures (120 days)', ch_dd: 'Drawdown', ch_hist: 'Daily P&L distribution', ch_roll: 'Rolling Sharpe',
      ch_wf: 'Walk-forward (out of sample)', ch_lk: 'Hidden block (seen by the Judge)', ch_de: 'In-sample, optimised (NOT reliable)', ch_days: 'Days', ch_rs: 'Rolling Sharpe (40 days, annualised)', ch_med: 'Median',
      warn_dev: 'This curve is IN-SAMPLE: it is the best of all combinations on the same data used to pick it. It always looks good and proves nothing. Judge by the walk-forward and the hidden block.',
      warn_lk: 'Hidden block: data no optimisation ever saw. The Judge has already looked at it for this strategy.',
      aud_title: 'Auditor commentary (AI)', aud_next: 'Next step', planner: 'Planner',
      tr_wait: ['OFO TRADING DESK', 'waiting for', 'approved strategies'], tr_desk: 'TRADING DESK', tr_ready: 'ready', tr_head: 'APPROVED', tr_none: 'none yet',
      t_all: 'ALL', t_note: 'Data: your historical file {s} (New York time). Wheel to zoom, drag to pan. There is no automatic trading here yet.',
      t_loading: 'Loading candles…', t_coarser: 'Too many candles: using timeframe {tf}', t_sessions: '{n} sessions · {a} → {b}', t_err: 'Data not available: {e}',
      t_vol: 'Vol',
      agents_title: 'Agents at work', st_idle: 'idle', st_working: 'working', st_done: 'done',
      t_strat: 'Strategy on chart', t_none: '— none —', t_trades: '{n} trades · {w} winners · P&L {p}', t_hidden: '{n} trades in the hidden block not shown',
      t_notrades: 'No trades to show', t_legend: 'Arrows = entries (↑ long, ↓ short) · circles = exits (value = trade P&L). These are the trades of the best combination (in-sample): they do not prove profit.',
      acct_title: 'Accounts', acct_demo_note: 'DEMO DATA. For your accounts: copy rules/accounts.example.json to data/accounts.json and edit it.', acct_eval: 'EVAL', acct_funded: 'FUNDED', acct_demo: 'DEMO', acct_sign: ['ACCOUNTS', 'DEMO DATA'], acct_sign_real: ['ACCOUNTS'],
      as_ok: 'on track', as_danger: 'near the limit', as_passed: 'passed', as_busted: 'busted', acct_payouts: 'payouts: {n}', acct_prog: 'target {p}', acct_buf: 'buffer {p}'
    }
  };
  let lang = 'it';
  try { lang = JSON.parse(localStorage.getItem('ofo_lang')) || 'it'; } catch (e) {}
  if (!D[lang]) lang = 'it';
  window.LANG = lang;
  window.t = (k, v) => {
    let s = D[lang][k]; if (s === undefined) s = D.it[k]; if (s === undefined) return k;
    if (v && typeof s === 'string') for (const x in v) s = s.split('{' + x + '}').join(v[x]);
    return s;
  };
  window.setLang = (l) => { try { localStorage.setItem('ofo_lang', JSON.stringify(l)); } catch (e) {} location.reload(); };
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(e => e.textContent = t(e.dataset.i18n));
  document.querySelectorAll('[data-i18n-ph]').forEach(e => e.placeholder = t(e.dataset.i18nPh));
  document.querySelectorAll('[data-i18n-title]').forEach(e => e.title = t(e.dataset.i18nTitle));
  document.getElementById('lang-' + lang).classList.add('on');
  document.getElementById('lang-it').onclick = () => setLang('it'); document.getElementById('lang-en').onclick = () => setLang('en');
})();
