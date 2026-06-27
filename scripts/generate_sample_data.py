#!/usr/bin/env python3
"""Generate synthetic sample ES bars. Thin wrapper around orb_bot.sample_data.

    python scripts/generate_sample_data.py --out tests/sample_data/es_sample.csv --days 10
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orb_bot.sample_data import generate  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="tests/sample_data/es_sample.csv")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    path = generate(args.out, days=args.days, seed=args.seed)
    print(f"Wrote {args.days} session(s) to {path}")


if __name__ == "__main__":
    main()
