"""Command-line interface for the ORB bot."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import Config, ContractSpec
from .feed.csv_feed import CSVFeed


def _load_config(args) -> Config:
    cfg = Config.from_yaml(args.config) if args.config else Config()
    contract = getattr(args, "contract", None)
    override = None
    if contract == "mes":
        override = ContractSpec.mes()
    elif contract == "equity":
        # Price equities at $1/share; label with the traded symbol when known.
        override = ContractSpec.equity(getattr(args, "symbol", None) or "SPY")
    if override is not None:
        cfg = Config(
            contract=override,
            session=cfg.session,
            strategy=cfg.strategy,
            risk=cfg.risk,
            data=cfg.data,
        )
    return cfg


def _cmd_backtest(args) -> int:
    from .backtest.engine import Backtester

    cfg = _load_config(args)
    result = Backtester(cfg, slippage_ticks=args.slippage).run(CSVFeed(args.data))
    if args.json:
        print(json.dumps(result.stats.as_dict(), indent=2, default=str))
    else:
        print(result.render())
    return 0


def _cmd_replay(args) -> int:
    from .live.runner import run_replay

    cfg = _load_config(args)
    strat = run_replay(cfg, CSVFeed(args.data), slippage_ticks=args.slippage)
    from .backtest.metrics import compute_stats

    print(compute_stats(strat.broker.closed_trades).render())
    return 0


def _cmd_live(args) -> int:  # pragma: no cover - requires IB connection
    from .live.runner import run_ibkr_live

    cfg = _load_config(args)
    logging.warning(
        "Starting LIVE/PAPER IB session on %s:%d. Ensure this is a paper "
        "account unless you intend to trade real money.",
        args.host, args.port,
    )
    run_ibkr_live(
        cfg, host=args.host, port=args.port, client_id=args.client_id, expiry=args.expiry
    )
    return 0


def _cmd_fetch(args) -> int:
    from .feed import build_feed, write_csv

    cfg = _load_config(args)
    feed = build_feed(
        args.provider,
        args.symbol,
        interval=args.interval,
        from_date=getattr(args, "from"),
        to_date=args.to,
        asset_class=args.asset_class,
        alpaca_feed=cfg.data.alpaca_feed,
    )
    n = write_csv(feed.bars(), args.out)
    print(f"Wrote {n} {args.interval} bars for {args.symbol} ({args.provider}) -> {args.out}")
    return 0


def _cmd_gen_sample(args) -> int:
    from .sample_data import generate

    generate(args.out, days=args.days, seed=args.seed)
    print(f"Wrote {args.days} session(s) of sample 1-min bars to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orb-bot", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true", help="enable info logging")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--config", help="path to config.yaml (defaults to built-in ES config)")
        sp.add_argument(
            "--contract", choices=["es", "mes", "equity"], default="es",
            help="instrument pricing: es/mes futures, or equity ($1/share for SPY/QQQ)",
        )

    bt = sub.add_parser("backtest", help="run a historical backtest on a CSV")
    add_common(bt)
    bt.add_argument("--data", required=True, help="CSV of 1-min OHLCV bars")
    bt.add_argument("--slippage", type=float, default=1.0, help="slippage in ticks per side")
    bt.add_argument("--json", action="store_true", help="emit stats as JSON")
    bt.set_defaults(func=_cmd_backtest)

    rp = sub.add_parser("replay", help="dry-run the live wiring against a CSV")
    add_common(rp)
    rp.add_argument("--data", required=True)
    rp.add_argument("--slippage", type=float, default=1.0)
    rp.set_defaults(func=_cmd_replay)

    lv = sub.add_parser("live", help="trade live/paper via Interactive Brokers")
    add_common(lv)
    lv.add_argument("--host", default="127.0.0.1")
    lv.add_argument("--port", type=int, default=7497, help="7497 TWS paper, 4002 Gateway paper")
    lv.add_argument("--client-id", type=int, default=1)
    lv.add_argument("--expiry", default=None, help="contract expiry YYYYMM (else front month)")
    lv.set_defaults(func=_cmd_live)

    ft = sub.add_parser("fetch", help="download real bars from FMP or Alpaca to CSV")
    add_common(ft)
    ft.add_argument("--provider", choices=["alpaca", "fmp"], default="alpaca")
    ft.add_argument("--symbol", required=True, help="e.g. SPY, QQQ (or ESUSD for FMP futures)")
    ft.add_argument("--interval", default="1min", choices=["1min", "5min", "15min", "30min"])
    ft.add_argument("--from", default=None, help="start date YYYY-MM-DD")
    ft.add_argument("--to", default=None, help="end date YYYY-MM-DD")
    ft.add_argument(
        "--asset-class", default="stock",
        choices=["stock", "commodity", "index", "crypto", "forex"],
        help="FMP only: which endpoint family the symbol belongs to",
    )
    ft.add_argument("--out", required=True, help="output CSV path")
    ft.set_defaults(func=_cmd_fetch)

    gs = sub.add_parser("gen-sample", help="generate synthetic sample data")
    gs.add_argument("--out", default="tests/sample_data/es_sample.csv")
    gs.add_argument("--days", type=int, default=10)
    gs.add_argument("--seed", type=int, default=42)
    gs.set_defaults(func=_cmd_gen_sample)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        # Expected, user-facing failures (missing keys, bad params, API errors).
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
