"""Performance statistics computed from a list of closed trades."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Trade


@dataclass
class PerformanceStats:
    trades: int
    wins: int
    losses: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    expectancy: float  # average PnL per trade
    max_drawdown: float
    sharpe: float  # per-trade Sharpe (not annualised)
    max_consecutive_losses: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()

    def render(self) -> str:
        lines = [
            f"  Trades:          {self.trades}",
            f"  Win rate:        {self.win_rate * 100:.1f}%  ({self.wins}W / {self.losses}L)",
            f"  Net PnL:         ${self.net_pnl:,.2f}",
            f"  Profit factor:   {self.profit_factor:.2f}",
            f"  Expectancy:      ${self.expectancy:,.2f} / trade",
            f"  Avg win / loss:  ${self.avg_win:,.2f} / ${self.avg_loss:,.2f}",
            f"  Max drawdown:    ${self.max_drawdown:,.2f}",
            f"  Sharpe (trade):  {self.sharpe:.2f}",
            f"  Max consec loss: {self.max_consecutive_losses}",
        ]
        return "\n".join(lines)


def compute_stats(trades: list[Trade]) -> PerformanceStats:
    n = len(trades)
    if n == 0:
        return PerformanceStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # positive number
    net = sum(pnls)

    # Equity curve & max drawdown.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # Max consecutive losses.
    streak = best_streak = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0

    mean = net / n
    if n > 1:
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var)
        sharpe = mean / std * math.sqrt(n) if std > 0 else 0.0
    else:
        sharpe = 0.0

    return PerformanceStats(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / n,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else math.inf,
        avg_win=(gross_profit / len(wins)) if wins else 0.0,
        avg_loss=(-gross_loss / len(losses)) if losses else 0.0,
        expectancy=mean,
        max_drawdown=max_dd,
        sharpe=sharpe,
        max_consecutive_losses=best_streak,
    )
