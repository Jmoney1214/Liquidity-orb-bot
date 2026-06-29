"""Tests for entry modes (breakout vs liquidity-sweep fade) and confirmations."""

from __future__ import annotations

from datetime import date

import pytest

from orb_bot.broker.paper import PaperBroker
from orb_bot.indicators import IndicatorSnapshot
from orb_bot.models import Bar, Side
from orb_bot.strategy import ORBStrategy

from helpers import default_config, et, flat_bar, opening_range_bars


def _run(config, bars):
    broker = PaperBroker(config.contract, slippage_ticks=0.0)
    strat = ORBStrategy(config, broker)
    for b in bars:
        strat.on_bar(b)
    return broker, strat


# -- liquidity sweep fade ----------------------------------------------

def test_sweep_of_highs_goes_short():
    cfg = default_config(entry_mode="liquidity_sweep_fade")
    bars = opening_range_bars(date(2024, 3, 4), high=5010, low=4990)
    # 10:00 bar wicks above 5010 (grabs liquidity) but closes back inside -> SHORT.
    bars.append(Bar(et(2024, 3, 4, 10, 0), 5009, 5013, 5007, 5008, 100))
    # Next bar falls to the fade target.
    bars.append(Bar(et(2024, 3, 4, 10, 1), 5008, 5009, 5000, 5001, 100))

    broker, _ = _run(cfg, bars)
    assert len(broker.closed_trades) == 1
    t = broker.closed_trades[0]
    assert t.side is Side.SHORT
    assert t.reason == "target"


def test_sweep_of_lows_goes_long():
    cfg = default_config(entry_mode="liquidity_sweep_fade")
    bars = opening_range_bars(date(2024, 3, 4), high=5010, low=4990)
    # Wick below 4990 but close back inside -> LONG.
    bars.append(Bar(et(2024, 3, 4, 10, 0), 4991, 4993, 4987, 4992, 100))
    bars.append(Bar(et(2024, 3, 4, 10, 1), 4992, 5000, 4991, 4999, 100))

    broker, _ = _run(cfg, bars)
    assert len(broker.closed_trades) == 1
    assert broker.closed_trades[0].side is Side.LONG


def test_clean_breakout_does_not_trigger_sweep_mode():
    # A bar that closes well beyond the range is a breakout, not a failed sweep.
    cfg = default_config(entry_mode="liquidity_sweep_fade")
    bars = opening_range_bars(date(2024, 3, 4), high=5010, low=4990)
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 5020))  # closes outside -> no fade
    bars.append(flat_bar(et(2024, 3, 4, 15, 55), 5020))
    broker, _ = _run(cfg, bars)
    assert broker.closed_trades == []


def test_invalid_entry_mode_rejected():
    cfg = default_config(entry_mode="nonsense")
    with pytest.raises(ValueError):
        ORBStrategy(cfg, PaperBroker(cfg.contract))


# -- confirmation filters (unit-tested directly) -----------------------

def _strategy(**overrides):
    cfg = default_config(**overrides)
    return ORBStrategy(cfg, PaperBroker(cfg.contract))


def _bar(close):
    return flat_bar(et(2024, 3, 4, 10, 0), close)


def test_rvol_filter_blocks_when_thin():
    s = _strategy(min_relative_volume=2.0)
    s.snapshot = IndicatorSnapshot(vwap=100, rsi=60, ema=100, atr=1, rvol=1.0)
    ok, reason = s._confirms(Side.LONG, "breakout", _bar(101))
    assert not ok and reason == "rvol"

    s.snapshot = IndicatorSnapshot(vwap=100, rsi=60, ema=100, atr=1, rvol=2.5)
    ok, _ = s._confirms(Side.LONG, "breakout", _bar(101))
    assert ok


def test_vwap_filter_breakout_vs_fade():
    # Breakout long must be ABOVE vwap.
    s = _strategy(require_vwap=True)
    s.snapshot = IndicatorSnapshot(vwap=100, rsi=55, ema=99, atr=1, rvol=None)
    assert s._confirms(Side.LONG, "breakout", _bar(101))[0]
    assert not s._confirms(Side.LONG, "breakout", _bar(99))[0]

    # Fade short must be ABOVE vwap (extended, room to revert down).
    sf = _strategy(require_vwap=True, entry_mode="liquidity_sweep_fade")
    sf.snapshot = IndicatorSnapshot(vwap=100, rsi=75, ema=100, atr=1, rvol=None)
    assert sf._confirms(Side.SHORT, "sweep", _bar(103))[0]
    assert not sf._confirms(Side.SHORT, "sweep", _bar(98))[0]


def test_rsi_filter_modes():
    # Breakout long needs RSI >= 50.
    s = _strategy(require_rsi=True)
    s.snapshot = IndicatorSnapshot(vwap=100, rsi=40, ema=100, atr=1, rvol=None)
    assert not s._confirms(Side.LONG, "breakout", _bar(101))[0]

    # Fade short needs RSI >= overbought (70).
    sf = _strategy(require_rsi=True, entry_mode="liquidity_sweep_fade")
    sf.snapshot = IndicatorSnapshot(vwap=100, rsi=75, ema=100, atr=1, rvol=None)
    assert sf._confirms(Side.SHORT, "sweep", _bar(101))[0]
    sf.snapshot = IndicatorSnapshot(vwap=100, rsi=65, ema=100, atr=1, rvol=None)
    assert not sf._confirms(Side.SHORT, "sweep", _bar(101))[0]


def test_rsi_warmup_blocks_when_required():
    s = _strategy(require_rsi=True)
    s.snapshot = IndicatorSnapshot(vwap=100, rsi=None, ema=100, atr=1, rvol=None)
    ok, reason = s._confirms(Side.LONG, "breakout", _bar(101))
    assert not ok and reason == "rsi_warmup"
