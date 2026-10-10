"""Usage:
  python scripts/massive_cli.py ls [PREFIX]
  python scripts/massive_cli.py get PREFIX YYYY-MM-DD YYYY-MM-DD OUT_DIR
"""
import datetime as dt
import sys

from ofo import massive

cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "ls":
    print("\n".join(massive.list_prefix(args[0] if args else "")))
elif cmd == "get":
    prefix, a, b, out = args
    files = massive.download_range(prefix, dt.date.fromisoformat(a), dt.date.fromisoformat(b), out)
    print(f"{len(files)} files in {out}")
else:
    sys.exit(__doc__)
