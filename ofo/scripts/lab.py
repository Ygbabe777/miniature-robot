"""Usage: python scripts/lab.py DATA.csv.gz [50k|25k] [MNQ|NQ] [contracts] [--port 8765]
Starts the Lab (3D laboratory, chat, charts) at http://127.0.0.1:8765 and opens the browser.
The AI is optional: without OFO_LLM_BASE_URL you can still browse reports and charts."""
import sys
import webbrowser
from pathlib import Path

from ofo.app.lab import Lab
from ofo.app.server import make_server
from ofo.backtest import MNQ, NQ
from ofo.llm.client import LLMClient, LLMError
from ofo.llm.pool import build_pool
from ofo.prop import PRESETS

argv = sys.argv[1:]
port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8765
args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--port")]
llm = pool = None
try:
    if Path("config/llm.json").exists():
        pool, llm = build_pool("config/llm.json")
        pool.call_wait_max = 0          # interactive: fail fast instead of waiting for a free call slot
    else:
        llm = LLMClient()
except LLMError as e:
    print("AI non configurata (", e, ") -> il Lab parte senza chat AI")
lab = Lab(args[0], rules=PRESETS[args[1] if len(args) > 1 else "50k"],
          contract={"MNQ": MNQ, "NQ": NQ}[args[2] if len(args) > 2 else "MNQ"],
          contracts=int(args[3]) if len(args) > 3 else 2, llm=llm, pool=pool)
lab.start_background()                    # the Scout's schedule (only acts if you switch auto mode on)
srv = make_server(lab, port=port)
url = f"http://127.0.0.1:{port}"
print(f"Lab pronto su {url}  (Ctrl+C per chiudere)")
webbrowser.open(url)
try:
    srv.serve_forever()
except KeyboardInterrupt:
    print("\nchiuso")
