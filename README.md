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

### Entry modes

Two modes share the same opening-range engine, indicator stack, and risk gate
(set `strategy.entry_mode`, or override with `--mode`):

| Mode | Trigger | Stop | Idea |
|------|---------|------|------|
| `breakout_continuation` *(default)* | Bar **closes** beyond the range (+buffer) | Opposite side of range | Buy strength / sell weakness after a confirmed break |
| `liquidity_sweep_fade` | A wick runs **past** the level but the bar **closes back inside** | Just past the swept wick | Fade a failed break after stops get run — short swept highs, long swept lows |

```bash
orb-bot backtest --data data/spy.csv --contract equity --mode breakout_continuation
orb-bot backtest --data data/spy.csv --contract equity --mode liquidity_sweep_fade
```

### Confirmation stack

Optional indicator filters (all **off by default**) gate entries. Computed
incrementally so they behave identically in backtest and live:

| Filter | `breakout_continuation` | `liquidity_sweep_fade` |
|--------|-------------------------|------------------------|
| **VWAP** (`require_vwap`) | trade with it (long above / short below) | trade against an extension (short above / long below) |
| **EMA** (`require_ema_trend`) | long above EMA / short below | *(n/a)* |
| **RSI** (`require_rsi`) | momentum: long ≥ 50 / short ≤ 50 | extreme: short ≥ overbought / long ≤ oversold |
| **Relative volume** (`min_relative_volume`) | bar volume ≥ Nx the same minute-of-day's norm | same |
| **ATR** (`atr_period`) | computed and surfaced for sizing/analysis | same |

A failed filter rejects the bar but does **not** burn the day — a later bar can
still qualify. Tune these in `config.yaml` under `strategy:`.

## Copilot: decision gate + scanner (alert-only)

The bot can run as a **stock/ETF copilot**: it scans a watchlist, runs every
detected setup through a deterministic gate, and emits **PASS / WARN / BLOCK**
alerts with a risk preview. It **never places orders** — a human reads the
alert and decides.

```bash
# Scan a watchlist over a date range (provider data)
orb-bot scan --provider alpaca --symbols SPY,QQQ --from 2024-03-01 --to 2024-03-15

# Scan a local CSV (no keys needed)
orb-bot scan --data data/spy.csv --symbol SPY

# Live, alert-only polling loop
orb-bot scan --live --provider alpaca --symbols SPY,QQQ --poll 15
```

Each alert looks like:

```
[WARN ] SPY  breakout LONG @ 2024-03-13 10:08 | R:R 1.00 | WARN — caution on:
        reward_risk_quality. (breakout LONG, vwap 4926.13, rsi 61, rvol 1.49x)
```

**Verdict rules** (`config.yaml → gate:`):

- **BLOCK** — any *hard* (L5) safety rail fails: in a position, daily loss limit
  hit, size rounds to 0, invalid stop, price below `min_price`, notional below
  `min_dollar_volume`, R:R below `min_rr_hard`, or spread over `max_spread_bps`.
- **WARN** — no hard failure, but a soft check flags: R:R below `min_rr_good`,
  thin/unknown relative volume, against-VWAP, or off-side RSI.
- **PASS** — every check clean.

The logic is 100% deterministic — an LLM analyst may *explain* a verdict
(`Decision.analyst_note`), but it never overrides the rules.

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

## Real market data (FMP + Alpaca)

Pull real 1-minute bars straight into the CSV format above. API keys are read
from environment variables — copy [`.env.example`](.env.example) to `.env` and
fill it in:

| Provider | Env vars | Notes |
|----------|----------|-------|
| **Alpaca** | `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` | Pro/algo tier → full **SIP** feed (`alpaca_feed: sip`). Stocks/ETFs only. |
| **FMP** | `FMP_API_KEY` | Stocks/ETFs/indexes, plus true continuous **ES** futures as `ESUSD` (`--asset-class commodity`). |

```bash
# Alpaca (SIP) — SPY 1-minute bars, then backtest as an equity ($1/share)
orb-bot fetch --provider alpaca --symbol SPY \
  --from 2024-01-02 --to 2024-03-01 --out data/spy.csv
orb-bot backtest --data data/spy.csv --contract equity

# FMP — QQQ equities, or the real E-mini S&P 500 continuous future
orb-bot fetch --provider fmp --symbol QQQ  --asset-class stock     --out data/qqq.csv
orb-bot fetch --provider fmp --symbol ESUSD --asset-class commodity --out data/es.csv
```

> **Note on instruments.** Alpaca trades **stocks/ETFs/options/crypto — not
> futures.** For an equities ORB, trade **SPY/QQQ** (the ES/NQ proxies). FMP can
> additionally serve true ES futures bars (`ESUSD`) for research, but execution
> of ES still requires a futures broker (the bundled IBKR adapter).

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
  strategy.py      # ORBStrategy — modes, confirmation stack, broker-agnostic core
  indicators.py    # incremental EMA / RSI / ATR / session VWAP / relative volume
  gate.py          # deterministic PASS/WARN/BLOCK decision engine
  scanner.py       # alert-only watchlist scanner (batch + live polling)
  risk.py          # position sizing + daily risk guard
  feed/            # DataFeed interface, CSV reader, FMP + Alpaca providers
  broker/          # Broker interface, Paper / Null / IBKR adapters
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
