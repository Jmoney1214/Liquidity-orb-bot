"""Alpaca Market Data v2 provider (stocks / ETFs).

Fetches OHLCV bars from Alpaca's ``/v2/stocks/{symbol}/bars`` endpoint with
automatic pagination, and exposes latest-bar polling for a live/alert loop.

A **pro / algo-trader** subscription unlocks the full **SIP** consolidated feed
(``feed="sip"``); the free tier is limited to IEX (``feed="iex"``).

Credentials are read from the environment (first match wins):
    ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
    APCA_API_KEY_ID   / APCA_API_SECRET_KEY   (Alpaca's native names)

Alpaca timestamps are RFC-3339 UTC; this module returns ET-localised Bars to
match the rest of the bot.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import EXCHANGE_TZ
from ..models import Bar
from .base import DataFeed


def _creds(key_id: str | None, secret_key: str | None) -> tuple[str, str]:
    key_id = key_id or os.environ.get("ALPACA_API_KEY_ID") or os.environ.get("APCA_API_KEY_ID")
    secret_key = (
        secret_key
        or os.environ.get("ALPACA_API_SECRET_KEY")
        or os.environ.get("APCA_API_SECRET_KEY")
    )
    if not key_id or not secret_key:
        raise ValueError(
            "Alpaca credentials required. Set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY (or the APCA_* equivalents)."
        )
    return key_id, secret_key


def _parse_ts(raw: str) -> datetime:
    # RFC-3339 UTC, e.g. "2024-03-01T14:30:00Z" -> ET-localised.
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EXCHANGE_TZ)


def parse_bars(raw_bars: list[dict]) -> list[Bar]:
    """Map Alpaca bar dicts to Bars (already chronological from the API)."""
    return [
        Bar(
            timestamp=_parse_ts(b["t"]),
            open=float(b["o"]),
            high=float(b["h"]),
            low=float(b["l"]),
            close=float(b["c"]),
            volume=float(b.get("v", 0) or 0),
        )
        for b in raw_bars
    ]


class AlpacaClient:
    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        feed: str = "sip",
        base_url: str = "https://data.alpaca.markets",
        timeout: float = 30.0,
        session=None,
    ) -> None:
        self.key_id, self.secret_key = _creds(key_id, secret_key)
        self.feed = feed
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        session.headers.update(
            {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret_key}
        )
        self._session = session

    def _get(self, url: str, params: dict) -> dict:
        resp = self._session.get(url, params=params, timeout=self.timeout)
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        # Alpaca returns {"message": "..."} on auth/permission/limit errors.
        if isinstance(payload, dict) and payload.get("message") and not payload.get("bars"):
            raise RuntimeError(f"Alpaca error ({resp.status_code}): {payload['message']}")
        resp.raise_for_status()
        return payload or {}

    def bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        start: str | None = None,
        end: str | None = None,
        adjustment: str = "raw",
        limit: int = 10_000,
    ) -> list[dict]:
        """Return all raw bar dicts for a symbol/range, following pagination."""
        url = f"{self.base_url}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": timeframe,
            "feed": self.feed,
            "adjustment": adjustment,
            "limit": limit,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        out: list[dict] = []
        page_token = None
        while True:
            if page_token:
                params["page_token"] = page_token
            payload = self._get(url, params)
            out.extend(payload.get("bars") or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return out

    def latest_bars(self, symbols: list[str], timeframe: str = "1Min") -> dict[str, Bar]:
        """Most recent completed bar per symbol (for a polling live loop)."""
        url = f"{self.base_url}/v2/stocks/bars/latest"
        params = {"symbols": ",".join(symbols), "feed": self.feed}
        bars = self._get(url, params).get("bars") or {}
        return {sym: parse_bars([b])[0] for sym, b in bars.items()}


class AlpacaFeed(DataFeed):
    """A :class:`DataFeed` backed by Alpaca historical bars."""

    def __init__(
        self,
        symbol: str,
        timeframe: str = "1Min",
        start: str | None = None,
        end: str | None = None,
        client: AlpacaClient | None = None,
        feed: str = "sip",
        **client_kwargs,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.start = start
        self.end = end
        self.client = client or AlpacaClient(feed=feed, **client_kwargs)

    def bars(self) -> Iterator[Bar]:
        raw = self.client.bars(self.symbol, self.timeframe, self.start, self.end)
        yield from parse_bars(raw)
