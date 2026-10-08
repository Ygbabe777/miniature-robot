"""Generatore di dati SINTETICI di esempio (NON dati di mercato reali).

Serve per demo e test deterministici. I file prodotti sono marcati
`source: SYNTHETIC_SAMPLE` e il report mostra un avviso esplicito.
Uso:  python tools/sample_data.py --date 2026-10-08 --out-dir data
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def _weekdays_before(d: date, n: int) -> list[date]:
    out, cur = [], d
    while len(out) < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            out.append(cur)
    return sorted(out)


def _session_times(day: date) -> list[datetime]:
    """Barre 5m Globex: (giorno lavorativo precedente) 18:00 -> day 17:00 (escluso 17:00)."""
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5 and day.weekday() != 0:
        prev -= timedelta(days=1)
    if day.weekday() == 0:
        prev = day - timedelta(days=1)  # domenica sera
    start = datetime.combine(prev, datetime.min.time()).replace(hour=18)
    end = datetime.combine(day, datetime.min.time()).replace(hour=17)
    n = int((end - start).total_seconds() // 300)
    return [start + timedelta(minutes=5 * i) for i in range(n)]


def generate(run_date: str, seed: int = 2026, n_days: int = 8, start_price: float = 28900.0,
             drift_bias: float = 0.0) -> tuple[pd.DataFrame, dict]:
    """Ritorna (ohlcv, levels) sintetici per `run_date`, solo barre fino alle 09:25."""
    rng = np.random.default_rng(seed)
    d = datetime.strptime(run_date, "%Y-%m-%d").date()
    days = _weekdays_before(d, n_days)
    times: list[datetime] = []
    for day in days:
        times.extend(_session_times(day))
    on_start = datetime.combine(days[-1], datetime.min.time()).replace(hour=18)
    cutoff = datetime.combine(d, datetime.min.time()).replace(hour=9, minute=30)
    times.extend(t for t in (on_start + timedelta(minutes=5 * i) for i in range(int((cutoff - on_start).total_seconds() // 300)))
                 if t < cutoff)
    times = sorted(set(times))

    rows, price = [], start_price
    day_drift = {}
    for t in times:
        key = (t - timedelta(hours=18)).date() if t.hour >= 18 else t.date()
        day_drift.setdefault(key, float(rng.normal(drift_bias, 0.35)))
        hr = t.hour + t.minute / 60
        rth = 9.5 <= hr < 16
        sd = 11.0 if rth else 5.5
        if 9.5 <= hr < 10.5 or 15 <= hr < 16:
            sd *= 1.4
        drift = day_drift[key] * (1.0 if rth else 0.35)
        o = price
        c = o + float(rng.normal(drift, sd))
        hi = max(o, c) + abs(float(rng.normal(0, sd * 0.5)))
        lo = min(o, c) - abs(float(rng.normal(0, sd * 0.5)))
        u = 1.0 + 0.9 * abs(math.cos(math.pi * (hr - 9.5) / 6.5)) if rth else 0.25
        vol = int(max(50, rng.lognormal(math.log(900 * u), 0.35)))
        q = lambda x: round(x * 4) / 4  # noqa: E731 - tick 0.25
        o, hi, lo, c = q(o), q(hi), q(lo), q(c)
        hi, lo = max(hi, o, c), min(lo, o, c)
        rows.append((t.strftime("%Y-%m-%d %H:%M:%S"), o, hi, lo, c, vol))
        price = c
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])

    ts = pd.to_datetime(df["timestamp"])
    rth = (ts.dt.time >= datetime.strptime("09:30", "%H:%M").time()) & (ts.dt.time < datetime.strptime("16:00", "%H:%M").time())
    prev_day = days[-1]
    prev = df[rth & (ts.dt.date == prev_day)]
    on = df[ts >= datetime.combine(prev_day, datetime.min.time()).replace(hour=16)]
    last_week_days = [x for x in days if x.isocalendar()[1] == (d - timedelta(days=7)).isocalendar()[1]]
    wk = df[rth & ts.dt.date.isin(last_week_days)] if last_week_days else prev
    spot = float(df["close"].iloc[-1])
    levels = {
        "date": run_date,
        "instrument": "NQ",
        "spot": spot,
        "source": "SYNTHETIC_SAMPLE",
        "options": {
            "gamma_flip": float(math.floor((spot - 60) / 10) * 10),
            "call_wall": float(math.ceil((spot + 250) / 50) * 50),
            "put_wall": float(math.floor((spot - 150) / 50) * 50),
            "notes": "Valori SINTETICI di esempio, non derivati da dati opzioni reali.",
        },
        "levels": {
            "previous_session_high": float(prev["high"].max()),
            "previous_session_low": float(prev["low"].min()),
            "overnight_high": float(on["high"].max()),
            "overnight_low": float(on["low"].min()),
            "previous_week_high": float(wk["high"].max()),
            "previous_week_low": float(wk["low"].min()),
        },
    }
    return df, levels


def build_es(df: pd.DataFrame, run_date: str, seed: int = 2026, es_scale: float = 4.45,
             decorrelate: bool = False) -> tuple[pd.DataFrame, dict]:
    """ES SINTETICO correlato a NQ (rendimenti NQ * 0.85 + rumore). `decorrelate` produce ES indipendente."""
    rng = np.random.default_rng(seed + 1)
    nq_ret = np.log(df["close"] / df["open"]).to_numpy()
    o_a, h_a, l_a, c_a = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    t_a, v_a = df["timestamp"].to_numpy(), df["volume"].to_numpy()
    price = float(o_a[0]) / es_scale
    rows = []
    q = lambda x: round(x * 4) / 4  # noqa: E731
    for i, r in enumerate(nq_ret):
        o = price
        noise = float(rng.normal(0, 0.00015))
        ret = noise * 3 if decorrelate else 0.85 * r + noise
        c = o * math.exp(ret)
        up = (h_a[i] - max(o_a[i], c_a[i])) / c_a[i]
        dn = (min(o_a[i], c_a[i]) - l_a[i]) / c_a[i]
        hi, lo = max(o, c) * (1 + 0.85 * up), min(o, c) * (1 - 0.85 * dn)
        o, hi, lo, c = q(o), q(hi), q(lo), q(c)
        hi, lo = max(hi, o, c), min(lo, o, c)
        rows.append((t_a[i], o, hi, lo, c, int(v_a[i] * 3)))
        price = c
    es = pd.DataFrame(rows, columns=df.columns)
    ts = pd.to_datetime(es["timestamp"])
    rth = (ts.dt.time >= datetime.strptime("09:30", "%H:%M").time()) & (ts.dt.time < datetime.strptime("16:00", "%H:%M").time())
    d = datetime.strptime(run_date, "%Y-%m-%d").date()
    prev_day = sorted(set(ts[rth].dt.date))[-1]
    prev = es[rth & (ts.dt.date == prev_day)]
    on = es[ts >= datetime.combine(prev_day, datetime.min.time()).replace(hour=16)]
    spot = float(es["close"].iloc[-1])
    block = {
        "spot": spot,
        "options": {"gamma_flip": float(math.floor((spot - 13) / 5) * 5), "call_wall": float(math.ceil((spot + 56) / 5) * 5),
                    "put_wall": float(math.floor((spot - 34) / 5) * 5),
                    "notes": "Valori ES SINTETICI di esempio."},
        "levels": {"previous_session_high": float(prev["high"].max()), "previous_session_low": float(prev["low"].min()),
                   "overnight_high": float(on["high"].max()), "overnight_low": float(on["low"].min())},
    }
    return es, block


def write(out_dir: Path, run_date: str, seed: int = 2026, with_options: bool = True, with_es: bool = True,
          decorrelate_es: bool = False) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df, levels = generate(run_date, seed)
    if with_es:
        es, block = build_es(df, run_date, seed, decorrelate=decorrelate_es)
        es.to_csv(out_dir / "ohlcv_es.csv", index=False)
        levels["es"] = block
    if not with_options:
        levels["options"] = {"gamma_flip": 0, "call_wall": 0, "put_wall": 0, "notes": "dati opzioni non disponibili"}
        if "es" in levels:
            levels["es"]["options"] = {"gamma_flip": 0, "call_wall": 0, "put_wall": 0, "notes": ""}
    df.to_csv(out_dir / "ohlcv.csv", index=False)
    (out_dir / "levels.json").write_text(json.dumps(levels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Genera dati SINTETICI di esempio")
    ap.add_argument("--date", default="2026-10-08")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    write(Path(a.out_dir), a.date, a.seed)
    print(f"Dati sintetici scritti in {a.out_dir}/ (SYNTHETIC_SAMPLE)")
