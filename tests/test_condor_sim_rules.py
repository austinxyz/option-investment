"""Tests for scripts/condor_sim_rules.py - pure candidate rules, no network.

Invented ticker ZZZQ; paper-trading numbers only.
"""
from datetime import date
from pathlib import Path
import hashlib
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import condor_sim_rules as rules
import spread_core as core
from spread_fixtures import make_chain, make_call_chain

SNAP = date(2026, 10, 26)
EXPIRY = date(2026, 11, 2)


def row(strike, bid, ask, delta=None, iv=0.40, last=None):
    """One raw chain row; delta is the signed broker delta (None = absent)."""
    return {"strike": strike, "bid": bid, "ask": ask, "last": last if last is not None else (bid + ask) / 2,
            "iv": iv, "delta": delta}


def build(puts, calls, width=5.0, earnings="no", ex_div=None, spot=100.0, days=7):
    return rules.build_condor_candidates(spot, days, puts, calls, width, earnings, ex_div, EXPIRY, SNAP)


def hand_chain():
    """Width 5, net credit 1.50: put side 1.30-0.70=0.60, call side 1.20-0.30=0.90."""
    puts = [row(85.0, 0.69, 0.71, -0.03), row(90.0, 1.29, 1.31, -0.10)]
    calls = [row(110.0, 1.19, 1.21, 0.10), row(115.0, 0.29, 0.31, 0.03)]
    return puts, calls


def bucket(cands, b):
    return next(c for c in cands if c["delta_bucket"] == b)


# --- short legs -----------------------------------------------------------

def test_pick_short_put_nearest_delta_otm():
    pick = rules.pick_short_put(make_chain(strikes=[float(k) for k in range(80, 105)], quote_spread=0.01), 100.0, 7, 0.15)
    assert pick["quote"]["strike"] == 95.0
    assert pick["delta"] == pytest.approx(0.167, abs=0.001)


def test_pick_short_call_nearest_delta_otm():
    pick = rules.pick_short_call(make_call_chain(strikes=[float(k) for k in range(95, 121)], quote_spread=0.01), 100.0, 7, 0.15)
    assert pick["quote"]["strike"] == 106.0
    assert pick["delta"] == pytest.approx(0.156, abs=0.001)


def test_short_leg_wide_spread_rejected():
    puts = make_chain(quote_spread=0.5)  # ~25% of a 0.2-ish price
    assert isinstance(rules.pick_short_put(puts, 100.0, 7, 0.15), str)
    calls = make_call_chain(quote_spread=0.5)
    reason = rules.pick_short_call(calls, 100.0, 7, 0.15)
    assert isinstance(reason, str) and "价差" in reason


def test_short_leg_delta_gap_rejected():
    puts = [row(80.0, 0.20, 0.22, -0.02)]
    reason = rules.pick_short_put(puts, 100.0, 7, 0.20)
    assert isinstance(reason, str) and "Delta" in reason


def test_short_leg_missing_delta_and_iv_rejected_not_raised():
    puts = [row(90.0, 1.0, 1.02, None, iv=None)]
    calls = [row(110.0, 1.0, 1.02, None, iv=None)]
    assert isinstance(rules.pick_short_put(puts, 100.0, 7, 0.10), str)
    assert isinstance(rules.pick_short_call(calls, 100.0, 7, 0.10), str)
    out = build(puts + [row(85.0, 0.5, 0.52)], calls + [row(115.0, 0.5, 0.52)])
    assert all(c["status"] == "unavailable" and c["reason"] for c in out)


def test_short_leg_ignores_itm_strikes():
    puts = [row(105.0, 6.0, 6.02, -0.10)]  # ITM put with a tempting delta
    assert isinstance(rules.pick_short_put(puts, 100.0, 7, 0.10), str)
    calls = [row(95.0, 6.0, 6.02, 0.10)]
    assert isinstance(rules.pick_short_call(calls, 100.0, 7, 0.10), str)


# --- long wings -----------------------------------------------------------

def test_find_put_long_reports_actual_width():
    puts = make_chain()
    got = rules.find_put_long(puts, 95.0, 5.0)
    assert got["quote"]["strike"] == 90.0 and got["width"] == 5.0
    sparse = [r for r in puts if r["strike"] in (91.0, 95.0)]
    got = rules.find_put_long(sparse, 95.0, 5.0)  # nearest to 90 is 91 -> width 4
    assert got["quote"]["strike"] == 91.0 and got["width"] == 4.0


def test_find_put_long_rejects_far_wing():
    sparse = [r for r in make_chain() if r["strike"] in (94.5, 95.0)]
    assert isinstance(rules.find_put_long(sparse, 95.0, 5.0), str)


def test_find_call_long_requires_exact_width():
    calls = make_call_chain()
    assert rules.find_call_long(calls, 106.0, 5.0)["quote"]["strike"] == 111.0
    no_exact = [r for r in calls if r["strike"] != 111.0]
    reason = rules.find_call_long(no_exact, 106.0, 5.0)
    assert isinstance(reason, str) and "上方没有等宽的长腿" in reason


def test_wing_wide_spread_warns_not_rejects():
    puts, calls = hand_chain()
    puts[0] = row(85.0, 0.50, 0.90, -0.03)  # 57% spread on the long put
    calls[1] = row(115.0, 0.20, 0.40, 0.03)  # 67% on the long call
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "ok"
    assert sum("长翼" in w and "价差" in w for w in c["warnings"]) == 2


def test_stale_quote_warns_not_rejects():
    puts, calls = hand_chain()
    puts[1] = {"strike": 90.0, "bid": 0, "ask": 0, "last": 1.30, "iv": 0.40, "delta": -0.10}
    calls[0] = {"strike": 110.0, "bid": 0, "ask": 0, "last": 1.20, "iv": 0.40, "delta": 0.10}
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "ok"
    assert sum("报价陈旧" in w for w in c["warnings"]) == 2  # short put + short call


# --- candidate numbers ----------------------------------------------------

def test_candidate_credits_and_net_credit_sum():
    c = bucket(build(*hand_chain()), 0.10)
    assert c["status"] == "ok"
    assert (c["put_short_strike"], c["put_long_strike"]) == (90.0, 85.0)
    assert (c["call_short_strike"], c["call_long_strike"]) == (110.0, 115.0)
    assert c["width"] == 5.0
    assert c["put_credit"] == pytest.approx(0.60)
    assert c["call_credit"] == pytest.approx(0.90)
    assert c["net_credit"] == pytest.approx(1.50)
    assert c["put_short_delta"] == pytest.approx(0.10)
    assert c["call_short_delta"] == pytest.approx(0.10)
    assert c["put_short_iv"] == 0.40 and c["call_short_iv"] == 0.40
    assert c["underlying_price"] == 100.0
    assert "credit" not in c and "short_strike" not in c and "long_strike" not in c


def test_candidate_breakevens_and_max_loss_and_required_win_rate():
    c = bucket(build(*hand_chain()), 0.10)
    assert c["breakeven_low"] == pytest.approx(88.5)
    assert c["breakeven_high"] == pytest.approx(111.5)
    assert c["max_loss"] == pytest.approx(350.0)  # (5 - 1.50) * 100
    assert c["required_win_rate"] == pytest.approx(0.70)


def test_thin_credit_warning_uses_net_over_width():
    puts, calls = hand_chain()
    c = bucket(build(puts, calls), 0.10)  # 1.50 / 5 = 30%: not thin
    assert not any("权利金仅占" in w for w in c["warnings"])
    puts[1] = row(90.0, 0.345, 0.355, -0.10)  # put side 0.35 - 0.05 = 0.30
    puts[0] = row(85.0, 0.04, 0.06, -0.03)
    calls[0] = row(110.0, 0.345, 0.355, 0.10)  # call side 0.35 - 0.30 = 0.05 -> net 0.35, 7%
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "ok" and c["net_credit"] == pytest.approx(0.35)
    assert any("权利金仅占" in w for w in c["warnings"])


def test_up_move_full_width_warning_always_present():
    c = bucket(build(*hand_chain()), 0.10)
    assert any("大涨" in w and "满额" in w for w in c["warnings"])


def test_earnings_in_window_warns():
    yes = bucket(build(*hand_chain(), earnings="yes"), 0.10)
    unknown = bucket(build(*hand_chain(), earnings="unknown"), 0.10)
    no = bucket(build(*hand_chain(), earnings="no"), 0.10)
    assert any("财报" in w for w in yes["warnings"])
    assert any("没取到" in w for w in unknown["warnings"])
    assert not any("财报" in w for w in no["warnings"])


def test_ex_div_with_near_short_call_warns():
    puts, calls = hand_chain()
    near = [row(102.0, 1.99, 2.01, 0.10), row(107.0, 0.29, 0.31, 0.03)]
    c = bucket(build(puts, near, ex_div=date(2026, 10, 30)), 0.10)
    assert c["status"] == "ok" and any("除息" in w for w in c["warnings"])
    far = bucket(build(puts, calls, ex_div=date(2026, 10, 30)), 0.10)  # 110 > 103
    assert not any("除息" in w for w in far["warnings"])
    after = bucket(build(puts, near, ex_div=date(2026, 11, 9)), 0.10)  # after expiry
    assert not any("除息" in w for w in after["warnings"])
    none = bucket(build(puts, near, ex_div=None), 0.10)
    assert not any("除息" in w for w in none["warnings"])


# --- structure validity ---------------------------------------------------

def test_candidate_rejects_invalid_range_short_put_ge_short_call(monkeypatch):
    puts, calls = hand_chain()
    q = core.quote_map(puts)
    monkeypatch.setattr(rules, "pick_short_call", lambda *a: {"quote": q[90.0], "delta": 0.1, "warnings": []})
    monkeypatch.setattr(rules, "find_call_long",
                        lambda *a: {"quote": core.quote_map(calls)[115.0], "width": 5.0, "warnings": []})
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "unavailable" and "区间无效" in c["reason"]


def test_candidate_rejects_reused_strike(monkeypatch):
    puts, calls = hand_chain()
    pq, cq = core.quote_map(puts), core.quote_map(calls)
    monkeypatch.setattr(rules, "find_call_long",
                        lambda *a: {"quote": pq[85.0], "width": 5.0, "warnings": []})
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "unavailable" and c["reason"] == "两侧复用了同一个行权价"
    monkeypatch.setattr(rules, "find_call_long",
                        lambda *a: {"quote": cq[110.0], "width": 5.0, "warnings": []})
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "unavailable" and c["reason"] == "两侧复用了同一个行权价"


def test_candidate_rejects_credit_ge_width_and_nonpositive_side_credit():
    puts, calls = hand_chain()
    rich = [row(110.0, 5.19, 5.21, 0.10), calls[1]]  # call side 5.20-0.30 = 4.90 < 5 but net 5.5 >= 5
    c = bucket(build(puts, rich), 0.10)
    assert c["status"] == "unavailable" and "净权利金" in c["reason"]
    over = [row(110.0, 5.49, 5.51, 0.10), calls[1]]  # call side 5.2 >= width
    assert bucket(build(puts, over), 0.10)["status"] == "unavailable"
    negative = [row(85.0, 1.99, 2.01, -0.03), puts[1]]  # long put dearer than short: put side < 0
    c = bucket(build(negative, calls), 0.10)
    assert c["status"] == "unavailable" and "权利金" in c["reason"]


def test_unlisted_call_long_rejects_candidate():
    puts, calls = hand_chain()
    c = bucket(build(puts, calls[:1]), 0.10)
    assert c["status"] == "unavailable" and "上方没有等宽的长腿" in c["reason"]


def test_long_leg_without_valid_mid_rejects():
    puts, calls = hand_chain()
    puts[0] = {"strike": 85.0, "bid": 0, "ask": 0, "last": 0, "iv": 0.4, "delta": -0.03}
    c = bucket(build(puts, calls), 0.10)
    assert c["status"] == "unavailable"


def test_three_buckets_independent():
    puts, calls = make_chain(quote_spread=0.01), make_call_chain(quote_spread=0.01)
    base = build(puts, calls)
    assert [c["status"] for c in base] == ["ok", "ok", "ok"]
    assert [c["delta_bucket"] for c in base] == [0.10, 0.15, 0.20]
    # Bucket 0.15 uses call short 106 -> long 111; removing 111 kills only that bucket.
    out = build(puts, [r for r in calls if r["strike"] != 111.0])
    assert [c["status"] for c in out] == ["ok", "unavailable", "ok"]
    assert out[0] == base[0] and out[2] == base[2]


def test_width_defaults_to_recommended_when_none():
    c = build(make_chain(quote_spread=0.01), make_call_chain(quote_spread=0.01), width=None)
    assert c[0]["status"] == "ok"
    assert c[0]["width"] == pytest.approx(1.0)  # recommend_width on this chain


# --- existing put-spread path pinned --------------------------------------

def test_existing_put_spread_candidates_unchanged():
    out = (core.build_candidates(100.0, 7, make_chain(), width=5.0, earnings="yes")
           + core.build_candidates(100.0, 7, make_chain(stale=True)))
    digest = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    assert digest == "5b86f8968f5a7dcd1a333e49822bc5358165dc1f5330778077a170642728bb66"


# --- final review: boundaries and the up-move warning text -----------------

def test_ex_div_short_call_exactly_three_percent_above_spot_warns():
    puts, _ = hand_chain()
    edge = [row(103.0, 1.99, 2.01, 0.10), row(108.0, 0.29, 0.31, 0.03)]  # 103 == 100 x 1.03
    c = bucket(build(puts, edge, ex_div=date(2026, 10, 30)), 0.10)
    assert c["status"] == "ok" and any("除息" in w for w in c["warnings"])
    beyond = [row(103.5, 1.99, 2.01, 0.10), row(108.5, 0.29, 0.31, 0.03)]
    c = bucket(build(puts, beyond, ex_div=date(2026, 10, 30)), 0.10)
    assert c["status"] == "ok" and not any("除息" in w for w in c["warnings"])


def test_short_leg_spread_exactly_at_limit_is_accepted():
    # mid 2.50, spread 0.125 -> exactly 5% (all binary-exact numbers)
    exact = [row(95.0, 2.4375, 2.5625, -0.15)]
    pick = rules.pick_short_put(exact, 100.0, 7, 0.15)
    assert isinstance(pick, dict) and pick["quote"]["strike"] == 95.0
    over = [row(95.0, 2.43, 2.57, -0.15)]  # 5.6%
    assert isinstance(rules.pick_short_put(over, 100.0, 7, 0.15), str)


def test_up_move_warning_describes_loss_capped_at_long_wing():
    w = rules.WARN_UP_MOVE
    assert "大涨" in w and "满额" in w
    assert "涨过短 Call" in w and "长翼处亏满宽度" in w and "再涨亏损不再增加" in w
    assert "长翼之外" not in w
