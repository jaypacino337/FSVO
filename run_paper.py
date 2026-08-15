#!/usr/bin/env python3
"""Paper-trading loop: polls live candles, prints FSVZO states per timeframe,
the confluence score, and the quotes the MM would place. No orders are sent.

  python run_paper.py --symbol BTC/USDT --interval 60
"""

import argparse
import logging
import time

from fsvo import data
from fsvo.confluence import ConfluenceEngine
from fsvo.indicators import atr
from fsvo.mm import MMConfig, make_quote

log = logging.getLogger("fsvo.paper")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--days", type=int, default=30, help="history to bootstrap indicators")
    p.add_argument("--interval", type=int, default=60, help="poll seconds")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    engine = ConfluenceEngine({"15m": 1.0, "1h": 2.0, "4h": 3.0})
    mm_cfg = MMConfig()
    inventory = 0.0  # paper inventory; wire fills in when connecting an exchange

    while True:
        try:
            df = data.fetch_ccxt(args.symbol, args.timeframe, args.days)
        except Exception as exc:
            log.warning("fetch failed (%s); retrying in %ss", exc, args.interval)
            time.sleep(args.interval)
            continue

        conf = engine.compute(df)
        last = conf.iloc[-1]
        mid = float(df["close"].iloc[-1])
        atr_pct = float((atr(df) / df["close"]).iloc[-1])
        quote = make_quote(mid, atr_pct, inventory, float(last["confluence"]), mm_cfg)

        states = ", ".join(
            f"{tf}={last[f'state_{tf}']}" for tf in engine.timeframes
        )
        log.info("mid=%.2f | %s | confluence=%+.2f", mid, states, last["confluence"])
        if quote.flatten:
            log.info("  -> FLATTEN position")
        elif quote.bid_price or quote.ask_price:
            log.info("  -> quote bid %.2f x %.3f | ask %.2f x %.3f",
                     quote.bid_price or float("nan"), quote.bid_size,
                     quote.ask_price or float("nan"), quote.ask_size)
        else:
            log.info("  -> no quotes (chop / below confluence threshold)")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
