#!/usr/bin/env python3
"""Pure candidate rules for the collar / protective-put screen (no I/O, no network).

Put tiers are percent-of-spot floors (not Delta); the call leg is whichever OTM call
roughly pays for the put. See docs/superpowers/specs/2026-10-25-collar-design.md (5, 10).
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from call_screen_rules import REASON_SHORT_SHARES
from holdings import HeldLot
from put_screen_rules import MAX_SPREAD_PCT, Reject, spread_warnings

TIERS = (0.90, 0.85, 0.80)
EX_DIV_NEAR_PCT = 0.03
LONG_TERM_DAYS = 365
MAX_TIER_MISS = 0.05  # a real strike more than 5 points of spot below the tier is not that tier

REASON_PUTS_USED = "已有的多头 Put 已用完该持仓可对冲的张数"
REASON_NO_PUT_QUOTE = "Put 取不到有效报价"
REASON_SPARSE = "行权价间距过粗，达不到该档"
REASON_PUT_WIDE = "Put 买卖价差过宽（超过权利金的 {limit:.0%}）"
REASON_NO_ZERO_CALL = "没有可用的虚值 Call（不存在或价差超过 {limit:.0%}）"
NOTE_COVERED = "已被未平仓 call 覆盖：只出保护性 Put"
WARN_SAME_STRIKE = "与上一档行权价相同"


def _leg_ok(q: dict, *, spread_limit: float = MAX_SPREAD_PCT) -> bool:
    """Spread is judged on the leg's own price; a stale quote (spread unknown) passes.

    The 5%-10% warning is not part of the verdict; evaluate_lot adds it via _leg_warnings.
    """
    return q["spread_pct"] is None or q["spread_pct"] <= spread_limit


def pick_put(puts: list[dict], spot: float, tier: float, *,
             spread_limit: float = MAX_SPREAD_PCT) -> dict | Reject:
    """Nearest real strike NOT above spot * tier; Reject if unquoted or spread > 5%."""
    quotes = core.quote_map(puts)
    target = spot * tier
    eligible = [k for k in quotes if k <= target + 1e-9]
    if not eligible:
        return Reject("", REASON_NO_PUT_QUOTE)
    quote = quotes[max(eligible)]
    if quote["strike"] < spot * (tier - MAX_TIER_MISS) - 1e-9:
        return Reject("", f"{REASON_SPARSE}：目标 {spot * tier:g}，最近行权价 {quote['strike']:g} "
                          f"仅到现价的 {quote['strike'] / spot * 100:.1f}%")
    if not _leg_ok(quote, spread_limit=spread_limit):
        return Reject("", f"{REASON_PUT_WIDE.format(limit=spread_limit)}：行权价 {quote['strike']:g}")
    return quote


def pick_zero_cost_call(calls: list[dict], spot: float, put_debit: float, *,
                        spread_limit: float = MAX_SPREAD_PCT) -> dict | None:
    """OTM call (strike > spot) whose mid is closest to the put debit; wide spreads skipped."""
    otm = [q for q in core.quote_map(calls).values() if q["strike"] > spot
           and _leg_ok(q, spread_limit=spread_limit)]
    if not otm:
        return None
    return min(otm, key=lambda q: (abs(q["mid"] - put_debit), q["strike"]))


def tax_warning(lot: HeldLot, today: date) -> str | None:
    """Warn for real lots in accounts that include a taxable account held under a year."""
    if lot.source != "real" or not lot.has_taxable_account:
        return None
    try:
        held = (today - date.fromisoformat(lot.earliest_entry_date)).days
    except (TypeError, ValueError):
        held = None  # missing or unparseable date counts as unknown
    if held is not None and held >= LONG_TERM_DAYS:
        return None
    return ("含应税账户且持股不满 1 年（或买入日未知）：买入 Put 可能重置持有期，"
            "将来卖出享受不到长期资本利得税率；税务细节策略页来源未覆盖，请先咨询税务专业人士")


def _leg_warnings(put: dict, call: dict | None, spread_limit: float = MAX_SPREAD_PCT,
                  spread_warn: float | None = None) -> list[str]:
    legs = [(name, q) for name, q in (("Put", put), ("Call", call)) if q is not None]
    out = [f"{name}报价陈旧" for name, q in legs if q["stale"]]
    for _, q in legs:
        out.extend(spread_warnings(q["spread_pct"], spread_limit, spread_warn))
    return out


def _lot_warnings(lot: HeldLot, today: date, *, call: dict | None, spot: float,
                  earnings: str, ex_div: date | None, expiry: str) -> list[str]:
    warnings: list[str] = []
    if earnings == "yes":
        warnings.append("信息：到期前有财报（Collar 常为财报而做，不排除）")
    elif earnings == "unknown":
        warnings.append("财报日期未知")
    if call is not None:
        warnings.extend(_call_warnings(lot, call, spot, ex_div, today, expiry))
    if lot.cost_estimated:
        warnings.append("成本为估算值")
    if lot.accounts > 1:
        warnings.append(f"来自 {lot.accounts} 个账户合并")
    tax = tax_warning(lot, today)
    if tax:
        warnings.append(tax)
    return warnings


def _call_warnings(lot: HeldLot, call: dict, spot: float, ex_div: date | None,
                   today: date, expiry: str) -> list[str]:
    out: list[str] = []
    if lot.cost_basis <= 0:
        out.append("成本未知，无法判断 Call 行权价是否低于成本")
    elif call["strike"] < lot.cost_basis:
        out.append(f"Call 行权价 ${call['strike']:g} 低于成本 ${lot.cost_basis:.2f}，若被行权会亏")
    if (ex_div is not None and today <= ex_div <= date.fromisoformat(expiry)
            and call["strike"] <= spot * (1 + EX_DIV_NEAR_PCT) + 1e-9):
        out.append(f"到期前有除息日 {ex_div.isoformat()} 且 Call 偏实值，提前被指派风险高")
    return out


def _candidate(tier: float, put: dict, call: dict | None, spot: float,
               contracts: int, warnings: list[str]) -> dict:
    """cap_cost: dollars of upside given up beyond the cap per contract, measured from spot."""
    call_credit = call["mid"] if call else 0.0
    return {
        "tier": tier, "put": put, "call": call, "contracts": contracts,
        "net_credit": call_credit - put["mid"], "floor": put["strike"],
        "cap": call["strike"] if call else None,
        "cap_cost": (call["strike"] - spot) * 100 if call else None,
        "warnings": warnings,
    }


def evaluate_lot(lot: HeldLot, *, snapshot: date, spot: float, puts: list[dict],
                 calls: list[dict], expiry: str, earnings: str, ex_div: date | None,
                 available_for_put: int, available_for_call: int,
                 spread_limit: float = MAX_SPREAD_PCT,
                 spread_warn: float | None = None) -> dict | Reject:
    """Per tier a collar candidate (or Reject) and a put-only candidate (or Reject).

    Only a lot with fewer than 100 shares is rejected whole; a lot whose calls are all
    covered keeps its put-only variants and carries a note instead of collar variants.
    """
    if available_for_put < 1:
        short = int(lot.shares // 100) < 1
        return Reject(lot.label, REASON_SHORT_SHARES if short else REASON_PUTS_USED)
    covered = available_for_call < 1
    tiers: list = []
    put_only: list = []
    prev_strike = None
    for tier in TIERS:
        put = pick_put(puts, spot, tier, spread_limit=spread_limit)
        if isinstance(put, Reject):
            rejected = Reject(lot.label, put.reason)
            put_only.append(rejected)
            if not covered:  # a covered lot has no collar variants: tiers stays []
                tiers.append(rejected)
            continue
        same = [WARN_SAME_STRIKE] if put["strike"] == prev_strike else []
        prev_strike = put["strike"]
        legs = _leg_warnings(put, None, spread_limit, spread_warn) + same
        base = _lot_warnings(lot, snapshot, call=None, spot=spot, earnings=earnings,
                             ex_div=ex_div, expiry=expiry)
        put_only.append(_candidate(tier, put, None, spot, available_for_put, base + legs))
        if covered:
            continue
        call = pick_zero_cost_call(calls, spot, put["mid"], spread_limit=spread_limit)
        if call is None:
            tiers.append(Reject(lot.label, REASON_NO_ZERO_CALL.format(limit=spread_limit)))
            continue
        w = (_lot_warnings(lot, snapshot, call=call, spot=spot, earnings=earnings,
                           ex_div=ex_div, expiry=expiry)
             + _leg_warnings(put, call, spread_limit, spread_warn) + same)
        tiers.append(_candidate(tier, put, call, spot, available_for_call, w))
    return {"tiers": tiers, "put_only": put_only, "note": NOTE_COVERED if covered else None}
