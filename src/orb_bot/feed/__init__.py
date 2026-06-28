"""Market-data feeds: a common interface, a CSV reader, and live providers."""

import csv
from collections.abc import Iterable
from pathlib import Path

from ..models import Bar
from .base import DataFeed
from .csv_feed import CSVFeed

__all__ = ["DataFeed", "CSVFeed", "write_csv", "build_feed"]


def write_csv(bars: Iterable[Bar], path: str | Path) -> int:
    """Write bars to the canonical CSV format. Returns the row count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume])
            n += 1
    return n


def build_feed(
    provider: str,
    symbol: str,
    *,
    interval: str = "1min",
    from_date: str | None = None,
    to_date: str | None = None,
    asset_class: str = "stock",
    alpaca_feed: str = "sip",
) -> DataFeed:
    """Construct a historical feed for ``provider`` ("alpaca", "fmp", or "csv")."""
    provider = provider.lower()
    if provider == "fmp":
        from .fmp_feed import FMPFeed

        return FMPFeed(symbol, interval, from_date, to_date, asset_class)
    if provider == "alpaca":
        from .alpaca_feed import AlpacaFeed

        tf = {"1min": "1Min", "5min": "5Min", "15min": "15Min", "30min": "30Min"}.get(
            interval, interval
        )
        return AlpacaFeed(symbol, tf, from_date, to_date, feed=alpaca_feed)
    if provider == "csv":
        return CSVFeed(symbol)  # symbol is treated as a path here
    raise ValueError(f"unknown provider: {provider!r}")
