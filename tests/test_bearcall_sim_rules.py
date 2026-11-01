"""Tests for scripts/bearcall_sim_rules.py - pure bear call credit spread candidate rules, no network.

Invented ticker ZQBR; paper-trading numbers only (bearish design section 8 hand example).
"""
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bearcall_sim_rules as rules
import bullcall_sim_rules
import condor_sim_rules
import spread_core as core
from spread_fixtures import make_chain, make_call_chain

SNAP = date(2026, 10, 26)
DAYS = 7
EXPIRY = SNAP + timedelta(days=DAYS)

CANDIDATE_KEYS = {
    "delta_bucket", "status", "option_type", "short_strike", "long_strike", "width",
    "short_mid", "long_mid", "credit", "breakeven", "breakeven_move_pct", "max_loss_move_pct",
    "short_delta", "short_iv", "short_spread_pct", "long_spread_pct", "underlying_price",
    "max_profit", "max_loss", "required_win_rate", "warnings",
}
FORBIDDEN = {"bucket", "available", "breakeven_win_rate", "margin", "net_credit", "exit_cost",
             "breached_side", "lot_ref", "debit", "long_call_strike", "short_call_strike",
             "long_put_strike", "short_put_strike", "put_strike"}


def row(strike, bid, ask, delta=None, iv=None, last=None):
    """One raw call row; delta is the broker delta (None = absent). iv None skips the cross-check."""
    return {"strike": strike, "bid": bid, "ask": ask,
            "last": last if last is not None else (bid + ask) / 2, "iv": iv, "delta": delta}


def build(calls, width=5.0, earnings="no", ex_div=None, spot=100.0, days=DAYS):
    return rules.build_bearcall_candidates(spot, days, calls, width, earnings, ex_div, EXPIRY, SNAP)


def hand_chain():
    """Design section 8: spot 100, sell 105 Call mid 2.10, buy 110 Call mid 0.90, width 5."""
    return [row(105.0, 2.09, 2.11, 0.15, iv=0.40), row(110.0, 0.89, 0.91, 0.06, iv=0.40)]


def bs_chain():
    """Black-Scholes calls 100..125 at 7 days: buckets pick 108 / 106 / 105, longs +5."""
    return make_call_chain(strikes=[float(k) for k in range(100, 126)], days=DAYS,
                           quote_spread=0.001)


def bucket(cands, b):
    return next(c for c in cands if c["delta_bucket"] == b)


def mid_bucket(calls, **kw):
    return bucket(build(calls, **kw), 0.15)


# --- short leg ----------------------------------------------------------------

def test_pick_short_call_nearest_delta_otm_only():
    chain = bs_chain()
    assert rules.pick_short_call(chain, 100.0, DAYS, 0.20)["quote"]["strike"] == 105.0
    assert rules.pick_short_call(chain, 100.0, DAYS, 0.15)["quote"]["strike"] == 106.0
    assert rules.pick_short_call(chain, 100.0, DAYS, 0.10)["quote"]["strike"] == 108.0
    # an in-the-money call whose broker delta happens to match is never picked
    itm = [row(95.0, 6.0, 6.1, 0.15), row(100.0, 2.0, 2.05, 0.40)]
    assert isinstance(rules.pick_short_call(itm, 100.0, DAYS, 0.15), str)


def test_bearcall_short_strike_equal_spot_is_valid():
    calls = [row(100.0, 2.09, 2.11, 0.15), row(105.0, 0.89, 0.91, 0.05)]
    c = mid_bucket(calls)
    assert c["status"] == "ok" and c["short_strike"] == 100.0 and c["long_strike"] == 105.0
    assert c["credit"] == pytest.approx(1.20)
    assert c["breakeven_move_pct"] == pytest.approx(0.012)  # = credit / spot
    assert c["max_loss_move_pct"] == pytest.approx(0.05)


def test_short_delta_gap_rejected():
    exact = [row(105.0, 2.09, 2.11, 0.20), row(110.0, 0.89, 0.91, 0.06)]  # gap exactly 0.05
    assert isinstance(rules.pick_short_call(exact, 100.0, DAYS, 0.15), dict)
    assert mid_bucket(exact)["status"] == "ok"
    over = [row(105.0, 2.09, 2.11, 0.21), row(110.0, 0.89, 0.91, 0.06)]
    c = mid_bucket(over)
    assert c["status"] == "unavailable" and "Delta" in c["reason"]


def test_short_missing_delta_and_iv_rejected_not_raised():
    calls = [row(105.0, 2.09, 2.11, None, iv=None), row(110.0, 0.89, 0.91, None, iv=None)]
    out = build(calls)
    assert all(c["status"] == "unavailable" and c["reason"] for c in out)


# --- long leg -----------------------------------------------------------------

def test_find_long_call_requires_exact_width():
    near = [row(105.0, 2.09, 2.11, 0.15), row(109.0, 1.09, 1.11, 0.08), row(111.0, 0.69, 0.71, 0.04)]
    reason = rules.find_long_call(near, 105.0, 5.0)
    assert isinstance(reason, str) and "上方没有等宽的长腿" in reason
    assert "上方没有等宽的长腿" in mid_bucket(near)["reason"]
    hit = rules.find_long_call(hand_chain(), 105.0, 5.0)
    assert hit["quote"]["strike"] == 110.0 and hit["warnings"] == []
    no_mid = [hand_chain()[0], {"strike": 110.0, "bid": 0, "ask": 0, "last": 0, "iv": 0.4}]
    assert isinstance(rules.find_long_call(no_mid, 105.0, 5.0), str)


def test_either_leg_wide_spread_rejected():
    # mid 2.50 with spread 0.125, and mid 1.25 with spread 0.0625: exactly 5% (binary-exact)
    short_exact = [row(105.0, 2.4375, 2.5625, 0.15), row(110.0, 0.99, 1.01, 0.06)]
    assert mid_bucket(short_exact)["status"] == "ok"
    short_over = [row(105.0, 2.43, 2.57, 0.15), row(110.0, 0.99, 1.01, 0.06)]
    c = mid_bucket(short_over)
    assert c["status"] == "unavailable" and "短 Call" in c["reason"] and "价差" in c["reason"]
    long_exact = [row(105.0, 2.49, 2.51, 0.15), row(110.0, 1.21875, 1.28125, 0.06)]
    assert mid_bucket(long_exact)["status"] == "ok"
    long_over = [row(105.0, 2.49, 2.51, 0.15), row(110.0, 1.21, 1.29, 0.06)]
    c = mid_bucket(long_over)
    assert c["status"] == "unavailable" and "长 Call" in c["reason"] and "价差" in c["reason"]


def test_stale_quote_warns_not_rejects():
    calls = [{"strike": 105.0, "bid": 0, "ask": 0, "last": 2.10, "iv": 0.40, "delta": 0.15},
             {"strike": 110.0, "bid": 0, "ask": 0, "last": 0.90, "iv": 0.40, "delta": 0.06}]
    c = mid_bucket(calls)
    assert c["status"] == "ok"
    assert sum("报价陈旧" in w for w in c["warnings"]) == 2


# --- money fields -------------------------------------------------------------

def test_bearcall_candidate_hand_numbers():
    c = mid_bucket(hand_chain())
    assert c["status"] == "ok" and set(c) == CANDIDATE_KEYS
    assert c["option_type"] == "C"
    assert (c["short_strike"], c["long_strike"], c["width"]) == (105.0, 110.0, 5.0)
    assert c["short_mid"] == pytest.approx(2.10) and c["long_mid"] == pytest.approx(0.90)
    assert c["credit"] == pytest.approx(1.20)
    assert c["max_profit"] == 120.0 and c["max_loss"] == 380.0
    assert c["breakeven"] == pytest.approx(106.20)
    assert c["required_win_rate"] == pytest.approx(0.76)
    assert c["breakeven_move_pct"] == pytest.approx(0.062)  # +6.20%
    assert c["max_loss_move_pct"] == pytest.approx(0.10)  # +10.00%
    assert c["short_delta"] == 0.15 and c["short_iv"] == 0.40
    assert c["underlying_price"] == 100.0 and c["delta_bucket"] == 0.15


def test_credit_rounding_and_breakeven():
    calls = [row(105.0, 3.20, 3.21, 0.15), row(110.0, 1.30, 1.305, 0.06)]  # mids 3.205 / 1.3025
    c = mid_bucket(calls)
    assert c["short_mid"] == 3.205 and c["long_mid"] == 1.3025
    assert c["credit"] == 1.9025 and c["breakeven"] == 106.9025
    assert abs(c["credit"] - (c["short_mid"] - c["long_mid"])) <= core.BEARCALL_ROUNDING_TOL
    assert abs(c["breakeven"] - (c["short_strike"] + c["credit"])) <= core.BEARCALL_ROUNDING_TOL


def test_nonpositive_or_full_width_credit_rejected():
    for short_bid, short_ask in ((0.89, 0.91), (0.79, 0.81), (5.89, 5.91), (5.99, 6.01)):
        # credit 0, -0.10, exactly 5 (= width), 5.10
        calls = [row(105.0, short_bid, short_ask, 0.15), row(110.0, 0.89, 0.91, 0.06)]
        c = mid_bucket(calls)
        assert c["status"] == "unavailable" and "净权利金" in c["reason"], (short_bid, c)


# --- warnings -----------------------------------------------------------------

def test_earnings_in_window_warns():
    yes = mid_bucket(hand_chain(), earnings="yes")
    unknown = mid_bucket(hand_chain(), earnings="unknown")
    no = mid_bucket(hand_chain(), earnings="no")
    assert yes["status"] == "ok" and any("财报" in w and "建议跳过" in w for w in yes["warnings"])
    assert any("没取到" in w for w in unknown["warnings"])
    assert not any("财报" in w for w in no["warnings"])


def test_bearcall_ex_div_with_itm_short_call_warns():
    at_money = [row(100.0, 2.09, 2.11, 0.15), row(105.0, 0.89, 0.91, 0.05)]
    in_window = SNAP + timedelta(days=3)
    c = mid_bucket(at_money, ex_div=in_window)
    assert c["status"] == "ok" and any("除息" in w and "指派" in w for w in c["warnings"])
    assert any("除息" in w for w in mid_bucket(at_money, ex_div=EXPIRY)["warnings"])
    for when in (EXPIRY + timedelta(days=1), SNAP - timedelta(days=1), None):
        assert not any("除息" in w for w in mid_bucket(at_money, ex_div=when)["warnings"])
    # out of the money short call: no assignment warning even with ex-div in the window
    assert not any("除息" in w for w in mid_bucket(hand_chain(), ex_div=in_window)["warnings"])


def test_thin_credit_warns():
    calls = [row(105.0, 0.99, 1.01, 0.15), row(110.0, 0.79, 0.81, 0.06)]  # credit 0.20 / 5 = 4%
    c = mid_bucket(calls)
    assert c["status"] == "ok" and any("赔率" in w for w in c["warnings"])
    assert not any("赔率" in w for w in mid_bucket(hand_chain())["warnings"])


def test_every_ok_candidate_carries_fixed_risk_warnings():
    out = build(bs_chain())
    assert [c["status"] for c in out] == ["ok", "ok", "ok"]
    for c in out:
        assert c["warnings"].count(rules.WARN_BEAR_CALL_UPSIDE) == 1
        assert c["warnings"].count(rules.WARN_DRIFT) == 1
    assert "上涨亏钱" in rules.WARN_BEAR_CALL_UPSIDE
    assert "宽度减权利金" in rules.WARN_BEAR_CALL_UPSIDE
    assert "向上漂移" in rules.WARN_DRIFT and "看跌" in rules.WARN_DRIFT


# --- buckets and wings ----------------------------------------------------------

def test_three_buckets_independent():
    base = build(bs_chain())
    assert [c["delta_bucket"] for c in base] == [0.10, 0.15, 0.20]
    assert [(c["short_strike"], c["long_strike"]) for c in base] == [
        (108.0, 113.0), (106.0, 111.0), (105.0, 110.0)]
    out = build([r for r in bs_chain() if r["strike"] != 111.0])
    assert [c["status"] for c in out] == ["ok", "unavailable", "ok"]
    assert out[0] == base[0] and out[2] == base[2]


def test_nonpositive_width_raises():
    for bad in (0.0, -5.0):
        with pytest.raises(ValueError):
            build(hand_chain(), width=bad)


def test_long_strike_needed_lists_missing_wings():
    # 0.10 bucket picks the 110 call (delta 0.06) and so needs 115; 0.15 / 0.20 pick 105 -> 110 listed
    assert rules.long_strike_needed(hand_chain(), 100.0, DAYS, 5.0) == (115.0,)
    assert rules.long_strike_needed(hand_chain() + [row(115.0, 0.29, 0.31, 0.02)],
                                    100.0, DAYS, 5.0) == ()
    assert rules.long_strike_needed(hand_chain()[:1], 100.0, DAYS, 5.0) == (110.0,)
    window = [r for r in bs_chain() if r["strike"] <= 110.0]
    assert rules.long_strike_needed(window, 100.0, DAYS, 5.0) == (111.0, 113.0)


# --- existing paths pinned and no forbidden keys --------------------------------

def test_existing_candidate_builders_unchanged():
    puts, calls = make_chain(quote_spread=0.01), make_call_chain(quote_spread=0.01)
    put_out = (core.build_candidates(100.0, 7, puts, width=5.0, earnings="yes")
               + core.build_candidates(100.0, 7, make_chain(stale=True)))
    condor_out = (condor_sim_rules.build_condor_candidates(
                      100.0, 7, puts, calls, 5.0, "unknown", date(2026, 10, 30),
                      date(2026, 11, 2), date(2026, 10, 26))
                  + condor_sim_rules.build_condor_candidates(
                      100.0, 7, puts, calls, None, "no", None, date(2026, 11, 2), date(2026, 10, 26)))
    bc = make_call_chain(strikes=[float(k) for k in range(80, 131)], days=35, quote_spread=0.01)
    bull_out = (bullcall_sim_rules.build_bullcall_candidates(
                    100.0, 35, bc, 5.0, "yes", date(2026, 11, 10), date(2026, 11, 30),
                    date(2026, 10, 26))
                + bullcall_sim_rules.build_bullcall_candidates(
                    100.0, 35, make_call_chain(stale=True, days=35), 2.0, "no", None,
                    date(2026, 11, 30), date(2026, 10, 26)))

    def digest(x):
        return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()

    # Captured before the bearish candidate rules existed.
    assert digest(put_out) == "68e558adc1d32f0361b0e386e4aa56a6582272b230041427ff75412990af32d2"
    assert digest(condor_out) == "8e5432d96e96f328f20fe6d0c0e96513ceb2aa0fb91eafe5001ced2a19190a6c"
    assert digest(bull_out) == "6b866d62e7649ad3d14e4559a371abfc9af9a12e6a0be3757981ea1a5b3c8abd"


def test_candidate_has_no_forbidden_keys():
    for c in build(bs_chain()) + build(hand_chain()):
        assert not (FORBIDDEN & set(c)), c


# --- Yahoo-mode spread kwargs ----------------------------------------------------

YAHOO = {"spread_limit": 0.10, "spread_warn": 0.05}


def ybuild(calls, **kw):
    return rules.build_bearcall_candidates(100.0, DAYS, calls, 5.0, "no", None, EXPIRY, SNAP, **kw)


def test_default_spread_kwargs_match_explicit_ibkr():
    for chain in (bs_chain(), hand_chain()):
        assert build(chain) == ybuild(chain, spread_limit=0.05, spread_warn=None)


def test_yahoo_spread_limit_and_warning():
    long_ = row(110.0, 0.99, 1.01, 0.06)
    warned = bucket(ybuild([row(105.0, 2.50, 2.70, 0.15), long_], **YAHOO), 0.15)  # 7.7%
    assert warned["status"] == "ok" and any("Yahoo" in w for w in warned["warnings"])
    exact = [row(105.0, 2.375, 2.625, 0.15), long_]  # exactly 10%
    assert bucket(ybuild(exact, **YAHOO), 0.15)["status"] == "ok"
    assert bucket(build(exact), 0.15)["status"] == "unavailable"
    over = [row(105.0, 2.35, 2.65, 0.15), long_]  # 12%
    c = bucket(ybuild(over, **YAHOO), 0.15)
    assert c["status"] == "unavailable" and "短 Call" in c["reason"] and "10%" in c["reason"]
    assert "5%" in bucket(build(over), 0.15)["reason"]
    wide_long = [row(105.0, 2.49, 2.51, 0.15), row(110.0, 1.10, 1.40, 0.06)]  # 24%
    c = bucket(ybuild(wide_long, **YAHOO), 0.15)
    assert c["status"] == "unavailable" and "长 Call" in c["reason"] and "10%" in c["reason"]
    assert rules.long_strike_needed(exact[:1], 100.0, DAYS, 5.0) == ()  # short rejected at 5%
    assert rules.long_strike_needed(exact[:1], 100.0, DAYS, 5.0, spread_limit=0.10) == (110.0,)
