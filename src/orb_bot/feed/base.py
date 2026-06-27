"""Abstract market-data feed."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import Bar


class DataFeed(ABC):
    """A source of OHLCV bars, yielded in chronological order."""

    @abstractmethod
    def bars(self) -> Iterator[Bar]:
        """Yield bars oldest-first."""
