"""The Opening Range Breakout strategy core.

This module is deliberately broker-agnostic: it consumes bars and drives a
:class:`~orb_bot.broker.base.Broker`. The very same object powers the
backtester and the live runner.

Bar timestamp convention
------------------------
Each :class:`~orb_bot.models.Bar` is timestamped with its **open** time. So a
1-minute bar stamped 09:59 covers 09:59:00-09:59:59 and is the final bar of a
09:30-10:00 opening range; the bar stamped 10:00 is the first bar that can
trigger a breakout entry.
"""

from __future__ import annotations

import logging

from .config import Config, EXCHANGE_TZ
from .broker.base import Broker
from .models import Bar, OpeningRange, Side, Trade, DayResult
from .risk import DailyRiskGuard, position_size

logger = logging.getLogger(__name__)


class ORBStrategy:
    def __init__(self, config: Config, broker: Broker) -> None:
        self.cfg = config
        self.broker = broker
        self.guard = DailyRiskGuard(config.risk)

        self._date = None  # current trading date (ET)
        self._or_high: float | None = None
        self._or_low: float | None = None
        self._opening_range: OpeningRange | None = None
        self._range_ready = False
        self._range_skipped = False  # filtered out (too wide/narrow)
        self._traded_today = False
        self.results: list[DayResult] = []
        self._today: DayResult | None = None

    # -- main entry point ------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        """Process one completed bar."""
        # 1. Let the broker resolve resting stop/target orders first.
        for trade in self.broker.on_bar(bar):
            self._record(trade)

        et = bar.timestamp.astimezone(EXCHANGE_TZ)
        t = et.time()
        day = et.date()

        # 2. Roll over on a new trading day.
        if day != self._date:
            self._start_new_day(day)

        sess = self.cfg.session

        # 3. Build / finalize the opening range.
        if sess.open_time <= t < sess.opening_range_end:
            self._extend_range(bar)
            return  # never trade inside the opening range
        if not self._range_ready and t >= sess.opening_range_end:
            self._finalize_range(bar)

        # 4. Force flat near the close.
        if t >= sess.flat_time:
            trade = self.broker.flatten(bar.close, "eod_flat")
            if trade:
                self._record(trade)
            return

        # 5. Consider a new entry.
        self._maybe_enter(bar, t)

    # -- day lifecycle ---------------------------------------------------

    def _start_new_day(self, day) -> None:
        self._date = day
        self._or_high = None
        self._or_low = None
        self._opening_range = None
        self._range_ready = False
        self._range_skipped = False
        self._traded_today = False
        self.guard.reset()
        self._today = DayResult(date=str(day), opening_range=None)
        self.results.append(self._today)

    def _extend_range(self, bar: Bar) -> None:
        self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
        self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)

    def _finalize_range(self, bar: Bar) -> None:
        self._range_ready = True
        if self._or_high is None or self._or_low is None:
            self._range_skipped = True
            logger.info("%s: no opening range data; skipping day", self._date)
            return
        rng = OpeningRange(
            high=self._or_high,
            low=self._or_low,
            start=bar.timestamp,
            end=bar.timestamp,
        )
        self._opening_range = rng
        if self._today is not None:
            self._today.opening_range = rng

        height = rng.height
        scfg = self.cfg.strategy
        if height > scfg.max_range_points or height < scfg.min_range_points:
            self._range_skipped = True
            logger.info(
                "%s: opening range %.2f pts outside [%.2f, %.2f]; skipping",
                self._date, height, scfg.min_range_points, scfg.max_range_points,
            )

    # -- entries ---------------------------------------------------------

    def _maybe_enter(self, bar: Bar, t) -> None:
        if not self._range_ready or self._range_skipped or self._opening_range is None:
            return
        if self.broker.position() is not None:
            return
        if self._traded_today and self.cfg.strategy.one_trade_per_day:
            return
        if t > self.cfg.session.last_entry_time:
            return
        if self.guard.trading_halted():
            return

        scfg = self.cfg.strategy
        buffer = scfg.breakout_buffer_ticks * self.cfg.contract.tick_size
        rng = self._opening_range

        side: Side | None = None
        if scfg.allow_long and bar.close >= rng.high + buffer:
            side = Side.LONG
        elif scfg.allow_short and bar.close <= rng.low - buffer:
            side = Side.SHORT
        if side is None:
            return

        entry = bar.close
        stop = self._stop_price(side, entry, rng)
        risk_points = abs(entry - stop)
        if risk_points <= 0:
            return
        target = self._target_price(side, entry, risk_points)

        qty = position_size(self.cfg.risk, self.cfg.contract, entry, stop)
        if qty <= 0:
            logger.info("%s: position size rounded to 0; skipping signal", self._date)
            self._traded_today = True
            return

        self.broker.enter_bracket(side, qty, stop, target, entry, tag="orb_entry")
        self._traded_today = True
        logger.info(
            "%s: %s %d @ %.2f stop %.2f target %s (range %.2f-%.2f)",
            self._date, side.value, qty, entry, stop,
            f"{target:.2f}" if target else "none", rng.low, rng.high,
        )

    def _stop_price(self, side: Side, entry: float, rng: OpeningRange) -> float:
        scfg = self.cfg.strategy
        if scfg.stop_at_opposite_side:
            return rng.low if side is Side.LONG else rng.high
        return entry - side.sign * scfg.fixed_stop_points

    def _target_price(self, side: Side, entry: float, risk_points: float) -> float | None:
        r = self.cfg.strategy.target_r_multiple
        if r <= 0:
            return None
        return entry + side.sign * r * risk_points

    # -- bookkeeping -----------------------------------------------------

    def _record(self, trade: Trade) -> None:
        self.guard.record(trade.pnl)
        if self._today is not None:
            self._today.trades.append(trade)
        logger.info(
            "EXIT %s %d @ %.2f (%s) pnl=%.2f",
            trade.side.value, trade.quantity, trade.exit_price, trade.reason, trade.pnl,
        )
