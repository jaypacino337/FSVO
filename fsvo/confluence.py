"""Multi-timeframe FSVZO confluence.

Computes the FSVZO candle state on several timeframes (resampled from one
base-timeframe OHLCV feed so everything stays consistent and lookahead-free)
and blends them into a single confluence score in [-1, 1]:

  +1  = every timeframe agrees bullish
   0  = mixed / chop
  -1  = every timeframe agrees bearish

Higher timeframes get higher weights by default, matching how the indicator
is used discretionarily: the HTF sets the bias, the LTF times entries.
"""

import pandas as pd

from .indicators import FsvzoParams, fsvzo, state_direction

PANDAS_FREQ = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "45m": "45min",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1D",
}


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    freq = PANDAS_FREQ[timeframe]
    out = df.resample(freq).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


class ConfluenceEngine:
    def __init__(
        self,
        timeframes: dict[str, float],
        params: FsvzoParams = FsvzoParams(),
    ):
        """timeframes maps timeframe string -> weight, e.g. {"15m": 1, "1h": 2, "4h": 3}."""
        self.timeframes = timeframes
        self.params = params

    def compute(self, base_df: pd.DataFrame) -> pd.DataFrame:
        """Return a frame indexed like base_df with per-TF direction columns
        and a blended `confluence` score.

        Lookahead safety: each higher-timeframe candle's state is only
        applied to base bars *after* that candle closes (shift(1) before
        forward-filling onto the base index).
        """
        total_weight = sum(self.timeframes.values())
        result = pd.DataFrame(index=base_df.index)
        score = pd.Series(0.0, index=base_df.index)

        for tf, weight in self.timeframes.items():
            tf_df = resample_ohlcv(base_df, tf)
            ind = fsvzo(tf_df, self.params)
            direction = pd.Series(
                [state_direction(s, o) for s, o in zip(ind["state"], ind["fsvzo"].fillna(0.0))],
                index=ind.index,
            )
            # only completed HTF candles are visible to the base timeframe
            aligned = direction.shift(1).reindex(base_df.index, method="ffill").fillna(0.0)
            result[f"dir_{tf}"] = aligned
            result[f"state_{tf}"] = (
                ind["state"].shift(1).reindex(base_df.index, method="ffill").fillna("neutral")
            )
            score = score + aligned * (weight / total_weight)

        result["confluence"] = score.clip(-1.0, 1.0)
        return result
