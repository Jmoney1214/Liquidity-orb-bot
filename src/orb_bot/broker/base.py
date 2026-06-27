"""Abstract broker interface.

The strategy talks to a broker only through this surface, so the exact same
ORB logic drives a simulated backtest, a paper account, or a live account.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Bar, Position, Side, Trade


class Broker(ABC):
    """Minimal execution interface used by the strategy."""

    @abstractmethod
    def position(self) -> Position | None:
        """Return the currently open position, or None if flat."""

    @abstractmethod
    def enter_bracket(
        self,
        side: Side,
        quantity: int,
        stop_price: float,
        target_price: float | None,
        reference_price: float,
        tag: str = "entry",
    ) -> Position:
        """Enter at market with an attached protective stop and optional target.

        ``reference_price`` is the price the decision was made on (typically the
        signal bar's close); simulated brokers use it as the fill basis before
        slippage.
        """

    @abstractmethod
    def flatten(self, reference_price: float, reason: str) -> Trade | None:
        """Close any open position at market. Returns the resulting Trade."""

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[Trade]:
        """Advance the broker by one bar.

        Implementations check resting stop/target orders against the bar's
        range and return any trades that closed as a result.
        """

    @property
    @abstractmethod
    def closed_trades(self) -> list[Trade]:
        """All round-trip trades closed so far this run."""
