"""Test every web channel from THIS computer and print the exact result or error. Run:  python scripts/check_sources.py
Share the output (it contains no keys) if a channel shows an error."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ofo.sources import ALLOWED_BIN, build_channels, config_problem, load_config, missing_channels, run_cli  # noqa: E402

CFG = Path(__file__).resolve().parent.parent / "config" / "sources.json"
prob = config_problem(CFG)
print("config:", prob or "OK")
cfg = load_config(CFG)
print("channels switched on:", [k for k, v in cfg["channels"].items() if v])
for b in sorted(ALLOWED_BIN):
    print(f"  {b:12s}", shutil.which(b) or "not found")
for m in missing_channels(CFG):
    print("MISSING:", m["name"], "->", m["hint"])
try:
    print("yt-dlp version:", run_cli(["yt-dlp", "--version"]).strip())
except Exception as e:
    print("yt-dlp:", e)
for ch in build_channels(CFG):
    try:
        res = ch.search("NQ futures opening range")
        print(f"[{ch.name}] OK, {len(res)} results", (" e.g. " + res[0]["title"][:70]) if res else "")
    except Exception as e:
        print(f"[{ch.name}] ERROR: {type(e).__name__}: {e}")
