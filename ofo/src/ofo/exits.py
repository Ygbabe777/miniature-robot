"""Risk/reward layer: a stop-loss and a profit target, measured in ATR, put on top of ANY strategy.

The strategy still decides when to enter (at the close of a bar, like everywhere else). From that
entry the stop sits `stop_atr` x ATR away and the target `rr` x the stop distance away (rr = reward:risk).
Rules, chosen to never flatter the result:
  * both touched inside one bar -> the stop is assumed to have hit first;
  * a bar that OPENS beyond the stop fills at the open (gap), not at the stop;
  * after an exit the strategy stays flat until its own signal changes (no instant re-entry).
`rr=None` means no target (the strategy's own exit applies); the stop is always present."""
from __future__ import annotations

import numpy as np
import pandas as pd

ATR_N = 14
STOPS = (1.0, 1.5, 2.0, 3.0)                 # in ATR
RRS = (1.0, 1.5, 2.0, 3.0, None)             # reward : risk, None = the strategy's own exit


def atr(bars: pd.DataFrame, n: int = ATR_N) -> np.ndarray:
    h, l, c = bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()


def apply_exits(bars: pd.DataFrame, pos, stop_atr: float, rr: float | None, atr_n: int = ATR_N):
    """Returns (new position series, exit-price series (NaN except on exit bars), info)."""
    p = np.asarray(pos, dtype=float).copy()
    n = len(p)
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(bars, atr_n)
    out, xpx = p.copy(), np.full(n, np.nan)
    pts, stops, kinds = [], [], []                       # per trade: points won, stop distance, how it ended
    i = 0
    while i < n:
        if p[i] != 0 and (i == 0 or p[i - 1] != p[i]):
            side = p[i]
            j = i
            while j + 1 < n and p[j + 1] == side:
                j += 1
            last = min(j + 1, n - 1)                       # the strategy's own exit fills at the close of bar j+1
            risk = stop_atr * a[i]
            if i + 1 > last:                               # entry on the very last bar: nothing to manage
                i = j + 1
                continue
            if not np.isfinite(risk) or risk <= 0:
                pts.append(side * (c[last] - c[i])); stops.append(np.nan); kinds.append("signal")
                i = j + 1
                continue
            stop = c[i] - side * risk
            tgt = c[i] + side * rr * risk if rr is not None else None
            ks = np.arange(i + 1, last + 1)
            if side > 0:
                hit_stop = l[ks] <= stop
                hit_tgt = h[ks] >= tgt if tgt is not None else np.zeros(len(ks), bool)
            else:
                hit_stop = h[ks] >= stop
                hit_tgt = l[ks] <= tgt if tgt is not None else np.zeros(len(ks), bool)
            hit = np.flatnonzero(hit_stop | hit_tgt)
            if len(hit) == 0:
                pts.append(side * (c[last] - c[i])); stops.append(risk); kinds.append("signal")
            else:
                k = ks[hit[0]]
                if hit_stop[hit[0]]:                       # stop wins ties; gaps fill at the open
                    px = min(stop, o[k]) if side > 0 else max(stop, o[k])
                    kinds.append("stop")
                else:
                    px = tgt
                    kinds.append("target")
                xpx[k] = px
                if k <= j:
                    out[k:j + 1] = 0.0                     # flat for the rest of the strategy's own trade
                pts.append(side * (px - c[i])); stops.append(risk)
            i = j + 1
        else:
            i += 1
    info = {"n_trades": len(pts), "points": np.array(pts), "stop_points": np.array(stops),
            "kinds": kinds}
    return pd.Series(out, index=bars.index), pd.Series(xpx, index=bars.index), info


def exit_grid(stops=STOPS, rrs=RRS) -> list[dict]:
    return [{"stop_atr": s, "rr": r} for s in stops for r in rrs]


def summarize_info(info: dict) -> dict:
    pts = info["points"]
    st = info["stop_points"]
    st = st[np.isfinite(st)] if len(st) else st
    return {"n_trades": int(info["n_trades"]),
            "win_rate": float((pts > 0).mean()) if len(pts) else None,
            "stop_rate": float(np.mean([k == "stop" for k in info["kinds"]])) if info["kinds"] else None,
            "target_rate": float(np.mean([k == "target" for k in info["kinds"]])) if info["kinds"] else None,
            "median_stop_points": float(np.median(st)) if len(st) else None}
