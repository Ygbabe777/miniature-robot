/* Reads theme.css so the 3D scene and the charts always match the page. */
(function () {
  const cs = getComputedStyle(document.documentElement);
  const css = (n, d) => (cs.getPropertyValue(n).trim() || d);
  const num = (hex) => parseInt(hex.replace('#', ''), 16);
  const THEME = {
    bg: css('--bg', '#050505'), panel: css('--panel', '#0b0b0c'), txt: css('--txt', '#f2f2f2'), dim: css('--dim', '#8a8a90'),
    accent: css('--accent', '#f2f2f2'), grid: css('--grid', '#202024'), up: css('--up', '#3ddc84'), down: css('--down', '#ff5c5c'), warn: css('--warn', '#d8b24a'),
    // scientists' lab coats: 'color' = one colour per role, 'mono' = brand greys only
    coatMode: 'color'
  };
  THEME.n = { bg: num(THEME.bg), accent: num(THEME.accent), dim: num(THEME.dim), up: num(THEME.up), down: num(THEME.down), warn: num(THEME.warn) };
  window.THEME = THEME;
})();
