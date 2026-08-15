"""Smoke tests: run with `python -m pytest` or `python tests/test_smoke.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fsvo import backtest, data
from fsvo.confluence import ConfluenceEngine
from fsvo.indicators import STATES, FsvzoParams, fsvzo
from fsvo.mm import MMConfig, make_quote


def _df():
    return data.synthetic_ohlcv(days=20, timeframe_minutes=15, seed=1)


def test_fsvzo_bounded_and_classified():
    ind = fsvzo(_df(), FsvzoParams())
    osc = ind["fsvzo"].dropna()
    assert len(osc) > 0
    assert osc.abs().max() <= 100.5
    assert set(ind["state"].unique()) <= set(STATES)


def test_confluence_in_range():
    conf = ConfluenceEngine({"15m": 1.0, "1h": 2.0, "4h": 3.0}).compute(_df())
    assert conf["confluence"].between(-1.0, 1.0).all()


def test_quote_respects_inventory_cap_and_flatten():
    cfg = MMConfig()
    q = make_quote(mid=100.0, atr_pct=0.002, inventory=cfg.max_inventory,
                   confluence=0.5, cfg=cfg)
    assert q.bid_price is None  # capped long: no more bids
    q = make_quote(mid=100.0, atr_pct=0.002, inventory=0.2,
                   confluence=-0.9, cfg=cfg)
    assert q.flatten  # strong opposing confluence flattens
    q = make_quote(mid=100.0, atr_pct=0.002, inventory=0.0, confluence=0.5, cfg=cfg)
    assert q.bid_price is not None and q.ask_price is not None
    assert q.bid_price < 100.0 < q.ask_price
    assert q.bid_size > q.ask_size  # sizes lean bullish


def test_backtest_runs():
    result = backtest.run(_df())
    assert len(result.equity) > 0
    assert "total_return" in result.summary


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name}: OK")
