#!/usr/bin/env python3
"""Pure candidate rules for the bear call credit spread simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-27-bearish-and-new-strategies-design.md (5.2-5.4, 8, 10).
Written separately from spread_core.build_candidates / bullcall_sim_rules so their outputs stay
byte-identical.

Hand example (design section 8): spot 100, sell 105 Call mid 2.10, buy 110 Call mid 0.90,
width 5 -> credit 1.20, max profit $120, max loss $380, breakeven 106.20, required win rate
0.76, breakeven move +6.20% (0.062), max-loss move (up to the long strike) +10.00% (0.10).
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from put_screen_rules import MAX_SPREAD_PCT, spread_warnings

_EPS = 1e-9  # float slack: a delta gap of exactly MAX_DELTA_GAP must not be rejected

WARN_BEAR_CALL_UPSIDE = ("上涨亏钱：到期股价在长腿行权价或以上就是满额亏损，"
                         "最大亏损是宽度减权利金")
# Shared by all three bearish rule files (bearput / longput import it from here).
WARN_DRIFT = "历史上市场整体向上漂移，看跌样本里亏的概率偏高"


def stale_warning(name: str, quote: dict) -> list[str]:
    return [f"{name}报价陈旧（休市时 bid/ask 为空，用了最后成交价）"] if quote["stale"] else []


def spread_reason(name: str, quote: dict, spread_limit: float = MAX_SPREAD_PCT) -> str | None:
    """Hard liquidity rule on a leg; exactly the limit passes, unknown (stale) passes."""
    pct = quote["spread_pct"]
    if pct is not None and pct > spread_limit:
        return (f"{name} {quote['strike']:g} 买卖价差 {pct * 100:.1f}% 超过 "
                f"{spread_limit * 100:.0f}%")
    return None


def in_window(when: date | None, snapshot: date, expiry: date) -> bool:
    return when is not None and snapshot <= when <= expiry


def pick_short_call(calls: list[dict], spot: float, days: float, target: float, *,
                    spread_limit: float = MAX_SPREAD_PCT,
                    spread_warn: float | None = None) -> dict | str:
    """Out-of-the-money call (strike >= spot) with delta nearest `target`: {quote, delta, warnings}.

    A strike with neither a broker delta nor a usable iv cannot be judged and is skipped; when no
    strike is left the bucket gets a reason, never a raise."""
    best = None
    for q in core.quote_map(calls).values():
        if q["strike"] < spot:
            continue
        try:
            delta, note = core.delta_for("C", q, spot, days)
        except ValueError:
            continue
        gap = abs(delta - target)
        if best is None or gap < best[0] - _EPS:
            best = (gap, q, delta, note)
    if best is None:
        return "无可用的短 Call（虚值行权价缺少有效报价、Delta 或隐含波动率）"
    gap, q, delta, note = best
    if gap > core.MAX_DELTA_GAP + _EPS:
        return (f"短 Call 最近的 Delta {delta:.2f} 离目标 {target:.2f} 超过 "
                f"{core.MAX_DELTA_GAP:.2f}（行权价 {q['strike']:g}）")
    reason = spread_reason("短 Call", q, spread_limit)
    if reason:
        return reason
    return {"quote": q, "delta": delta,
            "warnings": (stale_warning("短 Call ", q)
                         + spread_warnings(q["spread_pct"], spread_limit, spread_warn)
                         + ([note] if note else []))}


def _long_target(short_strike: float, width: float) -> float:
    return round(short_strike + width, 4)


def find_long_call(calls: list[dict], short_strike: float, width: float, *,
                   spread_limit: float = MAX_SPREAD_PCT,
                   spread_warn: float | None = None) -> dict | str:
    """Long call EXACTLY at `short_strike + width` with a usable mid: {quote, warnings} or reason."""
    target = _long_target(short_strike, width)
    match = [q for k, q in core.quote_map(calls).items() if abs(k - target) < _EPS]
    if not match:
        return f"上方没有等宽的长腿（需要 Call {target:g} 的有效报价）"
    q = match[0]
    reason = spread_reason("长 Call", q, spread_limit)
    if reason:
        return reason
    return {"quote": q, "warnings": stale_warning("长 Call ", q)
            + spread_warnings(q["spread_pct"], spread_limit, spread_warn)}


def long_strike_needed(calls: list[dict], spot: float, days: float, width: float, *,
                       spread_limit: float = MAX_SPREAD_PCT) -> tuple[float, ...]:
    """Long strikes some bucket's short leg needs but `calls` has no row for (to request explicitly).

    Buckets whose short leg is already rejected need nothing; the result is sorted and unique."""
    listed = [float(r["strike"]) for r in calls]
    needed = set()
    for target in core.DELTA_BUCKETS:
        pick = pick_short_call(calls, spot, days, target, spread_limit=spread_limit)
        if isinstance(pick, str):
            continue
        long_ = _long_target(pick["quote"]["strike"], width)
        if not any(abs(k - long_) < _EPS for k in listed):
            needed.add(long_)
    return tuple(sorted(needed))


def _earnings_warning(earnings: str) -> list[str]:
    if earnings == "yes":
        return ["到期前有财报：财报跳空向上可能直接满额亏损，按分享页规则建议跳过"]
    if earnings == "unknown":
        return ["财报日期没取到，请自行确认到期前有没有财报"]
    return []


def _candidate_warnings(credit: float, width: float, short_strike: float, spot: float,
                        earnings: str, ex_div: date | None, expiry: date,
                        snapshot: date) -> list[str]:
    out = [WARN_BEAR_CALL_UPSIDE, WARN_DRIFT]
    ratio = credit / width
    if ratio < core.THIN_CREDIT_RATIO:
        out.append(f"权利金仅占宽度 {ratio * 100:.1f}% < {core.THIN_CREDIT_RATIO * 100:.0f}%，赔率太差")
    out.extend(_earnings_warning(earnings))
    # Short call at (or in) the money with an ex-dividend date before expiry: early assignment.
    if in_window(ex_div, snapshot, expiry) and short_strike <= spot + _EPS:
        out.append(f"到期前有除息日 {ex_div.isoformat()} 且短 Call 已到价内边缘，"
                   "除息前可能被提前指派（风险 #6）")
    return out


def _unavailable(bucket: float, reason: str) -> dict:
    return {"delta_bucket": bucket, "status": "unavailable", "reason": reason}


def _candidate(bucket: float, spot: float, days: float, calls: list[dict], width: float,
               earnings: str, ex_div: date | None, expiry: date, snapshot: date,
               spread: dict) -> dict:
    """`spread` holds the spread_limit / spread_warn keywords for both legs."""
    short = pick_short_call(calls, spot, days, bucket, **spread)
    if isinstance(short, str):
        return _unavailable(bucket, short)
    long_ = find_long_call(calls, short["quote"]["strike"], width, **spread)
    if isinstance(long_, str):
        return _unavailable(bucket, long_)
    sq, lq = short["quote"], long_["quote"]
    short_mid, long_mid = round(sq["mid"], 4), round(lq["mid"], 4)
    # Credit from the ROUNDED mids so open's |credit - (short_mid - long_mid)| check stays in tolerance.
    credit = round(short_mid - long_mid, 4)
    if credit <= 0 or credit >= width:
        return _unavailable(bucket, f"净权利金 {credit:.4f} 无效（≤0 或 ≥宽度 {width:g}）")
    breakeven = round(core.bearcall_breakeven(sq["strike"], credit), 4)
    return {
        "delta_bucket": bucket, "status": "ok", "option_type": "C",
        "short_strike": sq["strike"], "long_strike": lq["strike"], "width": width,
        "short_mid": short_mid, "long_mid": long_mid, "credit": credit,
        "breakeven": breakeven,
        "breakeven_move_pct": round(breakeven / spot - 1, 4),
        "max_loss_move_pct": round(lq["strike"] / spot - 1, 4),
        "short_delta": round(short["delta"], 4), "short_iv": sq["iv"],
        "short_spread_pct": sq["spread_pct"], "long_spread_pct": lq["spread_pct"],
        "underlying_price": spot,
        "max_profit": round(credit * 100, 2) + 0.0,
        "max_loss": round((width - credit) * 100, 2) + 0.0,
        "required_win_rate": core.breakeven_win_rate(credit, width),
        "warnings": short["warnings"] + long_["warnings"] + _candidate_warnings(
            credit, width, sq["strike"], spot, earnings, ex_div, expiry, snapshot),
    }


def build_bearcall_candidates(spot: float, days: float, calls: list[dict], width: float,
                              earnings: str, ex_div: date | None, expiry: date,
                              snapshot: date, *, spread_limit: float = MAX_SPREAD_PCT,
                              spread_warn: float | None = None) -> list[dict]:
    """One candidate per DELTA_BUCKETS entry (0.10 / 0.15 / 0.20), each judged on its own.

    `width` is already resolved by the caller (--width or recommend_width / DEFAULT_WIDTH);
    `earnings` is "yes" | "no" | "unknown". Money fields are for one contract."""
    if not width > 0:
        raise ValueError(f"width must be positive, got {width!r}")
    # IBKR defaults forward nothing at all, so the leg pickers see exactly the default calls
    spread = ({} if (spread_limit, spread_warn) == (MAX_SPREAD_PCT, None)
              else {"spread_limit": spread_limit, "spread_warn": spread_warn})
    return [_candidate(b, spot, days, calls, width, earnings, ex_div, expiry, snapshot, spread)
            for b in core.DELTA_BUCKETS]
