#!/usr/bin/env python3
"""Pure selection rules for the covered-call screen (no I/O, no network).

Delta-0.20 OTM call choice with spread walk-up and the per-lot candidate/reject
decision. See docs/superpowers/specs/2026-10-21-call-screen-design.md.
"""
from __future__ import annotations
from datetime import date
from typing import Callable

import spread_core as core
import spread_data
from holdings import HeldLot
from put_screen_rules import (
    MAX_SPREAD_PCT, MAX_WALK_STEPS, REASON_DELTA_GAP, REASON_NO_IV,
    REASON_NO_MONTHLY, REASON_NO_WEEKLY, REASON_WIDE, TARGET_DELTA, Reject,
    DELTA_ARG_MAX, DELTA_ARG_MIN, delta_arg, is_default_delta, pick_monthly_expiry,
    pick_weekly_expiry, spread_warnings, walk_floor)

REASON_SHORT_SHARES = "股数不足 100 股"
REASON_COVERED = "已被未平仓 call 覆盖"
REASON_COST_UNKNOWN = "成本未知"


def pick_call(calls: list[dict], spot: float, days: float, *,
              spread_limit: float = MAX_SPREAD_PCT,
              spread_warn: float | None = None,
              target_delta: float = TARGET_DELTA) -> tuple[dict, float, list[str]] | str:
    """Strike above spot with Delta nearest the target; walk up if the spread is wide.

    Returns (quote, delta, warnings) or a reject-reason string.
    """
    otm = sorted(
        ((q, *core.delta_for("C", q, spot, days))
         for q in core.quote_map(calls).values()
         if q["strike"] > spot and (core._usable_iv(q["iv"]) or core.has_delta(q))),
        key=lambda qdw: qdw[0]["strike"])
    if not otm:
        return REASON_NO_IV
    start = min(range(len(otm)), key=lambda i: abs(otm[i][1] - target_delta))
    if abs(otm[start][1] - target_delta) > core.MAX_DELTA_GAP:
        return (f"{REASON_DELTA_GAP}：最接近 {target_delta:.2f} 的行权价 {otm[start][0]['strike']:g} "
                f"Delta {otm[start][1]:.2f}（IV 不可靠或行权价间距过粗）")
    floor = walk_floor(target_delta)
    for step in range(MAX_WALK_STEPS + 1):
        idx = start + step
        if idx >= len(otm):
            break
        quote, delta, cross = otm[idx]
        if step > 0 and delta < floor:
            break
        warnings: list[str] = []
        if quote["spread_pct"] is None:
            warnings.append("报价陈旧")
        elif quote["spread_pct"] > spread_limit:
            continue
        else:
            warnings.extend(spread_warnings(quote["spread_pct"], spread_limit, spread_warn))
        if step > 0:
            warnings.append(f"实际 Delta {delta:.2f}，已偏离 {target_delta:.2f}")
        if cross:
            warnings.append(cross)
        return quote, delta, warnings
    return REASON_WIDE


def _loss_text(cost: float, proceeds: float) -> str:
    loss = cost - proceeds
    if loss >= 0:
        return f"每股亏 ${loss:.2f}"
    return f"每股亏 $0.00（含权利金后小赚 ${-loss:.2f}）"


def _below_cost_warning(lot: HeldLot, strike: float, mid: float) -> str | None:
    if strike >= lot.original_cost and strike >= lot.cost_basis:
        return None
    proceeds = strike + mid
    return (f"行权价 ${strike:g} 低于成本：若被行权，相对原始成本（每股 ${lot.original_cost:.2f}）"
            f"{_loss_text(lot.original_cost, proceeds)}，相对有效成本 ${lot.cost_basis:.2f} "
            f"{_loss_text(lot.cost_basis, proceeds)}（均已含本次权利金 ${mid:.2f}/股）")


def _lot_warnings(lot: HeldLot, base: list[str], *, strike: float, mid: float,
                  earnings: str, ex_div: date | None, in_window: bool) -> list[str]:
    warnings = list(base)
    below = _below_cost_warning(lot, strike, mid)
    if below:
        warnings.append(below)
    if ex_div is not None:
        warnings.append(f"到期前有除息日 {ex_div.isoformat()}，提前被行权的概率更高")
    if earnings == "unknown":
        warnings.append("财报日期未知")
    if lot.cost_estimated:
        warnings.append("成本为估算值")
    if lot.accounts > 1:
        warnings.append(f"来自 {lot.accounts} 个账户合并")
    if not in_window:
        warnings.append("到期日在偏好窗口之外")
    return warnings


def _pct_vs(spot: float, level: float | None) -> float | None:
    return None if level is None else (spot / level - 1) * 100


def _precheck(lot: HeldLot, available: int) -> str | None:
    if available < 1:
        return REASON_SHORT_SHARES if int(lot.shares // 100) == 0 else REASON_COVERED
    return REASON_COST_UNKNOWN if lot.cost_basis <= 0 else None


def _lot_record(lot: HeldLot, quote: dict, delta: float, base: list[str], *, snapshot: date,
                spot: float, ma50: float | None, ma200: float | None, month_return: float | None,
                available: int, expiry: str, days: int, in_window: bool, dte_mode: str,
                earnings: str, ex_div: date | None, target_delta: float) -> dict:
    """The candidate record for a lot whose call was picked (no data access)."""
    strike, mid = quote["strike"], quote["mid"]
    record = {
        "ticker": lot.ticker, "source": lot.source, "lot_id": lot.lot_id, "label": lot.label,
        "shares": lot.shares, "contracts_available": available,
        "cost_basis": lot.cost_basis, "original_cost": lot.original_cost,
        "reference_price": lot.reference_price, "cost_estimated": lot.cost_estimated,
        "accounts": lot.accounts, "snapshot_date": snapshot.isoformat(), "spot": spot,
        "ma50_pct": _pct_vs(spot, ma50), "ma200_pct": _pct_vs(spot, ma200),
        "month_return_pct": month_return,
        "expiry": expiry, "days": days, "in_window": in_window, "dte_mode": dte_mode,
        "strike": strike, "mid": mid, "stale": quote["stale"],
        "spread_pct": quote["spread_pct"], "delta": delta, "iv": quote["iv"],
        "earnings": earnings, "ex_dividend": ex_div.isoformat() if ex_div else None,
        "premium_per_contract": mid * 100,
        "upside_to_strike_pct": (strike - spot) / spot * 100,
        "annualized_pct": mid / spot * 365 / days * 100,
        "warnings": _lot_warnings(lot, base, strike=strike, mid=mid, earnings=earnings,
                                  ex_div=ex_div, in_window=in_window),
    }
    if not is_default_delta(target_delta):  # absent key = built-in target, so old files stay valid
        record["target_delta"] = round(target_delta, 2)
    return record


def evaluate_lot(lot: HeldLot, *, snapshot: date, spot: float, ma50: float | None,
                 ma200: float | None, month_return: float | None, contracts_available: int,
                 expirations: list[str], earnings_for: Callable[[str], str],
                 ex_div_for: Callable[[str], date | None],
                 calls_for: Callable[[str], list[dict]],
                 dte_mode: str = "weekly", spread_limit: float = MAX_SPREAD_PCT,
                 spread_warn: float | None = None,
                 target_delta: float = TARGET_DELTA) -> dict | Reject:
    early = _precheck(lot, contracts_available)
    if early:
        return Reject(lot.label, early)
    weekly = dte_mode == "weekly"
    picked_expiry = (pick_weekly_expiry if weekly else pick_monthly_expiry)(expirations, snapshot)
    if picked_expiry is None:
        return Reject(lot.label, REASON_NO_WEEKLY if weekly else REASON_NO_MONTHLY)
    expiry, in_window = picked_expiry
    earnings = earnings_for(expiry)
    if earnings == "yes":
        return Reject(lot.label, "到期前有财报")
    try:
        calls = calls_for(expiry)
    except spread_data.SourceUnavailable:
        raise
    except spread_data.DataError:
        return Reject(lot.label, "取不到期权链")
    days = (date.fromisoformat(expiry) - snapshot).days
    picked = pick_call(calls, spot, days, spread_limit=spread_limit,
                       spread_warn=spread_warn, target_delta=target_delta)
    if isinstance(picked, str):
        return Reject(lot.label, picked)
    quote, delta, base = picked
    return _lot_record(lot, quote, delta, base, snapshot=snapshot, spot=spot, ma50=ma50,
                       ma200=ma200, month_return=month_return, available=contracts_available,
                       expiry=expiry, days=days, in_window=in_window, dte_mode=dte_mode,
                       earnings=earnings, ex_div=ex_div_for(expiry), target_delta=target_delta)
