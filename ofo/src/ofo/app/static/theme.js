/* Reads theme.css so the 3D scene and the charts always match the page. */
(function () {
  const cs = getComputedStyle(document.documentElement);
  const css = (n, d) => (cs.getPropertyValue(n).trim() || d);
  const num = (hex) => parseInt(hex.replace('#', ''), 16);
  const THEME = {
    bg: css('--bg', '#050505'), panel: css('--panel', '#101113'), txt: css('--txt', '#E7E9EC'), dim: css('--dim', '#A6ABB1'),
    accent: css('--accent', '#E7E9EC'), grid: css('--grid', '#1C1E21'), up: css('--up', '#8FC7A6'), down: css('--down', '#D98B8B'), warn: css('--warn', '#D9C27F'), critical: css('--critical', '#FF6B6B'),
    // scientists' lab coats: 'color' = one colour per role, 'mono' = brand greys only
    coatMode: 'mono'
  };
  THEME.n = { critical: num(THEME.critical), bg: num(THEME.bg), accent: num(THEME.accent), dim: num(THEME.dim), up: num(THEME.up), down: num(THEME.down), warn: num(THEME.warn) };
  window.THEME = THEME;
})();
