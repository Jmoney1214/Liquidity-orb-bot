"""Financial Modeling Prep (FMP) historical intraday data provider.

Fetches OHLCV bars from FMP's ``historical-chart`` family of endpoints and maps
them onto :class:`~orb_bot.models.Bar`. Works for equities/ETFs (SPY, QQQ),
the true E-mini S&P 500 continuous future (``ESUSD`` under commodities), index
symbols, crypto, and forex — selected via ``asset_class``.

API key is read from the ``FMP_API_KEY`` environment variable unless passed
explicitly. A "massive"/paid FMP plan is recommended for deep 1-minute history.

FMP intraday timestamps are US Eastern and returned **newest-first**; this
module normalises both (tz-aware ET, sorted oldest-first).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

from ..config import EXCHANGE_TZ
from ..models import Bar
from .base import DataFeed

# Map an asset class to the FMP "stable" endpoint path prefix.
_ASSET_PATHS = {
    "stock": "historical-chart",
    "commodity": "historical-chart",
    "index": "historical-chart",
    "crypto": "historical-chart",
    "forex": "historical-chart",
}

_VALID_INTERVALS = {"1min", "5min", "15min", "30min", "1hour", "4hour"}


class FMPClient:
    """Thin REST client over the FMP stable API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://financialmodelingprep.com",
        timeout: float = 30.0,
        session=None,
    ) -> None:
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Set FMP_API_KEY or pass api_key=..."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def intraday(
        self,
        symbol: str,
        interval: str = "1min",
        from_date: str | None = None,
        to_date: str | None = None,
        asset_class: str = "stock",
    ) -> list[dict]:
        """Return raw FMP bar dicts (newest-first, as the API delivers them)."""
        if interval not in _VALID_INTERVALS:
            raise ValueError(f"interval must be one of {sorted(_VALID_INTERVALS)}")
        if asset_class not in _ASSET_PATHS:
            raise ValueError(f"asset_class must be one of {sorted(_ASSET_PATHS)}")

        url = f"{self.base_url}/stable/{_ASSET_PATHS[asset_class]}/{interval}"
        params = {"symbol": symbol, "apikey": self.api_key}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        resp = self._session.get(url, params=params, timeout=self.timeout)
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        # Surface FMP's structured auth/limit errors (e.g. 401 invalid key) cleanly.
        if isinstance(payload, dict) and payload.get("Error Message"):
            raise RuntimeError(f"FMP error: {payload['Error Message']}")
        resp.raise_for_status()
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected FMP response for {symbol}: {str(payload)[:200]}")
        return payload


def parse_bars(raw: list[dict]) -> list[Bar]:
    """Map FMP rows to Bars, sorted oldest-first with ET-localised timestamps."""
    bars = []
    for row in raw:
        ts = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=EXCHANGE_TZ)
        bars.append(
            Bar(
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            )
        )
    bars.sort(key=lambda b: b.timestamp)
    return bars


class FMPFeed(DataFeed):
    """A :class:`DataFeed` backed by FMP historical intraday data."""

    def __init__(
        self,
        symbol: str,
        interval: str = "1min",
        from_date: str | None = None,
        to_date: str | None = None,
        asset_class: str = "stock",
        client: FMPClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.from_date = from_date
        self.to_date = to_date
        self.asset_class = asset_class
        self.client = client or FMPClient(api_key=api_key)

    def bars(self) -> Iterator[Bar]:
        raw = self.client.intraday(
            self.symbol, self.interval, self.from_date, self.to_date, self.asset_class
        )
        yield from parse_bars(raw)
