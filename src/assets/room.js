(function(){
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const F = D.frames, STEPS = D.steps, META = D.meta, TR = D.transcript;
const AG = ["price_action","options_flow","strategist","risk_manager","judge"];
const $ = (s, r=document) => r.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined && txt !== null) e.textContent = txt; return e; };
const fmt = v => (v === null || v === undefined) ? "n/d" : (typeof v === "number" ? v.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}) : String(v));
let cur = 0, playing = false, speed = D.ui.default_speed || 1, timer = null, inspected = null;
const bubbleSeen = {}; // chiave evento -> timestamp di prima visualizzazione
let bubbleOpen = {};   // agent -> true se riaperta manualmente

// ---------- sprite pixel-art
const BASE = ["............","....hhhh....","...hhhhhh...","...hsssssh..","...sesses...","...ssssss...","....ssss....","..bbbbbbbb..",".sbbbbbbbbs.",".sbbbbbbbbs.","..bbbbbbbb..","..bbbbbbbb..","..ll....ll..","..ll....ll..","..kk....kk.."];
function shade(hex, f){ const n=parseInt(hex.slice(1),16); const c=[(n>>16)&255,(n>>8)&255,n&255].map(v=>Math.max(0,Math.min(255,Math.round(v*f)))); return "#"+c.map(v=>v.toString(16).padStart(2,"0")).join(""); }
const ACC = {
 price_action:{hair:"#1d3b7a",extra:[[4,3,"#9fd4ff"],[4,5,"#9fd4ff"],[4,6,"#9fd4ff"],[4,8,"#9fd4ff"]]},
 options_flow:{hair:"#5a2d9c",extra:[[0,5,"#e0c8ff"],[0,6,"#e0c8ff"]]},
 strategist:{hair:"#1f7a48",extra:[[3,2,"#46e08c"],[3,3,"#46e08c"],[3,9,"#46e08c"],[3,10,"#46e08c"]]},
 risk_manager:{hair:"#6b1a1a",extra:[[3,3,"#ff5c5c"],[3,4,"#ff5c5c"],[3,5,"#ff5c5c"],[3,6,"#ff5c5c"],[3,7,"#ff5c5c"],[3,8,"#ff5c5c"],[7,1,"#ff9a9a"],[7,10,"#ff9a9a"]]},
 judge:{hair:"#f0f0f0",extra:[[2,2,"#f0f0f0"],[2,9,"#f0f0f0"],[3,2,"#f0f0f0"],[3,10,"#f0f0f0"],[7,5,"#ffffff"],[7,6,"#ffffff"]]}
};
function sprite(a){
  const col = META[a].color, a0 = ACC[a];
  const pal = {h:a0.hair, s:"#f2c8a0", e:"#10131c", b: a==="judge" ? "#4a4f5e" : shade(col,.78), l:"#2a2f45", k:"#0b0d14"};
  const grid = BASE.map(r => r.split(""));
  a0.extra.forEach(([r,c,color]) => { grid[r][c] = "#"+color.slice(1); });
  const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.setAttribute("viewBox","0 0 12 15"); svg.setAttribute("shape-rendering","crispEdges"); svg.setAttribute("class","sprite");
  grid.forEach((row,r) => row.forEach((ch,c) => {
    if (ch === ".") return;
    const rect = document.createElementNS("http://www.w3.org/2000/svg","rect");
    rect.setAttribute("x",c); rect.setAttribute("y",r); rect.setAttribute("width",1); rect.setAttribute("height",1);
    rect.setAttribute("fill", ch.startsWith("#") ? ch : pal[ch]); svg.appendChild(rect);
  }));
  return svg;
}
function monitorSVG(type, color){
  const s = document.createElementNS("http://www.w3.org/2000/svg","svg"); s.setAttribute("viewBox","0 0 60 24"); s.setAttribute("preserveAspectRatio","none");
  const add = (tag, at) => { const n = document.createElementNS("http://www.w3.org/2000/svg",tag); Object.entries(at).forEach(([k,v])=>n.setAttribute(k,v)); s.appendChild(n); return n; };
  if (type==="chart"){ [[4,6,14],[12,8,16],[20,4,12],[28,10,18],[36,6,15],[44,3,12],[52,5,10]].forEach(([x,y,h])=>{ add("line",{x1:x+2,x2:x+2,y1:y-2,y2:y+h+2,stroke:color,"stroke-width":1}); add("rect",{x:x,y:y,width:4,height:h*0.6,fill:color}); }); add("polyline",{points:"0,18 12,15 24,17 36,9 48,11 60,5",fill:"none",stroke:"#fff","stroke-width":1}); }
  if (type==="gamma"){ [6,12,18,24,30,36,42,48,54].forEach((x,i)=>{ const h=[6,10,16,8,20,12,6,14,9][i]; add("rect",{x:x-2,y:22-h,width:4,height:h,fill:color}); }); add("line",{x1:30,x2:30,y1:0,y2:24,stroke:"#fff","stroke-dasharray":"2 2"}); }
  if (type==="scenario"){ [4,10,16].forEach((y,i)=>add("line",{x1:4,x2:56-i*14,y1:y,y2:y,stroke:color,"stroke-width":2})); }
  if (type==="risk"){ add("rect",{x:4,y:9,width:52,height:7,fill:"none",stroke:color}); add("rect",{x:5,y:10,width:30,height:5,fill:color}); }
  if (type==="review"){ add("line",{x1:6,x2:54,y1:6,y2:6,stroke:color}); add("line",{x1:6,x2:44,y1:12,y2:12,stroke:color}); add("line",{x1:6,x2:50,y1:18,y2:18,stroke:color}); }
  return s;
}

// ---------- costruzione scena
const room = $("#room"), arrows = document.createElementNS("http://www.w3.org/2000/svg","svg");
arrows.setAttribute("id","arrows"); arrows.setAttribute("viewBox","0 0 100 100"); arrows.setAttribute("preserveAspectRatio","none");
const layerSym = el("div"), layerBub = el("div");
const desks = {}, agents = {};
AG.forEach(a => {
  const d = el("div","desk"); d.style.setProperty("--c", META[a].color);
  const p = D.locations[a].desk; d.style.left = p[0]+"%"; d.style.top = (p[1]-15)+"%";
  const mon = el("div","top-mon"); mon.appendChild(monitorSVG(META[a].monitor, META[a].color));
  if (a==="judge"){ const js = el("div","jscore",""); mon.appendChild(js); }
  d.appendChild(el("div","lbl", META[a].workstation)); d.appendChild(mon); d.appendChild(el("div","slab"));
  d.onclick = () => inspect(a); room.appendChild(d); desks[a] = d;
});
const table = el("div","table"); table.innerHTML = "<div class='t1'>COUNCIL TABLE</div><div class='t2'></div><div class='t3'></div><div class='t4'></div><div class='t5'></div>";
room.appendChild(table); room.appendChild(arrows); room.appendChild(layerSym);
AG.forEach(a => {
  const g = el("div","agent"); g.style.setProperty("--c", META[a].color);
  g.appendChild(el("div","fx")); g.appendChild(sprite(a));
  g.appendChild(el("div","np", META[a].emoji+" "+META[a].name));
  g.appendChild(el("div","chip","IDLE"));
  const c = el("div","conf"); c.appendChild(el("i")); g.appendChild(c);
  g.onclick = () => inspect(a); room.appendChild(g); agents[a] = g;
});
room.appendChild(layerBub);

// ---------- header / ticker
$("#clock-label").textContent = "RUN "+D.run.run_id;
const badges = $("#badges");
const mk = (t, c) => badges.appendChild(el("span","badge "+(c||""),t));
mk(D.run.mode==="MOCK" ? "MOCK · "+(D.run.mock_scenario||"ok") : "LIVE LLM", D.run.mode==="MOCK"?"warn":"ok");
mk("DATA "+D.run.quality, D.run.quality==="GREEN"?"ok":D.run.quality==="RED"?"bad":"warn");
if (D.run.synthetic) mk("DATI SINTETICI","warn");
mk(D.run.disclaimer,"bad");
const tk = $("#ticker");
D.ticker.forEach(t => { const d = el("div","tk"); d.appendChild(el("span",null,t.k)); d.appendChild(el("b",null,fmt(t.v))); tk.appendChild(d); });
tk.appendChild(el("div","src","DATA SOURCE: STATIC / PRE-MARKET"));

// ---------- timeline
const tl = $("#timeline");
const stepEls = STEPS.map(s => { const d = el("div","step waiting"); d.appendChild(el("span","ic","○")); d.appendChild(el("span",null,s.label));
  d.onclick = () => { go(s.jump); if (s.agent) inspect(s.agent); }; tl.appendChild(d); return d; });
const ICON = {done:"✓",active:"●",waiting:"○",failed:"✖",skipped:"–"};

// ---------- render
function evIdx(id){ return parseInt(id.slice(4),10)-1; }
function clamp(v,a,b){ return Math.max(a,Math.min(b,v)); }
function render(i){
  cur = clamp(i,0,F.length-1); const f = F[cur];
  room.className = "mode-"+f.room_mode;
  document.documentElement.style.setProperty("--mv", (900/speed)+"ms");
  AG.forEach(a => {
    const s = f.agents[a], g = agents[a];
    g.style.left = s.pos[0]+"%"; g.style.top = s.pos[1]+"%";
    g.className = "agent st-"+s.state+(s.moving?" moving":"")+(a==="judge" && s.loc==="center"?" judge-big":"");
    const fx = s.facing ? f.agents[s.facing].pos[0] : null;
    if (fx !== null && fx < s.pos[0]) g.classList.add("face-left");
    $(".chip",g).textContent = s.state;
    $(".conf i",g).style.width = (s.confidence ?? 0)+"%";
    $(".conf",g).title = s.confidence===null ? "confidenza n/d" : "confidenza "+s.confidence+"/100";
    const active = f.monitors && ["ANALYZING","THINKING","READING_DATA","SPEAKING","VETO","APPROVED","WARNING"].includes(s.state);
    desks[a].classList.toggle("on", !!active);
  });
  const js = $(".jscore", desks.judge); if (js) js.textContent = (f.table.judge_score ?? "");
  // tavolo
  const t = f.table; $(".t2",table).textContent = "NQ "+fmt(t.price); $(".t3",table).textContent = t.regime ? "REGIME "+t.regime : "";
  $(".t4",table).textContent = t.status || ""; $(".t5",table).textContent = (t.scenarios.length ? t.scenarios[t.scenarios.length-1] : "");
  table.classList.toggle("active", t.mode==="debate" || t.mode==="final");
  // frecce
  while (arrows.firstChild) arrows.removeChild(arrows.firstChild); layerSym.innerHTML = "";
  f.arrows.forEach(ar => {
    const p1 = f.agents[ar.from].pos, p2 = f.agents[ar.to].pos;
    const dx = p2[0]-p1[0], dy = p2[1]-p1[1], L = Math.hypot(dx,dy) || 1, sh = 5;
    const x1 = p1[0]+dx/L*sh, y1 = p1[1]+dy/L*sh-3, x2 = p2[0]-dx/L*sh, y2 = p2[1]-dy/L*sh-3;
    const ln = document.createElementNS("http://www.w3.org/2000/svg","line");
    ln.setAttribute("x1",x1); ln.setAttribute("y1",y1); ln.setAttribute("x2",x2); ln.setAttribute("y2",y2); ln.setAttribute("class","k-"+ar.kind);
    arrows.appendChild(ln);
    const sym = {agree:"✓",disagree:"✗",veto:"⛔",question:"?",warn:"!"}[ar.kind];
    if (sym){ const m = el("div","asym",sym); m.style.left=(x1+x2)/2+"%"; m.style.top=(y1+y2)/2+"%";
      m.style.color = {agree:"var(--ok)",disagree:"var(--warn)",veto:"var(--bad)",question:"#9fd4ff",warn:"var(--warn)"}[ar.kind]; layerSym.appendChild(m); }
  });
  // bolle
  layerBub.innerHTML = "";
  const now = Date.now();
  AG.forEach(a => {
    const b = f.agents[a].bubble; if (!b) return;
    const key = b.event_id; if (!(key in bubbleSeen)) bubbleSeen[key] = now;
    const recent = cur - evIdx(b.event_id) <= 2;
    const fresh = (now - bubbleSeen[key]) < (D.ui.bubble_seconds*1000/speed);
    const pos = f.agents[a].pos, x = clamp(pos[0],15,85), below = pos[1] < 34 || (a==="judge" && f.agents[a].loc==="center");
    if ((recent && fresh) || bubbleOpen[a]){
      const bb = el("div","bubble"+(below?" below":"")); bb.style.setProperty("--c", META[a].color);
      bb.style.left = x+"%"; bb.style.top = (below ? pos[1]+14 : pos[1]-12)+"%";
      const h = el("div","bh"); h.appendChild(el("span",null,META[a].emoji+" "+META[a].name+" · "+b.ts));
      h.appendChild(el("span","ty "+b.type,b.type)); bb.appendChild(h);
      const txt = b.text.length>170 ? b.text.slice(0,167)+"…" : b.text; const bt = el("div","bt",txt); bt.title=b.text; bb.appendChild(bt);
      bb.appendChild(el("div","src2", (b.source==="agent"?"output agente":"evento sistema")+(b.target&&b.target!=="all"&&META[b.target]?" → "+META[b.target].name:"")));
      bb.onclick = () => { bubbleOpen[a] = false; render(cur); }; layerBub.appendChild(bb);
    } else {
      const m = el("div","mini","💬"); m.style.setProperty("--c", META[a].color); m.style.left=x+"%"; m.style.top=(below?pos[1]+12:pos[1]-10)+"%";
      m.title = "Riapri ultimo messaggio ("+META[a].name+")"; m.onclick = () => { bubbleOpen[a] = true; render(cur); }; layerBub.appendChild(m);
    }
  });
  // timeline
  STEPS.forEach((s,k) => { const st = f.steps[s.key]; stepEls[k].className = "step "+st; $(".ic",stepEls[k]).textContent = ICON[st]; });
  // didascalia, clock, slider
  $("#caption").innerHTML = ""; const cb = el("b",null,f.caption); $("#caption").appendChild(cb);
  const ev = TR[cur]; if (ev && ev.message) $("#caption").appendChild(document.createTextNode(" — "+(ev.message.length>140?ev.message.slice(0,137)+"…":ev.message)));
  if (f.final) { const fl = el("div","final","⚖ "+f.final.label); fl.style.color = {positive:"var(--ok)",warning:"var(--bad)",neutral:"var(--dim)"}[f.room_mode] || "var(--warn)"; $("#caption").appendChild(fl); }
  $("#clock").textContent = f.ts.replace("T"," ").slice(0,19);
  $("#scrub").max = F.length-1; $("#scrub").value = cur; $("#counter").textContent = (cur+1)+"/"+F.length;
  renderTranscript(); if (inspected) inspect(inspected, true);
  $("#playbtn").textContent = playing ? "⏸ PAUSA" : (cur>=F.length-1 ? "↺ RIPLAY" : "▶ PLAY");
}

// ---------- transcript
let trShown = 0;
function renderTranscript(){
  const list = $("#trlist"), onlyMsg = $("#onlymsg").checked;
  if (trShown > cur+1 || list.dataset.filter !== String(onlyMsg)) { list.innerHTML = ""; trShown = 0; list.dataset.filter = String(onlyMsg); }
  for (; trShown <= cur; trShown++){
    const e = TR[trShown]; if (onlyMsg && !e.message) continue;
    const d = el("details"), s = el("summary");
    const who = e.agent && META[e.agent] ? META[e.agent].emoji+" "+META[e.agent].name : "SISTEMA";
    const to = e.target && META[e.target] ? " → "+META[e.target].emoji : (e.target==="all" ? " → TUTTI" : "");
    s.textContent = e.ts+"  "+who+to+"  ["+(e.mtype||e.type)+"] "+(e.message ? (e.message.length>70?e.message.slice(0,67)+"…":e.message) : "");
    if (e.agent && META[e.agent]) s.style.color = META[e.agent].color;
    d.appendChild(s);
    const b = el("div","body"); b.appendChild(el("div",null,e.message||"(evento di stato)"));
    if (e.evidence.length){ const w = el("div"); e.evidence.forEach(id => { const x = el("span","eid",id); const ev = D.evidence[id]; x.title = ev ? ev.label+" = "+JSON.stringify(ev.value) : "evidenza non presente nel registro"; w.appendChild(x); }); b.appendChild(w); }
    const meta = [e.id, e.type, e.source==="agent"?"output agente":"evento sistema"];
    if (e.confidence!==null && e.confidence!==undefined) meta.push("confidenza "+e.confidence);
    if (e.status) meta.push("stato "+e.status); if (e.model) meta.push("modello "+e.model);
    if (e.duration_ms!==null && e.duration_ms!==undefined) meta.push(e.duration_ms+" ms");
    b.appendChild(el("div","hint",meta.join(" · "))); d.appendChild(b); list.appendChild(d);
  }
  list.scrollTop = list.scrollHeight;
}

// ---------- inspector
function inspect(a, silent){
  inspected = a; const box = $("#insp"); box.innerHTML = "";
  const m = META[a], det = D.details[a], f = F[cur], live = f.agents[a];
  const h = el("h3",null,m.emoji+" "+m.name); h.style.color = m.color; box.appendChild(h);
  const row = (k,v) => { const r = el("div","row"); r.appendChild(el("span",null,k)); r.appendChild(el("span",null,v===null||v===undefined?"n/d":String(v))); box.appendChild(r); };
  const done = det.finished_idx !== null && det.finished_idx !== undefined && cur >= det.finished_idx;
  row("STATUS", live.state); row("MODEL", (done && det.model) || live.model || det.requested_model);
  row("CONFIDENCE", live.confidence); row("CURRENT TASK", live.activity);
  row("PROCESSING TIME", (done && det.duration_ms!==null) ? (det.duration_ms/1000).toFixed(2)+"s" : "n/d");
  if (done && det.fallback_used) row("FALLBACK", "usato (richiesto "+det.requested_model+")");
  if (done && det.retry_count) row("RETRY", det.retry_count);
  if (live.bias) row("BIAS", live.bias);
  const sec = (title, node) => { const s = el("div","sec"); s.appendChild(el("h4",null,title)); s.appendChild(node); box.appendChild(s); };
  if (live.key_evidence && live.key_evidence.length){ const w = el("div"); live.key_evidence.forEach(id => { const x = el("span","eid",id); const ev = D.evidence[id]; x.title = ev ? ev.label+" = "+JSON.stringify(ev.value) : "n/d"; w.appendChild(x); }); sec("KEY EVIDENCE", w); }
  if (live.last_message) sec("LAST MESSAGE", el("div",null,live.last_message));
  if (done){
    const ul = (arr) => { const u = el("ul"); arr.forEach(x => u.appendChild(el("li",null,typeof x==="string"?x:x))); return u; };
    if (det.summary) sec("SINTESI", el("div",null,det.summary));
    if (det.facts.length) sec("FATTI", ul(det.facts)); if (det.interpretations.length) sec("INTERPRETAZIONI", ul(det.interpretations));
    if (det.scenarios.length) sec("SCENARI", ul(det.scenarios.map(s => s.id+" "+s.direction+" · entry "+s.entry_zone.join("–")+" · inv "+s.invalidation+" · T1 "+s.target_1+" · R:R "+s.expected_rr+" · conf "+s.confidence)));
    if (det.assessments.length) sec("VERDETTI RISCHIO", ul(det.assessments.map(x => x.scenario_id+": "+x.verdict+" (R:R "+x.computed_rr+")")));
    if (det.issues.length) sec("PROBLEMI", ul(det.issues.map(x => "["+x.severity+"] "+x.type+": "+x.description)));
    if (det.score!==null && det.score!==undefined) row("PUNTEGGIO", det.score+"/100");
    if (det.warnings.length) sec("AVVISI", ul(det.warnings)); if (det.errors.length) sec("ERRORI", ul(det.errors));
  } else if (det.status === "SKIPPED" && det.reason) sec("NOTA", el("div",null,det.reason));
  if (!silent) box.scrollIntoView({block:"nearest"});
}

// ---------- playback
function stop(){ playing = false; clearTimeout(timer); }
function tick(){ if (!playing) return; if (cur >= F.length-1){ stop(); render(cur); return; }
  render(cur+1); timer = setTimeout(tick, D.ui.event_seconds*1000/speed); }
function play(){ if (cur >= F.length-1) cur = 0; playing = true; render(cur); clearTimeout(timer); timer = setTimeout(tick, D.ui.event_seconds*1000/speed); }
function go(i){ stop(); render(i); }
$("#playbtn").onclick = () => playing ? (stop(), render(cur)) : play();
$("#next").onclick = () => { stop(); render(cur+1); };
$("#prev").onclick = () => { stop(); render(cur-1); };
$("#first").onclick = () => { stop(); bubbleOpen = {}; render(0); };
$("#last").onclick = () => { stop(); render(F.length-1); };
$("#scrub").oninput = e => { stop(); render(parseInt(e.target.value,10)); };
$("#onlymsg").onchange = () => { trShown = 0; renderTranscript(); };
const sel = $("#speed"); D.ui.speeds.forEach(s => { const o = el("option",null,s+"x"); o.value = s; if (s===speed) o.selected = true; sel.appendChild(o); });
sel.onchange = e => { speed = parseFloat(e.target.value); render(cur); if (playing){ clearTimeout(timer); timer = setTimeout(tick, D.ui.event_seconds*1000/speed); } };
document.addEventListener("keydown", e => { if (e.target.tagName==="INPUT"||e.target.tagName==="SELECT") return;
  if (e.key===" "){ e.preventDefault(); $("#playbtn").click(); } else if (e.key==="ArrowRight") $("#next").click(); else if (e.key==="ArrowLeft") $("#prev").click(); });
render(0); inspect("price_action", true);
if (D.autoplay) play();
})();
