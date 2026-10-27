"""Shared synthetic option-chain builders for the spread-sim tests (no network)."""
from pathlib import Path
import math
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_core as core


def bs_put(spot, strike, iv, days, r=core.RISK_FREE):
    t = days / 365.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    return strike * math.exp(-r * t) * core.norm_cdf(-d2) - spot * core.norm_cdf(-d1)


def make_chain(spot=100.0, iv=0.40, days=7, strikes=None, quote_spread=0.04, stale=False):
    """Put chain priced by Black-Scholes. stale=True zeroes bid/ask like a weekend chain."""
    strikes = strikes or [float(k) for k in range(80, 100)]
    rows = []
    for k in strikes:
        px = bs_put(spot, k, iv, days)
        rows.append({
            "strike": k,
            "bid": 0 if stale else max(px - quote_spread / 2, 0.01),
            "ask": 0 if stale else px + quote_spread / 2,
            "last": px,
            "iv": iv,
        })
    return rows


def bs_call(spot, strike, iv, days, r=core.RISK_FREE):
    t = days / 365.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    return spot * core.norm_cdf(d1) - strike * math.exp(-r * t) * core.norm_cdf(d2)


def make_call_chain(spot=100.0, iv=0.40, days=7, strikes=None, quote_spread=0.04, stale=False):
    """Call chain priced by Black-Scholes. stale=True zeroes bid/ask like a weekend chain."""
    strikes = strikes or [float(k) for k in range(101, 121)]
    rows = []
    for k in strikes:
        px = bs_call(spot, k, iv, days)
        rows.append({
            "strike": k,
            "bid": 0 if stale else max(px - quote_spread / 2, 0.01),
            "ask": 0 if stale else px + quote_spread / 2,
            "last": px,
            "iv": iv,
        })
    return rows
