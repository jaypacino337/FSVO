"""FSVZO indicator stack.

Recreates the public structure of InSilico's Fourier Smoothed Volume Zone
Oscillator (FSVZO):

  1. Fourier low-pass smoothing of price (drives the up/down sign of volume)
  2. Volume Zone Oscillator (VZO) on the smoothed sign
  3. Ehlers-style white-noise gate to suppress chop signals
  4. Candle-state classification (the "candle indicator" coloring)

Conventions follow the common published levels: +/-15 trend zone edge,
+/-40 overbought/oversold, +/-60 extreme.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Oscillator levels
TREND_ZONE = 15.0
OB_OS = 40.0
EXTREME = 60.0


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def fourier_smooth(series: pd.Series, window: int = 32, harmonics: int = 4) -> pd.Series:
    """Low-pass smooth by keeping only the lowest `harmonics` DFT components
    of a rolling window, reconstructing the newest point each bar."""
    x = series.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) >= window:
        segs = np.lib.stride_tricks.sliding_window_view(x, window)
        spec = np.fft.rfft(segs, axis=1)
        spec[:, harmonics + 1 :] = 0.0
        out[window - 1 :] = np.fft.irfft(spec, n=window, axis=1)[:, -1]
    return pd.Series(out, index=series.index)


def ehlers_white_noise_ratio(series: pd.Series, decay: float = 0.991) -> pd.Series:
    """Normalized signal-to-peak ratio in [-1, 1].

    White noise is estimated as the two-bar midpoint change (Ehlers); the
    running peak envelope decays slowly so the ratio adapts to volatility.
    Values near 0 mean the move is indistinguishable from noise.
    """
    x = series.to_numpy(dtype=float)
    wn = np.zeros(len(x))
    wn[2:] = (x[2:] - x[:-2]) / 2.0
    ratio = np.zeros(len(x))
    peak = 1e-12
    for i in range(len(x)):
        peak = max(abs(wn[i]), peak * decay)
        ratio[i] = wn[i] / peak
    return pd.Series(ratio, index=series.index)


def vzo(close: pd.Series, volume: pd.Series, length: int = 14) -> pd.Series:
    """Volume Zone Oscillator (Khalil/Fahmy): 100 * EMA(signed vol) / EMA(vol)."""
    sign = np.sign(close.diff().fillna(0.0))
    vp = ema(pd.Series(sign.to_numpy() * volume.to_numpy(), index=close.index), length)
    tv = ema(volume, length)
    return 100.0 * vp / tv.replace(0.0, np.nan)


@dataclass
class FsvzoParams:
    length: int = 14
    fourier_window: int = 32
    harmonics: int = 4
    smooth: int = 3
    noise_gate: float = 0.25  # |white-noise ratio| below this = chop


def fsvzo(df: pd.DataFrame, params: FsvzoParams = FsvzoParams()) -> pd.DataFrame:
    """Compute the oscillator and candle states for an OHLCV frame.

    Expects columns: open, high, low, close, volume. Returns a frame with
    fsvzo, rising, noise_ratio, gated, state columns aligned to df.index.
    """
    smoothed_close = fourier_smooth(df["close"], params.fourier_window, params.harmonics)
    osc = vzo(smoothed_close, df["volume"], params.length)
    osc = ema(osc, params.smooth)
    noise = ehlers_white_noise_ratio(smoothed_close)

    rising = osc.diff() > 0
    gated = noise.abs() < params.noise_gate

    out = pd.DataFrame(
        {
            "fsvzo": osc,
            "rising": rising,
            "noise_ratio": noise,
            "gated": gated,
        },
        index=df.index,
    )
    out["state"] = classify_states(out)
    return out


# Candle states, mirroring the bi-color band + OB/OS bands of the original.
STATES = (
    "strong_bull",  # rising, above trend zone, not gated
    "bull",         # rising
    "bear",         # falling
    "strong_bear",  # falling, below -trend zone, not gated
    "overbought",   # above +40 (reversal risk for longs)
    "oversold",     # below -40 (reversal risk for shorts)
    "neutral",      # noise-gated chop
)


def classify_states(ind: pd.DataFrame) -> pd.Series:
    osc, rising, gated = ind["fsvzo"], ind["rising"], ind["gated"]
    state = pd.Series("neutral", index=ind.index)
    state[rising] = "bull"
    state[~rising] = "bear"
    state[rising & (osc > TREND_ZONE)] = "strong_bull"
    state[~rising & (osc < -TREND_ZONE)] = "strong_bear"
    state[osc >= OB_OS] = "overbought"
    state[osc <= -OB_OS] = "oversold"
    state[gated] = "neutral"
    state[osc.isna()] = "neutral"
    return state


def state_direction(state: str, osc: float) -> float:
    """Directional bias in [-1, 1] for a single candle state.

    Trend states push with the trend; OB/OS lean *against* continuation
    (mean-reversion), scaled by how far past the band the oscillator is.
    """
    if state == "strong_bull":
        return 1.0
    if state == "bull":
        return 0.5
    if state == "bear":
        return -0.5
    if state == "strong_bear":
        return -1.0
    if state == "overbought":
        return -min(1.0, (osc - OB_OS) / (EXTREME - OB_OS)) * 0.75
    if state == "oversold":
        return min(1.0, (-osc - OB_OS) / (EXTREME - OB_OS)) * 0.75
    return 0.0


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return ema(tr, length)
