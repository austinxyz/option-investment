"""Pure chain-selection and quote-conversion helpers for the IBKR options layer.

No ib_async import, no network, no clock. Inputs are duck-typed ib_async
objects (read via getattr with defaults) so tests can use SimpleNamespace.
IBKR uses -1 as a "no data" sentinel and nan for missing values.
"""
from __future__ import annotations

import statistics
from typing import Iterable, Iterator, Sequence

_EPS = 1e-9


def usable(x) -> bool:
    """True for a finite number > 0 (rejects -1, 0, nan, None)."""
    try:
        return x is not None and x == x and x > 0 and x != float("inf")
    except TypeError:
        return False


def _clean(x) -> float | None:
    return float(x) if usable(x) else None


def _group_size(chain) -> int:
    return len(getattr(chain, "expirations", None) or ()) * len(
        getattr(chain, "strikes", None) or ())


def select_chain(chains: Sequence, symbol: str):
    """Pick the standard SMART / multiplier-100 chain; None if none qualifies."""
    eligible = [
        c for c in chains
        if getattr(c, "exchange", None) == "SMART"
        and str(getattr(c, "multiplier", "")) == "100"
    ]
    if not eligible:
        return None
    named = [c for c in eligible if getattr(c, "tradingClass", None) == symbol]
    pool = named or eligible
    return max(pool, key=_group_size)


def strike_window(strikes: Iterable[float], spot: float, side: str, *,
                  pct: float = 0.15, limit: int = 40) -> list[float]:
    """Strikes on the OTM side within pct of spot, nearest-first capped, ascending."""
    if side == "P":
        lo, hi = spot * (1 - pct) - _EPS, spot
        cand = [s for s in strikes if lo <= s < hi]
    elif side == "C":
        lo, hi = spot, spot * (1 + pct) + _EPS
        cand = [s for s in strikes if lo < s <= hi]
    else:
        raise ValueError(f"side must be 'P' or 'C', got {side!r}")
    nearest = sorted(cand, key=lambda s: abs(s - spot))[:limit]
    return sorted(nearest)


def _unit_abs(x) -> float | None:
    """abs(x) for a finite number with |x| <= 1 (a delta), else None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return abs(v) if v == v and abs(v) <= 1 else None


def ticker_to_row(t) -> dict | None:
    """Convert a ticker to a quote row.

    The row may carry iv / delta / und_price as None (no greeks yet, or sentinel
    values); it is dropped only when it has no usable price and no usable iv/delta."""
    greeks = getattr(t, "modelGreeks", None)
    iv = _clean(getattr(greeks, "impliedVol", None)) if greeks else None
    delta = _unit_abs(getattr(greeks, "delta", None)) if greeks else None
    und_price = _clean(getattr(greeks, "undPrice", None)) if greeks else None
    bid = _clean(getattr(t, "bid", None))
    ask = _clean(getattr(t, "ask", None))
    last = _clean(getattr(t, "last", None))
    close = _clean(getattr(t, "close", None))
    from_close = False
    two_sided = bid is not None and ask is not None and ask >= bid
    if last is None and not two_sided and close is not None:
        last, from_close = close, True
    if (bid is None and ask is None and last is None) and iv is None and delta is None:
        return None
    contract = getattr(t, "contract", None)
    row = {
        "strike": getattr(contract, "strike", None),
        "bid": bid,
        "ask": ask,
        "last": last,
        "iv": iv,
        "delta": delta,
        "und_price": und_price,
        "close": close,
    }
    if from_close:
        row["from_close"] = True
    return row


def spot_from_rows(rows: Iterable[dict], stock_close) -> float | None:
    """Median und_price of rows if any usable, else stock_close, else None."""
    prices = [r["und_price"] for r in rows if usable(r.get("und_price"))]
    if prices:
        return float(statistics.median(prices))
    return float(stock_close) if usable(stock_close) else None


def batched(seq: Iterable, n: int) -> Iterator[list]:
    """Yield successive lists of at most n items."""
    if n < 1:
        raise ValueError("n must be >= 1")
    batch: list = []
    for item in seq:
        batch.append(item)
        if len(batch) == n:
            yield batch
            batch = []
    if batch:
        yield batch
