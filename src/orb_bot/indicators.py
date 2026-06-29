"""Incremental intraday indicators.

Every indicator updates one bar at a time and exposes a ``.value``, so the same
code path serves both the vectorised backtest and the live bar stream. Session-
scoped indicators (VWAP, relative volume) reset at the start of each trading day.

Warmup: RSI/ATR return ``None`` until they have enough bars; callers treat a
``None`` as "not confirmed" when a filter requires that indicator.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .config import EXCHANGE_TZ
from .models import Bar


class EMA:
    """Exponential moving average."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._k = 2.0 / (period + 1)
        self.value: float | None = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else x * self._k + self.value * (1 - self._k)
        return self.value


class RSI:
    """Wilder's Relative Strength Index."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        change = close - self._prev
        self._prev = close
        gain, loss = max(change, 0.0), max(-change, 0.0)

        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < self.period:
                return None
            self._avg_gain = sum(self._gains) / self.period
            self._avg_loss = sum(self._losses) / self.period
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period

        if self._avg_loss == 0:
            self.value = 100.0
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = 100.0 - 100.0 / (1.0 + rs)
        return self.value


class ATR:
    """Wilder's Average True Range."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self.value: float | None = None

    def update(self, bar: Bar) -> float | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close

        if self.value is None:
            self._trs.append(tr)
            if len(self._trs) < self.period:
                return None
            self.value = sum(self._trs) / self.period
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


class SessionVWAP:
    """Volume-weighted average price, reset each session."""

    def __init__(self) -> None:
        self._cum_pv = 0.0
        self._cum_v = 0.0
        self.value: float | None = None

    def reset(self) -> None:
        self._cum_pv = 0.0
        self._cum_v = 0.0
        self.value = None

    def update(self, bar: Bar) -> float | None:
        typical = (bar.high + bar.low + bar.close) / 3.0
        self._cum_pv += typical * bar.volume
        self._cum_v += bar.volume
        # Fall back to price when a feed reports zero volume.
        self.value = (self._cum_pv / self._cum_v) if self._cum_v > 0 else bar.close
        return self.value


class RelativeVolume:
    """Current bar volume vs. the average for the same minute-of-day.

    Keyed by ET minute-of-day over a rolling window of prior sessions, so it
    answers "is this 09:31 bar unusually heavy versus a typical 09:31?".
    """

    def __init__(self, lookback_days: int = 14) -> None:
        self.lookback = lookback_days
        self._hist: dict[tuple[int, int], deque] = {}
        self.value: float | None = None

    def update(self, bar: Bar) -> float | None:
        et = bar.timestamp.astimezone(EXCHANGE_TZ)
        key = (et.hour, et.minute)
        dq = self._hist.get(key)
        if dq:
            avg = sum(dq) / len(dq)
            self.value = (bar.volume / avg) if avg > 0 else None
        else:
            self.value = None
        if dq is None:
            dq = deque(maxlen=self.lookback)
            self._hist[key] = dq
        dq.append(bar.volume)
        return self.value


@dataclass
class IndicatorSnapshot:
    """Indicator values as of the current bar (any may be None during warmup)."""

    vwap: float | None
    rsi: float | None
    ema: float | None
    atr: float | None
    rvol: float | None


class Indicators:
    """Bundles the indicator stack and updates them together per bar."""

    def __init__(
        self,
        rsi_period: int = 14,
        ema_period: int = 20,
        atr_period: int = 14,
        rvol_lookback_days: int = 14,
    ) -> None:
        self.ema = EMA(ema_period)
        self.rsi = RSI(rsi_period)
        self.atr = ATR(atr_period)
        self.vwap = SessionVWAP()
        self.rvol = RelativeVolume(rvol_lookback_days)

    def reset_session(self) -> None:
        self.vwap.reset()

    def update(self, bar: Bar) -> IndicatorSnapshot:
        return IndicatorSnapshot(
            vwap=self.vwap.update(bar),
            rsi=self.rsi.update(bar.close),
            ema=self.ema.update(bar.close),
            atr=self.atr.update(bar),
            rvol=self.rvol.update(bar),
        )
