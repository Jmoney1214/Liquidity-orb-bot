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

from dataclasses import dataclass

from .config import Config, EXCHANGE_TZ
from .broker.base import Broker
from .indicators import Indicators, IndicatorSnapshot
from .models import Bar, OpeningRange, Side, Trade, DayResult
from .risk import DailyRiskGuard, position_size

logger = logging.getLogger(__name__)

ENTRY_MODES = ("breakout_continuation", "liquidity_sweep_fade")


@dataclass
class _Signal:
    """A structural entry candidate before confirmation/sizing."""

    side: Side
    entry: float
    stop: float
    target: float | None
    risk_points: float
    kind: str  # "breakout" or "sweep"


class ORBStrategy:
    def __init__(self, config: Config, broker: Broker) -> None:
        if config.strategy.entry_mode not in ENTRY_MODES:
            raise ValueError(
                f"entry_mode must be one of {ENTRY_MODES}, got {config.strategy.entry_mode!r}"
            )
        self.cfg = config
        self.broker = broker
        self.guard = DailyRiskGuard(config.risk)

        scfg = config.strategy
        self.ind = Indicators(
            rsi_period=scfg.rsi_period,
            ema_period=scfg.ema_period,
            atr_period=scfg.atr_period,
            rvol_lookback_days=scfg.rvol_lookback_days,
        )
        self.snapshot: IndicatorSnapshot | None = None  # latest indicator values

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

        # 2. Roll over on a new trading day (resets session indicators first).
        if day != self._date:
            self._start_new_day(day)

        # 3. Update the indicator stack with this bar.
        self.snapshot = self.ind.update(bar)

        sess = self.cfg.session

        # 4. Build / finalize the opening range.
        if sess.open_time <= t < sess.opening_range_end:
            self._extend_range(bar)
            return  # never trade inside the opening range
        if not self._range_ready and t >= sess.opening_range_end:
            self._finalize_range(bar)

        # 5. Force flat near the close.
        if t >= sess.flat_time:
            trade = self.broker.flatten(bar.close, "eod_flat")
            if trade:
                self._record(trade)
            return

        # 6. Consider a new entry.
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
        self.ind.reset_session()
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

        # 1. Structural signal for the active mode.
        sig = self._signal(bar, self._opening_range)
        if sig is None:
            return

        # 2. Confirmation stack. A rejection does NOT consume the day -- a later
        #    bar may still qualify (esp. for breakout continuation).
        ok, reason = self._confirms(sig.side, sig.kind, bar)
        if not ok:
            logger.info("%s: %s %s setup rejected by %s", self._date, sig.kind, sig.side.value, reason)
            return

        # 3. Size and enter.
        qty = position_size(self.cfg.risk, self.cfg.contract, sig.entry, sig.stop)
        if qty <= 0:
            logger.info("%s: position size rounded to 0; skipping signal", self._date)
            self._traded_today = True
            return

        self.broker.enter_bracket(sig.side, qty, sig.stop, sig.target, sig.entry, tag=f"orb_{sig.kind}")
        self._traded_today = True
        snap = self.snapshot
        logger.info(
            "%s: %s %s %d @ %.2f stop %.2f target %s | vwap=%s rsi=%s rvol=%s",
            self._date, sig.kind, sig.side.value, qty, sig.entry, sig.stop,
            f"{sig.target:.2f}" if sig.target else "none",
            f"{snap.vwap:.2f}" if snap and snap.vwap else "na",
            f"{snap.rsi:.0f}" if snap and snap.rsi else "na",
            f"{snap.rvol:.2f}" if snap and snap.rvol else "na",
        )

    # -- signal generation (per mode) ------------------------------------

    def _signal(self, bar: Bar, rng: OpeningRange) -> _Signal | None:
        if self.cfg.strategy.entry_mode == "liquidity_sweep_fade":
            return self._signal_sweep(bar, rng)
        return self._signal_breakout(bar, rng)

    def _signal_breakout(self, bar: Bar, rng: OpeningRange) -> _Signal | None:
        scfg = self.cfg.strategy
        buf = scfg.breakout_buffer_ticks * self.cfg.contract.tick_size
        side: Side | None = None
        if scfg.allow_long and bar.close >= rng.high + buf:
            side = Side.LONG
        elif scfg.allow_short and bar.close <= rng.low - buf:
            side = Side.SHORT
        if side is None:
            return None
        entry = bar.close
        stop = self._stop_price(side, entry, rng)
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        return _Signal(side, entry, stop, self._target_price(side, entry, risk), risk, "breakout")

    def _signal_sweep(self, bar: Bar, rng: OpeningRange) -> _Signal | None:
        """Fade a failed breakout: a wick pierces the level but the bar closes
        back inside the range. Short swept highs, long swept lows."""
        scfg = self.cfg.strategy
        tick = self.cfg.contract.tick_size
        buf = scfg.sweep_buffer_ticks * tick
        side: Side | None = None
        stop: float | None = None
        if scfg.allow_short and bar.high >= rng.high + buf and bar.close < rng.high:
            side, stop = Side.SHORT, bar.high + tick  # just above the swept wick
        elif scfg.allow_long and bar.low <= rng.low - buf and bar.close > rng.low:
            side, stop = Side.LONG, bar.low - tick
        if side is None or stop is None:
            return None
        entry = bar.close
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        return _Signal(side, entry, stop, self._target_price(side, entry, risk), risk, "sweep")

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

    # -- confirmation stack ----------------------------------------------

    def _confirms(self, side: Side, kind: str, bar: Bar) -> tuple[bool, str]:
        """Apply the indicator filters. Returns (passed, reason_if_failed)."""
        snap = self.snapshot
        scfg = self.cfg.strategy
        if snap is None:
            return False, "warmup"

        # Relative volume (both modes).
        if scfg.min_relative_volume > 0:
            if snap.rvol is None or snap.rvol < scfg.min_relative_volume:
                return False, "rvol"

        # VWAP: continuation trades with it; fade trades against an extension.
        if scfg.require_vwap and snap.vwap is not None:
            if kind == "breakout":
                if side is Side.LONG and bar.close < snap.vwap:
                    return False, "vwap"
                if side is Side.SHORT and bar.close > snap.vwap:
                    return False, "vwap"
            else:  # sweep/fade
                if side is Side.SHORT and bar.close < snap.vwap:
                    return False, "vwap"
                if side is Side.LONG and bar.close > snap.vwap:
                    return False, "vwap"

        # EMA trend filter (continuation only).
        if scfg.require_ema_trend and kind == "breakout" and snap.ema is not None:
            if side is Side.LONG and bar.close < snap.ema:
                return False, "ema"
            if side is Side.SHORT and bar.close > snap.ema:
                return False, "ema"

        # RSI: momentum for breakouts, extreme for fades.
        if scfg.require_rsi:
            if snap.rsi is None:
                return False, "rsi_warmup"
            if kind == "breakout":
                if side is Side.LONG and snap.rsi < 50:
                    return False, "rsi"
                if side is Side.SHORT and snap.rsi > 50:
                    return False, "rsi"
            else:
                if side is Side.SHORT and snap.rsi < scfg.rsi_overbought:
                    return False, "rsi"
                if side is Side.LONG and snap.rsi > scfg.rsi_oversold:
                    return False, "rsi"

        return True, "ok"

    # -- bookkeeping -----------------------------------------------------

    def _record(self, trade: Trade) -> None:
        self.guard.record(trade.pnl)
        if self._today is not None:
            self._today.trades.append(trade)
        logger.info(
            "EXIT %s %d @ %.2f (%s) pnl=%.2f",
            trade.side.value, trade.quantity, trade.exit_price, trade.reason, trade.pnl,
        )
