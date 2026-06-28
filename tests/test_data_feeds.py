"""Tests for the FMP and Alpaca data providers (offline, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orb_bot.config import EXCHANGE_TZ
from orb_bot.feed.fmp_feed import FMPClient, FMPFeed, parse_bars as fmp_parse
from orb_bot.feed.alpaca_feed import AlpacaClient, AlpacaFeed, parse_bars as alpaca_parse

FIXTURE = Path(__file__).parent / "fixtures" / "fmp_intraday_1min.json"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Returns queued payloads in order, recording the requests made."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return FakeResponse(self._payloads.pop(0))


# -- FMP ----------------------------------------------------------------

def test_fmp_parse_sorts_and_localizes():
    raw = json.loads(FIXTURE.read_text())  # newest-first, as FMP returns
    bars = fmp_parse(raw)
    # Sorted oldest-first.
    assert [b.timestamp for b in bars] == sorted(b.timestamp for b in bars)
    # Timezone-aware ET.
    assert all(b.timestamp.tzinfo is not None for b in bars)
    assert bars[0].timestamp.utcoffset() == \
        bars[0].timestamp.astimezone(EXCHANGE_TZ).utcoffset()
    # OHLCV mapped correctly against the known first (oldest) record.
    assert bars[0].close == 7377.75
    assert bars[-1].close == 7383.0


def test_fmp_client_and_feed_use_session():
    raw = json.loads(FIXTURE.read_text())
    sess = FakeSession([raw])
    client = FMPClient(api_key="TEST", session=sess)
    feed = FMPFeed("SPY", "1min", "2026-06-25", "2026-06-25", client=client)
    bars = list(feed.bars())
    assert len(bars) == len(raw)
    # The request carried the symbol, interval path, and key.
    url, params = sess.calls[0]
    assert "1min" in url
    assert params["symbol"] == "SPY"
    assert params["apikey"] == "TEST"


def test_fmp_requires_key(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(ValueError):
        FMPClient()


def test_fmp_rejects_bad_interval():
    client = FMPClient(api_key="TEST", session=FakeSession([[]]))
    with pytest.raises(ValueError):
        client.intraday("SPY", interval="2min")


# -- Alpaca -------------------------------------------------------------

ALPACA_PAGE_1 = {
    "bars": [
        {"t": "2024-03-01T14:30:00Z", "o": 510.0, "h": 511.0, "l": 509.5, "c": 510.5, "v": 1000},
        {"t": "2024-03-01T14:31:00Z", "o": 510.5, "h": 510.9, "l": 510.1, "c": 510.2, "v": 800},
    ],
    "next_page_token": "tok",
}
ALPACA_PAGE_2 = {
    "bars": [
        {"t": "2024-03-01T14:32:00Z", "o": 510.2, "h": 510.4, "l": 509.0, "c": 509.3, "v": 1200},
    ],
    "next_page_token": None,
}


def test_alpaca_parse_converts_utc_to_et():
    bars = alpaca_parse(ALPACA_PAGE_1["bars"])
    # 14:30 UTC on 2024-03-01 is 09:30 ET (EST, -5).
    et = bars[0].timestamp.astimezone(EXCHANGE_TZ)
    assert (et.hour, et.minute) == (9, 30)
    assert bars[0].open == 510.0


def test_alpaca_client_follows_pagination():
    sess = FakeSession([ALPACA_PAGE_1, ALPACA_PAGE_2])
    client = AlpacaClient(key_id="K", secret_key="S", session=sess, feed="sip")
    feed = AlpacaFeed("SPY", "1Min", "2024-03-01", "2024-03-02", client=client)
    bars = list(feed.bars())
    assert len(bars) == 3  # two pages merged
    assert len(sess.calls) == 2
    assert sess.calls[1][1]["page_token"] == "tok"
    assert sess.calls[0][1]["feed"] == "sip"
    # Auth headers were set on the session.
    assert sess.headers["APCA-API-KEY-ID"] == "K"


def test_alpaca_requires_creds(monkeypatch):
    for var in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError):
        AlpacaClient()
