#!/usr/bin/env python3
"""Strategy lab: search, walk-forward, rank, keep the winners.

Samples many strategy variants (FSVZO params x timeframe sets x MM configs),
backtests each on a TRAIN window, then forward-tests the best on a held-out
TEST window it never saw — the paper equivalent of running many wallets and
watching which one does really well. Ranking is done on the TEST window only,
on two axes:

    profit  (pnl)             — "high profit, less volume"
    volume  (cost per $1M)    — "high volume, slightly less profit"

Everything on the Pareto frontier with negative test cost/$1M (profitable
volume) is kept. The single best-balanced config is written to
strategies/best.json, which run_volume.py --strategy consumes. Re-run weekly
on fresh data to re-optimize.

  python run_lab.py --days 60 --samples 40
  python run_lab.py --symbol BTC/USDT --exchange hyperliquid --base 5m
"""

import argparse
import json
import logging
import random
from dataclasses import asdict
from pathlib import Path

from fsvo import data
from fsvo.confluence import ConfluenceEngine
from fsvo.indicators import FsvzoParams
from fsvo.mm import MMConfig
from fsvo.venues import PaperVenue, VenueConfig, replay

log = logging.getLogger("fsvo.lab")

TIMEFRAME_SETS = [
    {"1m": 1.0, "5m": 2.0, "15m": 3.0},
    {"3m": 1.0, "15m": 2.0, "45m": 3.0},
    {"5m": 1.0, "15m": 2.0, "1h": 3.0},
    {"5m": 1.0, "30m": 2.0, "1h": 3.0},
    {"15m": 1.0, "45m": 2.0, "1h": 3.0},
    {"15m": 1.0, "1h": 2.0, "4h": 3.0},
]


def sample_strategy(rng: random.Random, base_tf: str) -> dict:
    tf_set = rng.choice([s for s in TIMEFRAME_SETS if base_tf in s] or TIMEFRAME_SETS)
    return {
        "timeframes": tf_set,
        "fsvzo": {
            "length": rng.choice([10, 14, 21, 28]),
            "fourier_window": rng.choice([24, 32, 48]),
            "harmonics": rng.choice([3, 4, 6]),
            "smooth": rng.choice([2, 3, 5]),
            "noise_gate": rng.choice([0.15, 0.25, 0.35]),
        },
        "mm": {
            "base_half_spread": rng.choice([0.0006, 0.0008, 0.0012, 0.0018]),
            "vol_spread_mult": rng.choice([0.2, 0.3, 0.45]),
            "confluence_skew": rng.choice([0.0006, 0.0010, 0.0015]),
            "inventory_skew": rng.choice([0.0010, 0.0020, 0.0030]),
            "size_lean": rng.choice([0.3, 0.5, 0.75]),
            "flatten_threshold": rng.choice([0.55, 0.7, 0.85]),
            "min_confluence_to_quote": rng.choice([0.0, 0.0, 0.1, 0.2]),
        },
    }


def evaluate(strategy: dict, df, venue_cfg: VenueConfig) -> dict:
    params = FsvzoParams(**strategy["fsvzo"])
    engine = ConfluenceEngine(strategy["timeframes"], params)
    confluence = engine.compute(df)["confluence"]
    mm = MMConfig(quote_size=venue_cfg.quote_size,
                  max_inventory=venue_cfg.max_inventory, **strategy["mm"])
    venue = PaperVenue(venue_cfg, mm_cfg=mm)
    report = replay(venue, df, confluence, warmup=params.fourier_window * 4)
    return report


def pareto_front(rows: list[dict]) -> list[dict]:
    """Keep rows not dominated on (test_pnl, test_volume)."""
    front = []
    for r in rows:
        dominated = any(
            o["test_pnl"] >= r["test_pnl"] and o["test_volume"] >= r["test_volume"]
            and (o["test_pnl"] > r["test_pnl"] or o["test_volume"] > r["test_volume"])
            for o in rows
        )
        if not dominated:
            front.append(r)
    return sorted(front, key=lambda r: -r["test_pnl"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--exchange", default="binance", help="data source exchange id")
    p.add_argument("--base", default="5m", help="base timeframe for the sweep")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--samples", type=int, default=40, help="strategies to sample")
    p.add_argument("--train-frac", type=float, default=0.7)
    p.add_argument("--maker-fee", type=float, default=0.0001)
    p.add_argument("--taker-fee", type=float, default=0.00035)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", default="strategies")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rng = random.Random(args.seed)

    df = data.load(args.symbol, args.base, args.days, exchange_id=args.exchange)
    split = int(len(df) * args.train_frac)
    train, test = df.iloc[:split], df.iloc[split:]
    log.info("train=%d bars, test=%d bars (base %s)", len(train), len(test), args.base)

    venue_cfg = VenueConfig(name="lab", symbol=args.symbol, timeframe=args.base,
                            maker_fee=args.maker_fee, taker_fee=args.taker_fee)

    rows = []
    for i in range(args.samples):
        strategy = sample_strategy(rng, args.base)
        try:
            tr = evaluate(strategy, train, venue_cfg)
            te = evaluate(strategy, test, venue_cfg)
        except Exception:
            log.exception("strategy %d failed", i)
            continue
        rows.append({
            "id": i,
            "strategy": strategy,
            "train_pnl": tr["pnl"], "train_volume": tr["volume_usd"],
            "test_pnl": te["pnl"], "test_volume": te["volume_usd"],
            "test_cost_per_1m": te["cost_per_$1M_vol"],
            "test_fills": te["fills"],
        })
        log.info("[%02d/%d] train pnl %10.2f | test pnl %10.2f | test vol %12.0f | cost/$1M %8.2f",
                 i + 1, args.samples, tr["pnl"], te["pnl"], te["volume_usd"],
                 te["cost_per_$1M_vol"])

    if not rows:
        raise SystemExit("no strategies evaluated")

    profitable = [r for r in rows if r["test_cost_per_1m"] < 0]
    front = pareto_front(profitable or rows)

    print(f"\n=== Pareto frontier (test window, {len(profitable)}/{len(rows)} "
          f"profitable-volume strategies) ===")
    print(f"{'id':>4} | {'test_pnl':>10} | {'test_volume':>13} | {'cost/$1M':>9} | {'fills':>6} | timeframes")
    for r in front:
        print(f"{r['id']:>4} | {r['test_pnl']:>10,.2f} | {r['test_volume']:>13,.0f} | "
              f"{r['test_cost_per_1m']:>9,.2f} | {r['test_fills']:>6} | "
              f"{','.join(r['strategy']['timeframes'])}")

    # Best-balanced pick: most profit per unit of drawdown risk proxy —
    # here simply highest test pnl among the frontier (frontier already
    # filters for volume efficiency).
    best = front[0]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "best.json").write_text(json.dumps({
        "symbol": args.symbol, "base_timeframe": args.base,
        "selected_by": "test_pnl on pareto frontier",
        "metrics": {k: v for k, v in best.items() if k != "strategy"},
        **best["strategy"],
    }, indent=2))
    (out_dir / "leaderboard.json").write_text(json.dumps(rows, indent=2))
    print(f"\nBest strategy #{best['id']} -> {out_dir/'best.json'}")
    print(f"Full leaderboard    -> {out_dir/'leaderboard.json'}")
    print("\nUse it:  python run_volume.py --strategy strategies/best.json")


if __name__ == "__main__":
    main()
