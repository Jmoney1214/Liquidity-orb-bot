"""Parameter sweep with an out-of-sample split, to tune STRATEGY parameters.

What this tunes — and what it deliberately does NOT
---------------------------------------------------
It varies the parameters that actually drive backtested PnL (entry mode, target
R, opening-range length, breakout buffer, range filters, confirmation toggles)
and ranks combinations on a chosen metric.

It does **not** sweep the gate's hard safety thresholds (`min_price`,
`min_dollar_volume`, `min_rr_hard`, daily loss limit, ...). Those are risk
*rails*, not performance dials — "optimising" them against PnL just means
removing safety to flatter a curve. They stay fixed by intent.

Overfitting guard
-----------------
Data is split chronologically into in-sample (train) and out-of-sample (test).
We rank on train but report BOTH, so you can see whether an edge survives on
unseen data. A config that tops the train table but collapses out-of-sample is
curve-fit — trust the ones that hold up on both.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from .backtest.engine import Backtester
from .backtest.metrics import PerformanceStats
from .config import Config
from .feed.base import DataFeed
from .feed.memory import ListFeed
from .config import EXCHANGE_TZ
from .models import Bar

# Parameters worth tuning (dotted section.key paths) and a sane default grid.
DEFAULT_GRID: dict[str, list] = {
    "strategy.entry_mode": ["breakout_continuation", "liquidity_sweep_fade"],
    "strategy.target_r_multiple": [1.0, 1.5, 2.0],
    "strategy.breakout_buffer_ticks": [1, 2],
    "session.opening_range_minutes": [15, 30],
    "strategy.max_range_points": [30.0, 40.0, 60.0],
}

RANK_METRICS = ("net_pnl", "profit_factor", "expectancy", "sharpe")


@dataclass
class SweepResult:
    params: dict
    train: PerformanceStats
    test: PerformanceStats | None
    score: float

    def holds_up(self) -> bool:
        """Did the edge survive out-of-sample (positive expectancy on test)?"""
        return self.test is not None and self.test.trades > 0 and self.test.expectancy > 0


def apply_overrides(cfg: Config, overrides: dict) -> Config:
    """Return a Config with dotted ``section.key`` overrides applied."""
    groups: dict[str, dict] = {}
    for path, value in overrides.items():
        section, key = path.split(".", 1)
        groups.setdefault(section, {})[key] = value
    kw = {section: replace(getattr(cfg, section), **kv) for section, kv in groups.items()}
    return replace(cfg, **kw)


def expand_grid(grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def split_bars(bars: list[Bar], train_frac: float) -> tuple[list[Bar], list[Bar]]:
    """Chronological train/test split on whole trading days (no leakage)."""
    if not 0.0 < train_frac <= 1.0:
        raise ValueError("train_frac must be in (0, 1]")
    dates = sorted({b.timestamp.astimezone(EXCHANGE_TZ).date() for b in bars})
    if train_frac >= 1.0 or len(dates) < 2:
        return bars, []
    cut = dates[int(len(dates) * train_frac)]
    train = [b for b in bars if b.timestamp.astimezone(EXCHANGE_TZ).date() < cut]
    test = [b for b in bars if b.timestamp.astimezone(EXCHANGE_TZ).date() >= cut]
    return train, test


def _score(stats: PerformanceStats, rank_by: str) -> float:
    value = getattr(stats, rank_by)
    # Profit factor is +inf with zero losses; keep it finite so sorting/printing work.
    if value == float("inf"):
        return 1e9
    return value


def run_sweep(
    base_config: Config,
    feed: DataFeed,
    grid: dict[str, list] | None = None,
    train_frac: float = 0.7,
    rank_by: str = "profit_factor",
    min_trades: int = 10,
    slippage_ticks: float = 1.0,
) -> list[SweepResult]:
    if rank_by not in RANK_METRICS:
        raise ValueError(f"rank_by must be one of {RANK_METRICS}")
    grid = grid or DEFAULT_GRID

    all_bars = list(feed.bars())
    train_bars, test_bars = split_bars(all_bars, train_frac)
    train_feed, test_feed = ListFeed(train_bars), ListFeed(test_bars) if test_bars else None

    results: list[SweepResult] = []
    for combo in expand_grid(grid):
        cfg = apply_overrides(base_config, combo)
        train_res = Backtester(cfg, slippage_ticks).run(train_feed)
        if train_res.stats.trades < min_trades:
            continue
        test_stats = None
        if test_feed is not None:
            test_stats = Backtester(cfg, slippage_ticks).run(test_feed).stats
        results.append(
            SweepResult(combo, train_res.stats, test_stats, _score(train_res.stats, rank_by))
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def render(results: list[SweepResult], rank_by: str, top: int = 10) -> str:
    if not results:
        return "No configurations met the minimum-trade threshold. Loosen --min-trades or widen the grid."
    lines = [
        f"Top {min(top, len(results))} of {len(results)} configs (ranked by train {rank_by}):",
        "  IS = in-sample (train), OOS = out-of-sample (test). Trust configs strong on BOTH.",
        "-" * 78,
    ]
    for r in results[:top]:
        params = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in r.params.items())
        is_s = r.train
        line = (
            f"  IS: pf {is_s.profit_factor:>5.2f}  net ${is_s.net_pnl:>10,.0f}  "
            f"n {is_s.trades:>3}  win {is_s.win_rate*100:>4.0f}%"
        )
        if r.test is not None:
            t = r.test
            flag = "  ✓ holds up" if r.holds_up() else "  ✗ weak OOS"
            line += (
                f"   |  OOS: pf {t.profit_factor:>5.2f}  net ${t.net_pnl:>9,.0f}  "
                f"n {t.trades:>3}{flag}"
            )
        lines.append(f"• {params}\n  {line}")
    return "\n".join(lines)
