"""Read historical bars from a CSV file.

Expected columns (header row, case-insensitive):
    timestamp, open, high, low, close, volume

``timestamp`` may be ISO-8601 (``2024-03-01T09:30:00-05:00``) or a plain
``YYYY-MM-DD HH:MM:SS``. Naive timestamps are assumed to be US Eastern, which
is the natural clock for an NYSE-session bot.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..config import EXCHANGE_TZ
from ..models import Bar
from .base import DataFeed


def _parse_ts(raw: str) -> datetime:
    raw = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EXCHANGE_TZ)
    return dt


class CSVFeed(DataFeed):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def bars(self) -> Iterator[Bar]:
        with self.path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
            required = {"timestamp", "open", "high", "low", "close"}
            missing = required - cols.keys()
            if missing:
                raise ValueError(f"CSV {self.path} missing columns: {sorted(missing)}")
            vol_col = cols.get("volume")
            for row in reader:
                yield Bar(
                    timestamp=_parse_ts(row[cols["timestamp"]]),
                    open=float(row[cols["open"]]),
                    high=float(row[cols["high"]]),
                    low=float(row[cols["low"]]),
                    close=float(row[cols["close"]]),
                    volume=float(row[vol_col]) if vol_col and row[vol_col] else 0.0,
                )
