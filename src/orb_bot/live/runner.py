"""Live trading runners.

Two flavours, both driving the identical :class:`ORBStrategy`:

* :func:`run_replay` — feed a CSV through the strategy + PaperBroker as if it
  were live. Great for an end-to-end dry run of the wiring with no broker.
* :func:`run_ibkr_live` — subscribe to real-time 1-minute bars from IB and
  route orders to an IB paper/live account.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..broker.paper import PaperBroker
from ..feed.base import DataFeed
from ..strategy import ORBStrategy

logger = logging.getLogger(__name__)


def run_replay(config: Config, feed: DataFeed, slippage_ticks: float = 1.0) -> ORBStrategy:
    """Drive the strategy bar-by-bar against a simulated broker."""
    broker = PaperBroker(config.contract, slippage_ticks=slippage_ticks)
    strategy = ORBStrategy(config, broker)
    for bar in feed.bars():
        strategy.on_bar(bar)
    return strategy


def run_ibkr_live(
    config: Config,
    host: str = "127.0.0.1",
    port: int = 7497,
    client_id: int = 1,
    expiry: str | None = None,
) -> None:  # pragma: no cover - requires a live IB connection
    """Connect to IB, stream 1-minute bars, and trade the ORB strategy.

    Uses ``reqRealTimeBars`` (5s) aggregated into 1-minute bars so the
    strategy sees the same bar shape it was backtested on.
    """
    from ..broker.ibkr import IBKRBroker, _require_ib
    from ..models import Bar

    ib_async = _require_ib()
    broker = IBKRBroker.connect(
        config.contract, host=host, port=port, client_id=client_id, expiry=expiry
    )
    strategy = ORBStrategy(config, broker)
    ib = broker._ib

    bars5s = ib.reqRealTimeBars(broker._ib_contract, 5, "TRADES", useRTH=False)

    agg = _MinuteAggregator()

    def _on_update(bars, has_new_bar):
        if not has_new_bar:
            return
        last = bars[-1]
        completed = agg.add(last.time, last.open_, last.high, last.low, last.close, last.volume)
        if completed is not None:
            strategy.on_bar(completed)

    bars5s.updateEvent += _on_update
    logger.info("Live ORB bot running. Ctrl-C to stop.")
    ib.run()


class _MinuteAggregator:
    """Roll 5-second IB bars up into completed 1-minute bars (ET-stamped)."""

    def __init__(self) -> None:
        self._minute = None
        self._o = self._h = self._l = self._c = None
        self._v = 0.0

    def add(self, ts, o, h, l, c, v):
        from ..config import EXCHANGE_TZ
        from ..models import Bar

        minute = ts.replace(second=0, microsecond=0)
        completed = None
        if self._minute is not None and minute != self._minute:
            completed = Bar(
                timestamp=self._minute.astimezone(EXCHANGE_TZ),
                open=self._o, high=self._h, low=self._l, close=self._c, volume=self._v,
            )
            self._o = None
        if self._o is None:
            self._minute = minute
            self._o, self._h, self._l, self._c, self._v = o, h, l, c, v
        else:
            self._h = max(self._h, h)
            self._l = min(self._l, l)
            self._c = c
            self._v += v
        return completed
