"""Signal sources for the MM engine.

Two ways to get a confluence score:

  * ComputedSignal — runs the built-in FSVZO recreation on an OHLCV feed
    (fallback when the licensed indicator isn't wired in).
  * ExternalSignal — reads the score your own FSVZO posts from the terminal /
    TradingView via webhook alerts (see run_signal_server.py). Per-timeframe
    directions are blended with the same weights as the computed engine, and
    stale signals decay to 0 so the MM stands down if the feed dies.
"""

import json
import time
from pathlib import Path

import pandas as pd

from .confluence import ConfluenceEngine


class ComputedSignal:
    def __init__(self, timeframes: dict[str, float]):
        self.engine = ConfluenceEngine(timeframes)

    def latest(self, df: pd.DataFrame) -> float:
        return float(self.engine.compute(df)["confluence"].iloc[-1])


class ExternalSignal:
    """Blends per-timeframe directions posted to a state file by the webhook
    server. Each entry: {"direction": -1..1, "ts": unix_seconds}."""

    def __init__(self, state_file: str | Path, timeframes: dict[str, float],
                 max_age_s: float = 3 * 3600):
        self.state_file = Path(state_file)
        self.timeframes = timeframes
        self.max_age_s = max_age_s

    def latest(self, df: pd.DataFrame | None = None) -> float:
        if not self.state_file.exists():
            return 0.0
        try:
            state = json.loads(self.state_file.read_text())
        except (json.JSONDecodeError, OSError):
            return 0.0
        now = time.time()
        total_w = sum(self.timeframes.values())
        score = 0.0
        for tf, weight in self.timeframes.items():
            entry = state.get(tf)
            if not entry or now - entry.get("ts", 0) > self.max_age_s:
                continue
            direction = max(-1.0, min(1.0, float(entry.get("direction", 0.0))))
            score += direction * weight / total_w
        return max(-1.0, min(1.0, score))
