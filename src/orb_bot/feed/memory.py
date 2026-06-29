"""In-memory feed backed by a list of bars (used by the sweep's train/test split)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from ..models import Bar
from .base import DataFeed


class ListFeed(DataFeed):
    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)

    def bars(self) -> Iterator[Bar]:
        yield from self._bars
