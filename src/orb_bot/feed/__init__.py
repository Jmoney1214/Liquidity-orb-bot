"""Market-data feeds: a common interface plus a CSV historical reader."""

from .base import DataFeed
from .csv_feed import CSVFeed

__all__ = ["DataFeed", "CSVFeed"]
