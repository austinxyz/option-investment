#!/usr/bin/env python3
"""Pure candidate rules for the risk reversal simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-27-risk-reversal-design.md (5.2-5.5, 5.8, 8, 9).
Money maths lives in spread_core (rr_*); this module only chooses legs and assembles records.

Hand example (design section 8): spot 100, short 90 Put mid 2.00, long 110 Call mid 1.50
-> net credit 0.50, capital $8,950, breakeven_down 89.50, dead zone 90 - 110.
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from put_screen_rules import MAX_SPREAD_PCT

EPS = 1e-9  # float slack: a delta gap of exactly MAX_DELTA_GAP must not be rejected
MIN_DAYS_FLOOR = 7  # --min-days may relax the lower bound but never below this

WARN_RR_DOWNSIDE = ("下行风险接近裸卖 Put：股价跌破 Put 行权价会被指派，亏损一直算到归零；"
                    "占用资金按现金担保口径显示（保守），券商实际保证金因家而异")
WARN_RR_DEAD_ZONE = "两个行权价之间两腿都作废；若 Call 是净支出，这笔钱白付"
WARN_RR_WILLING = "只适合真的愿意按 Put 行权价接股的标的"
WARN_RR_DEBIT_HEAVY = "Call 太贵，净支出偏大"
FIXED_WARNINGS = (WARN_RR_DOWNSIDE, WARN_RR_DEAD_ZONE, WARN_RR_WILLING)


def pick_rr_expiry(expirations: list[str], snapshot: date,
                   min_days: int = core.RR_MIN_DAYS) -> str | None:
    """Nearest expiry whose days-to-expiry fall in [min_days, RR_MAX_DAYS], else None."""
    if min_days < MIN_DAYS_FLOOR:
        raise ValueError(f"min_days must be >= {MIN_DAYS_FLOOR}, got {min_days!r}")
    inside = [((date.fromisoformat(e) - snapshot).days, e) for e in expirations]
    inside = [de for de in inside if min_days <= de[0] <= core.RR_MAX_DAYS]
    return min(inside)[1] if inside else None


def no_expiry_reason(min_days: int = core.RR_MIN_DAYS) -> str:
    return f"没有 {min_days}–{core.RR_MAX_DAYS} 天内的到期日"


def _unavailable(bucket: float, reason: str) -> dict:
    return {"delta_bucket": bucket, "status": "unavailable", "reason": reason}


def unavailable_candidates(reason: str) -> list[dict]:
    """All three buckets rejected with the same reason (e.g. no expiry in the window)."""
    return [_unavailable(b, reason) for b in core.RR_PUT_DELTA_BUCKETS]


def _stale_warning(name: str, quote: dict) -> list[str]:
    return [f"{name}报价陈旧（休市时 bid/ask 为空，用了最后成交价）"] if quote["stale"] else []


def _too_wide(quote: dict) -> bool:
    """Spread above MAX_SPREAD_PCT of mid; exactly the limit passes, unknown (stale) passes."""
    pct = quote["spread_pct"]
    return pct is not None and pct > MAX_SPREAD_PCT + EPS


def pick_short_put(puts: list[dict], spot: float, days: float, target: float) -> dict | str:
    """OTM put (strike < spot) with |delta| nearest `target`: {quote, delta, warnings} or reason."""
    best = None
    for q in core.quote_map(puts).values():
        if not q["strike"] < spot:
            continue
        try:
            delta, note = core.delta_for("P", q, spot, days)
        except ValueError:
            continue
        gap = abs(delta - target)
        if best is None or gap < best[0] - EPS:
            best = (gap, q, delta, note)
    if best is None:
        return "无可用的虚值 Put（行权价缺少有效报价、Delta 或隐含波动率）"
    gap, q, delta, note = best
    if gap > core.MAX_DELTA_GAP + EPS:
        return (f"短 Put 最近的 Delta {delta:.2f} 离目标 {target:.2f} 超过 "
                f"{core.MAX_DELTA_GAP:.2f}（行权价 {q['strike']:g}）")
    if _too_wide(q):
        return (f"短 Put {q['strike']:g} 买卖价差 {q['spread_pct'] * 100:.1f}% 超过 "
                f"{MAX_SPREAD_PCT * 100:.0f}%")
    return {"quote": q, "delta": delta,
            "warnings": _stale_warning("短 Put ", q) + ([note] if note else [])}


def pick_long_call(calls: list[dict], spot: float, put_mid: float) -> dict | str:
    """OTM call (strike > spot, spread ok) whose mid is nearest `put_mid`; ties -> lower strike."""
    ok = [q for q in core.quote_map(calls).values()
          if q["strike"] > spot and not _too_wide(q)]
    if not ok:
        return "没有可用的虚值 Call（不存在或价差超过 5%）"
    best = min(ok, key=lambda q: (round(abs(put_mid - q["mid"]), 4), q["strike"]))
    return {"quote": best, "warnings": _stale_warning("长 Call ", best)}


def _call_delta(quote: dict, spot: float, days: float) -> tuple[float | None, list[str]]:
    try:
        delta, note = core.delta_for("C", quote, spot, days)
    except ValueError:
        return None, ["长 Call 缺少 Delta 与有效隐含波动率，Delta 未记录"]
    return round(delta, 4), ([note] if note else [])


def _earnings_warning(earnings: str) -> list[str]:
    if earnings == "yes":
        return ["到期前有财报：财报跳空向下会直接打到短 Put"]
    if earnings == "unknown":
        return ["财报日期没取到，请自行确认到期前有没有财报"]
    return []


def _risk_warnings(net_credit: float, put_mid: float, earnings: str,
                   ex_div: date | None, expiry: date, snapshot: date) -> list[str]:
    out = list(FIXED_WARNINGS)
    if net_credit < 0 and -net_credit > put_mid * core.RR_DEBIT_WARN_FRACTION + EPS:
        out.append(WARN_RR_DEBIT_HEAVY)
    out.extend(_earnings_warning(earnings))
    if ex_div is not None and snapshot <= ex_div <= expiry:
        out.append(f"到期前有除息日 {ex_div.isoformat()}，短 Put 价值受股息影响")
    return out


def _record(bucket: float, spot: float, days: float, put: dict, call: dict,
            earnings: str, ex_div: date | None, expiry: date, snapshot: date) -> dict:
    pq, cq = put["quote"], call["quote"]
    put_mid, call_mid = round(pq["mid"], 4), round(cq["mid"], 4)
    net = core.rr_net_credit(put_mid, call_mid)
    call_delta, delta_warn = _call_delta(cq, spot, days)
    warnings = (put["warnings"] + call["warnings"] + delta_warn
                + _risk_warnings(net, put_mid, earnings, ex_div, expiry, snapshot))
    return {
        "delta_bucket": bucket, "status": "ok",
        "short_put_strike": pq["strike"], "long_call_strike": cq["strike"],
        "short_put_mid": put_mid, "long_call_mid": call_mid, "net_credit": net,
        "breakeven_down": core.rr_breakeven_down(pq["strike"], net),
        "put_move_pct": round(core.rr_put_move_pct(pq["strike"], spot), 4),
        "call_move_pct": round(core.rr_call_move_pct(cq["strike"], spot), 4),
        "capital_basis": core.rr_capital(pq["strike"], net),
        "upside_breakeven": round(cq["strike"] - net, 4) if net < 0 else None,
        "dead_zone": [pq["strike"], cq["strike"]],
        "short_put_delta": round(put["delta"], 4), "long_call_delta": call_delta,
        "short_put_iv": pq["iv"], "long_call_iv": cq["iv"],
        "underlying_price": spot, "expiry": expiry.isoformat(), "days": days,
        "earnings_in_window": {"yes": True, "no": False}.get(earnings),
        "warnings": warnings,
    }


def _candidate(bucket: float, spot: float, days: float, puts: list[dict], calls: list[dict],
               earnings: str, ex_div: date | None, expiry: date, snapshot: date) -> dict:
    put = pick_short_put(puts, spot, days, bucket)
    if isinstance(put, str):
        return _unavailable(bucket, put)
    call = pick_long_call(calls, spot, round(put["quote"]["mid"], 4))
    if isinstance(call, str):
        return _unavailable(bucket, call)
    return _record(bucket, spot, days, put, call, earnings, ex_div, expiry, snapshot)


def build_rr_candidates(spot: float, days: float, puts: list[dict], calls: list[dict],
                        earnings: str, ex_div: date | None, expiry: date,
                        snapshot: date) -> list[dict]:
    """One candidate per RR_PUT_DELTA_BUCKETS entry (0.30 / 0.20 / 0.15), each judged on its own.

    `earnings` is "yes" | "no" | "unknown". Money fields are for one contract."""
    return [_candidate(b, spot, days, puts, calls, earnings, ex_div, expiry, snapshot)
            for b in core.RR_PUT_DELTA_BUCKETS]


def validate_candidate(rec: dict) -> str | None:
    """Open-time re-check of a stored record against its own legs: reason or None when consistent."""
    tol = core.RR_ROUNDING_TOL
    net = rec["short_put_mid"] - rec["long_call_mid"]
    if abs(rec["net_credit"] - net) > tol:
        return f"净权利金 {rec['net_credit']} 与两腿中间价之差 {net:.4f} 不一致"
    be = rec["short_put_strike"] - rec["net_credit"]
    if abs(rec["breakeven_down"] - be) > tol:
        return f"下行盈亏平衡价 {rec['breakeven_down']} 与 {be:.4f} 不一致"
    return None
