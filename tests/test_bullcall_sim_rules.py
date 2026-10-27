"""Tests for scripts/bullcall_sim_rules.py - pure bull call debit spread candidate rules, no network.

Invented ticker ZZZQ; paper-trading numbers only (design section 10 hand example).
"""
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bullcall_sim_rules as rules
import condor_sim_rules
import spread_core as core
from spread_fixtures import make_chain, make_call_chain

SNAP = date(2026, 10, 26)
DAYS = 35
EXPIRY = SNAP + timedelta(days=DAYS)

CANDIDATE_KEYS = {
    "delta_bucket", "status", "long_call_strike", "short_call_strike", "width",
    "long_call_mid", "short_call_mid", "debit", "breakeven", "breakeven_move_pct",
    "max_profit_move_pct", "long_call_delta", "short_call_delta", "long_call_iv",
    "short_call_iv", "underlying_price", "max_profit", "max_loss", "required_win_rate", "warnings",
}


def row(strike, bid, ask, delta=None, iv=None, last=None):
    """One raw call row; delta is the broker delta (None = absent). iv None skips the cross-check."""
    return {"strike": strike, "bid": bid, "ask": ask,
            "last": last if last is not None else (bid + ask) / 2, "iv": iv, "delta": delta}


def build(calls, width=5.0, earnings="no", ex_div=None, spot=100.0, days=DAYS):
    return rules.build_bullcall_candidates(spot, days, calls, width, earnings, ex_div, EXPIRY, SNAP)


def hand_chain():
    """Design section 10: spot 100, long 100 Call mid 3.20, short 105 Call mid 1.30, width 5."""
    return [row(100.0, 3.19, 3.21, 0.50, iv=0.40), row(105.0, 1.29, 1.31, 0.30, iv=0.40)]


def bs_chain():
    """Black-Scholes calls 80..130 at 35 days: buckets pick 98 (ITM) / 101 / 104, shorts +5."""
    return make_call_chain(strikes=[float(k) for k in range(80, 131)], days=DAYS, quote_spread=0.01)


def bucket(cands, b):
    return next(c for c in cands if c["delta_bucket"] == b)


def mid_bucket(calls, **kw):
    return bucket(build(calls, **kw), 0.50)


# --- long leg ---------------------------------------------------------------

def test_pick_long_call_nearest_delta_including_itm():
    pick = rules.pick_long_call(bs_chain(), 100.0, DAYS, 0.60)
    assert pick["quote"]["strike"] == 98.0  # in the money: delta 0.601
    assert pick["delta"] == pytest.approx(0.601, abs=0.001)
    assert rules.pick_long_call(bs_chain(), 100.0, DAYS, 0.50)["quote"]["strike"] == 101.0
    assert rules.pick_long_call(bs_chain(), 100.0, DAYS, 0.40)["quote"]["strike"] == 104.0


def test_long_strike_equal_to_spot_is_valid():
    # spot 102, long 102 mid 2.50, short 107 mid 0.46 -> debit 2.04, breakeven 104.04
    calls = [row(102.0, 2.49, 2.51, 0.50), row(107.0, 0.455, 0.465, 0.25)]
    c = mid_bucket(calls, spot=102.0)
    assert c["status"] == "ok" and c["long_call_strike"] == 102.0
    assert c["debit"] == pytest.approx(2.04)
    assert c["breakeven_move_pct"] == pytest.approx(round(2.04 / 102.0, 4))  # = debit / spot
    assert c["max_profit_move_pct"] == pytest.approx(round(107.0 / 102.0 - 1, 4))


def test_long_delta_gap_rejected():
    exact = [row(100.0, 3.19, 3.21, 0.55), row(105.0, 1.29, 1.31, 0.30)]  # gap exactly 0.05
    assert isinstance(rules.pick_long_call(exact, 100.0, DAYS, 0.50), dict)
    assert mid_bucket(exact)["status"] == "ok"
    over = [row(100.0, 3.19, 3.21, 0.56), row(105.0, 1.29, 1.31, 0.30)]
    reason = rules.pick_long_call(over, 100.0, DAYS, 0.50)
    assert isinstance(reason, str) and "Delta" in reason
    c = mid_bucket(over)
    assert c["status"] == "unavailable" and "Delta" in c["reason"]


def test_long_missing_delta_and_iv_rejected_not_raised():
    calls = [row(100.0, 3.19, 3.21, None, iv=None), row(105.0, 1.29, 1.31, None, iv=None)]
    assert isinstance(rules.pick_long_call(calls, 100.0, DAYS, 0.50), str)
    out = build(calls)
    assert all(c["status"] == "unavailable" and c["reason"] for c in out)
    # A long leg that can be judged but a short leg with neither delta nor iv: rejected, not raised.
    calls = [row(100.0, 3.19, 3.21, 0.50), row(105.0, 1.29, 1.31, None, iv=None)]
    c = mid_bucket(calls)
    assert c["status"] == "unavailable" and "短 Call" in c["reason"]


# --- short leg ----------------------------------------------------------------

def test_find_short_call_requires_exact_width():
    near = [row(100.0, 3.19, 3.21, 0.50), row(104.0, 1.59, 1.61, 0.33), row(106.0, 1.09, 1.11, 0.27)]
    reason = rules.find_short_call(near, 100.0, 5.0)
    assert isinstance(reason, str) and "上方没有等宽的短腿" in reason
    assert "上方没有等宽的短腿" in mid_bucket(near)["reason"]
    hit = rules.find_short_call(hand_chain(), 100.0, 5.0)
    assert hit["quote"]["strike"] == 105.0 and hit["warnings"] == []
    no_mid = [hand_chain()[0], {"strike": 105.0, "bid": 0, "ask": 0, "last": 0, "iv": 0.4, "delta": 0.3}]
    assert isinstance(rules.find_short_call(no_mid, 100.0, 5.0), str)


def test_either_leg_wide_spread_rejected():
    # mid 2.50 with spread 0.125, and mid 1.25 with spread 0.0625: exactly 5% (binary-exact)
    long_exact = [row(100.0, 2.4375, 2.5625, 0.50), row(105.0, 0.99, 1.01, 0.30)]
    assert mid_bucket(long_exact)["status"] == "ok"
    long_over = [row(100.0, 2.43, 2.57, 0.50), row(105.0, 0.99, 1.01, 0.30)]  # 5.6%
    c = mid_bucket(long_over)
    assert c["status"] == "unavailable" and "长 Call" in c["reason"] and "价差" in c["reason"]
    short_exact = [row(100.0, 3.19, 3.21, 0.50), row(105.0, 1.21875, 1.28125, 0.30)]
    assert mid_bucket(short_exact)["status"] == "ok"
    short_over = [row(100.0, 3.19, 3.21, 0.50), row(105.0, 1.21, 1.29, 0.30)]  # 6.4%
    c = mid_bucket(short_over)
    assert c["status"] == "unavailable" and "短 Call" in c["reason"] and "价差" in c["reason"]


def test_stale_quote_warns_not_rejects():
    calls = [{"strike": 100.0, "bid": 0, "ask": 0, "last": 3.20, "iv": 0.40, "delta": 0.50},
             {"strike": 105.0, "bid": 0, "ask": 0, "last": 1.30, "iv": 0.40, "delta": 0.30}]
    c = mid_bucket(calls)
    assert c["status"] == "ok"
    assert sum("报价陈旧" in w for w in c["warnings"]) == 2  # long + short


# --- money fields -------------------------------------------------------------

def test_debit_rounding_and_breakeven():
    calls = [row(100.0, 3.20, 3.21, 0.50), row(105.0, 1.30, 1.305, 0.30)]  # mids 3.205 / 1.3025
    c = mid_bucket(calls)
    assert c["long_call_mid"] == 3.205 and c["short_call_mid"] == 1.3025
    assert c["debit"] == 1.9025 and c["breakeven"] == 101.9025
    assert abs(c["debit"] - (c["long_call_mid"] - c["short_call_mid"])) <= core.BULLCALL_ROUNDING_TOL
    assert abs(c["breakeven"] - (c["long_call_strike"] + c["debit"])) <= core.BULLCALL_ROUNDING_TOL


def test_candidate_money_fields_match_hand_example():
    c = mid_bucket(hand_chain())
    assert c["status"] == "ok" and set(c) == CANDIDATE_KEYS
    assert (c["long_call_strike"], c["short_call_strike"], c["width"]) == (100.0, 105.0, 5.0)
    assert c["long_call_mid"] == pytest.approx(3.20) and c["short_call_mid"] == pytest.approx(1.30)
    assert c["debit"] == pytest.approx(1.90) and c["breakeven"] == pytest.approx(101.90)
    assert c["max_loss"] == 190.0 and c["max_profit"] == 310.0
    assert c["required_win_rate"] == pytest.approx(0.38)
    assert c["breakeven_move_pct"] == pytest.approx(0.019)  # +1.90%
    assert c["max_profit_move_pct"] == pytest.approx(0.05)  # +5.00%
    assert c["long_call_delta"] == 0.50 and c["short_call_delta"] == 0.30
    assert c["long_call_iv"] == 0.40 and c["short_call_iv"] == 0.40
    assert c["underlying_price"] == 100.0 and c["delta_bucket"] == 0.50


def test_nonpositive_or_full_width_debit_rejected():
    for long_bid, long_ask in ((1.29, 1.31), (1.19, 1.21), (6.29, 6.31), (6.39, 6.41)):
        # debit 0, -0.10, exactly 5 (= width), 5.10
        calls = [row(100.0, long_bid, long_ask, 0.50), row(105.0, 1.29, 1.31, 0.30)]
        c = mid_bucket(calls)
        assert c["status"] == "unavailable" and "净支出" in c["reason"], (long_bid, c)


# --- warnings -----------------------------------------------------------------

def test_poor_odds_warning_above_060():
    at = [row(100.0, 3.99, 4.01, 0.50), row(105.0, 0.99, 1.01, 0.30)]  # debit 3.00 / 5 = 0.60
    c = mid_bucket(at)
    assert c["status"] == "ok" and c["required_win_rate"] == pytest.approx(0.60)
    assert not any("赔率差" in w for w in c["warnings"])
    above = [row(100.0, 4.00, 4.02, 0.50), row(105.0, 0.99, 1.01, 0.30)]  # 3.01 / 5 = 0.602
    assert any("赔率差" in w for w in mid_bucket(above)["warnings"])


def test_earnings_in_window_warns():
    yes = mid_bucket(hand_chain(), earnings="yes")
    unknown = mid_bucket(hand_chain(), earnings="unknown")
    no = mid_bucket(hand_chain(), earnings="no")
    assert yes["status"] == "ok" and any("财报" in w and "满额亏损" in w for w in yes["warnings"])
    assert any("没取到" in w for w in unknown["warnings"])
    assert not any("财报" in w for w in no["warnings"])


def test_ex_div_with_near_short_call_warns():
    edge = [row(98.0, 4.19, 4.21, 0.50), row(103.0, 2.29, 2.31, 0.30)]  # 103 == 100 x 1.03
    in_window = date(2026, 11, 10)
    c = mid_bucket(edge, ex_div=in_window)
    assert c["status"] == "ok" and any("除息" in w for w in c["warnings"])
    beyond = [row(98.5, 4.19, 4.21, 0.50), row(103.5, 2.29, 2.31, 0.30)]
    assert not any("除息" in w for w in mid_bucket(beyond, ex_div=in_window)["warnings"])
    for when in (EXPIRY + timedelta(days=1), SNAP - timedelta(days=1), None):
        assert not any("除息" in w for w in mid_bucket(edge, ex_div=when)["warnings"])
    assert any("除息" in w for w in mid_bucket(edge, ex_div=EXPIRY)["warnings"])


def test_short_dte_warning_below_30_days():
    short = mid_bucket(hand_chain(), days=21)
    assert short["status"] == "ok" and any("时间不在你这边" in w for w in short["warnings"])
    assert not any("时间不在你这边" in w for w in mid_bucket(hand_chain(), days=30)["warnings"])


def test_every_ok_candidate_carries_full_debit_warning():
    out = build(bs_chain())
    assert [c["status"] for c in out] == ["ok", "ok", "ok"]
    for c in out:
        assert c["warnings"].count(rules.WARN_FULL_DEBIT) == 1
    w = rules.WARN_FULL_DEBIT
    assert "亏掉全部净支出" in w and "长腿行权价或以下" in w and "满额亏损" in w
    assert "靠赔率赚钱" in w


# --- buckets and wings ----------------------------------------------------------

def test_three_buckets_independent():
    base = build(bs_chain())
    assert [c["delta_bucket"] for c in base] == [0.60, 0.50, 0.40]
    assert [(c["long_call_strike"], c["short_call_strike"]) for c in base] == [
        (98.0, 103.0), (101.0, 106.0), (104.0, 109.0)]
    out = build([r for r in bs_chain() if r["strike"] != 106.0])
    assert [c["status"] for c in out] == ["ok", "unavailable", "ok"]
    assert out[0] == base[0] and out[2] == base[2]


def test_nonpositive_width_raises():
    for bad in (0.0, -5.0):
        with pytest.raises(ValueError):
            build(hand_chain(), width=bad)


def test_short_strike_needed_lists_missing_wings():
    assert rules.short_strike_needed(hand_chain(), 100.0, DAYS, 5.0) == ()
    assert rules.short_strike_needed(hand_chain()[:1], 100.0, DAYS, 5.0) == (105.0,)
    window = [r for r in bs_chain() if r["strike"] <= 105.0]
    assert rules.short_strike_needed(window, 100.0, DAYS, 5.0) == (106.0, 109.0)


def test_recommend_width_accepts_call_rows():
    rec = core.recommend_width(bs_chain(), 100.0, DAYS)
    assert rec is not None and rec["step"] == 1.0 and rec["width"] == pytest.approx(2.0)
    c = bucket(build(bs_chain(), width=rec["width"]), 0.50)
    assert c["status"] == "ok" and c["short_call_strike"] == 103.0


# --- existing paths pinned and no seller keys -----------------------------------

def test_existing_put_and_condor_candidates_unchanged():
    puts, calls = make_chain(quote_spread=0.01), make_call_chain(quote_spread=0.01)
    put_out = (core.build_candidates(100.0, 7, puts, width=5.0, earnings="yes")
               + core.build_candidates(100.0, 7, make_chain(stale=True)))
    condor_out = (condor_sim_rules.build_condor_candidates(
                      100.0, 7, puts, calls, 5.0, "unknown", date(2026, 10, 30),
                      date(2026, 11, 2), date(2026, 10, 26))
                  + condor_sim_rules.build_condor_candidates(
                      100.0, 7, puts, calls, None, "no", None, date(2026, 11, 2), date(2026, 10, 26)))

    def digest(x):
        return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()

    # Captured before bullcall_sim_rules existed.
    assert digest(put_out) == "68e558adc1d32f0361b0e386e4aa56a6582272b230041427ff75412990af32d2"
    assert digest(condor_out) == "8e5432d96e96f328f20fe6d0c0e96513ceb2aa0fb91eafe5001ced2a19190a6c"


def test_candidate_has_no_seller_keys():
    seller = {"credit", "short_strike", "long_strike", "net_credit", "exit_cost", "breached_side",
              "lot_ref", "breakeven_win_rate", "margin", "bucket", "available"}
    for c in build(bs_chain()) + build(hand_chain()):
        assert not (seller & set(c)), c
