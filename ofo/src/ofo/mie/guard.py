"""The only door through which the MIE sees market data.

`DevView` is built from the bars and the frozen holdout epoch. It exposes DEVELOPMENT sessions (strictly before the epoch start) and, separately,
FORWARD sessions (strictly after the epoch end). The epoch itself, the hidden block, is not reachable: there is no accessor, and asking for a range
that touches it raises `LockedAccessError`. Forward sessions can verify predictions that were registered earlier; they never feed hypothesis generation."""
from __future__ import annotations

import pandas as pd

from .schema import fingerprint
from .sessions import session_labels


class LockedAccessError(RuntimeError):
    pass


class DevView:
    def __init__(self, bars: pd.DataFrame, epoch_start: str, epoch_end: str, market: str = "NQ"):
        lab = session_labels(bars)
        day = lab.dt.strftime("%Y-%m-%d").to_numpy()
        self.market, self.epoch = market, (epoch_start[:10], epoch_end[:10])
        self._dev = bars[day < epoch_start[:10]].copy()
        self._fwd = bars[day > epoch_end[:10]].copy()
        self._dev["session"] = lab[day < epoch_start[:10]].values
        self._fwd["session"] = lab[day > epoch_end[:10]].values
        self.data_version = fingerprint({"market": market, "rows": len(self._dev), "first": str(self._dev.index.min()), "last": str(self._dev.index.max()),
                                         "close_sum": round(float(self._dev["close"].sum()), 4) if len(self._dev) else 0})

    @property
    def development(self) -> pd.DataFrame:
        return self._dev

    @property
    def forward(self) -> pd.DataFrame:
        return self._fwd

    def window(self, start: str, end: str) -> pd.DataFrame:
        """A development window. Any request overlapping the hidden epoch is refused, not clipped."""
        s, e = str(start)[:10], str(end)[:10]
        if not (e < self.epoch[0] or s > self.epoch[1]):
            raise LockedAccessError(f"{s}..{e} touches the hidden block {self.epoch[0]}..{self.epoch[1]}")
        src = self._dev if e < self.epoch[0] else self._fwd
        lab = pd.to_datetime(src["session"]).dt.strftime("%Y-%m-%d")
        return src[(lab >= s).to_numpy() & (lab <= e).to_numpy()]

    def forward_version(self) -> str:
        return fingerprint({"market": self.market, "rows": len(self._fwd), "last": str(self._fwd.index.max()) if len(self._fwd) else None})
