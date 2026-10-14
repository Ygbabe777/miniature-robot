/* Market Intelligence panel and research trace. Everything shown comes from /api/mie/* (what the backend really persisted); a missing
   value is printed as NOT AVAILABLE, never estimated. Statuses use the six evidence words; the word PROVEN does not exist here. */
(function () {
  const NA = 'NOT AVAILABLE';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = (v, d) => (v == null || !isFinite(v)) ? NA : Number(v).toFixed(d == null ? 3 : d);
  const pct = v => (v == null || !isFinite(v)) ? NA : (v * 100).toFixed(1) + '%';
  async function get(path) { try { const r = await fetch(path, { headers: { 'X-OFO': '1' } }); return await r.json(); } catch (e) { return { error: String(e) }; } }
  async function post(path) { try { const r = await fetch(path, { method: 'POST', headers: { 'X-OFO': '1', 'Content-Type': 'application/json' }, body: '{}' }); return await r.json(); } catch (e) { return { error: String(e) }; } }
  let data = {}, tab = 'overview', box = null, lastMem = 0;
  const kv = (a, b) => `<div class="kv"><span>${a}</span><b>${b}</b></div>`;
  const chip = st => `<span class="mie-st mie-${esc(st)}">${esc(st)}</span>`;

  async function refresh(full) {
    const [overview, intelligence, cycle, chart] = await Promise.all([get('/api/mie/overview'), get('/api/mie/intelligence'), get('/api/mie/cycle'), get('/api/mie/chart')]);
    data = Object.assign(data, { overview, intelligence, cycle, chart });
    if (full || Date.now() - lastMem > 60000) { data.memory = await get('/api/mie/memory'); lastMem = Date.now(); }
    if (window.LabScene && LabScene.setMie) LabScene.setMie(data);
    if (box && !box.hidden) render();
    return data;
  }

  function drawChart(cv, ch) {
    const g = cv.getContext('2d'), w = cv.width, h = cv.height; g.fillStyle = '#050505'; g.fillRect(0, 0, w, h);
    if (!ch || !ch.available) { g.fillStyle = '#44484d'; g.font = 'bold 22px monospace'; g.textAlign = 'center'; g.fillText(NA, w / 2, h / 2); return; }
    const cs = ch.candles, n = cs.length, L = 52, R = w - 12, Tp = 24, B = h - 22; let lo = Infinity, hi = -Infinity; cs.forEach(c => { lo = Math.min(lo, c[3]); hi = Math.max(hi, c[2]); });
    const pad = (hi - lo) * 0.05; lo -= pad; hi += pad; const X = i => L + (i + 0.5) / n * (R - L), Y = v => B - (v - lo) / (hi - lo) * (B - Tp), cw = Math.max(1, (R - L) / n * 0.7);
    const near = ts => { let b = 0; for (let i = 0; i < n; i++) { if (cs[i][0] <= ts) b = i; else break; } return b; };
    g.font = '11px monospace'; g.fillStyle = '#7b8087'; g.textAlign = 'right'; g.strokeStyle = '#1c1e21';
    for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4; g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(R, Y(v)); g.stroke(); g.fillText(v.toFixed(0), L - 5, Y(v) + 4); }
    cs.forEach((c, i) => { g.strokeStyle = g.fillStyle = c[4] >= c[1] ? '#e7e9ec' : '#5a5f65'; g.beginPath(); g.moveTo(X(i), Y(c[2])); g.lineTo(X(i), Y(c[3])); g.stroke(); g.fillRect(X(i) - cw / 2, Math.min(Y(c[1]), Y(c[4])), cw, Math.max(1, Math.abs(Y(c[1]) - Y(c[4])))); });
    g.setLineDash([6, 4]); g.textAlign = 'left';
    Object.entries(ch.levels || {}).forEach(([k, v]) => { if (Y(v) < Tp || Y(v) > B) return; g.strokeStyle = '#a6abb1'; g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(R, Y(v)); g.stroke(); g.fillStyle = '#a6abb1'; g.fillText(k.toUpperCase() + ' ' + v.toFixed(2), L + 4, Y(v) - 3); });
    g.setLineDash([]);
    (ch.swings || []).forEach(s => { const x = X(near(s.t)), y = s.kind === 'H' ? Y(s.price) - 8 : Y(s.price) + 8; g.fillStyle = '#c4c8cd'; g.beginPath(); if (s.kind === 'H') { g.moveTo(x, y + 4); g.lineTo(x - 3, y - 2); g.lineTo(x + 3, y - 2); } else { g.moveTo(x, y - 4); g.lineTo(x - 3, y + 2); g.lineTo(x + 3, y + 2); } g.fill(); });
    const col = { SUPPORTED: '#e7e9ec', CONTRADICTED: '#d98b8b', INCONCLUSIVE: '#d9c27f' };
    (ch.events || []).forEach(e => { const x = X(near(e.t)), y = e.level != null ? Y(e.level) : Y(cs[near(e.t)][4]); g.strokeStyle = g.fillStyle = col[e.status] || '#a6abb1'; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 6, 0, 7); g.stroke(); g.lineWidth = 1; g.textAlign = 'left'; g.fillText((e.sign > 0 ? '▲ ' : '▼ ') + e.study.slice(0, 28), x + 9, y - 7); });
    g.fillStyle = '#7b8087'; g.textAlign = 'left'; g.fillText(ch.sessions.join(' + ') + ' · ' + ch.tf + ' · ' + t('mie_chart_scope'), L, h - 6);
  }
  const views = {
    chart() { const c = data.chart || {}; return (c.available ? '' : `<div class="smeta">${esc(c.why || '')}</div>`) + `<canvas id="mie-cv" width="880" height="420" style="width:100%;border:1px solid var(--edge)"></canvas><div class="smeta">${t('mie_chart_legend')}</div>`; },
    overview() {
      const o = data.overview || {};
      if (!o.available) return `<div class="mie-na">${NA}</div><div class="smeta">${esc(o.note || '')}</div>`;
      const rs = Object.entries(o.regime_share || {}).sort((a, b) => b[1] - a[1]);
      return kv(t('mie_market'), esc(o.market)) + kv(t('mie_period'), esc(o.period[0] + ' → ' + o.period[1])) + kv(t('mie_sessions'), o.sessions) + kv(t('mie_bars'), Number(o.bars).toLocaleString('en'))
        + kv(t('mie_missing'), o.missing_minutes) + kv(t('mie_short'), esc((o.short_sessions || []).slice(0, 4).join(', ') || '–')) + kv(t('mie_fwd'), o.forward_sessions)
        + kv(t('mie_hidden'), esc(o.epoch.start + ' → ' + o.epoch.end) + ' · ' + t('mie_hidden_na')) + kv(t('mie_orderflow'), NA)
        + `<h3>${t('mie_regimes')}</h3>` + rs.map(([k, v]) => `<div class="mie-bar"><span>${esc(k)}</span><i style="width:${Math.round(v * 100)}%"></i><b>${pct(v)}</b></div>`).join('')
        + `<div class="smeta">${t('mie_asof', { a: esc(o.at) })}</div>`;
    },
    intelligence() {
      const i = data.intelligence || {};
      if (!i.available) return `<div class="mie-na">${NA}</div>`;
      const st = i.studies.map(s => `<tr><td>${esc(s.name)}</td><td>${chip(s.status)}</td><td>${s.n_events}</td><td>${s.n_sessions}</td><td>${num(s.mean_net)}</td><td>${num(s.diff)}</td><td>${num(s.q_value)}</td></tr>`).join('');
      const hy = i.hypotheses.map(h => `<details class="mie-h"><summary><b>${esc(h.id)}</b> ${esc(h.family)} · ${t('mie_prio')} ${h.priority} · ${t('mie_novelty')} ${h.novelty} · ${h.sent ? t('mie_sent') : t('mie_open')}</summary>
          <div><b>${t('mie_mech')}</b> ${esc(h.mechanism)}</div><div><b>${t('mie_pred')}</b> ${esc(h.prediction)}</div><div><b>${t('mie_fals')}</b> ${esc(h.falsification)}</div>
          <div><b>${t('mie_src')}</b> ${esc((h.sources || []).join('; '))}</div><div><b>${t('mie_refs')}</b> ${esc((h.obsidian_refs || []).join(', ') || NA)}</div></details>`).join('');
      return `<h3>${t('mie_studies')} (${i.studies.length})</h3><div class="mie-tw"><table class="mie-t"><tr><th></th><th>${t('mie_status')}</th><th>n</th><th>${t('mie_sess')}</th><th>ATR</th><th>Δ base</th><th>q</th></tr>${st}</table></div>
        <div class="smeta">${t('mie_dev_only')}</div><h3>${t('mie_hyps')} (${i.hypotheses.length})</h3>${hy || `<div class="smeta">${NA}</div>`}
        <h3>${t('mie_missions')}</h3>${(i.missions.open || []).map(m => `<div class="ev2"><small>${esc(m.type)} · ${m.priority}</small>${esc(m.question)}</div>`).join('') || `<div class="smeta">${NA}</div>`}`;
    },
    memory() {
      const m = data.memory || {};
      if (!m.available) return `<div class="mie-na">${NA}</div><div class="smeta">${t('mie_mem_where', { r: esc(m.root || '') })}</div>`;
      const ac = m.by_access || {}, ev = Object.entries(m.by_evidence || {});
      return kv(t('mie_vault'), m.shared_vault ? t('mie_vault_yes') : t('mie_vault_no')) + kv(t('mie_notes'), m.notes_intelligence + ' / ' + m.notes_total) + kv('dev', ac.dev || 0) + kv('owner', ac.owner || 0)
        + kv('external', ac.external || 0) + kv('locked_derived', (ac.locked_derived || 0) + ' · ' + t('mie_never_read')) + kv(t('mie_quarantine'), m.quarantined || 0) + kv(t('mie_lastw'), esc(m.last_write || NA))
        + kv(t('mie_lastr'), esc(m.last_read || NA)) + `<h3>${t('mie_by_ev')}</h3>` + (ev.map(([k, v]) => `<span class="mie-pill">${chip(k)} ${v}</span>`).join(' ') || NA)
        + `<h3>${t('mie_conf')} (${(m.conflicts || []).length})</h3>` + ((m.conflicts || []).map(c => `<div class="ev2">${esc(c.question)}<small>${esc((c.differ_in || []).join(', '))}</small></div>`).join('') || `<div class="smeta">${t('mie_none')}</div>`)
        + `<h3>${t('mie_recent')}</h3>` + (m.recent || []).map(n => `<div class="ev2"><small>${esc(n.kind)} · ${esc(n.access)}</small>${esc(n.title)} ${chip(n.evidence)}</div>`).join('');
    },
    cycle() {
      const c = data.cycle || {}, st = c.status || {};
      if (!st.status || st.status === 'idle') return `<div class="mie-na">${NA}</div><div class="smeta">${t('mie_cycle_none')}</div><button class="btn small" id="mie-start">${t('mie_start')}</button>`;
      const k = c.counts, weeks = c.weeks || [];
      const bar = weeks.map(w => `<span class="mie-wk ${st.focus === w ? 'on' : ''}">${esc(t('mie_wk_' + w))}</span>`).join('');
      return `<div class="mie-weeks">${bar}</div>` + kv(t('mie_cycle'), esc(st.id || '–') + ' · ' + esc(st.status))
        + (st.status === 'active' ? kv(t('mie_labdays'), st.lab_days_elapsed + ' / ' + st.lab_days_planned) + kv(t('mie_studies'), st.studies_run) + kv(t('mie_fwd'), st.forward_sessions) : '')
        + (k ? `<h3>${t('mie_five')}</h3>` + kv(t('mie_labdays'), k.lab_days_elapsed) + kv(t('mie_sessions'), k.market_sessions_development) + kv(t('mie_bars'), Number(k.historical_bars_1min).toLocaleString('en'))
          + kv(t('mie_events'), k.events_observed) + kv(t('mie_fwd'), k.forward_sessions) + kv(t('mie_hyps'), k.hypotheses) + `<div class="smeta">${t('mie_distinct')}</div>` : `<div class="smeta">${NA}</div>`)
        + (st.report ? `<h3>${t('mie_report')}</h3><div class="smeta">${st.report.thin ? t('mie_report_thin') : t('mie_report_ok')} · ${esc(st.report.path || '')}</div>` : '');
    }
  };

  function render() {
    if (!box) return;
    const tabs = ['overview', 'chart', 'intelligence', 'memory', 'cycle'];
    box.innerHTML = `<div class="ih"><b>${t('dp_mie')}</b><button id="mie-x">✕</button></div>
      <div class="seg mie-tabs">${tabs.map(k => `<button data-k="${k}" class="${k === tab ? 'on' : ''}">${t('mie_tab_' + k)}</button>`).join('')}</div>
      <div class="mie-body">${views[tab]()}</div>
      <div class="acts"><button class="btn small ghost" id="mie-run">${t('mie_run')}</button></div>`;
    const cv = box.querySelector('#mie-cv'); if (cv) drawChart(cv, data.chart);
    box.querySelectorAll('.mie-tabs button').forEach(b => b.onclick = () => { tab = b.dataset.k; render(); if (tab === 'memory') refresh(true); });
    box.querySelector('#mie-x').onclick = close;
    box.querySelector('#mie-run').onclick = async () => { const r = await post('/api/mie/tick'); box.querySelector('#mie-run').textContent = r.skipped ? t('mie_busy') : t('mie_started'); setTimeout(() => refresh(true), 4000); };
    const st = box.querySelector('#mie-start'); if (st) st.onclick = async () => { await post('/api/mie/cycle/start'); refresh(true); };
  }
  function open(k) {
    if (!box) { box = document.createElement('aside'); box.id = 'mie-panel'; box.className = 'panel'; document.body.appendChild(box); }
    if (k) tab = k; box.hidden = false; render(); refresh(true);
  }
  function close() { if (box) box.hidden = true; }

  // ---------- research trace inside a strategy dossier ----------
  async function trace(slug, el) {
    if (!el) return;
    const d = await get('/api/mie/trace?slug=' + encodeURIComponent(slug));
    if (d.error || !d.from_mie) { el.innerHTML = ''; return; }
    el.innerHTML = `<div class="judge"><b>${t('mie_trace')} · ${esc(d.hypothesis)}</b><div class="mie-trace">${d.steps.map(s =>
      `<div class="mie-step ${s.state === 'ok' ? 'ok' : 'na'}"><small>${esc(s.label)}</small><span>${esc(s.state === 'ok' ? s.detail : NA)}</span></div>`).join('<i>→</i>')}</div></div>`;
  }

  window.MIE = { open, close, refresh, trace, data: () => data };
  refresh(false);
  setInterval(() => refresh(false), 15000);
})();
