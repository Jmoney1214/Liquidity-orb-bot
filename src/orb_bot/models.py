"""Core data structures shared across the strategy, broker, and backtest layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    """Direction of a trade or order."""

    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        """+1 for long, -1 for short. Useful for PnL math."""
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG


class OrderType(str, Enum):
    MARKET = "MARKET"
    STOP = "STOP"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar. `timestamp` is timezone-aware (exchange/ET time)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar.timestamp must be timezone-aware")


@dataclass
class Order:
    """An instruction sent to a broker."""

    side: Side
    quantity: int
    order_type: OrderType
    price: float | None = None  # stop/limit trigger; None for market
    tag: str = ""  # e.g. "entry", "stop", "target", "eod_flat"


@dataclass
class Position:
    """An open position. Quantity is always positive; direction lives in `side`."""

    side: Side
    quantity: int
    entry_price: float
    entry_time: datetime
    stop_price: float | None = None
    target_price: float | None = None

    def unrealized_points(self, mark: float) -> float:
        return (mark - self.entry_price) * self.side.sign


@dataclass
class Trade:
    """A completed round-trip trade, recorded for analytics."""

    side: Side
    quantity: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    reason: str  # why we exited: "stop", "target", "eod_flat"
    points: float  # signed points captured (per contract)
    pnl: float  # signed dollar PnL after costs
    fees: float = 0.0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class OpeningRange:
    """The high/low established during the opening-range window."""

    high: float
    low: float
    start: datetime
    end: datetime

    @property
    def height(self) -> float:
        return self.high - self.low


@dataclass
class DayResult:
    """Per-day summary produced by the strategy/backtest."""

    date: str
    opening_range: OpeningRange | None
    trades: list[Trade] = field(default_factory=list)

    @property
    def pnl(self) -> float:
        return sum(t.pnl for t in self.trades)
