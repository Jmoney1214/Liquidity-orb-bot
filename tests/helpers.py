"""Shared test helpers for building deterministic bar sequences."""

from __future__ import annotations

from datetime import datetime, timedelta

from orb_bot.config import (
    Config,
    ContractSpec,
    RiskConfig,
    SessionConfig,
    StrategyConfig,
    EXCHANGE_TZ,
)
from orb_bot.models import Bar


def et(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=EXCHANGE_TZ)


def flat_bar(ts: datetime, price: float) -> Bar:
    return Bar(timestamp=ts, open=price, high=price, low=price, close=price, volume=100)


def opening_range_bars(day, high: float, low: float):
    """30 one-minute bars (09:30-09:59) that establish [low, high]."""
    bars = []
    base = et(day.year, day.month, day.day, 9, 30)
    for i in range(30):
        ts = base + timedelta(minutes=i)
        if i == 0:
            bars.append(Bar(ts, low, high, low, (high + low) / 2, 100))
        else:
            mid = (high + low) / 2
            bars.append(Bar(ts, mid, mid + 0.25, mid - 0.25, mid, 100))
    return bars


def default_config(**strategy_overrides) -> Config:
    strat = StrategyConfig(**strategy_overrides) if strategy_overrides else StrategyConfig()
    return Config(
        contract=ContractSpec(),
        session=SessionConfig(),
        strategy=strat,
        risk=RiskConfig(fixed_contracts=1),
    )
