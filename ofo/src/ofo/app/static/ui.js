/* OFO Lab - interface logic (Italian / English) */
(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const loc = LANG === 'en' ? 'en-US' : 'it-IT';
  const money = (v) => v == null ? '–' : (v < 0 ? '-$' : '$') + Math.abs(Math.round(v)).toLocaleString(loc);
  const num = (v, d = 2) => v == null ? '–' : Number(v).toFixed(d);
  const pct = (v) => v == null ? '–' : (v * 100).toFixed(0) + '%';
  const AGENTS = { researcher: 'Researcher', strategist: 'Strategist', you: LANG === 'en' ? 'You' : 'Tu', optimizer: 'Optimizer', planner: 'Planner', validator: 'Validator', judge: 'Judge', auditor: 'Auditor', lab: 'Lab', market_observer: 'Market Observer', structure_engine: 'Structure Engine', behavior_analyzer: 'Behavior Analyzer', obsidian_memory: 'Obsidian Memory', research_director: 'Research Director' };
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
  if (window.LabScene) LabScene.onPick = (st, key) => { if (key) openInspector(key); };
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
  let bubOn = store.get('ofo_bubbles_all', false);                   // false: only important messages get a bubble
  $('#bh').onclick = () => { bubOn = !bubOn; store.set('ofo_bubbles_all', bubOn); $('#bh').style.opacity = bubOn ? 1 : .45; if (window.LabScene) LabScene.setBubbles(bubOn); };
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
  const evCache = [];
  function addFeed(e) {
    evCache.push(e); if (evCache.length > 500) evCache.shift();
    const d = document.createElement('div'); d.className = 'ev ' + e.agent;
    d.innerHTML = `<small>${esc(AGENTS[e.agent] || e.agent)} · ${esc(e.type)} · ${esc((e.t || '').slice(11, 19))}</small>${esc(e.message)}`;
    const f = $('#feed'); f.insertBefore(d, f.firstChild); while (f.children.length > 150) f.removeChild(f.lastChild);
  }
  function noteEvent(e) {
    if (e.type === 'need_pdf') { needPdf(e); notify('left'); }
    if (e.type === 'alert') { toast(`❗ <b>${esc(AGENTS[e.agent] || e.agent)}</b><div>${esc(e.message)}</div>`, 20000); notify('left'); }
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
    } catch (e) { console.error("OFO UI", e); }
  }

  // ---------- state ----------
  let lastState = null, lastAcct = [];
  function renderState(s) {
    lastState = s;
    if (s.build) { const c = $('#credit'); if (c && !c.dataset.b) { c.dataset.b = 1; c.textContent = 'build ' + s.build + ' · ' + c.textContent; } }
    $('#c-trials').textContent = s.trials; $('#c-looks').textContent = s.locked_looks;
    $('#c-calls').textContent = s.calls_last_minute == null ? '–' : s.calls_last_minute + '/' + (s.calls_per_minute ?? '–');
    $('#c-ai').textContent = s.ai ? t('ai_on') : t('ai_off'); $('#c-ai-chip').className = 'chip ' + (s.ai ? 'ok' : 'bad');
    $('#ideas').innerHTML = s.ideas.length ? s.ideas.map(i => `<div><span>${esc(i.name)}</span><span>${i.read ? '<i>' + t('read_ok') + '</i>' : i.kb + ' KB'}</span></div>`).join('') : `<div class="empty">${t('no_files')}</div>`;
    $('#n-strat').textContent = s.pending.length || ''; $('#n-strat').style.display = s.pending.length ? 'inline' : 'none';
    const pend = $('#pending'); pend.innerHTML = s.pending.length ? `<h2>${t(s.autopilot && s.autopilot.enabled ? 'pending_auto' : 'pending_title')}</h2>` : '';
    s.pending.forEach(sp => { const o = document.createElement('div'); o.style.cssText = 'color:var(--dim);font-size:10px;margin-bottom:2px'; o.textContent = t('origin') + ': ' + t('o_' + sp.origin) + (sp.market ? ' · ' + sp.market : ''); pend.appendChild(o); pend.appendChild(specCard(sp)); });
    $('#jobs').innerHTML = s.jobs.filter(j => j.status === 'queued' || j.status === 'running').map(j => `<div class="card"><span class="spin"></span>${t('job_run', { n: '<b>' + esc(j.name) + '</b>', c: j.n_candidates })}
      <div class="meta">${j.status === 'queued' ? t('job_wait', { why: esc(engineWhy(s.engine)) }) : t('job_stage', { s: t('stg_' + (j.stage || 'starting')), d: esc(j.stage_detail || '') })}</div></div>`).join('')
      + s.jobs.filter(j => j.status === 'error').slice(-1).map(j => `<div class="card" style="border-color:var(--down)">${t('job_err', { n: esc(j.name), e: esc(j.error) })}</div>`).join('');
    $('#studies').innerHTML = `<h2>${t('studies_title')}</h2>` + (s.strategies.length ? s.strategies.map(x => `<div class="row" data-slug="${esc(x.slug)}"><div><b>${esc(x.name)}</b><small>${esc(x.template)} · ${x.n_candidates} ${t('combos')} · ${t('med_sharpe')} ${num(x.median_sharpe, 3)} · ${x.robust ? t('robust') : t('fragile')}${x.role ? ' · ' + t('ro_' + x.role) : ''}</small></div><span class="badge b-${x.status}">${t('v_' + x.status)}</span><a class="pdf-dl" href="/api/pdf/${encodeURIComponent(x.slug)}" download title="PDF" onclick="event.stopPropagation()">PDF</a></div>`).join('') : `<div class="empty">${t('none_yet')}</div>`);
    $('#studies').querySelectorAll('.row').forEach(r => r.onclick = () => openDetail(r.dataset.slug));
    $('#reqs').innerHTML = s.template_requests.length ? `<h2>${t('reqs_title')}</h2>` + s.template_requests.map(r => `<div class="card"><b>${esc(r.name)}</b><div class="meta">${esc(r.when)}</div>${esc(r.description)}</div>`).join('') : '';
    if (window.LabScene) LabScene.setTrader(s.strategies.filter(x => x.status === 'APPROVED').map(x => x.name), { wait: t('tr_wait'), desk: t('tr_desk'), ready: t('tr_ready'), head: t('tr_head'), none: t('tr_none') });
    trader.symbol = s.symbol; trader.setMarkets(s.markets, s.primary); trader.setStrategies(s.strategies);
    renderFasc(s.fascicoli || []); renderResearcher(s.researcher);
    const rk = s.strategies.map(x => x.slug + x.status).join('|') + (s.farming_pending || 0); if (rk !== rankKey) { rankKey = rk; loadRanking(); }
    farmPending(s.farming_pending || 0);
  }
  async function poll() { try { const s = await api('/api/state'); renderState(s); if (s.scout) renderScout(s.scout); if (s.autopilot) renderAutopilot(s.autopilot); if (s.obsidian) renderObsidian(s.obsidian); renderBudget(s.budget); } catch (e) { console.error("OFO UI", e); } }

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
      <div class="apm">${b.daily_limit === false ? t('bud_today_free', { s: b.spent_today_usd.toFixed(3) }) : t('bud_today', { s: b.spent_today_usd.toFixed(3), c: b.autonomous_cap_usd.toFixed(2), p: pctDay })} · ${(b.tokens_today / 1000).toFixed(0)}k tok</div>
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
    const ws = a.work_state || {};
    const key = JSON.stringify([a.enabled, bits, a.paused, LANG, a.markets, ws.state, ws.why]);
    if (key === apKey) return; apKey = key;
    $('#autopilot').innerHTML = `<div class="${a.enabled ? 'apon' : 'apoff'}">${a.enabled ? t('ap_on') : t('ap_off')}</div><div class="apm">${bits.join(' · ')}</div>`
      + (ws.state && ws.state !== 'ACTIVE' ? `<div class="apm"><b>${esc(ws.state)}</b> — ${esc(ws.why || '')}</div>` : '')
      + (a.paused ? `<div class="apoff">${t('ap_paused_' + a.paused)}${budgetWhy(a.paused_info)}</div>` : '')
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
    } catch (e) { console.error("OFO UI", e); }
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
    } catch (e) { console.error("OFO UI", e); }
  }


  // ---------- prop farming: dossiers (fascicoli) on the trader's desk, ranking, farming box in the detail ----------
  const usd = x => x == null ? 'n/d' : (x < 0 ? '-$' : '$') + Math.abs(Math.round(x)).toLocaleString('it-IT');
  const gcls = st => ({ PASS: 'ok', FAIL: 'no', INSUFFICIENT: 'warn', PENDING: 'warn' }[st] || '');
  const gname = st => ({ PASS: t('g_pass'), FAIL: t('g_fail'), INSUFFICIENT: t('g_insuf'), PENDING: t('g_pending') }[st] || st);
  function gatesTable(gs) {
    return `<table class="rrt"><tr><th>#</th><th>${t('fc_gate')}</th><th>${t('fc_status')}</th><th>${t('fc_why')}</th></tr>${gs.map(g => `<tr><td>${g.id}</td><td>${esc(g.name)}</td><td class="${gcls(g.status)}"><b>${gname(g.status)}</b></td><td style="text-align:left">${esc(g.reason)}</td></tr>`).join('')}</table>`;
  }
  function farmBox(fr) {
    if (!fr || !fr.by_ruleset) return '';
    const rows = Object.entries(fr.by_ruleset).map(([id, x]) => { const e = x.evaluation, m = e.metrics, n = m.npfv;
      return `<tr class="${id === fr.best_ruleset ? 'best' : ''}"><th>${esc(id)}<br><small class="${e.ruleset.provenance === 'VERIFIED_RULESET' ? 'ok' : 'warn'}">${esc(e.ruleset.provenance)}</small></th><td>${t('v_' + x.gates.verdict)}</td><td>${e.micro}</td>
        <td><b>${usd(n.lcb95)}</b></td><td>${usd(n.mean)}</td><td>${usd(n.median)}</td><td>${pct(m.p_first_payout)}</td><td>${pct(m.p_pass)}</td><td>${m.p_funded_survival == null ? '–' : pct(m.p_funded_survival)}</td><td>${m.e_payout_count.toFixed(1)}</td><td>${usd(m.e_total_cost)}</td><td>${pct(m.p_account_failure)}</td></tr>`; }).join('');
    const best = fr.by_ruleset[fr.best_ruleset];
    return `<div class="judge"><b>${t('pf_title')}</b><div class="sub">${t('pf_sub')}</div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th>Ruleset</th><th>${t('fc_verdict')}</th><th>${t('pf_size')}</th><th>NPFV LCB95</th><th>NPFV ${t('pf_mean')}</th><th>${t('pf_median')}</th><th>P(1° payout)</th><th>P(pass)</th><th>P(${t('pf_survive')})</th><th>${t('pf_npay')}</th><th>${t('pf_cost')}</th><th>P(${t('pf_fail')})</th></tr>${rows}</table></div>
      ${best ? gatesTable(best.gates.gates) : ''}
      <div class="sub">${esc(best ? best.evaluation.limits : '')}</div></div>`;
  }
  let fascKey = '', fascSeen = null, folderList = null;
  function applyFolders() { if (folderList && window.LabScene && LabScene.setFolders) { LabScene.setFolders(folderList); folderList = null; } }
  setInterval(applyFolders, 1000);
  function renderFasc(list) {
    const key = JSON.stringify(list.map(x => [x.slug, x.decisione]));
    if (key === fascKey) return; fascKey = key;
    folderList = list; applyFolders();
    if (fascSeen !== null && list.length > fascSeen) { toast('📁 <b>' + t('fc_new') + '</b>', 8000); notify('right', 'strategies'); }
    fascSeen = list.length;
    const el = $('#fasclist');
    el.innerHTML = list.length ? `<h2>${t('fc_title')} (${list.length})</h2>` + list.map(x => `<div class="row" data-fasc="${esc(x.slug)}"><div><b>${esc(x.nome)}</b><small>${esc(x.ruleset)} · NPFV LCB95 ${usd(x.npfv_lcb95)} · ${x.decisione ? t('fc_dec_' + x.decisione) : t('fc_dec_none')}</small></div><span class="badge b-APPROVED">📁</span></div>`).join('') : '';
    el.querySelectorAll('[data-fasc]').forEach(r => r.onclick = () => openFasc(r.dataset.fasc));
  }
  const hookFolder = () => { if (window.LabScene) LabScene.onFolder = (slug) => openFasc(slug); };
  hookFolder(); setInterval(hookFolder, 1500);
  async function openFasc(slug) {
    const f = await api('/api/fascicolo/' + encodeURIComponent(slug)); if (f.error) return;
    let ov = $('#fasc'); if (!ov) { ov = document.createElement('div'); ov.id = 'fasc'; document.body.appendChild(ov); }
    const r = f.risultati, n = r.npfv, L = f.logica_ingresso, dec = f.decisione && f.decisione.decision;
    const kv = (k, v) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`;
    const stress = Object.entries(r.stress || {}).map(([k, v]) => `<tr><td style="text-align:left">${esc(k.replace(/_/g, ' '))}</td><td>${usd(v.npfv_mean)}</td><td>${pct(v.p_first_payout)}</td><td>${pct(v.p_account_failure)}</td></tr>`).join('');
    const pols = Object.entries(r.policies_dev || {}).map(([k, v]) => `<tr class="${k === r.policy ? 'best' : ''}"><td style="text-align:left">${k} — ${esc(v.label)}</td><td>${usd(v.npfv_lcb95)}</td><td>${usd(v.npfv_mean)}</td><td>${v.e_payout_count.toFixed(1)}</td><td>${pct(v.p_funded_failure)}</td></tr>`).join('');
    const pf = f.portafoglio ? Object.entries(f.portafoglio).map(([k, p]) => `<tr><td>${k}</td><td>${usd(p.total_capital_spent_mean)}</td><td>${usd(p.npfv_total_mean)}</td><td>${usd(p.npfv_total_p05)}</td><td>${usd(p.independent_assumption.npfv_total_p05)}</td><td>${pct(p.p_all_fail)}</td><td>${p.mean_accounts_failed.toFixed(1)}</td></tr>`).join('') : '';
    ov.innerHTML = `<div class="folder"><div class="ftab">${esc(f.nome)}</div><button class="fx" id="fasc-x">✕</button>
      <div class="fbody">
        <div class="fhead"><h1>${esc(f.nome)}</h1><span class="badge b-${f.stato}">${t('v_' + f.stato)}</span></div>
        <div class="fsub">${esc(f.mercato)} · ${t('fc_suited')}: <b>${esc((f.ruleset_adatti || []).join(', ') || '–')}</b> · ${esc(f.creato)} · ${esc(f.archetipo ? f.archetipo.label : '')}</div>
        ${(f.avvisi || []).map(a => `<div class="fwarn">⚠ ${esc(a)}</div>`).join('')}
        <h2>${t('fc_logic')}</h2>
        <p>${esc(L.descrizione)}</p>
        <div class="kvs">${kv(t('fc_tf'), esc(L.timeframe))}${kv(t('fc_dir'), esc(L.direzione))}${kv(t('fc_params'), esc(Object.entries(L.parametri).map(([a, b]) => a + '=' + b).join(', ') || '–'))}${kv(t('fc_exit'), esc(L.uscite))}${kv(t('fc_role'), esc(L.ruolo || '–'))}${kv(t('fc_window'), esc(L.finestra_oraria || '–'))}${kv(t('fc_cap'), esc(L.max_trade_giorno || '–'))}${kv(t('fc_size'), r.size_micro + ' micro')}${kv(t('fc_policy'), esc(r.policy + ' — ' + r.policy_label))}</div>
        <p><i>${esc(L.ipotesi)}</i></p><p class="fsmall">${esc(L.regole_comuni)}</p>
        <h2>${t('fc_results')}</h2>
        <div class="kvs">${kv('NPFV LCB95', '<span style="color:var(--up)">' + usd(n.lcb95) + '</span>')}${kv('NPFV ' + t('pf_mean'), usd(n.mean))}${kv(t('pf_median'), usd(n.median))}${kv('UCB95', usd(n.ucb95))}
          ${kv('P(1° payout)', pct(r.p_first_payout))}${kv('P(pass)', pct(r.p_pass))}${kv('P(' + t('pf_survive') + ')', r.p_funded_survival == null ? 'n/d' : pct(r.p_funded_survival))}${kv(t('fc_t1'), r.e_time_to_first_payout == null ? 'n/d' : r.e_time_to_first_payout.toFixed(0) + ' ' + t('fc_sess'))}
          ${kv(t('pf_npay'), r.e_payout_count.toFixed(1))}${kv('P(2° / 3° payout)', pct(r.p_second_payout) + ' / ' + pct(r.p_third_payout))}${kv(t('pf_cost'), usd(r.e_total_cost))}${kv(t('fc_costfirst'), usd(r.cost_per_first_payout))}
          ${kv(t('fc_eff'), r.payout_efficiency == null ? 'n/d' : r.payout_efficiency.toFixed(2) + '×')}${kv('P(' + t('pf_fail') + ')', pct(r.p_account_failure))}${kv(t('fc_es'), usd(r.npfv_es5))}${kv(t('fc_dd'), pct(r.drawdown_utilization))}</div>
        <div class="fsmall">${t('fc_unc')}</div>
        <h2>${t('fc_gates')}</h2>${gatesTable(f.barriere)}
        <h2>${t('fc_why_h')}</h2><ul>${f.perche.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
        <h2>${t('fc_stress')}</h2><table class="rrt"><tr><th></th><th>NPFV</th><th>P(1° payout)</th><th>P(${t('pf_fail')})</th></tr>${stress}</table>
        ${pols ? `<h2>${t('fc_pol')}</h2><table class="rrt"><tr><th></th><th>NPFV LCB95</th><th>NPFV</th><th>${t('pf_npay')}</th><th>P(${t('pf_fail')} funded)</th></tr>${pols}</table><div class="fsmall">${t('fc_pol_note')}</div>` : ''}
        <h2>${t('fc_inval')}</h2><ul>${f.condizioni_invalidanti.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
        ${pf ? `<h2>${t('fc_port')}</h2><table class="rrt"><tr><th>N</th><th>${t('fc_capital')}</th><th>NPFV ${t('pf_mean')}</th><th>NPFV p05</th><th>p05 (${t('fc_indep')})</th><th>P(${t('fc_allfail')})</th><th>${t('fc_nfail')}</th></tr>${pf}</table><div class="fsmall">${t('fc_port_note')}</div>` : ''}
        <h2>${t('fc_board')}</h2>${f.verbale.map(v => `<div class="say"><b>${esc(v.role)}</b> ${esc(v.text)}</div>`).join('')}
        <div class="fsmall">${esc(f.limiti)}</div>
        <div class="fdecide"><b>${t('fc_decide')}</b> ${dec ? '<span class="badge">' + t('fc_dec_' + dec) + '</span>' : ''}
          <textarea id="fasc-note" placeholder="${t('fc_note')}">${esc((f.decisione && f.decisione.note) || '')}</textarea>
          <button class="btn" data-d="live">🟢 ${t('fc_live')}</button><button class="btn" data-d="hold">🟡 ${t('fc_hold')}</button><button class="btn" data-d="discarded">⚪ ${t('fc_discard')}</button>
          <div class="fsmall">${t('fc_live_note')}</div></div>
      </div></div>`;
    ov.hidden = false;
    $('#fasc-x').onclick = () => { ov.hidden = true; };
    ov.onclick = (e) => { if (e.target === ov) ov.hidden = true; };
    ov.querySelectorAll('[data-d]').forEach(b => b.onclick = async () => {
      if (b.dataset.d === 'live') { const g7 = (f.barriere.find(g => g.id === 7) || {}).status; if (g7 !== 'PASS' && !confirm(t('fc_confirm_live'))) return; }
      await api('/api/fascicolo/decision', { method: 'POST', json: { slug: f.slug, decision: b.dataset.d, note: $('#fasc-note').value } });
      fascKey = ''; poll(); openFasc(f.slug); });
  }
  async function loadRanking(rid) {
    const d = await api('/api/ranking' + (rid ? '?ruleset=' + encodeURIComponent(rid) : '')), el = $('#ranking');
    if (!d.evaluated) { el.innerHTML = ''; return; }
    const rows = d.ranking.map((e, i) => `<tr><th>${i + 1}</th><td style="text-align:left">${esc(e.name)}${e.indistinguishable_from_top ? ' <small class="warn">≈</small>' : ''}</td><td><b>${usd(e.npfv.lcb95)}</b></td><td>${usd(e.npfv.mean)}</td><td>${pct(e.p_first_payout)}</td><td>${pct(e.p_pass)}</td><td>${e.p_funded_survival == null ? '–' : pct(e.p_funded_survival)}</td><td>${e.e_payout_count.toFixed(1)}</td><td>${e.e_time_to_first_payout == null ? '–' : e.e_time_to_first_payout.toFixed(0)}</td><td>${usd(e.e_total_cost)}</td><td>${pct(e.p_account_failure)}</td></tr>`).join('');
    el.innerHTML = `<h2>${t('rk_title')}</h2><div class="srow"><select id="rk-sel">${d.rulesets.map(r => `<option ${r === d.ruleset ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select><span class="smeta">${d.evaluated} ${t('rk_eval')} · ${d.ranking.length} ${t('rk_ok')}</span></div>
      ${d.ranking.length ? `<div style="overflow-x:auto"><table class="rrt"><tr><th>#</th><th></th><th>NPFV LCB95</th><th>NPFV</th><th>P(1° payout)</th><th>P(pass)</th><th>P(${t('pf_survive')})</th><th>${t('pf_npay')}</th><th>${t('fc_t1')}</th><th>${t('pf_cost')}</th><th>P(${t('pf_fail')})</th></tr>${rows}</table></div>` : ''}
      <div class="smeta">${esc(d.note)}</div>`;
    $('#rk-sel').onchange = (e) => loadRanking(e.target.value);
  }
  let rankKey = '';
  function engineWhy(e) {
    if (e && e.stuck) return t('job_wait_stuck', { w: e.stuck.where.slice().reverse().join(' ← ') || '?', s: e.stuck.secs });
    if (!e || !e.busy) return t('job_wait_free');
    const m = e.elapsed_s != null ? Math.max(0, Math.round(e.elapsed_s / 60)) : 0;
    if (e.kind === 'review') return t('job_wait_review', { n: e.name, m });
    if (e.kind === 'study') return t('job_wait_study', { n: e.name, s: e.stage ? t('stg_' + e.stage) : '', m });
    return t('job_wait_other');
  }
  async function loadResearch() {
    let d; try { d = await api('/api/research'); } catch (e) { return; }
    const el = $('#research'); if (!el || d.error) return;
    const caps = d.budget.caps, used = d.budget.used, bar = (u, c) => `<span class="bar"><i style="width:${Math.min(100, c ? 100 * u / c : 0)}%"></i></span> ${u}/${c}`;
    const stg = rcfgStages.map(s => `<tr><th>${t('rsr_st_' + s)}</th><td>${bar(used[s], caps[s])}</td><td>${(d.stage_flow[s] || {}).entered || 0}</td><td>${(d.stage_flow[s] || {}).passed || 0}</td></tr>`).join('');
    const fam = Object.entries(d.trials_by_family).map(([f, v]) => `<tr class="${v.status === 'blocked_no_data' ? 'dim' : ''}"><th>${esc(f)} <small>${esc(v.label)}</small></th><td>${v.trials}</td><td>${v.studies}</td><td>${v.survivors}</td><td>${v.validated}</td><td>${v.approved}</td><td>${v.rate == null ? '–' : (v.rate * 100).toFixed(0) + '%'}</td><td>${v.status === 'blocked_no_data' ? esc(t('rsr_blocked')) : ''}</td></tr>`).join('');
    const gt = d.elimination_by_gate.map(([g, n]) => `<div>${n} × ${esc(g)}</div>`).join('') || `<div class="smeta">–</div>`;
    const ng = d.recurring_negatives.map(([g, n]) => `<div>${n} × ${esc(g)}</div>`).join('') || `<div class="smeta">–</div>`;
    const pr = d.promising.map(x => `<div>${esc(x.name)} <small>${esc(x.family || '')} · ${esc(x.evidence)}${x.best_tf ? ' · ' + esc(x.best_tf) : ''}</small></div>`).join('') || `<div class="smeta">${t('rsr_none')}</div>`;
    el.innerHTML = `<h2>${t('rsr_title')}</h2><div class="smeta">${t('rsr_cfg')} ${esc(d.config_version)} · ${t('rsr_total', { u: d.total_budget.trials_registered, c: d.total_budget.cap })} · ${t('rsr_today', { s: d.today.studies, a: d.today.approved })}</div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th>${t('rsr_stage')}</th><th>${t('rsr_budget_today')}</th><th>${t('rsr_entered')}</th><th>${t('rsr_passed')}</th></tr>${stg}</table></div>
      <div class="smeta">${t('rsr_cost', { c: d.mean_cost_per_test_s == null ? '–' : d.mean_cost_per_test_s, s: d.mean_study_s })} · ${t('rsr_dups', { e: d.duplicates_avoided.exact, n: d.duplicates_avoided.near })} · ${t('rsr_flow', { q: d.in_queue, r: d.running, p: d.in_paper, a: d.approved })}</div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th>${t('rsr_family')}</th><th>${t('rsr_trials')}</th><th>${t('rsr_studies')}</th><th>${t('rsr_surv')}</th><th>${t('rsr_valid')}</th><th>${t('rsr_appr')}</th><th>${t('rsr_rate')}</th><th></th></tr>${fam}</table></div>
      <div class="two"><div><b>${t('rsr_elim')}</b>${gt}</div><div><b>${t('rsr_neg')}</b>${ng}</div></div>
      <div><b>${t('rsr_prom')}</b>${pr}</div><div class="smeta">${esc(d.evidence_note)}</div><div class="smeta">${esc(d.today.note)}</div>`;
  }
  const rcfgStages = ['hypothesis', 'screening', 'validation', 'robustness', 'audit'];
  setInterval(loadResearch, 30000); setTimeout(loadResearch, 1500);
  function farmPending(n) {
    const el = $('#farmreview'); if (!el) return;
    el.innerHTML = n ? `<div class="card"><b>${t('fr_title')}</b><div class="meta">${t('fr_sub', { n })}</div><button class="btn small" id="fr-go">${t('fr_go')}</button></div>` : '';
    if (n) $('#fr-go').onclick = async () => { await api('/api/farming/review', { method: 'POST', json: {} }); toast(t('fr_started'), 5000); };
  }

  // ---------- Trader tab: candlestick chart of the user's own historical data ----------
  trader = (function () {
    const TFS = [['1min', '1m'], ['5min', '5m'], ['15min', '15m'], ['30min', '30m'], ['60min', '1h'], ['1D', '1D']];
    const RNG = [['1D', 1, '1min'], ['5D', 5, '5min'], ['1M', 21, '30min'], ['3M', 63, '60min'], ['6M', 126, '1D'], ['1Y', 252, '1D'], ['ALL', 'all', '1D']];
    let tf = '15min', rng = '5D', chart = null, cs = null, vs = null, last = null, symbol = 'NQ', before = null;
    let tfilter = 'all', focus = -1, ov = null, tsize = 'base', tAcct = store.get('ofo_tacct', '50k'), tMicro = null; let trades = null, loaded = [], slug = '', listKey = '', indSeries = [], market = '', markets = [];
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
      cs = chart.addCandlestickSeries({ upColor: '#E7E9EC', downColor: '#44484D', borderUpColor: '#E7E9EC', borderDownColor: '#A6ABB1', borderVisible: true, wickUpColor: '#E7E9EC', wickDownColor: '#A6ABB1' });
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
    const nC = () => trades ? (trades.contracts || trades.base_contracts) : 1;      // the backtest is replayed AT this size (not rescaled)
    const pnlOf = x => x.pnl;
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
        if (sel || list.length <= 100) {                   // stop loss and take profit of EVERY trade in view, plus the trade box
          const lv = (p, c, tag) => { const y = Y(p); if (y === null) return; g.strokeStyle = c; g.setLineDash(sel ? [] : [5, 3]); g.lineWidth = sel ? 2 : 1.3; g.beginPath(); g.moveTo(xe, y); g.lineTo(Math.max(xx, xe + 24), y); g.stroke(); g.setLineDash([]);
            const lab = tag + ' ' + Number(p).toFixed(2), wl = g.measureText(lab).width + 8, lx = Math.max(xx, xe + 24) + 3; g.fillStyle = c; g.fillRect(lx, y - 7, wl, 14); g.fillStyle = '#000'; g.fillText(lab, lx + 4, y); };
          if (x.stop != null) lv(x.stop, THEME.down, 'SL'); if (x.target != null) lv(x.target, THEME.up, 'TP');
          g.fillStyle = col + '22'; g.fillRect(Math.min(xe, xx), Math.min(ye, yx), Math.max(2, Math.abs(xx - xe)), Math.max(2, Math.abs(yx - ye)));
        }
        g.strokeStyle = col; g.lineWidth = sel ? 2.5 : 1.5; g.setLineDash([2, 2]); g.beginPath(); g.moveTo(xe, ye); g.lineTo(xx, yx); g.stroke(); g.setLineDash([]);
        const up = x.side > 0, s = sel ? 8 : 6;               // entry triangle: up = long, down = short
        g.fillStyle = up ? THEME.up : THEME.down; g.strokeStyle = '#fff'; g.lineWidth = 1; g.beginPath();
        if (up) { g.moveTo(xe, ye - s); g.lineTo(xe - s, ye + s * .7); g.lineTo(xe + s, ye + s * .7); } else { g.moveTo(xe, ye + s); g.lineTo(xe - s, ye - s * .7); g.lineTo(xe + s, ye - s * .7); }
        g.closePath(); g.fill(); g.stroke();
        g.fillStyle = col; g.beginPath(); g.arc(xx, yx, sel ? 6 : 4.5, 0, 6.3); g.fill(); g.stroke();
        if (few || sel) {
          g.fillStyle = '#E7E9EC';
          g.fillText((up ? 'L ' : 'S ') + Number(x.entry_px).toFixed(2) + ' ×' + nC(), xe + 9, ye + (up ? 12 : -12));
          g.fillStyle = col; g.fillText((win ? '+$' : '-$') + Math.abs(Math.round(pnlOf(x))) + ' · ' + reasonTxt(x) + (x.r != null ? ' · ' + x.r + 'R' : ''), xx + 8, yx + (win === up ? -12 : 12));
        }
      });
      g.globalAlpha = 1;
    }
    function propBanner(p) {
      if (!p || p.status === 'none' || p.status === 'error') return `<div class="propb open">${t('prop_none')}${p && p.error ? ' (' + esc(p.error) + ')' : ''}</div>`;
      const h = p.hist, hh = h ? t('prop_hist', { p: pct(h.p_pass), b: pct(h.p_bust), n: h.n_windows }) : '';
      const cls = p.status === 'passed' ? 'ok' : p.status === 'busted' ? 'bad' : 'open';
      const head = p.status === 'passed' ? t('prop_passed', { a: p.account, n: p.micro, d: p.sessions }) : p.status === 'busted' ? t('prop_busted', { a: p.account, n: p.micro, d: p.sessions }) : t('prop_open', { a: p.account, n: p.micro, d: p.sessions });
      const why = p.breach ? `<div>${t('prop_why', { k: esc(p.breach.kind), d: esc(p.breach.session), e: money(p.breach.equity), f: money(p.breach.floor) })}</div>` : '';
      return `<div class="propb ${cls}"><b>${head}</b>${why}<div class="smeta">${t('prop_from', { d: esc(p.start), t: money(p.target), dd: money(p.max_drawdown) })} ${hh}</div></div>`;
    }
    const fmtT = e => new Date(e * 1000).toISOString().slice(5, 16).replace('T', ' ');
    function renderList() {
      const box = $('#tlist'); if (!box) return;
      if (!trades || !trades.trades.length) { box.innerHTML = ''; return; }
      const L = vis(), cols = t('tl_cols');
      const fb = (k, label) => `<button data-f="${k}" class="${tfilter === k ? 'on' : ''}">${label}</button>`;
      box.innerHTML = propBanner(trades.prop) + `<div class="bar">${fb('all', t('tl_all'))}${fb('win', t('tl_win'))}${fb('loss', t('tl_loss'))}${fb('long', t('tl_long'))}${fb('short', t('tl_short'))}
        <button data-n="-1">${t('tl_prev')}</button><button data-n="1">${t('tl_next')}</button><span>${t('tl_of', { i: Math.max(0, L.findIndex(x => x.i === focus) + 1), n: L.length })}</span>
        <select id="tacct">${['25k', '50k'].map(a => `<option value="${a}" ${a === tAcct ? 'selected' : ''}>${t('tl_acct', { a })}</option>`).join('')}</select>
        <select id="tsize"><option value="" ${!tMicro ? 'selected' : ''}>${t('tl_size_rec2', { a: tAcct, n: (trades.sizing || {})[tAcct] || trades.base_contracts })}</option>${(trades.sizes || []).map(n => `<option value="${n}" ${tMicro === n ? 'selected' : ''}>${t('tl_size_n', { n })}</option>`).join('')}</select>
        <button data-csv="1">${t('tl_csv')}</button></div>
        <table><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>${L.map((x, k) => `<tr class="tr ${x.i === focus ? 'sel' : ''}" data-i="${x.i}"><td>${k + 1}</td><td>${fmtT(x.entry_t)}</td><td class="${x.side > 0 ? 'up' : 'dn'}">${x.side > 0 ? 'L' : 'S'}</td><td>${Number(x.entry_px).toFixed(2)}</td><td><b>${nC()}</b></td><td class="dn">${x.stop != null ? Number(x.stop).toFixed(2) : '–'}</td><td class="up">${x.target != null ? Number(x.target).toFixed(2) : t('tl_tp_strategy')}</td><td>${Number(x.exit_px).toFixed(2)}</td><td>${reasonTxt(x)}</td><td class="${x.pnl >= 0 ? 'up' : 'dn'}">${x.pnl >= 0 ? '+' : '-'}$${Math.abs(Math.round(pnlOf(x)))}</td><td>${x.r == null ? '' : x.r}</td></tr>`).join('')}</table>`;
      box.querySelector('#tsize').onchange = (e) => { tMicro = e.target.value ? +e.target.value : null; reloadTrades(); };
      box.querySelector('#tacct').onchange = (e) => { tAcct = e.target.value; store.set('ofo_tacct', tAcct); tMicro = null; reloadTrades(); };
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
        if (trades.direction && trades.direction !== 'both') h += `<div><b>${t('dr_' + trades.direction)}</b></div>`;
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
      const cols = [THEME.accent, '#A6ABB1', '#FFFFFF', '#7B8087', '#C4C8CD'];
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
    const tradesUrl = s => '/api/trades/' + encodeURIComponent(s) + '?account=' + tAcct + (tMicro ? '&micro=' + tMicro : '');
    async function reloadTrades() {
      if (!slug) return; const d = await api(tradesUrl(slug)); if (d.error) return; trades = d; note(); drawOverlay();
    }
    async function pick(s) {
      slug = s; trades = null; focus = -1; tfilter = 'all'; tMicro = null;
      if (!s) { applyMarkers(); drawIndicators(); note(); return; }
      const d = await api(tradesUrl(s));
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
      return `<tr class="${best ? 'best' : ''}"><th>${esc(k)}${best ? ' ★' : ''}</th><td>${t('dr_' + (v.direction || 'both'))}</td><td><b>${(v.score * 100).toFixed(0)}</b></td><td>${v.sharpe.toFixed(3)}</td><td class="${v.net_pnl >= 0 ? 'pos' : 'neg'}">${money(v.net_pnl)}</td>
        <td>${money(v.max_dd)}</td><td>${v.profit_dd == null ? '–' : v.profit_dd.toFixed(2)}</td><td>${v.trades_per_day.toFixed(1)}</td><td>${pct(v.days_with_trade)}</td><td>${pct(v.trade_win_rate)}</td><td>${pct(v.positive_days)}</td>
        ${accts.map(a => `<td>${v.pass_rate[a] == null ? '–' : pct(v.pass_rate[a])}</td>`).join('')}${accts.map(a => `<td>${md(a)}</td>`).join('')}<td>${esc(Object.entries(v.best_params).map(([a, b]) => a + '=' + b).join(' '))}</td></tr>`;
    }).join('');
    return `<div class="judge"><b>${t('tfs_title')}</b><div class="sub">${t('tfs_sub', { tf: esc(bt.best_tf || '–') })}</div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th></th><th>${t('dr_title')}</th><th>${t('tfs_score')}</th><th>Sharpe</th><th>P&L</th><th>${t('tfs_dd')}</th><th>${t('tfs_pdd')}</th><th>${t('tfs_tpd')}</th><th>${t('tfs_dwt')}</th><th>${t('tfs_wr')}</th><th>${t('tfs_pos')}</th>
      ${accts.map(a => `<th>${t('tfs_pass', { a, d: bt.window_days })}</th>`).join('')}${accts.map(a => `<th>${t('tfs_med_days')} ${a}</th>`).join('')}<th>${t('tfs_params')}</th></tr>${rows}</table></div>
      ${dirDetail(bt)}<div class="sub">${t('tfs_note')}</div></div>`;
  }
  function dirDetail(bt) {
    const v = bt.timeframes[bt.best_tf]; if (!v || !v.by_direction) return '';
    return `<div class="sub"><b>${t('dr_title')} (${esc(bt.best_tf)})</b>: ` + ['both', 'long', 'short'].map(d => { const x = v.by_direction[d]; return `${t('dr_' + d)} ${money(x.net_pnl)} · DD ${money(x.max_dd)} · ${x.n_trades} trade · ${pct(x.trade_win_rate)}` + (d === v.direction ? ' ★' : ''); }).join(' | ') + `</div>`;
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
  function rolesBox(ro) {
    if (!ro) return '';
    const pa = ro.pass_account || {}, py = ro.payout || {};
    const prof = Object.entries(pa.profiles || {}).map(([acct, p]) => ['aggressive', 'prudent'].filter(k => p[k]).map(k => { const x = p[k];
      return `<tr><th>${esc(acct)} · ${t('ro_' + k)}</th><td>${x.n_micro}</td><td>${pct(x.p_pass_window)}</td><td>${pct(x.p_pass_horizon)}</td><td>${x.mean_days == null ? '–' : num(x.mean_days, 1)}</td><td>${x.median_days == null ? '–' : num(x.median_days, 1)}</td><td>${pct(x.p_bust_horizon)}</td></tr>`; }).join('')).join('');
    const failed = (pa.failed_checks || []).map(k => t('k_' + k)).join(', ');
    return `<div class="judge rrbox"><b>${t('ro_title')}</b>
      <div class="sub"><b>${t('ro_' + (ro.label || 'NONE'))}</b></div>
      <div class="sub">${pa.valid ? '✔' : '✘'} ${t('ro_pass')} ${failed ? '— ' + esc(failed) : ''} · ${py.valid ? '✔' : '✘'} ${t('ro_payout')} ${py.first_failed ? '— ' + esc(py.first_failed.name + ': ' + py.first_failed.reason) : ''}</div>
      ${prof ? `<div style="overflow-x:auto"><table class="rrt"><tr><th>${t('ro_profile')}</th><th>${t('ro_micro')}</th><th>${t('ro_p_win')}</th><th>${t('ro_p_hor')}</th><th>${t('ro_mean')}</th><th>${t('ro_med')}</th><th>${t('ro_bust')}</th></tr>${prof}</table></div>
      <div class="sub">${pa.in_sample ? t('tm_insample') : t('tm_oos')} ${t('ro_note')}</div>` : ''}</div>`;
  }
  function scheduleBox(sc) {
    if (!sc || !sc.by_time) return '';
    if (!sc.n_trades) return `<div class="judge rrbox"><b>${t('sch_title')}</b><div class="sub">${esc(sc.note || t('sch_none'))}</div></div>`;
    const maxA = Math.max(1, ...sc.by_time.map(r => Math.abs(r.pnl)));
    const tr = sc.by_time.map(r => { const a = Math.min(1, Math.abs(r.pnl) / maxA), bg = r.pnl >= 0 ? `rgba(143,199,166,${0.12 + 0.5 * a})` : `rgba(217,139,139,${0.12 + 0.5 * a})`;
      const inW = sc.window && r.from >= sc.window.from && r.to <= sc.window.to;
      return `<tr style="${inW ? 'outline:2px solid #fff;' : ''}"><th>${r.from}–${r.to}</th><td>${r.n}</td><td>${pct(r.win_rate)}</td><td style="background:${bg}" class="${r.pnl >= 0 ? 'pos' : 'neg'}">${money(r.pnl)}</td><td>${money(r.avg)}</td></tr>`; }).join('');
    const cr = (sc.by_count || []).map(r => `<tr class="${sc.max_per_day && r.ordinal <= sc.max_per_day ? 'best' : ''}"><th>${r.n_th}</th><td>${r.n}</td><td>${pct(r.win_rate)}</td><td class="${r.pnl >= 0 ? 'pos' : 'neg'}">${money(r.pnl)}</td><td>${money(r.cum_pnl)}</td></tr>`).join('');
    const win = sc.window ? t('sch_win', { a: sc.window.from, b: sc.window.to, n: sc.window.n, p: money(sc.window.pnl) }) : t('sch_nowin');
    const cap = sc.max_per_day ? t('sch_cap', { n: sc.max_per_day }) : t('sch_nocap');
    const ver = sc.with ? t('sch_ver', { a: num(sc.baseline.sharpe, 3), b: num(sc.with.sharpe, 3), p: money(sc.with.pnl) }) : '';
    return `<div class="judge rrbox"><b>${t('sch_title')}</b><div class="sub">${win} · ${cap} · ${t('sch_avg', { n: num(sc.avg_trades_per_day, 1) })}</div>
      <div class="sub">${sc.applied ? t('sch_applied') : sc.informational ? t('sch_info') : t('sch_notapplied')} ${ver}</div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th>${t('sch_slot')}</th><th>${t('sch_n')}</th><th>${t('sch_wr')}</th><th>${t('sch_pnl')}</th><th>${t('sch_avgp')}</th></tr>${tr}</table></div>
      <div style="overflow-x:auto"><table class="rrt"><tr><th>${t('sch_ord')}</th><th>${t('sch_n')}</th><th>${t('sch_wr')}</th><th>${t('sch_pnl')}</th><th>${t('sch_cum')}</th></tr>${cr}</table></div>
      <div class="sub">${t('sch_note')}</div></div>`;
  }
  function rrBox(ex, rescue) {
    if (!ex) return '';
    const stops = [...new Set(ex.grid.map(c => c.stop_atr))], rrs = [...new Set(ex.grid.map(c => c.rr))];
    const cell = (s, r) => ex.grid.find(c => c.stop_atr === s && c.rr === r);
    const max = Math.max(...ex.grid.map(c => Math.abs(c.sharpe)), Math.abs(ex.baseline.sharpe)) || 1;
    const best = ex.best, bg = v => { const a = Math.min(1, Math.abs(v) / max); return v >= 0 ? `rgba(143,199,166,${0.12 + 0.5 * a})` : `rgba(217,139,139,${0.12 + 0.5 * a})`; };
    const rows = stops.map(s => `<tr><th>${t('rr_stop', { s })}</th>${rrs.map(r => { const c = cell(s, r);
      const isBest = ex.chosen && best.stop_atr === s && best.rr === r;
      return `<td style="background:${bg(c.sharpe)};${isBest ? 'outline:2px solid #fff;' : ''}" title="${t('rr_cell', { w: pct(c.win_rate), n: c.n_trades, p: money(c.pnl) })}">${num(c.sharpe, 3)}<small class="${c.pnl >= 0 ? 'pos' : 'neg'}">${money(c.pnl)}</small></td>`; }).join('')}</tr>`).join('');
    const own = `<tr><th>${t('rr_own')}</th><td colspan="${rrs.length}" style="background:${bg(ex.baseline.sharpe)};${!ex.chosen ? 'outline:2px solid #fff;' : ''}">${num(ex.baseline.sharpe, 3)} <small class="${ex.baseline.pnl >= 0 ? 'pos' : 'neg'}">${money(ex.baseline.pnl)}</small> ${!ex.chosen ? '← ' + t('rr_best_label') : ''}</td></tr>`;
    const chosen = ex.chosen ? t('rr_chosen', { s: best.stop_atr, r: best.rr == null ? t('rr_none') : best.rr + ':1' }) : t('rr_not_chosen');
    const resc = rescue && rescue.robust ? `<div class="sub ok">${t('rr_rescued')}</div>` : (rescue && ex.rescue_tried ? `<div class="sub">${t('rr_notrescued')}</div>` : '');
    return `<div class="judge rrbox"><b>${t('rr_title')}</b><div class="sub">${chosen}</div>${resc}
      <table class="rrt"><tr><th></th>${rrs.map(r => `<th>${r == null ? t('rr_none') : 'R:R ' + r + ':1'}</th>`).join('')}</tr>${own}${rows}</table>
      <div class="sub">${t('rr_note', { b: num(ex.baseline.sharpe, 3), f: pct(ex.frac_better_than_baseline), n: ex.n_trials })}</div></div>`;
  }

  // ---- Judge diagnosis (edge / prop / final): what passed, what failed, why, and what to try next ----
  function jxVal(v) {
    if (v == null) return '–';
    if (typeof v === 'number') return Math.abs(v) < 5 ? v.toFixed(2) : Math.round(v).toLocaleString();
    if (typeof v === 'object') return Object.entries(v).map(([k, x]) => k + ' ' + jxVal(x)).join(' · ');
    return String(v);
  }
  function budgetWhy(i) {
    if (!i) return '';
    const q = (i.queued || []).find(x => x.blocked_by); const b = q && q.blocked_by;
    const caps = Object.entries(i.caps || {}).filter(([k]) => ['screening', 'validation', 'robustness', 'audit'].includes(k)).map(([k, c]) => `${k} ${(i.used || {})[k] || 0}/${c}`).join(' · ');
    return `<div class="sub">${esc(i.day)} · ${esc(i.unit)}: ${esc(caps)}${b ? ` — «${esc(q.name)}»: ${esc(b.stage)} ${b.used}+${b.needs} > ${b.cap}` : ''}</div>`;
  }
  function judgeBox(r) {
    const j = r.judgment; if (!j) return '';
    const row = g => `<tr class="jx-${g.status}"><td>${esc(g.label)}</td><td>${esc(g.status)}</td><td>${g.observed != null ? esc(jxVal(g.observed)) : '–'}</td><td>${g.threshold != null ? esc(jxVal(g.threshold)) : '–'}</td><td>${g.interval && g.interval[0] != null ? esc(jxVal(g.interval[0]) + ' … ' + jxVal(g.interval[1])) : '–'}</td><td>${esc(g.reason || '')}</td></tr>`;
    const bad = j.gates.filter(g => g.status !== 'PASS'), ok = j.gates.filter(g => g.status === 'PASS');
    const d = j.diagnosis;
    const next = d && d.next_experiments && d.next_experiments.length ? '<ul>' + d.next_experiments.map(e => `<li>${esc(e.text || e.kind)}</li>`).join('') + '</ul>' : '';
    const props = Object.entries((j.prop && j.prop.by_ruleset) || {}).map(([id, v]) => `<li><b>${esc(id)}</b>: ${esc(v.status)} · ${v.size_micro || '–'} micro · NPFV ${v.metrics.npfv_mean == null ? '–' : '$' + Math.round(v.metrics.npfv_mean).toLocaleString()} · ${t('ro_p_win')}: ${v.metrics.p_pass == null ? '–' : pct(v.metrics.p_pass)} · payout: ${v.metrics.p_first_payout == null ? '–' : pct(v.metrics.p_first_payout)}</li>`).join('');
    const stg = j.stage && j.stage.stage ? `<span class="badge b-stage">${esc(j.stage.stage)}</span>` : '';
    const modes = r.dsr_modes ? `<details><summary>${t('jx_dsr_modes')}</summary><table class="jx-t"><tr><th></th><th>N</th><th>V</th><th>SR₀ ${t('jx_annual')}</th><th>DSR</th><th></th></tr>${['holdout_null', 'raw', 'locked_looks'].map(k => { const m = r.dsr_modes[k]; return m ? `<tr class="${m.official ? 'jx-PASS' : ''}"><td>${esc(k)}${m.official ? ' ★' : ''}</td><td>${m.N}</td><td>${m.V == null ? '–' : m.V.toPrecision(2)}</td><td>${m.sr0_annual == null ? '–' : m.sr0_annual.toFixed(2)}</td><td>${m.value == null ? '–' : m.value.toFixed(2)}</td><td>${esc(m.formula)}<br><span class="sub">${esc(m.V_meaning)}. ${esc(m.interpretation)}</span></td></tr>` : ''; }).join('')}</table></details>` : '';
    const fin = r.finalist ? `<div class="sub">${t('jx_finalist', { k: r.finalist.k_index, m: (r.finalist.epoch || {}).max_finalists, a: (r.finalist.epoch || {}).start, b: (r.finalist.epoch || {}).end })}</div>` : '';
    const shl = r.shelved ? `<div class="sub">⚠ ${esc(r.shelved.reason)}</div>` : '';
    const fwd = r.forward_test ? `<details><summary>${t('jx_forward')}: ${esc(r.forward_test.status)} (${r.forward_test.sessions} ${t('jx_sessions')}, z ≥ ${r.forward_test.boundary.toFixed(2)})</summary><table class="jx-t">${(r.forward_test.looks || []).map(l => `<tr><td>T=${l.T}</td><td>z ${l.z.toFixed(2)}</td><td>Sharpe ${l.annual_sharpe.toFixed(2)}</td></tr>`).join('')}</table>${r.forward_plan ? `<div class="sub">plan ${esc(r.forward_plan.hash.slice(0, 10))} · ${esc(r.forward_plan.method)} · ${esc((r.forward_plan.stopping || []).join(' | '))}</div>` : ''}</details>` : '';
    return `<div class="judge jx" id="jx"><b>${t('jx_title')}</b> <span class="badge b-${j.verdict}">${t('v_' + j.verdict)}</span> ${stg}
      <div class="sub">${esc(j.reason || '')}</div><div class="sub">${t('jx_det')}</div>
      ${r.can_promote_to_paper ? `<button class="btn small" id="jx-paper">${t('jx_paper_go')}</button>` : ''}
      ${r.paper ? `<div class="sub">${t('jx_paper_state')}: ${esc(r.paper.status)}${r.paper.probation ? ' (prova)' : ''}</div>` : ''}
      ${j.conflict ? `<div class="sub">⚠ ${t('jx_conflict')}: ${esc(j.conflict)}</div>` : ''}
      ${d ? `<div class="jx-diag"><div><b>${esc(d.failed_gate ? d.failed_gate.label : '')}</b> — ${esc(d.classification_label || d.classification)} · ${d.recoverable ? t('jx_recov') : t('jx_norecov')}</div>
        <div>${esc(d.technical_reason || '')}</div>
        <div class="sub">${t('jx_obs')}: ${esc(jxVal(d.observed))} · ${t('jx_thr')}: ${esc(jxVal(d.threshold))}${d.interval && d.interval[0] != null ? ' · ' + t('jx_ci') + ': ' + esc(jxVal(d.interval[0]) + ' … ' + jxVal(d.interval[1])) : ''}</div>
        <div><b>${t('jx_next')}</b>${next || '<div class="sub">–</div>'}</div>
        <div class="sub"><b>${t('jx_frozen')}:</b> ${(d.frozen || []).map(esc).join(' · ')}</div></div>` : ''}
      <details open><summary>${t('jx_failed')} (${bad.length})</summary>${bad.length ? `<table class="jx-t"><tr><th></th><th></th><th>${t('jx_obs')}</th><th>${t('jx_thr')}</th><th>${t('jx_ci')}</th><th></th></tr>${bad.map(row).join('')}</table>` : `<div class="sub">${t('jx_none')}</div>`}</details>
      <details><summary>${t('jx_passed')} (${ok.length})</summary><table class="jx-t">${ok.map(row).join('')}</table></details>
      ${props ? `<details><summary>${t('jx_prop')}</summary><ul>${props}</ul></details>` : ''}${modes}${fin}${shl}${fwd}</div>`;
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
      <div class="dtitle"><h1>${esc(rep.name)}</h1><span class="badge b-${L.verdict || L.status}">${t('v_' + (L.verdict || L.status))}</span><a class="btn small" id="pdf-go" href="/api/pdf/${encodeURIComponent(slug)}" download>PDF</a> <button class="btn small" id="retest-go" title="${esc(t('retest_tip'))}">↻ ${t('retest')}</button></div>
      ${rep.retest_no ? `<div class="sub">${t('retest_n', { n: rep.retest_no })}</div>` : ''}
      <div class="sub">${t('d_model')} ${esc(rep.template)} · ${rep.n_candidates} ${t('d_tested')} · ${esc(rep.source || '')}</div>
      <div class="judge"><b>${t('d_judge')}</b> ${L.reason ? '<div style="margin-top:6px">' + esc(itReason(L.reason)) + '</div>' : ''}
        ${L.checks ? '<ul style="padding:0;margin:8px 0">' + Object.entries(L.checks).map(([k, v]) => `<li class="${v ? 'ok' : 'no'}">${esc(t('k_' + k))}</li>`).join('') + '</ul>' : ''}
        ${L.p_pass != null ? `<div>${t('d_pass')}: <b>${pct(L.p_pass)}</b> (${t('d_interval')} ${pct(L.p_pass_ci95[0])}–${pct(L.p_pass_ci95[1])}) · ${t('d_bust')}: <b>${pct(L.p_bust)}</b> · ${t('d_pnl_block')}: <b class="${L.pnl >= 0 ? 'pos' : 'neg'}">${money(L.pnl)}</b></div>` : ''}</div>
      <div class="grid">${kpi(t('kp_med'), num(sur.median_sharpe, 3))}${kpi(t('kp_pos'), pct(sur.frac_positive))}${kpi(t('kp_plat'), num(sur.plateau_ratio, 2))}${kpi(t('kp_rob'), sur.robust ? t('yes') : t('no'), sur.robust ? 'pos' : 'neg')}${kpi(t('kp_wf'), money(rep.wf_oos_pnl), rep.wf_oos_pnl >= 0 ? 'pos' : 'neg')}</div>
      ${d.best_params ? `<div class="sub">${t('d_best', { p: esc(JSON.stringify(d.best_params)), n: rep.n_candidates })}</div>` : ''}
      <div id="jx-slot"></div>
      <div id="mie-trace"></div>
      <div id="streak-slot"></div>
      ${rolesBox(rep.roles)}
      ${farmBox(rep.farming)}
      ${tfBox(rep.by_timeframe)}
      ${sizingBox(rep.locked && rep.locked.sizing ? rep.locked.sizing : rep.sizing)}
      ${timingBox(rep.locked && rep.locked.timing ? rep.locked.timing : rep.timing)}
      ${rrBox(rep.exits, rep.rescue)}
      ${scheduleBox(rep.schedule)}
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
    if (window.MIE) MIE.trace(slug, $('#mie-trace'));
    api('/api/streaks/' + encodeURIComponent(slug)).then(r => {
      const el = $('#streak-slot'); if (!el) return;
      if (!r.available) { el.innerHTML = `<div class="judge"><b>${t('stk_title')}</b> <span class="smeta">NOT AVAILABLE</span></div>`; return; }
      const f = v => v == null ? '–' : Number(v).toFixed(1), row = (n, s) => `<tr><td>${n}</td><td>${f(s.win.avg)}</td><td>${s.win.max}</td><td>${f(s.loss.avg)}</td><td>${s.loss.max}</td></tr>`;
      el.innerHTML = `<div class="judge"><b>${t('stk_title')}</b><table class="mie-t"><tr><th></th><th>${t('stk_wavg')}</th><th>${t('stk_wmax')}</th><th>${t('stk_lavg')}</th><th>${t('stk_lmax')}</th></tr>${row(t('stk_trades'), r.trades)}${row(t('stk_days'), r.days)}</table><div class="smeta">${t('stk_note', { n: r.trades.n })}</div></div>`;
    });
    api('/api/judge?slug=' + encodeURIComponent(slug)).then(r => {
      const slot = $('#jx-slot'); if (!slot || r.error) return;
      slot.outerHTML = judgeBox(r);
      const pb = $('#jx-paper');
      if (pb) pb.onclick = async () => { const x = await api('/api/promote', { method: 'POST', body: JSON.stringify({ slug }) }); toast(x.error ? '⚠ ' + esc(x.error) : '✔ ' + esc(t('jx_paper_started')), 8000); };
    }).catch(() => {});
    $('#retest-go').onclick = async () => {
      if (!confirm(t('retest_confirm'))) return;
      const r = await api('/api/retest', { method: 'POST', body: JSON.stringify({ slug }) });
      toast(r.error ? '⚠ ' + esc(r.error) : '↻ ' + esc(t('retest_started', { n: r.retest_no })), 8000);
      if (!r.error) { $('#modal').hidden = true; try { LabCharts.destroyAll(); } catch (e) {} poll(); }
    };
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

  // ---------- talk to the Researcher: search / keep searching / stop searching / notes ----------
  const DKIND = { search: 'dir_search', keep: 'dir_keep', stop: 'dir_stop', note: 'dir_note' };
  function renderDirectives(list) {
    $('#directives').innerHTML = `<form id="dirform" class="srow"><input id="dirin" placeholder="${esc(t('dir_ph'))}" autocomplete="off" style="flex:1;min-width:0">
      <select id="dirkind"><option value="">${t('dir_auto')}</option>${Object.entries(DKIND).map(([k, v]) => `<option value="${k}">${t(v)}</option>`).join('')}</select>
      <button class="btn small">${t('dir_send')}</button></form>
      <div class="spapers">${list.length ? list.slice().reverse().map(d => `<div class="dir ${d.active ? '' : 'used'}"><span class="dk ${d.kind}">${t(DKIND[d.kind])}</span> ${esc(d.topic)}${d.kind === 'search' && !d.active ? ' ✓' : ''} <button class="x" data-id="${esc(d.id)}" title="${t('dir_remove')}">✕</button></div>`).join('') : `<div class="smeta">${t('dir_empty')}</div>`}</div>`;
    $('#dirform').onsubmit = async (e) => { e.preventDefault(); const v = $('#dirin').value.trim(); if (!v) return;
      const r = await api('/api/directives', { method: 'POST', json: { text: v, kind: $('#dirkind').value || null } });
      if (r.directives) { renderDirectives(r.directives); toast('📌 ' + esc(r.item.topic.slice(0, 100)) + (r.search_started ? ' — ' + t('dir_started') : ''), 4000); } scoutKey = ''; };
    $('#directives').querySelectorAll('button.x').forEach(b => b.onclick = async () => { const r = await api('/api/directives/remove', { method: 'POST', json: { id: b.dataset.id } }); renderDirectives(r.directives); });
  }
  api('/api/directives').then(d => renderDirectives(d.directives || [])).catch(() => {});
  function renderResearcher(r) {
    const el = $('#rstatus'); if (!r || !el) return;
    const bits = [r.ai ? `AI: <b class="ok">${t('rs_on')}</b>` : `AI: <b class="no">${t('rs_off')}</b>`, r.allowed ? '' : `<b class="no">${t('rs_blocked')}: ${esc(r.why || '')}</b>`,
      `${t('rs_unread')}: <b>${r.unread}</b>`, `${t('rs_queue')}: <b>${r.queue}</b>`].filter(Boolean).join(' · ');
    const rec = (r.recent || []).slice().reverse().map(x => x.error ? `<div class="no">✘ ${esc(x.at)} ${esc(x.file)}: ${esc(x.error)}</div>`
      : `<div class="${x.proposals ? 'ok' : ''}">${x.proposals ? '✔' : '·'} ${esc(x.at)} ${esc(x.file)}: ${x.proposals} ${t('rs_prop')}${(x.rejected || []).length ? ' (' + esc(x.rejected[0]) + ')' : ''}</div>`).join('');
    const h = r.hypothesis, hy = h ? `<div class="smeta">${t('rs_hyp')}: <b class="${h.ready ? 'ok' : ''}">${esc(h.ready ? t('rs_hyp_ready') : h.why)}</b>${(h.log || []).length ? ' · ' + esc(h.log[h.log.length - 1].family) + ': ' + (h.log[h.log.length - 1].error ? '✘ ' + esc(h.log[h.log.length - 1].error) : h.log[h.log.length - 1].proposals + ' ' + t('rs_prop')) : ''}</div>` : '';
    const dg = r.doing || { now: [], events: [] };
    const nowL = `<div class="smeta">${t('rs_now')}: <b class="${dg.now.length ? 'ok' : ''}">${dg.now.length ? dg.now.map(k => t('rs_now_' + k)).join(' + ') : t('rs_now_idle')}</b></div>`;
    const evL = dg.events.slice().reverse().map(x => `<div class="smeta">${esc(x.at)} ${esc(x.agent)} · ${esc(x.type)}: ${esc(x.message)}</div>`).join('');
    el.innerHTML = `<div class="smeta">${bits}</div>${nowL}${hy}${rec}${evL}`;
  }

  // ---------- PDF the Researcher asked for: notification + upload ----------
  function toast(html, ms) {
    let box = $('#toasts'); if (!box) { box = document.createElement('div'); box.id = 'toasts'; document.body.appendChild(box); }
    const d = document.createElement('div'); d.className = 'toast'; d.innerHTML = html + '<button class="x">✕</button>'; box.appendChild(d);
    d.querySelector('.x').onclick = () => d.remove(); if (ms) setTimeout(() => d.remove(), ms); return d;
  }
  function pickPdf(id) {
    const f = document.createElement('input'); f.type = 'file'; f.accept = '.pdf,.txt,.md';
    f.onchange = async () => { const file = f.files[0]; if (!file) return;
      const r = await api('/api/ideas/' + encodeURIComponent(file.name) + '?for=' + encodeURIComponent(id), { method: 'PUT', body: await file.arrayBuffer() });
      toast(r.error ? esc(r.error) : '✔ ' + t('pdf_sent', { n: esc(r.saved) }), 6000); scoutKey = ''; poll(); };
    f.click();
  }
  function needPdf(e) {
    const d = e.data || {};
    const el = toast(`📄 <b>${t('pdf_need')}</b><div>${esc((d.title || e.message || '').slice(0, 110))}</div>
      <a href="${esc(d.url || '#')}" target="_blank" rel="noopener">${t('pdf_open')}</a> <button class="btn small" id="tp-${Date.now()}">${t('pdf_upload')}</button>`, 0);
    const b = el.querySelector('button.btn'); if (b) b.onclick = () => { pickPdf(d.id); el.remove(); };
  }

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
      ${(sc.manual_ssrn || []).length ? `<div class="smeta"><b>${t('ssrn_manual')}</b></div><div class="spapers">${sc.manual_ssrn.slice(0, 8).map(p => `<div><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc((p.title || '').slice(0, 60))}</a> <button class="btn small pdfup" data-id="${esc(p.id)}">${t('pdf_upload')}</button></div>`).join('')}</div>` : ''}
      <div class="smeta">${t('scout_note')}</div>`;
    $('#scout').querySelectorAll('.pdfup').forEach(b => b.onclick = () => pickPdf(b.dataset.id));
    $('#scout-go').onclick = async () => { await api('/api/scout/run', { method: 'POST', json: { lang: LANG } }); scoutKey = ''; poll(); };
    $('#scout-auto').onchange = async (e) => { const m = +e.target.value; await api('/api/scout/auto', { method: 'POST', json: { enabled: m > 0, minutes: m || 60 } }); scoutKey = ''; poll(); };
  }

  // ---------- live web feed: where the Researcher is searching / reading right now ----------
  let webKey = '';
  function renderHealth(d) {
    const h = d.health || {}, ar = d.agent_reach || {};
    const status = n => { const x = h[n];
      if (!x) return `<i class="na">${t('hl_untested')}</i>`;
      if (x.last_error) return `<i class="bad" title="${esc(x.last_error)}">✘ ${esc(x.last_error.slice(0, 60))}</i>`;
      return `<i class="ok">✔ ${x.last_n == null ? '' : x.last_n + ' ' + t('web_found')}</i>`; };
    const row = x => `<label class="src ${x.available ? '' : 'off'}" title="${esc(x.hint || '')}"><input type="checkbox" data-src="${esc(x.name)}" ${x.enabled ? 'checked' : ''} ${x.available ? '' : 'disabled'}> <b>${esc(x.name)}</b>
      ${x.available ? status(x.name) : `<i class="bad">${t('hl_missing')}</i>`}${x.needs_account ? ` <i class="na">${t('src_account')}</i>` : ''}</label>`;
    const cfgErr = d.config_error ? `<div class="hs bad">${esc(d.config_error)}</div>` : '';
    const miss = (d.missing || []).map(m => `<div class="hs bad">${esc(m.name)}: ${t('hl_missing')} — <code>${esc(m.hint)}</code></div>`).join('');
    const arT = ar.installed === null || ar.installed === undefined ? t('hl_ar_check') : ar.installed ? t('hl_ar_yes') : t('hl_ar_no');
    const html = cfgErr + `<div class="hs na"><b>${t('src_title')}</b></div><div class="srcs">${(d.sources || []).map(row).join('')}</div>${miss}<div class="hs na">Agent Reach: ${arT}</div>`;
    if ($('#webhealth').dataset.k !== html) { $('#webhealth').dataset.k = html; $('#webhealth').innerHTML = html; }
  }
  document.addEventListener('change', async (e) => {
    const n = e.target && e.target.dataset && e.target.dataset.src; if (!n) return;
    await api('/api/sources', { method: 'POST', json: { name: n, enabled: e.target.checked } }); webKey = ''; pollWeb(); });
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
      const d = await api('/api/accounts'), L = d.accounts; lastAcct = L;
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

  // ---------- the Validation Chamber and Risk Manager screens show the LATEST real study and the real accounts ----------
  let valSlug = null;
  async function feedScreens() {
    if (!window.LabScene || !LabScene.setValidation || !lastState) return;
    const last = (lastState.strategies || [])[0];
    if (last && last.slug !== valSlug) {
      valSlug = last.slug;
      try { const d = await api('/api/strategy/' + encodeURIComponent(last.slug)); const c = d.charts && (d.charts.wf_oos || d.charts.dev_best); LabScene.setValidation({ name: last.name, equity: c ? c.equity : null }); } catch (e) { valSlug = null; }
    }
    if (last) {
      const rl = last.role ? t('ro_' + last.role) : '–';
      LabScene.setRisk({ study: [String(last.name).slice(0, 30), t('v_' + last.status), rl.slice(0, 32), (last.robust ? t('robust') : t('fragile'))],
        accounts: (lastAcct || []).slice(0, 6).map(a => `${a.firm} ${a.plan} ${a.kind === 'funded' ? 'F' : 'E'} ${Math.round((a.health || 0) * 100)}%`) });
    }
  }
  setInterval(feedScreens, 6000);

  // ---------- departments: zoom bar + Inspector (live data only: nothing here is made up) ----------
  const DEPT_AGENTS = { research: ['researcher', 'scout'], optimizer: ['optimizer', 'planner'], validator: ['validator'], strategist: ['strategist'], risk: ['risk'], judge: ['judge', 'auditor'], trader: ['trader'], core: [], vault: [], farm: [], mie: ['market_observer', 'structure_engine', 'behavior_analyzer', 'obsidian_memory', 'research_director'] };
  const DEPT_STATE = { research: 'research', optimizer: 'optimizer', validator: 'validator', strategist: 'strategist', risk: 'risk', judge: 'judge', trader: 'trader', mie: 'mie_director' };
  let inspKey = null;
  function buildDeptBar() {
    const bar = $('#deptbar'); if (!bar || !window.LabScene || !LabScene.departments) return;
    bar.innerHTML = `<button class="home" data-k="">${t('dp_home')}</button>` + LabScene.departments().map(d => `<button data-k="${d.key}">${esc(t('dp_' + d.key))}</button>`).join('');
    bar.querySelectorAll('button').forEach(b => b.onclick = () => { if (b.dataset.k) { LabScene.focus(b.dataset.k); openInspector(b.dataset.k); } else { LabScene.home(); closeInspector(); } });
  }
  function closeInspector() { inspKey = null; $('#inspector').hidden = true; markBar(null); }
  function markBar(k) { document.querySelectorAll('#deptbar button').forEach(b => b.classList.toggle('on', !!k && b.dataset.k === k)); }
  function openInspector(key) { inspKey = key; markBar(key); renderInspector(); }
  function evLines(agents, n) {
    const quiet = agents.includes('market_observer') ? ['idle', 'web'] : ['working', 'done', 'idle', 'web'];       // the MIE agents report through done/working lines
    return evCache.filter(e => agents.includes(e.agent) && !quiet.includes(e.type)).slice(-n).reverse()
      .map(e => `<div class="ev2"><small>${esc(AGENTS[e.agent] || e.agent)} · ${esc(e.type)} · ${esc((e.t || '').slice(11, 19))}</small>${esc(String(e.message || '').slice(0, 180))}</div>`).join('') || `<div class="smeta">${t('dp_noev')}</div>`;
  }
  function renderInspector() {
    const box = $('#inspector'); if (!inspKey || !box) return; const k = inspKey, s = lastState || {}, kv = (a, b) => `<div class="kv"><span>${a}</span><b>${b}</b></div>`;
    const sts = LabScene.states ? LabScene.states() : {}, stn = DEPT_STATE[k] ? sts[DEPT_STATE[k]] : null;
    const jobs = s.jobs || [], run = jobs.find(j => j.status === 'running'), queued = jobs.filter(j => j.status === 'queued').length, strat = s.strategies || [];
    const cnt = st => strat.filter(x => x.status === st).length, last = strat[0];
    let rows = '', extra = '', acts = '';
    if (k === 'research') { const r = (s.autopilot && s.autopilot.researcher) || s.researcher || {}; if (r.ai == null && s.ai != null) r.ai = s.ai; rows = kv(t('dp_unread'), r.unread ?? '–') + kv(t('dp_queue'), r.queue ?? '–') + kv('AI', r.ai ? t('rs_on') : t('rs_off')) + kv(t('rs_hyp'), esc(r.hypothesis ? (r.hypothesis.ready ? t('rs_hyp_ready') : r.hypothesis.why) : '–')); acts = `<button class="btn small" data-go="chat">${t('dp_talk')}</button>`; }
    else if (k === 'optimizer') { rows = kv(t('dp_running'), run ? esc(run.name) : '–') + kv(t('dp_stage'), run ? esc(t('stg_' + (run.stage || 'starting'))) : '–') + kv(t('dp_waiting'), queued) + kv(t('dp_trials'), s.trials ?? '–'); }
    else if (k === 'validator') { rows = last ? kv(t('dp_last'), esc(last.name)) + kv(t('med_sharpe'), num(last.median_sharpe, 3)) + kv(t('dp_surface'), last.robust ? t('robust') : t('fragile')) : `<div class="smeta">${t('dp_nostudy')}</div>`; }
    else if (k === 'core') { rows = kv(t('dp_running'), run ? esc(run.name) : '–') + kv(t('dp_waiting'), queued) + kv(t('dp_studies'), strat.length) + kv(t('v_APPROVED'), cnt('APPROVED')) + kv(t('v_REJECTED'), cnt('REJECTED')) + kv(t('v_INSUFFICIENT_EVIDENCE'), cnt('INSUFFICIENT_EVIDENCE')); acts = `<button class="btn small" data-go="strategies">${t('dp_open_strat')}</button>`; }
    else if (k === 'strategist') { rows = ''; acts = `<button class="btn small" data-go="chat">${t('dp_talk')}</button>`; }
    else if (k === 'risk') { rows = kv(t('dp_review'), s.farming_pending ?? 0) + (last ? kv(t('dp_last'), esc(last.name)) + kv(t('ro_title').split(':')[0], last.role ? esc(t('ro_' + last.role)) : '–') : ''); }
    else if (k === 'judge') { rows = kv(t('v_APPROVED'), cnt('APPROVED')) + kv(t('v_REJECTED'), cnt('REJECTED')) + kv(t('v_INSUFFICIENT_EVIDENCE'), cnt('INSUFFICIENT_EVIDENCE')) + kv(t('dp_looks'), s.locked_looks ?? '–'); }
    else if (k === 'vault') { const f = s.fascicoli || []; rows = kv(t('dp_dossiers'), f.length); extra = f.slice(0, 8).map(x => `<div class="fl" data-fasc="${esc(x.slug)}">▸ ${esc(x.nome)} · ${esc(x.decisione || t('dp_pending'))}</div>`).join(''); }
    else if (k === 'farm') { const A = lastAcct || []; const by = st => A.filter(a => a.state === st).length; rows = kv(t('dp_accounts'), A.length) + kv(t('acct_eval'), A.filter(a => a.kind !== 'funded').length) + kv(t('acct_funded'), A.filter(a => a.kind === 'funded').length) + kv('passed', by('passed')) + kv('busted', by('busted')) + kv(t('dp_payouts'), A.reduce((x, a) => x + (a.payouts || 0), 0)); }
    else if (k === 'mie') { const m = s.mie || {}, c = m.cycle || {}; rows = kv(t('mie_cycle'), c.status && c.status !== 'idle' ? esc(c.status) + (c.focus ? ' · ' + esc(c.focus) : '') : 'NOT AVAILABLE') + kv(t('mie_studies'), m.studies ?? 'NOT AVAILABLE') + kv(t('mie_hyps'), m.hypotheses ?? 'NOT AVAILABLE') + (m.error ? kv('!', esc(m.error)) : ''); acts = `<button class="btn small" id="insp-mie">${t('mie_open_panel')}</button>`; }
    else if (k === 'trader') { rows = kv(t('v_APPROVED'), cnt('APPROVED')); acts = `<button class="btn small" data-go="trader">${t('dp_open_trader')}</button>`; }
    const ag = DEPT_AGENTS[k] || [];
    box.hidden = false;
    box.innerHTML = `<div class="ih"><b>${esc(t('dp_' + k))}</b><button id="insp-x">✕</button></div>
      ${stn ? `<span class="st ${stn}">${esc(t('st_' + stn))}</span>` : ''}<div class="desc">${esc(t('dpd_' + k))}</div>${rows}${extra}
      ${ag.length ? `<h3>${t('dp_events')}</h3>${evLines(ag, 5)}` : ''}<div class="acts"><button class="btn small ghost" id="insp-zoom">${t('dp_zoom')}</button>${acts}</div>`;
    $('#insp-x').onclick = () => { LabScene.home(); closeInspector(); };
    $('#insp-zoom').onclick = () => LabScene.focus(k);
    const mb = box.querySelector('#insp-mie'); if (mb) mb.onclick = () => window.MIE && MIE.open();
    box.querySelectorAll('[data-go]').forEach(b => b.onclick = () => tab(b.dataset.go));
    box.querySelectorAll('[data-fasc]').forEach(b => b.onclick = () => openFasc(b.dataset.fasc));
  }
  setInterval(() => { if (inspKey) renderInspector(); }, 4000);
  if (window.LabScene && LabScene.departments) { buildDeptBar(); LabScene.onSelectCb = k => { if (k) { inspKey = k; markBar(k); renderInspector(); } }; }
  window.addEventListener('ofo-lang', buildDeptBar);

  // ---------- brand logo (optional file: brand/icon.png) ----------
  const li = $('#logo-img');
  li.onload = () => { li.style.display = 'block'; $('#logo-cube').style.display = 'none'; const f = document.querySelector('link[rel=icon]'); if (f) f.href = 'brand/icon.png'; };
  li.onerror = () => { li.remove(); };
  if (li.complete && li.naturalWidth) li.onload();

  poll(); pollEvents();
  setInterval(poll, 3000); setInterval(pollEvents, 1500);
})();
