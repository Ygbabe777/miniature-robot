/* OFO Lab - dark voxel laboratory with scientists. Every animation is driven by real events. */
(function () {
  const T = THREE;
  const canvas = document.getElementById('scene');
  const overlay = document.getElementById('bubbles');
  const renderer = new T.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene();
  scene.background = new T.Color(0x04060a);
  scene.fog = new T.Fog(0x04060a, 62, 125);
  const camera = new T.PerspectiveCamera(42, 1, 0.1, 300);
  camera.position.set(31, 25, 35);
  const controls = new T.OrbitControls(camera, canvas);
  controls.target.set(0, 2.5, 1);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.47;
  controls.minDistance = 14; controls.maxDistance = 95;

  // dark room, coloured pools of light on every station
  scene.add(new T.HemisphereLight(0x7f9fd6, 0x05070c, 0.78));
  const sun = new T.DirectionalLight(0xcfe0ff, 0.5);
  sun.position.set(22, 38, 18); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -34, right: 34, top: 34, bottom: -34, near: 1, far: 120 });
  scene.add(sun);
  const pool = (c, x, y, z, i) => { const l = new T.PointLight(c, i || 0.9, 17, 1.6); l.position.set(x, y, z); scene.add(l); return l; };

  // ---------- voxel helpers ----------
  const matCache = {}, geoCache = {};
  const mat = (c, o) => { const k = c + JSON.stringify(o || {}); return matCache[k] || (matCache[k] = new T.MeshLambertMaterial(Object.assign({ color: c }, o || {}))); };
  const geo = (w, h, d) => { const k = w + '/' + h + '/' + d; return geoCache[k] || (geoCache[k] = new T.BoxGeometry(w, h, d)); };
  const glow = (c, i) => ({ emissive: c, emissiveIntensity: i == null ? 0.9 : i });
  // x,z = centre on the floor, y = bottom
  function vox(parent, x, y, z, w, h, d, color, o) {
    o = o || {};
    const m = new T.Mesh(geo(w, h, d), mat(color, o.mat));
    m.position.set(x, y + h / 2, z);
    m.castShadow = o.cast != null ? o.cast : (w * h * d > 0.6); m.receiveShadow = true;
    parent.add(m); return m;
  }
  function paint(w, h, draw, bg) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d'); g.fillStyle = bg || '#05080d'; g.fillRect(0, 0, w, h); draw(g, w, h);
    const t = new T.CanvasTexture(c); t.magFilter = T.NearestFilter; t.minFilter = T.NearestFilter;
    return { tex: t, canvas: c, g, redraw(fn, bgc) { g.fillStyle = bgc || bg || '#05080d'; g.fillRect(0, 0, w, h); fn(g, w, h); t.needsUpdate = true; } };
  }
  function plane(parent, x, y, z, w, h, tt, rotY) {
    const m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ map: tt.tex }));
    m.position.set(x, y, z); m.rotation.y = rotY || 0; parent.add(m); return m;
  }
  const centered = (lines, color, size) => (g, w, h) => {
    g.fillStyle = color; g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = 'bold ' + size + 'px monospace';
    lines.forEach((l, i) => g.fillText(l, w / 2, (h / (lines.length + 1)) * (i + 1)));
  };
  function sign(parent, x, y, z, w, h, lines, bg, fg, size, rotY) {
    const tt = paint(512, Math.round(512 * h / w), centered(lines, fg, size * 2), bg);
    return { mesh: plane(parent, x, y, z, w, h, tt, rotY), tt };
  }

  // ---------- room ----------
  const room = new T.Group(); scene.add(room);
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
  vox(room, 0, 0, Z0 - 0.5, X1 - X0 + 1, 11, 1, 0x080b10);                                    // back wall
  vox(room, X0 - 0.5, 0, 0, 1, 11, Z1 - Z0, 0x070a0f);                                        // left wall
  for (let i = -7; i <= 7; i++) vox(room, i * 2.2, 0.7, Z0 + 0.12, 2.0, 5.4, 0.1, i % 2 ? 0x0e131c : 0x0b0f17, { cast: false });
  for (let j = -6; j <= 6; j++) vox(room, X0 + 0.12, 0.7, j * 2.2, 0.1, 5.4, 2.0, j % 2 ? 0x0e131c : 0x0b0f17, { cast: false });
  vox(room, 0, 0, Z0 + 0.1, X1 - X0, 0.8, 0.3, 0x05070b); vox(room, X0 + 0.1, 0, 0, 0.3, 0.8, Z1 - Z0, 0x05070b);
  vox(room, 0, 10.3, Z0 + 0.2, X1 - X0, 0.25, 0.2, 0x0a5560, { cast: false, mat: glow(0x0a8a99, 0.9) });   // cyan trim
  vox(room, X0 + 0.2, 10.3, 0, 0.2, 0.25, Z1 - Z0, 0x0a5560, { cast: false, mat: glow(0x0a8a99, 0.9) });
  sign(room, 0, 9.2, Z0 + 0.02, 11, 1.9, ['OFO  LAB'], '#05080d', '#5ee27a', 44);
  sign(room, X0 + 0.02, 8.2, -2, 8, 1.7, ['PROP PROJECT'], '#05080d', '#ffb03a', 30, Math.PI / 2);
  // clock
  const clock = paint(256, 256, (g, w, h) => {
    g.strokeStyle = '#8b93a8'; g.lineWidth = 12; g.beginPath(); g.arc(128, 128, 112, 0, 6.3); g.stroke();
    const d = new Date(), hr = (d.getHours() % 12 + d.getMinutes() / 60) / 12 * 6.283, mn = d.getMinutes() / 60 * 6.283;
    g.strokeStyle = '#e6e9f2'; g.lineWidth = 14; g.beginPath(); g.moveTo(128, 128); g.lineTo(128 + Math.sin(hr) * 60, 128 - Math.cos(hr) * 60); g.stroke();
    g.lineWidth = 9; g.beginPath(); g.moveTo(128, 128); g.lineTo(128 + Math.sin(mn) * 90, 128 - Math.cos(mn) * 90); g.stroke();
  }, '#0a0d14');
  plane(room, 11.5, 7.6, Z0 + 0.03, 1.8, 1.8, clock, 0);
  // hazard sign
  const haz = paint(512, 256, (g, w, h) => {
    for (let i = -h; i < w; i += 48) { g.fillStyle = '#e0b400'; g.beginPath(); g.moveTo(i, 0); g.lineTo(i + 24, 0); g.lineTo(i + 24 - h, h); g.lineTo(i - h, h); g.fill(); }
    g.fillStyle = '#111'; g.fillRect(24, 48, w - 48, h - 96);
    g.fillStyle = '#ffd84a'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.font = 'bold 44px monospace'; g.fillText('CAUTION', w / 2, 92); g.font = 'bold 52px monospace'; g.fillStyle = '#ff5c5c'; g.fillText('OVERFITTING', w / 2, 162);
  }, '#e0b400');
  plane(room, 14, 5.2, Z0 + 0.03, 3.6, 1.8, haz, 0);

  // ---------- belt ----------
  const PATH = [[-13, -5], [9, -5], [9, 9]];
  const segLen = [];
  for (let i = 0; i < PATH.length - 1; i++) segLen.push(Math.hypot(PATH[i + 1][0] - PATH[i][0], PATH[i + 1][1] - PATH[i][1]));
  const TOTAL = segLen.reduce((a, b) => a + b, 0);
  function pointAt(s) {
    s = Math.max(0, Math.min(TOTAL, s));
    for (let i = 0; i < segLen.length; i++) {
      if (s <= segLen[i] || i === segLen.length - 1) { const t = segLen[i] ? s / segLen[i] : 0, a = PATH[i], b = PATH[i + 1]; return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; }
      s -= segLen[i];
    }
  }
  for (let s = 0; s <= TOTAL; s += 1) {
    const p = pointAt(s); vox(room, p[0], 0, p[1], 1.6, 0.35, 1.6, 0x0b0d12, { cast: false });
    const horiz = s < segLen[0]; const k = Math.floor(s) % 2 ? 0xd9ad00 : 0x16181d;       // hazard edge stripes
    vox(room, p[0] + (horiz ? 0 : 1.05), 0, p[1] + (horiz ? 1.05 : 0), horiz ? 1 : 0.28, 0.06, horiz ? 0.28 : 1, k, { cast: false });
    vox(room, p[0] - (horiz ? 0 : 1.05), 0, p[1] - (horiz ? 1.05 : 0), horiz ? 1 : 0.28, 0.06, horiz ? 0.28 : 1, k, { cast: false });
  }
  const chevrons = [];
  for (let s = 0; s < TOTAL; s += 2.5) { const m = vox(room, 0, 0.36, 0, 0.5, 0.08, 0.5, 0x2adfff, { cast: false, mat: glow(0x2adfff, 0.9) }); m.userData.s = s; chevrons.push(m); }
  const STOP = { research: 4, optimizer: 11, validator: 18, judge: 22, shelf: 28, trader: TOTAL - 0.5 };

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
    const tag = document.createElement('div'); tag.className = 'tag'; tag.textContent = label; overlay.appendChild(tag);
    const bub = document.createElement('div'); bub.className = 'bubble'; overlay.appendChild(bub);
    const r = { name, g, armL, armR, head, tag, bub, until: 0, bubUntil: 0, phase: Math.random() * 6, base: 0 };
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
  sign(libG, -9.4, 8.1, -13.85, 6.2, 1.2, ['RESEARCHER'], '#07121f', '#4aa3ff', 26);
  tagStation(libG, 'research');

  // --- OPTIMIZER: server racks + parameter sweep machine
  const optG = new T.Group(); scene.add(optG);
  [-4.2, -2.2, -0.2].forEach(rx => {
    vox(optG, rx, 0, -12.6, 1.8, 7.0, 1.6, 0x0f131b);
    for (let row = 0; row < 7; row++) {
      vox(optG, rx, 0.5 + row * 0.9, -11.78, 1.5, 0.62, 0.06, 0x070a0f, { cast: false });
      for (let c = 0; c < 3; c++) { const l = vox(optG, rx - 0.45 + c * 0.45, 0.65 + row * 0.9, -11.74, 0.16, 0.16, 0.05, [0x39ff7a, 0x2adfff, 0xffb03a][(row + c) % 3], { cast: false, mat: glow([0x39ff7a, 0x2adfff, 0xffb03a][(row + c) % 3], 1.1) }); leds.push(l); }
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
  sign(optG, -2.2, 8.1, -13.85, 6.2, 1.2, ['OPTIMIZER'], '#1e1100', '#ffb03a', 26);
  tagStation(optG, 'optimizer');

  // --- VALIDATOR: bench with flasks + microscope, and the Monte Carlo glass chamber
  const valG = new T.Group(); scene.add(valG);
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
  sign(valG, 5.0, 8.1, -13.85, 6.2, 1.2, ['VALIDATOR'], '#150d28', '#b58cff', 26);
  tagStation(valG, 'validator');

  // --- JUDGE: dark podium, verdict screen, lamp, gavel
  const judG = new T.Group(); scene.add(judG);
  vox(judG, 13.2, 0, -5, 3.4, 1.6, 4.4, 0x14100c); vox(judG, 13.2, 1.6, -5, 3.8, 0.3, 4.8, 0x2a1d12); vox(judG, 13.2, 1.9, -2.6, 3.9, 0.12, 0.12, 0xd4af37, { cast: false, mat: glow(0x6a5500, 0.6) });
  vox(judG, 15.2, 0, -5, 0.6, 4.8, 5.0, 0x0e0f14);
  const verdict = paint(512, 256, centered(['VERDICT', 'waiting...'], '#6b7390', 54), '#07080c');
  plane(judG, 14.88, 3.4, -5, 4.4, 2.2, verdict, -Math.PI / 2);
  const lamp = vox(judG, 15.2, 4.8, -5, 1.0, 1.0, 1.0, 0x3a3f50);
  const gavel = new T.Group(); gavel.position.set(12.4, 2.2, -3.9); judG.add(gavel);
  vox(gavel, 0, 0, 0, 0.2, 0.2, 1.2, 0x8b5a2b, { cast: false }); vox(gavel, 0, 0, 0.6, 0.55, 0.55, 0.55, 0xc58a46, { cast: false });
  sign(judG, 14.5, 7.6, -5, 5, 1.1, ['JUDGE'], '#0b0b10', '#ffffff', 26, -Math.PI / 2);
  tagStation(judG, 'judge');
  const binG = new T.Group(); scene.add(binG);
  vox(binG, 9, 0, -9.8, 2.6, 1.8, 2.6, 0x3b0f0f); vox(binG, 9, 1.8, -9.8, 3, 0.3, 3, 0x1e0707); vox(binG, 9, 1.82, -9.8, 2.0, 0.05, 2.0, 0x050101, { cast: false });
  vox(binG, 9, 0.2, -8.45, 1.4, 0.08, 0.08, 0xff3b3b, { cast: false, mat: glow(0xff3b3b, 1) });
  vox(binG, 11.4, 0, -10.5, 1.4, 2.0, 1.4, 0x1b3a1f); vox(binG, 11.4, 2.0, -10.5, 1.5, 0.15, 1.5, 0x0f2412); vox(binG, 11.4, 0.9, -9.78, 0.7, 0.6, 0.05, 0x7dff3a, { cast: false, mat: glow(0x7dff3a, 1) });
  const shelfG = new T.Group(); scene.add(shelfG);
  vox(shelfG, 12.4, 0, 1.5, 2.4, 3.2, 3, 0x3a3010); vox(shelfG, 12.4, 3.2, 1.5, 2.8, 0.3, 3.4, 0x1e1808);
  sign(shelfG, 11.15, 2.0, 1.5, 2.2, 0.9, ['HOLD'], '#1e1808', '#ffd84a', 20, -Math.PI / 2);

  // --- TRADER: terminal with candle chart, approved list, status
  const trG = new T.Group(); scene.add(trG);
  vox(trG, 9, 0, 11.5, 10, 1.5, 2.6, 0x0d1510); vox(trG, 9, 1.5, 11.5, 10.4, 0.25, 3, 0x1a2a1f);
  const monitors = [];
  for (let i = -1; i <= 1; i++) {
    vox(trG, 9 + i * 3.2, 1.75, 12.3, 0.5, 0.9, 0.5, 0x0b0d12);
    vox(trG, 9 + i * 3.2, 2.55, 12.4, 3.0, 2.3, 0.3, 0x040507);
    const tt = paint(512, 384, () => {}, '#040a06');
    plane(trG, 9 + i * 3.2, 3.7, 12.58, 2.7, 2.0, tt, 0);
    monitors.push(tt);
  }
  vox(trG, 4.4, 0, 12.0, 1.2, 1.7, 1.2, 0x15171d); vox(trG, 4.4, 1.7, 12.0, 1.4, 0.2, 1.4, 0x0b0d12);
  tagStation(trG, 'trader');
  // --- STRATEGIST: whiteboard on the left wall + table with laptop and sticky notes
  const stG = new T.Group(); scene.add(stG);
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
  vox(stG, -13.6, 1.72, 6.6, 1.3, 0.1, 1.6, 0x15171d); vox(stG, -13.9, 1.82, 6.6, 0.1, 1.1, 1.5, 0x07080b); plane(stG, -13.84, 2.4, 6.6, 1.4, 0.95, paint(256, 192, centered(['> chat'], '#ff7ac8', 40), '#10040c'), Math.PI / 2);
  [0xffd84a, 0xff7ac8, 0x7dff3a, 0x2adfff].forEach((c, i) => vox(stG, -13.4, 1.72, 8.2 + i * 0.6, 0.45, 0.04, 0.45, c, { cast: false }));
  tagStation(stG, 'strategist');
  // --- lab bench with flasks along the left wall
  const benchG = new T.Group(); scene.add(benchG);
  vox(benchG, -14.7, 0, -1.2, 1.8, 1.6, 7.0, 0x151a24); vox(benchG, -14.7, 1.6, -1.2, 2.0, 0.2, 7.4, 0x2c3342);
  [[-3.6, 0xff5c5c], [-2.4, 0x2aff8a], [-1.2, 0x2adfff], [0, 0xffd84a], [1.2, 0xb58cff]].forEach(([z, c]) => {
    vox(benchG, -14.7, 1.8, z, 0.7, 0.6, 0.7, c, { cast: false, mat: Object.assign({ transparent: true, opacity: 0.8 }, glow(c, 1.0)) });
    vox(benchG, -14.7, 2.4, z, 0.25, 0.6, 0.25, 0xcfe8ff, { cast: false, mat: { transparent: true, opacity: 0.4 } });
    flasks.push(benchG.children[benchG.children.length - 2]);
  });
  // bubbling tank (back, between optimizer and chamber)
  const tankG = new T.Group(); scene.add(tankG);
  vox(tankG, 15.0, 0, -11.8, 1.6, 0.4, 1.6, 0x14171f);
  vox(tankG, 15.0, 0.4, -11.8, 1.4, 4.2, 1.4, 0x2aff8a, { cast: false, mat: Object.assign({ transparent: true, opacity: 0.22, depthWrite: false }, glow(0x0a6a3a, 0.6)) });
  vox(tankG, 15.0, 4.6, -11.8, 1.6, 0.3, 1.6, 0x14171f);
  for (let i = 0; i < 9; i++) { const b = vox(tankG, 15.0, 1, -11.8, 0.18, 0.18, 0.18, 0xbfffd8, { cast: false, mat: glow(0x7dffb0, 1) }); b.userData = { y: Math.random() * 4, x: (Math.random() - .5) * 0.8, z: (Math.random() - .5) * 0.8, v: 0.8 + Math.random() }; bubbles.push(b); }

  // coloured pools of light
  pool(0x4aa3ff, -9, 5, -8, 0.8); pool(0xffa533, -1.7, 5, -8, 0.95); pool(0xa07bff, 6, 5, -8, 0.9);
  pool(0xfff2c9, 12, 5, -3, 0.7); pool(0x39ff7a, 9, 5, 10, 0.8); pool(0xff7ac8, -12, 5, 8, 0.8); pool(0x2adfff, -14, 4, -1, 0.5);

  makeScientist('research', 'Researcher', { coat: 0xe9edf5, trim: 0xd5dbe8, accent: 0x4aa3ff, hair: 0x3a2a1c, item: 'clipboard' });
  makeScientist('optimizer', 'Optimizer', { coat: 0xe3862a, trim: 0xc87420, accent: 0xffb03a, hair: 0x1c1410, goggles: true, glasses: false, skin: 0xc58d63, item: null });
  makeScientist('validator', 'Validator', { coat: 0x8f6bd6, trim: 0x7656bb, accent: 0xb58cff, hair: 0xd8d8e8, hairStyle: 'tall', item: 'flask' });
  makeScientist('judge', 'Judge', { coat: 0x13151d, trim: 0xd4af37, accent: 0xd4af37, hair: 0xb9b9c4, hairStyle: 'short', shirt: 0xe9edf5, pocket: 0x2a2e3b, item: 'gavel', skin: 0xd9a77e });
  makeScientist('trader', 'Trader', { coat: 0x2fae63, trim: 0x258a4e, accent: 0x5ee27a, hair: 0x121212, headset: true, glasses: false, skin: 0x8d5b3a });
  makeScientist('strategist', 'Strategist', { coat: 0xd9529c, trim: 0xb8417f, accent: 0xff7ac8, hair: 0x5a1f3a, hairStyle: 'bun', item: 'tablet', skin: 0xf0c8a5 });
  // they stand at their stations but turn towards the belt (and the camera) to watch the strategies go by
  place(robots.research, -8.6, -6.7, -6.5, 0); place(robots.optimizer, -1.7, -6.5, -0.2, 0);
  place(robots.validator, 4.2, -6.7, 5.8, 0); place(robots.judge, 13.2, -5, 9.5, 2, 1.9);
  place(robots.trader, 5.8, 10.2, 10, 14); place(robots.strategist, -11.4, 8.0, -7, 14);
  Object.values(robots).forEach(r => tagStation(r.g, r.name));

  // ---------- crates (strategies travelling on the belt) ----------
  const crates = [];
  function makeCrate(color) {
    const g = new T.Group();
    vox(g, 0, 0, 0, 1.3, 1.1, 1.3, color, { mat: glow(color, 0.45) }); vox(g, 0, 1.1, 0, 0.7, 0.2, 0.7, 0xffffff, { cast: false, mat: glow(0x888888, 0.8) });
    const c = { g, s: 0, target: 0, free: null, hold: 0, then: null, glow: 0 };
    scene.add(g); crates.push(c); return c;
  }
  function placeCrate(c) {
    if (c.free) { c.g.position.set(c.free.x, c.free.y, c.free.z); return; }
    const p = pointAt(c.s); c.g.position.set(p[0], 0.4 + Math.sin(performance.now() / 300 + c.s) * 0.05, p[1]);
  }
  let current = null;
  const spawn = (stop, color) => { const c = makeCrate(color); c.s = c.target = STOP[stop]; placeCrate(c); return c; };
  const goTo = (c, stop, then) => { c.target = STOP[stop]; c.then = then || null; };

  // ---------- events ----------
  let gearSpeed = 0.4, partSpeed = 0.3, optRunning = false, valUntil = 0, heatT = 0;
  const now = () => performance.now() / 1000;
  const MAP = { researcher: 'research', strategist: 'strategist', you: 'strategist', optimizer: 'optimizer', planner: 'optimizer', validator: 'validator', judge: 'judge', auditor: 'judge', lab: 'judge' };
  function speak(station, text, secs) {
    const r = robots[station]; if (!r || !text) return;
    r.bub.textContent = text.length > 110 ? text.slice(0, 107) + '...' : text;
    r.bubUntil = now() + (secs || 6); r.until = now() + 2.5;
  }
  function setLamp(status, name) {
    const col = status === 'APPROVED' ? 0x39ff7a : status === 'REJECTED' ? 0xff4b4b : 0xffd84a;
    lamp.material = new T.MeshLambertMaterial({ color: col, emissive: col, emissiveIntensity: 1.2 });
    lamp.userData.until = now() + 10; gavel.userData.hit = now();
    const hex = '#' + col.toString(16).padStart(6, '0');
    verdict.redraw(centered([status === 'INSUFFICIENT_EVIDENCE' ? 'NOT ENOUGH DATA' : status, (name || '').slice(0, 22)], hex, status === 'INSUFFICIENT_EVIDENCE' ? 40 : 56), '#07080c');
  }
  function onEvent(ev) {
    const st = MAP[ev.agent];
    if (st) speak(st, ev.message);
    const msg = ev.message || '';
    if (ev.agent === 'researcher' && ev.type === 'proposal') { current = spawn('research', 0x4aa3ff); goTo(current, 'optimizer'); }
    if (ev.agent === 'optimizer' && ev.type === 'study_started') { optRunning = true; if (!current) current = spawn('optimizer', 0xffb03a); else goTo(current, 'optimizer'); }
    if (ev.agent === 'validator' && ev.type === 'surface') { valUntil = now() + 7; if (current) goTo(current, 'validator'); }
    if (ev.agent === 'judge' && ev.type === 'verdict') {
      optRunning = false;
      const status = /APPROVED/.test(msg) ? 'APPROVED' : /INSUFFICIENT/.test(msg) ? 'INSUFFICIENT_EVIDENCE' : 'REJECTED';
      setLamp(status, msg.split(':')[0]);
      if (!current) current = spawn('validator', 0xb58cff);
      const c = current; current = null;
      goTo(c, 'judge', () => {
        c.hold = 1.2;
        c.then2 = () => {
          if (status === 'APPROVED') { goTo(c, 'trader', () => { c.fade = 6; }); }
          else if (status === 'REJECTED') { c.free = { x: 9, y: 0.4, z: -5 }; c.drop = true; }
          else goTo(c, 'shelf', () => { c.fade = 12; });
        };
      });
    }
  }

  // ---------- main loop ----------
  const clk = new T.Clock();
  function updateOverlay() {
    const w = canvas.clientWidth, h = canvas.clientHeight, v = new T.Vector3();
    Object.values(robots).forEach(r => {
      r.head.getWorldPosition(v); v.y += 1.1;
      const p = v.clone().project(camera);
      const vis = p.z < 1 && p.x > -1.1 && p.x < 1.1 && p.y > -1.1 && p.y < 1.1;
      const sx = (p.x * 0.5 + 0.5) * w, sy = (-p.y * 0.5 + 0.5) * h;
      r.tag.style.display = vis ? 'block' : 'none'; r.tag.style.left = sx + 'px'; r.tag.style.top = sy + 'px';
      r.bub.style.left = sx + 'px'; r.bub.style.top = (sy - 22) + 'px';
      r.bub.classList.toggle('on', vis && now() < r.bubUntil);
    });
  }
  function frame() {
    const dt = Math.min(clk.getDelta(), 0.1), t = now();
    controls.update();
    Object.values(robots).forEach(r => {
      const active = t < r.until;
      r.g.position.y = r.base + Math.abs(Math.sin(t * (active ? 7 : 2) + r.phase)) * (active ? 0.22 : 0.04);
      r.armL.rotation.x = active ? Math.sin(t * 9 + r.phase) * 0.9 : Math.sin(t * 1.5 + r.phase) * 0.07;
      r.armR.rotation.x = active ? -Math.sin(t * 9 + r.phase) * 0.9 : -Math.sin(t * 1.5 + r.phase) * 0.07;
    });
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
    if (lamp.userData.until && t > lamp.userData.until) { lamp.material = mat(0x3a3f50); lamp.userData.until = 0; }
    const gh = gavel.userData.hit ? t - gavel.userData.hit : 9;
    gavel.rotation.x = gh < 0.8 ? -Math.abs(Math.sin(gh * 14)) * 0.9 : 0;
    for (let i = crates.length - 1; i >= 0; i--) {
      const c = crates[i];
      if (c.drop) {
        c.free.z -= dt * 5; if (c.free.z <= -9.8) { c.free.y -= dt * 4; c.g.scale.multiplyScalar(1 - dt * 1.5); }
        if (c.free.y < -1.5) { scene.remove(c.g); crates.splice(i, 1); continue; }
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
    camera.zoom = Math.min(1, (visible / w) * 1.42);
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
    if (hit && window.LabScene.onPick) window.LabScene.onPick(hit.object.userData.station);
  });

  // ---------- trader monitors ----------
  const L = { wait: ['OFO TRADING DESK', 'waiting for', 'approved strategies'], none: 'none yet' };
  function setTrader(names, labels) {
    labels = labels || {};
    const head = labels.head || 'APPROVED';
    monitors[0].redraw(centered(names.length ? [labels.desk || 'TRADING DESK', names.length + ' ' + (labels.ready || 'ready')] : (labels.wait || L.wait), names.length ? '#5ee27a' : '#3a8a50', 34), '#040a06');
    monitors[2].redraw(centered(names.length ? [head, ...names.slice(0, 3).map(n => n.slice(0, 18))] : [head, labels.none || L.none], names.length ? '#5ee27a' : '#3a8a50', 32), '#040a06');
  }
  function setTraderChart(candles, symbol) {
    if (!candles || !candles.length) return;
    monitors[1].redraw((g, w, h) => {
      const cs = candles.slice(-48), hi = Math.max(...cs.map(c => c[2])), lo = Math.min(...cs.map(c => c[3])), pad = 38, cw = (w - 2 * pad) / cs.length;
      const Y = v => pad + (1 - (v - lo) / ((hi - lo) || 1)) * (h - 2 * pad - 30);
      g.strokeStyle = '#122418'; g.lineWidth = 1; for (let i = 0; i < 5; i++) { g.beginPath(); g.moveTo(pad, pad + i * 62); g.lineTo(w - pad, pad + i * 62); g.stroke(); }
      cs.forEach((c, i) => {
        const x = pad + i * cw + cw / 2, up = c[4] >= c[1]; g.strokeStyle = g.fillStyle = up ? '#39ff7a' : '#ff4b4b'; g.lineWidth = 2;
        g.beginPath(); g.moveTo(x, Y(c[2])); g.lineTo(x, Y(c[3])); g.stroke();
        g.fillRect(x - cw * 0.3, Math.min(Y(c[1]), Y(c[4])), cw * 0.6, Math.max(2, Math.abs(Y(c[1]) - Y(c[4]))));
      });
      g.fillStyle = '#8bd7a0'; g.font = 'bold 26px monospace'; g.textAlign = 'left'; g.fillText((symbol || 'NQ') + ' ' + cs.length + ' candles', pad, 28);
      g.textAlign = 'right'; g.fillText(cs[cs.length - 1][4].toFixed(2), w - pad, 28);
    }, '#040a06');
  }
  setTrader([]);

  window.LabScene = { onEvent, speak, setTrader, setTraderChart, onPick: null, ready: true, robots, resize, camera, controls };
  frame();
})();
