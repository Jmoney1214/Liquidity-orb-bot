"""Behavioural tests for the ORB strategy state machine."""

from __future__ import annotations

from datetime import date, timedelta

from orb_bot.broker.paper import PaperBroker
from orb_bot.models import Bar, Side
from orb_bot.strategy import ORBStrategy

from helpers import default_config, et, flat_bar, opening_range_bars


def _run(config, bars):
    broker = PaperBroker(config.contract, slippage_ticks=0.0)
    strat = ORBStrategy(config, broker)
    for b in bars:
        strat.on_bar(b)
    return broker, strat


def test_long_breakout_hits_target():
    day = date(2024, 3, 4)
    cfg = default_config()  # opposite-side stop, 1R target, 1 contract
    bars = opening_range_bars(day, high=5010, low=4990)

    # 10:00 breakout above 5010 -> long @ 5012, stop 4990 (22pt), target 5034.
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 5012))
    # Drive up through the target.
    bars.append(Bar(et(2024, 3, 4, 10, 1), 5013, 5035, 5012, 5034, 100))

    broker, strat = _run(cfg, bars)

    assert len(broker.closed_trades) == 1
    t = broker.closed_trades[0]
    assert t.side is Side.LONG
    assert t.reason == "target"
    assert t.exit_price == 5034
    assert t.pnl > 0


def test_short_breakout_hits_stop():
    day = date(2024, 3, 4)
    cfg = default_config()
    bars = opening_range_bars(day, high=5010, low=4990)

    # Break below 4990 -> short @ 4988, stop at 5010 (opposite side).
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 4988))
    # Price rallies back through the stop at 5010.
    bars.append(Bar(et(2024, 3, 4, 10, 1), 4989, 5011, 4987, 5009, 100))

    broker, strat = _run(cfg, bars)

    assert len(broker.closed_trades) == 1
    t = broker.closed_trades[0]
    assert t.side is Side.SHORT
    assert t.reason == "stop"
    assert t.exit_price == 5010
    assert t.pnl < 0


def test_no_trade_inside_opening_range():
    day = date(2024, 3, 4)
    cfg = default_config()
    # Only opening-range bars: a spike inside the range must not trade.
    bars = opening_range_bars(day, high=5010, low=4990)
    broker, strat = _run(cfg, bars)
    assert broker.position() is None
    assert broker.closed_trades == []


def test_force_flat_at_session_end():
    day = date(2024, 3, 4)
    cfg = default_config()
    bars = opening_range_bars(day, high=5010, low=4990)
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 5012))  # enter long
    # Drift sideways (no stop/target hit) until the flat time.
    bars.append(flat_bar(et(2024, 3, 4, 15, 55), 5020))

    broker, strat = _run(cfg, bars)
    assert len(broker.closed_trades) == 1
    assert broker.closed_trades[0].reason == "eod_flat"
    assert broker.position() is None


def test_wide_range_is_skipped():
    day = date(2024, 3, 4)
    cfg = default_config(max_range_points=10.0)  # range of 20 is too wide
    bars = opening_range_bars(day, high=5010, low=4990)
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 5020))  # would-be breakout
    broker, strat = _run(cfg, bars)
    assert broker.closed_trades == []
    assert broker.position() is None


def test_one_trade_per_day():
    day = date(2024, 3, 4)
    cfg = default_config(target_r_multiple=1.0)
    bars = opening_range_bars(day, high=5010, low=4990)
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 5012))  # long
    bars.append(Bar(et(2024, 3, 4, 10, 1), 5013, 5035, 5012, 5034, 100))  # target hit
    # A second breakout later in the day must be ignored.
    bars.append(flat_bar(et(2024, 3, 4, 11, 0), 5040))
    bars.append(flat_bar(et(2024, 3, 4, 15, 55), 5040))

    broker, strat = _run(cfg, bars)
    assert len(broker.closed_trades) == 1


def test_short_disabled_blocks_short():
    day = date(2024, 3, 4)
    cfg = default_config(allow_short=False)
    bars = opening_range_bars(day, high=5010, low=4990)
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 4985))  # downside breakout
    bars.append(flat_bar(et(2024, 3, 4, 15, 55), 4985))
    broker, strat = _run(cfg, bars)
    assert broker.closed_trades == []
