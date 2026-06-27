"""Generate synthetic ES-like 1-minute bars for demos and tests.

The data is *not* a real market — it is a random walk with a deliberately
constructed opening range and occasional intraday trends, just enough to
exercise the strategy end to end. Never draw trading conclusions from it.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from .config import EXCHANGE_TZ


def _session_bars(day: datetime, rng: random.Random) -> list[tuple]:
    """Build one RTH session (09:30-16:00) of 1-minute bars.

    Each day is given a "personality" so the backtest sees a realistic mix:
    clean trend days that break out and run, fade days that break out and
    reverse, and quiet range-bound days that never trigger.
    """
    rows = []
    price = 5000 + rng.uniform(-80, 80)
    tick = 0.25
    start = day.replace(hour=9, minute=30, second=0, microsecond=0, tzinfo=EXCHANGE_TZ)

    # Day type: trend (breakout runs), fade (breakout reverses), or chop.
    day_type = rng.choices(["trend", "fade", "chop"], weights=[0.45, 0.25, 0.30])[0]
    direction = rng.choice([-1, 1])

    for i in range(390):  # 6.5h * 60
        ts = start + timedelta(minutes=i)
        if i < 30:
            # Opening range: gentle random walk -> a modest, tradable range.
            drift, vol = 0.0, 1.0
        elif day_type == "trend":
            drift, vol = direction * 0.55, 1.2
        elif day_type == "fade":
            # Push out of the range early, then reverse hard the other way.
            drift = direction * (0.8 if i < 60 else -0.7)
            vol = 1.4
        else:  # chop
            drift, vol = 0.0, 1.0

        step = rng.gauss(drift, vol)
        o = price
        c = price + step
        h = max(o, c) + abs(rng.gauss(0, 0.6))
        l = min(o, c) - abs(rng.gauss(0, 0.6))
        o, h, l, c = (round(x / tick) * tick for x in (o, h, l, c))
        rows.append((ts.isoformat(), o, h, l, c, rng.randint(200, 3000)))
        price = c
    return rows


def generate(out_path: str | Path, days: int = 10, seed: int = 42) -> Path:
    rng = random.Random(seed)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Start far enough back to produce `days` weekday sessions.
    day = datetime(2024, 3, 4)  # a Monday
    sessions = []
    while len(sessions) < days:
        if day.weekday() < 5:  # Mon-Fri
            sessions.append(day)
        day += timedelta(days=1)

    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for session in sessions:
            for row in _session_bars(session, rng):
                w.writerow(row)
    return out
