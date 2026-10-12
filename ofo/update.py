"""Usage (from the ofo folder):  python update.py
Downloads the latest OFO code from GitHub and copies it over this folder.
It NEVER touches your own files: data/, reports/, ideas/, config/llm.json, .env."""
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/ygbabe777/miniature-robot/archive/refs/heads/claude/ofo-trading-strategy-platform-o5efpn.zip"
KEEP = {"data", "reports", "ideas", ".env"}          # top-level names that are yours
KEEP_FILES = {"config/llm.json"}
here = Path(__file__).resolve().parent

if not (here / "pyproject.toml").exists():
    sys.exit("Run this from the ofo folder (the one with pyproject.toml).")
print("downloading latest code ...")
zf = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(URL, timeout=120).read()))
marker = next((n for n in zf.namelist() if n.endswith("/ofo/pyproject.toml")), None)
if marker is None:
    sys.exit("Unexpected download: no ofo/pyproject.toml inside.")
prefix = marker[: -len("pyproject.toml")]
changed = 0
for name in zf.namelist():
    if not name.startswith(prefix) or name.endswith("/"):
        continue
    rel = name[len(prefix):]
    if rel.split("/")[0] in KEEP or rel in KEEP_FILES or "__pycache__" in rel or ".egg-info" in rel:
        continue
    dest = here / rel
    data = zf.read(name)
    if dest.exists() and dest.read_bytes() == data:
        continue
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    changed += 1
    print("  updated", rel)
print(f"done: {changed} files updated.")
if changed:
    print("If you see a message about dependencies, also run:  pip install -e \".[dev,data]\"")
