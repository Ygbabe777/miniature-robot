/* OFO Lab - voxel laboratory. Every animation is driven by real events from data/events.jsonl. */
(function () {
  const T = THREE;
  const canvas = document.getElementById('scene');
  const overlay = document.getElementById('bubbles');
  const renderer = new T.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene();
  scene.background = new T.Color(0x0e1118);
  scene.fog = new T.Fog(0x0e1118, 60, 120);
  const camera = new T.PerspectiveCamera(42, 1, 0.1, 300);
  camera.position.set(31, 25, 35);
  const controls = new T.OrbitControls(camera, canvas);
  controls.target.set(0, 2.5, 1);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.47;
  controls.minDistance = 14; controls.maxDistance = 95;

  scene.add(new T.HemisphereLight(0xbfd4ff, 0x232842, 0.95));
  const sun = new T.DirectionalLight(0xfff0d2, 0.85);
  sun.position.set(22, 38, 18);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -34, right: 34, top: 34, bottom: -34, near: 1, far: 120 });
  scene.add(sun);

  // ---------- voxel helpers ----------
  const matCache = {}, geoCache = {};
  const mat = (c, o) => { const k = c + JSON.stringify(o || {}); return matCache[k] || (matCache[k] = new T.MeshLambertMaterial(Object.assign({ color: c }, o || {}))); };
  const geo = (w, h, d) => { const k = w + '/' + h + '/' + d; return geoCache[k] || (geoCache[k] = new T.BoxGeometry(w, h, d)); };
  // x,z = centre on the floor, y = bottom
  function vox(parent, x, y, z, w, h, d, color, o) {
    o = o || {};
    const m = new T.Mesh(geo(w, h, d), mat(color, o.mat));
    m.position.set(x, y + h / 2, z);
    m.castShadow = o.cast !== false; m.receiveShadow = true;
    parent.add(m); return m;
  }
  function textTexture(lines, w, h, o) {
    o = o || {};
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d');
    g.fillStyle = o.bg || '#0a0d14'; g.fillRect(0, 0, w, h);
    g.fillStyle = o.fg || '#5ee27a'; g.textAlign = 'center'; g.textBaseline = 'middle';
    const size = o.size ? o.size * 2 : Math.floor(h / (lines.length + 1));
    g.font = 'bold ' + size + 'px monospace';
    lines.forEach((l, i) => g.fillText(l, w / 2, (h / (lines.length + 1)) * (i + 1)));
    const t = new T.CanvasTexture(c); t.magFilter = T.NearestFilter; t.minFilter = T.NearestFilter;
    return { tex: t, canvas: c, g };
  }
  function sign(parent, x, y, z, w, h, lines, o, rotY) {
    const tt = textTexture(lines, 512, Math.round(512 * h / w), o);
    const m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ map: tt.tex }));
    m.position.set(x, y, z); m.rotation.y = rotY || 0; parent.add(m); return { mesh: m, tt };
  }

  // ---------- room ----------
  const room = new T.Group(); scene.add(room);
  const X0 = -16, X1 = 16, Z0 = -14, Z1 = 14;
  (function floor() {
    const n = (X1 - X0) * (Z1 - Z0);
    const a = new T.InstancedMesh(geo(1, 0.4, 1), mat(0x2a3042), Math.ceil(n / 2) + 1);
    const b = new T.InstancedMesh(geo(1, 0.4, 1), mat(0x353d55), Math.ceil(n / 2) + 1);
    const m4 = new T.Matrix4(); let ia = 0, ib = 0;
    for (let x = X0; x < X1; x++) for (let z = Z0; z < Z1; z++) {
      m4.setPosition(x + 0.5, -0.2, z + 0.5);
      ((x + z) & 1 ? a : b).setMatrixAt((x + z) & 1 ? ia++ : ib++, m4);
    }
    a.count = ia; b.count = ib; a.receiveShadow = b.receiveShadow = true; room.add(a, b);
  })();
  vox(room, 0, 0, Z0 - 0.5, X1 - X0 + 1, 11, 1, 0x1c2132);            // back wall
  vox(room, X0 - 0.5, 0, 0, 1, 11, Z1 - Z0, 0x1a1f2f);                // left wall
  vox(room, 0, 0, Z0 + 0.1, X1 - X0, 0.7, 0.3, 0x0d1019);             // baseboards
  vox(room, X0 + 0.1, 0, 0, 0.3, 0.7, Z1 - Z0, 0x0d1019);
  for (let i = -7; i <= 7; i++) vox(room, i * 2.2, 0.7, Z0 + 0.12, 2.0, 5.2, 0.12, i % 2 ? 0x232a40 : 0x1f2538, { cast: false });  // wall panels
sign(room, 0, 9.3, Z0 + 0.02, 12, 2, ['OFO  LAB'], { bg: '#10141f', fg: '#5ee27a', size: 44 });
  sign(room, X0 + 0.02, 7.3, -2, 9, 2, ['PROP PROJECT'], { bg: '#10141f', fg: '#ffb03a', size: 34 }, Math.PI / 2);

  // ---------- belt ----------
  const PATH = [[-13, -5], [9, -5], [9, 9]];
  const segLen = [];
  for (let i = 0; i < PATH.length - 1; i++) segLen.push(Math.hypot(PATH[i + 1][0] - PATH[i][0], PATH[i + 1][1] - PATH[i][1]));
  const TOTAL = segLen.reduce((a, b) => a + b, 0);
  function pointAt(s) {
    s = Math.max(0, Math.min(TOTAL, s));
    for (let i = 0; i < segLen.length; i++) {
      if (s <= segLen[i] || i === segLen.length - 1) {
        const t = segLen[i] ? s / segLen[i] : 0, a = PATH[i], b = PATH[i + 1];
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      }
      s -= segLen[i];
    }
  }
  for (let s = 0; s <= TOTAL; s += 1) { const p = pointAt(s); vox(room, p[0], 0, p[1], 1.6, 0.35, 1.6, 0x14171f, { cast: false }); }
  const chevrons = [];
  for (let s = 0; s < TOTAL; s += 2.5) { const m = vox(room, 0, 0.36, 0, 0.5, 0.08, 0.5, 0xffd84a, { cast: false }); m.userData.s = s; chevrons.push(m); }
  const STOP = { research: 4, optimizer: 11, validator: 18, judge: 22, shelf: 22 + 6, trader: TOTAL - 0.5 };
  // The belt is [-13..9] along x (22 long) then down x=9. stops are distances along it.

  // ---------- robots ----------
  const robots = {};
  function makeRobot(name, color, label) {
    const g = new T.Group();
    const dark = 0x1a1d27;
    vox(g, -0.35, 0, 0, 0.45, 0.9, 0.5, dark); vox(g, 0.35, 0, 0, 0.45, 0.9, 0.5, dark);       // legs
    vox(g, 0, 0.9, 0, 1.5, 1.5, 0.95, color);                                                 // body
    vox(g, 0, 1.4, 0.5, 0.5, 0.5, 0.1, 0xffffff, { mat: { emissive: 0x444444 } });             // chest light
    const armL = new T.Group(), armR = new T.Group();
    vox(armL, 0, -1.0, 0, 0.4, 1.2, 0.45, color); armL.position.set(-1.0, 2.3, 0);
    vox(armR, 0, -1.0, 0, 0.4, 1.2, 0.45, color); armR.position.set(1.0, 2.3, 0);
    g.add(armL, armR);
    const head = new T.Group(); head.position.y = 2.45; g.add(head);
    vox(head, 0, 0, 0, 1.3, 1.2, 1.1, 0xe8ecf6);
    vox(head, -0.3, 0.55, 0.56, 0.28, 0.28, 0.1, 0x10131b); vox(head, 0.3, 0.55, 0.56, 0.28, 0.28, 0.1, 0x10131b);
    vox(head, 0, 0.2, 0.56, 0.6, 0.12, 0.1, 0x10131b);
    vox(head, 0, 1.2, 0, 0.12, 0.5, 0.12, 0x8b93a8);
    const bulb = vox(head, 0, 1.7, 0, 0.3, 0.3, 0.3, color, { mat: { emissive: color, emissiveIntensity: 0.9 } });
    const tag = document.createElement('div'); tag.className = 'tag'; tag.textContent = label; overlay.appendChild(tag);
    const bub = document.createElement('div'); bub.className = 'bubble'; overlay.appendChild(bub);
    const r = { name, g, armL, armR, head, bulb, tag, bub, until: 0, bubUntil: 0, phase: Math.random() * 6, base: 0 };
    robots[name] = r; scene.add(g); return r;
  }
  function place(r, x, z, faceX, faceZ, y) {
    r.g.position.set(x, y || 0, z); r.base = y || 0; r.g.rotation.y = Math.atan2(faceX - x, faceZ - z);
  }

  const pickables = [];
  const tagStation = (obj, st) => obj.traverse(o => { if (o.isMesh) { o.userData.station = st; pickables.push(o); } });

  // --- researcher: library + reading desk
  const libG = new T.Group(); scene.add(libG);
  for (let k = 0; k < 3; k++) {
    vox(libG, X0 + 1.2, 0, -12 + k * 2.6, 1.6, 6.5, 2.4, 0x5a3b22);
    for (let row = 0; row < 5; row++) for (let b = 0; b < 4; b++)
      vox(libG, X0 + 1.6, 0.5 + row * 1.2, -12.9 + k * 2.6 + b * 0.55, 0.9, 0.9, 0.42, [0xff5c5c, 0x4aa3ff, 0xffd84a, 0x5ee27a, 0xb58cff][(row + b + k) % 5], { cast: false });
  }
  vox(libG, -9, 0, -8.2, 4.2, 1.3, 2.2, 0x6b4a2b); vox(libG, -9, 1.3, -8.3, 1.8, 0.2, 1.3, 0xf4f1e6);
  vox(libG, -10.6, 1.3, -8.6, 0.4, 1.4, 0.4, 0x8b93a8); vox(libG, -10.6, 2.7, -8.6, 1.1, 0.5, 1.1, 0xffd84a, { mat: { emissive: 0xaa8c00 } });
  sign(libG, -9, 5.6, -13.3, 6, 1.2, ['RESEARCHER'], { bg: '#10243d', fg: '#4aa3ff', size: 30 });
  tagStation(libG, 'research');

  // --- optimizer: machine with gears
  const optG = new T.Group(); scene.add(optG);
  vox(optG, -2, 0, -9, 6, 4.2, 3.6, 0x3a3f52); vox(optG, -2, 4.2, -9, 6.4, 0.5, 4, 0x2a2e3e);
  vox(optG, -2, 0.6, -7.1, 4.8, 2.4, 0.2, 0x0e1119);
  const lights = [];
  for (let i = 0; i < 5; i++) lights.push(vox(optG, -4 + i * 1.0, 3.3, -7.1, 0.4, 0.4, 0.2, 0xffb03a, { mat: { emissive: 0xffb03a, emissiveIntensity: 0.8 } }));
  vox(optG, -2, 4.7, -9, 1.2, 2.2, 1.2, 0x4b5068);
  const gears = [];
  [[-3.6, 1.8], [-0.4, 1.8]].forEach(([gx, gy], gi) => {
    const gr = new T.Group(); gr.position.set(gx, gy, -6.95);
    vox(gr, 0, -0.9, 0, 1.8, 1.8, 0.3, 0xffb03a, { mat: { emissive: 0x6b3d00 } });
    for (let t = 0; t < 8; t++) { const a = t * Math.PI / 4; const tooth = vox(gr, Math.cos(a) * 1.15, -0.4 + Math.sin(a) * 1.15 - 0.5, 0, 0.5, 0.5, 0.3, 0xe08a00); }
    gr.userData.dir = gi ? -1 : 1; optG.add(gr); gears.push(gr);
  });
  sign(optG, -2, 6.9, -9.4, 6, 1.2, ['OPTIMIZER'], { bg: '#3b2300', fg: '#ffb03a', size: 30 });
  tagStation(optG, 'optimizer');

  // --- validator: glass chamber with Monte Carlo particles
  const valG = new T.Group(); scene.add(valG);
  vox(valG, 5, 0, -9, 6.4, 0.6, 5, 0x2b2f42);
  const glass = vox(valG, 5, 0.6, -9, 6, 4.6, 4.6, 0x8a6bff, { cast: false, mat: { transparent: true, opacity: 0.22, depthWrite: false } });
  vox(valG, 5, 5.2, -9, 6.4, 0.5, 5, 0x2b2f42);
  const parts = [];
  for (let i = 0; i < 26; i++) {
    const p = vox(valG, 5, 1, -9, 0.38, 0.38, 0.38, [0xb58cff, 0xffffff, 0x7dd3fc][i % 3], { cast: false, mat: { emissive: 0x442a88 } });
    p.userData = { v: [(Math.random() - .5), (Math.random() - .5), (Math.random() - .5)], p: [Math.random() * 5 - 2.5, Math.random() * 4 + 0.8, Math.random() * 4 - 2] };
    parts.push(p);
  }
  sign(valG, 5, 6.9, -9.4, 6, 1.2, ['VALIDATOR'], { bg: '#1c1233', fg: '#b58cff', size: 30 });
  tagStation(valG, 'validator');

  // --- judge: podium + lamp + gavel
  const judG = new T.Group(); scene.add(judG);
  vox(judG, 13.2, 0, -5, 3.4, 1.6, 4.4, 0x3b2a1a); vox(judG, 13.2, 1.6, -5, 3.8, 0.3, 4.8, 0x5a3b22);
  vox(judG, 14.4, 0, -5, 1.4, 4.2, 4.8, 0x2f2217);
  const lamp = vox(judG, 14.4, 4.2, -5, 1.2, 1.2, 1.2, 0x555b70, { mat: { emissive: 0x000000 } });
  const gavel = new T.Group(); gavel.position.set(12.4, 2.1, -3.9); judG.add(gavel);
  vox(gavel, 0, 0, 0, 0.2, 0.2, 1.2, 0x8b5a2b); vox(gavel, 0, 0, 0.6, 0.55, 0.55, 0.55, 0xc58a46);
  sign(judG, 14.6, 6.2, -5, 5, 1.1, ['JUDGE'], { bg: '#1d1d24', fg: '#ffffff', size: 30 }, -Math.PI / 2);
  tagStation(judG, 'judge');
  // bin (rejected) and storage shelf (insufficient evidence)
  const binG = new T.Group(); scene.add(binG);
  vox(binG, 9, 0, -9.8, 2.6, 1.8, 2.6, 0x6b1f1f); vox(binG, 9, 1.8, -9.8, 3, 0.3, 3, 0x3b1010);
  vox(binG, 9, 1.82, -9.8, 2.0, 0.05, 2.0, 0x120505, { cast: false });
  const shelfG = new T.Group(); scene.add(shelfG);
  vox(shelfG, 12.3, 0, 1.5, 2.4, 3.2, 3, 0x6b5a1f); vox(shelfG, 12.3, 3.2, 1.5, 2.8, 0.3, 3.4, 0x3b3010);
  sign(shelfG, 11.05, 2.0, 1.5, 2.2, 0.9, ['ATTESA'], { bg: '#2b2305', fg: '#ffd84a', size: 22 }, -Math.PI / 2);

  // --- trader desk with monitors
  const trG = new T.Group(); scene.add(trG);
  vox(trG, 9, 0, 11.6, 9, 1.4, 2.6, 0x2a3a2f); vox(trG, 9, 1.4, 11.6, 9.4, 0.25, 3, 0x3b5240);
  const monitors = [];
  for (let i = -1; i <= 1; i++) {
    vox(trG, 9 + i * 3, 1.65, 12.3, 0.5, 0.9, 0.5, 0x15181f);
    const frame = vox(trG, 9 + i * 3, 2.5, 12.4, 2.7, 2.1, 0.3, 0x0b0d12);
    const s = sign(trG, 9 + i * 3, 3.55, 12.58, 2.4, 1.8, ['TRADING DESK', 'in attesa', 'di strategie', 'approvate'], { bg: '#06120a', fg: '#5ee27a', size: 17 }, 0);
    monitors.push(s);
  }
  tagStation(trG, 'trader');

  // --- strategist: chat counter
  const stG = new T.Group(); scene.add(stG);
  vox(stG, -6, 0, 8.6, 5.2, 1.4, 1.8, 0x5b2b4a); vox(stG, -6, 1.4, 8.6, 5.6, 0.25, 2.2, 0x7a3a64);
  sign(stG, -6, 3.6, 9.62, 4.2, 1.1, ['STRATEGIST', 'chatta qui ->'], { bg: '#2b0f22', fg: '#ff7ac8', size: 20 }, 0);
  tagStation(stG, 'strategist');

  makeRobot('research', 0x4aa3ff, 'Researcher'); place(robots.research, -9, -5.4 + 3, -9, -8);
  makeRobot('optimizer', 0xffb03a, 'Optimizer'); place(robots.optimizer, -2, -2.7, -2, -8);
  makeRobot('validator', 0xb58cff, 'Validator'); place(robots.validator, 5, -2.7, 5, -8);
  makeRobot('judge', 0xf2f2f2, 'Judge'); place(robots.judge, 13.2, -5, 9, -5, 1.9);
  makeRobot('trader', 0x5ee27a, 'Trader'); place(robots.trader, 3.8, 11.6, 9, 11.6);
  makeRobot('strategist', 0xff7ac8, 'Strategist'); place(robots.strategist, -6, 6.2, -6, 9);
  Object.values(robots).forEach(r => tagStation(r.g, r.name));

  // ---------- crates (strategies travelling on the belt) ----------
  const crates = [];
  function makeCrate(color) {
    const g = new T.Group();
    vox(g, 0, 0, 0, 1.3, 1.1, 1.3, color, { mat: { emissive: color, emissiveIntensity: 0.25 } });
    vox(g, 0, 1.1, 0, 0.7, 0.2, 0.7, 0xffffff, { cast: false, mat: { emissive: 0x666666 } });
    const c = { g, s: 0, target: 0, free: null, hold: 0, then: null, glow: 0 };
    scene.add(g); crates.push(c); return c;
  }
  function placeCrate(c) {
    if (c.free) { c.g.position.set(c.free.x, c.free.y, c.free.z); return; }
    const p = pointAt(c.s); c.g.position.set(p[0], 0.4 + Math.sin(performance.now() / 300 + c.s) * 0.05, p[1]);
  }
  let current = null;
  const spawn = (stop, color) => { const c = makeCrate(color); c.s = c.target = STOP[stop]; placeCrate(c); return c; };
  function goTo(c, stop, then) { c.target = STOP[stop]; c.then = then || null; }

  // ---------- state driven by events ----------
  let gearSpeed = 0.4, partSpeed = 0.3, optRunning = false, valUntil = 0;
  const now = () => performance.now() / 1000;
  const MAP = { researcher: 'research', strategist: 'strategist', you: 'strategist', optimizer: 'optimizer', planner: 'optimizer',
                validator: 'validator', judge: 'judge', auditor: 'judge', lab: 'judge' };
  function speak(station, text, secs) {
    const r = robots[station]; if (!r || !text) return;
    r.bub.textContent = text.length > 110 ? text.slice(0, 107) + '...' : text;
    r.bubUntil = now() + (secs || 6); r.until = now() + 2.5;
  }
  function setLamp(status) {
    const col = status === 'APPROVED' ? 0x5ee27a : status === 'REJECTED' ? 0xff5c5c : 0xffd84a;
    lamp.material = new T.MeshLambertMaterial({ color: col, emissive: col, emissiveIntensity: 1 });
    lamp.userData.until = now() + 9; gavel.userData.hit = now();
  }
  function onEvent(ev) {
    const st = MAP[ev.agent];
    if (st) speak(st, ev.message);
    const msg = ev.message || '';
    if (ev.agent === 'researcher' && ev.type === 'proposal') { current = spawn('research', 0x4aa3ff); goTo(current, 'optimizer'); }
    if (ev.agent === 'optimizer' && ev.type === 'study_started') {
      optRunning = true;
      if (!current) current = spawn('optimizer', 0xffb03a);
      else { goTo(current, 'optimizer'); }
    }
    if (ev.agent === 'validator' && ev.type === 'surface') { valUntil = now() + 7; if (current) goTo(current, 'validator'); }
    if (ev.agent === 'judge' && ev.type === 'verdict') {
      optRunning = false;
      const status = /APPROVED/.test(msg) ? 'APPROVED' : /INSUFFICIENT/.test(msg) ? 'INSUFFICIENT_EVIDENCE' : 'REJECTED';
      setLamp(status);
      if (!current) current = spawn('validator', 0xb58cff);
      const c = current; current = null;
      goTo(c, 'judge', () => {
        c.hold = 1.2;
        c.then2 = () => {
          if (status === 'APPROVED') { c.glow = 1; goTo(c, 'trader', () => { c.fade = 6; }); }
          else if (status === 'REJECTED') { c.free = { x: 9, y: 0.4, z: -5 }; c.drop = true; }
          else { goTo(c, 'shelf', () => { c.fade = 12; }); }
        };
      });
    }
  }

  // ---------- main loop ----------
  const clock = new T.Clock();
  function updateOverlay() {
    const w = canvas.clientWidth, h = canvas.clientHeight, v = new T.Vector3();
    Object.values(robots).forEach(r => {
      r.head.getWorldPosition(v); v.y += 1.2;
      const p = v.clone().project(camera);
      const vis = p.z < 1 && p.x > -1.1 && p.x < 1.1 && p.y > -1.1 && p.y < 1.1;
      const sx = (p.x * 0.5 + 0.5) * w, sy = (-p.y * 0.5 + 0.5) * h;
      r.tag.style.display = vis ? 'block' : 'none'; r.tag.style.left = sx + 'px'; r.tag.style.top = sy + 'px';
      r.bub.style.left = sx + 'px'; r.bub.style.top = (sy - 22) + 'px';
      r.bub.classList.toggle('on', vis && now() < r.bubUntil);
    });
  }
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1), t = now();
    controls.update();
    // robots
    Object.values(robots).forEach(r => {
      const active = t < r.until;
      r.g.position.y = r.base + Math.abs(Math.sin(t * (active ? 7 : 2) + r.phase)) * (active ? 0.28 : 0.06);
      r.armL.rotation.x = active ? Math.sin(t * 9 + r.phase) * 0.9 : Math.sin(t * 1.5 + r.phase) * 0.08;
      r.armR.rotation.x = active ? -Math.sin(t * 9 + r.phase) * 0.9 : -Math.sin(t * 1.5 + r.phase) * 0.08;
      r.bulb.material.emissiveIntensity = active ? 1.6 : 0.6 + 0.3 * Math.sin(t * 3 + r.phase);
    });
    // belt chevrons
    chevrons.forEach(m => { m.userData.s = (m.userData.s + dt * 2.2) % TOTAL; const p = pointAt(m.userData.s); m.position.set(p[0], 0.4, p[1]); });
    // gears + lights
    gearSpeed += ((optRunning ? 4 : 0.35) - gearSpeed) * Math.min(1, dt * 3);
    gears.forEach(g => g.rotation.z += g.userData.dir * gearSpeed * dt);
    lights.forEach((l, i) => l.material.emissiveIntensity = optRunning ? (0.4 + 0.6 * Math.abs(Math.sin(t * 6 + i))) : 0.25);
    // validator particles
    const active = t < valUntil; partSpeed += ((active ? 5 : 0.6) - partSpeed) * Math.min(1, dt * 3);
    parts.forEach(p => {
      const u = p.userData;
      for (let k = 0; k < 3; k++) {
        u.p[k] += u.v[k] * partSpeed * dt * 2;
        const lim = k === 1 ? [0.8, 4.4] : [-2.5, 2.5];
        if (u.p[k] < lim[0] || u.p[k] > lim[1]) { u.v[k] *= -1; u.p[k] = Math.max(lim[0], Math.min(lim[1], u.p[k])); }
      }
      p.position.set(5 + u.p[0], u.p[1], -9 + u.p[2] * 0.9);
    });
    // judge lamp + gavel
    if (lamp.userData.until && t > lamp.userData.until) { lamp.material = mat(0x555b70, { emissive: 0x000000 }); lamp.userData.until = 0; }
    const gh = gavel.userData.hit ? t - gavel.userData.hit : 9;
    gavel.rotation.x = gh < 0.8 ? -Math.abs(Math.sin(gh * 14)) * 0.9 : 0;
    // crates
    for (let i = crates.length - 1; i >= 0; i--) {
      const c = crates[i];
      if (c.drop) {                                  // rejected: slide to the bin and drop in
        c.free.z -= dt * 5; if (c.free.z <= -9.8) { c.free.y -= dt * 4; c.g.scale.multiplyScalar(1 - dt * 1.5); }
        if (c.free.y < -1.5) { scene.remove(c.g); crates.splice(i, 1); continue; }
      } else if (c.hold > 0) { c.hold -= dt; if (c.hold <= 0 && c.then2) { const f = c.then2; c.then2 = null; f(); } }
      else if (Math.abs(c.target - c.s) > 0.05) { c.s += Math.sign(c.target - c.s) * Math.min(Math.abs(c.target - c.s), dt * 6); if (Math.abs(c.target - c.s) <= 0.05 && c.then) { const f = c.then; c.then = null; f(); } }
      if (c.fade) { c.fade -= dt; if (c.fade <= 0) { scene.remove(c.g); crates.splice(i, 1); continue; } }
      placeCrate(c);
    }
    renderer.render(scene, camera);
    updateOverlay();
    requestAnimationFrame(frame);
  }
  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h;
    // keep the whole room visible between the side panels (322px left, 442px right)
    const wide = w > 1000, leftW = wide ? 322 : 0, rightW = wide ? 442 : 0, visible = w - leftW - rightW;
    camera.zoom = Math.min(1, (visible / w) * 1.42);
    camera.setViewOffset(w, h, w / 2 - (leftW + (w - rightW)) / 2, 0, w, h);
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();

  // ---------- picking ----------
  const ray = new T.Raycaster(), mouse = new T.Vector2(); let down = null;
  canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
    mouse.set((e.clientX / canvas.clientWidth) * 2 - 1, -(e.clientY / canvas.clientHeight) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(pickables, false)[0];
    if (hit && window.LabScene.onPick) window.LabScene.onPick(hit.object.userData.station);
  });

  function setTrader(names) {
    monitors.forEach((m, i) => {
      const lines = names.length ? ['APPROVATE', ...names.slice(i * 2, i * 2 + 2).map(n => n.slice(0, 16)), names.length + ' totali']
                                  : ['TRADING DESK', 'in attesa', 'di strategie', 'approvate'];
      const g = m.tt.g, c = m.tt.canvas;
      g.fillStyle = '#06120a'; g.fillRect(0, 0, c.width, c.height); g.fillStyle = names.length ? '#5ee27a' : '#3a8a50';
      g.font = 'bold ' + Math.round(17 * c.width / 256) + 'px monospace'; g.textAlign = 'center'; g.textBaseline = 'middle';
      lines.forEach((l, k) => g.fillText(l, c.width / 2, (c.height / (lines.length + 1)) * (k + 1)));
      m.tt.tex.needsUpdate = true;
    });
  }

  window.LabScene = { onEvent, speak, setTrader, onPick: null, ready: true, robots };
  frame();
})();
