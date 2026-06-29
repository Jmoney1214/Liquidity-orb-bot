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

    # Cash equity / ETF (e.g. SPY, QQQ): $0.01 tick, $1 per $1 move per share,
    # commission-free on Alpaca.
    @classmethod
    def equity(cls, symbol: str = "SPY", fees_per_side: float = 0.0) -> "ContractSpec":
        return cls(symbol=symbol, tick_size=0.01, tick_value=0.01, fees_per_side=fees_per_side)


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

    # Entry mode:
    #   "breakout_continuation" -> trade THROUGH the range (buy strength / sell
    #       weakness after a confirmed break).
    #   "liquidity_sweep_fade"  -> fade a FAILED break: price runs past the level
    #       to grab stop-run liquidity, then closes back inside -> short the sweep
    #       of highs, long the sweep of lows.
    entry_mode: str = "breakout_continuation"

    # Confirm a breakout on the close of a bar beyond the range, plus this many
    # ticks of buffer to avoid getting chopped up right at the level.
    breakout_buffer_ticks: int = 1
    # How far beyond the level (ticks) a wick must poke to count as a sweep.
    sweep_buffer_ticks: int = 1
    # Take profit at this multiple of risk (range height by default). 0 disables.
    target_r_multiple: float = 1.0
    # Stop at the opposite side of the opening range when True; otherwise use
    # ``fixed_stop_points`` below. (Breakout mode; sweeps always stop past the wick.)
    stop_at_opposite_side: bool = True
    fixed_stop_points: float = 5.0
    # Skip the day if the opening range is wider than this (too volatile / poor R).
    max_range_points: float = 40.0
    # Skip the day if the opening range is narrower than this (no real range).
    min_range_points: float = 2.0
    one_trade_per_day: bool = True
    allow_long: bool = True
    allow_short: bool = True

    # --- Confirmation stack (all OFF/lenient by default) -------------------
    # VWAP: continuation trades with VWAP (long above / short below); fade trades
    # back toward it (short when extended above / long when extended below).
    require_vwap: bool = False
    # EMA trend filter (continuation only): long above EMA, short below.
    require_ema_trend: bool = False
    ema_period: int = 20
    # RSI: continuation needs momentum (long >=50 / short <=50); fade needs an
    # extreme (short >= overbought / long <= oversold).
    require_rsi: bool = False
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    # Relative volume gate: require current bar >= this multiple of normal. 0 = off.
    min_relative_volume: float = 0.0
    rvol_lookback_days: int = 14
    atr_period: int = 14


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
class DataConfig:
    """Market-data provider settings.

    API keys are NEVER stored here — they are read from environment variables
    at runtime (``FMP_API_KEY``, ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY``,
    also accepting Alpaca's native ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``).
    """

    provider: str = "alpaca"  # default historical/live provider: "alpaca" | "fmp"
    watchlist: tuple[str, ...] = ("SPY", "QQQ")

    # Alpaca market data (pro tier -> full SIP feed).
    alpaca_feed: str = "sip"  # "sip" (pro) or "iex" (free)
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Financial Modeling Prep.
    fmp_base_url: str = "https://financialmodelingprep.com"
    # Which FMP endpoint family the symbol belongs to:
    #   "stock" (SPY/QQQ/AAPL), "commodity" (ESUSD/NQUSD), "index" (^GSPC), "crypto", "forex".
    fmp_asset_class: str = "stock"


@dataclass(frozen=True)
class GateConfig:
    """Thresholds for the PASS/WARN/BLOCK decision engine.

    "Hard" checks are L5 blocks — non-negotiable safety rails that force BLOCK.
    "Warn" checks downgrade a PASS to WARN but still let the human decide.
    """

    # --- Hard blocks (L5) ---
    min_price: float = 5.0                  # avoid sub-$5 / illiquid names
    min_dollar_volume: float = 1_000_000.0  # per-bar notional floor (volume * price)
    max_spread_bps: float = 10.0            # only enforced when a live quote is supplied
    min_rr_hard: float = 1.0                # reject reward:risk below this (when a target is set)

    # --- Soft warnings ---
    min_rr_good: float = 1.5                # warn on R:R between hard floor and this
    warn_dollar_volume: float = 5_000_000.0
    min_rvol_warn: float = 1.0              # warn on thin/unknown relative volume
    warn_spread_bps: float = 5.0


@dataclass(frozen=True)
class Config:
    contract: ContractSpec = field(default_factory=ContractSpec)
    session: SessionConfig = field(default_factory=SessionConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    gate: GateConfig = field(default_factory=GateConfig)

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

        d = dict(data.get("data") or {})
        if "watchlist" in d and d["watchlist"] is not None:
            d["watchlist"] = tuple(d["watchlist"])
        data_cfg = DataConfig(**d)

        gate = GateConfig(**(data.get("gate") or {}))

        return cls(
            contract=contract, session=session, strategy=strategy,
            risk=risk, data=data_cfg, gate=gate,
        )
