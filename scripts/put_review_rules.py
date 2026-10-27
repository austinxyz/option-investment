#!/usr/bin/env python3
"""Pure review rules for open cash-secured puts (no I/O, no network, no clock).

Turns one position's metrics into a verdict: close / hold / attention /
await_settlement. See docs/superpowers/specs/2026-10-18-put-review-design.md.
"""
from __future__ import annotations

# spread_core is pure arithmetic (no I/O, no clock), so importing its constant keeps this pure.
from spread_core import BULLCALL_STOP_FRACTION, BULLCALL_TIME_STOP_DAYS

CLOSE_CAPTURED = 0.80
ATTENTION_DELTA = 0.35
NEAR_EXPIRY_DAYS = 5
QUIET_DELTA = 0.10

# Protection-expiry reminder window for collars / protective puts; kept out of thresholds()
# because those two strategies do not use the premium-selling rules the dict describes.
LOT_PUT_STRATEGIES = ("collar", "protective_put")
PROTECTION_ENDS_DAYS = 7
# Literal rather than imported so this module stays dependency-free (pure rules).
STRATEGY_IRON_CONDOR = "iron_condor"
STRATEGY_BULL_CALL = "bull_call_spread"

EPS = 1e-9  # float tolerance so a displayed "80%" really hits the 80% threshold
UNKNOWN_EARNINGS_NOTE = "；财报日期未知"
STALE_NOTE = "数据陈旧，Delta 未使用"


def thresholds() -> dict[str, float]:
    return {
        "CLOSE_CAPTURED": CLOSE_CAPTURED,
        "ATTENTION_DELTA": ATTENTION_DELTA,
        "NEAR_EXPIRY_DAYS": NEAR_EXPIRY_DAYS,
        "QUIET_DELTA": QUIET_DELTA,
    }


def _verdict(advice: str, rule: int, reason: str) -> dict:
    return {"advice": advice, "rule": rule, "reason": reason}


def _itm_reason(strategy: str | None) -> str:
    if strategy == "covered_call":
        return "现价已到行权价之上，可能被行权（股票按行权价卖出）；由你决定接受行权还是回购"
    base = "已进入价内或触及止损线："
    if strategy == "cash_secured_put":
        return base + "平仓止损，或接受被指派"
    if strategy == "bull_put_spread":
        return base + "平仓止损，或承担价差最大亏损"
    return base + "平仓止损或评估风险"


def _stop_reason(strategy: str | None) -> str:
    if strategy == "covered_call":
        return "已触及止损线（亏损≥权利金的2倍）：回购止损，或继续持有"
    return _itm_reason(strategy)


def _call_itm(m: dict) -> bool:
    strike = m.get("call_strike")
    return strike is not None and m["spot"] >= strike  # >= matches the settlement rule


def review_lot_put(m: dict) -> dict:
    """Collar / protective put rules (spec 5.11), first match wins.

    The 80%-captured close and the Delta rules assume premium selling, so they never apply.
    Ex-dividend data comes from the caller (put_review fetches it only for an in-the-money
    call); it is checked before the plain in-the-money rule so its more specific
    early-assignment reason is the one shown.
    """
    if m["error"] is not None:
        return _verdict("attention", 0, f"取不到报价：{m['error']}")
    if m["days_left"] <= 0:
        return _verdict("await_settlement", 1, "已到期，等待结算")
    call_itm = _call_itm(m)
    if call_itm and m.get("ex_div"):
        return _verdict("attention", 3, f"除息日 {m['ex_div']} 前 Call 已在价内，有提前指派风险；"
                        "被指派则股票按 Call 行权价卖出")
    if call_itm:
        return _verdict("attention", 2, f"现价已到 Call 行权价 {m['call_strike']:g} 之上，"
                        f"可能被叫走（股票按 {m['call_strike']:g} 卖出）；由你决定接受还是回购")
    if m["spot"] < m["put_strike"]:
        return _verdict("hold", 4, f"保护已生效：Put 在价内，下方锁定在 {m['put_strike']:g}；继续持有")
    if m["days_left"] <= PROTECTION_ENDS_DAYS:
        return _verdict("hold", 5, f"还剩 {m['days_left']} 天，到期后保护结束；继续持有")
    return _verdict("hold", 6, "继续持有")


def condor_itm_side(spot: float, put_short: float, call_short: float) -> str | None:
    """'put' / 'call' / None. Strict: a close exactly at a short strike settles that side at zero."""
    if spot < put_short:
        return "put"
    if spot > call_short:
        return "call"
    return None


def _condor_itm_reason(m: dict, side: str) -> str:
    if side == "put":
        return (f"现价已跌破短 Put {m['put_short_strike']:g}（Put 侧价内）："
                "平仓止损，或承担该侧最大亏损")
    reason = (f"现价已涨过短 Call {m['call_short_strike']:g}（Call 侧价内）："
              "平仓止损，或承担该侧最大亏损")
    if m.get("ex_div"):
        reason += f"；除息日 {m['ex_div']} 前短 Call 在价内，有提前指派风险"
    return reason


def _condor_high_delta(m: dict, limit: float) -> str | None:
    """Reason naming the first short leg whose delta reaches `limit`, else None."""
    for label, key in (("短 Put", "put_short_delta"), ("短 Call", "call_short_delta")):
        d = m.get(key)
        if d is not None and d >= limit - EPS:
            return f"{label} Delta {d:.2f} 偏高，需要关注"
    return None


def _condor_quiet(m: dict, t: dict[str, float]) -> bool:
    deltas = (m.get("put_short_delta"), m.get("call_short_delta"))
    return (m["data_ok"] and m["days_left"] <= t["NEAR_EXPIRY_DAYS"]
            and all(d is not None and d <= t["QUIET_DELTA"] + EPS for d in deltas))


def review_condor(m: dict, th: dict[str, float] | None = None) -> dict:
    """Iron condor rules (spec 5.12 / 12.9): same order as a single spread, either side counts."""
    t = {**thresholds(), **(th or {})}
    if m["error"] is not None:
        return _verdict("attention", 0, f"取不到报价：{m['error']}")
    if m["days_left"] <= 0:
        return _verdict("await_settlement", 1, "已到期，等待结算")
    note = "" if m["data_ok"] else f"；{STALE_NOTE}"
    unknown = UNKNOWN_EARNINGS_NOTE if m["earnings"] == "unknown" else ""
    side = condor_itm_side(m["spot"], m["put_short_strike"], m["call_short_strike"])
    if side is not None:
        return _verdict("attention", 2, _condor_itm_reason(m, side) + note)
    if m["stop_flag"]:
        return _verdict("attention", 2, "已触及止损线：平仓止损，或承担铁鹰最大亏损" + note)
    captured = m["captured"]
    if captured is not None and captured >= t["CLOSE_CAPTURED"] - EPS:
        return _verdict("close", 3, f"已捕获 {captured:.0%} 权利金，建议平仓" + note + unknown)
    if m["earnings"] == "yes":
        return _verdict("attention", 4, "持仓期内有财报，需要关注" + note)
    high = _condor_high_delta(m, t["ATTENTION_DELTA"]) if m["data_ok"] else None
    if high:
        return _verdict("attention", 4, high)
    if _condor_quiet(m, t):
        return _verdict("hold", 5, "临近到期且两个短腿 Delta 都很低，等它自然到期归零" + unknown)
    return _verdict("hold", 5, "继续持有" + note + unknown)


def _bullcall_attention(m: dict, t: dict[str, float]) -> str | None:
    """Rule 4 reasons for a bull call (spec 5.13), or None."""
    long_k, short_k, days = m["long_call_strike"], m["short_call_strike"], m["days_left"]
    if m["earnings"] == "yes":
        return "到期前有财报：跳空向下照样满额亏损，需要关注"
    if days <= BULLCALL_TIME_STOP_DAYS and m["pnl"] <= 0:
        return f"只剩 {days} 天仍没有浮盈（时间止损），需要关注"
    if days <= t["NEAR_EXPIRY_DAYS"] and long_k < m["spot"] < short_k:
        return f"只剩 {days} 天且现价在两腿之间（{long_k:g}–{short_k:g}）：到期前主动处理"
    return None


def review_bullcall(m: dict, th: dict[str, float] | None = None) -> dict:
    """Bull call debit spread rules (spec 5.13), first match wins; no Delta rule (buyer side).

    The short-leg ex-dividend check (strictly in the money) comes before the close rule:
    a short call in the money always also touches the short strike, so after it the
    early-assignment reason could never be shown."""
    t = {**thresholds(), **(th or {})}
    if m["error"] is not None:
        return _verdict("attention", 0, f"取不到报价：{m['error']}")
    if m["days_left"] <= 0:
        return _verdict("await_settlement", 1, "已到期，等待结算")
    note = "" if m["data_ok"] else f"；{STALE_NOTE}"
    unknown = UNKNOWN_EARNINGS_NOTE if m["earnings"] == "unknown" else ""
    short_k = m["short_call_strike"]
    if m["stop_flag"]:
        return _verdict("attention", 2, f"已触及止损线（浮亏≥净支出的 {BULLCALL_STOP_FRACTION:.0%}）：平仓止损，"
                        "或承担最大亏损（全部净支出）" + note)
    if m["spot"] > short_k and m.get("ex_div"):
        return _verdict("attention", 4, f"除息日 {m['ex_div']} 前短腿 {short_k:g} 已在价内，"
                        "有提前指派风险：考虑平仓" + note)
    if m["take_profit_flag"]:
        return _verdict("close", 3, f"浮盈已达最大收益的 {m['captured']:.0%}，建议平仓"
                        + note + unknown)
    if m["spot"] >= short_k:
        return _verdict("close", 3, f"现价已触及短腿 {short_k:g}：再涨也不多赚，建议平仓"
                        + note + unknown)
    attention = _bullcall_attention(m, t)
    if attention:
        return _verdict("attention", 4, attention + note)
    return _verdict("hold", 5, "继续持有" + note + unknown)


def review_position(m: dict, th: dict[str, float] | None = None) -> dict:
    """First matching rule wins (spec 4.3). `th` overrides any of thresholds()."""
    if m.get("strategy") == STRATEGY_BULL_CALL:
        return review_bullcall(m, th)
    if m.get("strategy") == STRATEGY_IRON_CONDOR:
        return review_condor(m, th)
    if m.get("strategy") in LOT_PUT_STRATEGIES:
        return review_lot_put(m)
    t = {**thresholds(), **(th or {})}
    if m["error"] is not None:
        return _verdict("attention", 0, f"取不到报价：{m['error']}")
    if m["days_left"] <= 0:
        return _verdict("await_settlement", 1, "已到期，等待结算")

    data_ok = m["data_ok"]
    delta = m["delta"]
    note = "" if data_ok else f"；{STALE_NOTE}"
    unknown = UNKNOWN_EARNINGS_NOTE if m["earnings"] == "unknown" else ""
    reason_itm = _itm_reason(m.get("strategy"))

    if m["itm"]:
        return _verdict("attention", 2, reason_itm + note)
    if m["stop_flag"]:
        return _verdict("attention", 2, _stop_reason(m.get("strategy")) + note)

    captured = m["captured"]
    if captured is not None and captured >= t["CLOSE_CAPTURED"] - EPS:
        return _verdict("close", 3, f"已捕获 {captured:.0%} 权利金，建议平仓" + note + unknown)

    if m["earnings"] == "yes":
        return _verdict("attention", 4, "持仓期内有财报，需要关注" + note)
    if data_ok and delta is not None and delta >= t["ATTENTION_DELTA"] - EPS:
        return _verdict("attention", 4, f"Delta {delta:.2f} 偏高，需要关注")

    if (data_ok and delta is not None and m["days_left"] <= t["NEAR_EXPIRY_DAYS"]
            and delta <= t["QUIET_DELTA"] + EPS):
        return _verdict("hold", 5, "临近到期且 Delta 很低，等它自然到期归零" + unknown)
    return _verdict("hold", 5, "继续持有" + note + unknown)
