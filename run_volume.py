#!/usr/bin/env python3
"""Multi-venue volume campaign runner.

Default mode replays each configured venue over history (real data via ccxt
when reachable, synthetic otherwise) with the volume-maximizing MM profile
and reports the numbers that matter for a points/volume campaign:

    volume generated, fees paid, PnL, and net cost per $1M of volume.

Live mode (--live) polls each venue, cancels stale quotes, and re-places
post-only bid/ask pairs sized by the FSVZO confluence. It requires per-venue
API keys in the environment and should be started with tiny sizes.

  python run_volume.py                          # replay, venues.json or demo
  python run_volume.py --config venues.json --days 60
  python run_volume.py --live --interval 30     # EXPERIMENTAL
  python run_volume.py --signal-file signals.json   # use YOUR indicator feed
"""

import argparse
import json
import logging
import time
from pathlib import Path

from fsvo import data
from fsvo.confluence import ConfluenceEngine
from fsvo.indicators import FsvzoParams, atr
from fsvo.mm import MMConfig, make_quote
from fsvo.signals import ExternalSignal
from fsvo.venues import LiveVenue, PaperVenue, VenueConfig, replay

log = logging.getLogger("fsvo.volume")

DEFAULT_TIMEFRAMES = {"15m": 1.0, "1h": 2.0, "4h": 3.0}


def load_strategy(path: str | None) -> dict | None:
    """Optional strategy file from run_lab.py (strategies/best.json)."""
    if not path:
        return None
    strategy = json.loads(Path(path).read_text())
    log.info("using lab strategy from %s (timeframes: %s)",
             path, ",".join(strategy["timeframes"]))
    return strategy


def build_engine(timeframes: dict[str, float], strategy: dict | None) -> ConfluenceEngine:
    if strategy:
        return ConfluenceEngine(strategy["timeframes"], FsvzoParams(**strategy["fsvzo"]))
    return ConfluenceEngine(timeframes)


def build_mm(v_cfg: VenueConfig, strategy: dict | None) -> MMConfig:
    if strategy:
        return MMConfig(quote_size=v_cfg.quote_size,
                        max_inventory=v_cfg.max_inventory, **strategy["mm"])
    return MMConfig.for_volume(v_cfg.maker_fee, v_cfg.quote_size, v_cfg.max_inventory)


def load_config(path: str | None) -> tuple[list[VenueConfig], dict[str, float]]:
    if path is None:
        for candidate in ("venues.json", "venues.example.json"):
            if Path(candidate).exists():
                path = candidate
                break
    if path is None:
        return [VenueConfig(name="demo_dex")], DEFAULT_TIMEFRAMES

    raw = json.loads(Path(path).read_text())
    venues = [
        VenueConfig(**{k: v for k, v in v_cfg.items() if not k.startswith("_")})
        for v_cfg in raw["venues"]
    ]
    return venues, raw.get("timeframes", DEFAULT_TIMEFRAMES)


def replay_mode(venues: list[VenueConfig], timeframes: dict[str, float],
                days: int, strategy: dict | None = None) -> None:
    engine = build_engine(timeframes, strategy)
    reports = []
    for v_cfg in venues:
        if v_cfg.exchange_id:
            df = data.load(v_cfg.symbol, v_cfg.timeframe, days,
                           exchange_id=v_cfg.exchange_id)
        else:
            df = data.synthetic_ohlcv(days=days, seed=hash(v_cfg.name) % 2**31)
        confluence = engine.compute(df)["confluence"]
        venue = PaperVenue(v_cfg, mm_cfg=build_mm(v_cfg, strategy))
        reports.append(replay(venue, df, confluence))

    print(f"\n{'venue':>12} | {'volume_usd':>14} | {'fills':>6} | {'fees':>10} | "
          f"{'pnl':>10} | {'cost/$1M':>9} | {'inv':>8}")
    print("-" * 84)
    total_vol = total_pnl = 0.0
    for r in reports:
        print(f"{r['venue']:>12} | {r['volume_usd']:>14,.0f} | {r['fills']:>6} | "
              f"{r['fees']:>10,.2f} | {r['pnl']:>10,.2f} | "
              f"{r['cost_per_$1M_vol']:>9,.2f} | {r['ending_inventory']:>8}")
        total_vol += r["volume_usd"]
        total_pnl += r["pnl"]
    cost = -total_pnl / total_vol * 1e6 if total_vol else 0.0
    print("-" * 84)
    print(f"{'TOTAL':>12} | {total_vol:>14,.0f} | {'':>6} | {'':>10} | "
          f"{total_pnl:>10,.2f} | {cost:>9,.2f} |")
    print("\ncost/$1M = net dollars burned per $1M volume (negative = profitable volume)")


def live_mode(venues: list[VenueConfig], timeframes: dict[str, float],
              interval: int, signal_file: str | None,
              strategy: dict | None = None) -> None:
    engine = build_engine(timeframes, strategy)
    external = ExternalSignal(signal_file, timeframes) if signal_file else None
    live = {}
    for v_cfg in venues:
        if not v_cfg.exchange_id:
            log.warning("skipping %s (no exchange_id, paper-only venue)", v_cfg.name)
            continue
        live[v_cfg.name] = LiveVenue(v_cfg)

    if not live:
        raise SystemExit("no live-capable venues configured")

    log.warning("LIVE MODE: post-only quoting on %s — start with tiny sizes.",
                ", ".join(live))
    while True:
        for name, venue in live.items():
            try:
                df = venue.fetch_ohlcv_df()
                score = (external.latest() if external
                         else float(engine.compute(df)["confluence"].iloc[-1]))
                mid = float(df["close"].iloc[-1])
                atr_pct = float((atr(df) / df["close"]).iloc[-1])
                inventory = venue.inventory()
                cfg = venue.cfg
                # Funding bias: positive funding (longs pay) pushes the
                # confluence down so inventory drifts to the collecting side.
                funding = venue.funding_rate()
                score = max(-1.0, min(1.0, score - funding * cfg.funding_bias_mult))
                quote = make_quote(mid, atr_pct, inventory, score,
                                   build_mm(cfg, strategy))
                venue.cancel_all()
                if quote.flatten:
                    log.warning("%s: confluence flipped hard against inventory %.4f "
                                "— quotes pulled, flatten manually or wait for skew",
                                name, inventory)
                else:
                    venue.place_quotes(quote)
                    log.info("%s: mid=%.2f conf=%+.2f inv=%.4f bid=%s ask=%s",
                             name, mid, score, inventory,
                             f"{quote.bid_price:.2f}x{quote.bid_size}" if quote.bid_price else "-",
                             f"{quote.ask_price:.2f}x{quote.ask_size}" if quote.ask_price else "-")
            except Exception:
                log.exception("%s: cycle failed", name)
        time.sleep(interval)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="venues json (default: venues.json / example)")
    p.add_argument("--days", type=int, default=60, help="replay history length")
    p.add_argument("--live", action="store_true", help="place real post-only orders")
    p.add_argument("--interval", type=int, default=30, help="live poll seconds")
    p.add_argument("--signal-file", help="consume your indicator via signals.json "
                                         "(see run_signal_server.py)")
    p.add_argument("--strategy", help="strategy json from run_lab.py "
                                      "(e.g. strategies/best.json)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    venues, timeframes = load_config(args.config)
    strategy = load_strategy(args.strategy)

    if args.live:
        live_mode(venues, timeframes, args.interval, args.signal_file, strategy)
    else:
        replay_mode(venues, timeframes, args.days, strategy)


if __name__ == "__main__":
    main()
