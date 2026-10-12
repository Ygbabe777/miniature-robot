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
