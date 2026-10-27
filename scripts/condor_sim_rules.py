#!/usr/bin/env python3
"""Pure candidate rules for the iron-condor simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-25-iron-condor-design.md (5, 12). The put-only helpers in
spread_core are left untouched; these are separate, side-aware versions.
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from put_screen_rules import MAX_SPREAD_PCT, spread_warnings

EX_DIV_NEAR_PCT = 0.03
EPS = 1e-9  # float slack so a gap of exactly MAX_DELTA_GAP is not rejected by rounding

WARN_UP_MOVE = ("大涨对铁鹰可能是满额亏损：涨过短 Call 后每多涨一块都由你承担，"
                "直到长翼处亏满宽度（再涨亏损不再增加），不是只怕下跌")

_SIDE_NAME = {"P": "Put", "C": "Call"}


def _stale_warning(name: str, quote: dict) -> list[str]:
    return [f"{name}报价陈旧（休市时 bid/ask 为空，用了最后成交价）"] if quote["stale"] else []


def _pick_short(rows: list[dict], side: str, spot: float, days: float, target: float, *,
                spread_limit: float = MAX_SPREAD_PCT,
                spread_warn: float | None = None) -> dict | str:
    """Nearest-|delta| OTM leg for `side` ("P" or "C"); a reason string when none qualifies."""
    name = _SIDE_NAME[side]
    best = None
    for strike, q in core.quote_map(rows).items():
        if (strike >= spot) if side == "P" else (strike <= spot):
            continue
        try:
            delta, note = core.delta_for(side, q, spot, days)
        except ValueError:  # neither a broker delta nor a usable iv: this strike cannot be judged
            continue
        gap = abs(delta - target)
        if best is None or gap < best[0] - EPS:
            best = (gap, q, delta, note)
    if best is None:
        return f"无可用的短 {name}（虚值行权价缺少有效报价、Delta 或隐含波动率）"
    gap, q, delta, note = best
    if gap > core.MAX_DELTA_GAP + EPS:
        return (f"短 {name} 最近的 Delta {delta:.2f} 离目标 {target:.2f} 超过 "
                f"{core.MAX_DELTA_GAP:.2f}（行权价 {q['strike']:g}）")
    pct = q["spread_pct"]
    if pct is not None and pct > spread_limit:
        return f"短 {name} {q['strike']:g} 买卖价差 {pct * 100:.0f}% 超过 {spread_limit * 100:.0f}%"
    warnings = (_stale_warning(f"短 {name} ", q) + spread_warnings(pct, spread_limit, spread_warn)
                + ([note] if note else []))
    return {"quote": q, "delta": delta, "warnings": warnings}


def pick_short_put(puts: list[dict], spot: float, days: float, target: float, *,
                   spread_limit: float = MAX_SPREAD_PCT,
                   spread_warn: float | None = None) -> dict | str:
    """OTM put (strike < spot) with |delta| nearest `target`: {quote, delta, warnings} or reason."""
    return _pick_short(puts, "P", spot, days, target, spread_limit=spread_limit,
                       spread_warn=spread_warn)


def pick_short_call(calls: list[dict], spot: float, days: float, target: float, *,
                    spread_limit: float = MAX_SPREAD_PCT,
                    spread_warn: float | None = None) -> dict | str:
    """OTM call (strike > spot) with delta nearest `target`: {quote, delta, warnings} or reason."""
    return _pick_short(calls, "C", spot, days, target, spread_limit=spread_limit,
                       spread_warn=spread_warn)


def _wing(name: str, q: dict, width: float) -> dict:
    warnings = _stale_warning(f"长翼 {name} ", q)
    pct = q["spread_pct"]
    if pct is not None and pct > core.WIDE_SPREAD_PCT:
        warnings.append(f"长翼 {name} {q['strike']:g} 买卖价差 {pct * 100:.0f}% > "
                        f"{core.WIDE_SPREAD_PCT * 100:.0f}%，真实成交会被吃掉很多")
    return {"quote": q, "width": width, "warnings": warnings}


def find_put_long(puts: list[dict], put_short: float, width: float) -> dict | str:
    """Listed put nearest `put_short - width` (within the usual tolerance); reports its real width."""
    quotes = core.quote_map(puts)
    lower = [k for k in quotes if k < put_short]
    if not lower:
        return "找不到合适的长 Put 行权价（下方没有有效报价）"
    best = min(lower, key=lambda k: (abs(k - (put_short - width)), k))
    actual = put_short - best
    if abs(actual - width) > width * core.MAX_WIDTH_DEVIATION:
        return f"找不到合适的长 Put 行权价（离目标宽度 {width:g} 太远）"
    return _wing("Put", quotes[best], actual)


def find_call_long(calls: list[dict], call_short: float, width: float) -> dict | str:
    """Long call must sit EXACTLY at `call_short + width`, else a reason string."""
    target = call_short + width
    match = [q for k, q in core.quote_map(calls).items() if abs(k - target) < EPS]
    if not match:
        return f"上方没有等宽的长腿（需要 Call {target:g} 的有效报价）"
    return _wing("Call", match[0], width)


def _structure_error(ps: dict, pl: dict, cs: dict, cl: dict) -> str | None:
    if ps["strike"] >= cs["strike"]:
        return f"区间无效：短 Put {ps['strike']:g} ≥ 短 Call {cs['strike']:g}"
    strikes = [pl["strike"], ps["strike"], cs["strike"], cl["strike"]]
    if len(set(strikes)) < 4:
        return "两侧复用了同一个行权价"
    if strikes != sorted(strikes):
        return "区间无效：行权价顺序不是 长Put < 短Put < 短Call < 长Call"
    return None


def _credit_error(put_credit: float, call_credit: float, width: float) -> str | None:
    for name, credit in (("Put", put_credit), ("Call", call_credit)):
        if credit <= 0 or credit >= width:
            return f"{name} 侧权利金 {credit:.2f} 不合理（≤0 或 ≥宽度 {width:g}）"
    if put_credit + call_credit >= width:
        return f"净权利金 {put_credit + call_credit:.2f} ≥ 宽度 {width:g}"
    return None


def _candidate_warnings(net: float, width: float, call_short: float, spot: float,
                        earnings: str, ex_div: date | None, expiry: date,
                        snapshot: date) -> list[str]:
    out = [WARN_UP_MOVE]
    if earnings == "yes":
        out.append("到期前有财报，财报前 IV 会先让持仓亏（铁鹰风险 #5），按分享页规则建议跳过")
    elif earnings == "unknown":
        out.append("财报日期没取到，请自行确认到期前有没有财报")
    ratio = net / width
    if ratio < core.CONDOR_THIN_CREDIT_RATIO:
        out.append(f"净权利金仅占单侧宽度 {ratio * 100:.1f}% < "
                   f"{core.CONDOR_THIN_CREDIT_RATIO * 100:.0f}%，赔率太差")
    if (ex_div is not None and snapshot <= ex_div <= expiry
            and call_short <= spot * (1 + EX_DIV_NEAR_PCT) + EPS):
        out.append(f"到期前有除息日 {ex_div.isoformat()} 且短 Call 偏近现价，提前被指派风险高")
    return out


def _unavailable(bucket: float, reason: str) -> dict:
    return {"delta_bucket": bucket, "status": "unavailable", "reason": reason}


def _legs(puts: list[dict], calls: list[dict], spot: float, days: float, bucket: float,
          width: float, spread: dict) -> tuple[dict, dict, dict, dict] | str:
    """The four leg picks (short put, long put, short call, long call) or a reject reason.

    `spread` holds the spread_limit / spread_warn keywords for the two short legs."""
    sp = pick_short_put(puts, spot, days, bucket, **spread)
    if isinstance(sp, str):
        return sp
    sc = pick_short_call(calls, spot, days, bucket, **spread)
    if isinstance(sc, str):
        return sc
    lp = find_put_long(puts, sp["quote"]["strike"], width)
    if isinstance(lp, str):
        return lp
    lc = find_call_long(calls, sc["quote"]["strike"], lp["width"])
    return lc if isinstance(lc, str) else (sp, lp, sc, lc)


def _candidate(bucket: float, spot: float, days: float, puts: list[dict], calls: list[dict],
               width: float, earnings: str, ex_div: date | None, expiry: date,
               snapshot: date, spread: dict) -> dict:
    legs = _legs(puts, calls, spot, days, bucket, width, spread)
    if isinstance(legs, str):
        return _unavailable(bucket, legs)
    sp, lp, sc, lc = legs
    ps, pl, cs, cl = sp["quote"], lp["quote"], sc["quote"], lc["quote"]
    error = _structure_error(ps, pl, cs, cl)
    if error:
        return _unavailable(bucket, error)
    w = lp["width"]
    put_credit, call_credit = ps["mid"] - pl["mid"], cs["mid"] - cl["mid"]
    error = _credit_error(put_credit, call_credit, w)
    if error:
        return _unavailable(bucket, error)
    net = put_credit + call_credit
    low, high = core.condor_breakevens(ps["strike"], cs["strike"], net)
    warnings = (sp["warnings"] + sc["warnings"] + lp["warnings"] + lc["warnings"]
                + _candidate_warnings(net, w, cs["strike"], spot, earnings, ex_div, expiry, snapshot))
    return {
        "delta_bucket": bucket, "status": "ok", "warnings": warnings,
        "put_short_strike": ps["strike"], "put_long_strike": pl["strike"],
        "call_short_strike": cs["strike"], "call_long_strike": cl["strike"],
        "width": w, "put_credit": round(put_credit, 4), "call_credit": round(call_credit, 4),
        "net_credit": round(net, 4), "breakeven_low": round(low, 4), "breakeven_high": round(high, 4),
        "put_short_delta": round(sp["delta"], 4), "call_short_delta": round(sc["delta"], 4),
        "put_short_iv": ps["iv"], "call_short_iv": cs["iv"], "underlying_price": spot,
        "max_loss": round((w - net) * 100, 2),
        "required_win_rate": core.breakeven_win_rate(net, w),
    }


def build_condor_candidates(spot: float, days: float, puts: list[dict], calls: list[dict],
                            width: float | None, earnings: str, ex_div: date | None,
                            expiry: date, snapshot: date, *,
                            spread_limit: float = MAX_SPREAD_PCT,
                            spread_warn: float | None = None) -> list[dict]:
    """One candidate per delta bucket; a bucket that fails is 'unavailable' with a reason.

    `width` is only the put-side target (None = recommended width); the real width comes from
    the put legs. `earnings` is "yes" | "no" | "unknown"."""
    if width is None:
        rec = core.recommend_width(puts, spot, days)
        width = rec["width"] if rec else core.DEFAULT_WIDTH
    # IBKR defaults forward nothing at all, so the leg pickers see exactly the old calls
    spread = ({} if (spread_limit, spread_warn) == (MAX_SPREAD_PCT, None)
              else {"spread_limit": spread_limit, "spread_warn": spread_warn})
    return [_candidate(b, spot, days, puts, calls, width, earnings, ex_div, expiry, snapshot, spread)
            for b in core.DELTA_BUCKETS]
