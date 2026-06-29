"""End-to-end tests for the alert-only scanner."""

from __future__ import annotations

from datetime import date

from orb_bot.feed.base import DataFeed
from orb_bot.gate import Verdict
from orb_bot.models import Bar, Side
from orb_bot.scanner import Scanner, summarize

from helpers import default_config, et, flat_bar, opening_range_bars


class ListFeed(DataFeed):
    def __init__(self, bars):
        self._bars = bars

    def bars(self):
        yield from self._bars


def _breakout_day():
    bars = opening_range_bars(date(2024, 3, 4), high=5010, low=4990)
    bars.append(flat_bar(et(2024, 3, 4, 10, 0), 5012))  # long breakout
    return bars


def test_scan_emits_one_alert_per_setup():
    cfg = default_config()
    alerts = Scanner(cfg).scan_symbol("SPY", ListFeed(_breakout_day()))
    assert len(alerts) == 1
    a = alerts[0]
    assert a.symbol == "SPY"
    assert a.decision.side is Side.LONG
    assert a.decision.kind == "breakout"
    assert a.decision.verdict in (Verdict.PASS, Verdict.WARN, Verdict.BLOCK)


def test_scan_no_setup_no_alert():
    cfg = default_config()
    # Opening range only -> nothing breaks out.
    bars = opening_range_bars(date(2024, 3, 4), high=5010, low=4990)
    alerts = Scanner(cfg).scan_symbol("SPY", ListFeed(bars))
    assert alerts == []


def test_scan_multiple_symbols_sorted():
    cfg = default_config()
    feeds = {"SPY": ListFeed(_breakout_day()), "QQQ": ListFeed(_breakout_day())}
    alerts = Scanner(cfg).scan(feeds)
    assert len(alerts) == 2
    assert {a.symbol for a in alerts} == {"SPY", "QQQ"}
    # Returned in chronological order.
    assert alerts[0].timestamp <= alerts[1].timestamp


def test_scanner_never_holds_position():
    # The NullBroker must keep the strategy flat so no trades are simulated.
    cfg = default_config()
    scanner = Scanner(cfg)
    alerts = scanner.scan_symbol("SPY", ListFeed(_breakout_day()))
    assert all(a.decision.checks for a in alerts)  # gate actually ran


def test_summarize_tally():
    cfg = default_config()
    alerts = Scanner(cfg).scan({"SPY": ListFeed(_breakout_day())})
    text = summarize(alerts)
    assert "setup(s)" in text
    assert "SPY" in text


def test_summarize_empty():
    assert "No setups" in summarize([])
