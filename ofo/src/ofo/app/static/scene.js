/* OFO Lab - dark voxel laboratory with scientists. Every animation is driven by real events. */
(function () {
  const T = THREE;
  const canvas = document.getElementById('scene');
  const overlay = document.getElementById('bubbles');
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.toneMapping = T.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.15;          // soft highlight roll-off: platinum stays platinum instead of clipping to white
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene();
  renderer.setClearColor(0x000000, 0);                         // the page paints a soft graphite gradient behind the lab
  scene.fog = new T.Fog(THEME.n.bg, 62, 125);
  const camera = new T.PerspectiveCamera(42, 1, 0.1, 300);
  camera.position.set(19.5, 19.5, 31.5);
  const controls = new T.OrbitControls(camera, canvas);
  controls.target.set(-5.5, 0.8, 5.5);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.47;
  controls.minDistance = 8; controls.maxDistance = 95;

  // ---------- MONOCHROME LUXURY: every colour the scene asks for is mapped to the OFO palette (obsidian > graphite > gunmetal > silver > platinum > white) ----------
  // by LUMINANCE, lifted so walls, desks and screens stay readable (nothing turns pure black). Colour survives only for operational states (SEM).
  const RAMP = [[0x12, 0x13, 0x15], [0x20, 0x22, 0x25], [0x4a, 0x4e, 0x54], [0xa6, 0xab, 0xb1], [0xe7, 0xe9, 0xec], [0xff, 0xff, 0xff]];       // obsidian/graphite/gunmetal lifted one step so the room stays readable
  const PAL = new Set([0x050505, 0x101113, 0x1c1e21, 0x44484d, 0xa6abb1, 0xe7e9ec, 0xffffff]);          // already brand colours: kept exactly
  const SEM = new Set([0x8fc7a6, 0xd98b8b, 0xd9c27f, 0xff6b6b]);
  function monoRGB(r, g, b) {
    const Y = Math.min(1, Math.max(0, (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255));
    const t = Math.pow(Y, 0.74) * (RAMP.length - 1), i = Math.min(RAMP.length - 2, Math.floor(t)), f = t - i, a = RAMP[i], c = RAMP[i + 1];
    return [a[0] + (c[0] - a[0]) * f, a[1] + (c[1] - a[1]) * f, a[2] + (c[2] - a[2]) * f];
  }
  function mono(c) {
    if (typeof c !== 'number' || SEM.has(c) || PAL.has(c)) return c;
    const o = monoRGB((c >> 16) & 255, (c >> 8) & 255, c & 255);
    return (Math.round(o[0]) << 16) | (Math.round(o[1]) << 8) | Math.round(o[2]);
  }
  const cssProbe = document.createElement('canvas').getContext('2d'), cssCache = {};
  function monoCss(v) {
    if (typeof v !== 'string') return v;                                  // gradients / patterns are built from already-mapped colours
    if (cssCache[v] !== undefined) return cssCache[v];
    cssProbe.fillStyle = '#000'; cssProbe.fillStyle = v;                  // the browser normalises names, rgb(), hsl(), #abc to '#rrggbb' or 'rgba(r, g, b, a)'
    const n = cssProbe.fillStyle; let r, g, b, a = 1, out = v;
    const hx = /^#([0-9a-f]{6})$/i.exec(n), rg = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/.exec(n);
    if (hx) { const k = parseInt(hx[1], 16); r = (k >> 16) & 255; g = (k >> 8) & 255; b = k & 255; }
    else if (rg) { r = +rg[1]; g = +rg[2]; b = +rg[3]; a = rg[4] == null ? 1 : +rg[4]; }
    if (r != null && !SEM.has((r << 16) | (g << 8) | b)) {
      const o = monoRGB(r, g, b).map(Math.round);
      out = a < 1 ? `rgba(${o[0]},${o[1]},${o[2]},${a})` : '#' + o.map(x => x.toString(16).padStart(2, '0')).join('');
    }
    return (cssCache[v] = out);
  }

  // dark room, cold white pools of light on every station
  scene.add(new T.HemisphereLight(0xe9eef4, 0x2a2c30, 1.1));                               // cold white ambient, graphite ground bounce
  const sun = new T.DirectionalLight(0xf6f9fd, 0.8);
  sun.position.set(22, 38, 18); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -34, right: 34, top: 34, bottom: -34, near: 1, far: 120 });
  scene.add(sun);
  const pool = (c, x, y, z, i) => { const l = new T.PointLight(mono(c), i || 0.9, 17, 1.6); l.position.set(x, y, z); scene.add(l); return l; };

  // ---------- voxel helpers ----------
  const matCache = {}, geoCache = {};
  const mat = (c, o) => { const k = c + JSON.stringify(o || {}); return matCache[k] || (matCache[k] = new T.MeshLambertMaterial(Object.assign({ color: mono(c) }, o || {}))); };
  const geo = (w, h, d) => { const k = w + '/' + h + '/' + d; return geoCache[k] || (geoCache[k] = new T.BoxGeometry(w, h, d)); };
  const glow = (c, i) => ({ emissive: mono(c), emissiveIntensity: i == null ? 0.9 : i });
  // x,z = centre on the floor, y = bottom
  function vox(parent, x, y, z, w, h, d, color, o) {
    o = o || {};
    const m = new T.Mesh(geo(w, h, d), mat(color, o.mat));
    m.position.set(x, y + h / 2, z);
    m.castShadow = o.cast != null ? o.cast : (w * h * d > 0.6); m.receiveShadow = true;
    parent.add(m); return m;
  }
  function monoCtx(g) {                      // every fill/stroke colour drawn on a sign or screen goes through the same palette
    ['fillStyle', 'strokeStyle'].forEach(k => {
      const d = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, k);
      Object.defineProperty(g, k, { get() { return d.get.call(this); }, set(v) { d.set.call(this, monoCss(v)); }, configurable: true });
    });
  }
  function paint(w, h, draw, bg) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d'); monoCtx(g); g.fillStyle = bg || '#050505'; g.fillRect(0, 0, w, h); draw(g, w, h);
    const t = new T.CanvasTexture(c); t.magFilter = T.NearestFilter; t.minFilter = T.NearestFilter;
    return { tex: t, canvas: c, g, redraw(fn, bgc) { g.fillStyle = bgc || bg || '#050505'; g.fillRect(0, 0, w, h); fn(g, w, h); t.needsUpdate = true; } };
  }
  function plane(parent, x, y, z, w, h, tt, rotY) {
    const m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ map: tt.tex }));
    m.position.set(x, y, z); m.rotation.y = rotY || 0; parent.add(m); return m;
  }
  const centered = (lines, color, size) => (g, w, h) => {
    g.fillStyle = color; g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = 'bold ' + size + 'px monospace';
    lines.forEach((l, i) => g.fillText(l, w / 2, (h / (lines.length + 1)) * (i + 1)));
  };
  let logoCanvas = null; const logoWaiters = [];
  const withLogo = cb => { if (logoCanvas) cb(logoCanvas); else logoWaiters.push(cb); };          // the supplied logo, shared by the wall emblem and the Core
  function screen2(parent, x, y, z, w, h, tt, rotY) {                                          // readable from BOTH sides (the camera sees the lab from the front-right)
    const r = rotY || 0, a = plane(parent, x, y, z, w, h, tt, r);
    const b = plane(parent, x - Math.sin(r) * 0.05, y, z - Math.cos(r) * 0.05, w, h, tt, r + Math.PI);
    return [a, b];
  }
  function sign(parent, x, y, z, w, h, lines, bg, fg, size, rotY) {
    const tt = paint(512, Math.round(512 * h / w), centered(lines, fg, size * 2), bg);
    return { mesh: plane(parent, x, y, z, w, h, tt, rotY), tt };
  }

  // ---------- room ----------
  const room = new T.Group(); scene.add(room);
  vox(room, 0, -1.0, 0, 41, 0.5, 37, 0x101113, { cast: false }); [[0, -0.52, 18.6, 41.3, 0.3], [0, -0.52, -18.6, 41.3, 0.3]].forEach(a => vox(room, a[0], a[1], a[2], a[3], 0.06, a[4], 0x44484d, { cast: false, mat: glow(0x44484d, 0.25) })); [[-20.6], [20.6]].forEach(a => vox(room, a[0], -0.52, 0, 0.3, 0.06, 37.3, 0x44484d, { cast: false, mat: glow(0x44484d, 0.25) }));   // display plinth: no void around the lab
  const X0 = -16, X1 = 16, Z0 = -14, Z1 = 14;
  vox(room, 0, -0.45, 0, X1 - X0 + 1, 0.4, Z1 - Z0 + 1, 0x020305, { cast: false });         // dark grout
  (function floor() {
    const n = (X1 - X0) * (Z1 - Z0);
    const a = new T.InstancedMesh(geo(0.94, 0.4, 0.94), mat(0x0c1017), Math.ceil(n / 2) + 1);
    const b = new T.InstancedMesh(geo(0.94, 0.4, 0.94), mat(0x10151e), Math.ceil(n / 2) + 1);
    const m4 = new T.Matrix4(); let ia = 0, ib = 0;
    for (let x = X0; x < X1; x++) for (let z = Z0; z < Z1; z++) {
      m4.setPosition(x + 0.5, -0.2, z + 0.5);
      ((x + z) & 1 ? a : b).setMatrixAt((x + z) & 1 ? ia++ : ib++, m4);
    }
    a.count = ia; b.count = ib; a.receiveShadow = b.receiveShadow = true; room.add(a, b);
  })();
  vox(room, 0, 0, Z0 - 0.5, X1 - X0 + 1, 13, 1, 0x080b10);                                    // back wall
  vox(room, X0 - 0.5, 0, 0, 1, 13, Z1 - Z0, 0x070a0f);                                        // left wall
  for (let i = -7; i <= 7; i++) vox(room, i * 2.2, 0.7, Z0 + 0.12, 2.0, 5.4, 0.1, i % 2 ? 0x0e131c : 0x0b0f17, { cast: false });
  for (let j = -6; j <= 6; j++) vox(room, X0 + 0.12, 0.7, j * 2.2, 0.1, 5.4, 2.0, j % 2 ? 0x0e131c : 0x0b0f17, { cast: false });
  vox(room, 0, 0, Z0 + 0.1, X1 - X0, 0.8, 0.3, 0x05070b); vox(room, X0 + 0.1, 0, 0, 0.3, 0.8, Z1 - Z0, 0x05070b);
  vox(room, 0, 12.3, Z0 + 0.2, X1 - X0, 0.25, 0.2, 0x8a8a90, { cast: false, mat: glow(0xcfcfd6, 0.7) });   // cyan trim
  vox(room, X0 + 0.2, 12.3, 0, 0.2, 0.25, Z1 - Z0, 0x8a8a90, { cast: false, mat: glow(0xcfcfd6, 0.7) });
  const ofoSign = sign(room, 0, 9.2, Z0 + 0.02, 11, 1.9, ['OFO  LAB'], '#050505', THEME.accent, 44);
  // ---------- the OFO emblem: backlit metal plaque on the main wall. The supplied logo (brand/icon.png), never redrawn. ----------
  (function emblem() {
    const img = new Image();
    img.onload = () => {
      const lc = document.createElement('canvas'); lc.width = img.width * 4; lc.height = img.height * 4;                // 4x smooth upscale of the supplied file
      const lg = lc.getContext('2d'); lg.imageSmoothingEnabled = true; lg.imageSmoothingQuality = 'high';
      lg.filter = 'brightness(1.9) contrast(1.15)'; lg.drawImage(img, 0, 0, lc.width, lc.height);                      // lit from behind: same artwork, more light
      logoCanvas = lc; logoWaiters.splice(0).forEach(f => f(lc));
      const tex = new T.CanvasTexture(lc); tex.anisotropy = 8; tex.magFilter = T.LinearFilter; tex.minFilter = T.LinearMipmapLinearFilter; tex.needsUpdate = true;
      if (T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace; else if (T.sRGBEncoding) tex.encoding = T.sRGBEncoding;
      const W = 2.5, H = W * img.height / img.width, cx = 0, cy = 10.9, z = Z0 + 0.36;
      const hc = document.createElement('canvas'); hc.width = hc.height = 256; const hg = hc.getContext('2d');
      const hr = hg.createRadialGradient(128, 128, 6, 128, 128, 128); hr.addColorStop(0, 'rgba(255,255,255,0.5)'); hr.addColorStop(0.4, 'rgba(231,233,236,0.14)'); hr.addColorStop(1, 'rgba(231,233,236,0)');
      hg.fillStyle = '#000'; hg.fillRect(0, 0, 256, 256); hg.fillStyle = hr; hg.fillRect(0, 0, 256, 256);
      const halo = { tex: new T.CanvasTexture(hc) };
      const hm = new T.Mesh(new T.PlaneGeometry(9, 6), new T.MeshBasicMaterial({ map: halo.tex, transparent: true, blending: T.AdditiveBlending, depthWrite: false }));
      hm.position.set(cx, cy - 0.6, Z0 + 0.04); room.add(hm);
      vox(room, cx, cy - H / 2 - 0.14, z - 0.1, W + 0.5, H + 0.28, 0.14, 0x1c1e21, { cast: false });                       // dark steel slab
      [[0, H / 2 + 0.17, W + 0.52, 0.05], [0, -H / 2 - 0.17, W + 0.52, 0.05]].forEach(([dx, dy, w, h]) => vox(room, cx + dx, cy + dy - h / 2, z - 0.02, w, h, 0.06, 0xa6abb1, { cast: false, mat: glow(0xa6abb1, 0.35) }));
      [[-(W / 2 + 0.25), 0], [W / 2 + 0.25, 0]].forEach(([dx]) => vox(room, cx + dx, cy - H / 2 - 0.14, z - 0.02, 0.05, H + 0.28, 0.06, 0xa6abb1, { cast: false, mat: glow(0xa6abb1, 0.35) }));
      const pm = new T.Mesh(new T.PlaneGeometry(W, H), new T.MeshBasicMaterial({ map: tex }));
      pm.position.set(cx, cy, z); room.add(pm);
      const lt = new T.PointLight(0xffffff, 0.9, 16, 1.4); lt.position.set(cx, cy, Z0 + 2.2); scene.add(lt);            // the plaque lights the wall in front of it
      ofoSign.mesh.visible = false;
    };
    img.onerror = () => {};                                                                                           // no logo file: the text sign stays
    img.src = 'brand/icon.png';
  })();
  
  // clock
  const clock = paint(256, 256, (g, w, h) => {
    g.strokeStyle = '#8b93a8'; g.lineWidth = 12; g.beginPath(); g.arc(128, 128, 112, 0, 6.3); g.stroke();
    const d = new Date(), hr = (d.getHours() % 12 + d.getMinutes() / 60) / 12 * 6.283, mn = d.getMinutes() / 60 * 6.283;
    g.strokeStyle = '#e6e9f2'; g.lineWidth = 14; g.beginPath(); g.moveTo(128, 128); g.lineTo(128 + Math.sin(hr) * 60, 128 - Math.cos(hr) * 60); g.stroke();
    g.lineWidth = 9; g.beginPath(); g.moveTo(128, 128); g.lineTo(128 + Math.sin(mn) * 90, 128 - Math.cos(mn) * 90); g.stroke();
  }, '#0a0d14');
  plane(room, X0 + 0.03, 9.6, -10.2, 1.8, 1.8, clock, Math.PI / 2);
  // hazard sign
  const haz = paint(512, 256, (g, w, h) => {
    for (let i = -h; i < w; i += 48) { g.fillStyle = '#e8e8ec'; g.beginPath(); g.moveTo(i, 0); g.lineTo(i + 24, 0); g.lineTo(i + 24 - h, h); g.lineTo(i - h, h); g.fill(); }
    g.fillStyle = '#111'; g.fillRect(24, 48, w - 48, h - 96);
    g.fillStyle = '#f2f2f2'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.font = 'bold 44px monospace'; g.fillText('CAUTION', w / 2, 92); g.font = 'bold 52px monospace'; g.fillStyle = '#ff5c5c'; g.fillText('OVERFITTING', w / 2, 162);
  }, '#e8e8ec');
  plane(room, X0 + 0.03, 6.2, -6.0, 3.6, 1.8, haz, Math.PI / 2);

  // ---------- belt: the life of a strategy. Research -> Optimization -> Validation -> Core -> Judge -> Vault / Archive ----------
  const PATH = [[-9, -3.6], [9, -3.6], [9, 2.2], [5.6, 2.2], [5.6, 7.4], [-9, 7.4], [-9, 9.8]];
  const segLen = [], cum = [0];
  for (let i = 0; i < PATH.length - 1; i++) { segLen.push(Math.hypot(PATH[i + 1][0] - PATH[i][0], PATH[i + 1][1] - PATH[i][1])); cum.push(cum[i] + segLen[i]); }
  const TOTAL = cum[cum.length - 1];
  function segAt(s) { for (let i = 0; i < segLen.length; i++) if (s <= cum[i + 1] || i === segLen.length - 1) return i; return 0; }
  function pointAt(s) {
    s = Math.max(0, Math.min(TOTAL, s));
    const i = segAt(s), t = segLen[i] ? (s - cum[i]) / segLen[i] : 0, a = PATH[i], b = PATH[i + 1];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  }
  for (let s = 0; s <= TOTAL; s += 1) {
    const p = pointAt(s), i = segAt(Math.min(s, TOTAL - 0.01)), horiz = PATH[i + 1][0] !== PATH[i][0];
    vox(room, p[0], 0, p[1], 1.6, 0.35, 1.6, 0x0b0d12, { cast: false });
    const k = Math.floor(s) % 2 ? 0xd9d9de : 0x16181d;       // hazard edge stripes
    vox(room, p[0] + (horiz ? 0 : 1.05), 0, p[1] + (horiz ? 1.05 : 0), horiz ? 1 : 0.28, 0.06, horiz ? 0.28 : 1, k, { cast: false });
    vox(room, p[0] - (horiz ? 0 : 1.05), 0, p[1] - (horiz ? 1.05 : 0), horiz ? 1 : 0.28, 0.06, horiz ? 0.28 : 1, k, { cast: false });
  }
  const chevrons = [];
  for (let s = 0; s < TOTAL; s += 2.5) { const m = vox(room, 0, 0.36, 0, 0.5, 0.08, 0.5, 0xf2f2f2, { cast: false, mat: glow(0xf2f2f2, 0.8) }); m.userData.s = s; chevrons.push(m); }
  const sAtX = (seg, x) => cum[seg] + Math.abs(x - PATH[seg][0]);
  const STOP = { research: 0.6, optimizer: sAtX(0, 0.5), validator: cum[1], core: cum[3], judge: sAtX(4, 0), vault: TOTAL - 0.3 };

  // ---------- scientists ----------
  const robots = {};
  function makeScientist(name, label, o) {
    const g = new T.Group();
    const skin = o.skin || 0xe0b48c, hair = o.hair || 0x2a1d14, coat = o.coat;
    [-0.42, 0.42].forEach(sx => { vox(g, sx, 0, 0.1, 0.55, 0.32, 0.95, 0x08090c); vox(g, sx, 0.3, 0, 0.5, 0.95, 0.55, 0x1a2030); });
    vox(g, 0, 0.55, 0, 2.0, 0.65, 1.2, coat);                                                   // long coat (skirt)
    vox(g, 0, 1.15, 0, 1.9, 1.8, 1.1, coat);                                                    // coat body
    vox(g, 0, 1.2, 0.57, 0.55, 1.7, 0.08, o.shirt || 0x1f2a3a);                                 // shirt under the open coat
    vox(g, -0.42, 2.35, 0.58, 0.32, 0.55, 0.1, o.trim || coat); vox(g, 0.42, 2.35, 0.58, 0.32, 0.55, 0.1, o.trim || coat);
    vox(g, 0.62, 1.7, 0.6, 0.5, 0.42, 0.06, o.pocket || 0xcfd5e2); vox(g, 0.72, 2.0, 0.62, 0.09, 0.32, 0.06, o.pen || 0x4aa3ff);
    vox(g, -0.62, 2.15, 0.6, 0.46, 0.3, 0.06, 0xf4f6fb); vox(g, -0.62, 2.15, 0.64, 0.46, 0.08, 0.04, o.accent, { mat: glow(o.accent, 0.6) });
    const armL = new T.Group(), armR = new T.Group();
    [[armL, -1.2], [armR, 1.2]].forEach(([a, x]) => { vox(a, 0, -1.4, 0, 0.55, 1.4, 0.62, coat); vox(a, 0, -1.7, 0, 0.46, 0.34, 0.5, skin); a.position.set(x, 2.8, 0); g.add(a); });
    vox(g, 0, 2.95, 0, 0.5, 0.25, 0.5, skin);
    const head = new T.Group(); head.position.y = 3.55; g.add(head);
    const bulb = vox(head, 0, 1.55, 0, 0.3, 0.3, 0.3, 0xffffff, { cast: false, mat: glow(0xffffff, 1.3) }); bulb.visible = false;
    vox(head, 0, 0, 0, 1.3, 1.3, 1.2, skin);
    const style = o.hairStyle || 'short';
    if (style !== 'bald') {
      vox(head, 0, 0.78, -0.04, 1.4, 0.5, 1.28, hair); vox(head, 0, 0.1, -0.62, 1.4, 1.1, 0.16, hair);
      vox(head, -0.7, 0.35, 0, 0.12, 0.7, 1.1, hair); vox(head, 0.7, 0.35, 0, 0.12, 0.7, 1.1, hair);
    }
    if (style === 'tall') { vox(head, -0.3, 1.25, 0, 0.35, 0.5, 0.35, hair); vox(head, 0.25, 1.35, 0.1, 0.35, 0.65, 0.35, hair); vox(head, 0, 1.2, -0.3, 0.4, 0.4, 0.4, hair); }
    if (style === 'bun') vox(head, 0, 1.1, -0.55, 0.55, 0.55, 0.55, hair);
    vox(head, -0.3, 0.1, 0.61, 0.2, 0.2, 0.06, 0x0b0c10, { cast: false }); vox(head, 0.3, 0.1, 0.61, 0.2, 0.2, 0.06, 0x0b0c10, { cast: false });
    vox(head, 0, -0.35, 0.61, 0.4, 0.08, 0.06, 0x6b2f2f, { cast: false });
    if (o.glasses !== false) {
      [-0.3, 0.3].forEach(x => { vox(head, x, 0.1, 0.66, 0.46, 0.36, 0.05, 0x14161c, { cast: false }); vox(head, x, 0.1, 0.69, 0.32, 0.24, 0.03, 0xa9dcff, { cast: false, mat: glow(0x2a6a8a, 0.7) }); });
      vox(head, 0, 0.12, 0.66, 0.16, 0.08, 0.05, 0x14161c, { cast: false });
    }
    if (o.goggles) { vox(head, 0, 0.62, 0.0, 1.4, 0.16, 1.3, 0x1a1c22, { cast: false }); vox(head, 0, 0.55, 0.64, 1.1, 0.32, 0.1, 0xc8ff3a, { cast: false, mat: glow(0xc8ff3a, 0.8) }); }
    if (o.headset) { vox(head, 0, 0.7, 0, 1.45, 0.12, 0.22, 0x14161c, { cast: false }); [-0.74, 0.74].forEach(x => vox(head, x, -0.1, 0, 0.2, 0.55, 0.5, 0x14161c, { cast: false })); vox(head, 0.5, -0.4, 0.7, 0.55, 0.08, 0.08, 0x14161c, { cast: false }); }
    if (o.item === 'clipboard') { vox(armL, 0.0, -2.1, 0.55, 0.8, 1.0, 0.08, 0x8a6a3a); vox(armL, 0.0, -2.05, 0.6, 0.65, 0.85, 0.04, 0xf4f1e6, { cast: false }); }
    if (o.item === 'tablet') vox(armR, 0, -2.0, 0.5, 0.9, 0.7, 0.08, 0x2adfff, { cast: false, mat: glow(0x2adfff, 0.8) });
    if (o.item === 'flask') { vox(armR, 0, -2.2, 0.4, 0.5, 0.7, 0.5, 0x7dffb0, { cast: false, mat: Object.assign({ transparent: true, opacity: 0.85 }, glow(0x2aff8a, 0.9)) }); }
    if (o.item === 'gavel') { vox(armR, 0, -1.95, 0.5, 0.2, 0.2, 0.8, 0x8b5a2b, { cast: false }); vox(armR, 0, -2.05, 0.9, 0.5, 0.5, 0.5, 0xc58a46, { cast: false }); }
    // silhouettes: agents are told apart by SHAPE, not by colour
    if (o.antenna) { vox(head, 0.38, 0.65, 0, 0.1, 1.0, 0.1, 0xa6abb1, { cast: false }); vox(head, 0.38, 1.62, 0, 0.26, 0.26, 0.26, 0xffffff, { cast: false, mat: glow(0xffffff, 1.4) }); }
    if (o.pauldron) [-1.3, 1.3].forEach(x => { vox(g, x, 2.72, 0, 0.95, 0.42, 1.05, o.trim || coat); vox(g, x, 3.1, 0, 0.7, 0.12, 0.8, 0xe7e9ec, { cast: false }); });
    if (o.halo) { vox(head, 0, 1.62, 0, 1.15, 0.08, 0.08, 0xffffff, { cast: false, mat: glow(0xffffff, 1.2) }); vox(head, 0, 1.62, 0, 0.08, 0.08, 1.15, 0xffffff, { cast: false, mat: glow(0xffffff, 1.2) }); }
    if (o.hardhat) { vox(head, 0, 0.7, 0.05, 1.55, 0.55, 1.5, 0xe7e9ec); vox(head, 0, 0.62, 0.85, 1.25, 0.12, 0.45, 0xe7e9ec, { cast: false }); vox(head, 0, 1.22, 0, 0.5, 0.18, 1.1, 0xa6abb1, { cast: false }); }
    if (o.cap) { vox(head, 0, 0.72, 0.1, 1.5, 0.4, 1.45, 0x1c1e21); vox(head, 0, 0.66, 0.85, 1.3, 0.12, 0.5, 0xa6abb1, { cast: false }); }
    const tag = document.createElement('div'); tag.className = 'tag'; tag.textContent = label; overlay.appendChild(tag);
    const bub = document.createElement('div'); bub.className = 'bubble'; overlay.appendChild(bub);
    const r = { name, label, g, armL, armR, head, bulb, tag, bub, until: 0, bubUntil: 0, phase: Math.random() * 6, base: 0, state: 'idle', shown: '' };
    robots[name] = r; g.scale.setScalar(0.62); scene.add(g); return r;
  }
  function place(r, x, z, faceX, faceZ, y) { r.g.position.set(x, y || 0, z); r.base = y || 0; r.g.rotation.y = Math.atan2(faceX - x, faceZ - z); }
  const pickables = [];
  const tagStation = (obj, st) => obj.traverse(o => { if (o.isMesh) { o.userData.station = st; pickables.push(o); } });
  const rack = [], leds = [], bubbles = [], flasks = [], parts = [];

  // --- RESEARCHER: library + reading desk
  const libG = new T.Group(); scene.add(libG);
  [-14.2, -12.2].forEach((sx, k) => {
    vox(libG, sx, 0, -13.1, 1.9, 7.2, 1.4, 0x2c1e12);
    for (let row = 0; row < 5; row++) {
      vox(libG, sx, 1.1 + row * 1.25, -12.9, 1.7, 0.1, 1.1, 0x4a3320, { cast: false });
      for (let b = 0; b < 4; b++) vox(libG, sx - 0.6 + b * 0.4, 1.2 + row * 1.25, -12.55, 0.3, 1.0 + (b % 2) * 0.1, 0.7, [0xc23b3b, 0x2f6fb5, 0xc9a227, 0x2f9e5a, 0x7a55c2][(row + b + k) % 5], { cast: false });
    }
  });
  vox(libG, -8.4, 0, -9.6, 4.6, 1.5, 2.3, 0x1a140e); vox(libG, -8.4, 1.5, -9.6, 5.0, 0.22, 2.6, 0x3a2a1c);
  const pdfScreen = paint(512, 384, (g, w, h) => { g.fillStyle = '#e9e6dd'; g.fillRect(40, 24, w - 80, h - 48); g.fillStyle = '#334'; g.font = 'bold 26px monospace'; g.fillText('MOMENTUM IN INTRADAY', 60, 70); g.fillRect(60, 95, 330, 6); for (let i = 0; i < 9; i++) g.fillRect(60, 130 + i * 24, 250 + (i * 53) % 150, 8); g.fillStyle = '#c33'; g.fillRect(60, 345, 120, 8); }, '#10131a');
  vox(libG, -8.4, 1.72, -10.2, 2.4, 1.7, 0.2, 0x07080b); plane(libG, -8.4, 2.6, -10.08, 2.2, 1.5, pdfScreen, 0);
  vox(libG, -8.4, 1.72, -9.1, 1.4, 0.06, 0.5, 0x15171d, { cast: false });
  for (let i = 0; i < 4; i++) vox(libG, -10.2, 1.72 + i * 0.1, -9.5, 1.0, 0.1, 1.3, i % 2 ? 0xe8e6dc : 0xd5d2c6, { cast: false });
  vox(libG, -6.5, 1.72, -9.4, 0.4, 0.5, 0.4, 0xe8e6dc); vox(libG, -6.1, 1.72, -10.3, 0.3, 1.2, 0.3, 0x30343f); vox(libG, -6.1, 2.9, -10.0, 0.9, 0.3, 0.9, 0xffd84a, { cast: false, mat: glow(0xffd84a, 0.9) });
  sign(libG, -9.4, 8.1, -13.85, 6.2, 1.2, ['RESEARCH WING'], '#050505', THEME.accent, 22);
  tagStation(libG, 'research');


  // --- OPTIMIZER: server racks + parameter sweep machine
  const optG = new T.Group(); scene.add(optG); optG.position.x = 2.2;   // OPTIMIZATION BAY: back wall, centre
  [-4.2, -2.2, -0.2].forEach(rx => {
    vox(optG, rx, 0, -12.6, 1.8, 7.0, 1.6, 0x0f131b);
    for (let row = 0; row < 7; row++) {
      vox(optG, rx, 0.5 + row * 0.9, -11.78, 1.5, 0.62, 0.06, 0x070a0f, { cast: false });
      for (let c = 0; c < 3; c++) { const l = vox(optG, rx - 0.45 + c * 0.45, 0.65 + row * 0.9, -11.74, 0.16, 0.16, 0.05, [0xf2f2f2, 0x9a9aa2, 0xe8e8ec][(row + c) % 3], { cast: false, mat: glow([0xf2f2f2, 0x9a9aa2, 0xe8e8ec][(row + c) % 3], 1.0) }); leds.push(l); }
    }
  });
  vox(optG, -1.7, 0, -8.7, 5.0, 2.6, 2.8, 0x1a1f2b); vox(optG, -1.7, 2.6, -8.7, 5.3, 0.3, 3.1, 0x0f131b);
  vox(optG, -1.7, 0.4, -7.28, 4.4, 1.6, 0.06, 0x05070b);
  const gears = [];
  [[-3.1, 1.2], [-0.3, 1.2]].forEach(([gx, gy], gi) => {
    const gr = new T.Group(); gr.position.set(gx, gy, -7.2);
    vox(gr, 0, -0.65, 0, 1.3, 1.3, 0.25, 0xe08a1a, { mat: glow(0x7a3d00, 0.8), cast: false });
    for (let t = 0; t < 8; t++) { const a = t * Math.PI / 4; vox(gr, Math.cos(a) * 0.9, -0.65 + Math.sin(a) * 0.9 - 0.2, 0, 0.4, 0.4, 0.25, 0xb86a10, { cast: false }); }
    gr.userData.dir = gi ? -1 : 1; optG.add(gr); gears.push(gr);
  });
  const heat = paint(256, 192, () => {}, '#05080d');
  const drawHeat = () => heat.redraw((g, w, h) => { for (let i = 0; i < 16; i++) for (let j = 0; j < 10; j++) { const v = (Math.sin(i * 0.5 + performance.now() / 700) + Math.cos(j * 0.6)) / 4 + 0.5 + (Math.random() - 0.5) * 0.25; g.fillStyle = `hsl(${200 - v * 150},80%,${22 + v * 35}%)`; g.fillRect(8 + i * 15, 8 + j * 17, 13, 15); } });
  drawHeat();
  vox(optG, 2.3, 1.6, -9.0, 0.2, 2.6, 0.2, 0x15171d); vox(optG, 2.3, 4.0, -9.0, 2.7, 2.2, 0.2, 0x07080b); plane(optG, 2.3, 5.1, -8.88, 2.5, 1.9, heat, 0);
  sign(optG, -2.2, 8.1, -13.85, 6.4, 1.2, ['OPTIMIZATION BAY'], '#050505', THEME.accent, 19);
  tagStation(optG, 'optimizer');


  // --- VALIDATOR: bench with flasks + microscope, and the Monte Carlo glass chamber
  const valG = new T.Group(); scene.add(valG); valG.position.x = 5.6;   // VALIDATION CHAMBER: back wall, right
  vox(valG, 3.0, 0, -9.6, 3.6, 1.6, 2.0, 0x161b26); vox(valG, 3.0, 1.6, -9.6, 3.9, 0.2, 2.3, 0x2c3342);
  [[2.1, 0x2aff8a], [3.0, 0xb58cff], [3.9, 0x2adfff]].forEach(([fx, fc], i) => {
    vox(valG, fx, 1.8, -9.9, 0.8, 0.7, 0.8, fc, { cast: false, mat: Object.assign({ transparent: true, opacity: 0.8 }, glow(fc, 1.0)) });
    vox(valG, fx, 2.5, -9.9, 0.3, 0.7, 0.3, 0xcfe8ff, { cast: false, mat: { transparent: true, opacity: 0.4 } });
    flasks.push(valG.children[valG.children.length - 2]);
  });
  vox(valG, 4.4, 1.8, -8.9, 0.8, 0.15, 0.8, 0x22262f); vox(valG, 4.4, 1.95, -9.1, 0.18, 1.1, 0.18, 0x22262f); vox(valG, 4.4, 2.9, -8.9, 0.35, 0.35, 0.9, 0x15171d); vox(valG, 4.4, 2.6, -8.45, 0.25, 0.5, 0.25, 0xc9ccd6);
  vox(valG, 6.9, 0, -10.0, 5.2, 0.5, 4.2, 0x1a1f2b);
  vox(valG, 6.9, 0.5, -10.0, 4.8, 4.6, 3.8, 0x9b7bff, { cast: false, mat: { transparent: true, opacity: 0.16, depthWrite: false } });
  vox(valG, 6.9, 5.1, -10.0, 5.2, 0.45, 4.2, 0x1a1f2b);
  for (let i = 0; i < 26; i++) {
    const p = vox(valG, 6.9, 1, -10, 0.34, 0.34, 0.34, [0xb58cff, 0xffffff, 0x7dd3fc][i % 3], { cast: false, mat: glow(0x442a88, 1.0) });
    p.userData = { v: [(Math.random() - .5), (Math.random() - .5), (Math.random() - .5)], p: [Math.random() * 4 - 2, Math.random() * 4 + 0.8, Math.random() * 3 - 1.5] }; parts.push(p);
  }
  sign(valG, 4.4, 8.1, -13.85, 7.4, 1.2, ['VALIDATION CHAMBER'], '#050505', THEME.accent, 17);

  // statistical screens above the bench: walk-forward and equity curve of the LAST real study (data comes from the backend, never invented)
  const wfScreen = paint(512, 320, () => {}, '#050505'), eqScreen = paint(512, 320, () => {}, '#050505');
  [[wfScreen, 6.9], [eqScreen, 10.0]].forEach(([tt, x]) => { vox(valG, x - 5.6 + 5.6 - 5.6 + 0, 3.55, -13.55, 2.9, 2.2, 0.16, 0x101113, { cast: false }); screen2(valG, x - 5.6, 4.65, -13.43, 2.7, 1.95, tt, 0); });
  function drawValidation(d) {
    const frame = (tt, title, body) => tt.redraw((g, w, h) => {
      g.fillStyle = '#a6abb1'; g.font = 'bold 26px monospace'; g.textAlign = 'left'; g.fillText(title, 22, 38);
      g.strokeStyle = '#44484d'; g.lineWidth = 2; g.strokeRect(14, 52, w - 28, h - 70);
      body(g, 28, 66, w - 56, h - 100);
    }, '#050505');
    const noData = (g, x, y, w, h) => { g.fillStyle = '#44484d'; g.font = '24px monospace'; g.textAlign = 'center'; g.fillText('no study yet', x + w / 2, y + h / 2); };
    const eq = d && d.equity && d.equity.length > 2 ? d.equity : null;
    frame(eqScreen, 'EQUITY CURVE' + (d && d.name ? ' · ' + d.name.slice(0, 16) : ''), (g, x, y, w, h) => {
      if (!eq) return noData(g, x, y, w, h);
      const lo = Math.min(0, ...eq), hi = Math.max(0, ...eq), Y = v => y + h - (v - lo) / ((hi - lo) || 1) * h, X = i => x + i / (eq.length - 1) * w;
      g.strokeStyle = '#44484d'; g.lineWidth = 1; g.beginPath(); g.moveTo(x, Y(0)); g.lineTo(x + w, Y(0)); g.stroke();
      g.strokeStyle = '#e7e9ec'; g.lineWidth = 3; g.beginPath(); eq.forEach((v, i) => i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))); g.stroke();
    });
    frame(wfScreen, 'WALK-FORWARD', (g, x, y, w, h) => {
      if (!eq) return noData(g, x, y, w, h);
      const n = Math.min(12, Math.max(4, Math.floor(eq.length / 20))), step = Math.floor(eq.length / n), bars = [];
      for (let i = 0; i < n; i++) bars.push(eq[Math.min(eq.length - 1, (i + 1) * step - 1)] - (i ? eq[i * step - 1] : 0));
      const m = Math.max(1, ...bars.map(Math.abs)), bw = w / n, mid = y + h / 2;
      bars.forEach((v, i) => { g.fillStyle = v >= 0 ? '#e7e9ec' : '#44484d'; const bh = Math.abs(v) / m * (h / 2 - 6); g.fillRect(x + i * bw + 4, v >= 0 ? mid - bh : mid, bw - 8, Math.max(2, bh)); });
      g.strokeStyle = '#a6abb1'; g.beginPath(); g.moveTo(x, mid); g.lineTo(x + w, mid); g.stroke();
    });
  }
  drawValidation(null);
  tagStation(valG, 'validator');

  // ======================= OFO CORE: the heart of the lab (centre of the room) =======================
  // A large hexagonal table in black metal and smoked glass. The OFO emblem is projected at its centre. Every dossier passes through it.
  const coreG = new T.Group(); scene.add(coreG);
  const CX = 0, CZ = 2.2;
  function hexPrism(parent, r, h, y, color, o) {
    const m = new T.Mesh(new T.CylinderGeometry(r, r, h, 6), mat(color, o)); m.rotation.y = Math.PI / 6;
    m.position.set(CX, y + h / 2, CZ); m.castShadow = true; m.receiveShadow = true; parent.add(m); return m;
  }
  hexPrism(coreG, 5.0, 0.3, 0, 0x1c1e21); hexPrism(coreG, 4.7, 0.22, 0.3, 0x44484d, glow(0x44484d, 0.12));
  hexPrism(coreG, 1.9, 1.2, 0.5, 0x101113);
  for (let k = 0; k < 6; k++) { const a = k * Math.PI / 3 + Math.PI / 6; vox(coreG, CX + Math.cos(a) * 3.3, 0.5, CZ + Math.sin(a) * 3.3, 0.7, 1.25, 0.7, 0x101113); }
  hexPrism(coreG, 4.5, 0.32, 1.7, 0x101113);                                                                // black metal top
  hexPrism(coreG, 4.56, 0.07, 1.92, 0xa6abb1, glow(0xa6abb1, 0.55));                                         // silver rim light
  hexPrism(coreG, 3.95, 0.1, 2.0, 0x1c1e21, { transparent: true, opacity: 0.6, depthWrite: false });        // smoked glass inset
  const coreGlow = new T.Mesh(new T.CircleGeometry(3.4, 48), new T.MeshBasicMaterial({ transparent: true, opacity: 0.0, color: 0xe7e9ec, blending: T.AdditiveBlending, depthWrite: false }));
  coreGlow.rotation.x = -Math.PI / 2; coreGlow.position.set(CX, 2.13, CZ); coreG.add(coreGlow);
  withLogo(lc => {                                                                                         // the emblem projected onto the glass
    const tex = new T.CanvasTexture(lc); tex.anisotropy = 8; if (T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace;
    const pm = new T.Mesh(new T.PlaneGeometry(3.2, 3.2 * lc.height / lc.width), new T.MeshBasicMaterial({ map: tex, transparent: true, blending: T.AdditiveBlending, depthWrite: false }));
    pm.rotation.x = -Math.PI / 2; pm.rotation.z = Math.PI / 4; pm.position.set(CX, 2.16, CZ); coreG.add(pm);
  });
  const coreRing = new T.Group(); coreRing.position.set(CX, 2.35, CZ); coreG.add(coreRing);                    // slow ring of light: speeds up while the engine works
  for (let i = 0; i < 24; i++) { const a = i / 24 * Math.PI * 2; vox(coreRing, Math.cos(a) * 3.55, 0, Math.sin(a) * 3.55, i % 6 === 0 ? 0.3 : 0.14, 0.06, i % 6 === 0 ? 0.3 : 0.14, 0xffffff, { cast: false, mat: glow(0xffffff, i % 6 === 0 ? 1.2 : 0.6) }); }
  for (let k = 0; k < 6; k++) { const a = k * Math.PI / 3; vox(coreG, CX + Math.cos(a) * 5.15, 0.3, CZ + Math.sin(a) * 5.15, 0.36, 0.9, 0.36, 0x1c1e21); vox(coreG, CX + Math.cos(a) * 5.15, 1.2, CZ + Math.sin(a) * 5.15, 0.2, 0.2, 0.2, 0xffffff, { cast: false, mat: glow(0xffffff, 1.0) }); }
  sign(coreG, CX, 0.42, CZ + 6.5, 4.4, 0.6, ['OFO  CORE'], '#050505', THEME.accent, 24, 0);
  tagStation(coreG, 'core');

  // ======================= RISK MANAGER: risk analysis and prop compatibility (centre right) =======================
  const riskG = new T.Group(); scene.add(riskG);
  vox(riskG, 15.1, 0, 2.2, 1.2, 0.9, 5.6, 0x1c1e21); vox(riskG, 15.1, 0.9, 2.2, 1.4, 0.12, 5.9, 0x44484d);                      // low console at the right edge
  const riskA = paint(512, 320, () => {}, '#050505'), riskB = paint(512, 320, () => {}, '#050505');
  [[riskA, 0.4], [riskB, 3.9]].forEach(([tt, z]) => { vox(riskG, 10.9, 0, z, 0.25, 2.9, 0.3, 0x101113, { cast: false }); vox(riskG, 10.9, 2.9, z, 0.2, 2.2, 3.0, 0x101113, { cast: false }); screen2(riskG, 11.02, 4.0, z, 2.8, 1.9, tt, Math.PI / 2); });
  sign(riskG, 15.83, 0.45, 2.2, 5.0, 0.6, ['RISK MANAGER'], '#050505', THEME.accent, 22, Math.PI / 2);
  function drawRisk(lines) {
    const draw = (tt, head, rows) => tt.redraw((g, w, h) => {
      g.fillStyle = '#a6abb1'; g.font = 'bold 26px monospace'; g.textAlign = 'left'; g.fillText(head, 22, 40);
      g.fillStyle = '#e7e9ec'; g.font = '22px monospace'; rows.slice(0, 7).forEach((r, i) => g.fillText(String(r).slice(0, 34), 22, 90 + i * 34));
    }, '#050505');
    draw(riskA, 'RISK · LATEST STUDY', lines && lines.study ? lines.study : ['waiting for a study']);
    draw(riskB, 'PROP COMPATIBILITY', lines && lines.accounts ? lines.accounts : ['no account data']);
  }
  drawRisk(null);
  tagStation(riskG, 'risk');

  // ======================= THE JUDGE: a raised, authoritative station at the front centre =======================
  const judG = new T.Group(); scene.add(judG);
  const JX = 0, JZ = 11.0;
  vox(judG, JX, 0, JZ, 7.4, 0.9, 4.4, 0x2a2d31); [[0, JZ - 2.28, 7.7, 0.12], [0, JZ + 2.28, 7.7, 0.12]].forEach(a => vox(judG, a[0], 0.9, a[1], a[2], 0.07, a[3], 0xa6abb1, { cast: false, mat: glow(0xa6abb1, 0.4) })); [[-3.78], [3.78]].forEach(a => vox(judG, a[0], 0.9, JZ, 0.12, 0.07, 4.6, 0xa6abb1, { cast: false, mat: glow(0xa6abb1, 0.4) }));
  vox(judG, JX, 0, JZ - 2.7, 3.6, 0.45, 0.9, 0x44484d); vox(judG, JX, 0, JZ - 2.25, 3.0, 0.7, 0.5, 0x44484d);          // steps towards the core
  vox(judG, JX, 0.97, JZ + 1.7, 5.4, 1.4, 0.5, 0x101113);                                                         // bench wall behind the judge
  sign(judG, JX, 0.46, JZ + 2.24, 5.0, 0.72, ['THE  JUDGE'], '#050505', THEME.accent, 24);
  const verdict = paint(512, 256, centered(['VERDICT', 'waiting...'], THEME.dim, 54), '#050505');
  vox(judG, JX - 3.4, 0.97, JZ + 1.2, 0.3, 3.4, 0.3, 0x101113, { cast: false });
  const vg = new T.Group(); vg.position.set(JX - 3.4, 0, JZ + 1.2); vg.rotation.y = 0.72; judG.add(vg);
  vox(vg, 0, 3.55, 0, 3.7, 1.9, 0.2, 0x101113, { cast: false }); screen2(vg, 0, 4.5, 0.13, 3.5, 1.7, verdict, 0);
  const lamp = vox(judG, JX - 3.4, 5.7, JZ + 1.2, 1.0, 0.5, 0.5, 0x44484d);
  const gavel = new T.Group(); gavel.position.set(JX - 1.2, 2.0, JZ - 0.6); judG.add(gavel);
  vox(gavel, 0, 0, 0, 0.2, 0.2, 1.2, 0xa6abb1, { cast: false }); vox(gavel, 0, 0, 0.6, 0.55, 0.55, 0.55, 0xe7e9ec, { cast: false });
  tagStation(judG, 'judge');
  // where dossiers go after the verdict: ARCHIVE (rejected) and ON HOLD (not enough evidence), both beside the platform
  const arcG = new T.Group(); scene.add(arcG);
  vox(arcG, 6.0, 0, 10.4, 2.6, 1.7, 2.2, 0x1c1e21); vox(arcG, 6.0, 1.7, 10.4, 2.9, 0.2, 2.5, 0x44484d); vox(arcG, 6.0, 1.9, 10.4, 1.6, 0.04, 0.12, 0x050505, { cast: false });
  sign(arcG, 6.0, 0.9, 11.52, 2.2, 0.6, ['ARCHIVE'], '#050505', THEME.dim, 20);
  vox(arcG, -6.0, 0, 10.4, 2.6, 2.4, 2.2, 0x1c1e21); vox(arcG, -6.0, 2.4, 10.4, 2.9, 0.2, 2.5, 0x44484d);
  sign(arcG, -6.0, 1.2, 11.52, 2.2, 0.6, ['ON HOLD'], '#050505', THEME.dim, 20);
  tagStation(arcG, 'judge');

  // ======================= STRATEGIST: analysis and planning of hypotheses (left, middle) =======================
  const stG = new T.Group(); scene.add(stG); stG.position.z = -8;
  const wb = paint(768, 384, (g, w, h) => {
    g.fillStyle = '#c9cfdb'; g.fillRect(0, 0, w, h);
    g.strokeStyle = '#7b8396'; g.lineWidth = 10; g.strokeRect(5, 5, w - 10, h - 10);
    g.font = 'bold 34px "Comic Sans MS", "Segoe Print", cursive'; g.textAlign = 'left';
    g.fillStyle = '#1a2a6b'; g.fillText('Sharpe = mu / sigma', 30, 60); g.fillText('DSR(N trials) > 0.95 ?', 30, 110);
    g.fillStyle = '#9b1c1c'; g.fillText('N trials UP  =>  bar UP', 30, 160);
    g.fillStyle = '#1b5e2a'; g.fillText('walk-forward OK  [x]', 30, 210); g.fillText('beta check     [x]', 30, 255);
    g.strokeStyle = '#1b5e2a'; g.lineWidth = 6; g.beginPath(); g.moveTo(430, 300); for (let i = 0; i < 12; i++) g.lineTo(430 + i * 26, 300 - i * 9 - Math.sin(i * 1.7) * 26); g.stroke();
    g.strokeStyle = '#9b1c1c'; g.strokeRect(440, 20, 300, 70); g.fillStyle = '#9b1c1c'; g.font = 'bold 30px monospace'; g.fillText('NO OVERFITTING!', 452, 66);
  });
  vox(stG, -15.75, 3.0, 8.0, 0.2, 4.6, 8.6, 0x1a1d27); plane(stG, -15.62, 5.3, 8.0, 8.2, 4.2, wb, Math.PI / 2);
  vox(stG, -13.6, 0, 8.0, 2.2, 1.5, 5.2, 0x2a1020); vox(stG, -13.6, 1.5, 8.0, 2.5, 0.22, 5.5, 0x4a1c38);
  vox(stG, -13.6, 1.72, 6.6, 1.3, 0.1, 1.6, 0x15171d); vox(stG, -13.9, 1.82, 6.6, 0.1, 1.1, 1.5, 0x07080b); plane(stG, -13.84, 2.4, 6.6, 1.4, 0.95, paint(256, 192, centered(['> chat'], '#f2f2f2', 40), '#050505'), Math.PI / 2);
  [0xe7e9ec, 0xa6abb1, 0xffffff, 0x44484d].forEach((c, i) => vox(stG, -13.4, 1.72, 8.2 + i * 0.6, 0.45, 0.04, 0.45, c, { cast: false }));
  tagStation(stG, 'strategist');

  sign(stG, X0 + 0.02, 8.9, 8.0, 5.6, 1.0, ['STRATEGIST'], '#050505', THEME.accent, 22, Math.PI / 2);

  // ======================= TRADER DESK: the owner's live terminal (right, between risk and the prop farm) =======================
  const trG = new T.Group(); scene.add(trG);
  vox(trG, 15.1, 0, 8.4, 1.2, 0.9, 5.2, 0x1c1e21); vox(trG, 15.1, 0.9, 8.4, 1.4, 0.12, 5.5, 0x44484d);
  const monitors = [];
  [6.1, 8.4, 10.7].forEach(z => {
    vox(trG, 10.6, 0, z, 0.25, 2.5, 0.3, 0x101113, { cast: false }); vox(trG, 10.6, 2.5, z, 0.2, 1.9, 2.3, 0x101113, { cast: false });
    const tt = paint(512, 384, () => {}, '#050505'); screen2(trG, 10.72, 3.45, z, 2.1, 1.6, tt, Math.PI / 2); monitors.push(tt);
  });
  tagStation(trG, 'trader');

  // ======================= STRATEGY VAULT: the archive of approved and validated strategies (front left) =======================
  const folderG = new T.Group(); scene.add(folderG);
  const VX = -10.6, VZ = 12.5;
  vox(folderG, VX, 0, VZ + 0.2, 9.0, 1.3, 1.9, 0x1c1e21); vox(folderG, VX, 1.3, VZ + 0.2, 9.3, 0.12, 2.1, 0x44484d);              // the vault table the dossiers lie on
  vox(folderG, VX, 1.42, VZ - 1.2, 9.3, 3.4, 0.35, 0x101113);                                                                    // back panel (towards the room)
  vox(folderG, VX, 1.42, VZ - 0.9, 9.3, 0.1, 0.5, 0xa6abb1, { cast: false, mat: glow(0xa6abb1, 0.4) });
  for (let row = 0; row < 3; row++) { vox(folderG, VX, 2.2 + row * 0.9, VZ - 0.98, 8.8, 0.05, 0.08, 0x44484d, { cast: false }); for (let c = 0; c < 8; c++) vox(folderG, VX - 3.85 + c * 1.1, 2.27 + row * 0.9, VZ - 0.97, 0.05, 0.7, 0.06, 0x44484d, { cast: false }); }
  sign(folderG, VX, 5.0, VZ - 1.0, 7.0, 1.0, ['STRATEGY  VAULT'], '#050505', THEME.accent, 22, 0);
  vox(folderG, VX - 4.7, 0, VZ - 1.0, 0.3, 5.9, 0.3, 0x1c1e21); vox(folderG, VX + 4.7, 0, VZ - 1.0, 0.3, 5.9, 0.3, 0x1c1e21); vox(folderG, VX, 5.9, VZ - 1.0, 9.7, 0.2, 0.3, 0x1c1e21);
  tagStation(folderG, 'vault');

  // ---------- VIVARIUM: one specimen flask per prop account ----------
  // liquid level = distance from the drawdown floor. evaluation = slime with an antenna, funded = monster with
  // horns. Wearing a crown = passed, red + shaking = close to the floor, lying down in an empty flask = busted.
  const vivG = new T.Group(); scene.add(vivG);
  const FX0 = 6.4, FDX = 1.75, FZ = 12.7;
  vox(vivG, 10.8, 0, FZ, 10.8, 1.3, 1.5, 0x101113); vox(vivG, 10.8, 1.3, FZ, 11.1, 0.18, 1.8, 0x44484d);
  vox(vivG, 5.5, 1.4, FZ + 0.75, 0.2, 4.9, 0.2, 0x1c1e21); vox(vivG, 16.1, 1.4, FZ + 0.75, 0.2, 4.9, 0.2, 0x1c1e21);
  const vivSign = sign(vivG, 10.8, 6.7, FZ + 0.85, 6.6, 1.2, ['PROP FARM'], '#050505', THEME.accent, 28, 0);
  sign(vivG, 10.8, 0.6, FZ + 0.77, 10.0, 0.7, ['EVALUATION > FUNDED  ·  liquid = buffer  ·  coins = payout'], '#050505', THEME.dim, 9, 0);
  tagStation(vivG, 'accounts');
  const acctG = new T.Group(); scene.add(acctG);
  const acctTags = [], creatures = [];
  function makeCreature(kind, state) {
    const g = new T.Group(), body = state === 'danger' ? 0xd98b8b : state === 'busted' ? 0x44484d : 0xe7e9ec, dark = 0x101113;
    const eyes = (y, z) => { [-0.22, 0.22].forEach(x => {
      if (state === 'busted') { vox(g, x, y, z, 0.24, 0.06, 0.05, dark, { cast: false }); vox(g, x, y - 0.09, z, 0.06, 0.24, 0.05, dark, { cast: false }); }
      else { vox(g, x, y, z, 0.24, 0.24, 0.06, 0xffffff, { cast: false }); vox(g, x, y + 0.06, z + 0.04, 0.11, 0.11, 0.05, dark, { cast: false }); } }); };
    if (kind === 'funded') {
      vox(g, 0, 0, 0, 0.95, 1.05, 0.8, body, { cast: false }); [-0.3, 0.3].forEach(x => vox(g, x, 1.05, 0, 0.2, 0.38, 0.2, body, { cast: false }));
      eyes(0.62, 0.41); vox(g, 0, 0.26, 0.41, 0.5, 0.14, 0.06, dark, { cast: false });
      [-0.15, 0.15].forEach(x => vox(g, x, 0.26, 0.44, 0.08, 0.1, 0.04, 0xffffff, { cast: false }));
      [-0.6, 0.6].forEach(x => vox(g, x, 0.3, 0, 0.2, 0.5, 0.25, body, { cast: false }));
    } else {
      vox(g, 0, 0, 0, 1.05, 0.62, 0.85, body, { cast: false }); vox(g, 0, 0.62, 0, 0.7, 0.18, 0.6, body, { cast: false });
      eyes(0.4, 0.44); vox(g, 0, 0.8, 0, 0.08, 0.4, 0.08, 0x8a8a90, { cast: false }); vox(g, 0, 1.2, 0, 0.18, 0.18, 0.18, 0xffffff, { cast: false, mat: glow(0xffffff, 1.0) });
    }
    if (state === 'passed') { const y = kind === 'funded' ? 1.4 : 0.98; vox(g, 0, y, 0, 0.7, 0.18, 0.5, 0xe7e9ec, { cast: false, mat: glow(0xe7e9ec, 0.7) }); [-0.25, 0, 0.25].forEach(x => vox(g, x, y + 0.18, 0, 0.14, 0.16, 0.14, 0xe7e9ec, { cast: false, mat: glow(0xe7e9ec, 0.7) })); }
    if (state === 'busted') g.rotation.z = Math.PI / 2;
    return g;
  }
  function setAccounts(list, texts, signLines) {
    while (acctG.children.length) acctG.remove(acctG.children[0]);
    acctTags.splice(0).forEach(o => o.el.remove()); creatures.length = 0;
    vivSign.tt.redraw((g, w, h) => {
      g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = '#ffffff'; g.font = 'bold 62px monospace'; g.fillText('PROP FARM', w / 2, signLines && signLines.length > 1 ? h * 0.36 : h / 2);
      if (signLines && signLines.length > 1) { g.fillStyle = '#a6abb1'; g.font = 'bold 26px monospace'; g.fillText(String(signLines[signLines.length - 1]).slice(0, 30), w / 2, h * 0.76); }
    }, '#050505');
    list.slice(0, 6).sort((p, q) => (p.kind === 'funded') - (q.kind === 'funded')).forEach((a, i) => {      // evaluation accounts first, then funded
      const fg = new T.Group(); fg.position.set(FX0 + i * FDX, 1.48, FZ); acctG.add(fg);
      const bust = a.state === 'busted', col = bust ? 0x222226 : a.state === 'danger' ? 0xd98b8b : a.state === 'passed' ? 0x8fc7a6 : 0xcfd6e6;
      vox(fg, 0, 0, 0, 1.4, 3.0, 1.4, 0xcfd6e6, { cast: false, mat: { transparent: true, opacity: bust ? 0.08 : 0.15, depthWrite: false } });
      if (!bust) vox(fg, 0, 3.0, 0, 1.6, 0.2, 1.6, 0x2a2b31); else { vox(fg, -0.2, 1.6, 0.76, 0.05, 1.2, 0.04, 0x050505, { cast: false }); vox(fg, 0.2, 1.0, 0.76, 0.05, 0.9, 0.04, 0x050505, { cast: false }); }
      const h = bust ? 0.12 : 0.5 + a.health * 2.1;
      vox(fg, 0, 0.08, 0, 1.3, h, 1.3, col, { cast: false, mat: Object.assign({ transparent: true, opacity: 0.5, depthWrite: false }, glow(col, bust ? 0.05 : 0.55)) });
      for (let c = 0; c < Math.min(a.payouts || 0, 5); c++) vox(fg, -0.35 + (c % 2) * 0.5, 0.1 + Math.floor(c / 2) * 0.12, 0.2, 0.42, 0.1, 0.42, 0xffffff, { cast: false, mat: glow(0xffffff, 0.8) });
      const cr = makeCreature(a.kind, a.state); const baseY = bust ? 0.35 : Math.max(0.45, Math.min(2.0, h * 0.7));
      cr.position.set(bust ? -0.2 : 0, baseY, 0); cr.scale.setScalar(0.85); fg.add(cr);
      creatures.push({ g: cr, state: a.state, base: baseY, phase: Math.random() * 6, x: cr.position.x });
      tagStation(fg, 'accounts');
      const el = document.createElement('div'); el.className = 'acct st-' + a.state;
      const [l1, l2] = (texts && texts[i]) || [a.firm + ' ' + a.plan, ''];
      el.innerHTML = '<b></b><span></span>'; el.firstChild.textContent = l1; el.lastChild.textContent = l2; overlay.appendChild(el);
      acctTags.push({ el, lvl: i % 3, pos: new T.Vector3(FX0 + i * FDX, 4.7, FZ) });
    });
  }
  setAccounts([], []);


  // cold white pools of light on every department
  [[-9, 5, -8, 0.8], [0.5, 5, -8, 0.9], [10, 5, -8, 0.85], [0, 7.5, 2.2, 1.5], [-12, 5, 0, 0.75], [12, 5, 2.2, 0.8], [0, 6, 10, 0.9], [-10.5, 5, 11, 0.7], [10.5, 5, 11, 0.7], [11, 5, 7, 0.6]]
    .forEach(a => pool(0xe9eef4, a[0], a[1], a[2], a[3]));

  makeScientist('research', 'Researcher', { coat: 0xe7e9ec, trim: 0xa6abb1, accent: 0xffffff, hair: 0x44484d, item: 'clipboard' });
  makeScientist('optimizer', 'Optimizer', { coat: 0x44484d, trim: 0xa6abb1, accent: 0xe7e9ec, hair: 0x101113, goggles: true, glasses: false, skin: 0xc4a58a, item: null });
  makeScientist('validator', 'Validator', { coat: 0xa6abb1, trim: 0xe7e9ec, accent: 0xffffff, hair: 0xe7e9ec, hairStyle: 'tall', antenna: true, item: 'flask' });
  makeScientist('judge', 'Judge', { coat: 0x101113, trim: 0xe7e9ec, accent: 0xffffff, hair: 0xa6abb1, hairStyle: 'short', shirt: 0xe7e9ec, pocket: 0x44484d, pauldron: true, item: 'gavel', skin: 0xd9b89a });
  makeScientist('trader', 'Trader', { coat: 0x1c1e21, trim: 0xa6abb1, accent: 0xe7e9ec, hair: 0x101113, headset: true, cap: true, glasses: false, skin: 0xa88a72 });
  makeScientist('strategist', 'Strategist', { coat: 0x7b8087, trim: 0xe7e9ec, accent: 0xffffff, hair: 0x2a2d31, hairStyle: 'bun', halo: true, item: 'tablet', skin: 0xe3c6ad });
  makeScientist('risk', 'Risk Manager', { coat: 0x2c2f33, trim: 0xe7e9ec, accent: 0xffffff, hair: 0x101113, hardhat: true, skin: 0xc9a98f, item: 'tablet' });
  // they work at their department and turn towards the Core / the camera
  place(robots.research, -8.6, -6.7, -6.5, 0); place(robots.optimizer, 0.5, -6.5, 2.5, 0);
  place(robots.validator, 9.8, -6.7, 11.4, 0); place(robots.judge, 0, 10.5, 6, 6, 0.9);
  place(robots.trader, 12.8, 8.4, 20, 10); place(robots.strategist, -11.4, 0, -6, 6);
  place(robots.risk, 12.8, 2.2, 20, 6);
  // each scientist works at a desk placed right in front of them: arms rest on it, they never mime in thin air
  const deskLights = {};
  function makeDesk(r, accent) {
    const dg = new T.Group(); dg.position.set(r.g.position.x, r.base, r.g.position.z); dg.rotation.y = r.g.rotation.y;
    const f = 1.3;
    vox(dg, 0, 1.0, f, 2.6, 0.14, 1.0, 0x20242e); vox(dg, 0, 1.14, f, 2.64, 0.05, 1.04, 0x333947);
    [-1.15, 1.15].forEach(x => [-0.4, 0.4].forEach(z => vox(dg, x, 0, f + z, 0.12, 1.0, 0.12, 0x14161c)));
    vox(dg, 0, 0.45, f + 0.5, 2.5, 0.55, 0.06, 0x14161c);                                     // front panel
    const pad = vox(dg, 0, 1.19, f, 1.5, 0.04, 0.55, accent, { cast: false, mat: glow(accent, 0.5) });  // keyboard / tablet
    vox(dg, -0.95, 1.19, f - 0.05, 0.5, 0.05, 0.4, 0xf4f1e6, { cast: false });                  // notes
    vox(dg, 0.98, 1.19, f - 0.1, 0.3, 0.14, 0.3, 0x14161c, { cast: false });                    // lamp base
    r.pad = pad; deskLights[r.name] = pad; scene.add(dg);
  }
  makeDesk(robots.research, 0xe7e9ec); makeDesk(robots.optimizer, 0xe7e9ec); makeDesk(robots.validator, 0xe7e9ec);
  makeDesk(robots.judge, 0xffffff); makeDesk(robots.strategist, 0xe7e9ec); makeDesk(robots.risk, 0xe7e9ec);
  Object.values(robots).forEach(r => tagStation(r.g, r.name));

  // ======================= MARKET INTELLIGENCE WING (front left, an extension of the lab floor) =======================
  // Five scientists: Market Observer, Structure Engine, Behavior Analyzer, Obsidian Memory, Research Director. The screens show ONLY what the backend reports
  // (LabScene.setMie); with no data they say NOT AVAILABLE. The links light up only when the corresponding agents really emitted events, in that order.
  const miG = new T.Group(); scene.add(miG);
  const MZ0 = Z1 + 0.5, MZ1 = Z1 + 11.5;                                                          // wing floor: z 14.5 .. 25.5, x as the lab
  vox(miG, (X0 + 0) / 2 - 0.0, -0.45, (MZ0 + MZ1) / 2, 16.6, 0.4, MZ1 - MZ0, 0x020305, { cast: false });
  (function wingFloor() {
    const a = new T.InstancedMesh(geo(0.94, 0.4, 0.94), mat(0x0c1017), 120), b = new T.InstancedMesh(geo(0.94, 0.4, 0.94), mat(0x10151e), 120), m4 = new T.Matrix4(); let ia = 0, ib = 0;
    for (let x = X0; x < 0; x++) for (let z = Math.ceil(MZ0); z < MZ1; z++) { m4.setPosition(x + 0.5, -0.2, z + 0.5); ((x + z) & 1 ? a : b).setMatrixAt((x + z) & 1 ? ia++ : ib++, m4); }
    a.count = ia; b.count = ib; a.receiveShadow = b.receiveShadow = true; miG.add(a, b);
  })();
  vox(miG, X0 - 0.5, 0, (MZ0 + MZ1) / 2 + 0.2, 1, 13, MZ1 - MZ0 + 0.4, 0x070a0f);                  // the left wall continues
  for (let z = MZ0 + 1; z < MZ1 - 0.5; z += 2.2) vox(miG, X0 + 0.12, 0.7, z, 0.1, 5.4, 2.0, Math.round(z) % 2 ? 0x0e131c : 0x0b0f17, { cast: false });
  vox(miG, X0 + 0.1, 0, (MZ0 + MZ1) / 2, 0.3, 0.8, MZ1 - MZ0, 0x05070b); vox(miG, X0 + 0.2, 12.3, (MZ0 + MZ1) / 2, 0.2, 0.25, MZ1 - MZ0, 0x8a8a90, { cast: false, mat: glow(0xcfcfd6, 0.7) });
  sign(miG, X0 + 0.02, 11.2, (MZ0 + MZ1) / 2, 9.4, 1.0, ['MARKET INTELLIGENCE WING'], '#050505', THEME.accent, 20, Math.PI / 2);
  // four small wall screens (data) high up, and a big CHART wall below them: the scientists stand in front of it and work on it
  const miScr = ['overview', 'intelligence', 'memory', 'cycle'].map((k, i) => {
    const z = MZ0 + 1.7 + i * 2.55, tt = paint(512, 384, () => {}, '#050505');
    vox(miG, X0 + 0.2, 7.1, z, 0.12, 2.4, 2.4, 0x101113, { cast: false }); plane(miG, X0 + 0.32, 8.3, z, 2.25, 1.7, tt, Math.PI / 2);
    return { k, tt, z };
  });
  const CH = { zc: (MZ0 + MZ1) / 2, yc: 3.7, W: 9.4, H: 4.4, x: X0 + 0.32, pw: 1280, ph: 600 };
  const chartTT = paint(CH.pw, CH.ph, () => {}, '#050505');
  vox(miG, X0 + 0.2, 1.3, CH.zc, 0.12, CH.H + 0.4, CH.W + 0.4, 0x101113, { cast: false }); plane(miG, CH.x, CH.yc, CH.zc, CH.W, CH.H, chartTT, Math.PI / 2);
  let chartGeo = null;                                                                          // pixel mapping of the last drawn chart (for the beams)
  const wall = (px, py) => new T.Vector3(CH.x, CH.yc + CH.H / 2 - py / CH.ph * CH.H, CH.zc + CH.W / 2 - px / CH.pw * CH.W);
  const MI_SEATS = [['mie_observer', 'Market Observer', 0, { coat: 0xe7e9ec, trim: 0xa6abb1, accent: 0xffffff, hair: 0x2a2d31, goggles: true, item: null }],
                    ['mie_structure', 'Structure Engine', 1, { coat: 0x44484d, trim: 0xe7e9ec, accent: 0xe7e9ec, hair: 0x101113, antenna: true, item: null }],
                    ['mie_behavior', 'Behavior Analyzer', 2, { coat: 0xa6abb1, trim: 0xe7e9ec, accent: 0xffffff, hair: 0xe7e9ec, hairStyle: 'tall', item: 'flask' }],
                    ['mie_memory', 'Obsidian Memory', 3, { coat: 0x101113, trim: 0xe7e9ec, accent: 0xffffff, hair: 0xa6abb1, hairStyle: 'bun', halo: true, item: 'tablet' }],
                    ['mie_director', 'Research Director', 4, { coat: 0x7b8087, trim: 0xe7e9ec, accent: 0xffffff, hair: 0x2a2d31, pauldron: true, item: 'tablet' }]];
  MI_SEATS.forEach(([n, label, i, o]) => { const z = MZ0 + 1.5 + i * 2.1; makeScientist(n, label, o); place(robots[n], -12.2, z, X0, z); tagStation(robots[n].g, n); });
  // a beam from each WORKING scientist to what it is looking at on the chart: observer -> last candle, structure -> last swing, behavior -> last event, memory -> the levels, director -> the title
  const beams = {};
  MI_SEATS.forEach(([n]) => {
    const m = new T.Mesh(new T.CylinderGeometry(0.035, 0.035, 1, 6), new T.MeshBasicMaterial({ color: 0xe7e9ec, transparent: true, opacity: 0.0, depthWrite: false })); m.visible = false; miG.add(m); beams[n] = m;
    const dot = new T.Mesh(new T.SphereGeometry(0.14, 8, 8), new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.0 })); dot.visible = false; miG.add(dot); m.userData.dot = dot;
  });
  const _up = new T.Vector3(0, 1, 0), _d = new T.Vector3(), _a = new T.Vector3();
  function aimBeams(t) {
    MI_SEATS.forEach(([n]) => {
      const r = robots[n], m = beams[n], g = chartGeo, on = r.state === 'working' && g && g.focus && g.focus[n];
      m.visible = m.userData.dot.visible = !!on; if (!on) return;
      const tgt = wall(g.focus[n][0] + Math.sin(t * 1.7 + r.phase) * 6, g.focus[n][1] + Math.cos(t * 1.3 + r.phase) * 4);
      r.armR.getWorldPosition(_a); _a.y -= 0.5; _d.subVectors(tgt, _a); const len = _d.length();
      m.position.copy(_a).addScaledVector(_d, 0.5); m.scale.set(1, len, 1); m.quaternion.setFromUnitVectors(_up, _d.normalize());
      m.material.opacity = 0.55 + 0.3 * Math.sin(t * 8 + r.phase); m.userData.dot.position.copy(tgt); m.userData.dot.material.opacity = 0.9;
    });
  }
  function drawChart(ch) {
    chartGeo = null;
    chartTT.redraw((g, w, h) => {
      g.textBaseline = 'alphabetic'; g.textAlign = 'left'; g.fillStyle = '#a6abb1'; g.font = 'bold 28px monospace';
      if (!ch || !ch.available) { g.fillStyle = '#a6abb1'; g.fillText('MARKET CHART', 30, 44); g.fillStyle = '#44484d'; g.font = 'bold 56px monospace'; g.textAlign = 'center'; g.fillText('NOT AVAILABLE', w / 2, h / 2 + 18); return; }
      g.fillText('NQ · 5min · ' + ch.sessions.join(' + ') + ' · DEVELOPMENT DATA ONLY', 30, 44);
      const L = 90, R = w - 40, Tp = 70, B = h - 40, cs = ch.candles, n = cs.length, T0 = cs[0][0], idx = {}; cs.forEach((c, i) => idx[c[0]] = i);
      let lo = Infinity, hi = -Infinity; cs.forEach(c => { lo = Math.min(lo, c[3]); hi = Math.max(hi, c[2]); });
      const pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
      const X = i => L + (i + 0.5) / n * (R - L), Y = v => B - (v - lo) / (hi - lo) * (B - Tp), cw = Math.max(1.5, (R - L) / n * 0.7);
      const near = ts => { if (idx[ts] != null) return idx[ts]; let b = 0; for (let i = 0; i < n; i++) { if (cs[i][0] <= ts) b = i; else break; } return b; };
      g.strokeStyle = '#1c1e21'; g.lineWidth = 1; g.fillStyle = '#7b8087'; g.font = '16px monospace'; g.textAlign = 'right';
      for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4, y = Y(v); g.beginPath(); g.moveTo(L, y); g.lineTo(R, y); g.stroke(); g.fillText(v.toFixed(0), L - 8, y + 5); }
      cs.forEach((c, i) => { const up = c[4] >= c[1]; g.strokeStyle = g.fillStyle = up ? '#e7e9ec' : '#5a5f65'; g.lineWidth = 1; g.beginPath(); g.moveTo(X(i), Y(c[2])); g.lineTo(X(i), Y(c[3])); g.stroke(); g.fillRect(X(i) - cw / 2, Math.min(Y(c[1]), Y(c[4])), cw, Math.max(1.5, Math.abs(Y(c[1]) - Y(c[4])))); });
      g.font = 'bold 16px monospace'; g.lineWidth = 1.5; g.setLineDash([8, 6]);
      Object.entries(ch.levels || {}).forEach(([k, v]) => { const y = Y(v); if (y < Tp || y > B) return; g.strokeStyle = '#a6abb1'; g.beginPath(); g.moveTo(L, y); g.lineTo(R, y); g.stroke(); g.setLineDash([]); g.fillStyle = '#a6abb1'; g.textAlign = 'left'; g.fillText(k.toUpperCase() + ' ' + v.toFixed(2), L + 6, y - 5); g.setLineDash([8, 6]); });
      g.setLineDash([]);
      const sw = (ch.swings || []).slice(-24); let lastSw = null;
      sw.forEach(s => { const i = near(s.t), x = X(i), y = s.kind === 'H' ? Y(s.price) - 14 : Y(s.price) + 14; g.fillStyle = '#c4c8cd'; g.beginPath(); if (s.kind === 'H') { g.moveTo(x, y + 8); g.lineTo(x - 6, y - 4); g.lineTo(x + 6, y - 4); } else { g.moveTo(x, y - 8); g.lineTo(x - 6, y + 4); g.lineTo(x + 6, y + 4); } g.fill(); lastSw = [x, y]; });
      let lastEv = null; const col = { SUPPORTED: '#e7e9ec', CONTRADICTED: '#d98b8b', INCONCLUSIVE: '#d9c27f' };
      (ch.events || []).forEach(e => { const i = near(e.t), x = X(i), y = e.level != null ? Y(e.level) : Y(cs[i][4]); g.strokeStyle = col[e.status] || '#a6abb1'; g.lineWidth = 3; g.beginPath(); g.arc(x, y, 11, 0, 7); g.stroke(); g.fillStyle = g.strokeStyle; g.font = 'bold 15px monospace'; g.textAlign = 'left'; g.fillText((e.sign > 0 ? '▲ ' : '▼ ') + e.study.slice(0, 26), x + 14, y - 12); lastEv = [x, y]; });
      g.fillStyle = '#7b8087'; g.font = '15px monospace'; g.textAlign = 'left'; g.fillText('△▽ swing confermati · ○ eventi studiati (colore = stato dell\'evidenza) · linee tratteggiate = livelli di sessione', L, h - 12);
      chartGeo = { focus: { mie_observer: [X(n - 1), Y(cs[n - 1][4])], mie_structure: lastSw || [X(n - 1), Y(cs[n - 1][4])], mie_behavior: lastEv || [X(Math.floor(n / 2)), Y(cs[Math.floor(n / 2)][4])],
        mie_memory: [L + 60, Tp + 20], mie_director: [w / 2, 32] } };
    }, '#050505');
  }
  // links of the real research pipeline: observer -> structure -> behavior -> memory -> director
  const MI_ORDER = ['mie_observer', 'mie_structure', 'mie_behavior', 'mie_memory', 'mie_director'], miLinks = {};
  for (let i = 0; i + 1 < MI_ORDER.length; i++) {
    const A = robots[MI_ORDER[i]].g.position, B = robots[MI_ORDER[i + 1]].g.position, dx = B.x - A.x, dz = B.z - A.z, len = Math.hypot(dx, dz);
    const m = vox(miG, (A.x + B.x) / 2, 0.05, (A.z + B.z) / 2, len, 0.08, 0.16, 0x44484d, { cast: false, mat: glow(0x44484d, 0.2) });
    m.material = m.material.clone(); m.rotation.y = -Math.atan2(dz, dx); miLinks[MI_ORDER[i + 1]] = m;
  }
  let miLast = null;
  function miEvent(station) {                                                                    // called for a real event of an MIE agent
    if (miLinks[station] && miLast && MI_ORDER.indexOf(miLast) + 1 === MI_ORDER.indexOf(station)) miLinks[station].userData.until = now() + 4;
    miLast = station;
  }
  tagStation(miG, 'mie_overview');
  function setMie(d) {
    const NA = (tt, title) => tt.redraw((g, w, h) => { g.fillStyle = '#a6abb1'; g.font = 'bold 28px monospace'; g.textAlign = 'left'; g.fillText(title, 20, 40); g.fillStyle = '#44484d'; g.font = 'bold 40px monospace'; g.textAlign = 'center'; g.fillText('NOT AVAILABLE', w / 2, h / 2 + 14); }, '#050505');
    const rows = (tt, title, lines) => tt.redraw((g, w, h) => {
      g.fillStyle = '#a6abb1'; g.font = 'bold 28px monospace'; g.textAlign = 'left'; g.fillText(title, 20, 40);
      g.fillStyle = '#e7e9ec'; g.font = '24px monospace'; lines.slice(0, 9).forEach((l, i) => g.fillText(String(l).slice(0, 34), 20, 92 + i * 33));
    }, '#050505');
    const [ov, iv, me, cy] = miScr.map(s => s.tt);
    const o = d && d.overview, i = d && d.intelligence, m = d && d.memory, c = d && d.cycle;
    drawChart(d && d.chart);
    if (o && o.available) {
      const top = Object.entries(o.regime_share || {}).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => k + ' ' + Math.round(v * 100) + '%');
      rows(ov, 'MARKET OVERVIEW · ' + o.market, [o.period[0] + ' > ' + o.period[1], o.sessions + ' sessions', Number(o.bars).toLocaleString('en') + ' 1-min bars', o.missing_minutes + ' missing minutes', 'fwd sessions ' + o.forward_sessions, 'order flow: ' + o.order_flow, ...top]);
    } else NA(ov, 'MARKET OVERVIEW');
    if (i && i.available) {
      const by = {}; (i.studies || []).forEach(s => by[s.status] = (by[s.status] || 0) + 1);
      rows(iv, 'MARKET INTELLIGENCE', [(i.studies || []).length + ' studies', ...Object.entries(by).map(([k, v]) => k + ' ' + v), (i.hypotheses || []).length + ' hypotheses', 'open missions ' + ((i.missions && i.missions.by_status && i.missions.by_status.open) || 0)]);
    } else NA(iv, 'MARKET INTELLIGENCE');
    if (m && m.available) rows(me, 'OBSIDIAN MEMORY', [m.notes_intelligence + ' MIE notes / ' + m.notes_total + ' total', 'dev ' + ((m.by_access || {}).dev || 0) + ' · owner ' + ((m.by_access || {}).owner || 0), 'locked-derived ' + ((m.by_access || {}).locked_derived || 0), 'conflicts ' + (m.conflicts || []).length, 'quarantined ' + (m.quarantined || 0), 'last write ' + (m.last_write || '-')]);
    else NA(me, 'OBSIDIAN MEMORY');
    if (c && c.status && c.status.status === 'active') rows(cy, 'LEARNING CYCLE ' + c.status.id, ['focus: ' + c.status.focus, 'lab day ' + c.status.lab_days_elapsed + ' / ' + c.status.lab_days_planned, 'studies ' + c.status.studies_run, 'hypotheses ' + c.status.hypotheses, 'forward sessions ' + c.status.forward_sessions]);
    else if (c && c.status && c.status.status === 'completed') rows(cy, 'LEARNING CYCLE', ['completed', c.status.report ? (c.status.report.thin ? 'report: INCONCLUSIVE' : 'report ready') : '']);
    else NA(cy, 'LEARNING CYCLE');
  }
  setMie(null);

  // ---------- crates (strategies travelling on the belt) ----------
  const crates = [];
  function makeCrate(color) {
    const g = new T.Group();
    vox(g, 0, 0, 0, 1.5, 1.2, 1.5, 0xe7e9ec, { mat: glow(0xe7e9ec, 0.85) }); vox(g, 0, 1.2, 0, 0.8, 0.22, 0.8, 0xffffff, { cast: false, mat: glow(0xffffff, 1.2) }); vox(g, 0, 0.5, 0.76, 0.9, 0.12, 0.04, 0x050505, { cast: false });
    const c = { g, s: 0, target: 0, free: null, hold: 0, then: null, glow: 0, slide: null };
    scene.add(g); crates.push(c); return c;
  }
  function placeCrate(c) {
    if (c.free) { c.g.position.set(c.free.x, c.free.y, c.free.z); return; }
    const p = pointAt(c.s); c.g.position.set(p[0], 0.4 + Math.sin(performance.now() / 300 + c.s) * 0.05, p[1]);
  }
  let current = null;
  const spawn = (stop, color) => { const c = makeCrate(color); c.s = c.target = STOP[stop]; placeCrate(c); return c; };
  const goTo = (c, stop, then) => { c.target = STOP[stop]; c.then = then || null; };
  const slideTo = (c, x, y, z, then) => { if (!c.free) { const p = pointAt(c.s); c.free = { x: p[0], y: 0.4, z: p[1] }; } c.slide = { x, y, z, then: then || null }; };

  // ---------- events ----------
  let gearSpeed = 0.4, partSpeed = 0.3, optRunning = false, valUntil = 0, heatT = 0, coreFlash = 0, coreSpin = 0.12;
  const now = () => performance.now() / 1000;
  let bubblesOn = false;                                              // routine thoughts are off by default: bubbles are for what matters
  const MAP = { risk: 'risk', researcher: 'research', scout: 'research', strategist: 'strategist', you: 'strategist', trader: 'trader', optimizer: 'optimizer', planner: 'optimizer', validator: 'validator', judge: 'judge', auditor: 'judge', lab: 'judge',
    market_observer: 'mie_observer', structure_engine: 'mie_structure', behavior_analyzer: 'mie_behavior', obsidian_memory: 'mie_memory', research_director: 'mie_director' };
  function speak(station, text, secs, alert) {
    const r = robots[station]; if (!r || !text) return;
    if (!alert) {
      if (!bubblesOn || (r.alertUntil || 0) > now()) { r.until = now() + 2.5; return; }     // routine thoughts only when asked; never over an important message
      r.bub.classList.remove('alert');
    } else { r.alertUntil = now() + 120; r.bub.classList.add('alert'); }
    r.bub.textContent = text.length > 150 ? text.slice(0, 147) + '...' : text;
    r.bubUntil = now() + (secs || 6); r.until = now() + 2.5; r.thoughtAt = now();
  }
  function setLamp(status, name) {
    const col = status === 'APPROVED' ? 0x8fc7a6 : status === 'REJECTED' ? 0xd98b8b : 0xd9c27f;      // operational states only, muted
    lamp.material = new T.MeshLambertMaterial({ color: col, emissive: col, emissiveIntensity: 1.2 });
    lamp.userData.until = now() + 10; gavel.userData.hit = now();
    const hex = '#' + col.toString(16).padStart(6, '0');
    verdict.redraw(centered([status === 'INSUFFICIENT_EVIDENCE' ? 'NOT ENOUGH DATA' : status, (name || '').slice(0, 22)], hex, status === 'INSUFFICIENT_EVIDENCE' ? 40 : 56), '#07080c');
  }
  const AGENTS_OF = { research: ['researcher', 'scout'], risk: ['risk'], strategist: ['strategist'], optimizer: ['optimizer', 'planner'], validator: ['validator'], judge: ['judge', 'auditor'], trader: ['trader'],
    mie_observer: ['market_observer'], mie_structure: ['structure_engine'], mie_behavior: ['behavior_analyzer'], mie_memory: ['obsidian_memory'], mie_director: ['research_director'] };
  const agentState = {};
  const matWork = mat(0xffffff, glow(0xffffff, 1.3)), matDone = mat(THEME.n.up, glow(THEME.n.up, 1.2));
  let stateLabels = { idle: 'idle', standby: 'standby', working: 'working', done: 'done' };
  // The server says who is REALLY busy (polled every few seconds). Events only give instant feedback for a few
  // seconds, so a long wait for the AI can never make a scientist look switched off.
  let serverWorking = null, serverAt = 0, autopilotOn = false, traderLive = false;
  function setServer(working, autopilot, live) { serverWorking = new Set(working || []); serverAt = now(); autopilotOn = !!autopilot; traderLive = !!live; }
  function setAgentState(agent, state, at) {
    const t0 = at == null ? now() : at;
    agentState[agent] = { state, until: t0 + (state === 'working' ? 8 : state === 'done' ? 12 : 0) };
  }
  function robotState(name, t) {
    let done = false;
    const fresh = serverWorking && t - serverAt < 20;
    for (const a of AGENTS_OF[name] || []) {
      if (fresh && serverWorking.has(a)) return 'working';
      const s = agentState[a]; if (!s) continue;
      if (s.state === 'working' && t < s.until) return 'working';
      if (s.state === 'done' && t < s.until) done = true;
    }
    if (done) return 'done';
    return (autopilotOn && name !== 'trader') || (name === 'trader' && traderLive) ? 'standby' : 'idle';
  }
  function onEvent(ev) {
    if (ev.type === 'working' || ev.type === 'done' || ev.type === 'idle') setAgentState(ev.agent, ev.type);
    if (ev.type === 'study_started') setAgentState('optimizer', 'working');          // older logs without lifecycle events
    if (ev.type === 'board' && AGENTS_OF[MAP[ev.agent]]) setAgentState(AGENTS_OF[MAP[ev.agent]][0], 'working');      // a real board line: that agent is talking now
    if (ev.agent === 'judge' && ev.type === 'verdict') { setAgentState('judge', 'done'); setAgentState('validator', 'done'); }
    const st = MAP[ev.agent];
    if (st && st.indexOf('mie_') === 0 && ev.type !== 'idle') miEvent(st);
    if (st && ev.type === 'alert') speak(st, '❗ ' + ev.message, 120, true);
    else if (st && ev.type === 'web') speak(st, ({ search: '🔎 ', found: '📄 ', read: '📖 ', saved: '💾 ', error: '⚠ ' }[ev.data && ev.data.phase] || '') + (ev.data && ev.data.src ? ev.data.src + ': ' : '') + ev.message);
    else if (st && ev.type === 'board') speak(st, '💬 ' + ev.message, 9);
    else if (st) speak(st, ({ working: '💭 ', verdict: '⚖ ', proposal: '💡 ', error: '⚠ ' }[ev.type] || '') + ev.message);
    const msg = ev.message || '';
    if (ev.agent === 'researcher' && ev.type === 'proposal') { current = spawn('research', 0xe7e9ec); goTo(current, 'optimizer'); }
    if (ev.agent === 'optimizer' && ev.type === 'study_started') { optRunning = true; if (!current) current = spawn('optimizer', 0xa6abb1); else goTo(current, 'optimizer'); }
    if (ev.agent === 'validator' && ev.type === 'surface') { valUntil = now() + 7; if (current) goTo(current, 'validator'); }
    if (ev.agent === 'judge' && ev.type === 'verdict') {
      optRunning = false;
      const status = /APPROVED/.test(msg) ? 'APPROVED' : /INSUFFICIENT/.test(msg) ? 'INSUFFICIENT_EVIDENCE' : 'REJECTED';
      setLamp(status, msg.split(':')[0]);
      if (!current) current = spawn('validator', 0xffffff);
      const c = current; current = null;
      // the dossier's way after validation: through the OFO CORE to the JUDGE, then to the VAULT (approved), the ARCHIVE (rejected) or ON HOLD
      goTo(c, 'core', () => { c.hold = 0.9; coreFlash = now() + 2.5; c.then2 = () => {
        goTo(c, 'judge', () => {
          c.hold = 1.2;
          c.then2 = () => {
            if (status === 'APPROVED') goTo(c, 'vault', () => { c.fade = 20; });
            else if (status === 'REJECTED') slideTo(c, 6.0, 1.9, 10.4, () => { c.fade = 2; });
            else slideTo(c, -6.0, 2.6, 10.4, () => { c.fade = 20; });
          };
        });
      }; });
    }
  }

  // ---------- departments: each one can be selected, zoomed into, and the general view restored ----------
  const HOME = { pos: [19.5, 19.5, 31.5], target: [-5.5, 0.8, 5.5] };
  const DEPT = {
    research:   { label: 'RESEARCH WING',      at: [-9, 2.0, -9.5],    r: 5.2, station: 'research',   agent: 'research' },
    optimizer:  { label: 'OPTIMIZATION BAY',   at: [0.5, 2.4, -9.5],   r: 5.4, station: 'optimizer',  agent: 'optimizer' },
    validator:  { label: 'VALIDATION CHAMBER', at: [10.5, 2.4, -9.5],  r: 5.6, station: 'validator',  agent: 'validator' },
    strategist: { label: 'STRATEGIST',         at: [-12.2, 2.0, 0],    r: 3.9, station: 'strategist', agent: 'strategist' },
    core:       { label: 'OFO CORE',           at: [0, 1.8, 2.2],      r: 5.6, station: 'core',       agent: null, dist: 1.25 },
    risk:       { label: 'RISK MANAGER',       at: [13, 2.4, 2.2],     r: 4.4, station: 'risk',       agent: 'risk' },
    trader:     { label: 'TRADER DESK',        at: [12.2, 2.4, 7],     r: 4.4, station: 'trader',     agent: 'trader' },
    judge:      { label: 'THE JUDGE',          at: [0, 2.6, 10.8],     r: 4.6, station: 'judge',      agent: 'judge' },
    vault:      { label: 'STRATEGY VAULT',     at: [-10.6, 2.2, 12.5], r: 5.4, station: 'vault',      agent: null },
    farm:       { label: 'PROP FARM',          at: [10.9, 2.4, 12.7],  r: 6.0, station: 'accounts',   agent: null },
    mie:        { label: 'MARKET INTELLIGENCE WING', at: [-9.5, 3.4, 20.0], r: 8.0, station: 'mie_director', agent: 'mie_director', view0: { target: [-15.5, -1.2, 19.0], pos: [-1.0, 9.5, 27.0] } }
  };
  const STATION_DEPT = { research: 'research', optimizer: 'optimizer', validator: 'validator', strategist: 'strategist', core: 'core', risk: 'risk', trader: 'trader', judge: 'judge', vault: 'vault', accounts: 'farm', mie_observer: 'mie', mie_structure: 'mie', mie_behavior: 'mie', mie_memory: 'mie', mie_director: 'mie', mie_overview: 'mie' };
  Object.entries(DEPT).forEach(([dkey, d]) => {
    d.key = dkey; const k = d.dist || 1;
    d.view = { target: [d.at[0], d.at[1] - 0.6, d.at[2] + 2.6], pos: [d.at[0] + 6.5 * k, d.at[1] + 6 * k, d.at[2] + 11.6 * k] };       // aimed a little in front of the department so the Inspector never covers it
    if (d.view0) d.view = d.view0;
    d.tag = document.createElement('div'); d.tag.className = 'tag dept'; d.tag.textContent = d.label; overlay.appendChild(d.tag);
    d.tag.style.pointerEvents = 'auto'; d.tag.style.cursor = 'pointer'; d.tag.onclick = () => focus(d.key);
  });
  let selected = null, tween = null, onSelect = null;
  const selRing = new T.Mesh(new T.RingGeometry(0.965, 1.0, 64), new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.0, blending: T.AdditiveBlending, depthWrite: false }));
  selRing.rotation.x = -Math.PI / 2; selRing.position.y = 0.06; scene.add(selRing);
  function flyTo(view, dur) {
    tween = { t0: now(), dur: dur || 0.9, p0: camera.position.clone(), q0: controls.target.clone(), p1: new T.Vector3(...view.pos), q1: new T.Vector3(...view.target) };
  }
  function select(key) {
    selected = DEPT[key] ? key : null;
    if (selected) { const d = DEPT[key]; selRing.position.x = d.at[0]; selRing.position.z = d.at[2]; selRing.scale.setScalar(d.r); }
    Object.entries(DEPT).forEach(([k, d]) => d.tag.classList.toggle('sel', k === selected));
    if (onSelect) onSelect(selected);
  }
  function focus(key) { if (!DEPT[key]) return; select(key); flyTo(DEPT[key].view, 0.9); }
  function home() { select(null); flyTo(HOME, 1.0); }

  // ---------- main loop ----------
  const clk = new T.Clock();
  function updateOverlay() {
    const w = canvas.clientWidth, h = canvas.clientHeight, v = new T.Vector3();
    const near = camera.position.distanceTo(controls.target) < 46;
    acctTags.forEach(o => {
      const p = o.pos.clone().project(camera), vis = p.z < 1 && Math.abs(p.x) < 1.1 && Math.abs(p.y) < 1.1;
      o.el.style.display = vis ? 'block' : 'none'; o.el.style.left = ((p.x * 0.5 + 0.5) * w) + 'px'; o.el.style.top = (((-p.y * 0.5 + 0.5) * h) - o.lvl * (near ? 30 : 20)) + 'px'; o.el.classList.toggle('far', !near);
    });
    const far = camera.position.distanceTo(controls.target) > 30;
    Object.entries(DEPT).forEach(([dk, d]) => {
      v.set(d.at[0], 0.4, d.at[2] + d.r * 0.45); const p = v.project(camera), vis = p.z < 1 && p.x > -1.1 && p.x < 1.1 && p.y > -1.1 && p.y < 1.1;
      d.tag.style.display = vis && (far || dk === selected) ? 'block' : 'none'; d.tag.style.left = ((p.x * 0.5 + 0.5) * w) + 'px'; d.tag.style.top = ((-p.y * 0.5 + 0.5) * h) + 'px';
      const live = d.agent && robots[d.agent] && robots[d.agent].state === 'working'; const txt = d.label + (live ? '  ●' : ''); if (d.tag.textContent !== txt) d.tag.textContent = txt;
    });
    Object.values(robots).forEach(r => {
      r.head.getWorldPosition(v); v.y += 1.1;
      const p = v.clone().project(camera);
      const vis = p.z < 1 && p.x > -1.1 && p.x < 1.1 && p.y > -1.1 && p.y < 1.1;
      const sx = (p.x * 0.5 + 0.5) * w, sy = (-p.y * 0.5 + 0.5) * h;
      const stTxt = r.label + ' · ' + (r.state === 'working' ? '● ' : r.state === 'done' ? '✓ ' : r.state === 'standby' ? '○ ' : '') + stateLabels[r.state];
      if (r.shown !== stTxt) { r.tag.textContent = stTxt; r.shown = stTxt; }
      r.tag.className = 'tag st-' + r.state;
      r.tag.style.display = vis && !far ? 'block' : 'none'; r.tag.style.left = sx + 'px'; r.tag.style.top = sy + 'px';
      r.bub.style.left = sx + 'px'; r.bub.style.top = (sy - 22) + 'px';
      const age = now() - (r.thoughtAt || -1e9), fresh = age < 30;       // the last thought stays up: bright while fresh, dim for 5 min, then gone (or while working)
      const isAlert = (r.alertUntil || 0) > now();
      r.bub.classList.toggle('on', vis && !!r.bub.textContent && (isAlert || (bubblesOn && (age < 300 || r.state === 'working'))));
      r.bub.classList.toggle('old', !isAlert && !fresh && r.state !== 'working');
    });
  }
  function frame() {
    const dt = Math.min(clk.getDelta(), 0.1), t = now();
    if (tween) {
      const k = Math.min(1, (t - tween.t0) / tween.dur), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      camera.position.lerpVectors(tween.p0, tween.p1, e); controls.target.lerpVectors(tween.q0, tween.q1, e); if (k >= 1) tween = null;
    }
    controls.update();
    selRing.material.opacity += ((selected ? 0.32 + 0.14 * Math.sin(t * 3) : 0) - selRing.material.opacity) * Math.min(1, dt * 6);
    Object.values(robots).forEach(r => {
      const st = robotState(r.name, t); r.state = st;
      const speaking = t < r.until, working = st === 'working';
      r.g.position.y = r.base + (st === 'done' ? Math.abs(Math.sin(t * 5 + r.phase)) * 0.1 : 0);       // no hopping: feet stay on the floor
      const hasDesk = !!r.pad || r.name === 'trader';
      let lx, rx, hd = 0;
      if (st === 'done') { lx = -2.5 + Math.sin(t * 6) * 0.25; rx = -2.5 - Math.sin(t * 6) * 0.25; }
      else if (hasDesk && (working || st === 'standby')) {
        // hands on the desk: fast alternating keystrokes when working, a slow tap while waiting for the next task
        const f = working ? 11 : 1.6, amp = working ? 0.2 : 0.04;
        lx = -1.12 + Math.sin(t * f + r.phase) * amp; rx = -1.12 + Math.sin(t * f + r.phase + 2.1) * amp;
        hd = working ? 0.28 + Math.sin(t * 2.4 + r.phase) * 0.08 : 0.1;                                    // looking down at the work
      } else if (working || speaking) { const w = Math.sin(t * 9 + r.phase) * 0.6; lx = -1.0 + w; rx = -1.0 - w; hd = 0.12; }
      else { lx = Math.sin(t * 1.2 + r.phase) * 0.05; rx = -lx; hd = st === 'idle' ? 0.32 : 0; }
      r.armL.rotation.x += (lx - r.armL.rotation.x) * Math.min(1, dt * 14);
      r.armR.rotation.x += (rx - r.armR.rotation.x) * Math.min(1, dt * 14);
      r.head.rotation.x += (hd - r.head.rotation.x) * Math.min(1, dt * 5);
      r.bulb.visible = st === 'working' || st === 'done';
      if (r.bulb.visible) { r.bulb.material = st === 'done' ? matDone : matWork; r.bulb.scale.setScalar(st === 'working' ? 0.8 + 0.4 * Math.abs(Math.sin(t * 5)) : 1); }
      Object.values(miLinks).forEach(m => { m.material.emissiveIntensity = (m.userData.until || 0) > t ? 1.4 : 0.2; });
      if (r.pad) r.pad.material.emissiveIntensity = working ? 0.9 + 0.5 * Math.sin(t * 9 + r.phase) : st === 'standby' ? 0.45 : 0.15;
    });
    aimBeams(t);
    chevrons.forEach(m => { m.userData.s = (m.userData.s + dt * 2.2) % TOTAL; const p = pointAt(m.userData.s); m.position.set(p[0], 0.4, p[1]); });
    gearSpeed += ((optRunning ? 4 : 0.35) - gearSpeed) * Math.min(1, dt * 3);
    gears.forEach(g => g.rotation.z += g.userData.dir * gearSpeed * dt);
    leds.forEach(l => { if (Math.random() < (optRunning ? 0.25 : 0.01)) l.visible = !l.visible; });
    if (optRunning && t - heatT > 0.4) { heatT = t; drawHeat(); }
    const act = t < valUntil; partSpeed += ((act ? 5 : 0.6) - partSpeed) * Math.min(1, dt * 3);
    parts.forEach(p => {
      const u = p.userData;
      for (let k = 0; k < 3; k++) {
        u.p[k] += u.v[k] * partSpeed * dt * 2;
        const lim = k === 1 ? [0.8, 4.6] : k === 0 ? [-2.1, 2.1] : [-1.6, 1.6];
        if (u.p[k] < lim[0] || u.p[k] > lim[1]) { u.v[k] *= -1; u.p[k] = Math.max(lim[0], Math.min(lim[1], u.p[k])); }
      }
      p.position.set(6.9 + u.p[0], u.p[1], -10 + u.p[2]);
    });
    bubbles.forEach(b => { const u = b.userData; u.y += u.v * dt; if (u.y > 4.3) u.y = 0.5; b.position.set(15 + u.x, u.y, -11.8 + u.z); });
    flasks.forEach((f, i) => { f.material.emissiveIntensity = 0.8 + 0.3 * Math.sin(t * 2 + i); });
    creatures.forEach(c => {
      if (c.state === 'busted') return;
      const amp = c.state === 'passed' ? 0.22 : 0.07;
      c.g.position.y = c.base + Math.sin(t * (c.state === 'passed' ? 4 : 1.6) + c.phase) * amp;
      if (c.state === 'danger') { c.g.position.x = Math.sin(t * 38 + c.phase) * 0.05; c.g.rotation.z = Math.sin(t * 31 + c.phase) * 0.09; }
    });
    const engineBusy = optRunning || (serverWorking && t - serverAt < 20 && serverWorking.size > 0);        // real activity only
    coreSpin += ((engineBusy ? 1.1 : 0.12) - coreSpin) * Math.min(1, dt * 2);
    coreRing.rotation.y += coreSpin * dt;
    coreGlow.material.opacity += ((t < coreFlash ? 0.5 : engineBusy ? 0.16 : 0.0) - coreGlow.material.opacity) * Math.min(1, dt * 3);
    if (lamp.userData.until && t > lamp.userData.until) { lamp.material = mat(0x44484d); lamp.userData.until = 0; }
    const gh = gavel.userData.hit ? t - gavel.userData.hit : 9;
    gavel.rotation.x = gh < 0.8 ? -Math.abs(Math.sin(gh * 14)) * 0.9 : 0;
    for (let i = crates.length - 1; i >= 0; i--) {
      const c = crates[i];
      if (c.slide) {
        const sl = c.slide, dx = sl.x - c.free.x, dy = sl.y - c.free.y, dz = sl.z - c.free.z, d = Math.hypot(dx, dy, dz), stp = Math.min(d, dt * 5);
        if (d > 0.02) { c.free.x += dx / d * stp; c.free.y += dy / d * stp; c.free.z += dz / d * stp; } else { const f = sl.then; c.slide = null; if (f) f(); }
      } else if (c.hold > 0) { c.hold -= dt; if (c.hold <= 0 && c.then2) { const f = c.then2; c.then2 = null; f(); } }
      else if (Math.abs(c.target - c.s) > 0.05) { c.s += Math.sign(c.target - c.s) * Math.min(Math.abs(c.target - c.s), dt * 6); if (Math.abs(c.target - c.s) <= 0.05 && c.then) { const f = c.then; c.then = null; f(); } }
      if (c.fade) { c.fade -= dt; if (c.fade <= 0) { scene.remove(c.g); crates.splice(i, 1); continue; } }
      placeCrate(c);
    }
    renderer.render(scene, camera); updateOverlay(); requestAnimationFrame(frame);
  }
  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h;
    const wide = w > 1000, rightEl = document.getElementById('right'), rw = wide && rightEl ? rightEl.getBoundingClientRect().width + 24 : 0;
    const leftW = wide ? 322 : 0, visible = Math.max(300, w - leftW - rw);
    camera.zoom = Math.min(1, (visible / w) * 1.26);
    camera.setViewOffset(w, h, w / 2 - (leftW + (w - rw)) / 2, 0, w, h);
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();

  const ray = new T.Raycaster(), mouse = new T.Vector2(); let down = null;
  canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
    mouse.set((e.clientX / canvas.clientWidth) * 2 - 1, -(e.clientY / canvas.clientHeight) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(pickables, false)[0];
    if (hit && hit.object.userData.folder && window.LabScene.onFolder) window.LabScene.onFolder(hit.object.userData.folder);
    else if (hit) { const key = STATION_DEPT[hit.object.userData.station]; if (key) select(key); if (window.LabScene.onPick) window.LabScene.onPick(hit.object.userData.station, key); }
  });
  canvas.addEventListener('dblclick', e => {
    mouse.set((e.clientX / canvas.clientWidth) * 2 - 1, -(e.clientY / canvas.clientHeight) * 2 + 1); ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(pickables, false)[0], key = hit && STATION_DEPT[hit.object.userData.station]; if (key) focus(key);
  });

  function restoreStates(events) {            // after a reload: replay the last 3 minutes of lifecycle events silently
    const cutoff = Date.now() - 180000;
    events.forEach(e => {
      if (!['working', 'done', 'idle'].includes(e.type)) return;
      const ts = Date.parse(e.t); if (!(ts > cutoff)) return;
      setAgentState(e.agent, e.type, now() - (Date.now() - ts) / 1000);
    });
  }
  // ---------- dossiers (fascicoli) in the STRATEGY VAULT: one folder per approved strategy, click to open ----------
  let folderMeshes = [];
  function setFolders(list) {
    for (let k = pickables.length - 1; k >= 0; k--) if (pickables[k].userData.folder) pickables.splice(k, 1);
    folderMeshes.forEach(m => { folderG.remove(m); });
    folderMeshes = [];
    (list || []).slice(0, 6).forEach((f, i) => {
      const g = new T.Group(); g.position.set(VX - 3.7 + i * 1.5, 1.45, VZ + 0.25); g.scale.setScalar(1.05); g.rotation.y = (i % 2 ? 0.07 : -0.05);
      const dec = f.decisione, col = dec === 'live' ? 0x8fc7a6 : dec === 'discarded' ? 0x44484d : dec === 'hold' ? 0xd9c27f : 0xa6abb1;
      const parts = [vox(g, 0, 0, 0, 1.3, 0.1, 1.8, col), vox(g, 0, 0.1, 0.02, 1.2, 0.07, 1.7, 0xf4f1e6), vox(g, -0.35, 0.1, -1.0, 0.55, 0.1, 0.22, col)];
      if (!dec) parts.push(vox(g, 0.45, 0.17, 0.55, 0.28, 0.06, 0.28, 0xffffff, { mat: glow(0xffffff, 0.9) }));           // red seal: waiting for your decision
      const lab = paint(256, 192, (c, w, h) => {
        c.fillStyle = '#101113'; c.textAlign = 'center'; c.font = 'bold 22px monospace'; c.fillText('FASCICOLO', w / 2, 34);
        c.font = 'bold 19px monospace'; const n = (f.nome || '').slice(0, 15), n2 = (f.nome || '').slice(15, 30);
        c.fillText(n, w / 2, 86); c.fillText(n2, w / 2, 112);
        c.font = '17px monospace'; c.fillText('LCB95 ' + (f.npfv_lcb95 == null ? 'n/d' : '$' + Math.round(f.npfv_lcb95)), w / 2, 160);
      }, '#f4f1e6');
      const pl = new T.Mesh(new T.PlaneGeometry(1.1, 0.82), new T.MeshBasicMaterial({ map: lab.tex }));
      pl.rotation.x = -Math.PI / 2; pl.position.set(0, 0.19, 0.05); g.add(pl);
      parts.push(pl);
      g.traverse(o => { if (o.isMesh) { o.userData.folder = f.slug; pickables.push(o); } });
      folderG.add(g); folderMeshes.push(g);
    });
  }

  // ---------- trader monitors ----------
  const L = { wait: ['OFO TRADING DESK', 'waiting for', 'approved strategies'], none: 'none yet' };
  function setTrader(names, labels) {
    labels = labels || {};
    const head = labels.head || 'APPROVED';
    monitors[0].redraw(centered(names.length ? [labels.desk || 'TRADING DESK', names.length + ' ' + (labels.ready || 'ready')] : (labels.wait || L.wait), names.length ? THEME.accent : THEME.dim, 34), '#050505');
    monitors[2].redraw(centered(names.length ? [head, ...names.slice(0, 3).map(n => n.slice(0, 18))] : [head, labels.none || L.none], names.length ? THEME.accent : THEME.dim, 32), '#050505');
  }
  function setTraderChart(candles, symbol) {
    if (!candles || !candles.length) return;
    monitors[1].redraw((g, w, h) => {
      const cs = candles.slice(-48), hi = Math.max(...cs.map(c => c[2])), lo = Math.min(...cs.map(c => c[3])), pad = 38, cw = (w - 2 * pad) / cs.length;
      const Y = v => pad + (1 - (v - lo) / ((hi - lo) || 1)) * (h - 2 * pad - 30);
      g.strokeStyle = THEME.grid; g.lineWidth = 1; for (let i = 0; i < 5; i++) { g.beginPath(); g.moveTo(pad, pad + i * 62); g.lineTo(w - pad, pad + i * 62); g.stroke(); }
      cs.forEach((c, i) => {
        const x = pad + i * cw + cw / 2, up = c[4] >= c[1]; g.strokeStyle = g.fillStyle = up ? THEME.up : THEME.down; g.lineWidth = 2;
        g.beginPath(); g.moveTo(x, Y(c[2])); g.lineTo(x, Y(c[3])); g.stroke();
        g.fillRect(x - cw * 0.3, Math.min(Y(c[1]), Y(c[4])), cw * 0.6, Math.max(2, Math.abs(Y(c[1]) - Y(c[4]))));
      });
      g.fillStyle = THEME.dim; g.font = 'bold 26px monospace'; g.textAlign = 'left'; g.fillText((symbol || 'NQ') + ' ' + cs.length + ' candles', pad, 28);
      g.textAlign = 'right'; g.fillText(cs[cs.length - 1][4].toFixed(2), w - pad, 28);
    }, '#050505');
  }
  setTrader([]);

  window.LabScene = { crates: () => crates.map(c => ({ s: +c.s.toFixed(1), at: c.g.position.toArray().map(v => +v.toFixed(1)), fade: !!c.fade })), focus, home, select, departments: () => Object.entries(DEPT).map(([k, d]) => ({ key: k, label: d.label, agent: d.agent })), set onSelectCb(f) { onSelect = f; }, setValidation: drawValidation, setRisk: drawRisk, setFolders, folderCount: () => folderMeshes.length, onFolder: null, onEvent, speak, setTrader, setTraderChart, setAccounts, onPick: null, ready: true, robots, resize, camera, controls, restoreStates, setServer,
    setStateLabels(l) { stateLabels = Object.assign(stateLabels, l); },
    setBubbles(on) { bubblesOn = !!on; },
    setMie,
    mieBeams: () => Object.fromEntries(Object.entries(beams).map(([k, m]) => [k, m.visible])), chartGeo: () => chartGeo && { n: chartGeo.focus ? Object.keys(chartGeo.focus).length : 0 },
    mieLinks: () => Object.fromEntries(Object.entries(miLinks).map(([k, m]) => [k, (m.userData.until || 0) > now()])),
    states() { const t = now(), o = {}; Object.keys(AGENTS_OF).forEach(n => o[n] = robotState(n, t)); return o; } };
  frame();
})();
