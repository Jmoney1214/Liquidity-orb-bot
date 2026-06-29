"""Deterministic PASS / WARN / BLOCK decision engine.

Given a detected setup (side, entry, stop, target, size, indicator snapshot, and
liquidity context), the gate runs a fixed set of checks and renders a verdict:

* **BLOCK** — at least one *hard* (L5) safety rail failed. Do not take it.
* **WARN**  — no hard failures, but one or more *soft* checks flagged. The human
  may still take it, eyes open.
* **PASS**  — every check is clean.

The logic here is 100% deterministic: an LLM analyst may *explain* a decision in
prose, but it never overrides these rules. Build a human-readable rationale with
:meth:`Decision.render`; an optional LLM hook can elaborate on ``explanation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import ContractSpec, GateConfig, RiskConfig
from .indicators import IndicatorSnapshot
from .models import Bar, OpeningRange, Side


class Verdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class Severity(str, Enum):
    HARD = "HARD"  # failure -> BLOCK
    SOFT = "SOFT"  # failure -> WARN
    INFO = "INFO"  # never affects the verdict


@dataclass
class Check:
    name: str
    passed: bool
    severity: Severity
    detail: str


@dataclass
class Quote:
    bid: float
    ask: float

    @property
    def spread_bps(self) -> float:
        mid = (self.bid + self.ask) / 2.0
        return (self.ask - self.bid) / mid * 10_000 if mid > 0 else float("inf")


@dataclass
class SetupContext:
    """Everything the gate needs to judge one setup."""

    symbol: str
    bar: Bar
    side: Side
    kind: str  # "breakout" | "sweep"
    entry: float
    stop: float
    target: float | None
    risk_points: float
    snapshot: IndicatorSnapshot
    opening_range: OpeningRange
    quantity: int
    contract: ContractSpec
    risk: RiskConfig
    daily_pnl: float
    in_position: bool
    entry_mode: str
    quote: Quote | None = None


@dataclass
class RiskPreview:
    entry: float
    stop: float
    target: float | None
    quantity: int
    risk_points: float
    per_unit_risk: float
    dollar_risk: float
    dollar_reward: float | None
    rr: float | None

    def render(self) -> str:
        t = f"{self.target:.2f}" if self.target is not None else "none"
        rr = f"{self.rr:.2f}" if self.rr is not None else "n/a"
        return (
            f"entry {self.entry:.2f} | stop {self.stop:.2f} | target {t} | "
            f"qty {self.quantity} | risk ${self.dollar_risk:,.0f} | R:R {rr}"
        )


@dataclass
class Decision:
    symbol: str
    verdict: Verdict
    side: Side
    kind: str
    checks: list[Check]
    risk: RiskPreview
    explanation: str = ""
    analyst_note: str = ""  # optional LLM-written elaboration (deterministic code still decides)
    bar_time: str = ""      # display timestamp, filled in by the gate

    @property
    def hard_failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity is Severity.HARD]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity is Severity.SOFT]

    def render(self) -> str:
        ts = self.bar_time
        lines = [
            f"[{self.verdict.value}] {self.symbol} {self.kind} {self.side.value} @ {ts}",
            f"  {self.risk.render()}",
            f"  {self.explanation}",
        ]
        for c in self.checks:
            if not c.passed:
                mark = "✗" if c.severity is Severity.HARD else "!"
                lines.append(f"   {mark} {c.name}: {c.detail}")
        return "\n".join(lines)


class Gate:
    """Runs the deterministic check battery and produces a :class:`Decision`."""

    def __init__(self, config: GateConfig | None = None) -> None:
        self.cfg = config or GateConfig()

    def evaluate(self, ctx: SetupContext) -> Decision:
        checks: list[Check] = []
        preview = self._risk_preview(ctx)
        snap = ctx.snapshot
        c = self.cfg

        # -- HARD blocks (L5) ------------------------------------------
        checks.append(Check(
            "not_in_position", not ctx.in_position, Severity.HARD,
            "already in a position" if ctx.in_position else "flat",
        ))
        halted = ctx.daily_pnl <= -abs(ctx.risk.daily_loss_limit)
        checks.append(Check(
            "daily_loss_limit", not halted, Severity.HARD,
            f"daily PnL ${ctx.daily_pnl:,.0f} hit the loss limit" if halted
            else f"daily PnL ${ctx.daily_pnl:,.0f} within limit",
        ))
        checks.append(Check(
            "tradeable_size", ctx.quantity >= 1, Severity.HARD,
            "position sizes to 0 contracts/shares at this stop distance"
            if ctx.quantity < 1 else f"{ctx.quantity} unit(s)",
        ))
        checks.append(Check(
            "valid_stop", ctx.risk_points > 0, Severity.HARD,
            "stop distance is zero" if ctx.risk_points <= 0
            else f"{ctx.risk_points:.2f} pts to stop",
        ))
        checks.append(Check(
            "price_floor", ctx.entry >= c.min_price, Severity.HARD,
            f"price {ctx.entry:.2f} below floor {c.min_price:.2f}"
            if ctx.entry < c.min_price else f"price {ctx.entry:.2f} ok",
        ))
        notional = ctx.bar.volume * ctx.entry
        checks.append(Check(
            "liquidity_notional", notional >= c.min_dollar_volume, Severity.HARD,
            f"bar notional ${notional:,.0f} below floor ${c.min_dollar_volume:,.0f}"
            if notional < c.min_dollar_volume else f"bar notional ${notional:,.0f}",
        ))
        if preview.rr is not None:
            checks.append(Check(
                "reward_risk_floor", preview.rr >= c.min_rr_hard, Severity.HARD,
                f"R:R {preview.rr:.2f} below hard floor {c.min_rr_hard:.2f}"
                if preview.rr < c.min_rr_hard else f"R:R {preview.rr:.2f}",
            ))
        if ctx.quote is not None:
            sb = ctx.quote.spread_bps
            checks.append(Check(
                "spread_ceiling", sb <= c.max_spread_bps, Severity.HARD,
                f"spread {sb:.1f}bps over ceiling {c.max_spread_bps:.1f}bps"
                if sb > c.max_spread_bps else f"spread {sb:.1f}bps",
            ))

        # -- SOFT warnings ---------------------------------------------
        if preview.rr is not None:
            checks.append(Check(
                "reward_risk_quality", preview.rr >= c.min_rr_good, Severity.SOFT,
                f"R:R {preview.rr:.2f} below preferred {c.min_rr_good:.2f}"
                if preview.rr < c.min_rr_good else f"R:R {preview.rr:.2f} ok",
            ))
        rvol_ok = snap.rvol is not None and snap.rvol >= c.min_rvol_warn
        checks.append(Check(
            "relative_volume", rvol_ok, Severity.SOFT,
            (f"RVOL {snap.rvol:.2f}x below {c.min_rvol_warn:.2f}x"
             if snap.rvol is not None else "RVOL unknown (warming up)")
            if not rvol_ok else f"RVOL {snap.rvol:.2f}x",
        ))
        if notional < c.warn_dollar_volume:
            checks.append(Check(
                "liquidity_soft", False, Severity.SOFT,
                f"bar notional ${notional:,.0f} below comfort ${c.warn_dollar_volume:,.0f}",
            ))
        checks.append(self._vwap_check(ctx))
        checks.append(self._rsi_check(ctx))
        if ctx.quote is not None and ctx.quote.spread_bps > c.warn_spread_bps:
            checks.append(Check(
                "spread_soft", False, Severity.SOFT,
                f"spread {ctx.quote.spread_bps:.1f}bps above comfort {c.warn_spread_bps:.1f}bps",
            ))

        verdict = self._verdict(checks)
        decision = Decision(
            symbol=ctx.symbol, verdict=verdict, side=ctx.side, kind=ctx.kind,
            checks=checks, risk=preview,
        )
        decision.bar_time = ctx.bar.timestamp.strftime("%Y-%m-%d %H:%M %Z")
        decision.explanation = self._explain(ctx, decision)
        return decision

    # -- helpers --------------------------------------------------------

    def _risk_preview(self, ctx: SetupContext) -> RiskPreview:
        pv = ctx.contract.point_value
        per_unit = ctx.risk_points * pv
        dollar_risk = per_unit * ctx.quantity
        reward = rr = None
        if ctx.target is not None:
            reward_points = abs(ctx.target - ctx.entry)
            reward = reward_points * pv * ctx.quantity
            rr = (reward_points / ctx.risk_points) if ctx.risk_points > 0 else None
        return RiskPreview(
            entry=ctx.entry, stop=ctx.stop, target=ctx.target, quantity=ctx.quantity,
            risk_points=ctx.risk_points, per_unit_risk=per_unit,
            dollar_risk=dollar_risk, dollar_reward=reward, rr=rr,
        )

    def _vwap_check(self, ctx: SetupContext) -> Check:
        vwap = ctx.snapshot.vwap
        if vwap is None:
            return Check("vwap_alignment", True, Severity.SOFT, "VWAP unavailable")
        above = ctx.entry >= vwap
        if ctx.kind == "breakout":
            aligned = (ctx.side is Side.LONG and above) or (ctx.side is Side.SHORT and not above)
            msg = "with VWAP" if aligned else "against VWAP"
        else:  # fade wants an extension away from VWAP
            aligned = (ctx.side is Side.SHORT and above) or (ctx.side is Side.LONG and not above)
            msg = "extended from VWAP" if aligned else "not extended from VWAP"
        return Check("vwap_alignment", aligned, Severity.SOFT, f"{msg} ({ctx.entry:.2f} vs {vwap:.2f})")

    def _rsi_check(self, ctx: SetupContext) -> Check:
        rsi = ctx.snapshot.rsi
        if rsi is None:
            return Check("rsi_quality", True, Severity.SOFT, "RSI warming up")
        if ctx.kind == "breakout":
            ok = (ctx.side is Side.LONG and rsi >= 50) or (ctx.side is Side.SHORT and rsi <= 50)
            return Check("rsi_quality", ok, Severity.SOFT,
                         f"RSI {rsi:.0f} {'momentum-aligned' if ok else 'against momentum'}")
        ok = (ctx.side is Side.SHORT and rsi >= 70) or (ctx.side is Side.LONG and rsi <= 30)
        return Check("rsi_quality", ok, Severity.SOFT,
                     f"RSI {rsi:.0f} {'at an extreme' if ok else 'not extreme enough to fade'}")

    def _verdict(self, checks: list[Check]) -> Verdict:
        if any(not c.passed and c.severity is Severity.HARD for c in checks):
            return Verdict.BLOCK
        if any(not c.passed and c.severity is Severity.SOFT for c in checks):
            return Verdict.WARN
        return Verdict.PASS

    def _explain(self, ctx: SetupContext, d: Decision) -> str:
        snap = ctx.snapshot
        bits = [f"{ctx.entry_mode.split('_')[0]} {ctx.side.value}"]
        if snap.vwap is not None:
            bits.append(f"vwap {snap.vwap:.2f}")
        if snap.rsi is not None:
            bits.append(f"rsi {snap.rsi:.0f}")
        if snap.rvol is not None:
            bits.append(f"rvol {snap.rvol:.2f}x")
        if snap.atr is not None:
            bits.append(f"atr {snap.atr:.2f}")
        ctx_str = ", ".join(bits)
        if d.verdict is Verdict.BLOCK:
            why = "; ".join(c.detail for c in d.hard_failures)
            return f"BLOCK — {why}. ({ctx_str})"
        if d.verdict is Verdict.WARN:
            why = "; ".join(c.name for c in d.warnings)
            return f"WARN — caution on: {why}. ({ctx_str})"
        return f"PASS — all checks clean. ({ctx_str})"
