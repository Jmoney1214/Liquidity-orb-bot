"""Position sizing and account-level risk guards."""

from __future__ import annotations

from .config import ContractSpec, RiskConfig


def position_size(
    risk: RiskConfig,
    contract: ContractSpec,
    entry_price: float,
    stop_price: float,
) -> int:
    """Number of contracts to trade for the given stop distance.

    Sizes so that hitting the stop loses about ``risk_per_trade_pct`` of the
    account, then clamps to ``max_contracts``. ``fixed_contracts`` overrides
    the calculation entirely when set.
    """
    if risk.fixed_contracts is not None:
        return max(0, min(risk.fixed_contracts, risk.max_contracts))

    stop_points = abs(entry_price - stop_price)
    if stop_points <= 0:
        return 0

    dollar_risk_per_contract = stop_points * contract.point_value
    budget = risk.account_size * (risk.risk_per_trade_pct / 100.0)
    qty = int(budget // dollar_risk_per_contract)
    return max(0, min(qty, risk.max_contracts))


class DailyRiskGuard:
    """Tracks realised PnL for the day and enforces stop-trading limits."""

    def __init__(self, risk: RiskConfig) -> None:
        self.risk = risk
        self.realized_pnl = 0.0

    def reset(self) -> None:
        self.realized_pnl = 0.0

    def record(self, pnl: float) -> None:
        self.realized_pnl += pnl

    def trading_halted(self) -> bool:
        if self.realized_pnl <= -abs(self.risk.daily_loss_limit):
            return True
        if (
            self.risk.daily_profit_target is not None
            and self.realized_pnl >= self.risk.daily_profit_target
        ):
            return True
        return False
