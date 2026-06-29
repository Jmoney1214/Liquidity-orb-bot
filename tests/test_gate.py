"""Tests for the deterministic PASS/WARN/BLOCK decision engine."""

from __future__ import annotations

from orb_bot.config import ContractSpec, GateConfig, RiskConfig
from orb_bot.gate import Gate, Quote, SetupContext, Severity, Verdict
from orb_bot.indicators import IndicatorSnapshot
from orb_bot.models import Bar, OpeningRange, Side

from helpers import et


def _ctx(**over) -> SetupContext:
    """A clean, PASS-worthy SPY long breakout; override fields per test."""
    bar = Bar(et(2024, 3, 4, 10, 0), 450, 451, 449, 450, over.pop("volume", 20_000))
    snap = IndicatorSnapshot(
        vwap=over.pop("vwap", 449.0),
        rsi=over.pop("rsi", 60.0),
        ema=over.pop("ema", 449.0),
        atr=over.pop("atr", 1.0),
        rvol=over.pop("rvol", 1.5),
    )
    base = dict(
        symbol="SPY", bar=bar, side=Side.LONG, kind="breakout",
        entry=450.0, stop=449.0, target=452.0, risk_points=1.0,
        snapshot=snap, opening_range=OpeningRange(451, 449, bar.timestamp, bar.timestamp),
        quantity=10, contract=ContractSpec.equity("SPY"), risk=RiskConfig(),
        daily_pnl=0.0, in_position=False, entry_mode="breakout_continuation",
        quote=None,
    )
    base.update(over)
    return SetupContext(**base)


def test_clean_setup_passes():
    d = Gate().evaluate(_ctx())
    assert d.verdict is Verdict.PASS
    assert d.risk.rr == 2.0
    assert d.risk.dollar_risk == 10.0  # 1pt * $1 * 10 shares


def test_block_when_size_zero():
    d = Gate().evaluate(_ctx(quantity=0))
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "tradeable_size" for c in d.hard_failures)


def test_block_below_price_floor():
    d = Gate().evaluate(_ctx(entry=3.0, stop=2.5, target=4.0))
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "price_floor" for c in d.hard_failures)


def test_block_thin_notional():
    d = Gate().evaluate(_ctx(volume=10))  # 10 * 450 = $4.5k notional
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "liquidity_notional" for c in d.hard_failures)


def test_block_reward_risk_below_hard_floor():
    # target only 0.5pt away vs 1pt risk -> R:R 0.5 < 1.0 hard floor.
    d = Gate().evaluate(_ctx(target=450.5))
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "reward_risk_floor" for c in d.hard_failures)


def test_block_when_in_position():
    d = Gate().evaluate(_ctx(in_position=True))
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "not_in_position" for c in d.hard_failures)


def test_block_on_daily_loss_limit():
    risk = RiskConfig(daily_loss_limit=500)
    d = Gate().evaluate(_ctx(risk=risk, daily_pnl=-600))
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "daily_loss_limit" for c in d.hard_failures)


def test_block_wide_spread_with_quote():
    d = Gate().evaluate(_ctx(quote=Quote(bid=449.0, ask=450.0)))  # ~22bps
    assert d.verdict is Verdict.BLOCK
    assert any(c.name == "spread_ceiling" for c in d.hard_failures)


def test_warn_on_mediocre_rr():
    # R:R exactly 1.0 -> clears hard floor (1.0) but below good (1.5) -> WARN.
    d = Gate().evaluate(_ctx(target=451.0))
    assert d.verdict is Verdict.WARN
    assert any(c.name == "reward_risk_quality" for c in d.warnings)


def test_warn_on_thin_rvol():
    d = Gate().evaluate(_ctx(rvol=0.4))
    assert d.verdict is Verdict.WARN
    assert any(c.name == "relative_volume" for c in d.warnings)


def test_hard_block_outranks_soft_warn():
    # Thin rvol (warn) AND in a position (block) -> BLOCK wins.
    d = Gate().evaluate(_ctx(rvol=0.1, in_position=True))
    assert d.verdict is Verdict.BLOCK


def test_fade_rsi_check_wants_extreme():
    # Sweep short with RSI 60 is not extreme enough -> a soft rsi_quality warn.
    d = Gate().evaluate(_ctx(
        kind="sweep", entry_mode="liquidity_sweep_fade", side=Side.SHORT,
        entry=450.0, stop=451.0, target=448.0, vwap=449.0, rsi=60.0,
    ))
    rsi_checks = [c for c in d.checks if c.name == "rsi_quality"]
    assert rsi_checks and not rsi_checks[0].passed
    assert rsi_checks[0].severity is Severity.SOFT
