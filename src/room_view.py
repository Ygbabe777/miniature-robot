"""Genera il componente HTML autonomo della sala operativa pixel-art."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .security import redact_obj
from .ui_state import build_view

_ASSETS = Path(__file__).parent / "assets"

_TEMPLATE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OFO Council Room</title><style>__CSS__</style></head><body><div class="wrap">
<div class="top"><div class="brand">OFO COUNCIL<small>NQ INTELLIGENCE DESK · SOLO ANALISI</small></div>
<div class="badges" id="badges"></div>
<div style="text-align:right"><div class="clock" id="clock">--</div><div class="legend" id="clock-label"></div></div></div>
<div class="ticker" id="ticker"></div>
<div class="main"><div><div id="room" aria-label="Sala del Consiglio OFO"></div>
<div class="cap" id="caption"></div>
<div class="controls"><button id="first" title="Inizio">⏮</button><button id="prev" title="Evento precedente">◀</button>
<button id="playbtn" class="primary">▶ PLAY</button><button id="next" title="Prossimo evento (STEP)">NEXT EVENT ▶</button>
<button id="last" title="Fine">⏭</button><input type="range" id="scrub" min="0" max="0" value="0"><span id="counter">0/0</span>
<label>velocità <select id="speed"></select></label></div>
<div class="hint">Le animazioni riproducono SOLO eventi reali persistiti nel transcript. La velocità cambia soltanto la riproduzione, non l'analisi. Spazio = play/pausa · ←/→ = passo.</div></div>
<div class="side"><div class="box"><h3>TIMELINE PIPELINE</h3><div id="timeline"></div></div>
<div class="box" id="insp"></div></div></div>
<details class="trbox" open><summary>LIVE TRANSCRIPT</summary><div style="padding:4px 8px;border:2px solid var(--line);border-top:0;background:var(--panel)">
<label><input type="checkbox" id="onlymsg"> solo messaggi</label> <span class="legend">· nessuna chiave API, header di autorizzazione o prompt di sistema viene mostrato</span></div>
<div id="trlist"></div></details></div>
<script type="application/json" id="data">__DATA__</script><script>__JS__</script></body></html>"""


def build_room_html(report: dict[str, Any], ui_cfg: Any = None, autoplay: bool = True) -> str:
    view = redact_obj(build_view(report, ui_cfg))
    view["autoplay"] = autoplay
    data = json.dumps(view, ensure_ascii=False).replace("</", "<\\/")
    return (_TEMPLATE.replace("__CSS__", (_ASSETS / "room.css").read_text(encoding="utf-8"))
            .replace("__JS__", (_ASSETS / "room.js").read_text(encoding="utf-8"))
            .replace("__DATA__", data))
