"""The markets the lab can test: Nasdaq-100, S&P 500 and gold futures.

Every market has a micro contract (what the strategies are backtested and sized in) and a mini (= 10 micros).
The Massive day files contain every CME ticker, so one download feeds all markets: build one continuous file per
root with scripts/build_dataset.py (data/NQ_1min.csv.gz, data/ES_1min.csv.gz, data/GC_1min.csv.gz) and the Lab
picks up every file it finds next to the main one."""
from __future__ import annotations

from pathlib import Path

from .backtest import ES, GC, MES, MGC, MNQ, NQ

MARKETS = {
    "NQ": {"name": "Nasdaq-100", "micro": MNQ, "mini": NQ},
    "ES": {"name": "S&P 500", "micro": MES, "mini": ES},
    "GC": {"name": "Gold", "micro": MGC, "mini": GC},
}


def discover(data_dir: str | Path) -> dict[str, str]:
    """{root: path} for every <ROOT>_1min.csv.gz found in data_dir."""
    d = Path(data_dir)
    return {r: str(d / f"{r}_1min.csv.gz") for r in MARKETS if (d / f"{r}_1min.csv.gz").exists()}


def root_of(symbol: str) -> str:
    """'MNQ' -> 'NQ', 'MES' -> 'ES', 'MGC' -> 'GC', 'NQ' -> 'NQ'."""
    for r, m in MARKETS.items():
        if symbol in (m["micro"].symbol, m["mini"].symbol, r):
            return r
    return symbol


def build_market(data_dir: str | Path, root: str, out: str | Path | None = None, min_lead_days: int = 3) -> dict:
    """Build <data_dir>/<ROOT>_1min.csv.gz from the Massive day files already in data_dir (they hold every CME ticker).
    The micro contract (MES, MGC) has the same price as its mini (ES, GC): the mini is the more liquid series, so it
    is the one used, and the lab applies the micro's tick value. Raises ValueError with a readable message."""
    import re

    from .data import validate
    from .futures import build_root
    d = Path(data_dir)
    paths = [str(p) for p in d.rglob("*.csv.gz") if re.search(r"\d{4}-\d{2}-\d{2}\.csv\.gz$", p.name)]
    if not paths:
        raise ValueError(f"no day files (YYYY-MM-DD.csv.gz) found in {d}: download them first (scripts/massive_cli.py)")
    try:
        df = build_root(paths, root, min_lead_days=min_lead_days)
    except ValueError as e:
        raise ValueError(f"{root}: {e}. If the day files do not contain {root}, your Massive plan does not include it.") from e
    validate(df[["open", "high", "low", "close", "volume"]])
    out = Path(out) if out else d / f"{root}_1min.csv.gz"
    tmp = Path(str(out) + ".tmp")
    df.to_csv(tmp, compression="gzip")
    tmp.replace(out)
    return {"root": root, "path": str(out), "day_files": len(paths), "rows": int(len(df)),
            "first": str(df.index[0])[:10], "last": str(df.index[-1])[:10],
            "chain": list(df.attrs.get("chain") or [])}
