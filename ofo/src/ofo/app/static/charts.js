/* Chart.js wrappers (dark theme, no animation). */
window.LabCharts = (function () {
  const a = (hex, al) => hex + Math.round(al * 255).toString(16).padStart(2, '0');
  const C = { txt: THEME.dim, grid: THEME.grid, green: THEME.up, red: THEME.down, blue: THEME.accent, orange: THEME.accent, purple: THEME.dim, yellow: THEME.warn };
  let live = [];
  Chart.defaults.color = C.txt; Chart.defaults.font.family = 'ui-monospace, Consolas, monospace'; Chart.defaults.font.size = 10;
  Chart.defaults.animation = false; Chart.defaults.maintainAspectRatio = false;
  const base = (extra) => Object.assign({
    responsive: true, interaction: { mode: 'index', intersect: false },
    plugins: { legend: { labels: { boxWidth: 10 } }, tooltip: { backgroundColor: '#000d' } },
    scales: { x: { grid: { color: C.grid }, ticks: { maxTicksLimit: 8 } }, y: { grid: { color: C.grid } } }
  }, extra || {});
  const make = (canvas, cfg) => { const c = new Chart(canvas, cfg); live.push(c); return c; };
  const money = (v) => (v < 0 ? '-$' : '$') + Math.abs(Math.round(v)).toLocaleString('it-IT');
  const fix = (cfg) => { cfg.options.scales.y.ticks = { callback: money }; return cfg; };

  function equity(canvas, packs, show) {
    const sets = [];
    const names = { wf_oos: [t('ch_wf'), C.blue], locked: [t('ch_lk'), '#FFFFFF'], dev_best: [t('ch_de'), '#7B8087'] };
    const labels = [...new Set(show.flatMap(k => packs[k] ? packs[k].dates : []))].sort();
    show.forEach(k => {
      if (!packs[k]) return;
      const m = {}; packs[k].dates.forEach((d, i) => m[d] = packs[k].equity[i]);
      const last = packs[k].equity[packs[k].equity.length - 1];
      const col = names[k][1] || C.blue;                       // equity lines are platinum/silver: colour is for states, not for decoration
      sets.push({ label: names[k][0], data: labels.map(d => m[d] ?? null), borderColor: col, backgroundColor: col, borderWidth: k === 'dev_best' ? 1.5 : 2.2, borderDash: k === 'dev_best' ? [5, 4] : [], pointRadius: 0, spanGaps: true, tension: 0 });
    });
    return make(canvas, fix({ type: 'line', data: { labels, datasets: sets }, options: base() }));
  }
  function drawdown(canvas, p) {
    return make(canvas, fix({ type: 'line', data: { labels: p.dates, datasets: [{ label: 'Drawdown', data: p.drawdown, borderColor: C.red, backgroundColor: a(C.red, 0.27), fill: true, pointRadius: 0, borderWidth: 1.6 }] }, options: base() }));
  }
  function monteCarlo(canvas, p) {
    const mc = p.monte_carlo;
    if (!mc || mc.error) return null;
    const x = Array.from({ length: mc.horizon }, (_, i) => i + 1), P = mc.percentiles;
    const line = (label, data, col, extra) => Object.assign({ label, data, borderColor: col, backgroundColor: col, pointRadius: 0, borderWidth: 1.2, tension: 0 }, extra || {});
    const sets = [
      line('95%', P['95'], a(THEME.accent, 0.33), { borderWidth: 0.5 }),
      line('5%–95%', P['5'], a(THEME.accent, 0.33), { fill: '-1', backgroundColor: a(THEME.accent, 0.09), borderWidth: 0.5 }),
      line('75%', P['75'], a(THEME.accent, 0.6), { borderWidth: 0.5 }),
      line('25%–75%', P['25'], a(THEME.accent, 0.6), { fill: '-1', backgroundColor: a(THEME.accent, 0.2), borderWidth: 0.5 }),
      line(t('ch_med'), P['50'], THEME.accent, { borderWidth: 2.4 })
    ];
    (mc.sample_paths || []).slice(0, 8).forEach(sp => sets.push(line('', sp, a(THEME.dim, 0.2), { borderWidth: 0.7 })));
    const opt = base(); opt.plugins.legend.labels.filter = (it) => it.text !== '' && it.text !== '95%' && it.text !== '75%';
    return make(canvas, fix({ type: 'line', data: { labels: x, datasets: sets }, options: opt }));
  }
  function histogram(canvas, p) {
    const e = p.histogram.edges, mid = p.histogram.counts.map((_, i) => (e[i] + e[i + 1]) / 2);
    return make(canvas, { type: 'bar', data: { labels: mid.map(m => Math.round(m)), datasets: [{ label: t('ch_days'), data: p.histogram.counts, backgroundColor: mid.map(m => m >= 0 ? a(THEME.dim, 0.8) : a(C.red, 0.8)) }] }, options: base({ plugins: { legend: { display: false } } }) });
  }
  function rolling(canvas, p) {
    return make(canvas, { type: 'line', data: { labels: p.dates, datasets: [{ label: t('ch_rs'), data: p.rolling_sharpe, borderColor: C.purple, pointRadius: 0, borderWidth: 1.8, spanGaps: false }] }, options: base() });
  }
  function destroyAll() { live.forEach(c => c.destroy()); live = []; }
  return { equity, drawdown, monteCarlo, histogram, rolling, destroyAll, money };
})();
