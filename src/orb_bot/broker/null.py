"""A broker that never fills — for detect-only / alert-only scanning.

The scanner drives the full strategy state machine (opening range, indicators,
signal detection) but must not simulate any trades; it only emits setups to the
gate. NullBroker satisfies the Broker interface while staying perpetually flat.
"""

from __future__ import annotations

from ..models import Bar, Position, Side, Trade


class NullBroker:
    def position(self) -> Position | None:
        return None

    @property
    def closed_trades(self) -> list[Trade]:
        return []

    def enter_bracket(
        self,
        side: Side,
        quantity: int,
        stop_price: float,
        target_price: float | None,
        reference_price: float,
        tag: str = "entry",
    ) -> Position:
        # Acknowledge without holding anything; the scanner alerts, humans act.
        return Position(
            side=side, quantity=quantity, entry_price=reference_price,
            entry_time=None, stop_price=stop_price, target_price=target_price,
        )

    def flatten(self, reference_price: float, reason: str) -> Trade | None:
        return None

    def on_bar(self, bar: Bar) -> list[Trade]:
        return []
