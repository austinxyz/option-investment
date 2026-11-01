"""Tests for scripts/rr_sim_rules.py - pure risk reversal candidate rules, no network.

Invented ticker ZZZQ; paper-trading numbers only (design section 8 hand example).
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import rr_sim_rules as rules
import spread_core as core
from spread_fixtures import make_chain, make_call_chain

SNAP = date(2026, 10, 27)
DAYS = 35
EXPIRY = SNAP + timedelta(days=DAYS)

OK_KEYS = {
    "delta_bucket", "status", "short_put_strike", "long_call_strike", "short_put_mid",
    "long_call_mid", "net_credit", "breakeven_down", "put_move_pct", "call_move_pct",
    "capital_basis", "upside_breakeven", "dead_zone", "short_put_delta", "long_call_delta",
    "short_put_iv", "long_call_iv", "underlying_price", "expiry", "days",
    "earnings_in_window", "warnings",
}


def row(strike, bid, ask, delta=None, iv=None, last=None):
    return {"strike": strike, "bid": bid, "ask": ask,
            "last": last if last is not None else (bid + ask) / 2, "iv": iv, "delta": delta}


def build(puts, calls, earnings="no", ex_div=None, spot=100.0, days=DAYS):
    return rules.build_rr_candidates(spot, days, puts, calls, earnings, ex_div, EXPIRY, SNAP)


def bucket(cands, b):
    return next(c for c in cands if c["delta_bucket"] == b)


def hand_puts(put_mid=2.0):
    return [row(90.0, put_mid - 0.01, put_mid + 0.01, -0.30, iv=0.40)]


def hand_calls(call_mid=1.5):
    return [row(110.0, call_mid - 0.01, call_mid + 0.01, 0.20, iv=0.40)]


def top(puts, calls, **kw):
    return bucket(build(puts, calls, **kw), 0.30)


# --- expiry -------------------------------------------------------------------

def test_expiry_picks_nearest_inside_window():
    exps = [(SNAP + timedelta(days=d)).isoformat() for d in (10, 29, 30, 45, 60, 61)]
    assert rules.pick_rr_expiry(exps, SNAP) == (SNAP + timedelta(days=30)).isoformat()


def test_expiry_window_edges_inclusive():
    only_60 = [(SNAP + timedelta(days=60)).isoformat()]
    assert rules.pick_rr_expiry(only_60, SNAP) == only_60[0]


def test_expiry_none_in_window():
    exps = [(SNAP + timedelta(days=d)).isoformat() for d in (14, 29, 61, 90)]
    assert rules.pick_rr_expiry(exps, SNAP) is None


def test_expiry_lowered_min_days():
    exps = [(SNAP + timedelta(days=14)).isoformat()]
    assert rules.pick_rr_expiry(exps, SNAP, min_days=7) == exps[0]


def test_expiry_min_days_below_floor_raises():
    with pytest.raises(ValueError):
        rules.pick_rr_expiry([], SNAP, min_days=6)


def test_no_expiry_reason_uses_actual_bounds():
    assert rules.no_expiry_reason() == "没有 30–60 天内的到期日"
    assert rules.no_expiry_reason(10) == "没有 10–60 天内的到期日"


def test_unavailable_candidates_cover_all_buckets():
    cands = rules.unavailable_candidates("x")
    assert [c["delta_bucket"] for c in cands] == [0.30, 0.20, 0.15]
    assert all(c["status"] == "unavailable" and c["reason"] == "x" for c in cands)


# --- hand examples ------------------------------------------------------------

def test_spec_example_net_credit():
    c = top(hand_puts(), hand_calls())
    assert c["status"] == "ok" and set(c) == OK_KEYS
    assert c["net_credit"] == 0.5
    assert c["capital_basis"] == 8950.0
    assert c["breakeven_down"] == 89.5
    assert c["dead_zone"] == [90.0, 110.0]
    assert c["put_move_pct"] == -0.1 and c["call_move_pct"] == 0.1
    assert c["upside_breakeven"] is None
    assert c["underlying_price"] == 100.0 and c["expiry"] == EXPIRY.isoformat()
    assert c["short_put_delta"] == 0.3 and c["long_call_delta"] == 0.2
    assert c["short_put_iv"] == 0.40 and c["long_call_iv"] == 0.40


def test_net_debit_example():
    c = top(hand_puts(1.20), hand_calls(1.80))
    assert c["net_credit"] == -0.6
    assert c["capital_basis"] == 9060.0
    assert c["upside_breakeven"] == 110.6
    assert rules.WARN_RR_DEBIT_HEAVY in c["warnings"]


def test_debit_exactly_at_fraction_does_not_warn():
    # put 1.20, call 1.50 -> debit 0.30 == 1.20 * 0.25
    c = top(hand_puts(1.20), hand_calls(1.50))
    assert c["net_credit"] == -0.3
    assert rules.WARN_RR_DEBIT_HEAVY not in c["warnings"]


def test_net_credit_never_warns_debit():
    assert rules.WARN_RR_DEBIT_HEAVY not in top(hand_puts(), hand_calls())["warnings"]


def test_iv_may_be_none():
    puts = [row(90.0, 1.99, 2.01, -0.30)]
    calls = [row(110.0, 1.49, 1.51, 0.20)]
    c = top(puts, calls)
    assert c["short_put_iv"] is None and c["long_call_iv"] is None


# --- fixed warnings -------------------------------------------------------------

def test_fixed_warnings_on_every_candidate_exact_text():
    puts = [row(90.0, 1.99, 2.01, -0.30, iv=0.4), row(85.0, 0.99, 1.01, -0.20, iv=0.4),
            row(80.0, 0.49, 0.51, -0.15, iv=0.4)]
    calls = [row(110.0, 1.49, 1.51, 0.2, iv=0.4)]
    cands = build(puts, calls)
    assert [c["status"] for c in cands] == ["ok"] * 3
    for c in cands:
        for w in (rules.WARN_RR_DOWNSIDE, rules.WARN_RR_DEAD_ZONE, rules.WARN_RR_WILLING):
            assert w in c["warnings"]
    assert rules.WARN_RR_DOWNSIDE.startswith("下行风险接近裸卖 Put：")
    assert rules.WARN_RR_DEAD_ZONE == "两个行权价之间两腿都作废；若 Call 是净支出，这笔钱白付"
    assert rules.WARN_RR_WILLING == "只适合真的愿意按 Put 行权价接股的标的"


# --- short put ------------------------------------------------------------------

def test_put_picks_nearest_delta_among_otm():
    puts = [row(95.0, 3.99, 4.01, -0.40), row(90.0, 1.99, 2.01, -0.31),
            row(85.0, 0.99, 1.01, -0.22)]
    pick = rules.pick_short_put(puts, 100.0, DAYS, 0.30)
    assert pick["quote"]["strike"] == 90.0


def test_put_itm_strikes_ignored():
    puts = [row(105.0, 6.99, 7.01, -0.30), row(90.0, 1.99, 2.01, -0.27)]
    pick = rules.pick_short_put(puts, 100.0, DAYS, 0.30)
    assert pick["quote"]["strike"] == 90.0


def test_put_strike_equal_spot_ignored():
    pick = rules.pick_short_put([row(100.0, 3.99, 4.01, -0.30)], 100.0, DAYS, 0.30)
    assert isinstance(pick, str)


def test_delta_gap_exactly_limit_passes():
    puts = [row(90.0, 1.99, 2.01, -0.25)]
    assert isinstance(rules.pick_short_put(puts, 100.0, DAYS, 0.30), dict)


def test_delta_gap_over_limit_rejected_with_strike():
    reason = rules.pick_short_put([row(90.0, 1.99, 2.01, -0.2499)], 100.0, DAYS, 0.30)
    assert isinstance(reason, str) and "90" in reason and "0.05" in reason


def test_put_without_delta_or_iv_skipped_then_rejected():
    reason = rules.pick_short_put([row(90.0, 1.99, 2.01)], 100.0, DAYS, 0.30)
    assert isinstance(reason, str)


def test_put_falls_back_to_black_scholes():
    puts = make_chain(spot=100.0, days=DAYS, strikes=[float(k) for k in range(80, 100)],
                      quote_spread=0.01)
    pick = rules.pick_short_put(puts, 100.0, DAYS, 0.30)
    assert isinstance(pick, dict) and abs(pick["delta"] - 0.30) <= core.MAX_DELTA_GAP


def test_put_spread_exactly_five_percent_passes():
    puts = [row(90.0, 1.95, 2.05, -0.30)]  # width 0.10 / mid 2.00 = 5.0%
    assert isinstance(rules.pick_short_put(puts, 100.0, DAYS, 0.30), dict)


def test_put_spread_just_over_five_percent_rejected_with_strike():
    puts = [row(90.0, 1.9499, 2.0501, -0.30)]
    reason = rules.pick_short_put(puts, 100.0, DAYS, 0.30)
    assert isinstance(reason, str) and "90" in reason and "5%" in reason


def test_wide_put_rejects_bucket():
    c = top([row(90.0, 1.50, 2.50, -0.30)], hand_calls())
    assert c["status"] == "unavailable" and "90" in c["reason"]


# --- long call ------------------------------------------------------------------

def test_call_picks_mid_nearest_put_mid():
    calls = [row(105.0, 3.49, 3.51, 0.3), row(110.0, 1.99, 2.01, 0.2), row(115.0, 0.99, 1.01, 0.1)]
    pick = rules.pick_long_call(calls, 100.0, 1.6)
    assert pick["quote"]["strike"] == 110.0


def test_call_tie_picks_lower_strike():
    calls = [row(115.0, 1.09, 1.11, 0.1), row(110.0, 1.89, 1.91, 0.2)]  # mids 1.10 and 1.90
    pick = rules.pick_long_call(calls, 100.0, 1.50)
    assert pick["quote"]["strike"] == 110.0


def test_call_only_otm_strikes():
    calls = [row(95.0, 5.99, 6.01, 0.6), row(100.0, 3.99, 4.01, 0.5)]
    assert isinstance(rules.pick_long_call(calls, 100.0, 4.0), str)


def test_call_no_zero_requirement_net_credit_may_be_large():
    calls = [row(110.0, 0.49, 0.51, 0.05)]
    c = top(hand_puts(), calls)
    assert c["net_credit"] == 1.5 and c["status"] == "ok"


def test_call_wide_spread_skipped_for_next_best():
    calls = [row(110.0, 1.0, 2.0, 0.2), row(115.0, 0.49, 0.51, 0.1)]  # first is 66% wide
    pick = rules.pick_long_call(calls, 100.0, 1.5)
    assert pick["quote"]["strike"] == 115.0


def test_call_spread_exactly_five_percent_passes():
    pick = rules.pick_long_call([row(110.0, 1.95, 2.05, 0.2)], 100.0, 2.0)
    assert isinstance(pick, dict)


def test_call_spread_just_over_five_percent_fails():
    reason = rules.pick_long_call([row(110.0, 1.9499, 2.0501, 0.2)], 100.0, 2.0)
    assert reason == "没有可用的虚值 Call（不存在或价差超过 5%）"


def test_no_call_rejects_bucket_with_reason():
    c = top(hand_puts(), [row(95.0, 5.99, 6.01, 0.6)])
    assert c["status"] == "unavailable"
    assert c["reason"] == "没有可用的虚值 Call（不存在或价差超过 5%）"


def test_call_delta_missing_still_candidate_with_warning():
    c = top(hand_puts(), [row(110.0, 1.49, 1.51)])
    assert c["status"] == "ok" and c["long_call_delta"] is None
    assert any("Delta" in w for w in c["warnings"])


def test_black_scholes_chain_end_to_end():
    puts = make_chain(spot=100.0, days=DAYS, strikes=[float(k) for k in range(70, 100)],
                      quote_spread=0.01)
    calls = make_call_chain(spot=100.0, days=DAYS, strikes=[float(k) for k in range(101, 131)],
                            quote_spread=0.01)
    for c in build(puts, calls):
        assert c["status"] == "ok"
        assert c["short_put_strike"] < 100.0 < c["long_call_strike"]
        assert rules.validate_candidate(c) is None


# --- stale / earnings / dividend -----------------------------------------------

def test_stale_quotes_only_warn():
    puts = [row(90.0, 0, 0, -0.30, last=2.0)]
    calls = [row(110.0, 0, 0, 0.20, last=1.5)]
    c = top(puts, calls)
    assert c["status"] == "ok" and c["net_credit"] == 0.5
    stale = [w for w in c["warnings"] if "陈旧" in w]
    assert len(stale) == 2


def test_earnings_yes_warns_and_flags():
    c = top(hand_puts(), hand_calls(), earnings="yes")
    assert c["status"] == "ok" and c["earnings_in_window"] is True
    assert any("财报" in w for w in c["warnings"])


def test_earnings_no_and_unknown():
    no = top(hand_puts(), hand_calls(), earnings="no")
    assert no["earnings_in_window"] is False and not any("财报" in w for w in no["warnings"])
    unk = top(hand_puts(), hand_calls(), earnings="unknown")
    assert unk["earnings_in_window"] is None and any("财报" in w for w in unk["warnings"])


def test_ex_dividend_before_expiry_warns():
    c = top(hand_puts(), hand_calls(), ex_div=SNAP + timedelta(days=10))
    assert any("除息" in w for w in c["warnings"])


def test_ex_dividend_after_expiry_or_past_no_warning():
    for d in (EXPIRY + timedelta(days=1), SNAP - timedelta(days=1)):
        c = top(hand_puts(), hand_calls(), ex_div=d)
        assert not any("除息" in w for w in c["warnings"])


def test_ex_dividend_on_expiry_day_warns():
    assert any("除息" in w for w in top(hand_puts(), hand_calls(), ex_div=EXPIRY)["warnings"])


# --- purity / validation -------------------------------------------------------

def test_inputs_not_mutated():
    puts, calls = hand_puts(), hand_calls()
    snap_p, snap_c = [dict(r) for r in puts], [dict(r) for r in calls]
    build(puts, calls)
    assert puts == snap_p and calls == snap_c


def test_validate_candidate_accepts_consistent_record():
    assert rules.validate_candidate(top(hand_puts(), hand_calls())) is None


def test_validate_candidate_tolerance_boundary():
    c = dict(top(hand_puts(), hand_calls()))
    c["net_credit"] = round(c["net_credit"] + core.RR_ROUNDING_TOL * 0.5, 6)
    c["breakeven_down"] = round(c["short_put_strike"] - c["net_credit"], 6)
    assert rules.validate_candidate(c) is None


def test_validate_candidate_rejects_bad_net_credit():
    c = dict(top(hand_puts(), hand_calls()))
    c["net_credit"] = 0.6
    assert "净权利金" in rules.validate_candidate(c)


def test_validate_candidate_rejects_bad_breakeven():
    c = dict(top(hand_puts(), hand_calls()))
    c["breakeven_down"] = 89.0
    assert "盈亏平衡" in rules.validate_candidate(c)


def test_module_does_not_use_seller_or_buyer_functions():
    src = (REPO_ROOT / "scripts" / "rr_sim_rules.py").read_text(encoding="utf-8")
    for name in ("threshold_flags(", "expiry_pnl(", "cost_to_close", "close_pnl(",
                 "unrealized_pnl", "breakeven_win_rate", "bullcall_"):
        assert name.replace("(", "") not in src.replace("rr_expiry_pnl", "").replace(
            "rr_close_pnl", "").replace("rr_threshold_flags", "")
