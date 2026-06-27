"""Tests for position sizing, the daily risk guard, and metrics."""

from __future__ import annotations

from datetime import datetime, timezone

from orb_bot.config import ContractSpec, RiskConfig
from orb_bot.models import Side, Trade
from orb_bot.risk import DailyRiskGuard, position_size
from orb_bot.backtest.metrics import compute_stats


def test_position_size_respects_risk_budget():
    contract = ContractSpec()  # $50/point
    risk = RiskConfig(account_size=50_000, risk_per_trade_pct=1.0, max_contracts=20)
    # 1% of 50k = $500 risk; 5-point stop = $250/contract -> 2 contracts.
    assert position_size(risk, contract, entry_price=5000, stop_price=4995) == 2


def test_position_size_clamped_to_max():
    contract = ContractSpec()
    risk = RiskConfig(account_size=1_000_000, risk_per_trade_pct=5.0, max_contracts=3)
    assert position_size(risk, contract, 5000, 4999) == 3


def test_position_size_fixed_override():
    contract = ContractSpec()
    risk = RiskConfig(fixed_contracts=4, max_contracts=10)
    assert position_size(risk, contract, 5000, 4990) == 4


def test_position_size_zero_when_stop_too_wide():
    contract = ContractSpec()
    risk = RiskConfig(account_size=5_000, risk_per_trade_pct=1.0, max_contracts=10)
    # $50 budget but a 10-point stop costs $500/contract -> 0 contracts.
    assert position_size(risk, contract, 5000, 4990) == 0


def test_daily_guard_halts_on_loss_limit():
    guard = DailyRiskGuard(RiskConfig(daily_loss_limit=500))
    guard.record(-300)
    assert not guard.trading_halted()
    guard.record(-250)
    assert guard.trading_halted()
    guard.reset()
    assert not guard.trading_halted()


def _trade(pnl: float) -> Trade:
    ts = datetime(2024, 3, 4, tzinfo=timezone.utc)
    return Trade(Side.LONG, 1, ts, 5000, ts, 5000 + pnl / 50, "x", pnl / 50, pnl)


def test_metrics_basic():
    trades = [_trade(100), _trade(-50), _trade(200), _trade(-50)]
    stats = compute_stats(trades)
    assert stats.trades == 4
    assert stats.wins == 2
    assert stats.losses == 2
    assert stats.net_pnl == 200
    assert stats.profit_factor == 3.0  # 300 / 100
    assert stats.max_consecutive_losses == 1


def test_metrics_empty():
    stats = compute_stats([])
    assert stats.trades == 0
    assert stats.net_pnl == 0
