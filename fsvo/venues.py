"""Perp-DEX venue layer.

VenueConfig captures the per-venue economics that decide whether volume is
cheap there (maker fee/rebate, taker fee, min size). PaperVenue simulates
maker fills candle-by-candle and tracks the numbers that matter for a
points/volume campaign: notional volume, fees, PnL, and cost per $1M volume.

Live trading: LiveVenue wraps ccxt with the same interface. It only ever
places post-only limit orders (maker), reads keys from environment variables
(FSVO_<NAME>_KEY / FSVO_<NAME>_SECRET), and must be enabled explicitly with
--live. It never self-matches: one account, one side resting per price level,
orders cross the public book only.
"""

from dataclasses import dataclass, field

import pandas as pd

from .indicators import atr
from .mm import MMConfig, Quote, make_quote


@dataclass
class VenueConfig:
    name: str
    symbol: str = "BTC/USDT"
    exchange_id: str | None = None   # ccxt id for live/data; None = synthetic
    maker_fee: float = 0.0002        # negative = rebate
    taker_fee: float = 0.0005
    quote_size: float = 0.05         # per-quote size in base units
    max_inventory: float = 0.5
    timeframe: str = "15m"


@dataclass
class VenueStats:
    volume_base: float = 0.0
    volume_usd: float = 0.0
    fills: int = 0
    fees: float = 0.0
    realized_flattens: int = 0

    def cost_per_million(self, pnl: float) -> float:
        """Net cost (negative pnl) per $1M of volume generated. Negative
        values mean the volume was generated at a profit."""
        if self.volume_usd == 0:
            return 0.0
        return -pnl / self.volume_usd * 1_000_000.0


class PaperVenue:
    """Simulated venue: same fill rules as the backtester, per-venue fees."""

    def __init__(self, cfg: VenueConfig, mm_cfg: MMConfig | None = None,
                 initial_cash: float = 100_000.0):
        self.cfg = cfg
        self.mm_cfg = mm_cfg or MMConfig.for_volume(
            cfg.maker_fee, cfg.quote_size, cfg.max_inventory
        )
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.inventory = 0.0
        self.stats = VenueStats()
        self.last_close: float | None = None

    def step(self, bar: pd.Series, atr_pct: float, confluence: float) -> Quote:
        """Advance one candle: quote off the previous close, fill against
        this bar's range. `bar` needs open/high/low/close."""
        if self.last_close is None:
            self.last_close = float(bar["close"])
            return Quote(None, 0.0, None, 0.0)

        quote = make_quote(self.last_close, atr_pct, self.inventory,
                           confluence, self.mm_cfg)

        if quote.flatten and self.inventory != 0.0:
            px = float(bar["open"])
            self._fill(-self.inventory, px, self.cfg.taker_fee)
            self.stats.realized_flattens += 1
        else:
            if quote.bid_price is not None and float(bar["low"]) <= quote.bid_price:
                self._fill(quote.bid_size, quote.bid_price, self.cfg.maker_fee)
            if quote.ask_price is not None and float(bar["high"]) >= quote.ask_price:
                self._fill(-quote.ask_size, quote.ask_price, self.cfg.maker_fee)

        self.last_close = float(bar["close"])
        return quote

    def _fill(self, signed_size: float, price: float, fee_rate: float) -> None:
        notional = abs(signed_size) * price
        fee = notional * fee_rate
        self.cash -= signed_size * price + fee
        self.inventory += signed_size
        self.stats.fills += 1
        self.stats.volume_base += abs(signed_size)
        self.stats.volume_usd += notional
        self.stats.fees += fee

    @property
    def equity(self) -> float:
        mark = self.last_close or 0.0
        return self.cash + self.inventory * mark

    @property
    def pnl(self) -> float:
        return self.equity - self.initial_cash

    def report(self) -> dict:
        return {
            "venue": self.cfg.name,
            "volume_usd": round(self.stats.volume_usd, 0),
            "fills": self.stats.fills,
            "fees": round(self.stats.fees, 2),
            "pnl": round(self.pnl, 2),
            "cost_per_$1M_vol": round(self.stats.cost_per_million(self.pnl), 2),
            "ending_inventory": round(self.inventory, 4),
        }


class LiveVenue:
    """ccxt-backed live venue (EXPERIMENTAL — start tiny and watch it).

    Maker-only by construction: every order is a post-only limit, so it can
    never cross the spread, never self-match, and always pays the maker fee
    (or earns the rebate). Keys come from FSVO_<NAME>_KEY / FSVO_<NAME>_SECRET
    (and optional FSVO_<NAME>_WALLET for wallet-auth DEXes like hyperliquid).
    """

    def __init__(self, cfg: VenueConfig):
        import os

        import ccxt

        self.cfg = cfg
        prefix = f"FSVO_{cfg.name.upper()}"
        params = {
            "apiKey": os.getenv(f"{prefix}_KEY"),
            "secret": os.getenv(f"{prefix}_SECRET"),
            "enableRateLimit": True,
        }
        wallet = os.getenv(f"{prefix}_WALLET")
        if wallet:
            params["walletAddress"] = wallet
        self.exchange = getattr(ccxt, cfg.exchange_id)(params)
        self.stats = VenueStats()

    def fetch_ohlcv_df(self, limit: int = 500) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(self.cfg.symbol, self.cfg.timeframe, limit=limit)
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").astype(float)

    def cancel_all(self) -> None:
        for order in self.exchange.fetch_open_orders(self.cfg.symbol):
            self.exchange.cancel_order(order["id"], self.cfg.symbol)

    def place_quotes(self, quote: Quote) -> None:
        params = {"postOnly": True}
        if quote.bid_price is not None and quote.bid_size > 0:
            self.exchange.create_order(self.cfg.symbol, "limit", "buy",
                                       quote.bid_size, quote.bid_price, params)
        if quote.ask_price is not None and quote.ask_size > 0:
            self.exchange.create_order(self.cfg.symbol, "limit", "sell",
                                       quote.ask_size, quote.ask_price, params)

    def inventory(self) -> float:
        try:
            positions = self.exchange.fetch_positions([self.cfg.symbol])
        except Exception:
            return 0.0
        for pos in positions:
            if pos.get("symbol") == self.cfg.symbol:
                size = float(pos.get("contracts") or 0.0)
                return size if pos.get("side") != "short" else -size
        return 0.0


def replay(venue: PaperVenue, df: pd.DataFrame, confluence: pd.Series,
           warmup: int = 128) -> dict:
    """Run a venue over a full OHLCV history with a precomputed confluence
    series (lookahead-safe: step i uses confluence[i-1])."""
    atr_pct = (atr(df) / df["close"]).to_numpy()
    conf = confluence.to_numpy()
    for i in range(len(df)):
        if i < warmup or not pd.notna(atr_pct[i - 1]):
            venue.last_close = float(df["close"].iloc[i])
            continue
        venue.step(df.iloc[i], float(atr_pct[i - 1]), float(conf[i - 1]))
    return venue.report()
