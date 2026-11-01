"""Tests for scripts/bearput_sim_rules.py - pure bear put debit spread candidate rules, no network.

Invented ticker ZQBP; paper-trading numbers only (bearish design section 8 hand example).
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bearput_sim_rules as rules
import bearcall_sim_rules
import spread_core as core
from spread_fixtures import make_chain

SNAP = date(2026, 10, 26)
DAYS = 35
EXPIRY = SNAP + timedelta(days=DAYS)

CANDIDATE_KEYS = {
    "delta_bucket", "status", "long_put_strike", "short_put_strike", "width",
    "long_put_mid", "short_put_mid", "debit", "breakeven", "breakeven_move_pct",
    "max_profit_move_pct", "long_put_delta", "short_put_delta", "long_put_iv",
    "short_put_iv", "underlying_price", "max_profit", "max_loss", "required_win_rate", "warnings",
}
FORBIDDEN = {"credit", "short_strike", "long_strike", "net_credit", "exit_cost", "breached_side",
             "lot_ref", "breakeven_win_rate", "margin", "bucket", "available", "option_type"}


def row(strike, bid, ask, delta=None, iv=None, last=None):
    """One raw put row; delta is the (negative) broker delta (None = absent)."""
    return {"strike": strike, "bid": bid, "ask": ask,
            "last": last if last is not None else (bid + ask) / 2, "iv": iv, "delta": delta}


def build(puts, width=10.0, earnings="no", ex_div=None, spot=100.0, days=DAYS):
    return rules.build_bearput_candidates(spot, days, puts, width, earnings, ex_div, EXPIRY, SNAP)


def hand_chain():
    """Design section 8: spot 100, buy 100 Put mid 4.80, sell 90 Put mid 1.80, width 10."""
    return [row(100.0, 4.79, 4.81, -0.50, iv=0.40), row(90.0, 1.79, 1.81, -0.20, iv=0.40)]


def bs_chain():
    """Black-Scholes puts 80..110 at 35 days: buckets pick 104 (ITM) / 101 / 98, shorts -5."""
    return make_chain(strikes=[float(k) for k in range(80, 111)], days=DAYS, quote_spread=0.01)


def bucket(cands, b):
    return next(c for c in cands if c["delta_bucket"] == b)


def mid_bucket(puts, **kw):
    return bucket(build(puts, **kw), 0.50)


# --- long leg ---------------------------------------------------------------

def test_pick_long_put_nearest_abs_delta_including_itm():
    chain = bs_chain()
    assert rules.pick_long_put(chain, 100.0, DAYS, 0.60)["quote"]["strike"] == 104.0  # ITM
    assert rules.pick_long_put(chain, 100.0, DAYS, 0.50)["quote"]["strike"] == 101.0
    assert rules.pick_long_put(chain, 100.0, DAYS, 0.40)["quote"]["strike"] == 98.0


def test_long_delta_gap_rejected():
    exact = [row(100.0, 4.79, 4.81, -0.55), row(90.0, 1.79, 1.81, -0.20)]  # gap exactly 0.05
    assert isinstance(rules.pick_long_put(exact, 100.0, DAYS, 0.50), dict)
    assert mid_bucket(exact)["status"] == "ok"
    over = [row(100.0, 4.79, 4.81, -0.56), row(90.0, 1.79, 1.81, -0.20)]
    c = mid_bucket(over)
    assert c["status"] == "unavailable" and "Delta" in c["reason"]


def test_missing_delta_and_iv_rejected_not_raised():
    puts = [row(100.0, 4.79, 4.81, None), row(90.0, 1.79, 1.81, None)]
    assert all(c["status"] == "unavailable" and c["reason"] for c in build(puts))
    puts = [row(100.0, 4.79, 4.81, -0.50), row(90.0, 1.79, 1.81, None)]
    c = mid_bucket(puts)
    assert c["status"] == "unavailable" and "短 Put" in c["reason"]


# --- short leg ----------------------------------------------------------------

def test_find_short_put_requires_exact_width():
    near = [row(100.0, 4.79, 4.81, -0.50), row(91.0, 2.09, 2.11, -0.22), row(89.0, 1.59, 1.61, -0.18)]
    reason = rules.find_short_put(near, 100.0, 10.0)
    assert isinstance(reason, str) and "下方没有等宽的短腿" in reason
    assert "下方没有等宽的短腿" in mid_bucket(near)["reason"]
    hit = rules.find_short_put(hand_chain(), 100.0, 10.0)
    assert hit["quote"]["strike"] == 90.0 and hit["warnings"] == []


def test_either_leg_wide_spread_rejected():
    long_exact = [row(100.0, 2.4375, 2.5625, -0.50), row(90.0, 0.99, 1.01, -0.20)]
    assert mid_bucket(long_exact)["status"] == "ok"
    long_over = [row(100.0, 2.43, 2.57, -0.50), row(90.0, 0.99, 1.01, -0.20)]
    c = mid_bucket(long_over)
    assert c["status"] == "unavailable" and "长 Put" in c["reason"] and "价差" in c["reason"]
    short_exact = [row(100.0, 4.79, 4.81, -0.50), row(90.0, 1.21875, 1.28125, -0.20)]
    assert mid_bucket(short_exact)["status"] == "ok"
    short_over = [row(100.0, 4.79, 4.81, -0.50), row(90.0, 1.21, 1.29, -0.20)]
    c = mid_bucket(short_over)
    assert c["status"] == "unavailable" and "短 Put" in c["reason"] and "价差" in c["reason"]


def test_stale_quote_warns_not_rejects():
    puts = [{"strike": 100.0, "bid": 0, "ask": 0, "last": 4.80, "iv": 0.40, "delta": -0.50},
            {"strike": 90.0, "bid": 0, "ask": 0, "last": 1.80, "iv": 0.40, "delta": -0.20}]
    c = mid_bucket(puts)
    assert c["status"] == "ok"
    assert sum("报价陈旧" in w for w in c["warnings"]) == 2


# --- money fields -------------------------------------------------------------

def test_bearput_candidate_hand_numbers():
    c = mid_bucket(hand_chain())
    assert c["status"] == "ok" and set(c) == CANDIDATE_KEYS
    assert (c["long_put_strike"], c["short_put_strike"], c["width"]) == (100.0, 90.0, 10.0)
    assert c["long_put_mid"] == pytest.approx(4.80) and c["short_put_mid"] == pytest.approx(1.80)
    assert c["debit"] == pytest.approx(3.00)
    assert c["max_loss"] == 300.0 and c["max_profit"] == 700.0
    assert c["breakeven"] == pytest.approx(97.00)
    assert c["required_win_rate"] == pytest.approx(0.30)
    assert c["breakeven_move_pct"] == pytest.approx(-0.03)  # -3.00%
    assert c["max_profit_move_pct"] == pytest.approx(-0.10)  # -10.00%
    assert c["long_put_delta"] == 0.50 and c["short_put_delta"] == 0.20
    assert c["long_put_iv"] == 0.40 and c["short_put_iv"] == 0.40
    assert c["underlying_price"] == 100.0 and c["delta_bucket"] == 0.50


def test_debit_rounding_and_breakeven():
    puts = [row(100.0, 3.20, 3.21, -0.50), row(90.0, 1.30, 1.305, -0.20)]  # mids 3.205 / 1.3025
    c = mid_bucket(puts)
    assert c["long_put_mid"] == 3.205 and c["short_put_mid"] == 1.3025
    assert c["debit"] == 1.9025 and c["breakeven"] == 98.0975
    assert abs(c["debit"] - (c["long_put_mid"] - c["short_put_mid"])) <= core.BEARPUT_ROUNDING_TOL
    assert abs(c["breakeven"] - (c["long_put_strike"] - c["debit"])) <= core.BEARPUT_ROUNDING_TOL


def test_nonpositive_or_full_width_debit_rejected():
    for long_bid, long_ask in ((1.79, 1.81), (1.69, 1.71), (11.79, 11.81), (11.89, 11.91)):
        # debit 0, -0.10, exactly 10 (= width), 10.10
        puts = [row(100.0, long_bid, long_ask, -0.50), row(90.0, 1.79, 1.81, -0.20)]
        c = mid_bucket(puts)
        assert c["status"] == "unavailable" and "净支出" in c["reason"], (long_bid, c)


# --- warnings -----------------------------------------------------------------

def test_earnings_in_window_warns():
    yes = mid_bucket(hand_chain(), earnings="yes")
    unknown = mid_bucket(hand_chain(), earnings="unknown")
    no = mid_bucket(hand_chain(), earnings="no")
    assert yes["status"] == "ok" and any("财报" in w and "建议跳过" in w for w in yes["warnings"])
    assert any("没取到" in w for w in unknown["warnings"])
    assert not any("财报" in w for w in no["warnings"])


def test_bearput_short_dte_warning_below_30_days():
    short = mid_bucket(hand_chain(), days=21)
    assert short["status"] == "ok" and any("时间不在你这边" in w for w in short["warnings"])
    assert not any("时间不在你这边" in w for w in mid_bucket(hand_chain(), days=30)["warnings"])


def test_no_ex_div_warning_for_puts():
    c = mid_bucket(hand_chain(), ex_div=SNAP + timedelta(days=5))
    assert not any("除息" in w for w in c["warnings"])


def test_every_ok_candidate_carries_fixed_risk_warnings():
    out = build(bs_chain(), width=5.0)
    assert [c["status"] for c in out] == ["ok", "ok", "ok"]
    for c in out:
        assert c["warnings"].count(rules.WARN_FULL_DEBIT_BEARPUT) == 1
        assert c["warnings"].count(bearcall_sim_rules.WARN_DRIFT) == 1
    w = rules.WARN_FULL_DEBIT_BEARPUT
    assert "亏掉全部净支出" in w and "所需胜率低于一半是正常的" in w


# --- buckets and wings ----------------------------------------------------------

def test_three_buckets_independent():
    base = build(bs_chain(), width=5.0)
    assert [c["delta_bucket"] for c in base] == [0.60, 0.50, 0.40]
    assert [(c["long_put_strike"], c["short_put_strike"]) for c in base] == [
        (104.0, 99.0), (101.0, 96.0), (98.0, 93.0)]
    out = build([r for r in bs_chain() if r["strike"] != 96.0], width=5.0)
    assert [c["status"] for c in out] == ["ok", "unavailable", "ok"]
    assert out[0] == base[0] and out[2] == base[2]


def test_nonpositive_width_raises():
    for bad in (0.0, -5.0):
        with pytest.raises(ValueError):
            build(hand_chain(), width=bad)


def test_short_strike_needed_lists_missing_wings():
    assert rules.short_strike_needed(hand_chain(), 100.0, DAYS, 10.0) == ()
    assert rules.short_strike_needed(hand_chain()[:1], 100.0, DAYS, 10.0) == (90.0,)
    window = [r for r in bs_chain() if r["strike"] >= 97.0]
    assert rules.short_strike_needed(window, 100.0, DAYS, 5.0) == (93.0, 96.0)


def test_recommend_width_on_put_chain():
    rec = core.recommend_width(bs_chain(), 100.0, DAYS)
    assert rec is not None and rec["step"] == 1.0
    c = bucket(build(bs_chain(), width=rec["width"]), 0.50)
    assert c["status"] == "ok" and c["short_put_strike"] == 101.0 - rec["width"]


def test_candidate_has_no_forbidden_keys():
    for c in build(bs_chain(), width=5.0) + build(hand_chain()):
        assert not (FORBIDDEN & set(c)), c


# --- Yahoo-mode spread kwargs ----------------------------------------------------

YAHOO = {"spread_limit": 0.10, "spread_warn": 0.05}


def ybuild(puts, width=10.0, **kw):
    return rules.build_bearput_candidates(100.0, DAYS, puts, width, "no", None, EXPIRY, SNAP, **kw)


def test_default_spread_kwargs_match_explicit_ibkr():
    assert build(bs_chain(), width=5.0) == ybuild(bs_chain(), 5.0, spread_limit=0.05, spread_warn=None)
    assert build(hand_chain()) == ybuild(hand_chain(), spread_limit=0.05, spread_warn=None)


def test_yahoo_spread_limit_and_warning():
    short = row(90.0, 0.99, 1.01, -0.20)
    warned = mid_bucket_y([row(100.0, 2.50, 2.70, -0.50), short])  # 7.7%
    assert warned["status"] == "ok" and any("Yahoo" in w for w in warned["warnings"])
    exact = [row(100.0, 2.375, 2.625, -0.50), short]  # exactly 10%
    assert mid_bucket_y(exact)["status"] == "ok"
    assert mid_bucket(exact)["status"] == "unavailable"
    over = [row(100.0, 2.35, 2.65, -0.50), short]  # 12%
    c = mid_bucket_y(over)
    assert c["status"] == "unavailable" and "长 Put" in c["reason"] and "10%" in c["reason"]
    assert "5%" in mid_bucket(over)["reason"]
    wide_short = [row(100.0, 4.79, 4.81, -0.50), row(90.0, 1.10, 1.40, -0.20)]  # 24%
    c = mid_bucket_y(wide_short)
    assert c["status"] == "unavailable" and "短 Put" in c["reason"] and "10%" in c["reason"]
    assert rules.short_strike_needed(exact[:1], 100.0, DAYS, 10.0) == ()
    assert rules.short_strike_needed(exact[:1], 100.0, DAYS, 10.0, spread_limit=0.10) == (90.0,)


def mid_bucket_y(puts):
    return bucket(ybuild(puts, **YAHOO), 0.50)
