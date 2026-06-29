"""Alert-only watchlist scanner.

Runs the full ORB state machine over one or more symbols and routes every
detected setup through the deterministic :class:`~orb_bot.gate.Gate`, producing
PASS / WARN / BLOCK alerts. It NEVER places orders — a human reads the alerts
and decides. Trades are detected with a :class:`~orb_bot.broker.NullBroker` so
no positions are ever simulated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .broker.null import NullBroker
from .config import Config
from .feed.base import DataFeed
from .gate import Decision, Gate, SetupContext, Verdict
from .strategy import ORBStrategy

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    symbol: str
    timestamp: datetime
    decision: Decision


class Scanner:
    def __init__(self, config: Config, gate: Gate | None = None) -> None:
        self.config = config
        self.gate = gate or Gate(config.gate)

    # -- historical / batch ---------------------------------------------

    def scan_symbol(self, symbol: str, feed: DataFeed) -> list[Alert]:
        alerts: list[Alert] = []

        def listener(ctx: SetupContext) -> None:
            alerts.append(Alert(symbol, ctx.bar.timestamp, self.gate.evaluate(ctx)))

        strat = ORBStrategy(self.config, NullBroker(), symbol=symbol, signal_listener=listener)
        for bar in feed.bars():
            strat.on_bar(bar)
        return alerts

    def scan(self, feeds: dict[str, DataFeed]) -> list[Alert]:
        """Scan a {symbol: feed} map and return all alerts, time-ordered."""
        alerts: list[Alert] = []
        for symbol, feed in feeds.items():
            alerts.extend(self.scan_symbol(symbol, feed))
        alerts.sort(key=lambda a: a.timestamp)
        return alerts

    # -- live polling ---------------------------------------------------

    def run_live(
        self,
        symbols: list[str],
        client,
        poll_seconds: float = 15.0,
        on_alert: Callable[[Alert], None] | None = None,
        max_polls: int | None = None,
    ) -> None:  # pragma: no cover - needs live market data
        """Poll latest 1-minute bars and emit alerts as setups appear.

        ``client`` is an :class:`~orb_bot.feed.alpaca_feed.AlpacaClient`. Polling
        (vs. websockets) keeps dependencies light and is plenty for a 1-minute,
        alert-only copilot.
        """
        import time

        emit = on_alert or (lambda a: print(a.decision.render()))
        strategies: dict[str, ORBStrategy] = {}
        last_ts: dict[str, datetime] = {}

        def make(sym: str) -> ORBStrategy:
            return ORBStrategy(
                self.config, NullBroker(), symbol=sym,
                signal_listener=lambda ctx: emit(Alert(sym, ctx.bar.timestamp, self.gate.evaluate(ctx))),
            )

        for s in symbols:
            strategies[s] = make(s)

        logger.info("Live scan started for %s (alert-only). Ctrl-C to stop.", ", ".join(symbols))
        polls = 0
        while max_polls is None or polls < max_polls:
            try:
                latest = client.latest_bars(symbols)
            except Exception as exc:  # transient API hiccup -> keep polling
                logger.warning("poll failed: %s", exc)
                latest = {}
            for sym, bar in latest.items():
                if last_ts.get(sym) == bar.timestamp:
                    continue  # not a new completed bar yet
                last_ts[sym] = bar.timestamp
                strategies[sym].on_bar(bar)
            polls += 1
            time.sleep(poll_seconds)


def summarize(alerts: list[Alert]) -> str:
    """One-line-per-alert summary with a verdict tally."""
    if not alerts:
        return "No setups detected."
    counts = {v: 0 for v in Verdict}
    lines = []
    for a in alerts:
        counts[a.decision.verdict] += 1
        d = a.decision
        rr = f"{d.risk.rr:.2f}" if d.risk.rr is not None else "n/a"
        lines.append(
            f"  [{d.verdict.value:5}] {a.symbol:6} {d.kind:8} {d.side.value:5} "
            f"@ {a.timestamp:%Y-%m-%d %H:%M} | R:R {rr} | {d.explanation}"
        )
    tally = "  ".join(f"{v.value}: {counts[v]}" for v in Verdict)
    header = f"{len(alerts)} setup(s)  ({tally})\n" + "-" * 60
    return header + "\n" + "\n".join(lines)
