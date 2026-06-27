"""Configuration: contract specs, session times, and strategy/risk parameters.

All times are expressed in US Eastern (the NYSE / CME equity-index session
clock). Internally we anchor everything to ``America/New_York`` so the bot
behaves correctly across DST changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ContractSpec:
    """Futures contract specification.

    Defaults describe the CME E-mini S&P 500 (ES):
    tick size 0.25 index points, $12.50 per tick  ->  $50 per full point.
    """

    symbol: str = "ES"
    tick_size: float = 0.25
    tick_value: float = 12.50
    fees_per_side: float = 2.50  # commission + exchange/clearing, per contract per side

    @property
    def point_value(self) -> float:
        """Dollar value of a one-point move for one contract."""
        return self.tick_value / self.tick_size

    def round_to_tick(self, price: float) -> float:
        """Snap a raw price to the nearest valid tick."""
        return round(price / self.tick_size) * self.tick_size

    # Micro E-mini S&P 500 (MES): same tick size, 1/10th the dollar value.
    @classmethod
    def mes(cls) -> "ContractSpec":
        return cls(symbol="MES", tick_size=0.25, tick_value=1.25, fees_per_side=0.50)


@dataclass(frozen=True)
class SessionConfig:
    """Defines the opening-range window and the trading day boundaries (ET)."""

    open_time: time = time(9, 30)  # NYSE / RTH open
    opening_range_minutes: int = 30  # 9:30 -> 10:00 range
    last_entry_time: time = time(15, 0)  # no new entries after this
    flat_time: time = time(15, 55)  # force-close everything by here
    session_close: time = time(16, 0)

    @property
    def opening_range_end(self) -> time:
        total = self.open_time.hour * 60 + self.open_time.minute + self.opening_range_minutes
        return time(total // 60, total % 60)


@dataclass(frozen=True)
class StrategyConfig:
    """ORB strategy rules."""

    # Confirm a breakout on the close of a bar beyond the range, plus this many
    # ticks of buffer to avoid getting chopped up right at the level.
    breakout_buffer_ticks: int = 1
    # Take profit at this multiple of risk (range height by default). 0 disables.
    target_r_multiple: float = 1.0
    # Stop at the opposite side of the opening range when True; otherwise use
    # ``fixed_stop_points`` below.
    stop_at_opposite_side: bool = True
    fixed_stop_points: float = 5.0
    # Skip the day if the opening range is wider than this (too volatile / poor R).
    max_range_points: float = 40.0
    # Skip the day if the opening range is narrower than this (no real range).
    min_range_points: float = 2.0
    one_trade_per_day: bool = True
    allow_long: bool = True
    allow_short: bool = True


@dataclass(frozen=True)
class RiskConfig:
    """Position sizing and account-level guards."""

    # Defaults assume one full ES contract is in budget. A full opening-range
    # stop on ES is ~5-10 pts (~$250-$500), so a $25k account at 1% risk would
    # size to 0 contracts; trade MES (ContractSpec.mes) on smaller accounts.
    account_size: float = 100_000.0
    risk_per_trade_pct: float = 1.0  # % of account risked between entry and stop
    max_contracts: int = 10
    fixed_contracts: int | None = None  # if set, overrides risk-based sizing
    daily_loss_limit: float = 2_000.0  # stop trading for the day past this loss
    daily_profit_target: float | None = None  # stop trading once reached


@dataclass(frozen=True)
class Config:
    contract: ContractSpec = field(default_factory=ContractSpec)
    session: SessionConfig = field(default_factory=SessionConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        import yaml

        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        def _time(value, default: time) -> time:
            if value is None:
                return default
            if isinstance(value, time):
                return value
            hh, mm = str(value).split(":")[:2]
            return time(int(hh), int(mm))

        contract = ContractSpec(**(data.get("contract") or {}))

        s = data.get("session") or {}
        session = SessionConfig(
            open_time=_time(s.get("open_time"), SessionConfig().open_time),
            opening_range_minutes=s.get("opening_range_minutes", SessionConfig().opening_range_minutes),
            last_entry_time=_time(s.get("last_entry_time"), SessionConfig().last_entry_time),
            flat_time=_time(s.get("flat_time"), SessionConfig().flat_time),
            session_close=_time(s.get("session_close"), SessionConfig().session_close),
        )

        strategy = StrategyConfig(**(data.get("strategy") or {}))
        risk = RiskConfig(**(data.get("risk") or {}))
        return cls(contract=contract, session=session, strategy=strategy, risk=risk)
