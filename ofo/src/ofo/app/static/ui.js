/* OFO Lab - interface logic (Italian / English) */
(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const loc = LANG === 'en' ? 'en-US' : 'it-IT';
  const money = (v) => v == null ? '–' : (v < 0 ? '-$' : '$') + Math.abs(Math.round(v)).toLocaleString(loc);
  const num = (v, d = 2) => v == null ? '–' : Number(v).toFixed(d);
  const pct = (v) => v == null ? '–' : (v * 100).toFixed(0) + '%';
  const AGENTS = { researcher: 'Researcher', strategist: 'Strategist', you: LANG === 'en' ? 'You' : 'Tu', optimizer: 'Optimizer', planner: 'Planner', validator: 'Validator', judge: 'Judge', auditor: 'Auditor', lab: 'Lab' };
  const api = async (path, opt) => {
    opt = opt || {};
    const r = await fetch(path, { method: opt.method || 'GET', headers: Object.assign({ 'X-OFO': '1' }, opt.json ? { 'Content-Type': 'application/json' } : {}), body: opt.json ? JSON.stringify(opt.json) : opt.body });
    return r.json();
  };
  const store = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };

  // ---------- tabs ----------
  let trader = null;
  function tab(name) {
    document.querySelectorAll('.tabs button[data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
    ['chat', 'strategies', 'trader'].forEach(n => $('#tab-' + n).hidden = n !== name);
    if (name === 'chat') $('#chatin').focus();
    if (name === 'trader') trader.show();
  }
  document.querySelectorAll('.tabs button[data-tab]').forEach(b => b.onclick = () => tab(b.dataset.tab));
  $('#wide').onclick = () => { $('#right').classList.toggle('wide'); setTimeout(() => { if (window.LabScene) LabScene.resize(); trader && trader.fit(true); }, 80); };
  if (window.LabScene) LabScene.onPick = (st) => {
    if (st === 'strategist') tab('chat'); else if (st === 'trader') tab('trader');
    else if (st === 'research') { const d = $('#drop'); d.classList.add('over'); setTimeout(() => d.classList.remove('over'), 1200); } else tab('strategies');
  };

  // ---------- chat ----------
  let history = store.get('ofo_chat', []);
  function addMsg(role, text, cls) {
    const d = document.createElement('div'); d.className = 'm ' + (role === 'user' ? 'u' : 'a') + (cls ? ' ' + cls : ''); d.textContent = text;
    $('#msgs').appendChild(d); $('#msgs').scrollTop = 1e9; return d;
  }
  function specCard(sp) {
    const d = document.createElement('div'); d.className = 'card'; d.dataset.id = sp.id;
    const r = Object.entries(sp.ranges).map(([k, v]) => `${k}: ${v.length > 6 ? v[0] + '…' + v[v.length - 1] + ' (' + v.length + ' ' + t('c_values') + ')' : v.join(', ')}`).join('\n');
    d.innerHTML = `<h3>${esc(sp.name)}</h3><div class="meta">${t('c_model')} <b>${esc(sp.template)}</b> · ${sp.n_candidates} ${t('c_combos')} · ${t('c_tf')} ${esc(sp.timeframes.join(', '))}</div>
      <details><summary>${t('c_params')}</summary><pre style="white-space:pre-wrap;margin:6px 0">${esc(r)}</pre>${esc(sp.rationale)}</details>
      <button class="btn small">▶ ${t('c_run', { n: sp.n_candidates })}</button><div class="warn">${t('c_warn', { n: sp.registry_trials })}</div>`;
    d.querySelector('button').onclick = async (e) => { e.target.disabled = true; e.target.textContent = t('c_started'); await api('/api/run', { method: 'POST', json: { id: sp.id, lang: LANG } }); tab('strategies'); poll(); };
    return d;
  }
  function reqCard(rq) {
    const d = document.createElement('div'); d.className = 'card';
    d.innerHTML = `<h3>🔧 ${t('req_title', { n: esc(rq.name) })}</h3><div class="meta">${t('req_saved')}</div>${esc(rq.description)}<div class="warn">${t('req_warn')}</div>`;
    return d;
  }
  async function send(text) {
    if (!text.trim()) return;
    addMsg('user', text); history.push({ role: 'user', content: text });
    const wait = addMsg('assistant', t('thinking'));
    const out = await api('/api/chat', { method: 'POST', json: { message: text, history: history.slice(-8), lang: LANG } });
    wait.textContent = out.reply || out.error || t('no_reply'); if (out.error) wait.classList.add('err');
    history.push({ role: 'assistant', content: out.reply || '' }); store.set('ofo_chat', history.slice(-30));
    if (out.spec) $('#msgs').appendChild(specCard(out.spec));
    if (out.template_request) $('#msgs').appendChild(reqCard(out.template_request));
    $('#msgs').scrollTop = 1e9; poll();
  }
  $('#chatform').onsubmit = (e) => { e.preventDefault(); const v = $('#chatin').value; $('#chatin').value = ''; send(v); };
  $('#sugg').innerHTML = [1, 2, 3, 4].map(i => `<button>${esc(t('sugg' + i))}</button>`).join('');
  $('#sugg').querySelectorAll('button').forEach(b => b.onclick = () => send(b.textContent));
  if (!history.length) addMsg('assistant', t('greeting')); else history.slice(-6).forEach(h => addMsg(h.role === 'user' ? 'user' : 'a', h.content));

  // ---------- ideas / upload ----------
  const drop = $('#drop'), fileIn = $('#file');
  drop.onclick = () => fileIn.click();
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  async function upload(files) {
    for (const f of files) {
      const r = await api('/api/ideas/' + encodeURIComponent(f.name), { method: 'PUT', body: await f.arrayBuffer() });
      feedLocal(r.error ? t('up_err', { e: r.error }) : t('uploaded', { n: r.saved }), 'lab');
    }
    poll();
  }
  drop.addEventListener('drop', e => upload(e.dataTransfer.files)); fileIn.onchange = () => upload(fileIn.files);
  $('#ingest').onclick = async () => { await api('/api/ingest', { method: 'POST', json: { lang: LANG } }); feedLocal(t('reading'), 'researcher'); };

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
      r.events.forEach(e => { addFeed(e); if (window.LabScene && (evNext > 0 || r.events.length < 8)) LabScene.onEvent(e); });
      evNext = r.next;
    } catch (e) {}
  }

  // ---------- state ----------
  function renderState(s) {
    $('#c-trials').textContent = s.trials; $('#c-looks').textContent = s.locked_looks;
    $('#c-calls').textContent = s.calls_last_hour == null ? '–' : s.calls_last_hour + '/' + (s.calls_per_hour ?? '–');
    $('#c-ai').textContent = s.ai ? t('ai_on') : t('ai_off'); $('#c-ai-chip').className = 'chip ' + (s.ai ? 'ok' : 'bad');
    $('#ideas').innerHTML = s.ideas.length ? s.ideas.map(i => `<div><span>${esc(i.name)}</span><span>${i.read ? '<i>' + t('read_ok') + '</i>' : i.kb + ' KB'}</span></div>`).join('') : `<div class="empty">${t('no_files')}</div>`;
    $('#n-strat').textContent = s.pending.length || ''; $('#n-strat').style.display = s.pending.length ? 'inline' : 'none';
    const pend = $('#pending'); pend.innerHTML = s.pending.length ? `<h2>${t('pending_title')}</h2>` : '';
    s.pending.forEach(sp => { const o = document.createElement('div'); o.style.cssText = 'color:#7d869b;font-size:10px;margin-bottom:2px'; o.textContent = t('origin') + ': ' + t('o_' + sp.origin); pend.appendChild(o); pend.appendChild(specCard(sp)); });
    $('#jobs').innerHTML = s.jobs.filter(j => j.status === 'queued' || j.status === 'running').map(j => `<div class="card"><span class="spin"></span>${t('job_run', { n: '<b>' + esc(j.name) + '</b>', c: j.n_candidates })}</div>`).join('')
      + s.jobs.filter(j => j.status === 'error').slice(-1).map(j => `<div class="card" style="border-color:#ff5c5c">${t('job_err', { n: esc(j.name), e: esc(j.error) })}</div>`).join('');
    $('#studies').innerHTML = `<h2>${t('studies_title')}</h2>` + (s.strategies.length ? s.strategies.map(x => `<div class="row" data-slug="${esc(x.slug)}"><div><b>${esc(x.name)}</b><small>${esc(x.template)} · ${x.n_candidates} ${t('combos')} · ${t('med_sharpe')} ${num(x.median_sharpe, 3)} · ${x.robust ? t('robust') : t('fragile')}</small></div><span class="badge b-${x.status}">${t('v_' + x.status)}</span></div>`).join('') : `<div class="empty">${t('none_yet')}</div>`);
    $('#studies').querySelectorAll('.row').forEach(r => r.onclick = () => openDetail(r.dataset.slug));
    $('#reqs').innerHTML = s.template_requests.length ? `<h2>${t('reqs_title')}</h2>` + s.template_requests.map(r => `<div class="card"><b>${esc(r.name)}</b><div class="meta">${esc(r.when)}</div>${esc(r.description)}</div>`).join('') : '';
    if (window.LabScene) LabScene.setTrader(s.strategies.filter(x => x.status === 'APPROVED').map(x => x.name), { wait: t('tr_wait'), desk: t('tr_desk'), ready: t('tr_ready'), head: t('tr_head'), none: t('tr_none') });
    trader.symbol = s.symbol;
  }
  async function poll() { try { renderState(await api('/api/state')); } catch (e) {} }

  // ---------- Trader tab: candlestick chart of the user's own historical data ----------
  trader = (function () {
    const TFS = [['1min', '1m'], ['5min', '5m'], ['15min', '15m'], ['30min', '30m'], ['60min', '1h'], ['1D', '1D']];
    const RNG = [['1D', 1, '1min'], ['5D', 5, '5min'], ['1M', 21, '30min'], ['3M', 63, '60min'], ['6M', 126, '1D'], ['1Y', 252, '1D'], ['ALL', 'all', '1D']];
    let tf = '15min', rng = '5D', chart = null, cs = null, vs = null, last = null, symbol = 'NQ';
    const order = TFS.map(x => x[0]);
    const seg = (el, items, cur, fn) => { el.innerHTML = items.map(([k, label]) => `<button data-k="${k}" class="${k === cur ? 'on' : ''}">${label}</button>`).join(''); el.querySelectorAll('button').forEach(b => b.onclick = () => fn(b.dataset.k)); };
    function draw() {
      seg($('#tf'), TFS, tf, k => { tf = k; draw(); load(); });
      seg($('#rng'), RNG.map(r => [r[0], r[0] === 'ALL' ? t('t_all') : r[0]]), rng, k => { rng = k; tf = RNG.find(r => r[0] === k)[2]; draw(); load(); });
    }
    function ensure() {
      if (chart) return;
      const el = $('#tchart');
      chart = LightweightCharts.createChart(el, {
        layout: { background: { type: 'solid', color: '#05070b' }, textColor: '#8b93a8', fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11 },
        grid: { vertLines: { color: '#0f1622' }, horzLines: { color: '#0f1622' } },
        rightPriceScale: { borderColor: '#1a2230' }, timeScale: { borderColor: '#1a2230', timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal }, width: el.clientWidth, height: el.clientHeight || 320
      });
      cs = chart.addCandlestickSeries({ upColor: '#39ff7a', downColor: '#ff4b4b', borderVisible: false, wickUpColor: '#39ff7a', wickDownColor: '#ff4b4b' });
      vs = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      chart.subscribeCrosshairMove(p => {
        const c = p.seriesData && p.seriesData.get(cs); if (!c) return showLast();
        const v = p.seriesData.get(vs); const up = c.close >= c.open;
        $('#tinfo').innerHTML = `O <b>${c.open}</b> H <b>${c.high}</b> L <b>${c.low}</b> C <b class="${up ? 'up' : 'dn'}">${c.close}</b>${v ? ' ' + t('t_vol') + ' <b>' + v.value + '</b>' : ''}`;
      });
      new ResizeObserver(fit).observe(el);
    }
    function fit(refit) { if (!chart) return; const el = $('#tchart'); chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || 320 }); if (refit === true) chart.timeScale().fitContent(); }
    function showLast() { if (last) $('#tinfo').textContent = last; }
    async function load(tryTf) {
      ensure(); fit();
      const days = RNG.find(r => r[0] === rng)[1];
      $('#tinfo').textContent = t('t_loading');
      const d = await api(`/api/candles?tf=${tf}&days=${days}`);
      if (d.error === 'too_many') {                       // too many candles: step up to a coarser timeframe
        const next = order[order.indexOf(tf) + 1]; if (!next) { $('#tinfo').textContent = t('t_err', { e: d.error }); return; }
        tf = next; draw(); $('#tnote').textContent = t('t_coarser', { tf }); return load();
      }
      if (d.error) { $('#tinfo').textContent = t('t_err', { e: d.error }); return; }
      symbol = d.symbol;
      cs.setData(d.candles.map(c => ({ time: c[0], open: c[1], high: c[2], low: c[3], close: c[4] })));
      vs.setData(d.candles.map(c => ({ time: c[0], value: c[5], color: c[4] >= c[1] ? '#39ff7a40' : '#ff4b4b40' })));
      chart.timeScale().fitContent();
      last = `${symbol} ${tf} · ` + t('t_sessions', { n: d.n_sessions, a: d.from, b: d.to }); $('#tinfo').textContent = last;
      $('#tnote').textContent = t('t_note', { s: d.symbol });
    }
    return { show() { draw(); ensure(); setTimeout(() => { fit(); if (!last) load(); }, 30); }, fit, set symbol(v) { symbol = v; }, get symbol() { return symbol; } };
  })();
  (async () => {   // the same data on the 3D trading monitors
    try { const d = await api('/api/candles?tf=15min&days=3'); if (d.candles && window.LabScene) LabScene.setTraderChart(d.candles, d.symbol); } catch (e) {}
  })();

  // ---------- detail ----------
  const kpi = (label, val, cls) => `<div class="kpi"><span>${label}</span><b class="${cls || ''}">${val}</b></div>`;
  function itReason(r) {
    if (!r) return '';
    const m = r.match(/\[(.*?)\]/), names = m ? m[1].replace(/'/g, '').split(',').map(x => x.trim()).filter(Boolean) : [];
    const l = names.map(n => t('w_' + n) === 'w_' + n ? n : t('w_' + n)).join('; ');
    return /fragile_without_market_drift/.test(r) ? t('r_fragile', { l }) : t('r_failed', { l });
  }
  async function openDetail(slug) {
    const d = await api('/api/strategy/' + encodeURIComponent(slug));
    if (d.error) return;
    const rep = d.report, L = rep.locked, ch = d.charts, sur = rep.surface;
    const keys = ['wf_oos'].concat(ch.locked ? ['locked'] : []).concat(['dev_best']).filter(k => ch[k]);
    let cur = keys[0];
    const title = { wf_oos: t('s_wf'), locked: t('s_locked'), dev_best: t('s_dev') };
    const pl = d.planner && d.planner.reason ? (/failed a deterministic gate/.test(d.planner.reason) ? t('pl_gate') : d.planner.reason) : '';
    $('#detail').innerHTML = `
      <div class="dtitle"><h1>${esc(rep.name)}</h1><span class="badge b-${L.status}">${t('v_' + L.status)}</span></div>
      <div class="sub">${t('d_model')} ${esc(rep.template)} · ${rep.n_candidates} ${t('d_tested')} · ${esc(rep.source || '')}</div>
      <div class="judge"><b>${t('d_judge')}</b> ${L.reason ? '<div style="margin-top:6px">' + esc(itReason(L.reason)) + '</div>' : ''}
        ${L.checks ? '<ul style="padding:0;margin:8px 0">' + Object.entries(L.checks).map(([k, v]) => `<li class="${v ? 'ok' : 'no'}">${esc(t('k_' + k))}</li>`).join('') + '</ul>' : ''}
        ${L.p_pass != null ? `<div>${t('d_pass')}: <b>${pct(L.p_pass)}</b> (${t('d_interval')} ${pct(L.p_pass_ci95[0])}–${pct(L.p_pass_ci95[1])}) · ${t('d_bust')}: <b>${pct(L.p_bust)}</b> · ${t('d_pnl_block')}: <b class="${L.pnl >= 0 ? 'pos' : 'neg'}">${money(L.pnl)}</b></div>` : ''}</div>
      <div class="grid">${kpi(t('kp_med'), num(sur.median_sharpe, 3))}${kpi(t('kp_pos'), pct(sur.frac_positive))}${kpi(t('kp_plat'), num(sur.plateau_ratio, 2))}${kpi(t('kp_rob'), sur.robust ? t('yes') : t('no'), sur.robust ? 'pos' : 'neg')}${kpi(t('kp_wf'), money(rep.wf_oos_pnl), rep.wf_oos_pnl >= 0 ? 'pos' : 'neg')}</div>
      ${d.best_params ? `<div class="sub">${t('d_best', { p: esc(JSON.stringify(d.best_params)), n: rep.n_candidates })}</div>` : ''}
      <div class="seg" id="seg">${keys.map(k => `<button data-k="${k}" class="${k === cur ? 'on' : ''}">${title[k]}</button>`).join('')}</div>
      <div id="warn"></div><div class="grid" id="kpis"></div>
      <div class="charts">
        <div class="chartbox"><h4>${t('ch_eq')}</h4><canvas id="c-eq"></canvas></div>
        <div class="chartbox"><h4>${t('ch_mc')}</h4><canvas id="c-mc"></canvas></div>
        <div class="chartbox"><h4>${t('ch_dd')}</h4><canvas id="c-dd"></canvas></div>
        <div class="chartbox"><h4>${t('ch_hist')}</h4><canvas id="c-hi"></canvas></div>
        <div class="chartbox"><h4>${t('ch_roll')}</h4><canvas id="c-ro"></canvas></div>
      </div>
      ${d.auditor ? `<div class="judge"><b>${t('aud_title')}</b><p>${esc(d.auditor.summary)}</p>${(d.auditor.risk_flags || []).length ? '<ul style="padding:0">' + d.auditor.risk_flags.map(f => `<li class="no">${esc(f)}</li>`).join('') + '</ul>' : ''}<p><b>${t('aud_next')}:</b> ${esc(d.auditor.next_step)}</p></div>` : ''}
      ${pl ? `<div class="judge"><b>${t('planner')}:</b> ${esc(t('pl_' + ({ abandon: 'abandon', extend: 'extend', new_hypothesis: 'new' }[d.planner.decision] || 'abandon')))} — ${esc(pl)}</div>` : ''}`;
    $('#modal').hidden = false;
    function draw() {
      LabCharts.destroyAll();
      const p = ch[cur], m = p.metrics, mc = p.monte_carlo;
      $('#warn').innerHTML = cur === 'dev_best' ? `<div class="warnbox">${t('warn_dev')}</div>` : cur === 'locked' ? `<div class="warnbox" style="color:#9bd">${t('warn_lk')}</div>` : '';
      $('#kpis').innerHTML = kpi(t('m_total'), money(m.total_pnl), m.total_pnl >= 0 ? 'pos' : 'neg') + kpi(t('m_sharpe'), num(m.sharpe)) + kpi(t('m_sortino'), num(m.sortino)) + kpi(t('m_pf'), num(m.profit_factor)) + kpi(t('m_payoff'), num(m.payoff_ratio)) + kpi(t('m_win'), pct(m.win_rate)) + kpi(t('m_dd'), money(m.max_drawdown), 'neg') + kpi(t('m_calmar'), num(m.calmar)) + kpi(t('m_bw'), money(m.best_day) + ' / ' + money(m.worst_day)) + kpi(t('m_days'), m.days)
        + (mc && !mc.error ? kpi(t('mc_med'), money(mc.final.p50), mc.final.p50 >= 0 ? 'pos' : 'neg') + kpi(t('mc_rng'), money(mc.final.p5) + ' / ' + money(mc.final.p95)) + kpi(t('mc_loss'), pct(mc.p_loss), mc.p_loss > 0.4 ? 'neg' : '') : '');
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
