"""Simulated broker used for backtesting and paper trading.

Fills are modelled deterministically from bar data:

* Market entries/exits fill at the reference price plus slippage.
* A resting stop fills when the bar's range trades through the stop price.
* A resting target fills when the bar's range trades through the target.
* If a single bar straddles both stop and target, we assume the **stop**
  filled first (conservative / worst-case for the trade).
"""

from __future__ import annotations

from ..config import ContractSpec
from ..models import Bar, Position, Side, Trade


class PaperBroker:
    """In-memory execution simulator."""

    def __init__(self, contract: ContractSpec, slippage_ticks: float = 1.0) -> None:
        self.contract = contract
        self.slippage_ticks = slippage_ticks
        self._position: Position | None = None
        self._trades: list[Trade] = []
        self._last_bar: Bar | None = None

    # -- Broker interface ------------------------------------------------

    def position(self) -> Position | None:
        return self._position

    @property
    def closed_trades(self) -> list[Trade]:
        return self._trades

    def enter_bracket(
        self,
        side: Side,
        quantity: int,
        stop_price: float,
        target_price: float | None,
        reference_price: float,
        tag: str = "entry",
    ) -> Position:
        if self._position is not None:
            raise RuntimeError("Cannot enter: a position is already open")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        # Entry slippage works against us: longs pay up, shorts sell down.
        fill = reference_price + side.sign * self._slip()
        fill = self.contract.round_to_tick(fill)
        ts = self._last_bar.timestamp if self._last_bar else None
        self._position = Position(
            side=side,
            quantity=quantity,
            entry_price=fill,
            entry_time=ts,
            stop_price=self.contract.round_to_tick(stop_price),
            target_price=self.contract.round_to_tick(target_price) if target_price else None,
        )
        return self._position

    def flatten(self, reference_price: float, reason: str) -> Trade | None:
        if self._position is None:
            return None
        pos = self._position
        # Exit slippage also works against us (opposite sign to the entry).
        fill = reference_price - pos.side.sign * self._slip()
        return self._close(self.contract.round_to_tick(fill), reason)

    def on_bar(self, bar: Bar) -> list[Trade]:
        self._last_bar = bar
        if self._position is None:
            return []

        pos = self._position
        hit_stop = self._stop_hit(pos, bar)
        hit_target = self._target_hit(pos, bar)

        if hit_stop:
            # Worst-case assumption when a bar covers both levels.
            return [self._close(pos.stop_price, "stop")]
        if hit_target:
            return [self._close(pos.target_price, "target")]
        return []

    # -- Internals -------------------------------------------------------

    def _slip(self) -> float:
        return self.slippage_ticks * self.contract.tick_size

    def _stop_hit(self, pos: Position, bar: Bar) -> bool:
        if pos.stop_price is None:
            return False
        if pos.side is Side.LONG:
            return bar.low <= pos.stop_price
        return bar.high >= pos.stop_price

    def _target_hit(self, pos: Position, bar: Bar) -> bool:
        if pos.target_price is None:
            return False
        if pos.side is Side.LONG:
            return bar.high >= pos.target_price
        return bar.low <= pos.target_price

    def _close(self, exit_price: float, reason: str) -> Trade:
        pos = self._position
        assert pos is not None
        points = (exit_price - pos.entry_price) * pos.side.sign
        fees = self.contract.fees_per_side * 2 * pos.quantity
        pnl = points * self.contract.point_value * pos.quantity - fees
        ts = self._last_bar.timestamp if self._last_bar else pos.entry_time
        trade = Trade(
            side=pos.side,
            quantity=pos.quantity,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            exit_time=ts,
            exit_price=exit_price,
            reason=reason,
            points=points,
            pnl=pnl,
            fees=fees,
        )
        self._trades.append(trade)
        self._position = None
        return trade
