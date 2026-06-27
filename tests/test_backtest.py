"""End-to-end backtest smoke test on generated sample data."""

from __future__ import annotations

from orb_bot.config import Config, ContractSpec, RiskConfig, SessionConfig, StrategyConfig
from orb_bot.backtest.engine import Backtester
from orb_bot.feed.csv_feed import CSVFeed
from orb_bot.sample_data import generate


def _config() -> Config:
    return Config(
        contract=ContractSpec(),
        session=SessionConfig(),
        strategy=StrategyConfig(),
        risk=RiskConfig(fixed_contracts=1),
    )


def test_backtest_runs_end_to_end(tmp_path):
    data = generate(tmp_path / "sample.csv", days=10, seed=7)
    result = Backtester(_config(), slippage_ticks=1.0).run(CSVFeed(data))

    # 10 weekday sessions should be tracked.
    assert len(result.days) == 10
    # No position should ever survive past the data.
    assert all(t.exit_price is not None for t in result.trades)
    # Stats render without error and PnL ties out to the trade list.
    assert abs(result.stats.net_pnl - sum(t.pnl for t in result.trades)) < 1e-6
    assert isinstance(result.render(), str)


def test_no_position_left_open(tmp_path):
    data = generate(tmp_path / "s.csv", days=5, seed=3)
    result = Backtester(_config()).run(CSVFeed(data))
    # Every trade is a closed round-trip with a known exit reason.
    for t in result.trades:
        assert t.reason in {"stop", "target", "eod_flat", "data_end"}
