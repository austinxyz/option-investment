#!/usr/bin/env python3
"""Single home of every threshold and constant for /strategy-pick (no I/O).

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.4-6.12). Classification and
decision code reads these names; none of them may be re-typed as a literal elsewhere, so the
report's `thresholds_used` is generated from here and cannot drift from what the code does.
All comparisons are inclusive ("reaching" a threshold counts as a hit).
"""
from __future__ import annotations

from typing import NamedTuple

from put_screen_rules import LEVERAGED_ETFS as _BASE_LEVERAGED_ETFS

# Per-class thresholds (6.6). Class keys: bluechip / hot / etf (ETF has a single tier).
CLASSES = ("bluechip", "hot", "etf")
CLASS_THRESHOLDS: dict[str, dict[str, float]] = {
    "bluechip": {"VOL_HIGH": 0.35, "DD_HIGH": 0.25, "UP_RETURN": 0.12,
                 "FLAT_RETURN": 0.10, "RANGE_MAX": 0.35, "THIN_IV": 0.20},
    "hot": {"VOL_HIGH": 0.60, "DD_HIGH": 0.40, "UP_RETURN": 0.25,
            "FLAT_RETURN": 0.15, "RANGE_MAX": 0.60, "THIN_IV": 0.35},
    "etf": {"VOL_HIGH": 0.30, "DD_HIGH": 0.20, "UP_RETURN": 0.10,
            "FLAT_RETURN": 0.08, "RANGE_MAX": 0.25, "THIN_IV": 0.15},
}

# History windows (6.5). RV windows = closes - RV_WINDOW (252 closes -> 231).
LOOKBACK_DAYS = 252
MIN_HISTORY_DAYS = 200
MIN_SLOPE_DAYS = 220  # MA200 today plus MA200 MA_SLOPE_LAG sessions ago
MA_SHORT = 50
MA_LONG = 200
MA_SLOPE_LAG = 20
RV_WINDOW = 21
RECENT_DAYS = 63
MISSING_SESSIONS_WARN = 5
ANNUALIZE = 252

# Range detection (6.6) and the "last 3 months differ" hint.
TOUCH_BAND_PCT = 0.10
TOUCH_GAP_DAYS = 10
MIN_TOUCHES = 2
RECENT_RETURN_MIN = 0.05
RECENT_RV_RATIO_HIGH = 1.5
RECENT_RV_RATIO_LOW = 0.6

# Step 1 gate (6.7). Sub-results are only pass / fail / unchecked.
LEVERAGED_ETFS = frozenset(_BASE_LEVERAGED_ETFS) | frozenset()
MIN_MARKET_CAP = 2e9
MIN_PRICE = 10.0
MIN_DOLLAR_VOLUME = 20e6
GATE_EXPIRY_HORIZON_DAYS = 90
MIN_EXPIRIES_90D = 4
GATE_STRIKE_RANGE_PCT = 0.15
MIN_STRIKES_15PCT = 10
GATE_MAX_SPREAD_PCT = 0.10
# Put + call OI at the ATM strike of the MONTHLY expiry in the 20-60d window (select_oi_expiry),
# not of the weekly the spreads use: a 4-5 week weekly is thin even for SPY (2026-10-26: 730 vs
# 6282 on the monthly). Names listing only monthlies are measured exactly as before.
# Every near-money (GATE_STRIKE_RANGE_PCT) OI 0 / NaN = Yahoo has no OI (after hours), not a real
# zero: the item is unchecked with a warning. Any non-zero value there is measured as usual.
MIN_ATM_OI = 500

# Expiry used for IV (6.2).
IV_EXPIRY_MIN_DAYS = 20
IV_EXPIRY_MAX_DAYS = 60
IV_EXPIRY_TARGET_DAYS = 30
IV_METHOD_PRIMARY = "iv_vs_rv"
IV_METHOD_FALLBACK = "rv_vs_rv"
IV_HIGH_PCTL = 0.70
IV_LOW_PCTL = 0.30

# View (6.3).
STATUS_TO_VIEW = {"active": "bull", "use": "bull", "watch": "neutral", "avoid": "bear"}
VIEW_ALIASES = {"看好": "bull", "中性": "neutral", "不看好": "bear",
                "bull": "bull", "neutral": "neutral", "bear": "bear"}
THESIS_STALE_DAYS = 90
VERDICT_MAX_CHARS = 200

# Earnings (6.10).
EARNINGS_WINDOW_DAYS = 45
EARNINGS_IMMINENT_DAYS = 7

# Ranking and reference levels (6.8).
CSP_MAX_PORTFOLIO_SHARE = 0.10
NORMAL_DELTA = 0.20
FAR_OTM_DELTA = (0.10, 0.15)
BULLCALL_LONG_DELTA = (0.50, 0.60)
CONDOR_SHORT_DELTA = 0.15
COLLAR_PUT_DELTA = (0.20, 0.30)
COLLAR_CALL_DELTA = 0.20
Z_BY_DELTA = {0.30: 0.52, 0.20: 0.84, 0.15: 1.04, 0.10: 1.28}

# Holding (6.11).
MIN_LOT_SHARES = 100

# Strategy vocabulary (6.8, 6.12).
STRATEGIES = ("cash_secured_put", "bull_put_spread", "bull_call_spread", "iron_condor",
              "covered_call", "collar", "protective_put", "hold", "no_options",
              "wait_earnings", "reduce_position")
NOT_CHOSEN_ONLY = ("long_call", "naked_call", "bearish", "wheel")
FORBIDDEN_PICKS = frozenset({"long_call", "naked_call", "naked_put", "bearish",
                             "bear_put_spread", "bear_call_spread", "long_put"})
BULLISH_OPEN = frozenset({"cash_secured_put", "bull_put_spread", "bull_call_spread",
                          "iron_condor", "covered_call", "long_call"})
ALL_STRATEGY_KEYS = STRATEGIES + NOT_CHOSEN_ONLY
# The 13 spec codes (6.12) first; the rest name the remaining "why not" cases the tree produces.
WHY_NOT_CODES = ("not_held", "held_no_lot", "iv_high_buyer", "iv_low_seller",
                 "trend_not_range", "range_not_trend", "high_vol_buyer", "view_not_bullish",
                 "never_bearish", "never_naked", "no_tool", "wheel_is_flow",
                 "earnings_imminent",
                 "held_use_shares", "downtrend", "trend_unclear", "needs_floor", "upside_capped",
                 "leaf_choice", "option_fits", "wait_not_triggered", "reduce_not_applicable",
                 "gate_failed", "history_short")

# Decision tree (6.8-6.11) as data; strategy_pick_tree only looks these up.
VIEWS = ("bull", "neutral", "bear")
REGIME_LEAVES = ("steady_up", "high_vol", "range", "down", "unclear")
IV_LABELS = ("high", "mid", "low")
IV_HIGH, IV_MID, IV_LOW = IV_LABELS
ETF_CLASS = "etf"
BRANCH_HELD, BRANCH_NOT_HELD, BRANCH_NO_LOT = "held", "not_held", "no_lot"
COLLAPSED_VIEWS = frozenset({"bear"})  # leaf id is "bear.held" / "bear.not_held" only
LEAF_GATE_FAIL, LEAF_HISTORY_SHORT = "gate_fail", "history_short"
SPECIAL_LEAF_CODES = {LEAF_GATE_FAIL: "gate_failed", LEAF_HISTORY_SHORT: "history_short"}
EARNINGS_IMMINENT_CODE = "earnings_imminent"
MAX_PICKS = 2
SELL_PAIR = "csp_or_bull_put"  # leaf token: both, ordered by rank_csp_vs_bull_put
CSP_KEY, BULL_PUT_KEY, WAIT_EARNINGS = "cash_secured_put", "bull_put_spread", "wait_earnings"
CSP_FIRST_CLASSES = frozenset({"bluechip", "etf"})
CONTRACT_MULTIPLIER = 100
REF_DECIMALS = 2
UNKNOWN_CLASS_FALLBACK = "hot"
ETF_QUOTE_TYPE = "ETF"
STOCK_TYPE_ETF_MARK = "宽基"
CLASS_BY_TIER_EMOJI = {"🏦": "bluechip", "🚀": "hot", "⚡": "hot"}

# Leg plans: (leg, side, (delta_low, delta_high)); side -1 = below spot, +1 above, 0 = at spot.
LEG_PLANS = {
    "put_normal": (("short_put", -1, (NORMAL_DELTA, NORMAL_DELTA)),),
    "put_far": (("short_put", -1, FAR_OTM_DELTA),),
    "call_normal": (("short_call", 1, (NORMAL_DELTA, NORMAL_DELTA)),),
    "call_far": (("short_call", 1, FAR_OTM_DELTA),),
    "bullcall": (("long_call", 0, BULLCALL_LONG_DELTA),),
    "condor": (("short_put", -1, (CONDOR_SHORT_DELTA, CONDOR_SHORT_DELTA)),
               ("short_call", 1, (CONDOR_SHORT_DELTA, CONDOR_SHORT_DELTA))),
    "collar": (("long_put", -1, COLLAR_PUT_DELTA),
               ("short_call", 1, (COLLAR_CALL_DELTA, COLLAR_CALL_DELTA))),
    "protective": (("long_put", -1, COLLAR_PUT_DELTA),),
}
PLAN_FLAGS = {"put_far": ("far_otm",), "call_far": ("far_otm",)}
# The CSP-vs-Bull-Put share uses the 0.20 strike, or the 0.15 strike when far OTM (V9: 90.78).
RANK_DELTA_BY_PLAN = {"put_normal": NORMAL_DELTA, "put_far": max(FAR_OTM_DELTA)}

# Pick modifiers (6.6 THIN_IV, 6.10 earnings / ex-div, 6.11 LTCG and reduce hint).
SELLER_PICKS = frozenset({"cash_secured_put", "bull_put_spread", "iron_condor", "covered_call"})
EARNINGS_SENSITIVE = SELLER_PICKS  # the four strategies 6.10 names
EARNINGS_AVOID_CLASSES = frozenset({"hot"})
EARNINGS_WARN_CLASSES = frozenset({"bluechip"})
NO_EARNINGS_CLASSES = frozenset({"etf"})
EX_DIV_PICKS = frozenset({"covered_call"})
LTCG_PICKS = frozenset({"collar", "protective_put"})
REDUCE_HINT_VIEWS = frozenset({"neutral", "bear"})
EARNINGS_BEFORE_EXPIRY_FLAG = "earnings_before_expiry"


class LeafPick(NamedTuple):
    strategy: str
    plan: str | None = None
    flags: tuple[str, ...] = ()


def _p(strategy, plan=None, *flags) -> LeafPick:
    return LeafPick(strategy, plan, tuple(flags))


_PAIR, _PAIR_FAR = _p(SELL_PAIR, "put_normal"), _p(SELL_PAIR, "put_far")
_NONE_THEN_THIN_CSP = (_p("no_options"), _p("cash_secured_put", "put_far", "thin_premium"))
_COLLAR = (_p("collar", "collar"),)
_CC_FAR = (_p("covered_call", "call_far"),)

# (view, branch, regime, iv, picks, warning keys); "*" matches anything, first row wins.
_LEAF_ROWS = (
    ("bull", "not_held", "steady_up", "high", (_PAIR,), ()),
    ("bull", "not_held", "steady_up", "mid",
     (_p("bull_put_spread", "put_normal"), _p("bull_call_spread", "bullcall")), ()),
    ("bull", "not_held", "steady_up", "low", (_p("bull_call_spread", "bullcall"),), ()),
    ("bull", "not_held", "high_vol", "low", (_p(SELL_PAIR, "put_far", "thin_premium"),), ()),
    ("bull", "not_held", "high_vol", "*", (_PAIR_FAR,), ()),
    ("bull", "not_held", "range", "high", (_p("iron_condor", "condor"),), ()),
    ("bull", "not_held", "range", "mid", (_p("iron_condor", "condor", "required_win_rate"),), ()),
    ("bull", "not_held", "range", "low",
     (_p("iron_condor", "condor", "thin_premium", "required_win_rate"),), ()),
    ("bull", "not_held", "down", "*",
     (_p("cash_secured_put", "put_far", "want_shares_only"), _p("no_options")), ("bull_down",)),
    ("bull", "not_held", "unclear", "low", _NONE_THEN_THIN_CSP, ()),  # = neutral not-held
    ("bull", "not_held", "unclear", "*", (_PAIR_FAR,), ()),
    ("bull", "held", "steady_up", "high", _CC_FAR, ()),
    ("bull", "held", "steady_up", "mid",
     (_p("hold"), _p("covered_call", "call_far", "premium_average")), ()),
    ("bull", "held", "steady_up", "low", (_p("hold"), _p("protective_put", "protective")), ()),
    ("bull", "held", "high_vol", "*", _COLLAR, ()),
    ("bull", "held", "range", "low", (_p("covered_call", "call_normal", "thin_premium"),), ()),
    ("bull", "held", "range", "*", (_p("covered_call", "call_normal"),), ()),
    ("bull", "held", "down", "*", _COLLAR, ("bull_down",)),
    ("bull", "held", "unclear", "*", _CC_FAR, ()),
    ("neutral", "not_held", "down", "*", (_p("no_options"),), ()),
    ("neutral", "not_held", "*", "low", _NONE_THEN_THIN_CSP, ()),
    ("neutral", "not_held", "range", "*", (_p("iron_condor", "condor"),), ()),
    ("neutral", "not_held", "*", "*", (_PAIR_FAR,), ()),
    ("neutral", "held", "high_vol", "*", _COLLAR, ()),
    ("neutral", "held", "down", "*", _COLLAR, ()),
    ("neutral", "held", "*", "*", _CC_FAR, ()),
)
_SPECIAL_LEAVES = {
    "bear.held": ((_p("reduce_position"), _p("collar", "collar")), ("bear_held",)),
    "bear.not_held": ((_p("no_options"),), ("bear_not_held",)),
    LEAF_GATE_FAIL: ((_p("no_options"),), ()),
    LEAF_HISTORY_SHORT: ((_p("no_options"),), ()),
}


def _expand_leaves() -> dict[str, tuple[tuple[LeafPick, ...], tuple[str, ...]]]:
    table = dict(_SPECIAL_LEAVES)
    for view, branch, regime, iv, picks, warns in _LEAF_ROWS:
        for r in (REGIME_LEAVES if regime == "*" else (regime,)):
            for i in (IV_LABELS if iv == "*" else (iv,)):
                table.setdefault(f"{view}.{branch}.{r}.{i}", (picks, warns))
    return table


# leaf id -> (picks, warning keys)
LEAF_TABLE = _expand_leaves()

# Why-not rules: strategy -> ((condition, code), ...); first match wins, {} matches anything.
# Condition keys: view / held (held, not_held, no_lot) / regime / iv.
_BEAR, _HELD = {"view": {"bear"}}, {"held": {"held"}}
_NO_LOT, _NOT_HELD = {"held": {"no_lot"}}, {"held": {"not_held"}}
_SELLER_PUT_RULES = ((_BEAR, "view_not_bullish"), (_HELD, "held_use_shares"),
                     ({"regime": {"range"}}, "range_not_trend"),
                     ({"regime": {"down"}}, "downtrend"), ({"iv": {"low"}}, "iv_low_seller"),
                     ({}, "leaf_choice"))
REASON_RULES = {
    "cash_secured_put": _SELLER_PUT_RULES,
    "bull_put_spread": _SELLER_PUT_RULES,
    "bull_call_spread": (({"view": {"bear", "neutral"}}, "view_not_bullish"),
                         (_HELD, "held_use_shares"),
                         ({"regime": {"high_vol"}}, "high_vol_buyer"),
                         ({"regime": {"range"}}, "range_not_trend"),
                         ({"regime": {"down"}}, "downtrend"),
                         ({"regime": {"unclear"}}, "trend_unclear"),
                         ({"iv": {"high"}}, "iv_high_buyer"), ({}, "leaf_choice")),
    "iron_condor": ((_BEAR, "view_not_bullish"), (_HELD, "held_use_shares"),
                    ({"regime": {"steady_up", "high_vol", "down"}}, "trend_not_range"),
                    ({"regime": {"unclear"}}, "trend_unclear"),
                    ({"iv": {"low"}}, "iv_low_seller"), ({}, "leaf_choice")),
    "covered_call": ((_NO_LOT, "held_no_lot"), (_NOT_HELD, "not_held"),
                     (_BEAR, "view_not_bullish"),
                     ({"regime": {"high_vol", "down"}}, "needs_floor"),
                     ({"iv": {"low"}}, "iv_low_seller"), ({}, "leaf_choice")),
    "collar": ((_NO_LOT, "held_no_lot"), (_NOT_HELD, "not_held"),
               ({"view": {"bull"}}, "upside_capped"), ({}, "leaf_choice")),
    "protective_put": ((_NO_LOT, "held_no_lot"), (_NOT_HELD, "not_held"),
                       ({"iv": {"high"}}, "iv_high_buyer"), ({}, "leaf_choice")),
    "hold": ((_NO_LOT, "held_no_lot"), (_NOT_HELD, "not_held"), (_BEAR, "view_not_bullish"),
             ({}, "leaf_choice")),
    "no_options": (({}, "option_fits"),),
    "wait_earnings": (({}, "wait_not_triggered"),),
    "reduce_position": (({}, "reduce_not_applicable"),),
    "long_call": (({}, "no_tool"),),
    "naked_call": (({}, "never_naked"),),
    "bearish": (({}, "never_bearish"),),
    "wheel": (({}, "wheel_is_flow"),),
}

# Text tables (labels, next commands, why-not sentences, warnings) live in the text module.
from strategy_pick_text import (NEXT_COMMAND_CAVEATS, NEXT_COMMANDS,  # noqa: E402
                                STRATEGY_LABELS, WHY_NOT)

# Output (6.13) and exit codes.
OUTPUT_DIR = "data/strategy-picks/"
EXIT_OK, EXIT_DATA_FAIL, EXIT_USAGE, EXIT_NEEDS_VIEW = 0, 1, 2, 3


def thresholds_for(asset_class: str) -> dict[str, float]:
    """Flat dict of the class tier plus every class-independent threshold, for `thresholds_used`."""
    if asset_class not in CLASS_THRESHOLDS:
        raise ValueError(f"unknown asset class: {asset_class!r}")
    shared = {
        "MIN_HISTORY_DAYS": MIN_HISTORY_DAYS, "MIN_SLOPE_DAYS": MIN_SLOPE_DAYS,
        "TOUCH_BAND_PCT": TOUCH_BAND_PCT, "TOUCH_GAP_DAYS": TOUCH_GAP_DAYS,
        "MIN_TOUCHES": MIN_TOUCHES, "RECENT_RETURN_MIN": RECENT_RETURN_MIN,
        "RECENT_RV_RATIO_HIGH": RECENT_RV_RATIO_HIGH,
        "RECENT_RV_RATIO_LOW": RECENT_RV_RATIO_LOW,
        "MIN_MARKET_CAP": MIN_MARKET_CAP, "MIN_PRICE": MIN_PRICE,
        "MIN_DOLLAR_VOLUME": MIN_DOLLAR_VOLUME, "MIN_EXPIRIES_90D": MIN_EXPIRIES_90D,
        "MIN_STRIKES_15PCT": MIN_STRIKES_15PCT, "GATE_MAX_SPREAD_PCT": GATE_MAX_SPREAD_PCT,
        "MIN_ATM_OI": MIN_ATM_OI, "IV_HIGH_PCTL": IV_HIGH_PCTL, "IV_LOW_PCTL": IV_LOW_PCTL,
        "EARNINGS_WINDOW_DAYS": EARNINGS_WINDOW_DAYS,
        "EARNINGS_IMMINENT_DAYS": EARNINGS_IMMINENT_DAYS,
        "CSP_MAX_PORTFOLIO_SHARE": CSP_MAX_PORTFOLIO_SHARE,
        "MIN_LOT_SHARES": MIN_LOT_SHARES, "THESIS_STALE_DAYS": THESIS_STALE_DAYS,
    }
    return {**CLASS_THRESHOLDS[asset_class], **shared}
