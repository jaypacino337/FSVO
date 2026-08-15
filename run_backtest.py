#!/usr/bin/env python3
"""Backtest the FSVZO confluence market maker.

Examples:
  python run_backtest.py                          # synthetic data (offline)
  python run_backtest.py --symbol BTC/USDT --days 90
  python run_backtest.py --csv data/mydata.csv
"""

import argparse
import logging
from pathlib import Path

from fsvo import backtest, data


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="15m", help="base timeframe (LTF)")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--csv", help="load OHLCV from CSV instead of fetching")
    p.add_argument("--out", default="data/backtest", help="output prefix for reports")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    df = data.load(args.symbol, args.timeframe, args.days, csv=args.csv)
    print(f"\nBacktesting {args.symbol} on {len(df)} x {args.timeframe} candles "
          f"({df.index[0]} .. {df.index[-1]})\n")

    result = backtest.run(df)
    result.print_summary()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(f"{out}_equity.csv", header=["equity"], index_label="timestamp")
    if len(result.fills):
        result.fills.to_csv(f"{out}_fills.csv", index=False)
    print(f"\nReports written to {out}_equity.csv / {out}_fills.csv")


if __name__ == "__main__":
    main()
