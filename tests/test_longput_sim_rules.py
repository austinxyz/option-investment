"""Tests for scripts/longput_sim_rules.py - pure long put candidate rules, no network.

Invented ticker ZQLP; paper-trading numbers only (long put design section 3 hand example).
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import longput_sim_rules as rules
import bearcall_sim_rules
import spread_core as core
from spread_fixtures import make_chain

SNAP = date(2026, 10, 26)
DAYS = 35
EXPIRY = SNAP + timedelta(days=DAYS)

CANDIDATE_KEYS = {
    "delta_bucket", "status", "put_strike", "put_mid", "debit", "breakeven",
    "breakeven_move_pct", "put_delta", "put_iv", "underlying_price", "max_profit", "max_loss",
    "warnings",
}
FORBIDDEN = {"credit", "short_strike", "long_strike", "net_credit", "exit_cost", "breached_side",
             "width", "required_win_rate", "lot_ref", "breakeven_win_rate", "margin", "bucket",
             "available", "long_put_strike", "short_put_strike", "max_profit_move_pct"}


def row(strike, bid, ask, delta=None, iv=None, last=None):
    """One raw put row; delta is the (negative) broker delta (None = absent)."""
    return {"strike": strike, "bid": bid, "ask": ask,
            "last": last if last is not None else (bid + ask) / 2, "iv": iv, "delta": delta}


def build(puts, earnings="no", ex_div=None, spot=100.0, days=DAYS):
    return rules.build_longput_candidates(spot, days, puts, earnings, ex_div, EXPIRY, SNAP)


def hand_chain():
    """Design section 3: spot 100, buy 95 Put mid 2.40."""
    return [row(95.0, 2.39, 2.41, -0.40, iv=0.40)]


def bs_chain():
    """Black-Scholes puts 80..110 at 35 days: buckets pick 101 / 98 / 95."""
    return make_chain(strikes=[float(k) for k in range(80, 111)], days=DAYS, quote_spread=0.01)


def bucket(cands, b):
    return next(c for c in cands if c["delta_bucket"] == b)


def mid_bucket(puts, **kw):
    return bucket(build(puts, **kw), 0.40)


def test_pick_nearest_abs_delta():
    assert [c["put_strike"] for c in build(bs_chain())] == [101.0, 98.0, 95.0]


def test_longput_strike_equal_spot_is_valid():
    c = mid_bucket([row(100.0, 3.99, 4.01, -0.40)])
    assert c["status"] == "ok" and c["put_strike"] == 100.0
    assert c["breakeven_move_pct"] == pytest.approx(-0.04)  # = -debit / spot


def test_delta_gap_rejected():
    assert mid_bucket([row(95.0, 2.39, 2.41, -0.45)])["status"] == "ok"  # gap exactly 0.05
    c = mid_bucket([row(95.0, 2.39, 2.41, -0.46)])
    assert c["status"] == "unavailable" and "Delta" in c["reason"]


def test_missing_delta_and_iv_rejected_not_raised():
    out = build([row(95.0, 2.39, 2.41, None, iv=None)])
    assert all(c["status"] == "unavailable" and c["reason"] for c in out)


def test_wide_spread_rejected_exact_limit_passes():
    assert mid_bucket([row(95.0, 2.4375, 2.5625, -0.40)])["status"] == "ok"  # exactly 5%
    c = mid_bucket([row(95.0, 2.43, 2.57, -0.40)])
    assert c["status"] == "unavailable" and "价差" in c["reason"]


def test_stale_quote_warns_not_rejects():
    c = mid_bucket([{"strike": 95.0, "bid": 0, "ask": 0, "last": 2.40, "iv": 0.40, "delta": -0.40}])
    assert c["status"] == "ok" and sum("报价陈旧" in w for w in c["warnings"]) == 1


def test_longput_candidate_hand_numbers():
    c = mid_bucket(hand_chain())
    assert c["status"] == "ok" and set(c) == CANDIDATE_KEYS
    assert c["put_strike"] == 95.0
    assert c["put_mid"] == pytest.approx(2.40) and c["debit"] == pytest.approx(2.40)
    assert c["max_loss"] == 240.0 and c["max_profit"] == 9260.0
    assert c["breakeven"] == pytest.approx(92.60)
    assert c["breakeven_move_pct"] == pytest.approx(-0.074)  # -7.40%
    assert c["put_delta"] == 0.40 and c["put_iv"] == 0.40
    assert c["underlying_price"] == 100.0 and c["delta_bucket"] == 0.40


def test_debit_rounding_and_breakeven():
    c = mid_bucket([row(95.0, 3.20, 3.21, -0.40)])  # mid 3.205
    assert c["put_mid"] == 3.205 and c["debit"] == 3.205 and c["breakeven"] == 91.795
    assert abs(c["debit"] - c["put_mid"]) <= core.LONGPUT_ROUNDING_TOL
    assert abs(c["breakeven"] - (c["put_strike"] - c["debit"])) <= core.LONGPUT_ROUNDING_TOL


def test_invalid_debit_rejected():
    # a price at or above the strike can never pay back
    c = mid_bucket([row(2.0, 2.0, 2.04, -0.40)], spot=2.5)
    assert c["status"] == "unavailable" and "净支出" in c["reason"]


def test_earnings_in_window_warns():
    yes = mid_bucket(hand_chain(), earnings="yes")
    assert any("财报" in w and "建议跳过" in w for w in yes["warnings"])
    assert any("没取到" in w for w in mid_bucket(hand_chain(), earnings="unknown")["warnings"])
    assert not any("财报" in w for w in mid_bucket(hand_chain())["warnings"])


def test_short_dte_warning_below_30_days():
    assert any("时间不在你这边" in w for w in mid_bucket(hand_chain(), days=21)["warnings"])
    assert not any("时间不在你这边" in w for w in mid_bucket(hand_chain(), days=30)["warnings"])


def test_every_ok_candidate_carries_fixed_risk_warnings():
    out = build(bs_chain())
    assert [c["status"] for c in out] == ["ok", "ok", "ok"]
    for c in out:
        assert c["warnings"].count(rules.WARN_THETA_DECAY) == 1
        assert sum(bearcall_sim_rules.WARN_DRIFT in w for w in c["warnings"]) == 1
    assert "Theta" in rules.WARN_THETA_DECAY and "亏光全部权利金" in rules.WARN_THETA_DECAY


def test_three_buckets_independent():
    base = build(bs_chain())
    out = build([r for r in bs_chain() if r["strike"] not in (97.0, 98.0, 99.0)])
    assert out[0] == base[0] and out[2] == base[2]
    assert [c["status"] for c in out] == ["ok", "unavailable", "ok"]


def test_candidate_has_no_forbidden_keys():
    for c in build(bs_chain()) + build(hand_chain()):
        assert not (FORBIDDEN & set(c)), c


# --- Yahoo-mode spread kwargs ----------------------------------------------------

YAHOO = {"spread_limit": 0.10, "spread_warn": 0.05}


def ybuild(puts, **kw):
    return rules.build_longput_candidates(100.0, DAYS, puts, "no", None, EXPIRY, SNAP, **kw)


def test_default_spread_kwargs_match_explicit_ibkr():
    for chain in (bs_chain(), hand_chain()):
        assert build(chain) == ybuild(chain, spread_limit=0.05, spread_warn=None)


def test_yahoo_spread_limit_and_warning():
    warned = bucket(ybuild([row(95.0, 2.50, 2.70, -0.40)], **YAHOO), 0.40)  # 7.7%
    assert warned["status"] == "ok" and any("Yahoo" in w for w in warned["warnings"])
    exact = [row(95.0, 2.375, 2.625, -0.40)]  # exactly 10%
    assert bucket(ybuild(exact, **YAHOO), 0.40)["status"] == "ok"
    assert mid_bucket(exact)["status"] == "unavailable"
    over = [row(95.0, 2.35, 2.65, -0.40)]  # 12%
    c = bucket(ybuild(over, **YAHOO), 0.40)
    assert c["status"] == "unavailable" and "10%" in c["reason"]
    assert "5%" in mid_bucket(over)["reason"]
