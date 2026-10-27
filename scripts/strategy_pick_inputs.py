#!/usr/bin/env python3
"""Input stages for /strategy-pick: gate, IV expiry, view, class, IV position, holding, earnings.

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.2-6.4, 6.7, 6.10, 6.11). Pure
functions on plain data, no I/O; every threshold is a strategy_pick_rules name. The decision
itself is in strategy_pick_tree, which re-exports these names.
"""
from __future__ import annotations

import operator
import statistics
from dataclasses import dataclass
from datetime import date

import strategy_pick_rules as R
import strategy_pick_text as T
from put_screen_rules import monthly_expirations
from strategy_pick_metrics import percentile_le
from strategy_pick_regime import Evidence

PASS, FAIL, UNCHECKED = "pass", "fail", "unchecked"
_OPS = {">=": operator.ge, "<=": operator.le}


# ---- step 1: gate (6.7) ----

@dataclass(frozen=True)
class GateInputs:
    ticker: str
    asset_class: str
    price: float | None
    market_cap: float | None
    dollar_volume: float | None
    expiries_90d: int
    expiry_used: date | None
    strikes_15pct: int | None
    atm_put_bid: float | None
    atm_put_ask: float | None
    atm_call_bid: float | None
    atm_call_ask: float | None
    atm_oi: int | None


@dataclass(frozen=True)
class Gate:
    passed: bool
    items: tuple[Evidence, ...]
    warnings: tuple[str, ...]


def _check(metric: str, value, op: str, constant: str) -> Evidence:
    threshold = getattr(R, constant)
    if value is None:
        result = UNCHECKED
    else:
        result = PASS if _OPS[op](value, threshold) else FAIL
    return Evidence(f"{metric} {op} {constant}", value, threshold, constant, result)


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    """(ask - bid) / mid; None when a side is missing or zero (closed-market stale quote)."""
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return (ask - bid) / statistics.fmean((bid, ask))


def _leveraged_item(g: GateInputs) -> Evidence:
    result = FAIL if g.ticker.upper() in R.LEVERAGED_ETFS else PASS
    return Evidence("ticker not_in LEVERAGED_ETFS", None, None, "LEVERAGED_ETFS", result)


def _market_cap_item(g: GateInputs, warnings: list[str]) -> Evidence:
    if g.asset_class in R.NO_EARNINGS_CLASSES:  # ETFs: market cap only applies to stocks
        return _check("MarketCap", None, ">=", "MIN_MARKET_CAP")
    if g.market_cap is None:
        warnings.append(T.WARN["market_cap_missing"])
    return _check("MarketCap", g.market_cap, ">=", "MIN_MARKET_CAP")


def _expiry_items(g: GateInputs, warnings: list[str]) -> list[Evidence]:
    has_expiry = g.expiry_used is not None
    put = spread_pct(g.atm_put_bid, g.atm_put_ask) if has_expiry else None
    call = spread_pct(g.atm_call_bid, g.atm_call_ask) if has_expiry else None
    if not has_expiry:
        warnings.append(T.WARN["no_expiry"])
    elif put is None or call is None:
        warnings.append(T.WARN["stale_quotes"])
    if has_expiry and g.atm_oi is None:
        warnings.append(T.WARN["oi_missing"])
    return [_check("Strikes15pct", g.strikes_15pct if has_expiry else None, ">=",
                   "MIN_STRIKES_15PCT"),
            _check("PutSpreadPct", put, "<=", "GATE_MAX_SPREAD_PCT"),
            _check("CallSpreadPct", call, "<=", "GATE_MAX_SPREAD_PCT"),
            _check("AtmOI", g.atm_oi if has_expiry else None, ">=", "MIN_ATM_OI")]


def judge_gate(g: GateInputs) -> Gate:
    """All sub-items must pass; `unchecked` (missing data) never fails the gate."""
    warnings: list[str] = []
    items = [_leveraged_item(g), _market_cap_item(g, warnings),
             _check("Price", g.price, ">=", "MIN_PRICE"),
             _check("DollarVolume", g.dollar_volume, ">=", "MIN_DOLLAR_VOLUME"),
             _check("Expiries90d", g.expiries_90d, ">=", "MIN_EXPIRIES_90D")]
    items += _expiry_items(g, warnings)
    return Gate(all(i.result != FAIL for i in items), tuple(items), tuple(warnings))


def select_iv_expiry(expiries: list[date], snapshot: date) -> date | None:
    """Expiry within the 20-60 day window nearest 30 days; equally near -> the earlier one."""
    window = [e for e in expiries
              if R.IV_EXPIRY_MIN_DAYS <= (e - snapshot).days <= R.IV_EXPIRY_MAX_DAYS]
    if not window:
        return None
    return min(window, key=lambda e: (abs((e - snapshot).days - R.IV_EXPIRY_TARGET_DAYS), e))


def select_oi_expiry(expiries: list[date], snapshot: date, expiry_used: date | None) -> date | None:
    """Expiry for the ATM open-interest check (6.7): the monthly (third-week) expiry chosen like
    select_iv_expiry; `expiry_used` when no monthly is in the 20-60 day window.

    Open interest pools on the standard monthly; a weekly 4-5 weeks out is newly listed and thin
    even for SPY, so measuring it there fails the most liquid names. Names that list only
    monthlies are unaffected (their `expiry_used` already is the monthly).
    """
    if expiry_used is None:
        return None
    monthlies = [date.fromisoformat(e)
                 for e in monthly_expirations(sorted(e.isoformat() for e in expiries))]
    return select_iv_expiry(monthlies, snapshot) or expiry_used


# ---- steps 2-3: class and view (6.3, 6.4) ----

@dataclass(frozen=True)
class ViewDecision:
    view: str
    source: str        # user / wiki
    conflict: bool     # --view given and differs from the wiki mapping
    wiki_view: str | None


@dataclass(frozen=True)
class NeedsView:
    status: str | None
    prompt: str        # template with {ticker}; the CLI exits 3 and writes nothing


def normalize_view(raw: str) -> str:
    key = raw.strip().lower()
    if key not in R.VIEW_ALIASES:
        raise ValueError(f"unknown view: {raw!r}")
    return R.VIEW_ALIASES[key]


def resolve_view(status: str | None, cli_view: str | None) -> ViewDecision | NeedsView:
    wiki_view = R.STATUS_TO_VIEW.get(status) if status else None
    if cli_view is not None:
        view = normalize_view(cli_view)
        return ViewDecision(view, "user", wiki_view is not None and wiki_view != view, wiki_view)
    if wiki_view is None:
        return NeedsView(status, T.NEEDS_VIEW_PROMPT)
    return ViewDecision(wiki_view, "wiki", False, wiki_view)


def _emoji_class(text: str | None) -> str | None:
    hits = [cls for emoji, cls in R.CLASS_BY_TIER_EMOJI.items() if text and emoji in text]
    return hits[0] if hits else None


def _etf_source(is_etf_page, quote_type, in_etf_table, stock_type) -> str | None:
    if is_etf_page:
        return "etf_page"
    if quote_type == R.ETF_QUOTE_TYPE:
        return "quote_type"
    if in_etf_table or (stock_type and R.STOCK_TYPE_ETF_MARK in stock_type):
        return "etf_table"
    return None


def resolve_class(cli_class: str | None, is_etf_page: bool, quote_type: str | None,
                  in_etf_table: bool, wiki_tier: str | None,
                  stock_type: str | None) -> tuple[str, str, list[str]]:
    """First hit wins: --class, ETF evidence, wiki tier, position type; else hot + warning."""
    if cli_class:
        if cli_class not in R.CLASSES:
            raise ValueError(f"unknown class: {cli_class!r}")
        return cli_class, "cli", []
    etf = _etf_source(is_etf_page, quote_type, in_etf_table, stock_type)
    if etf:
        return R.ETF_CLASS, etf, []
    wiki_cls, type_cls = _emoji_class(wiki_tier), _emoji_class(stock_type)
    if wiki_cls:
        clash = [T.WARN["class_conflict"].format(wiki=wiki_tier, stock_type=stock_type)]
        return wiki_cls, "wiki_tier", clash if type_cls and type_cls != wiki_cls else []
    if type_cls:
        return type_cls, "stock_type", []
    return R.UNKNOWN_CLASS_FALLBACK, "fallback", [T.WARN["class_unknown"]]


# ---- step 5: IV position (6.2) ----

@dataclass(frozen=True)
class IvPosition:
    atm_iv: float | None
    percentile: float | None
    label: str
    method: str
    iv_rv_ratio: float | None
    evidence: tuple[Evidence, ...] = ()


def _pctl_evidence(pct: float, op: str, constant: str) -> Evidence:
    threshold = getattr(R, constant)
    hit = _OPS[op](pct, threshold)
    return Evidence(f"IVpctl {op} {constant}", pct, threshold, constant, "hit" if hit else "miss")


def iv_position(atm_iv: float | None, rv21_series, rv21_current: float | None) -> IvPosition:
    """Percentile of ATM IV (or, without it, current RV21) among the year's RV21 windows (proxy)."""
    series = list(rv21_series)
    value = atm_iv if atm_iv is not None else rv21_current
    if not series or value is None:
        raise ValueError("IV position needs RV21 windows and an ATM IV or a current RV21")
    method = R.IV_METHOD_PRIMARY if atm_iv is not None else R.IV_METHOD_FALLBACK
    pct = percentile_le(value, series)
    high = _pctl_evidence(pct, ">=", "IV_HIGH_PCTL")
    low = _pctl_evidence(pct, "<=", "IV_LOW_PCTL")
    label = R.IV_HIGH if high.result == "hit" else (R.IV_LOW if low.result == "hit" else R.IV_MID)
    ratio = atm_iv / rv21_current if atm_iv is not None and rv21_current else None
    return IvPosition(atm_iv, pct, label, method, ratio, (high, low))


# ---- step 6: holding (6.11) ----

@dataclass(frozen=True)
class Holding:
    held_shares_total: float
    max_lot_shares: float
    lot_count: int
    ltcg_reached: bool
    treated_as_held: bool

    @property
    def branch(self) -> str:
        if self.treated_as_held:
            return R.BRANCH_HELD
        return R.BRANCH_NO_LOT if self.held_shares_total > 0 else R.BRANCH_NOT_HELD


def holding_state(lot_shares: list[float], ltcg_flags: list[bool]) -> Holding:
    """One entry per account row; a contract needs MIN_LOT_SHARES in a single account."""
    if len(lot_shares) != len(ltcg_flags):
        raise ValueError("lot_shares and ltcg_flags differ in length")
    lots = [(s, f) for s, f in zip(lot_shares, ltcg_flags) if s > 0]
    max_lot = max((s for s, _ in lots), default=0)
    # LTCG matters for the lots a collar would sit on; with none, fall back to every lot.
    flags = [f for s, f in lots if s >= R.MIN_LOT_SHARES] or [f for _, f in lots]
    return Holding(sum(s for s, _ in lots), max_lot, len(lots), bool(flags) and all(flags),
                   max_lot >= R.MIN_LOT_SHARES)


# ---- earnings (6.10) ----

@dataclass(frozen=True)
class Earnings:
    date: date | None
    days: int | None
    in_window: bool
    imminent: bool
    unknown: bool
    ex_div: date | None    # ETF only: kept when inside the window


def _within(target: date | None, snapshot: date, limit: int) -> int | None:
    if target is None:
        return None
    days = (target - snapshot).days
    return days if 0 <= days <= limit else None


def earnings_state(asset_class: str, next_earnings: date | None, snapshot: date,
                   ex_div: date | None) -> Earnings:
    if asset_class in R.NO_EARNINGS_CLASSES:
        in_win = _within(ex_div, snapshot, R.EARNINGS_WINDOW_DAYS) is not None
        return Earnings(None, None, False, False, False, ex_div if in_win else None)
    if next_earnings is None:
        return Earnings(None, None, False, False, True, None)
    days = (next_earnings - snapshot).days
    return Earnings(next_earnings, days,
                    _within(next_earnings, snapshot, R.EARNINGS_WINDOW_DAYS) is not None,
                    _within(next_earnings, snapshot, R.EARNINGS_IMMINENT_DAYS) is not None,
                    False, None)
