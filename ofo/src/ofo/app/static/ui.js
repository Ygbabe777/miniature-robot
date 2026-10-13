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
  let curTab = 'chat'; const tabN = { chat: 0, strategies: 0, trader: 0 };
  function tab(name) {
    curTab = name; tabN[name] = 0; try { paintDots(); } catch (e) {}
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


  // ---------- hide/show side panels, red-dot notifications, thought bubbles toggle ----------
  const panels = { left: { el: $('#left'), h: $('#lh'), dot: $('#lh-dot'), n: 0 }, right: { el: $('#right'), h: $('#rh'), dot: $('#rh-dot'), n: 0 } };
  function paintDots() {
    Object.values(panels).forEach(p => { const hid = p.el.classList.contains('hidden'); p.dot.textContent = p.n > 9 ? '9+' : p.n; p.dot.classList.toggle('on', hid && p.n > 0); });
    document.querySelectorAll('.tabs button[data-tab]').forEach(b => {
      let d = b.querySelector('.rdot'); if (!d) { d = document.createElement('i'); d.className = 'rdot'; b.appendChild(d); }
      const n = tabN[b.dataset.tab] || 0; d.textContent = n > 9 ? '9+' : n; d.classList.toggle('on', n > 0 && b.dataset.tab !== curTab);
    });
  }
  function notify(side, tabName) {                                   // something new: count it unless the user is already looking at it
    const p = panels[side], hid = p.el.classList.contains('hidden');
    if (side === 'left') { if (hid) p.n++; }
    else {
      if (hid) p.n++;
      if (tabName && (hid || tabName !== curTab)) tabN[tabName]++;
    }
    paintDots();
  }
  function setHidden(side, hid) {
    const p = panels[side]; p.el.classList.toggle('hidden', hid); p.h.textContent = side === 'left' ? (hid ? '▶' : '◀') : (hid ? '◀' : '▶');
    p.h.appendChild(p.dot); if (!hid) { p.n = 0; if (side === 'right') tabN[curTab] = 0; }
    store.set('ofo_hide_' + side, hid); paintDots(); setTimeout(() => { if (window.LabScene) LabScene.resize(); }, 280);
  }
  $('#lh').onclick = () => setHidden('left', !panels.left.el.classList.contains('hidden'));
  $('#rh').onclick = () => setHidden('right', !panels.right.el.classList.contains('hidden'));
  let bubOn = store.get('ofo_bubbles', true);
  $('#bh').onclick = () => { bubOn = !bubOn; store.set('ofo_bubbles', bubOn); $('#bh').style.opacity = bubOn ? 1 : .45; if (window.LabScene) LabScene.setBubbles(bubOn); };
  ['left', 'right'].forEach(sd => { if (store.get('ofo_hide_' + sd, false)) setHidden(sd, true); });
  $('#bh').style.opacity = bubOn ? 1 : .45; if (window.LabScene) LabScene.setBubbles(bubOn);
  document.addEventListener('keydown', e => { if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return; if (e.key === '[') $('#lh').click(); if (e.key === ']') $('#rh').click(); });

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
  function noteEvent(e) {
    if (e.type === 'verdict' || e.type === 'autostart' || e.type === 'template_request') notify('right', 'strategies');
    if (e.type === 'verdict' || e.type === 'download' || e.type === 'autostart' || e.type === 'budget' || e.type === 'proposal') notify('left');
    if (e.agent === 'strategist' && (e.type === 'proposal' || e.type === 'message')) notify('right', 'chat');
  }
  async function pollEvents() {
    try {
      const r = await api('/api/events?after=' + evNext);
      if (evNext === 0 && window.LabScene) LabScene.restoreStates(r.events);
      if (evNext === 0 && r.events.length > 60) r.events = r.events.slice(-60);
      r.events.forEach(e => { addFeed(e); if (evNext > 0) noteEvent(e); if (window.LabScene && (evNext > 0 || r.events.length < 8)) LabScene.onEvent(e); });
      evNext = r.next;
    } catch (e) {}
  }

  // ---------- state ----------
  function renderState(s) {
    $('#c-trials').textContent = s.trials; $('#c-looks').textContent = s.locked_looks;
    $('#c-calls').textContent = s.calls_last_minute == null ? '–' : s.calls_last_minute + '/' + (s.calls_per_minute ?? '–');
    $('#c-ai').textContent = s.ai ? t('ai_on') : t('ai_off'); $('#c-ai-chip').className = 'chip ' + (s.ai ? 'ok' : 'bad');
    $('#ideas').innerHTML = s.ideas.length ? s.ideas.map(i => `<div><span>${esc(i.name)}</span><span>${i.read ? '<i>' + t('read_ok') + '</i>' : i.kb + ' KB'}</span></div>`).join('') : `<div class="empty">${t('no_files')}</div>`;
    $('#n-strat').textContent = s.pending.length || ''; $('#n-strat').style.display = s.pending.length ? 'inline' : 'none';
    const pend = $('#pending'); pend.innerHTML = s.pending.length ? `<h2>${t(s.autopilot && s.autopilot.enabled ? 'pending_auto' : 'pending_title')}</h2>` : '';
    s.pending.forEach(sp => { const o = document.createElement('div'); o.style.cssText = 'color:var(--dim);font-size:10px;margin-bottom:2px'; o.textContent = t('origin') + ': ' + t('o_' + sp.origin) + (sp.market ? ' · ' + sp.market : ''); pend.appendChild(o); pend.appendChild(specCard(sp)); });
    $('#jobs').innerHTML = s.jobs.filter(j => j.status === 'queued' || j.status === 'running').map(j => `<div class="card"><span class="spin"></span>${t('job_run', { n: '<b>' + esc(j.name) + '</b>', c: j.n_candidates })}</div>`).join('')
      + s.jobs.filter(j => j.status === 'error').slice(-1).map(j => `<div class="card" style="border-color:var(--down)">${t('job_err', { n: esc(j.name), e: esc(j.error) })}</div>`).join('');
    $('#studies').innerHTML = `<h2>${t('studies_title')}</h2>` + (s.strategies.length ? s.strategies.map(x => `<div class="row" data-slug="${esc(x.slug)}"><div><b>${esc(x.name)}</b><small>${esc(x.template)} · ${x.n_candidates} ${t('combos')} · ${t('med_sharpe')} ${num(x.median_sharpe, 3)} · ${x.robust ? t('robust') : t('fragile')}</small></div><span class="badge b-${x.status}">${t('v_' + x.status)}</span></div>`).join('') : `<div class="empty">${t('none_yet')}</div>`);
    $('#studies').querySelectorAll('.row').forEach(r => r.onclick = () => openDetail(r.dataset.slug));
    $('#reqs').innerHTML = s.template_requests.length ? `<h2>${t('reqs_title')}</h2>` + s.template_requests.map(r => `<div class="card"><b>${esc(r.name)}</b><div class="meta">${esc(r.when)}</div>${esc(r.description)}</div>`).join('') : '';
    if (window.LabScene) LabScene.setTrader(s.strategies.filter(x => x.status === 'APPROVED').map(x => x.name), { wait: t('tr_wait'), desk: t('tr_desk'), ready: t('tr_ready'), head: t('tr_head'), none: t('tr_none') });
    trader.symbol = s.symbol; trader.setMarkets(s.markets, s.primary); trader.setStrategies(s.strategies);
  }
  async function poll() { try { const s = await api('/api/state'); renderState(s); if (s.scout) renderScout(s.scout); if (s.autopilot) renderAutopilot(s.autopilot); if (s.obsidian) renderObsidian(s.obsidian); renderBudget(s.budget); } catch (e) {} }

  // ---------- token budget ----------
  let budKey = '';
  function renderBudget(b) {
    const box = $('#budget'); if (!b) { box.innerHTML = `<div class="apm">${t('bud_none')}</div>`; return; }
    if (document.activeElement && document.activeElement.id === 'bud-in') return;
    const key = JSON.stringify([b, LANG]); if (key === budKey) return; budKey = key;
    const pctDay = b.autonomous_cap_usd > 0 ? Math.min(100, Math.round(100 * b.spent_today_usd / b.autonomous_cap_usd)) : 100;
    const cls = b.mode === 'normal' ? 'apon' : b.mode === 'economy' ? 'apm' : 'apoff';
    box.innerHTML = `<div class="${cls}">${t('bud_mode_' + b.mode)}</div>
      <div class="apm">${t('bud_left', { r: b.remaining_usd.toFixed(2), w: b.weekly_usd, d: b.days_left })}</div>
      <div class="apm">${t('bud_today', { s: b.spent_today_usd.toFixed(3), c: b.autonomous_cap_usd.toFixed(2), p: pctDay })} · ${(b.tokens_today / 1000).toFixed(0)}k tok</div>
      <div class="apm">${t('bud_price', { p: b.usd_per_million.toFixed(2) })} <i>(${esc(b.price_source)})</i></div>
      <div class="srow"><input id="bud-in" type="number" step="0.01" min="0" placeholder="${t('bud_ph')}" style="width:90px"> <button class="btn small" id="bud-go">${t('bud_set')}</button></div>
      <div class="apn">${t('bud_note')}</div>`;
    $('#bud-go').onclick = async () => { const v = parseFloat($('#bud-in').value); if (isNaN(v)) return; await api('/api/budget', { method: 'POST', json: { remaining_usd: v } }); budKey = ''; poll(); };
  }

  // ---------- Obsidian ----------
  let obsKey = '';
  function renderObsidian(o) {
    const key = JSON.stringify([o, LANG]); if (key === obsKey || document.activeElement === $('#obs-path')) return; obsKey = key;
    const w = o.last && o.last.wrote ? Object.values(o.last.wrote).reduce((a, b) => a + b, 0) : 0;
    $('#obsidian').innerHTML = `<input id="obs-path" placeholder="${t('obs_ph')}" value="${esc(o.vault || '')}" style="width:100%;box-sizing:border-box">
      <button class="btn small" id="obs-go">${o.vault ? t('obs_sync') : t('obs_connect')}</button>
      <div class="smeta">${o.error ? '<span class="apoff">' + esc(o.error) + '</span>' : o.last ? t('obs_last', { t: esc(o.last.at), n: w }) : t('obs_none')}</div>
      <div class="smeta">${t('obs_note')}</div>`;
    $('#obs-go').onclick = async () => { const r = await api('/api/obsidian', { method: 'POST', json: { vault: $('#obs-path').value } }); if (r.error) alert(r.error); obsKey = ''; poll(); };
  }

  // ---------- autopilot + ready strategies ----------
  function renderAutopilot(a) {
    if (window.LabScene) LabScene.setServer(a.working, a.enabled, a.deployed > 0);
    const mins = a.next_search ? Math.max(0, Math.round((a.next_search - Date.now() / 1000) / 60)) : null;
    const bits = [t('ap_phase_' + a.phase), t('ap_budget', { a: a.trials_today, b: a.max_trials_per_day }), t('ap_queue', { n: a.queue }), t('ap_ready', { n: a.ready })];
    if (a.searching) bits.push(t('ap_searching')); else if (mins !== null && a.enabled) bits.push(t('ap_next', { m: mins }));
    const key = JSON.stringify([a.enabled, bits, a.paused, LANG, a.markets]);
    if (key === apKey) return; apKey = key;
    $('#autopilot').innerHTML = `<div class="${a.enabled ? 'apon' : 'apoff'}">${a.enabled ? t('ap_on') : t('ap_off')}</div><div class="apm">${bits.join(' · ')}</div>`
      + (a.paused ? `<div class="apoff">${t('ap_paused_' + a.paused)}</div>` : '')
      + (a.markets && a.markets.length > 1 ? `<div class="apm"><b>${t('mk_title')}</b>${a.markets.map(m => `<label style="display:block"><input type="checkbox" class="mk-on" value="${m.sym}" ${m.enabled ? 'checked' : ''}> ${esc(m.name)} (${m.mini}/${m.micro})</label>`).join('')}</div>` : '')
      + `<button class="btn small" id="ap-toggle">${a.enabled ? t('ap_toggle_off') : t('ap_toggle_on')}</button><div class="apn">${t('ap_note')}</div>`;
    document.querySelectorAll('.mk-on').forEach(c => c.onchange = async () => { const on = [...document.querySelectorAll('.mk-on')].filter(x => x.checked).map(x => x.value); await api('/api/markets', { method: 'POST', json: { markets: on } }); apKey = ''; poll(); });
    $('#ap-toggle').onclick = async () => { await api('/api/autopilot', { method: 'POST', json: { enabled: !a.enabled } }); apKey = ''; poll(); };
    if (a.ready !== readyN) { readyN = a.ready; loadReady(); }
    if (a.deployed !== depN) { depN = a.deployed; loadDeployed(); }
  }
  let apKey = '', readyN = -1, depN = -1;
  async function loadDeployed() {
    try {
      const d = await api('/api/deployments'), el = $('#deployed');
      if (!d.items.length) { el.innerHTML = ''; return; }
      const dt = d.data || {};
      const note = dt.why === 'no_keys' ? t('dep_nokeys') : dt.ok === false ? t('dep_err', { e: esc(dt.why || '') }) : '';
      el.innerHTML = `<h2>${t('dep_title')}</h2><div class="sub">${t('dep_data', { d: esc(d.last_bar || '–') })} ${note}</div>` + d.items.map(x => {
        const f = x.forward || {};
        const eq = (f.equity || []).map(p => p[1]), max = Math.max(1, ...eq.map(Math.abs));
        const spark = eq.length > 1 ? `<svg viewBox="0 0 100 24" width="100%" height="24" preserveAspectRatio="none"><polyline fill="none" stroke="${(f.pnl || 0) >= 0 ? 'var(--up)' : 'var(--down)'}" stroke-width="1.5" points="${eq.map((v, i) => (i / (eq.length - 1) * 100).toFixed(1) + ',' + (12 - v / max * 11).toFixed(1)).join(' ')}"/></svg>` : '';
        return `<div class="card"><b>${esc(x.name)}</b> <span class="badge dep-${x.status}">${t('dep_' + x.status)}</span>
          <div class="meta">${x.account ? t('dep_on', { n: x.micro, a: esc(x.account) }) + ' · ' : ''}${t('dep_days', { n: f.days || 0 })}${f.days ? ' · P&L ' + money(f.pnl) : ''}${f.eval_if_traded ? ' · ' + t('dep_eval', { s: t('dep_ev_' + f.eval_if_traded.status), n: f.eval_if_traded.days }) : ''}</div>
          ${spark}${x.reason ? `<div class="sub">${esc(x.reason)}</div>` : ''}</div>`;
      }).join('');
    } catch (e) {}
  }
  setInterval(() => { depN = -1; }, 300000);
  async function loadReady() {
    try {
      const d = await api('/api/dossiers'), el = $('#ready');
      if (!d.dossiers.length) { el.innerHTML = `<h2>${t('ready_title')}</h2><div class="empty">${t('ready_none')}</div>`; return; }
      el.innerHTML = `<h2>${t('ready_title')} (${d.dossiers.length})</h2>` + d.dossiers.map(x => {
        const accts = Object.entries(x.accounts).map(([k, a]) => `<div class="dacct"><b>${t('ready_acct', { a: esc(k) })}</b><p>${esc(a.text)}</p>
          <details><summary>${t('ready_table')} (${t('ready_cols')})</summary><table>${a.table.map(r => `<tr class="${a.recommended && a.recommended.micro_equiv === r.micro_equiv ? 'pick' : ''}"><td>${r.micro_equiv}</td><td>${pct(r.p_fast)}</td><td>${pct(r.p_fast_lo)}</td><td>${pct(r.p_bust_fast)}</td><td>${pct(r.p_pass)}</td></tr>`).join('')}</table></details>
          ${a.multi ? `<details><summary>${t('ready_multi')} (${t('ready_multi_cols')})</summary><table>${a.multi.table.map(r => `<tr><td>${r.accounts}</td><td>${pct(r.p_pass_any)} <small>(${pct(r.p_pass_any_lo)}–${pct(r.p_pass_any_hi)})</small></td><td>${pct(r.p_payout_any)}</td><td>$${Math.round(r.expected_cost)}</td></tr>`).join('')}</table></details>` : ''}</div>`).join('');
        return `<div class="dossier"><h3 style="color:${x.fast_ok === false ? 'var(--warn)' : 'var(--up)'}">${esc(x.headline)}</h3><p>${esc(x.strategy_text)}</p>${x.comparison ? `<p><b>${esc(x.comparison)}</b></p>` : ''}${accts}${x.auditor ? `<p><b>${t('ready_aud')}:</b> ${esc(x.auditor)}</p>` : ''}<p class="cav">${esc(x.caveat)}</p></div>`;
      }).join('');
    } catch (e) {}
  }

  // ---------- Trader tab: candlestick chart of the user's own historical data ----------
  trader = (function () {
    const TFS = [['1min', '1m'], ['5min', '5m'], ['15min', '15m'], ['30min', '30m'], ['60min', '1h'], ['1D', '1D']];
    const RNG = [['1D', 1, '1min'], ['5D', 5, '5min'], ['1M', 21, '30min'], ['3M', 63, '60min'], ['6M', 126, '1D'], ['1Y', 252, '1D'], ['ALL', 'all', '1D']];
    let tf = '15min', rng = '5D', chart = null, cs = null, vs = null, last = null, symbol = 'NQ', before = null;
    let tfilter = 'all', focus = -1, ov = null, tsize = 'base'; let trades = null, loaded = [], slug = '', listKey = '', indSeries = [], market = '', markets = [];
    const order = TFS.map(x => x[0]);
    const seg = (el, items, cur, fn) => { el.innerHTML = items.map(([k, label]) => `<button data-k="${k}" class="${k === cur ? 'on' : ''}">${label}</button>`).join(''); el.querySelectorAll('button').forEach(b => b.onclick = () => fn(b.dataset.k)); };
    function draw() {
      seg($('#tf'), TFS, tf, k => { tf = k; draw(); load(); });
      seg($('#rng'), RNG.map(r => [r[0], r[0] === 'ALL' ? t('t_all') : r[0]]), rng, k => { rng = k; before = null; tf = RNG.find(r => r[0] === k)[2]; draw(); load(); });
    }
    function ensure() {
      if (chart) return;
      const el = $('#tchart');
      chart = LightweightCharts.createChart(el, {
        layout: { background: { type: 'solid', color: THEME.bg }, textColor: THEME.dim, fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11 },
        grid: { vertLines: { color: THEME.grid }, horzLines: { color: THEME.grid } },
        rightPriceScale: { borderColor: THEME.grid }, timeScale: { borderColor: THEME.grid, timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal }, width: el.clientWidth, height: el.clientHeight || 320
      });
      cs = chart.addCandlestickSeries({ upColor: THEME.up, downColor: THEME.down, borderVisible: false, wickUpColor: THEME.up, wickDownColor: THEME.down });
      vs = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      chart.subscribeCrosshairMove(p => {
        const c = p.seriesData && p.seriesData.get(cs); if (!c) return showLast();
        const v = p.seriesData.get(vs); const up = c.close >= c.open;
        $('#tinfo').innerHTML = `O <b>${c.open}</b> H <b>${c.high}</b> L <b>${c.low}</b> C <b class="${up ? 'up' : 'dn'}">${c.close}</b>${v ? ' ' + t('t_vol') + ' <b>' + v.value + '</b>' : ''}`;
      });
      new ResizeObserver(() => fit()).observe(el);
      chart.timeScale().subscribeVisibleTimeRangeChange(() => drawOverlay());
      ['wheel', 'pointermove', 'pointerup', 'touchmove'].forEach(ev => el.addEventListener(ev, () => requestAnimationFrame(drawOverlay), { passive: true }));
    }
    function fit(refit) { if (!chart) return; const el = $('#tchart'); chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || 320 }); if (refit === true) chart.timeScale().fitContent(); drawOverlay(); }
    function showLast() { if (last) $('#tinfo').textContent = last; }

    // entries (arrows) and exits (circles) of the selected strategy, snapped to the candles on screen
    function applyMarkers() {
      if (!cs) return;
      if (!trades || !trades.trades.length || !loaded.length) { cs.setMarkers([]); drawOverlay(); return; }
      const daily = typeof loaded[0][0] === 'string';
      if (!daily) { cs.setMarkers([]); drawOverlay(); return; }
      const times = loaded.map(c => c[0]), set = new Set(times);
      const snap = (epoch, session) => {
        if (daily) return set.has(session) ? session : null;
        if (epoch < times[0] || epoch > times[times.length - 1] + 86400) return null;
        let lo = 0, hi = times.length - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (times[m] <= epoch) lo = m; else hi = m - 1; }
        return times[lo];
      };
      const inView = trades.trades.filter(x => snap(x.entry_t, x.entry_session) !== null || snap(x.exit_t, x.session) !== null);
      const labels = inView.length <= 120, mk = [];
      inView.forEach(x => {
        const e = snap(x.entry_t, x.entry_session), q = snap(x.exit_t, x.session);
        if (e !== null) mk.push({ time: e, position: x.side > 0 ? 'belowBar' : 'aboveBar', color: x.side > 0 ? THEME.up : THEME.down, shape: x.side > 0 ? 'arrowUp' : 'arrowDown', text: labels ? (x.side > 0 ? 'L' : 'S') : '' });
        if (q !== null) mk.push({ time: q, position: 'inBar', color: x.pnl >= 0 ? THEME.up : THEME.down, shape: 'circle', text: labels ? (x.pnl >= 0 ? '+' : '-') + '$' + Math.abs(Math.round(x.pnl)) : '' });
      });
      mk.sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0);
      cs.setMarkers(mk.slice(0, 4000));
    }

    // ---- overlay: entry triangle at the real price, exit circle, connecting line, stop/target levels ----
    const vis = () => (trades ? trades.trades.map((x, i) => Object.assign({ i }, x)).filter(x => tfilter === 'all' || (tfilter === 'win' && x.pnl > 0) || (tfilter === 'loss' && x.pnl <= 0) || (tfilter === 'long' && x.side > 0) || (tfilter === 'short' && x.side < 0)) : []);
    const nC = () => tsize === 'base' || !trades ? (trades ? trades.base_contracts : 1) : ((trades.sizing || {})[tsize] || trades.base_contracts);
    const sc = () => nC() / (trades ? trades.base_contracts : 1), pnlOf = x => x.pnl * sc();
    const reasonTxt = x => t('tl_' + (x.reason === 'stop' || x.reason === 'target' ? x.reason : x.reason === 'open' ? 'open' : 'signal'));
    function drawOverlay() {
      const el = $('#tchart'); if (!el) return;
      if (!ov) { ov = document.createElement('canvas'); ov.id = 'tov'; el.appendChild(ov); }
      const w = el.clientWidth, h = el.clientHeight, dpr = window.devicePixelRatio || 1;
      ov.style.width = w + 'px'; ov.style.height = h + 'px'; ov.width = w * dpr; ov.height = h * dpr;
      const g = ov.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      if (!trades || !loaded.length || typeof loaded[0][0] === 'string') return;
      const ts = chart.timeScale(), t0 = loaded[0][0], t1 = loaded[loaded.length - 1][0] + 86400;
      const X = tt => ts.timeToCoordinate(tt), Y = p => cs.priceToCoordinate(p);
      const list = vis().filter(x => x.exit_t >= t0 && x.entry_t <= t1);
      const few = list.length <= 40;
      g.font = '10px ui-monospace, Consolas, monospace'; g.textBaseline = 'middle';
      list.forEach(x => {
        const sel = x.i === focus, win = x.pnl >= 0, col = win ? THEME.up : THEME.down;
        let xe = X(x.entry_t), xx = X(x.exit_t); const ye = Y(x.entry_px), yx = Y(x.exit_px);
        if (xe === null && xx === null) return;
        if (xe === null) xe = 0; if (xx === null) xx = w;
        if (ye === null || yx === null) return;
        g.globalAlpha = focus >= 0 && !sel ? 0.35 : 1;
        if (sel || few) {                                  // stop / target levels and the trade box
          const lv = (p, c) => { const y = Y(p); if (y === null) return; g.strokeStyle = c; g.setLineDash([4, 3]); g.lineWidth = 1; g.beginPath(); g.moveTo(xe, y); g.lineTo(xx, y); g.stroke(); g.setLineDash([]); g.fillStyle = c; g.fillText(p === x.stop ? 'SL ' + x.stop : 'TP ' + x.target, xx + 3, y); };
          if (x.stop) lv(x.stop, THEME.down); if (x.target) lv(x.target, THEME.up);
          g.fillStyle = col + '22'; g.fillRect(Math.min(xe, xx), Math.min(ye, yx), Math.max(2, Math.abs(xx - xe)), Math.max(2, Math.abs(yx - ye)));
        }
        g.strokeStyle = col; g.lineWidth = sel ? 2.5 : 1.5; g.setLineDash([2, 2]); g.beginPath(); g.moveTo(xe, ye); g.lineTo(xx, yx); g.stroke(); g.setLineDash([]);
        const up = x.side > 0, s = sel ? 8 : 6;               // entry triangle: up = long, down = short
        g.fillStyle = up ? THEME.up : THEME.down; g.strokeStyle = '#fff'; g.lineWidth = 1; g.beginPath();
        if (up) { g.moveTo(xe, ye - s); g.lineTo(xe - s, ye + s * .7); g.lineTo(xe + s, ye + s * .7); } else { g.moveTo(xe, ye + s); g.lineTo(xe - s, ye - s * .7); g.lineTo(xe + s, ye - s * .7); }
        g.closePath(); g.fill(); g.stroke();
        g.fillStyle = col; g.beginPath(); g.arc(xx, yx, sel ? 6 : 4.5, 0, 6.3); g.fill(); g.stroke();
        if (few || sel) {
          g.fillStyle = '#e8e8ee';
          g.fillText((up ? 'L ' : 'S ') + x.entry_px + ' ×' + nC(), xe + 9, ye + (up ? 12 : -12));
          g.fillStyle = col; g.fillText((win ? '+$' : '-$') + Math.abs(Math.round(pnlOf(x))) + ' · ' + reasonTxt(x) + (x.r != null ? ' · ' + x.r + 'R' : ''), xx + 8, yx + (win === up ? -12 : 12));
        }
      });
      g.globalAlpha = 1;
    }
    const fmtT = e => new Date(e * 1000).toISOString().slice(5, 16).replace('T', ' ');
    function renderList() {
      const box = $('#tlist'); if (!box) return;
      if (!trades || !trades.trades.length) { box.innerHTML = ''; return; }
      const L = vis(), cols = t('tl_cols');
      const fb = (k, label) => `<button data-f="${k}" class="${tfilter === k ? 'on' : ''}">${label}</button>`;
      box.innerHTML = `<div class="bar">${fb('all', t('tl_all'))}${fb('win', t('tl_win'))}${fb('loss', t('tl_loss'))}${fb('long', t('tl_long'))}${fb('short', t('tl_short'))}
        <button data-n="-1">${t('tl_prev')}</button><button data-n="1">${t('tl_next')}</button><span>${t('tl_of', { i: Math.max(0, L.findIndex(x => x.i === focus) + 1), n: L.length })}</span>
        <select id="tsize">${[['base', t('tl_size_base', { n: trades.base_contracts })]].concat(Object.entries(trades.sizing || {}).filter(([, v]) => v).map(([a, v]) => [a, t('tl_size_rec', { a, n: v })])).map(([k, l]) => `<option value="${k}" ${k === tsize ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <button data-csv="1">${t('tl_csv')}</button></div>
        <table><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>${L.map((x, k) => `<tr class="tr ${x.i === focus ? 'sel' : ''}" data-i="${x.i}"><td>${k + 1}</td><td>${fmtT(x.entry_t)}</td><td class="${x.side > 0 ? 'up' : 'dn'}">${x.side > 0 ? 'L' : 'S'}</td><td>${x.entry_px}</td><td><b>${nC()}</b></td><td>${x.exit_px}</td><td>${reasonTxt(x)}</td><td class="${x.pnl >= 0 ? 'up' : 'dn'}">${x.pnl >= 0 ? '+' : '-'}$${Math.abs(Math.round(pnlOf(x)))}</td><td>${x.r == null ? '' : x.r}</td></tr>`).join('')}</table>`;
      box.querySelector('#tsize').onchange = (e) => { tsize = e.target.value; renderList(); drawOverlay(); };
      box.querySelectorAll('[data-f]').forEach(b => b.onclick = () => { tfilter = b.dataset.f; focus = -1; renderList(); drawOverlay(); });
      box.querySelectorAll('[data-n]').forEach(b => b.onclick = () => { const k = L.findIndex(x => x.i === focus); const n = L[(k + +b.dataset.n + L.length) % L.length]; if (n) focusTrade(n.i); });
      box.querySelectorAll('tr.tr').forEach(r => r.onclick = () => focusTrade(+r.dataset.i));
      box.querySelector('[data-csv]').onclick = () => {
        const rows = [['entry_time_utc', 'side', 'contracts', 'entry_px', 'exit_px', 'reason', 'pnl', 'r', 'stop', 'target']].concat(L.map(x => [new Date(x.entry_t * 1000).toISOString(), x.side > 0 ? 'long' : 'short', nC(), x.entry_px, x.exit_px, x.reason, Math.round(pnlOf(x) * 100) / 100, x.r == null ? '' : x.r, x.stop || '', x.target || '']));
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([rows.map(r => r.join(',')).join('\n')], { type: 'text/csv' })); a.download = (slug || 'trades') + '.csv'; a.click();
      };
      const sel = box.querySelector('tr.sel'); if (sel) sel.scrollIntoView({ block: 'nearest' });
    }
    async function focusTrade(i) {
      focus = i; const x = trades.trades[i];
      const day = new Date(x.entry_t * 1000); day.setUTCDate(day.getUTCDate() + 2);
      if (!loaded.length || typeof loaded[0][0] === 'string' || x.entry_t < loaded[0][0] || x.exit_t > loaded[loaded.length - 1][0]) {
        before = day.toISOString().slice(0, 10); rng = '5D'; tf = order.includes(trades.tf) && trades.tf !== '1D' ? trades.tf : '15min'; draw(); await load();
      }
      const pad = Math.max(3600 * 3, (x.exit_t - x.entry_t) * 2);
      try { chart.timeScale().setVisibleRange({ from: x.entry_t - pad, to: x.exit_t + pad }); } catch (e) {}
      renderList(); drawOverlay();
    }
    function note() {
      let h = '';
      if (trades) {
        const s = trades.summary;
        h += s.n ? `<div><b>${esc(trades.name)}</b> — ${t('t_trades', { n: s.n, w: pct(s.win_rate), p: money(s.pnl) })}${trades.exit ? ' · ' + t('rr_short', { s: trades.exit.stop_atr, r: trades.exit.rr == null ? t('rr_none') : '1:' + trades.exit.rr }) : ''}</div>` : `<div>${t('t_notrades')}</div>`;
        if (trades.n_hidden) h += `<div>${t('t_hidden', { n: trades.n_hidden })}</div>`;
        h += `<div>${t('t_legend')}</div>`;
      }
      h += `<div>${t('t_note', { s: symbol })}</div>`;
      $('#tnote').innerHTML = h; renderList();
    }
    // the indicator lines the selected strategy really uses (EMAs, channels, bands, opening range, VWAP)
    async function drawIndicators() {
      indSeries.forEach(x => { try { chart.removeSeries(x); } catch (e) {} }); indSeries = [];
      const lg = $('#tlegend'); lg.innerHTML = '';
      if (!slug || !loaded.length) return;
      if (typeof loaded[0][0] === 'string') { lg.textContent = t('ind_daily'); return; }
      const d = await api(`/api/indicators/${encodeURIComponent(slug)}?from=${loaded[0][0]}&to=${loaded[loaded.length - 1][0] + 86400}`);
      if (!d.series || !d.series.length) return;
      const cols = [THEME.accent, '#9a9aa2', THEME.warn, '#c9c9d0', '#6d6d75'];
      let html = '';
      d.series.forEach((s, i) => {
        const dashed = /sigma|zone/i.test(s.name), col = cols[i % cols.length];
        const ls = chart.addLineSeries({ color: col, lineWidth: dashed ? 1 : 2, lineStyle: dashed ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        ls.setData(s.points.map(([tt, v]) => v === null ? { time: tt } : { time: tt, value: v }));
        indSeries.push(ls); html += `<span style="color:${col}">━</span> ${esc(s.name)} &nbsp; `;
      });
      lg.innerHTML = `<div><b>${t('ind_title')}:</b> ${html}<span style="color:var(--dim)">(${t('ind_tf', { tf: d.tf })})</span></div>` + (d.help && d.help[LANG] ? `<div style="color:var(--dim)">${esc(d.help[LANG])}</div>` : '');
    }
    let bumpNote = null;
    async function load() {
      ensure(); fit();
      const days = RNG.find(r => r[0] === rng)[1];
      $('#tinfo').textContent = t('t_loading');
      const d = await api(`/api/candles?tf=${tf}&days=${days}` + (before ? `&before=${before}` : '') + (market ? `&market=${market}` : ''));
      if (d.error === 'too_many') {                       // too many candles: step up to a coarser timeframe
        const next = order[order.indexOf(tf) + 1]; if (!next) { $('#tinfo').textContent = t('t_err', { e: d.error }); return; }
        bumpNote = { a: tf, b: next }; tf = next; draw(); return load();
      }
      if (d.error) { $('#tinfo').textContent = t('t_err', { e: d.error }); return; }
      symbol = d.symbol; loaded = d.candles;
      cs.setData(d.candles.map(c => ({ time: c[0], open: c[1], high: c[2], low: c[3], close: c[4] })));
      vs.setData(d.candles.map(c => ({ time: c[0], value: c[5], color: c[4] >= c[1] ? THEME.up + '40' : THEME.down + '40' })));
      chart.timeScale().fitContent(); applyMarkers(); drawIndicators();
      last = `${symbol} ${tf} · ` + t('t_sessions', { n: d.n_sessions, a: d.from, b: d.to }); $('#tinfo').textContent = last; note();
      if (bumpNote) { $('#tinfo').textContent = last + ' · ' + t('t_bumped', bumpNote); bumpNote = null; }
    }
    async function pick(s) {
      slug = s; trades = null; focus = -1; tfilter = 'all'; tsize = 'base';
      if (!s) { applyMarkers(); drawIndicators(); note(); return; }
      const d = await api('/api/trades/' + encodeURIComponent(s));
      if (d.error) { note(); return; }
      trades = d;
      if (d.market && d.market !== market) { market = d.market; $('#tmarket').value = market; }
      const lastTrade = d.trades.length ? d.trades[d.trades.length - 1].session : null;
      if (lastTrade) {                                    // show the most recent trades, on the strategy's own timeframe
        const dt = new Date(lastTrade + 'T00:00:00Z'); dt.setUTCDate(dt.getUTCDate() + 1);
        before = dt.toISOString().slice(0, 10); rng = '1M'; tf = order.includes(d.tf) ? d.tf : '15min';
        draw(); await load();
      } else { applyMarkers(); drawIndicators(); note(); }
    }
    $('#tstrat').onchange = (e) => pick(e.target.value);
    $('#tmarket').onchange = (e) => { market = e.target.value; last = null; load(); };
    function setMarkets(list, primary) {
      if (!list || JSON.stringify(list) === JSON.stringify(markets)) return;
      markets = list; if (!market) market = primary;
      $('#tmarket').innerHTML = list.map(m => `<option value="${m.sym}">${esc(m.name)} (${m.micro})</option>`).join(''); $('#tmarket').value = market;
      $('#tmwrap').style.display = list.length > 1 ? '' : 'none';
    }
    function setStrategies(list) {
      const key = list.map(x => x.slug).join('|'); if (key === listKey) return; listKey = key;
      $('#tstrat').innerHTML = `<option value="">${t('t_none')}</option>` + list.map(x => `<option value="${esc(x.slug)}">${esc(x.name)} · ${t('v_' + x.status)}</option>`).join('');
      $('#tstrat').value = slug;
    }
    return { show() { draw(); ensure(); setTimeout(() => { fit(); if (!last) load(); }, 30); }, fit, setStrategies, setMarkets, set symbol(v) { symbol = v; }, get symbol() { return symbol; } };
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
  function timingBox(tm) {
    if (!tm) return '';
    const rows = Object.entries(tm.accounts).map(([acct, r]) => {
      const w = String(tm.window_days), pw = r.p_pass_within[w];
      return `<tr><th>${esc(acct)}</th><td>${r.n_micro}</td><td><b>${r.min_days_seen == null ? '–' : r.min_days_seen}</b></td><td>${r.median_days == null ? '–' : r.median_days.toFixed(0)}</td>
        <td>${r.p10_days == null ? '–' : r.p10_days.toFixed(0) + '–' + r.p90_days.toFixed(0)}</td><td>${r.hist_min_days == null ? '–' : r.hist_min_days}</td>
        <td class="${r.within_window_ok ? 'pos' : 'neg'}">${pct(pw)} ${r.within_window_ok ? '✔' : '✘'}</td></tr>`;
    }).join('');
    return `<div class="judge"><b>${t('tm_title')}</b><div class="sub">${tm.in_sample ? t('tm_insample') : t('tm_oos')} ${t('tm_goal', { d: tm.window_days, p: pct(tm.min_pass_rate) })}</div>
      <table class="rrt"><tr><th></th><th>${t('tm_size')}</th><th>${t('tm_min')}</th><th>${t('tm_med')}</th><th>${t('tm_range')}</th><th>${t('tm_hist')}</th><th>${t('tm_within', { d: tm.window_days })}</th></tr>${rows}</table>
      <div class="sub">${t('tm_note')}</div></div>`;
  }
  function tfBox(bt) {
    if (!bt || !bt.timeframes) return '';
    const names = Object.keys(bt.timeframes), accts = [...new Set(names.flatMap(k => Object.keys(bt.timeframes[k].pass_rate || {})))];
    const rows = names.map(k => {
      const v = bt.timeframes[k], best = k === bt.best_tf, md = a => v.median_days && v.median_days[a] != null ? v.median_days[a].toFixed(0) : '–';
      return `<tr class="${best ? 'best' : ''}"><th>${esc(k)}${best ? ' ★' : ''}</th><td><b>${(v.score * 100).toFixed(0)}</b></td><td>${v.sharpe.toFixed(3)}</td><td class="${v.net_pnl >= 0 ? 'pos' : 'neg'}">${money(v.net_pnl)}</td>
        <td>${money(v.max_dd)}</td><td>${v.profit_dd == null ? '–' : v.profit_dd.toFixed(2)}</td><td>${v.trades_per_day.toFixed(1)}</td><td>${pct(v.days_with_trade)}</td><td>${pct(v.trade_win_rate)}</td><td>${pct(v.positive_days)}</td>
        ${accts.map(a => `<td>${v.pass_rate[a] == null ? '–' : pct(v.pass_rate[a])}</td>`).join('')}${accts.map(a => `<td>${md(a)}</td>`).join('')}<td>${esc(Object.entries(v.best_params).map(([a, b]) => a + '=' + b).join(' '))}</td></tr>`;
    }).join('');
    return `<div class="judge"><b>${t('tfs_title')}</b><div class="sub">${t('tfs_sub', { tf: esc(bt.best_tf || '–') })}</div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th></th><th>${t('tfs_score')}</th><th>Sharpe</th><th>P&L</th><th>${t('tfs_dd')}</th><th>${t('tfs_pdd')}</th><th>${t('tfs_tpd')}</th><th>${t('tfs_dwt')}</th><th>${t('tfs_wr')}</th><th>${t('tfs_pos')}</th>
      ${accts.map(a => `<th>${t('tfs_pass', { a, d: bt.window_days })}</th>`).join('')}${accts.map(a => `<th>${t('tfs_med_days')} ${a}</th>`).join('')}<th>${t('tfs_params')}</th></tr>${rows}</table></div>
      <div class="sub">${t('tfs_note')}</div></div>`;
  }
  function sizingBox(sz) {
    if (!sz || !sz.accounts) return '';
    const cols = t('sz_cols'), blocks = Object.entries(sz.accounts).map(([acct, a]) => {
      const head = cols.map(c => `<th>${esc(c.replace('{d}', sz.window_days))}</th>`).join('');
      const rows = a.table.map(r => `<tr class="${r.micro === a.recommended ? 'best' : ''} ${r.within_limits ? '' : 'dim'}"><th>${r.micro}${r.micro === a.recommended ? ' ★' : ''}</th><td class="${r.net_pnl >= 0 ? 'pos' : 'neg'}">${money(r.net_pnl)}</td><td>${money(r.max_dd)}</td><td>${r.profit_dd == null ? '–' : r.profit_dd.toFixed(2)}</td><td>${pct(r.dd_use)}</td><td>${money(r.worst_day)}</td><td>${pct(r.p_pass_window)}</td><td>${pct(r.p_bust_window)}</td></tr>`).join('');
      return `<div class="sub"><b>${esc(acct)}</b> — ${a.recommended ? t('sz_rec') + ': <b>' + a.recommended + ' micro</b>' : t('sz_none')}</div><div style="overflow-x:auto"><table class="rrt"><tr>${head}</tr>${rows}</table></div>`;
    }).join('');
    return `<div class="judge"><b>${t('sz_title')}</b><div class="sub">${sz.in_sample ? t('tm_insample') : t('tm_oos')} ${t('sz_sub', { dd: pct(sz.dd_headroom), dy: pct(sz.day_headroom) })}</div>${blocks}</div>`;
  }
  function rrBox(ex) {
    if (!ex) return '';
    const stops = [...new Set(ex.grid.map(c => c.stop_atr))], rrs = [...new Set(ex.grid.map(c => c.rr))];
    const cell = (s, r) => ex.grid.find(c => c.stop_atr === s && c.rr === r);
    const max = Math.max(...ex.grid.map(c => Math.abs(c.sharpe))) || 1;
    const best = ex.best;
    const rows = stops.map(s => `<tr><th>${s}× ATR</th>${rrs.map(r => { const c = cell(s, r), a = Math.min(1, Math.abs(c.sharpe) / max);
      const bg = c.sharpe >= 0 ? `rgba(80,200,120,${0.12 + 0.5 * a})` : `rgba(240,90,90,${0.12 + 0.5 * a})`;
      const isBest = ex.chosen && best.stop_atr === s && best.rr === r;
      return `<td style="background:${bg};${isBest ? 'outline:2px solid #fff;' : ''}" title="${t('rr_cell', { w: pct(c.win_rate), n: c.n_trades, p: money(c.pnl) })}">${num(c.sharpe, 3)}</td>`; }).join('')}</tr>`).join('');
    const chosen = ex.chosen ? t('rr_chosen', { s: best.stop_atr, r: best.rr == null ? t('rr_none') : '1:' + best.rr })
      : t('rr_not_chosen');
    return `<div class="judge rrbox"><b>${t('rr_title')}</b><div class="sub">${chosen}</div>
      <table class="rrt"><tr><th></th>${rrs.map(r => `<th>${r == null ? t('rr_none') : 'R ' + r}</th>`).join('')}</tr>${rows}</table>
      <div class="sub">${t('rr_note', { b: num(ex.baseline.sharpe, 3), f: pct(ex.frac_better_than_baseline), n: ex.n_trials })}</div></div>`;
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
      ${tfBox(rep.by_timeframe)}
      ${sizingBox(rep.locked && rep.locked.sizing ? rep.locked.sizing : rep.sizing)}
      ${timingBox(rep.locked && rep.locked.timing ? rep.locked.timing : rep.timing)}
      ${rrBox(rep.exits)}
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
      $('#warn').innerHTML = cur === 'dev_best' ? `<div class="warnbox">${t('warn_dev')}</div>` : cur === 'locked' ? `<div class="warnbox" style="color:var(--dim)">${t('warn_lk')}</div>` : '';
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

  // ---------- Scout: autonomous literature search ----------
  let scoutKey = '';
  function renderScout(sc) {
    const key = JSON.stringify([sc.channels, sc.running, sc.auto, sc.last_run, sc.papers.map(p => p.id + p.status), (sc.manual_ssrn || []).length, Math.floor((sc.next_run || 0) / 60)]);
    if (key === scoutKey) return; scoutKey = key;
    const mins = sc.next_run ? Math.max(0, Math.round((sc.next_run - Date.now() / 1000) / 60)) : null;
    const st = { downloaded: 'sp_downloaded', skipped: 'sp_skipped', failed: 'sp_failed', 'no open PDF': 'sp_nopdf', manual: 'sp_manual', abstract: 'sp_abstract' };
    $('#scout').innerHTML = `<div class="srow"><button class="btn small" id="scout-go" ${sc.running ? 'disabled' : ''}>${sc.running ? t('scout_running') : t('scout_now')}</button>
      <label>${t('scout_auto')} <select id="scout-auto">${[0, 30, 60, 180, 360].map(m => `<option value="${m}" ${(m === 0 ? !sc.auto.enabled : sc.auto.enabled && sc.auto.minutes === m) ? 'selected' : ''}>${m === 0 ? t('scout_off') : t('scout_every', { m })}</option>`).join('')}</select></label></div>
      <div class="smeta">${t('scout_topics', { a: sc.queries_done, b: sc.queries_total })}${sc.last_run ? ' · ' + t('scout_last', { f: sc.last_run.found, d: sc.last_run.downloaded }) : ''}${mins !== null && sc.auto.enabled ? ' · ' + t('scout_next', { m: mins }) : ''}</div>
      <div class="smeta">${t('scout_channels')}: ${(sc.channels || []).join(', ')}</div>
      <div class="spapers">${sc.papers.length ? sc.papers.slice(0, 6).map(p => `<div title="${esc(p.why || '')}"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc((p.title || '').slice(0, 60))}</a><i class="${p.status === 'downloaded' || p.status === 'abstract' ? 'ok' : p.status === 'manual' ? 'man' : ''}">${t(st[p.status] || 'sp_skipped')}</i></div>`).join('') : `<div class="empty">${t('scout_none')}</div>`}</div>
      ${(sc.manual_ssrn || []).length ? `<div class="smeta"><b>${t('ssrn_manual')}</b></div><div class="spapers">${sc.manual_ssrn.slice(0, 5).map(p => `<div><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc((p.title || '').slice(0, 70))}</a></div>`).join('')}</div>` : ''}
      <div class="smeta">${t('scout_note')}</div>`;
    $('#scout-go').onclick = async () => { await api('/api/scout/run', { method: 'POST', json: { lang: LANG } }); scoutKey = ''; poll(); };
    $('#scout-auto').onchange = async (e) => { const m = +e.target.value; await api('/api/scout/auto', { method: 'POST', json: { enabled: m > 0, minutes: m || 60 } }); scoutKey = ''; poll(); };
  }

  // ---------- live web feed: where the Researcher is searching / reading right now ----------
  let webKey = '';
  function renderHealth(d) {
    const h = d.health || {}, ar = d.agent_reach || {};
    const row = n => { const x = h[n];
      if (!x) return `<span class="hs na">${esc(n)}: ${t('hl_untested')}</span>`;
      if (x.last_error) return `<span class="hs bad" title="${esc(x.last_error)}">${esc(n)}: ✘ ${esc(x.last_error.slice(0, 60))}</span>`;
      return `<span class="hs ok">${esc(n)}: ✔ ${x.last_n == null ? '' : x.last_n + ' ' + t('web_found')}</span>`; };
    const cfgErr = d.config_error ? `<div class="hs bad">${esc(d.config_error)}</div>` : '';
    const miss = (d.missing || []).map(m => `<div class="hs bad">${esc(m.name)}: ${t('hl_missing')} — <code>${esc(m.hint)}</code></div>`).join('');
    const arT = ar.installed === null || ar.installed === undefined ? t('hl_ar_check') : ar.installed ? t('hl_ar_yes') : t('hl_ar_no');
    const html = cfgErr + `<div class="hline">${(d.channels || []).map(row).join('')}</div>${miss}<div class="hs na">Agent Reach: ${arT}</div>`;
    if ($('#webhealth').dataset.k !== html) { $('#webhealth').dataset.k = html; $('#webhealth').innerHTML = html; }
  }
  async function pollWeb() {
    let d; try { d = await api('/api/web'); } catch (e) { return; }
    $('#web-dot').className = 'wdot' + (d.running ? ' on' : '');
    renderHealth(d);
    const key = d.total + '|' + d.running; if (key === webKey) return; webKey = key;
    const box = $('#webfeed'), atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    const link = (u, txt) => /^https?:\/\//.test(u || '') ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(txt)}</a>` : esc(txt);
    box.innerHTML = d.items.length ? d.items.map(x => {
      const when = (x.t || '').slice(11, 19), who = `<b>${esc(x.src)}</b>`;
      const body = x.phase === 'search' ? `🔎 ${t('web_search')}: ${esc(x.query || x.msg)}`
        : x.phase === 'found' ? `📄 ${x.n} ${t('web_found')}: ${esc(x.query || '')}` + (x.items || []).map(i => `<small>${link(i.url, i.title || i.url)}</small>`).join('')
        : x.phase === 'read' ? `📖 ${t('web_read')}: ${link(x.url, x.msg || x.url)}`
        : x.phase === 'saved' ? `💾 ${t('web_saved')}: ${link(x.url, x.msg || x.url)}` : `⚠ ${esc(x.msg)}`;
      return `<div class="w ${x.phase}">${when} ${who} ${body}</div>`;
    }).join('') : `<div class="w">${t('web_empty')}</div>`;
    const big = $('#webbig'); if (big && !big.hidden) { $('#webbigbody').innerHTML = box.innerHTML; $('#webbigbody').scrollTop = $('#webbigbody').scrollHeight; $('#webbighealth').innerHTML = $('#webhealth').innerHTML; }
    if (atEnd) box.scrollTop = box.scrollHeight;
  }
  $('#webexpand').onclick = () => { $('#webbig').hidden = false; webKey = ''; pollWeb(); };
  $('#webclose').onclick = () => { $('#webbig').hidden = true; };
  setInterval(pollWeb, 2000); pollWeb();

  // ---------- agents at work ----------
  const ROWS = [['research', 'Researcher + Scout'], ['strategist', 'Strategist'], ['optimizer', 'Optimizer + Planner'], ['validator', 'Validator'], ['judge', 'Judge + Auditor']];
  if (window.LabScene) LabScene.setStateLabels({ idle: t('st_idle'), standby: t('st_standby'), working: t('st_working'), done: t('st_done') });
  function renderAgents() {
    if (!window.LabScene) return;
    const st = LabScene.states(), cls = { idle: 'i', standby: 's', working: 'w', done: 'd' };
    $('#agents').innerHTML = ROWS.map(([k, n]) => `<div><span>${n}</span><span class="${cls[st[k]]}">${st[k] === 'done' ? '✓ ' : ''}${t('st_' + st[k])}</span></div>`).join('');
  }
  setInterval(renderAgents, 500); renderAgents();

  // ---------- accounts (evaluation / funded) as flasks + list ----------
  async function loadAccounts() {
    try {
      const d = await api('/api/accounts'), L = d.accounts;
      const stTxt = (a) => t('as_' + a.state);
      const full = L.map(a => [`${a.firm} ${a.plan} · ${a.kind === 'funded' ? t('acct_funded') : t('acct_eval')}${d.demo ? ' · ' + t('acct_demo') : ''}`,
        `${money(a.balance)} · ${stTxt(a)}${a.kind === 'funded' ? ' · ' + t('acct_payouts', { n: a.payouts }) : a.progress != null ? ' · ' + t('acct_prog', { p: pct(a.progress) }) : ''}`]);
      const short = L.map(a => [`${a.firm} ${a.plan}`, `${a.kind === 'funded' ? t('acct_funded') : t('acct_eval')} · ${money(a.balance)}`]);   // compact: fits above the flask
      const texts = full;
      if (window.LabScene) LabScene.setAccounts(L, short, d.demo ? t('acct_sign') : t('acct_sign_real'));
      $('#accts').innerHTML = (d.demo ? `<div class="tnote" style="margin:0 0 6px">${t('acct_demo_note')}</div>` : '') + L.map((a, i) => {
        const v = a.kind === 'evaluation' && a.progress != null ? a.progress : a.health;
        return `<div class="ac st-${a.state}"><div><b>${esc(texts[i][0])}</b><small>${esc(texts[i][1])} · ${t('acct_buf', { p: pct(a.health) })}</small></div><div class="bar"><i style="width:${Math.round(v * 100)}%"></i></div></div>`;
      }).join('');
    } catch (e) {}
  }
  loadAccounts(); setInterval(loadAccounts, 30000);

  // ---------- brand logo (optional file: brand/icon.png) ----------
  const li = $('#logo-img');
  li.onload = () => { li.style.display = 'block'; $('#logo-cube').style.display = 'none'; const f = document.querySelector('link[rel=icon]'); if (f) f.href = 'brand/icon.png'; };
  li.onerror = () => { li.remove(); };
  if (li.complete && li.naturalWidth) li.onload();

  poll(); pollEvents();
  setInterval(poll, 3000); setInterval(pollEvents, 1500);
})();
