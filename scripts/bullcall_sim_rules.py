#!/usr/bin/env python3
"""Pure candidate rules for the bull call debit spread simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-25-bull-call-spread-design.md (5.2-5.8, 9, 10, 12.1, 12.3).
Written separately from spread_core / condor_sim_rules so their outputs stay byte-identical.

Hand example (design section 10): spot 100, buy 100 Call mid 3.20, sell 105 Call mid 1.30
-> debit 1.90, breakeven 101.90, max loss $190, max profit $310, required win rate 0.38,
breakeven move +1.90% (0.019), max-profit move +5.00% (0.05).
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from put_screen_rules import MAX_SPREAD_PCT, spread_warnings

EX_DIV_NEAR_PCT = 0.03
EPS = 1e-9  # float slack: a delta gap of exactly MAX_DELTA_GAP (0.55 - 0.50) must not be rejected

# Design 5.8 text, with "之下" made "或以下": expiring exactly at the long strike is also a full loss.
WARN_FULL_DEBIT = ("方向错了亏掉全部净支出：到期股价在长腿行权价或以下就是满额亏损；"
                   "所需胜率低于一半是正常的，靠赔率赚钱")


def _stale_warning(name: str, quote: dict) -> list[str]:
    return [f"{name}报价陈旧（休市时 bid/ask 为空，用了最后成交价）"] if quote["stale"] else []


def _spread_reason(name: str, quote: dict, spread_limit: float = MAX_SPREAD_PCT) -> str | None:
    """Hard liquidity rule on BOTH legs; exactly the limit passes, unknown (stale) passes."""
    pct = quote["spread_pct"]
    if pct is not None and pct > spread_limit:
        return (f"{name} {quote['strike']:g} 买卖价差 {pct * 100:.1f}% 超过 "
                f"{spread_limit * 100:.0f}%")
    return None


def pick_long_call(calls: list[dict], spot: float, days: float, target: float, *,
                   spread_limit: float = MAX_SPREAD_PCT,
                   spread_warn: float | None = None) -> dict | str:
    """Call (any moneyness, ITM allowed) with delta nearest `target`: {quote, delta, warnings} or reason.

    A strike with neither a broker delta nor a usable iv cannot be judged and is skipped, the same
    as condor_sim_rules._pick_short; when no strike is left the bucket gets a reason, never a raise."""
    best = None
    for q in core.quote_map(calls).values():
        try:
            delta, note = core.delta_for("C", q, spot, days)
        except ValueError:
            continue
        gap = abs(delta - target)
        if best is None or gap < best[0] - EPS:
            best = (gap, q, delta, note)
    if best is None:
        return "无可用的长 Call（行权价缺少有效报价、Delta 或隐含波动率）"
    gap, q, delta, note = best
    if gap > core.MAX_DELTA_GAP + EPS:
        return (f"长 Call 最近的 Delta {delta:.2f} 离目标 {target:.2f} 超过 "
                f"{core.MAX_DELTA_GAP:.2f}（行权价 {q['strike']:g}）")
    reason = _spread_reason("长 Call", q, spread_limit)
    if reason:
        return reason
    warnings = (_stale_warning("长 Call ", q)
                + spread_warnings(q["spread_pct"], spread_limit, spread_warn)
                + ([note] if note else []))
    return {"quote": q, "delta": delta, "warnings": warnings}


def _short_target(long_strike: float, width: float) -> float:
    return round(long_strike + width, 4)


def find_short_call(calls: list[dict], long_strike: float, width: float, *,
                    spread_limit: float = MAX_SPREAD_PCT,
                    spread_warn: float | None = None) -> dict | str:
    """Short call EXACTLY at `long_strike + width` with a usable mid: {quote, warnings} or reason."""
    target = _short_target(long_strike, width)
    match = [q for k, q in core.quote_map(calls).items() if abs(k - target) < EPS]
    if not match:
        return f"上方没有等宽的短腿（需要 Call {target:g} 的有效报价）"
    q = match[0]
    reason = _spread_reason("短 Call", q, spread_limit)
    if reason:
        return reason
    return {"quote": q, "warnings": _stale_warning("短 Call ", q)
            + spread_warnings(q["spread_pct"], spread_limit, spread_warn)}


def short_strike_needed(calls: list[dict], spot: float, days: float, width: float, *,
                        spread_limit: float = MAX_SPREAD_PCT) -> tuple[float, ...]:
    """Short strikes some bucket's long leg needs but `calls` has no row for (to request explicitly).

    Buckets whose long leg is already rejected need nothing; the result is sorted and unique."""
    listed = [float(r["strike"]) for r in calls]
    needed = set()
    for target in core.BULLCALL_DELTA_BUCKETS:
        pick = pick_long_call(calls, spot, days, target, spread_limit=spread_limit)
        if isinstance(pick, str):
            continue
        short = _short_target(pick["quote"]["strike"], width)
        if not any(abs(k - short) < EPS for k in listed):
            needed.add(short)
    return tuple(sorted(needed))


def _earnings_warning(earnings: str) -> list[str]:
    if earnings == "yes":
        return ["到期前有财报：财报跳空向下照样满额亏损（风险 #8），按分享页规则建议跳过"]
    if earnings == "unknown":
        return ["财报日期没取到，请自行确认到期前有没有财报"]
    return []


def _candidate_warnings(debit: float, width: float, short_strike: float, spot: float,
                        days: float, earnings: str, ex_div: date | None, expiry: date,
                        snapshot: date) -> list[str]:
    out = [WARN_FULL_DEBIT]
    ratio = core.bullcall_required_win_rate(debit, width)
    if ratio > core.BULLCALL_POOR_ODDS_RATIO + EPS:
        out.append(f"所需胜率 {ratio * 100:.1f}% > {core.BULLCALL_POOR_ODDS_RATIO * 100:.0f}%，"
                   "花得太多，赔率差")
    out.extend(_earnings_warning(earnings))
    if (ex_div is not None and snapshot <= ex_div <= expiry
            and short_strike <= spot * (1 + EX_DIV_NEAR_PCT) + EPS):
        out.append(f"到期前有除息日 {ex_div.isoformat()} 且短 Call 偏近现价，提前被指派风险高（风险 #6）")
    if days < core.BULLCALL_MIN_DAYS:
        out.append(f"距到期仅 {days:g} 天 < {core.BULLCALL_MIN_DAYS} 天：净买方，时间不在你这边")
    return out


def _unavailable(bucket: float, reason: str) -> dict:
    return {"delta_bucket": bucket, "status": "unavailable", "reason": reason}


def _legs(calls: list[dict], spot: float, days: float, bucket: float, width: float,
          spread: dict) -> tuple[dict, dict, float, list[str]] | str:
    """(long pick, short pick, short delta, leg warnings) or a reject reason.

    `spread` holds the spread_limit / spread_warn keywords for both legs."""
    long_ = pick_long_call(calls, spot, days, bucket, **spread)
    if isinstance(long_, str):
        return long_
    short = find_short_call(calls, long_["quote"]["strike"], width, **spread)
    if isinstance(short, str):
        return short
    try:
        short_delta, note = core.delta_for("C", short["quote"], spot, days)
    except ValueError:  # the record needs short_call_delta; without delta or iv it cannot be filled
        return f"短 Call {short['quote']['strike']:g} 缺少 Delta 与有效隐含波动率，无法记录"
    warnings = long_["warnings"] + short["warnings"] + ([note] if note else [])
    return long_, short, short_delta, warnings


def _candidate(bucket: float, spot: float, days: float, calls: list[dict], width: float,
               earnings: str, ex_div: date | None, expiry: date, snapshot: date,
               spread: dict) -> dict:
    legs = _legs(calls, spot, days, bucket, width, spread)
    if isinstance(legs, str):
        return _unavailable(bucket, legs)
    long_, short, short_delta, leg_warnings = legs
    lq, sq = long_["quote"], short["quote"]
    long_mid, short_mid = round(lq["mid"], 4), round(sq["mid"], 4)
    # Debit from the ROUNDED mids so open's |debit - (long_mid - short_mid)| check stays within tolerance.
    debit = round(long_mid - short_mid, 4)
    if debit <= 0 or debit >= width:
        return _unavailable(bucket, f"净支出 {debit:.4f} 无效（≤0 或 ≥宽度 {width:g}）")
    breakeven = round(core.bullcall_breakeven(lq["strike"], debit), 4)
    return {
        "delta_bucket": bucket, "status": "ok",
        "long_call_strike": lq["strike"], "short_call_strike": sq["strike"], "width": width,
        "long_call_mid": long_mid, "short_call_mid": short_mid, "debit": debit,
        "breakeven": breakeven,
        "breakeven_move_pct": round(breakeven / spot - 1, 4),
        "max_profit_move_pct": round(sq["strike"] / spot - 1, 4),
        "long_call_delta": round(long_["delta"], 4), "short_call_delta": round(short_delta, 4),
        "long_call_iv": lq["iv"], "short_call_iv": sq["iv"], "underlying_price": spot,
        "max_profit": core.bullcall_max_profit(debit, width),
        "max_loss": core.bullcall_max_loss(debit),
        "required_win_rate": core.bullcall_required_win_rate(debit, width),
        "warnings": leg_warnings + _candidate_warnings(debit, width, sq["strike"], spot, days,
                                                       earnings, ex_div, expiry, snapshot),
    }


def build_bullcall_candidates(spot: float, days: float, calls: list[dict], width: float,
                              earnings: str, ex_div: date | None, expiry: date,
                              snapshot: date, *, spread_limit: float = MAX_SPREAD_PCT,
                              spread_warn: float | None = None) -> list[dict]:
    """One candidate per BULLCALL_DELTA_BUCKETS entry (0.60 / 0.50 / 0.40), each judged on its own.

    `width` is already resolved by the caller (--width or recommend_width / DEFAULT_WIDTH);
    `earnings` is "yes" | "no" | "unknown". Money fields are for one contract."""
    if not width > 0:
        raise ValueError(f"width must be positive, got {width!r}")
    # IBKR defaults forward nothing at all, so the leg pickers see exactly the old calls
    spread = ({} if (spread_limit, spread_warn) == (MAX_SPREAD_PCT, None)
              else {"spread_limit": spread_limit, "spread_warn": spread_warn})
    return [_candidate(b, spot, days, calls, width, earnings, ex_div, expiry, snapshot, spread)
            for b in core.BULLCALL_DELTA_BUCKETS]
