#!/usr/bin/env python3
"""Pure candidate rules for the bear put debit spread simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-27-bearish-and-new-strategies-design.md (5.2-5.4, 8, 10).
Buyer side: explicit long_put_* / short_put_* fields, never the seller credit / short_strike keys.

Hand example (design section 8): spot 100, buy 100 Put mid 4.80, sell 90 Put mid 1.80, width 10
-> debit 3.00, max loss $300, max profit $700, breakeven 97.00, required win rate 0.30,
breakeven move -3.00% (-0.03), max-profit move (down to the short strike) -10.00% (-0.10).
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from bearcall_sim_rules import WARN_DRIFT, spread_reason, stale_warning
from put_screen_rules import MAX_SPREAD_PCT, spread_warnings

_EPS = 1e-9  # float slack: a delta gap of exactly MAX_DELTA_GAP must not be rejected

WARN_FULL_DEBIT_BEARPUT = ("方向错了亏掉全部净支出：到期股价在长腿行权价或以上就是满额亏损；"
                           "所需胜率低于一半是正常的，靠赔率赚钱")


def pick_long_put(puts: list[dict], spot: float, days: float, target: float,
                  name: str = "长 Put", *, spread_limit: float = MAX_SPREAD_PCT,
                  spread_warn: float | None = None) -> dict | str:
    """Put (any moneyness, ITM allowed) with |delta| nearest `target`: {quote, delta, warnings}.

    A strike with neither a broker delta nor a usable iv cannot be judged and is skipped; when no
    strike is left the bucket gets a reason, never a raise. `name` labels the leg in messages."""
    best = None
    for q in core.quote_map(puts).values():
        try:
            delta, note = core.delta_for("P", q, spot, days)
        except ValueError:
            continue
        gap = abs(delta - target)
        if best is None or gap < best[0] - _EPS:
            best = (gap, q, delta, note)
    if best is None:
        return f"无可用的{name}（行权价缺少有效报价、Delta 或隐含波动率）"
    gap, q, delta, note = best
    if gap > core.MAX_DELTA_GAP + _EPS:
        return (f"{name} 最近的 Delta {delta:.2f} 离目标 {target:.2f} 超过 "
                f"{core.MAX_DELTA_GAP:.2f}（行权价 {q['strike']:g}）")
    reason = spread_reason(name, q, spread_limit)
    if reason:
        return reason
    return {"quote": q, "delta": delta,
            "warnings": (stale_warning(f"{name} ", q)
                         + spread_warnings(q["spread_pct"], spread_limit, spread_warn)
                         + ([note] if note else []))}


def _short_target(long_strike: float, width: float) -> float:
    return round(long_strike - width, 4)


def find_short_put(puts: list[dict], long_strike: float, width: float, *,
                   spread_limit: float = MAX_SPREAD_PCT,
                   spread_warn: float | None = None) -> dict | str:
    """Short put EXACTLY at `long_strike - width` with a usable mid: {quote, warnings} or reason."""
    target = _short_target(long_strike, width)
    match = [q for k, q in core.quote_map(puts).items() if abs(k - target) < _EPS]
    if not match:
        return f"下方没有等宽的短腿（需要 Put {target:g} 的有效报价）"
    q = match[0]
    reason = spread_reason("短 Put", q, spread_limit)
    if reason:
        return reason
    return {"quote": q, "warnings": stale_warning("短 Put ", q)
            + spread_warnings(q["spread_pct"], spread_limit, spread_warn)}


def short_strike_needed(puts: list[dict], spot: float, days: float, width: float, *,
                        spread_limit: float = MAX_SPREAD_PCT) -> tuple[float, ...]:
    """Short strikes some bucket's long leg needs but `puts` has no row for (to request explicitly).

    Buckets whose long leg is already rejected need nothing; the result is sorted and unique."""
    listed = [float(r["strike"]) for r in puts]
    needed = set()
    for target in core.BEARPUT_DELTA_BUCKETS:
        pick = pick_long_put(puts, spot, days, target, spread_limit=spread_limit)
        if isinstance(pick, str):
            continue
        short = _short_target(pick["quote"]["strike"], width)
        if not any(abs(k - short) < _EPS for k in listed):
            needed.add(short)
    return tuple(sorted(needed))


def earnings_warning(earnings: str) -> list[str]:
    """Put-buyer earnings text (shared with longput_sim_rules)."""
    if earnings == "yes":
        return ["到期前有财报：财报后隐含波动率回落，跳空向上照样亏掉全部净支出，按分享页规则建议跳过"]
    if earnings == "unknown":
        return ["财报日期没取到，请自行确认到期前有没有财报"]
    return []


def short_dte_warning(days: float, min_days: int) -> list[str]:
    if days < min_days:
        return [f"距到期仅 {days:g} 天 < {min_days} 天：净买方，时间不在你这边"]
    return []


def _unavailable(bucket: float, reason: str) -> dict:
    return {"delta_bucket": bucket, "status": "unavailable", "reason": reason}


def _legs(puts: list[dict], spot: float, days: float, bucket: float, width: float,
          spread: dict) -> tuple[dict, dict, float, list[str]] | str:
    """(long pick, short pick, short |delta|, leg warnings) or a reject reason.

    `spread` holds the spread_limit / spread_warn keywords for both legs."""
    long_ = pick_long_put(puts, spot, days, bucket, **spread)
    if isinstance(long_, str):
        return long_
    short = find_short_put(puts, long_["quote"]["strike"], width, **spread)
    if isinstance(short, str):
        return short
    try:
        short_delta, note = core.delta_for("P", short["quote"], spot, days)
    except ValueError:  # the record needs short_put_delta; without delta or iv it cannot be filled
        return f"短 Put {short['quote']['strike']:g} 缺少 Delta 与有效隐含波动率，无法记录"
    return long_, short, short_delta, long_["warnings"] + short["warnings"] + ([note] if note else [])


def _candidate(bucket: float, spot: float, days: float, puts: list[dict], width: float,
               earnings: str, spread: dict) -> dict:
    legs = _legs(puts, spot, days, bucket, width, spread)
    if isinstance(legs, str):
        return _unavailable(bucket, legs)
    long_, short, short_delta, leg_warnings = legs
    lq, sq = long_["quote"], short["quote"]
    long_mid, short_mid = round(lq["mid"], 4), round(sq["mid"], 4)
    # Debit from the ROUNDED mids so open's |debit - (long_mid - short_mid)| check stays in tolerance.
    debit = round(long_mid - short_mid, 4)
    if debit <= 0 or debit >= width:
        return _unavailable(bucket, f"净支出 {debit:.4f} 无效（≤0 或 ≥宽度 {width:g}）")
    breakeven = round(core.bearput_breakeven(lq["strike"], debit), 4)
    return {
        "delta_bucket": bucket, "status": "ok",
        "long_put_strike": lq["strike"], "short_put_strike": sq["strike"], "width": width,
        "long_put_mid": long_mid, "short_put_mid": short_mid, "debit": debit,
        "breakeven": breakeven,
        "breakeven_move_pct": round(breakeven / spot - 1, 4),
        "max_profit_move_pct": round(sq["strike"] / spot - 1, 4),
        "long_put_delta": round(long_["delta"], 4), "short_put_delta": round(short_delta, 4),
        "long_put_iv": lq["iv"], "short_put_iv": sq["iv"], "underlying_price": spot,
        "max_profit": core.bearput_max_profit(debit, width),
        "max_loss": core.bearput_max_loss(debit),
        "required_win_rate": core.bearput_required_win_rate(debit, width),
        "warnings": (leg_warnings + [WARN_FULL_DEBIT_BEARPUT, WARN_DRIFT]
                     + earnings_warning(earnings)
                     + short_dte_warning(days, core.BEARPUT_MIN_DAYS)),
    }


def build_bearput_candidates(spot: float, days: float, puts: list[dict], width: float,
                             earnings: str, ex_div: date | None, expiry: date,
                             snapshot: date, *, spread_limit: float = MAX_SPREAD_PCT,
                             spread_warn: float | None = None) -> list[dict]:
    """One candidate per BEARPUT_DELTA_BUCKETS entry (0.60 / 0.50 / 0.40), each judged on its own.

    `width` is already resolved by the caller; `earnings` is "yes" | "no" | "unknown". `ex_div`,
    `expiry` and `snapshot` are accepted for a uniform signature: a long put spread has no
    early-assignment-before-dividend rule (design 5.8), so they add no warning."""
    if not width > 0:
        raise ValueError(f"width must be positive, got {width!r}")
    # IBKR defaults forward nothing at all, so the leg pickers see exactly the default calls
    spread = ({} if (spread_limit, spread_warn) == (MAX_SPREAD_PCT, None)
              else {"spread_limit": spread_limit, "spread_warn": spread_warn})
    return [_candidate(b, spot, days, puts, width, earnings, spread)
            for b in core.BEARPUT_DELTA_BUCKETS]
