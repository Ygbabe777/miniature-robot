/* OFO Lab - interface logic */
(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const money = (v) => v == null ? '–' : (v < 0 ? '-$' : '$') + Math.abs(Math.round(v)).toLocaleString('it-IT');
  const num = (v, d = 2) => v == null ? '–' : Number(v).toFixed(d);
  const pct = (v) => v == null ? '–' : (v * 100).toFixed(0) + '%';
  const VERDICT = { APPROVED: 'APPROVATA', REJECTED: 'RESPINTA', INSUFFICIENT_EVIDENCE: 'PROVE INSUFFICIENTI' };
  const CHECKS = { enough_days: 'Giorni sufficienti', dsr: 'Sharpe deflazionato (DSR) sufficiente', pass_prob_ci_lower: 'Probabilità di passare (limite inferiore) sufficiente', bust_risk: 'Rischio di bust accettabile' };
  const WHY = { plateau: 'stabilità dei parametri (plateau)', median_sharpe: 'Sharpe mediano positivo', frac_positive: 'almeno il 55% di combinazioni in profitto',
    neighbours_positive: 'vicini del migliore in profitto', walk_forward_pnl: 'walk-forward in profitto' };
  function itReason(r) {
    if (!r) return '';
    const m = r.match(/\[(.*?)\]/), names = m ? m[1].replace(/'/g, '').split(',').map(x => x.trim()).filter(Boolean) : [];
    const list = names.map(n => WHY[n] || n).join('; ');
    if (/fragile_without_market_drift/.test(r)) return 'Senza il trend del mercato la superficie dei parametri non regge (' + list + '). Il blocco nascosto non è stato toccato.';
    return 'Non ha superato i controlli: ' + list + '. Il blocco nascosto non è stato toccato.';
  }
  const itPlanner = (p) => /failed a deterministic gate/.test(p.reason) ? 'Fermata dai controlli matematici (superficie, walk-forward o trend): nessuna chiamata AI necessaria.' : p.reason;
  const AGENTS = { researcher: 'Researcher', strategist: 'Strategist', you: 'Tu', optimizer: 'Optimizer', planner: 'Planner', validator: 'Validator', judge: 'Judge', auditor: 'Auditor', lab: 'Lab' };
  const api = async (path, opt) => {
    opt = opt || {};
    const r = await fetch(path, { method: opt.method || 'GET', headers: Object.assign({ 'X-OFO': '1' }, opt.json ? { 'Content-Type': 'application/json' } : {}), body: opt.json ? JSON.stringify(opt.json) : opt.body });
    return r.json();
  };
  const store = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };

  // ---------- tabs ----------
  function tab(name) {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
    $('#tab-chat').hidden = name !== 'chat'; $('#tab-strategies').hidden = name !== 'strategies';
    if (name === 'chat') $('#chatin').focus();
  }
  document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => tab(b.dataset.tab));
  if (window.LabScene) LabScene.onPick = (st) => {
    if (st === 'strategist') tab('chat');
    else if (st === 'research') { const d = $('#drop'); d.classList.add('over'); setTimeout(() => d.classList.remove('over'), 1200); }
    else tab('strategies');
  };

  // ---------- chat ----------
  let history = store.get('ofo_chat', []);
  const SUGGEST = ['Creami una strategia basata sull\'ORB con i retest', 'Reversione al VWAP con soglie diverse', 'Cosa fa l\'orb_retest?', 'Quali modelli di strategia esistono?'];
  function addMsg(role, text, cls) {
    const d = document.createElement('div'); d.className = 'm ' + (role === 'user' ? 'u' : 'a') + (cls ? ' ' + cls : ''); d.textContent = text;
    $('#msgs').appendChild(d); $('#msgs').scrollTop = 1e9; return d;
  }
  function specCard(sp) {
    const d = document.createElement('div'); d.className = 'card'; d.dataset.id = sp.id;
    const r = Object.entries(sp.ranges).map(([k, v]) => `${k}: ${v.length > 6 ? v[0] + '…' + v[v.length - 1] + ' (' + v.length + ' valori)' : v.join(', ')}`).join('\n');
    d.innerHTML = `<h3>${esc(sp.name)}</h3><div class="meta">modello <b>${esc(sp.template)}</b> · ${sp.n_candidates} combinazioni · timeframe ${esc(sp.timeframes.join(', '))}</div>
      <details><summary>Parametri e motivazione</summary><pre style="white-space:pre-wrap;margin:6px 0">${esc(r)}</pre>${esc(sp.rationale)}</details>
      <button class="btn small">▶ Avvia il test (${sp.n_candidates} tentativi)</button>
      <div class="warn">Ogni combinazione testata viene contata e alza la soglia per le prossime approvazioni (ora: ${sp.registry_trials}).</div>`;
    d.querySelector('button').onclick = async (e) => { e.target.disabled = true; e.target.textContent = 'avviato…'; await api('/api/run', { method: 'POST', json: { id: sp.id } }); tab('strategies'); poll(); };
    return d;
  }
  function reqCard(rq) {
    const d = document.createElement('div'); d.className = 'card';
    d.innerHTML = `<h3>🔧 Manca un modello: ${esc(rq.name)}</h3><div class="meta">salvato tra le richieste</div>${esc(rq.description)}<div class="warn">Questo modello non esiste ancora: va implementato nel codice. Chiedilo a Claude e poi aggiorna il programma.</div>`;
    return d;
  }
  async function send(text) {
    if (!text.trim()) return;
    addMsg('user', text); history.push({ role: 'user', content: text });
    const wait = addMsg('assistant', '…');
    const out = await api('/api/chat', { method: 'POST', json: { message: text, history: history.slice(-8) } });
    wait.textContent = out.reply || out.error || 'Nessuna risposta'; if (out.error) wait.classList.add('err');
    history.push({ role: 'assistant', content: out.reply || '' }); store.set('ofo_chat', history.slice(-30));
    if (out.spec) $('#msgs').appendChild(specCard(out.spec));
    if (out.template_request) $('#msgs').appendChild(reqCard(out.template_request));
    $('#msgs').scrollTop = 1e9; poll();
  }
  $('#chatform').onsubmit = (e) => { e.preventDefault(); const v = $('#chatin').value; $('#chatin').value = ''; send(v); };
  $('#sugg').innerHTML = SUGGEST.map(s => `<button>${esc(s)}</button>`).join('');
  $('#sugg').querySelectorAll('button').forEach(b => b.onclick = () => send(b.textContent));
  if (!history.length) addMsg('assistant', 'Ciao! Sono lo Strategist. Dimmi che strategia vuoi (es. "ORB con i retest") e la preparo al volo. Poi decidi tu se avviare il test: non parte mai da solo.');
  else history.slice(-6).forEach(h => addMsg(h.role === 'user' ? 'user' : 'a', h.content));

  // ---------- ideas / upload ----------
  const drop = $('#drop'), fileIn = $('#file');
  drop.onclick = () => fileIn.click();
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  async function upload(files) {
    for (const f of files) {
      const r = await api('/api/ideas/' + encodeURIComponent(f.name), { method: 'PUT', body: await f.arrayBuffer() });
      feedLocal(r.error ? 'Errore: ' + r.error : 'Caricato: ' + r.saved, 'lab');
    }
    poll();
  }
  drop.addEventListener('drop', e => upload(e.dataTransfer.files)); fileIn.onchange = () => upload(fileIn.files);
  $('#ingest').onclick = async () => { await api('/api/ingest', { method: 'POST', json: {} }); feedLocal('Il Researcher sta leggendo i nuovi file…', 'researcher'); };

  // ---------- feed ----------
  let evNext = 0;
  function feedLocal(msg, agent) { addFeed({ agent, type: '', message: msg, t: new Date().toISOString() }); }
  function addFeed(e) {
    const d = document.createElement('div'); d.className = 'ev ' + e.agent;
    d.innerHTML = `<small>${esc(AGENTS[e.agent] || e.agent)} · ${esc(e.type)} · ${esc((e.t || '').slice(11, 19))}</small>${esc(e.message)}`;
    const f = $('#feed'); f.insertBefore(d, f.firstChild); while (f.children.length > 150) f.removeChild(f.lastChild);
  }
  async function pollEvents() {
    try {
      const r = await api('/api/events?after=' + evNext);
      if (evNext === 0 && r.events.length > 60) r.events = r.events.slice(-60);
      r.events.forEach(e => { addFeed(e); if (window.LabScene && evNext > 0 || r.events.length < 8) LabScene.onEvent(e); });
      evNext = r.next;
    } catch (e) {}
  }

  // ---------- state ----------
  let lastState = null;
  function renderState(s) {
    lastState = s;
    $('#c-trials').textContent = s.trials; $('#c-looks').textContent = s.locked_looks;
    $('#c-calls').textContent = s.calls_last_hour == null ? '–' : s.calls_last_hour + '/' + (s.calls_per_hour ?? '–');
    $('#c-ai').textContent = s.ai ? 'attiva' : 'spenta'; $('#c-ai-chip').className = 'chip ' + (s.ai ? 'ok' : 'bad');
    $('#ideas').innerHTML = s.ideas.length ? s.ideas.map(i => `<div><span>${esc(i.name)}</span><span>${i.read ? '<i>✔ letto</i>' : i.kb + ' KB'}</span></div>`).join('') : '<div class="empty">nessun file</div>';
    $('#n-strat').textContent = s.pending.length ? s.pending.length : '';
    $('#n-strat').style.display = s.pending.length ? 'inline' : 'none';
    // pending (from papers / planner / chat)
    const pend = $('#pending'); pend.innerHTML = s.pending.length ? '<h2>Strategie in attesa del tuo via</h2>' : '';
    s.pending.forEach(sp => { const c = specCard(sp); const o = document.createElement('div'); o.className = 'meta'; o.style.cssText = 'color:#8b93a8;font-size:10px;margin-bottom:2px'; o.textContent = 'origine: ' + ({ chat: 'chat', paper: 'research paper', planner: 'Planner' }[sp.origin] || sp.origin); pend.appendChild(o); pend.appendChild(c); });
    // jobs
    $('#jobs').innerHTML = s.jobs.filter(j => j.status === 'queued' || j.status === 'running').map(j => `<div class="card"><span class="spin"></span>Test in corso: <b>${esc(j.name)}</b> (${j.n_candidates} combinazioni)</div>`).join('')
      + s.jobs.filter(j => j.status === 'error').slice(-1).map(j => `<div class="card" style="border-color:#ff5c5c">Errore in ${esc(j.name)}: ${esc(j.error)}</div>`).join('');
    // studies
    $('#studies').innerHTML = '<h2>Strategie testate</h2>' + (s.strategies.length ? s.strategies.map(x => `<div class="row" data-slug="${esc(x.slug)}"><div><b>${esc(x.name)}</b><small>${esc(x.template)} · ${x.n_candidates} combinazioni · Sharpe mediano ${num(x.median_sharpe, 3)} · ${x.robust ? 'superficie robusta' : 'superficie fragile'}</small></div><span class="badge b-${x.status}">${VERDICT[x.status] || x.status}</span></div>`).join('') : '<div class="empty">Nessuna ancora. Chiedi una strategia nella chat.</div>');
    $('#studies').querySelectorAll('.row').forEach(r => r.onclick = () => openDetail(r.dataset.slug));
    $('#reqs').innerHTML = s.template_requests.length ? '<h2>Modelli richiesti (da implementare)</h2>' + s.template_requests.map(r => `<div class="card"><b>${esc(r.name)}</b><div class="meta">${esc(r.when)}</div>${esc(r.description)}</div>`).join('') : '';
    if (window.LabScene) LabScene.setTrader(s.strategies.filter(x => x.status === 'APPROVED').map(x => x.name));
  }
  async function poll() { try { renderState(await api('/api/state')); } catch (e) {} }

  // ---------- detail ----------
  const kpi = (label, val, cls) => `<div class="kpi"><span>${label}</span><b class="${cls || ''}">${val}</b></div>`;
  async function openDetail(slug) {
    const d = await api('/api/strategy/' + encodeURIComponent(slug));
    if (d.error) return;
    const rep = d.report, L = rep.locked, ch = d.charts, sur = rep.surface;
    const keys = ['wf_oos'].concat(ch.locked ? ['locked'] : []).concat(['dev_best']).filter(k => ch[k]);
    let cur = keys[0];
    const title = { wf_oos: 'Walk-forward (fuori campione)', locked: 'Blocco nascosto', dev_best: 'In-campione (ottimizzato)' };
    $('#detail').innerHTML = `
      <div class="dtitle"><h1>${esc(rep.name)}</h1><span class="badge b-${L.status}">${VERDICT[L.status] || L.status}</span></div>
      <div class="sub">modello ${esc(rep.template)} · ${rep.n_candidates} combinazioni testate · ${esc(rep.source || '')}</div>
      <div class="judge"><b>Il Judge (calcolo, non AI)</b> ${L.reason ? '<div style="margin-top:6px">' + esc(itReason(L.reason)) + '</div>' : ''}
        ${L.checks ? '<ul style="padding:0;margin:8px 0">' + Object.entries(L.checks).map(([k, v]) => `<li class="${v ? 'ok' : 'no'}">${esc(CHECKS[k] || k)}</li>`).join('') + '</ul>' : ''}
        ${L.p_pass != null ? `<div>Probabilità di passare la valutazione: <b>${pct(L.p_pass)}</b> (intervallo ${pct(L.p_pass_ci95[0])}–${pct(L.p_pass_ci95[1])}) · rischio di bust: <b>${pct(L.p_bust)}</b> · P&L sul blocco: <b class="${L.pnl >= 0 ? 'pos' : 'neg'}">${money(L.pnl)}</b></div>` : ''}</div>
      <div class="grid">${kpi('Sharpe mediano (tutte le combinazioni)', num(sur.median_sharpe, 3))}${kpi('Combinazioni positive', pct(sur.frac_positive))}${kpi('Plateau (vicini del migliore)', num(sur.plateau_ratio, 2))}${kpi('Superficie robusta', sur.robust ? 'sì' : 'no', sur.robust ? 'pos' : 'neg')}${kpi('Walk-forward P&L', money(rep.wf_oos_pnl), rep.wf_oos_pnl >= 0 ? 'pos' : 'neg')}</div>
      ${d.best_params ? `<div class="sub">Migliore combinazione in sviluppo: ${esc(JSON.stringify(d.best_params))} (attenzione: è il massimo di ${rep.n_candidates}, quindi ottimistico)</div>` : ''}
      <div class="seg" id="seg">${keys.map(k => `<button data-k="${k}" class="${k === cur ? 'on' : ''}">${title[k]}</button>`).join('')}</div>
      <div id="warn"></div><div class="grid" id="kpis"></div>
      <div class="charts">
        <div class="chartbox"><h4>P&L cumulato</h4><canvas id="c-eq"></canvas></div>
        <div class="chartbox"><h4>Monte Carlo: 2.000 futuri possibili (120 giorni)</h4><canvas id="c-mc"></canvas></div>
        <div class="chartbox"><h4>Drawdown</h4><canvas id="c-dd"></canvas></div>
        <div class="chartbox"><h4>Distribuzione del P&L giornaliero</h4><canvas id="c-hi"></canvas></div>
        <div class="chartbox"><h4>Sharpe mobile</h4><canvas id="c-ro"></canvas></div>
      </div>
      ${d.auditor ? `<div class="judge"><b>Commento dell'Auditor (AI)</b><p>${esc(d.auditor.summary)}</p>${(d.auditor.risk_flags || []).length ? '<ul style="padding:0">' + d.auditor.risk_flags.map(f => `<li class="no">${esc(f)}</li>`).join('') + '</ul>' : ''}<p><b>Prossimo passo:</b> ${esc(d.auditor.next_step)}</p></div>` : ''}
      ${d.planner && d.planner.reason ? `<div class="judge"><b>Planner:</b> ${esc({ abandon: 'abbandona', extend: 'estende', new_hypothesis: 'nuova ipotesi' }[d.planner.decision] || d.planner.decision)} — ${esc(itPlanner(d.planner))}</div>` : ''}`;
    $('#modal').hidden = false;
    function draw() {
      LabCharts.destroyAll();
      const p = ch[cur], m = p.metrics, mc = p.monte_carlo;
      $('#warn').innerHTML = cur === 'dev_best' ? '<div class="warnbox">Questa curva è IN-CAMPIONE: è la migliore di tutte le combinazioni sugli stessi dati usati per sceglierla. Sembra sempre bella e non prova nulla. Giudica dal walk-forward e dal blocco nascosto.</div>' : cur === 'locked' ? '<div class="warnbox" style="color:#9bd">Blocco nascosto: dati che nessuna ottimizzazione ha mai visto. Il Judge lo ha già guardato per questa strategia.</div>' : '';
      $('#kpis').innerHTML = kpi('P&L totale', money(m.total_pnl), m.total_pnl >= 0 ? 'pos' : 'neg') + kpi('Sharpe (annualizzato)', num(m.sharpe)) + kpi('Sortino', num(m.sortino)) + kpi('Profit factor', num(m.profit_factor)) + kpi('Payoff (media vinc./perd.)', num(m.payoff_ratio)) + kpi('% giorni vincenti', pct(m.win_rate)) + kpi('Max drawdown', money(m.max_drawdown), 'neg') + kpi('Calmar', num(m.calmar)) + kpi('Giorno migliore / peggiore', money(m.best_day) + ' / ' + money(m.worst_day)) + kpi('Giorni', m.days)
        + (mc && !mc.error ? kpi('Monte Carlo: mediana finale', money(mc.final.p50), mc.final.p50 >= 0 ? 'pos' : 'neg') + kpi('MC: 5° – 95° percentile', money(mc.final.p5) + ' / ' + money(mc.final.p95)) + kpi('MC: probabilità di perdita', pct(mc.p_loss), mc.p_loss > 0.4 ? 'neg' : '') : '');
      LabCharts.equity($('#c-eq'), ch, cur === 'locked' ? ['locked'] : cur === 'dev_best' ? ['dev_best', 'wf_oos'] : ['wf_oos']);
      LabCharts.monteCarlo($('#c-mc'), p); LabCharts.drawdown($('#c-dd'), p); LabCharts.histogram($('#c-hi'), p); LabCharts.rolling($('#c-ro'), p);
    }
    $('#seg').querySelectorAll('button').forEach(b => b.onclick = () => { cur = b.dataset.k; $('#seg').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); draw(); });
    draw();
  }
  $('#close').onclick = () => { $('#modal').hidden = true; LabCharts.destroyAll(); };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#close').click(); });

  poll(); pollEvents();
  setInterval(poll, 3000); setInterval(pollEvents, 1500);
})();
