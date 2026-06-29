"""Unit tests for the incremental indicator stack."""

from __future__ import annotations

from datetime import timedelta

from orb_bot.indicators import EMA, RSI, ATR, SessionVWAP, RelativeVolume
from orb_bot.models import Bar

from helpers import et


def test_ema_seeds_and_smooths():
    ema = EMA(period=2)  # k = 2/3
    assert ema.update(1.0) == 1.0  # seeds on first value
    assert abs(ema.update(2.0) - (2 * 2 / 3 + 1 * 1 / 3)) < 1e-9
    assert abs(ema.update(3.0) - (3 * 2 / 3 + 1.6666667 * 1 / 3)) < 1e-6


def test_rsi_extremes():
    up = RSI(period=3)
    val = None
    for c in [1, 2, 3, 4, 5, 6]:
        val = up.update(float(c))
    assert val == 100.0  # only gains -> RSI 100

    down = RSI(period=3)
    for c in [6, 5, 4, 3, 2, 1]:
        val = down.update(float(c))
    assert val == 0.0  # only losses -> RSI 0


def test_rsi_warmup_returns_none():
    rsi = RSI(period=14)
    assert rsi.update(100.0) is None  # first close, no change yet


def test_atr_constant_range():
    atr = ATR(period=3)
    base = et(2024, 3, 4, 9, 30)
    last = None
    for i in range(5):
        # Range of exactly 2.0 each bar, close flat -> ATR converges to 2.0.
        b = Bar(base + timedelta(minutes=i), 10.0, 11.0, 9.0, 10.0, 100)
        last = atr.update(b)
    assert abs(last - 2.0) < 1e-9


def test_session_vwap():
    vwap = SessionVWAP()
    base = et(2024, 3, 4, 9, 30)
    vwap.update(Bar(base, 10, 11, 9, 10, 100))  # typical=10
    v = vwap.update(Bar(base + timedelta(minutes=1), 20, 21, 19, 20, 100))  # typical=20
    assert abs(v - 15.0) < 1e-9  # (10*100 + 20*100) / 200
    vwap.reset()
    assert vwap.value is None


def test_relative_volume_across_days():
    rvol = RelativeVolume(lookback_days=5)
    d1 = Bar(et(2024, 3, 4, 9, 30), 10, 11, 9, 10, 100)
    d2 = Bar(et(2024, 3, 5, 9, 30), 10, 11, 9, 10, 200)
    assert rvol.update(d1) is None  # no prior history for 09:30
    assert rvol.update(d2) == 2.0  # 200 vs prior average of 100
