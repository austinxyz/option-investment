#!/usr/bin/env python3
"""Pure selection rules for the cash-secured put screen (no I/O, no network).

Monthly expiry choice, Delta-0.20 strike choice with spread walk-down, and the
per-ticker candidate/reject decision. See
docs/superpowers/specs/2026-10-18-put-screen-design.md.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import spread_core as core
import spread_data
from put_screen_wiki import UniverseRow

LEVERAGED_ETFS = frozenset({"TSLL"})
TARGET_DELTA = 0.20
MIN_DELTA = 0.10
MAX_SPREAD_PCT = 0.05
# Yahoo/demo mode: option_settings.PROFILES carries the relaxed hard line and the warn line; the rule
# functions only receive them as `spread_limit` / `spread_warn` (defaults = IBKR, byte-identical).
SPREAD_WARN_TEXT = "买卖价差 {pct:.1%}，超过 IBKR 模式的 {ibkr:.0%} 门槛（Yahoo 报价延迟，放宽到 {limit:.0%}）"
RULES_LINE_SPREAD_YAHOO = "买卖价差 ≤10%（Yahoo 模式放宽；5%–10% 警告）"
MAX_WALK_STEPS = 3
WINDOW_DAYS = (30, 45)
WEEKLY_DAYS = (7, 14)
FALLBACK_DAYS = (21, 50)
FALLBACK_TARGET_DAYS = 37

REASON_WIDE = "买卖价差过宽"
REASON_NO_IV = "IV 缺失"
REASON_DELTA_GAP = "Delta 偏离目标过大"
REASON_NO_WEEKLY = "没有可用的周度到期"
REASON_NO_MONTHLY = "没有可用的月度到期"


@dataclass(frozen=True)
class Reject:
    ticker: str
    reason: str


def spread_warnings(pct: float | None, spread_limit: float = MAX_SPREAD_PCT,
                    spread_warn: float | None = None) -> list[str]:
    """One warning for a spread that passed a relaxed limit but is over the warn line.

    Unknown (stale) spreads and the default `spread_warn=None` never warn, so IBKR is unchanged.
    """
    if spread_warn is None or pct is None or pct <= spread_warn:
        return []
    return [SPREAD_WARN_TEXT.format(pct=pct, ibkr=MAX_SPREAD_PCT, limit=spread_limit)]


def monthly_expirations(expirations: list[str]) -> list[str]:
    """Third-week Fridays; a third-week Thursday only when its Friday is not listed."""
    listed = set(expirations)
    out: list[str] = []
    for e in expirations:
        d = date.fromisoformat(e)
        if not 15 <= d.day <= 21:
            continue
        if d.weekday() == 4:
            out.append(e)
        elif d.weekday() == 3 and (d + timedelta(days=1)).isoformat() not in listed:
            out.append(e)
    return out


def pick_monthly_expiry(expirations: list[str], snapshot: date) -> tuple[str, bool] | None:
    """(expiry, in_window): first monthly 30-45 days out, else nearest 37 days within 21-50."""
    monthlies = [(e, (date.fromisoformat(e) - snapshot).days) for e in monthly_expirations(expirations)]
    for e, days in monthlies:
        if WINDOW_DAYS[0] <= days <= WINDOW_DAYS[1]:
            return e, True
    fallback = [(e, d) for e, d in monthlies if FALLBACK_DAYS[0] <= d <= FALLBACK_DAYS[1]]
    if not fallback:
        return None
    e, _ = min(fallback, key=lambda ed: abs(ed[1] - FALLBACK_TARGET_DAYS))
    return e, False


def pick_weekly_expiry(expirations: list[str], snapshot: date) -> tuple[str, bool] | None:
    """(expiry, True): earliest listed expiry 7-14 days out (weeklies included), else None."""
    lo, hi = WEEKLY_DAYS
    inside = sorted(e for e in expirations
                    if lo <= (date.fromisoformat(e) - snapshot).days <= hi)
    return (inside[0], True) if inside else None


def pick_put(puts: list[dict], spot: float, days: float, *,
             spread_limit: float = MAX_SPREAD_PCT,
             spread_warn: float | None = None) -> tuple[dict, float, list[str]] | str:
    """Strike with Delta nearest 0.20; walk down to a lower strike if the spread is wide.

    Returns (quote, delta, warnings) or a reject-reason string.
    """
    otm = sorted(
        ((q, *core.delta_for("P", q, spot, days))
         for q in core.quote_map(puts).values()
         if q["strike"] < spot and (core._usable_iv(q["iv"]) or core.has_delta(q))),
        key=lambda qdw: -qdw[0]["strike"])
    if not otm:
        return REASON_NO_IV
    start = min(range(len(otm)), key=lambda i: abs(otm[i][1] - TARGET_DELTA))
    if abs(otm[start][1] - TARGET_DELTA) > core.MAX_DELTA_GAP:
        return (f"{REASON_DELTA_GAP}：最接近 0.20 的行权价 {otm[start][0]['strike']:g} "
                f"Delta {otm[start][1]:.2f}（IV 不可靠或行权价间距过粗）")
    for step in range(MAX_WALK_STEPS + 1):
        idx = start + step
        if idx >= len(otm):
            break
        quote, delta, cross = otm[idx]
        if step > 0 and delta < MIN_DELTA:
            break
        warnings: list[str] = []
        if quote["spread_pct"] is None:
            warnings.append("报价陈旧")
        elif quote["spread_pct"] > spread_limit:
            continue
        else:
            warnings.extend(spread_warnings(quote["spread_pct"], spread_limit, spread_warn))
        if step > 0:
            warnings.append(f"实际 Delta {delta:.2f}，已偏离 0.20")
        if cross:
            warnings.append(cross)
        return quote, delta, warnings
    return REASON_WIDE


def prescreen_reject(row: UniverseRow) -> str | None:
    """Rejects that need no market data, so the caller can skip the network for them."""
    if row.ticker in LEVERAGED_ETFS:
        return "杠杆 ETF"
    if row.status == "avoid":
        return "Status 为 Avoid"
    if row.status == "unrecognized":
        return "状态无法识别"
    return None


def _screen_reject(row: UniverseRow, spot: float, ma200: float) -> str | None:
    early = prescreen_reject(row)
    if early:
        return early
    if spot < ma200:
        return "收盘价低于 MA200"
    return None


def _candidate(row: UniverseRow, picked: tuple[dict, float, list[str]], *, snapshot: date,
               spot: float, ma50: float, expiry: str, days: int, in_window: bool,
               dte_mode: str, earnings: str) -> dict:
    """The candidate record for a ticker whose put was picked (no data access)."""
    quote, delta, put_warnings = picked
    warnings = list(put_warnings)
    if row.status == "under_review":
        warnings.append("论点复核中")
    if earnings == "unknown":
        warnings.append("财报日期未知")
    if spot < ma50:
        warnings.append("价格低于 MA50，被指派概率更高")
    if not in_window:
        warnings.append("到期日在偏好窗口之外")
    warnings.extend(row.notes)
    strike, mid = quote["strike"], quote["mid"]
    return {
        "ticker": row.ticker, "tier": row.tier, "status": row.status,
        "snapshot_date": snapshot.isoformat(), "spot": spot,
        "expiry": expiry, "days": days, "in_window": in_window, "dte_mode": dte_mode,
        "strike": strike, "mid": mid, "stale": quote["stale"],
        "spread_pct": quote["spread_pct"], "delta": delta, "iv": quote["iv"],
        "earnings": earnings,
        "cash_required": strike * 100,
        "breakeven": strike - mid,
        "buffer_pct": (spot - strike) / spot * 100,
        "annualized_pct": mid / strike * 365 / days * 100,
        "max_loss_assigned": (strike - mid) * 100,
        "warnings": warnings,
    }


def evaluate_ticker(row: UniverseRow, *, snapshot: date, spot: float, ma50: float,
                    ma200: float, expirations: list[str],
                    earnings_for: Callable[[str], str],
                    puts_for: Callable[[str], list[dict]],
                    dte_mode: str = "monthly", spread_limit: float = MAX_SPREAD_PCT,
                    spread_warn: float | None = None) -> dict | Reject:
    reason = _screen_reject(row, spot, ma200)
    if reason:
        return Reject(row.ticker, reason)
    weekly = dte_mode == "weekly"
    picked_expiry = (pick_weekly_expiry if weekly else pick_monthly_expiry)(expirations, snapshot)
    if picked_expiry is None:
        return Reject(row.ticker, REASON_NO_WEEKLY if weekly else REASON_NO_MONTHLY)
    expiry, in_window = picked_expiry
    earnings = earnings_for(expiry)
    if earnings == "yes":
        return Reject(row.ticker, "到期前有财报")
    try:
        puts = puts_for(expiry)
    except spread_data.SourceUnavailable:
        raise
    except spread_data.DataError:
        return Reject(row.ticker, "取不到期权链")
    days = (date.fromisoformat(expiry) - snapshot).days
    picked = pick_put(puts, spot, days, spread_limit=spread_limit,
                      spread_warn=spread_warn)
    if isinstance(picked, str):
        return Reject(row.ticker, picked)
    return _candidate(row, picked, snapshot=snapshot, spot=spot, ma50=ma50, expiry=expiry,
                      days=days, in_window=in_window, dte_mode=dte_mode, earnings=earnings)
