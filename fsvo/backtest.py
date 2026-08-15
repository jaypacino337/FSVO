"""Candle-driven backtest of the FSVZO confluence market maker.

Per base-timeframe bar:
  1. quotes are built from data available at the *previous* bar close
     (indicators, mid, ATR, inventory) — no lookahead
  2. during the bar, the bid fills if the bar's low trades through it and
     the ask fills if the high trades through it (maker fills)
  3. a flatten signal closes the whole position at the bar open (taker)

This intentionally simplifies queue position and partial fills; it is a
signal-quality harness, not an execution simulator.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .confluence import ConfluenceEngine
from .indicators import FsvzoParams, atr
from .mm import MMConfig, make_quote


@dataclass
class BacktestConfig:
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    initial_cash: float = 100_000.0
    mm: MMConfig = field(default_factory=MMConfig)
    fsvzo: FsvzoParams = field(default_factory=FsvzoParams)
    timeframes: dict[str, float] = field(default_factory=lambda: {"15m": 1.0, "1h": 2.0, "4h": 3.0})


@dataclass
class BacktestResult:
    equity: pd.Series
    fills: pd.DataFrame
    summary: dict

    def print_summary(self) -> None:
        for k, v in self.summary.items():
            print(f"  {k:>22}: {v}")


def run(df: pd.DataFrame, cfg: BacktestConfig = BacktestConfig()) -> BacktestResult:
    engine = ConfluenceEngine(cfg.timeframes, cfg.fsvzo)
    conf = engine.compute(df)
    atr_pct = (atr(df) / df["close"]).to_numpy()
    confluence = conf["confluence"].to_numpy()
    open_, high = df["open"].to_numpy(), df["high"].to_numpy()
    low, close = df["low"].to_numpy(), df["close"].to_numpy()

    cash, inventory = cfg.initial_cash, 0.0
    equity = np.full(len(df), np.nan)
    fills: list[dict] = []
    warmup = cfg.fsvzo.fourier_window * 4

    for i in range(1, len(df)):
        if i < warmup or not np.isfinite(atr_pct[i - 1]):
            equity[i] = cash + inventory * close[i]
            continue

        # Quote off information known at the close of bar i-1.
        quote = make_quote(
            mid=close[i - 1],
            atr_pct=atr_pct[i - 1],
            inventory=inventory,
            confluence=confluence[i - 1],
            cfg=cfg.mm,
        )
        ts = df.index[i]

        if quote.flatten and inventory != 0.0:
            px = open_[i]
            fee = abs(inventory) * px * cfg.taker_fee
            cash += inventory * px - fee
            fills.append({"timestamp": ts, "side": "flatten", "price": px,
                          "size": -inventory, "fee": fee})
            inventory = 0.0
        else:
            if quote.bid_price is not None and low[i] <= quote.bid_price:
                fee = quote.bid_size * quote.bid_price * cfg.maker_fee
                cash -= quote.bid_size * quote.bid_price + fee
                inventory += quote.bid_size
                fills.append({"timestamp": ts, "side": "buy", "price": quote.bid_price,
                              "size": quote.bid_size, "fee": fee})
            if quote.ask_price is not None and high[i] >= quote.ask_price:
                fee = quote.ask_size * quote.ask_price * cfg.maker_fee
                cash += quote.ask_size * quote.ask_price - fee
                inventory -= quote.ask_size
                fills.append({"timestamp": ts, "side": "sell", "price": quote.ask_price,
                              "size": quote.ask_size, "fee": fee})

        equity[i] = cash + inventory * close[i]

    equity_s = pd.Series(equity, index=df.index).dropna()
    fills_df = pd.DataFrame(fills)
    ret = equity_s.iloc[-1] / cfg.initial_cash - 1.0
    drawdown = (equity_s / equity_s.cummax() - 1.0).min()
    per_bar = equity_s.pct_change().dropna()
    bars_per_year = pd.Timedelta(days=365) / (df.index[1] - df.index[0])
    sharpe = (per_bar.mean() / per_bar.std() * np.sqrt(bars_per_year)) if per_bar.std() > 0 else 0.0

    summary = {
        "bars": len(df),
        "fills": len(fills_df),
        "buys": int((fills_df["side"] == "buy").sum()) if len(fills_df) else 0,
        "sells": int((fills_df["side"] == "sell").sum()) if len(fills_df) else 0,
        "flattens": int((fills_df["side"] == "flatten").sum()) if len(fills_df) else 0,
        "fees_paid": round(float(fills_df["fee"].sum()), 2) if len(fills_df) else 0.0,
        "final_equity": round(float(equity_s.iloc[-1]), 2),
        "total_return": f"{ret:+.2%}",
        "max_drawdown": f"{drawdown:.2%}",
        "sharpe(annualized)": round(float(sharpe), 2),
        "ending_inventory": round(inventory, 4),
    }
    return BacktestResult(equity=equity_s, fills=fills_df, summary=summary)
