#!/usr/bin/env python3
"""Pure math for the bull-put-spread paper-trading simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-14-spread-sim-design.md.
"""
from __future__ import annotations
import math
from collections import Counter

DELTA_BUCKETS = (0.10, 0.15, 0.20)
DEFAULT_WIDTH = 5.0
RISK_FREE = 0.04
STOP_FRACTION = 0.5
TAKE_PROFIT_FRACTION = 0.5
# Cash-secured put stop: flag when the unrealized loss reaches 2x the premium
# received (a put has no fixed max loss, so the stop is premium-based).
CSP_STOP_PREMIUM_MULTIPLE = 2.0
WIDE_SPREAD_PCT = 0.15
THIN_CREDIT_RATIO = 0.05
MAX_WIDTH_DEVIATION = 0.5
WIDTH_EM_FRACTION = 0.15
WIDTH_MAX_SPOT_FRACTION = 0.05
ATM_MAX_DISTANCE = 0.05
IV_MIN = 0.01
IV_MAX = 5.0
MAX_DELTA_GAP = 0.05
DELTA_CROSSCHECK_TOL = 0.03  # |broker - Black-Scholes| above this earns a warning (calibrated: 1.5x p95 0.015, floor 0.03)


def _usable(x) -> bool:
    return x is not None and x == x and x > 0


def _usable_iv(iv) -> bool:
    return _usable(iv) and IV_MIN <= iv <= IV_MAX


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def put_delta(spot: float, strike: float, iv: float, days: float,
              r: float = RISK_FREE) -> float:
    """Absolute put delta |N(d1) - 1| = N(-d1). Intrinsic (0 or 1) when expired or iv<=0."""
    if days <= 0 or iv <= 0:
        return 1.0 if strike > spot else 0.0
    t = days / 365.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2.0) * t) / (iv * math.sqrt(t))
    return norm_cdf(-d1)


def call_delta(spot: float, strike: float, iv: float, days: float,
               r: float = RISK_FREE) -> float:
    """Call delta N(d1). Intrinsic (1 or 0) when expired or iv<=0."""
    if days <= 0 or iv <= 0:
        return 1.0 if spot > strike else 0.0
    t = days / 365.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2.0) * t) / (iv * math.sqrt(t))
    return norm_cdf(d1)


def has_delta(quote: dict) -> bool:
    """True when the quote carries a usable broker delta (finite, |delta| <= 1)."""
    d = quote.get("delta")
    try:
        return d is not None and abs(float(d)) <= 1  # NaN, inf and out-of-range fail
    except (TypeError, ValueError):
        return False


def delta_for(side: str, quote: dict, spot: float, days: float) -> tuple[float, str | None]:
    """(delta, warning): the broker delta when the quote has one, else local Black-Scholes.

    `side` is "P" or "C". With both available and further apart than DELTA_CROSSCHECK_TOL
    the warning names both numbers; an unusable iv skips the cross-check silently."""
    local_fn = put_delta if side == "P" else call_delta
    strike = quote["strike"]
    if not has_delta(quote):
        if not _usable_iv(quote.get("iv")):
            raise ValueError("no usable delta or iv")
        return local_fn(spot, strike, quote["iv"], days), None
    broker = abs(float(quote["delta"]))
    if not _usable_iv(quote.get("iv")):
        return broker, None
    local = local_fn(spot, strike, quote["iv"], days)
    diff = abs(broker - local)
    if diff > DELTA_CROSSCHECK_TOL:
        return broker, f"IBKR Delta {broker:.2f} 与本地 Black-Scholes {local:.2f} 相差 {diff:.2f}"
    return broker, None


def mid_price(bid, ask, last) -> tuple[float | None, bool]:
    """Return (mid, stale). Stale when bid/ask are unusable and lastPrice was used."""
    if _usable(bid) and _usable(ask) and ask >= bid:
        return (bid + ask) / 2.0, False
    if _usable(last):
        return float(last), True
    return None, True


def breakeven_win_rate(credit: float, width: float) -> float:
    """p* = loss / (win + loss), per share: (width - credit) / width."""
    return (width - credit) / width


def expected_move(spot: float, iv: float, days: float) -> float:
    """One-sigma expected move over `days`: S * IV * sqrt(days/365)."""
    return spot * iv * math.sqrt(days / 365.0)


def strike_step(puts: list[dict], spot: float) -> float | None:
    """Most common gap between listed strikes in [0.8*spot, spot]; the smaller one on a tie.

    A cheap stock with coarse strikes has too few in that window, so it widens to
    [0.5*spot, 1.2*spot] before giving up.
    """
    def window(lo: float, hi: float) -> list[float]:
        return sorted({float(p["strike"]) for p in puts if lo * spot <= float(p["strike"]) <= hi * spot})

    strikes = window(0.8, 1.0)
    if len(strikes) < 3:
        strikes = window(0.5, 1.2)
    gaps = Counter(round(b - a, 4) for a, b in zip(strikes, strikes[1:]) if b > a)
    if not gaps:
        return None
    top = max(gaps.values())
    return min(g for g, n in gaps.items() if n == top)


def atm_iv(puts: list[dict], spot: float) -> float | None:
    """Implied vol of the put whose strike is nearest the spot, ignoring unusable values.

    None when that put is more than ATM_MAX_DISTANCE of spot away: a far-out strike carries skew,
    not the at-the-money vol the expected move needs.
    """
    usable = [p for p in puts if _usable_iv(p.get("iv"))]
    if not usable:
        return None
    nearest = min(usable, key=lambda p: abs(float(p["strike"]) - spot))
    if abs(float(nearest["strike"]) - spot) > ATM_MAX_DISTANCE * spot:
        return None
    return float(nearest["iv"])


def recommend_width(puts: list[dict], spot: float, days: float,
                    fraction: float = WIDTH_EM_FRACTION) -> dict | None:
    """Spread width = `fraction` of the one-sigma expected move, rounded to the chain's strike
    step (at least one step, at most 5% of spot unless one step alone is larger).

    Returns the working so the saved file can show it, or None when the chain is too thin to
    estimate (no usable IV, or fewer than two strikes near the spot).
    """
    step = strike_step(puts, spot)
    iv = atm_iv(puts, spot)
    if step is None or iv is None:
        return None
    em = expected_move(spot, iv, days)
    raw = em * fraction
    cap = spot * WIDTH_MAX_SPOT_FRACTION
    n = max(1, round(raw / step))
    capped = False
    while n > 1 and n * step > cap:
        n -= 1
        capped = True
    return {"width": round(n * step, 4), "expected_move": em, "raw": raw, "step": step,
            "atm_iv": iv, "fraction": fraction, "capped": capped}


def _quote(p: dict) -> dict | None:
    mid, stale = mid_price(p.get("bid"), p.get("ask"), p.get("last"))
    if mid is None:
        return None
    spread_pct = None if stale else (p["ask"] - p["bid"]) / mid
    q = {"strike": float(p["strike"]), "mid": mid, "stale": stale,
         "spread_pct": spread_pct, "iv": p.get("iv"),
         "delta": p.get("delta"), "from_close": bool(p.get("from_close"))}
    if "last_trade_date" in p:  # Yahoo rows only: IBKR quotes keep their exact key set
        q["last_trade_date"] = p["last_trade_date"]
    return q


def quote_map(puts: list[dict]) -> dict[float, dict]:
    """strike -> {strike, mid, stale, spread_pct, iv, delta, from_close} for rows with a usable price.

    `delta` is the broker delta when the row carries one (else None); `from_close` flags prices
    taken from the previous close rather than a live quote."""
    out: dict[float, dict] = {}
    for p in puts:
        q = _quote(p)
        if q is not None:
            out[q["strike"]] = q
    return out


def _pick_short(quotes: dict[float, dict], spot: float, days: float,
                bucket: float) -> tuple[dict, float] | None:
    best = None
    for strike, q in quotes.items():
        if strike >= spot or not _usable_iv(q["iv"]):
            continue
        delta = put_delta(spot, strike, q["iv"], days)
        gap = abs(delta - bucket)
        if best is None or gap < best[0]:
            best = (gap, q, delta)
    return None if best is None else (best[1], best[2])


def _find_long(quotes: dict[float, dict], short_strike: float,
               width: float) -> dict | None:
    lower = [k for k in quotes if k < short_strike]
    if not lower:
        return None
    target = short_strike - width
    best = min(lower, key=lambda k: (abs(k - target), k))
    if abs((short_strike - best) - width) > width * MAX_WIDTH_DEVIATION:
        return None
    return quotes[best]


def _warnings(c: dict, earnings: str) -> list[str]:
    out: list[str] = []
    if earnings == "yes":
        out.append("到期前有财报，按分享页规则建议跳过")
    elif earnings == "unknown":
        out.append("财报日期没取到，请自行确认到期前有没有财报")
    for label, pct in (("短腿", c["short_spread_pct"]), ("长腿", c["long_spread_pct"])):
        if pct is not None and pct > WIDE_SPREAD_PCT:
            out.append(f"{label}买卖价差 {pct * 100:.0f}% > {WIDE_SPREAD_PCT * 100:.0f}%，真实成交会被吃掉很多")
    if c["stale"]:
        out.append("报价陈旧（休市时 bid/ask 为空，用了最后成交价）")
    gap = abs(c["short_delta"] - c["bucket"])
    if gap > MAX_DELTA_GAP:
        out.append(f"短腿 delta {c['short_delta']:.2f} 离目标 {c['bucket']:.2f} 较远，档位标签不准")
    if c["credit_ratio"] < THIN_CREDIT_RATIO:
        out.append(f"权利金仅占宽度 {c['credit_ratio'] * 100:.1f}% < {THIN_CREDIT_RATIO * 100:.0f}%，赔率太差")
    return out


def _unavailable(bucket: float, reason: str) -> dict:
    return {"bucket": bucket, "available": False, "reason": reason}


def _candidate(bucket: float, spot: float, days: float, quotes: dict[float, dict],
               width: float, earnings: str) -> dict:
    picked = _pick_short(quotes, spot, days, bucket)
    if picked is None:
        return _unavailable(bucket, "无可用的短腿（缺少有效报价或隐含波动率）")
    short, delta = picked
    long_ = _find_long(quotes, short["strike"], width)
    if long_ is None:
        return _unavailable(bucket, "找不到合适的长腿行权价")
    real_width = short["strike"] - long_["strike"]
    credit = short["mid"] - long_["mid"]
    if credit <= 0 or credit >= real_width:
        return _unavailable(bucket, "净权利金不合理（≤0 或 ≥宽度）")
    em = expected_move(spot, short["iv"], days)
    cand = {
        "bucket": bucket,
        "available": True,
        "short_strike": short["strike"],
        "long_strike": long_["strike"],
        "width": real_width,
        "credit": round(credit, 4),
        "short_mid": short["mid"],
        "long_mid": long_["mid"],
        "short_spread_pct": short["spread_pct"],
        "long_spread_pct": long_["spread_pct"],
        "short_delta": round(delta, 4),
        "short_iv": short["iv"],
        "stale": short["stale"] or long_["stale"],
        "max_profit": round(credit * 100, 2),
        "margin": round(real_width * 100, 2),
        "max_loss": round((real_width - credit) * 100, 2),
        "breakeven": round(short["strike"] - credit, 4),
        "credit_ratio": credit / real_width,
        "breakeven_win_rate": breakeven_win_rate(credit, real_width),
        "expected_move": round(em, 4),
        "em_multiple": (spot - short["strike"]) / em,
    }
    cand["warnings"] = _warnings(cand, earnings)
    return cand


def build_candidates(spot: float, days: float, puts: list[dict],
                     width: float = DEFAULT_WIDTH, earnings: str = "no",
                     buckets: tuple[float, ...] = DELTA_BUCKETS) -> list[dict]:
    """One candidate per delta bucket. `earnings` is "yes" | "no" | "unknown"."""
    quotes = quote_map(puts)
    cands = [_candidate(b, spot, days, quotes, width, earnings) for b in buckets]
    first_bucket: dict[float, float] = {}
    for c in cands:
        if not c["available"]:
            continue
        earlier = first_bucket.setdefault(c["short_strike"], c["bucket"])
        if earlier != c["bucket"]:
            c["warnings"].append(
                f"与档位 {earlier:.2f} 选了同一个短腿 {c['short_strike']:g}，这一档没有独立信息")
    return cands


def expiry_cost(short_strike: float, long_strike: float, underlying: float) -> float:
    """Per-share cost to close the spread at expiry (its intrinsic value)."""
    return max(short_strike - underlying, 0.0) - max(long_strike - underlying, 0.0)


def expiry_pnl(short_strike: float, long_strike: float, credit: float,
               underlying: float, contracts: int = 1) -> tuple[float, str]:
    """Settlement P&L in dollars and result: win / partial / loss."""
    cost = expiry_cost(short_strike, long_strike, underlying)
    pnl = round((credit - cost) * 100 * contracts, 2)
    if underlying >= short_strike:
        result = "win"
    elif underlying <= long_strike:
        result = "loss"
    else:
        result = "partial"
    return pnl, result


def close_pnl(credit: float, exit_cost: float, contracts: int = 1) -> tuple[float, str]:
    pnl = round((credit - exit_cost) * 100 * contracts, 2)
    return pnl, ("win" if pnl > 0 else "loss")


def cost_to_close(short_mid: float, long_mid: float) -> float:
    return max(short_mid - long_mid, 0.0)


def unrealized_pnl(credit: float, cost: float, contracts: int = 1) -> float:
    return round((credit - cost) * 100 * contracts, 2)


def threshold_flags(credit: float, width: float, pnl: float,
                    contracts: int = 1) -> dict[str, bool]:
    """Stop at 50% of max loss, take-profit at 50% of the credit. Advisory only."""
    max_loss = (width - credit) * 100 * contracts
    max_profit = credit * 100 * contracts
    return {
        "stop": pnl <= -STOP_FRACTION * max_loss,
        "take_profit": pnl >= TAKE_PROFIT_FRACTION * max_profit,
    }


def csp_expiry_pnl(strike: float, premium: float, underlying: float,
                   contracts: int = 1) -> tuple[float, str]:
    """Cash-secured put P&L at expiry: keep the premium, or get assigned."""
    if underlying >= strike:
        return round(premium * 100 * contracts, 2), "win"
    return round((premium - (strike - underlying)) * 100 * contracts, 2), "assigned"


def csp_threshold_flags(premium: float, pnl: float,
                        contracts: int = 1) -> dict[str, bool]:
    """Stop at a loss of CSP_STOP_PREMIUM_MULTIPLE x premium, take-profit at 50% of it."""
    total = premium * 100 * contracts
    return {
        "stop": pnl <= -CSP_STOP_PREMIUM_MULTIPLE * total,
        "take_profit": pnl >= TAKE_PROFIT_FRACTION * total,
    }


def cc_expiry_pnl(strike: float, premium: float, underlying: float,
                  contracts: int = 1) -> tuple[float, str]:
    """Covered-call option-leg P&L at expiry: always keep the premium; stock leg is separate."""
    result = "called_away" if underlying >= strike else "win"
    return round(premium * 100 * contracts, 2), result


def cc_stock_leg_pnl(strike: float, reference_price: float, contracts: int = 1) -> float:
    """Stock-leg P&L when called away at `strike` versus a reference price."""
    return round((strike - reference_price) * 100 * contracts, 2)


# --- collar / protective put: signed math. Premiums are PER SHARE; results are
# dollars (x100 x contracts). Deliberately separate from the clamped spread helpers.

def collar_unrealized_pnl(call_credit: float, put_debit: float, call_mid: float,
                          put_mid: float, contracts: int) -> float:
    """Open P&L in dollars, signed: (call sold - call now) + (put now - put paid)."""
    per_share = (call_credit - call_mid) + (put_mid - put_debit)
    return round(per_share * 100 * contracts, 2)


def collar_close_pnl(call_credit: float, put_debit: float, call_mid: float,
                     put_mid: float, contracts: int) -> float:
    """Realised P&L in dollars at early close; same signed formula, exit-time mids."""
    return collar_unrealized_pnl(call_credit, put_debit, call_mid, put_mid, contracts)


def collar_expiry_result(put_strike: float, call_strike: float | None, close: float) -> str:
    """Expiry label; call is called away at close >= strike, put is hit strictly below."""
    if call_strike is not None and close >= call_strike:
        return "called_away"
    return "protected" if close < put_strike else "expired_worthless"


def collar_expiry_option_pnl(net_credit: float, put_strike: float, close: float,
                             contracts: int) -> float:
    """Option-leg dollars at expiry: signed net premium plus put intrinsic below the put strike.

    The call's cost is carried by the stock leg (sold at the strike), so it is not
    subtracted here."""
    intrinsic = max(put_strike - close, 0.0)
    return round((net_credit + intrinsic) * 100 * contracts, 2)


def collar_stock_leg_pnl(reference_price: float, close: float, call_strike: float | None,
                         contracts: int) -> float:
    """Stock-leg dollars vs the reference price; capped at the call strike when there is one."""
    exit_price = close if call_strike is None else min(close, call_strike)
    return round((exit_price - reference_price) * 100 * contracts, 2)


# --- iron condor: per-share inputs, dollar pnl outputs (x100 x contracts). Each side's
# loss is clamped to [0, width] on its own; separate from the single-spread helpers.
CONDOR_THIN_CREDIT_RATIO = 0.15
CONDOR_BREACH_EPS = 1e-9  # a per-share loss below this is float noise, not a breach


def condor_breakevens(put_short: float, call_short: float, net_credit: float) -> tuple[float, float]:
    """Lower and upper breakeven prices: short put - credit, short call + credit (per share)."""
    return put_short - net_credit, call_short + net_credit


def condor_cost_to_close(put_short_mid: float, put_long_mid: float, call_short_mid: float,
                         call_long_mid: float, width: float) -> float:
    """Per-share cost to close all four legs; each side clamped to [0, width] before adding."""
    put_side = min(max(put_short_mid - put_long_mid, 0.0), width)
    call_side = min(max(call_short_mid - call_long_mid, 0.0), width)
    return put_side + call_side


def condor_unrealized_pnl(net_credit: float, cost: float, contracts: int = 1) -> float:
    """Open P&L in dollars, signed: (net credit - cost to close) x 100 x contracts."""
    return round((net_credit - cost) * 100 * contracts, 2)


def condor_close_pnl(net_credit: float, exit_cost: float, contracts: int = 1) -> tuple[float, str]:
    """Realised dollars at early close and win/loss label; pnl == 0 is a loss.

    `+ 0.0` turns a float-noise -0.0 into 0.0 so it never prints as "-$0"."""
    pnl = round((net_credit - exit_cost) * 100 * contracts, 2) + 0.0
    return pnl, ("win" if pnl > 0 else "loss")


def condor_expiry_losses(put_short: float, call_short: float, width: float,
                         close: float) -> tuple[float, float]:
    """Per-share (put-side, call-side) loss at expiry, each clamped to [0, width]."""
    put_loss = min(max(put_short - close, 0.0), width)
    call_loss = min(max(close - call_short, 0.0), width)
    return put_loss, call_loss


def condor_expiry_pnl(net_credit: float, put_short: float, call_short: float, width: float,
                      close: float, contracts: int = 1) -> tuple[float, str, str, float]:
    """Settlement (pnl dollars, win/loss, breached side put/call/none, exit_cost per share)."""
    put_loss, call_loss = condor_expiry_losses(put_short, call_short, width, close)
    exit_cost = put_loss + call_loss
    pnl, result = condor_close_pnl(net_credit, exit_cost, contracts)
    breached = ("put" if put_loss > CONDOR_BREACH_EPS
                else ("call" if call_loss > CONDOR_BREACH_EPS else "none"))
    return pnl, result, breached, exit_cost


# --- bull call spread (debit): per-share inputs, dollar pnl outputs (x100 x contracts).
# The long call is the lower strike; value is clamped to [0, width], separate from the
# credit-spread helpers above.
BULLCALL_DELTA_BUCKETS = (0.60, 0.50, 0.40)
BULLCALL_MIN_DAYS = 30
BULLCALL_POOR_ODDS_RATIO = 0.60
BULLCALL_TAKE_PROFIT_FRACTION = 0.50
BULLCALL_STOP_FRACTION = 0.50
BULLCALL_TIME_STOP_DAYS = 10
BULLCALL_ROUNDING_TOL = 1e-4


def bullcall_breakeven(long_strike: float, debit: float) -> float:
    """Per-share breakeven price at expiry: long strike + net debit."""
    return long_strike + debit


def bullcall_required_win_rate(debit: float, width: float) -> float:
    """Win probability needed to break even, a ratio: debit / width."""
    return debit / width


def bullcall_max_profit(debit: float, width: float, contracts: int = 1) -> float:
    """Max profit in dollars (positive): (width - debit) x 100 x contracts."""
    return round((width - debit) * 100 * contracts, 2)


def bullcall_max_loss(debit: float, contracts: int = 1) -> float:
    """Max loss in dollars as a positive magnitude: debit x 100 x contracts."""
    return round(debit * 100 * contracts, 2)


def bullcall_close_value(long_mid: float, short_mid: float, width: float) -> float:
    """Per-share value to sell the spread now: long mid - short mid, clamped to [0, width]."""
    return min(max(long_mid - short_mid, 0.0), width)


def bullcall_expiry_value(long_strike: float, width: float, close: float) -> float:
    """Per-share spread value at expiry: (close - long strike) clamped to [0, width]."""
    return min(max(close - long_strike, 0.0), width)


def bullcall_pnl(debit: float, value: float, contracts: int = 1) -> tuple[float, str]:
    """Dollars (value - debit) x 100 x contracts, signed, and win/loss; pnl == 0 is a loss.

    `+ 0.0` turns a float-noise -0.0 into 0.0 so it never prints as "-$0"."""
    pnl = round((value - debit) * 100 * contracts, 2) + 0.0
    return pnl, ("win" if pnl > 0 else "loss")


def bullcall_fraction_captured(pnl: float, debit: float, width: float,
                               contracts: int = 1) -> float:
    """Dollar pnl as a signed fraction of max profit; negative when losing."""
    return pnl / bullcall_max_profit(debit, width, contracts)


def bullcall_threshold_flags(debit: float, width: float, pnl: float,
                             contracts: int = 1) -> dict[str, bool]:
    """Stop at 50% of max loss (the debit), take-profit at 50% of max profit; inclusive, advisory."""
    max_loss = bullcall_max_loss(debit, contracts)
    max_profit = bullcall_max_profit(debit, width, contracts)
    return {
        "stop": pnl <= -BULLCALL_STOP_FRACTION * max_loss,
        "take_profit": pnl >= BULLCALL_TAKE_PROFIT_FRACTION * max_profit,
    }
