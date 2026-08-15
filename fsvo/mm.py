"""Market-maker quoting engine driven by FSVZO confluence.

The MM continuously quotes a bid and an ask around the mid price:

  * spread widens with volatility (ATR%) so quotes back off in fast markets
  * quotes are *skewed* by the confluence score — bullish confluence shifts
    both quotes up (buy closer, sell further) and sizes the bid larger, so
    inventory drifts in the direction the indicator favors
  * inventory skew pushes quotes the other way as position builds, keeping
    the book mean-reverting around the target
  * one side is pulled entirely when inventory hits its cap, and the whole
    position is flattened if strong confluence flips against it
"""

from dataclasses import dataclass


@dataclass
class MMConfig:
    base_half_spread: float = 0.0015   # 15 bps half-spread floor
    vol_spread_mult: float = 0.5       # extra half-spread per unit ATR%
    confluence_skew: float = 0.0012    # max quote shift from confluence
    inventory_skew: float = 0.0015     # max quote shift from inventory
    quote_size: float = 0.05           # base size per quote (in units of asset)
    size_lean: float = 0.75            # how much confluence tilts sizes (0..1)
    max_inventory: float = 0.5         # absolute inventory cap (units)
    flatten_threshold: float = 0.6     # |confluence| against position => flatten
    min_confluence_to_quote: float = 0.15  # stand down in chop

    @classmethod
    def for_volume(cls, maker_fee: float, quote_size: float = 0.05,
                   max_inventory: float = 0.5) -> "MMConfig":
        """Volume-maximizing profile: quote both sides continuously with the
        spread floor pinned just above round-trip maker fees, so each filled
        round trip is ~breakeven-or-better before adverse selection. The
        FSVZO confluence still skews quotes so inventory drifts with the
        signal instead of bleeding against trend."""
        return cls(
            base_half_spread=max(2.0 * maker_fee + 0.0002, 0.0006),
            vol_spread_mult=0.3,
            confluence_skew=0.0010,
            inventory_skew=0.0020,
            quote_size=quote_size,
            size_lean=0.5,
            max_inventory=max_inventory,
            flatten_threshold=0.75,
            min_confluence_to_quote=0.0,
        )


@dataclass
class Quote:
    bid_price: float | None
    bid_size: float
    ask_price: float | None
    ask_size: float
    flatten: bool = False


def make_quote(mid: float, atr_pct: float, inventory: float, confluence: float,
               cfg: MMConfig) -> Quote:
    """Produce the current two-sided quote (either side may be None)."""
    inv_ratio = max(-1.0, min(1.0, inventory / cfg.max_inventory))

    # Flatten if the indicator strongly disagrees with the position we hold.
    if inventory > 0 and confluence <= -cfg.flatten_threshold:
        return Quote(None, 0.0, None, 0.0, flatten=True)
    if inventory < 0 and confluence >= cfg.flatten_threshold:
        return Quote(None, 0.0, None, 0.0, flatten=True)

    if abs(confluence) < cfg.min_confluence_to_quote:
        return Quote(None, 0.0, None, 0.0)

    half_spread = cfg.base_half_spread + cfg.vol_spread_mult * atr_pct
    skew = confluence * cfg.confluence_skew - inv_ratio * cfg.inventory_skew

    bid = mid * (1.0 - half_spread + skew)
    ask = mid * (1.0 + half_spread + skew)

    # Lean sizes toward the favored side.
    bid_size = cfg.quote_size * max(0.0, 1.0 + cfg.size_lean * confluence)
    ask_size = cfg.quote_size * max(0.0, 1.0 - cfg.size_lean * confluence)

    # Respect the inventory cap.
    bid_size = min(bid_size, max(0.0, cfg.max_inventory - inventory))
    ask_size = min(ask_size, max(0.0, cfg.max_inventory + inventory))

    return Quote(
        bid_price=bid if bid_size > 0 else None,
        bid_size=bid_size,
        ask_price=ask if ask_size > 0 else None,
        ask_size=ask_size,
    )
