"""OHLCV data loading: exchange fetch via ccxt, CSV cache, synthetic fallback."""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("fsvo.data")

COLUMNS = ["open", "high", "low", "close", "volume"]


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    return df[COLUMNS].astype(float)


def save_csv(df: pd.DataFrame, path: str | Path) -> None:
    df.to_csv(path, index_label="timestamp")


def fetch_ccxt(symbol: str, timeframe: str, days: int, exchange_id: str = "binance") -> pd.DataFrame:
    """Fetch OHLCV history with ccxt (requires network access to the exchange)."""
    import ccxt  # imported lazily so the rest works without it

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    ms_per_candle = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - days * 86_400_000
    rows: list[list[float]] = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + ms_per_candle
        if len(batch) < 1000:
            break
    df = pd.DataFrame(rows, columns=["timestamp", *COLUMNS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").astype(float)


def synthetic_ohlcv(days: int = 60, timeframe_minutes: int = 15, seed: int = 7,
                    start_price: float = 50_000.0) -> pd.DataFrame:
    """Regime-switching random walk with volume tied to move size.

    Good enough to exercise the indicator/backtest pipeline offline; not a
    substitute for real market data.
    """
    rng = np.random.default_rng(seed)
    n = days * 24 * 60 // timeframe_minutes
    idx = pd.date_range("2025-01-01", periods=n, freq=f"{timeframe_minutes}min", tz="UTC")

    # drift regimes flip every ~1-2 weeks; vol regimes drift slowly
    regime_len = max(1, n // max(1, days // 10))
    drift = np.repeat(rng.normal(0, 0.00012, size=n // regime_len + 1), regime_len)[:n]
    vol = 0.0015 * np.exp(np.cumsum(rng.normal(0, 0.01, size=n)))
    vol = np.clip(vol, 0.0005, 0.01)

    rets = drift + rng.standard_t(df=4, size=n) * vol
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    span = np.abs(rets) * close + close * vol * rng.uniform(0.2, 1.0, size=n)
    high = np.maximum(open_, close) + span * rng.uniform(0.1, 0.6, size=n)
    low = np.minimum(open_, close) - span * rng.uniform(0.1, 0.6, size=n)
    volume = (np.abs(rets) / vol + rng.exponential(0.5, size=n)) * 100.0

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def load(symbol: str, timeframe: str, days: int, csv: str | None = None,
         cache_dir: str | Path = "data") -> pd.DataFrame:
    """CSV if given, else cache, else exchange fetch, else synthetic fallback."""
    if csv:
        return load_csv(csv)

    cache = Path(cache_dir) / f"{symbol.replace('/', '-')}_{timeframe}_{days}d.csv"
    if cache.exists():
        log.info("Loading cached data from %s", cache)
        return load_csv(cache)

    try:
        df = fetch_ccxt(symbol, timeframe, days)
        cache.parent.mkdir(parents=True, exist_ok=True)
        save_csv(df, cache)
        log.info("Fetched %d candles from exchange; cached to %s", len(df), cache)
        return df
    except Exception as exc:  # no network / ccxt missing — fall back
        log.warning("Exchange fetch failed (%s); using synthetic data", exc)
        tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(timeframe, 15)
        return synthetic_ohlcv(days=days, timeframe_minutes=tf_minutes)
