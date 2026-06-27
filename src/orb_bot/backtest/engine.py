"""Backtest harness: feed historical bars through the strategy and tally results."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from ..broker.paper import PaperBroker
from ..feed.base import DataFeed
from ..models import DayResult, Trade
from ..strategy import ORBStrategy
from .metrics import PerformanceStats, compute_stats


@dataclass
class BacktestResult:
    trades: list[Trade]
    days: list[DayResult]
    stats: PerformanceStats = field(init=False)

    def __post_init__(self) -> None:
        self.stats = compute_stats(self.trades)

    def render(self) -> str:
        traded_days = sum(1 for d in self.days if d.trades)
        header = (
            f"Backtest over {len(self.days)} session(s), "
            f"{traded_days} with trades\n" + "-" * 44
        )
        return f"{header}\n{self.stats.render()}"


class Backtester:
    def __init__(self, config: Config, slippage_ticks: float = 1.0) -> None:
        self.config = config
        self.slippage_ticks = slippage_ticks

    def run(self, feed: DataFeed) -> BacktestResult:
        broker = PaperBroker(self.config.contract, slippage_ticks=self.slippage_ticks)
        strategy = ORBStrategy(self.config, broker)

        last_bar = None
        for bar in feed.bars():
            strategy.on_bar(bar)
            last_bar = bar

        # Safety net: never leave a position open at the end of the data.
        if broker.position() is not None and last_bar is not None:
            trade = broker.flatten(last_bar.close, "data_end")
            if trade:
                strategy._record(trade)

        return BacktestResult(trades=list(broker.closed_trades), days=strategy.results)
