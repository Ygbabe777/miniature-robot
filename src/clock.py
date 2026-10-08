"""Orologio iniettabile: rende deterministici timestamp e durate nei test."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc).astimezone()

    def monotonic(self) -> float:
        return time.perf_counter()


class FakeClock:
    """Avanza di `step` secondi a ogni chiamata: output riproducibile."""

    def __init__(self, start: datetime | None = None, step: float = 1.0) -> None:
        self._t = start or datetime(2026, 10, 8, 13, 0, 0, tzinfo=timezone.utc)
        self._step = step
        self._mono = 0.0

    def now(self) -> datetime:
        self._t = self._t + timedelta(seconds=self._step)
        return self._t

    def monotonic(self) -> float:
        self._mono += self._step
        return self._mono
