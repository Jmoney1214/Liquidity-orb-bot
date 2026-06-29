"""Tests for the parameter sweep (overrides, grid, split, ranking)."""

from __future__ import annotations

from orb_bot.config import Config, RiskConfig
from orb_bot.feed.csv_feed import CSVFeed
from orb_bot.sample_data import generate
from orb_bot.sweep import apply_overrides, expand_grid, split_bars, run_sweep


def test_apply_overrides_is_immutable_and_nested():
    cfg = Config()
    new = apply_overrides(cfg, {
        "strategy.target_r_multiple": 2.0,
        "session.opening_range_minutes": 15,
    })
    assert new.strategy.target_r_multiple == 2.0
    assert new.session.opening_range_minutes == 15
    # Original frozen config is untouched.
    assert cfg.strategy.target_r_multiple == 1.0
    assert cfg.session.opening_range_minutes == 30


def test_expand_grid_cartesian():
    grid = {"strategy.a": [1, 2], "session.b": [3, 4, 5]}
    combos = expand_grid(grid)
    assert len(combos) == 6
    assert {"strategy.a": 1, "session.b": 3} in combos


def test_split_bars_is_chronological_and_lossless(tmp_path):
    data = generate(tmp_path / "s.csv", days=10, seed=1)
    bars = list(CSVFeed(data).bars())
    train, test = split_bars(bars, 0.7)
    assert train and test
    assert len(train) + len(test) == len(bars)
    # No leakage: every train bar is strictly before every test bar.
    assert max(b.timestamp for b in train) < min(b.timestamp for b in test)


def test_run_sweep_ranks_and_reports_oos(tmp_path):
    data = generate(tmp_path / "s.csv", days=16, seed=2)
    cfg = Config(risk=RiskConfig(fixed_contracts=1))
    grid = {
        "strategy.target_r_multiple": [1.0, 2.0],
        "strategy.entry_mode": ["breakout_continuation"],
    }
    results = run_sweep(cfg, CSVFeed(data), grid=grid, train_frac=0.7,
                        rank_by="net_pnl", min_trades=1)
    assert len(results) == 2
    # Sorted by score (train net_pnl) descending.
    assert results[0].score >= results[1].score
    # Out-of-sample stats are populated when a test split exists.
    assert all(r.test is not None for r in results)


def test_run_sweep_min_trades_filter(tmp_path):
    data = generate(tmp_path / "s.csv", days=8, seed=3)
    cfg = Config(risk=RiskConfig(fixed_contracts=1))
    grid = {"strategy.target_r_multiple": [1.0]}
    # An impossibly high min-trades floor removes every config.
    results = run_sweep(cfg, CSVFeed(data), grid=grid, min_trades=10_000)
    assert results == []
