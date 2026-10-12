/* Chart.js wrappers (dark theme, no animation). */
window.LabCharts = (function () {
  const C = { txt: '#8b93a8', grid: '#232a3d', green: '#5ee27a', red: '#ff5c5c', blue: '#4aa3ff', orange: '#ffb03a', purple: '#b58cff', yellow: '#ffd84a' };
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
    const names = { wf_oos: [t('ch_wf'), C.blue], locked: [t('ch_lk'), null], dev_best: [t('ch_de'), '#6b7390'] };
    const labels = [...new Set(show.flatMap(k => packs[k] ? packs[k].dates : []))].sort();
    show.forEach(k => {
      if (!packs[k]) return;
      const m = {}; packs[k].dates.forEach((d, i) => m[d] = packs[k].equity[i]);
      const last = packs[k].equity[packs[k].equity.length - 1];
      const col = names[k][1] || (last >= 0 ? C.green : C.red);
      sets.push({ label: names[k][0], data: labels.map(d => m[d] ?? null), borderColor: col, backgroundColor: col, borderWidth: k === 'dev_best' ? 1.5 : 2.2, borderDash: k === 'dev_best' ? [5, 4] : [], pointRadius: 0, spanGaps: true, tension: 0 });
    });
    return make(canvas, fix({ type: 'line', data: { labels, datasets: sets }, options: base() }));
  }
  function drawdown(canvas, p) {
    return make(canvas, fix({ type: 'line', data: { labels: p.dates, datasets: [{ label: 'Drawdown', data: p.drawdown, borderColor: C.red, backgroundColor: '#ff5c5c44', fill: true, pointRadius: 0, borderWidth: 1.6 }] }, options: base() }));
  }
  function monteCarlo(canvas, p) {
    const mc = p.monte_carlo;
    if (!mc || mc.error) return null;
    const x = Array.from({ length: mc.horizon }, (_, i) => i + 1), P = mc.percentiles;
    const line = (label, data, col, extra) => Object.assign({ label, data, borderColor: col, backgroundColor: col, pointRadius: 0, borderWidth: 1.2, tension: 0 }, extra || {});
    const sets = [
      line('95%', P['95'], '#5ee27a55', { borderWidth: 0.5 }),
      line('5%–95%', P['5'], '#5ee27a55', { fill: '-1', backgroundColor: '#5ee27a18', borderWidth: 0.5 }),
      line('75%', P['75'], '#5ee27a99', { borderWidth: 0.5 }),
      line('25%–75%', P['25'], '#5ee27a99', { fill: '-1', backgroundColor: '#5ee27a33', borderWidth: 0.5 }),
      line(t('ch_med'), P['50'], '#ffffff', { borderWidth: 2.4 })
    ];
    (mc.sample_paths || []).slice(0, 8).forEach(sp => sets.push(line('', sp, '#8b93a833', { borderWidth: 0.7 })));
    const opt = base(); opt.plugins.legend.labels.filter = (it) => it.text !== '' && it.text !== '95%' && it.text !== '75%';
    return make(canvas, fix({ type: 'line', data: { labels: x, datasets: sets }, options: opt }));
  }
  function histogram(canvas, p) {
    const e = p.histogram.edges, mid = p.histogram.counts.map((_, i) => (e[i] + e[i + 1]) / 2);
    return make(canvas, { type: 'bar', data: { labels: mid.map(m => Math.round(m)), datasets: [{ label: t('ch_days'), data: p.histogram.counts, backgroundColor: mid.map(m => m >= 0 ? '#5ee27acc' : '#ff5c5ccc') }] }, options: base({ plugins: { legend: { display: false } } }) });
  }
  function rolling(canvas, p) {
    return make(canvas, { type: 'line', data: { labels: p.dates, datasets: [{ label: t('ch_rs'), data: p.rolling_sharpe, borderColor: C.purple, pointRadius: 0, borderWidth: 1.8, spanGaps: false }] }, options: base() });
  }
  function destroyAll() { live.forEach(c => c.destroy()); live = []; }
  return { equity, drawdown, monteCarlo, histogram, rolling, destroyAll, money };
})();
