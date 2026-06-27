# ORB Bot — Opening Range Breakout for ES Futures

A day-trading bot that trades the **Opening Range Breakout (ORB)** strategy on
CME E-mini S&P 500 (**ES**) futures, using the first 30 minutes of the New York
(NYSE/RTH) session — 09:30–10:00 ET — to define the day's range.

The strategy core is **broker-agnostic**: the identical ORB logic powers the
backtester, a paper/replay dry-run, and live trading through Interactive
Brokers. Validate on history → dry-run the wiring → paper trade → (only then)
go live.

> ⚠️ **Risk notice.** Trading futures involves substantial risk of loss. This
> project is for research and education. The bundled sample data is synthetic
> and **not** a real market — any backtest result on it is meaningless for
> evaluating edge. Always paper trade before risking real capital.

---

## The strategy

1. **Opening range** — record the high and low of 09:30–10:00 ET.
2. **Filter** — skip the day if the range is implausibly wide or narrow
   (`max_range_points` / `min_range_points`).
3. **Entry** — when a bar **closes** beyond the range (plus a tick buffer), enter
   in that direction: long above the high, short below the low.
4. **Stop** — the opposite side of the opening range (or a fixed number of
   points).
5. **Target** — a configurable R-multiple of the stop distance (default 1R).
6. **Force flat** — close any open position by 15:55 ET; **one trade per day**.

Position size is risk-based: it sizes so that hitting the stop loses about
`risk_per_trade_pct` of the account, clamped to `max_contracts`. Daily loss
limit and optional profit target halt trading for the rest of the session.

---

## Install

```bash
pip install -e .            # core (PyYAML only)
pip install -e ".[ibkr]"    # + Interactive Brokers live trading (ib_async)
pip install -e ".[dev]"     # + pytest
```

Requires Python 3.10+.

## Quickstart

```bash
# 1. Generate synthetic sample data (30 weekday sessions of 1-min bars)
orb-bot gen-sample --out tests/sample_data/es_sample.csv --days 30

# 2. Backtest the ORB strategy on it
orb-bot backtest --data tests/sample_data/es_sample.csv

# 3. Tune via config, machine-readable output, micro contract, etc.
orb-bot backtest --data your_es_1min.csv --config config.yaml --json
orb-bot backtest --data your_es_1min.csv --contract mes   # Micro E-mini
```

Example output:

```
Backtest over 30 session(s), 30 with trades
--------------------------------------------
  Trades:          30
  Win rate:        76.7%  (23W / 7L)
  Net PnL:         $11,872.50
  Profit factor:   3.22
  ...
```

### Bring your own data

Backtests read a CSV of **1-minute** bars with a header row:

```
timestamp,open,high,low,close,volume
2024-03-01T09:30:00-05:00,5025.00,5027.50,5024.25,5026.75,1820
```

`timestamp` may be ISO-8601 (with offset) or `YYYY-MM-DD HH:MM:SS`; naive
timestamps are assumed to be US Eastern. Include the regular-hours session;
pre-market bars are ignored by the session window.

## Position sizing & account size

A full opening-range stop on ES is typically ~5–10 points (~$250–$500 per
contract). With risk-based sizing, a small account rounds to **0 contracts**
(and takes no trades). Two options:

- **Trade MES** (Micro E-mini, 1/10th the size): `--contract mes`.
- **Use a larger account** or `fixed_contracts` in the config.

The default config assumes a $100k account so one ES contract is in budget.

## Live / paper trading (Interactive Brokers)

```bash
# Dry-run the live wiring with no broker (replays a CSV through the live path):
orb-bot replay --data tests/sample_data/es_sample.csv

# Paper trade via IB (TWS/Gateway must be running):
orb-bot live --config config.yaml --port 7497   # 7497 = TWS paper
```

Ports: **7497** TWS paper · **4002** Gateway paper · 7496 TWS live · 4001
Gateway live. Orders are submitted as native IB **bracket orders**, so IB holds
the protective stop and target server-side. **Start on a paper account.**

## Configuration

All knobs live in [`config.yaml`](config.yaml) (times are US Eastern). Anything
omitted falls back to the built-in defaults. Highlights:

| Section    | Key                     | Meaning                                        |
|------------|-------------------------|------------------------------------------------|
| `contract` | `tick_size`/`tick_value`| ES = 0.25 pt / $12.50 ($50 per point)          |
| `session`  | `opening_range_minutes` | Range window length (default 30)               |
| `session`  | `flat_time`             | Force-flat time (default 15:55 ET)             |
| `strategy` | `target_r_multiple`     | Take-profit in R; `0` disables                 |
| `strategy` | `stop_at_opposite_side` | Stop at far range edge vs. `fixed_stop_points` |
| `strategy` | `max_range_points`      | Skip the day above this range width            |
| `risk`     | `risk_per_trade_pct`    | % of account risked per trade                  |
| `risk`     | `daily_loss_limit`      | Halt trading for the day past this loss        |

## Project layout

```
src/orb_bot/
  config.py        # contract spec, session times, strategy/risk params
  models.py        # Bar, Order, Position, Trade, OpeningRange
  strategy.py      # ORBStrategy — the broker-agnostic core
  risk.py          # position sizing + daily risk guard
  feed/            # DataFeed interface + CSV reader
  broker/          # Broker interface, PaperBroker (sim), IBKRBroker (live)
  backtest/        # Backtester + performance metrics
  live/            # replay + IB live runners
  sample_data.py   # synthetic data generator
tests/             # pytest suite (strategy, sizing, metrics, e2e backtest)
```

## Tests

```bash
pytest -q
```

## Disclaimer

Provided as-is for educational purposes, with no warranty. Nothing here is
financial advice. You are responsible for any orders this software places.
