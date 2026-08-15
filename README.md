# FSVO — FSVZO Confluence Market Maker

A Python trading system built around a recreation of InSilico's **FSVZO**
(Fourier Smoothed Volume Zone Oscillator) and its candle-state coloring. It
computes FSVZO on several timeframes, blends them into a **confluence score**,
and runs a **market-maker style** engine that quotes a bid/ask skewed by that
score — accumulating inventory in the direction the timeframes agree on and
standing down (or flattening) when they don't.

> ⚠️ Educational tooling, not financial advice. Everything here is
> backtest/paper-trade only — no order placement is wired to any exchange.

## How it works

### 1. FSVZO indicator (`fsvo/indicators.py`)

Recreated from the publicly documented structure:

- **Fourier smoothing** — price is low-pass filtered by keeping only the
  lowest harmonics of a rolling DFT window, which drives the up/down sign
  used for volume.
- **Volume Zone Oscillator** — `100 * EMA(signed volume) / EMA(volume)`,
  bounded roughly ±100. Standard levels: ±15 trend zone, ±40 OB/OS, ±60 extreme.
- **Ehlers white-noise gate** — a normalized signal-to-peak ratio; when the
  smoothed price move is indistinguishable from noise the bar is gated to
  `neutral` (this is what keeps the candle coloring quiet in chop).

### 2. Candle states (the "candle indicator")

Each bar is classified like the bi-color band / candle coloring in the
terminal: `strong_bull`, `bull`, `bear`, `strong_bear`, `overbought`,
`oversold`, `neutral`. Trend states carry a directional bias with the trend;
OB/OS states lean *against* continuation (mean reversion), scaled by how deep
past the band the oscillator is.

### 3. Multi-timeframe confluence (`fsvo/confluence.py`)

One base feed (default 15m) is resampled to 15m / 1h / 4h. Each timeframe's
candle state maps to a direction in [-1, +1]; the weighted blend (higher
timeframes weigh more) is the **confluence score**:

```
+1   all timeframes bullish      → lean quotes long
 0   mixed / gated chop          → stand down
-1   all timeframes bearish      → lean quotes short
```

Lookahead-safe: a higher-timeframe candle's state only becomes visible to the
base timeframe after that candle closes.

### 4. Market maker (`fsvo/mm.py`)

- Half-spread = floor + multiple of ATR% (backs off in fast markets)
- Quotes are **skewed** by confluence (buy closer / sell further when bullish)
  and counter-skewed by inventory so the position mean-reverts to target
- Bid/ask **sizes lean** toward the favored side; a side is pulled at the
  inventory cap
- Below a minimum |confluence| the MM doesn't quote at all; if strong
  confluence flips against the position, it **flattens** at market

## Quick start

```bash
pip install -r requirements.txt

# offline demo (synthetic data — a plumbing check, not an edge claim)
python run_backtest.py

# real data (needs network + ccxt): 90 days of BTC/USDT from Binance
python run_backtest.py --symbol BTC/USDT --days 90

# your own OHLCV csv (columns: timestamp,open,high,low,close,volume)
python run_backtest.py --csv data/mydata.csv

# live paper loop: prints per-TF states, confluence and the quotes it would place
python run_paper.py --symbol BTC/USDT --interval 60
```

The backtest prints a summary (return, max drawdown, Sharpe, fills, fees) and
writes `data/backtest_equity.csv` / `data/backtest_fills.csv`.

## Volume campaign mode (`run_volume.py`)

For points/volume programs on perp DEXes, the objective flips: maximize
**real maker volume per dollar of cost** while the FSVZO confluence keeps
inventory drifting with the trend instead of bleeding against it.
`MMConfig.for_volume(maker_fee)` pins the spread floor just above round-trip
maker fees and quotes both sides continuously within inventory caps.

```bash
cp venues.example.json venues.json   # edit venues, fees, sizes

python run_volume.py                 # replay: volume, fees, pnl, cost/$1M per venue
python run_volume.py --live          # EXPERIMENTAL: real post-only quoting
python run_volume.py --live --signal-file signals.json   # driven by YOUR indicator
```

To drive it with your licensed FSVZO instead of the built-in recreation, run
`python run_signal_server.py` and point the terminal/TradingView alerts at
`POST /signal` with `{"timeframe": "1h", "state": "strong_bull"}` (or a raw
`direction` in [-1, 1]). Stale timeframes decay to neutral automatically.

The replay's `cost/$1M` column is the campaign metric: net dollars burned per
$1M of volume generated (negative = the volume paid for itself).

**Hard rules built into the live path:** post-only orders only (never crosses
the spread, never self-matches), per-venue inventory caps, quotes pulled when
confluence flips hard against the position. Wash trading — trading against
your own orders to print volume — is deliberately not supported: it's market
manipulation and the reliable way to get zeroed out of a points program.
Check each venue's program rules before pointing size at it.

## Strategy lab (`run_lab.py`)

The lab is the "many wallets" idea done as parallel simulations: it samples
dozens of strategy variants (FSVZO params x timeframe sets from 1m to 4h x
quoting configs), backtests each on a train window, forward-tests the
survivors on a held-out test window, and keeps the Pareto frontier of
profit vs volume — everything with **negative cost/$1M** (volume that pays
for itself).

```bash
python run_lab.py --days 60 --samples 40 --base 5m
python run_volume.py --strategy strategies/best.json    # deploy the winner
```

Re-run weekly on fresh data to re-optimize. Only promote a strategy whose
TEST metrics (not train) stay profitable across re-runs — a config that only
wins on the window it was tuned on is overfit and will burn money live.

## Tuning

- `FsvzoParams` (`fsvo/indicators.py`): VZO length, Fourier window/harmonics,
  noise gate threshold.
- `BacktestConfig.timeframes` (`fsvo/backtest.py`): timeframe → weight map.
- `MMConfig` (`fsvo/mm.py`): spreads, skews, quote size, inventory cap,
  flatten threshold, minimum confluence to quote.
- Fees: `BacktestConfig.maker_fee` / `taker_fee`.

## Honest limitations

- The fill model (bid fills when the bar's low trades through it) ignores
  queue position and adverse selection — real maker fills are worse. Treat
  backtest numbers as *signal quality* evidence, not live PnL estimates.
- FSVZO's exact production formula is proprietary; this follows the published
  structure (VZO + Fourier smoothing + Ehlers noise filter + standard levels).
- Synthetic data is for pipeline testing only; always validate on real data.
