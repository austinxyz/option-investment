"""Tests for scripts/spread_core.py — pure math, no network.

Paper-trading data only; invented tickers, no real account information.
"""
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_core as core
from spread_fixtures import make_chain


# --- primitives -----------------------------------------------------------

def test_put_delta_atm_is_near_half():
    assert core.put_delta(spot=100, strike=100, iv=0.40, days=7) == pytest.approx(0.4834, abs=0.001)


def test_put_delta_deep_otm_is_tiny():
    assert core.put_delta(100, 80, 0.40, 7) < 0.001


def test_put_delta_deep_itm_near_one():
    assert core.put_delta(100, 130, 0.40, 7) > 0.99


def test_put_delta_expired_or_zero_iv_is_intrinsic():
    assert core.put_delta(100, 110, 0.4, 0) == 1.0
    assert core.put_delta(100, 90, 0.4, 0) == 0.0
    assert core.put_delta(100, 90, 0.0, 7) == 0.0


def test_mid_price_uses_bid_ask():
    mid, stale = core.mid_price(1.0, 1.2, 0.5)
    assert mid == pytest.approx(1.1)
    assert stale is False


@pytest.mark.parametrize("bid,ask", [(0, 0), (None, None), (float("nan"), 1.0), (1.2, 1.0)])
def test_mid_price_falls_back_to_last_when_quote_unusable(bid, ask):
    assert core.mid_price(bid, ask, 0.8) == (0.8, True)


def test_mid_price_none_when_nothing_usable():
    assert core.mid_price(0, 0, 0) == (None, True)
    assert core.mid_price(None, None, None) == (None, True)


def test_breakeven_win_rate_matches_share_page_example():
    # TSLA 225/210, credit 2.10, width 15 -> 86%
    assert core.breakeven_win_rate(2.10, 15) == pytest.approx(0.86)


def test_expected_move_one_week():
    assert core.expected_move(100, 0.40, 7) == pytest.approx(5.54, abs=0.01)


# --- candidates -----------------------------------------------------------

def test_candidates_cover_three_buckets_with_increasing_short_strikes():
    cands = core.build_candidates(100.0, 7, make_chain())
    assert [c["bucket"] for c in cands] == [0.10, 0.15, 0.20]
    assert all(c["available"] for c in cands)
    shorts = [c["short_strike"] for c in cands]
    assert shorts == sorted(shorts)
    assert len(set(shorts)) == 3
    for c in cands:
        assert abs(c["short_delta"] - c["bucket"]) < 0.05


def test_candidate_numbers_are_consistent():
    c = core.build_candidates(100.0, 7, make_chain(), width=5.0)[1]
    assert c["short_strike"] - c["long_strike"] == pytest.approx(5.0)
    assert c["credit"] == pytest.approx(c["short_mid"] - c["long_mid"], abs=1e-4)
    assert c["max_profit"] == pytest.approx(c["credit"] * 100, abs=0.01)
    assert c["max_loss"] == pytest.approx((5.0 - c["credit"]) * 100, abs=0.01)
    assert c["breakeven"] == pytest.approx(c["short_strike"] - c["credit"], abs=1e-4)
    assert c["credit_ratio"] == pytest.approx(c["credit"] / 5.0, abs=1e-4)
    assert c["breakeven_win_rate"] == pytest.approx((5.0 - c["credit"]) / 5.0, abs=1e-4)
    em = core.expected_move(100.0, 0.40, 7)
    assert c["em_multiple"] == pytest.approx((100.0 - c["short_strike"]) / em, abs=1e-3)


def test_bucket_unavailable_when_no_long_leg_near_target_width():
    puts = [r for r in make_chain() if r["strike"] in (93.0, 95.0, 96.0)]
    cands = core.build_candidates(100.0, 7, puts, width=5.0)
    assert not cands[0]["available"]
    assert "长腿" in cands[0]["reason"]


def test_bucket_unavailable_when_chain_empty():
    cands = core.build_candidates(100.0, 7, [])
    assert all(not c["available"] for c in cands)


def test_strikes_without_usable_iv_are_skipped_as_short_legs():
    puts = make_chain()
    for r in puts:
        r["iv"] = None
    assert all(not c["available"] for c in core.build_candidates(100.0, 7, puts))


def test_weekend_chain_uses_last_price_and_flags_stale():
    cands = core.build_candidates(100.0, 7, make_chain(stale=True))
    c = cands[1]
    assert c["available"] and c["stale"] is True
    assert c["short_spread_pct"] is None
    assert any("陈旧" in w for w in c["warnings"])


def test_earnings_warnings():
    yes = core.build_candidates(100.0, 7, make_chain(), earnings="yes")[1]
    unknown = core.build_candidates(100.0, 7, make_chain(), earnings="unknown")[1]
    no = core.build_candidates(100.0, 7, make_chain(), earnings="no")[1]
    assert any("财报" in w and "跳过" in w for w in yes["warnings"])
    assert any("没取到" in w for w in unknown["warnings"])
    assert not any("财报" in w for w in no["warnings"])


def test_wide_bid_ask_warning():
    c = core.build_candidates(100.0, 7, make_chain(quote_spread=0.5))[1]
    assert any("买卖价差" in w for w in c["warnings"])


def test_thin_credit_warning():
    puts = make_chain(iv=0.10)  # very low IV -> tiny credit for a 5-wide spread
    c = core.build_candidates(100.0, 7, puts)[2]
    assert c["available"]
    assert c["credit_ratio"] < core.THIN_CREDIT_RATIO
    assert any("赔率" in w for w in c["warnings"])


# --- P&L ------------------------------------------------------------------

@pytest.mark.parametrize("underlying,pnl,result", [
    (96.0, 100.0, "win"),       # above short strike: keep full credit
    (95.0, 100.0, "win"),       # exactly at short strike
    (94.0, 0.0, "partial"),     # exactly at breakeven
    (92.0, -200.0, "partial"),  # between the legs
    (90.0, -400.0, "loss"),     # exactly at long strike: max loss
    (85.0, -400.0, "loss"),     # below long strike: loss capped
])
def test_expiry_pnl_cases(underlying, pnl, result):
    assert core.expiry_pnl(95.0, 90.0, 1.00, underlying) == (pnl, result)


def test_expiry_pnl_scales_with_contracts():
    assert core.expiry_pnl(95.0, 90.0, 1.00, 85.0, contracts=2) == (-800.0, "loss")


def test_expiry_cost_is_intrinsic_value():
    assert core.expiry_cost(95.0, 90.0, 92.0) == pytest.approx(3.0)
    assert core.expiry_cost(95.0, 90.0, 100.0) == 0.0
    assert core.expiry_cost(95.0, 90.0, 80.0) == pytest.approx(5.0)


def test_close_pnl_win_and_loss():
    assert core.close_pnl(1.00, 0.40) == (60.0, "win")
    assert core.close_pnl(1.00, 2.50) == (-150.0, "loss")
    assert core.close_pnl(1.00, 1.00) == (0.0, "loss")  # flat is not a win
    assert core.close_pnl(1.00, 0.40, contracts=3) == (180.0, "win")


def test_cost_to_close_never_negative():
    assert core.cost_to_close(1.5, 0.5) == pytest.approx(1.0)
    assert core.cost_to_close(0.4, 0.5) == 0.0


def test_unrealized_pnl():
    assert core.unrealized_pnl(1.00, 0.60, contracts=2) == 80.0


def test_threshold_flags():
    # width 5, credit 1.00: max loss 400, max profit 100
    assert core.threshold_flags(1.00, 5.0, -200.0) == {"stop": True, "take_profit": False}
    assert core.threshold_flags(1.00, 5.0, -199.0) == {"stop": False, "take_profit": False}
    assert core.threshold_flags(1.00, 5.0, 50.0) == {"stop": False, "take_profit": True}
    assert core.threshold_flags(1.00, 5.0, 49.0) == {"stop": False, "take_profit": False}
    assert core.threshold_flags(1.00, 5.0, -400.0, contracts=2) == {"stop": True, "take_profit": False}
    assert core.threshold_flags(1.00, 5.0, -399.0, contracts=2) == {"stop": False, "take_profit": False}


# --- delta-gap / duplicate / placeholder-IV guards -------------------------

def test_delta_gap_warning_when_short_leg_far_from_bucket():
    puts = [r for r in make_chain() if r["strike"] in (85.0, 90.0, 92.0, 97.0)]
    cands = core.build_candidates(100.0, 7, puts)
    far = [c for c in cands if c["available"]
           and abs(c["short_delta"] - c["bucket"]) > core.MAX_DELTA_GAP]
    assert far
    for c in far:
        assert any("离目标" in w and "档位标签不准" in w for w in c["warnings"])


def test_no_delta_gap_warning_on_dense_chain():
    for c in core.build_candidates(100.0, 7, make_chain()):
        assert not any("离目标" in w for w in c["warnings"])


def test_duplicate_short_strike_warns_on_later_bucket():
    puts = [r for r in make_chain() if r["strike"] in (88.0, 90.0, 93.0, 95.0)]
    cands = core.build_candidates(100.0, 7, puts)
    avail = [c for c in cands if c["available"]]
    seen: dict[float, float] = {}
    dups = 0
    for c in avail:
        if c["short_strike"] in seen:
            dups += 1
            assert any(f"与档位 {seen[c['short_strike']]:.2f} 选了同一个短腿" in w
                       and "没有独立信息" in w for w in c["warnings"])
        else:
            seen[c["short_strike"]] = c["bucket"]
            assert not any("同一个短腿" in w for w in c["warnings"])
    assert dups >= 1


def test_placeholder_iv_strike_is_not_chosen_as_short_leg():
    puts = [r for r in make_chain() if r["strike"] in (85.0, 90.0, 97.0)]
    for r in puts:
        if r["strike"] == 90.0:
            r["iv"] = 1e-5
    cands = core.build_candidates(100.0, 7, puts)
    chosen = [c["short_strike"] for c in cands if c["available"]]
    assert chosen and 90.0 not in chosen
    assert 97.0 in chosen


def test_huge_iv_strike_is_not_chosen_as_short_leg():
    puts = [r for r in make_chain() if r["strike"] in (85.0, 90.0, 97.0)]
    for r in puts:
        if r["strike"] == 90.0:
            r["iv"] = 9.0
    cands = core.build_candidates(100.0, 7, puts)
    assert all(not c["available"] or c["short_strike"] != 90.0 for c in cands)


# --- recommended width ----------------------------------------------------------------------

def _strikes(lo, hi, step):
    out, k = [], lo
    while k <= hi + 1e-9:
        out.append(round(k, 4))
        k += step
    return out


def _chain_for(spot, iv, step, days=7):
    return make_chain(spot=spot, iv=iv, days=days, strikes=_strikes(spot * 0.8, spot, step))


def test_strike_step_is_the_most_common_gap_below_spot():
    puts = _chain_for(361.0, 0.65, 2.5)
    assert core.strike_step(puts, 361.0) == 2.5
    mixed = [{"strike": k} for k in (300, 305, 310, 315, 320, 322, 324, 326, 330, 335, 340, 345, 350, 355, 360)]
    assert core.strike_step(mixed, 361.0) == 5.0   # the odd $2 gap does not win


def test_strike_step_none_without_enough_strikes():
    assert core.strike_step([], 100.0) is None
    assert core.strike_step([{"strike": 95.0}], 100.0) is None


def test_atm_iv_uses_the_strike_nearest_the_spot_and_skips_unusable_iv():
    puts = [{"strike": 98.0, "iv": 0.30}, {"strike": 99.0, "iv": None}, {"strike": 90.0, "iv": 0.50}]
    assert core.atm_iv(puts, 100.0) == pytest.approx(0.30)
    assert core.atm_iv([{"strike": 99.0, "iv": 1e-5}], 100.0) is None
    assert core.atm_iv([], 100.0) is None


@pytest.mark.parametrize("spot,iv,step,expected", [
    (361.0, 0.65, 2.5, 5.0),     # TSLA-like: em ~32 -> 4.9 -> 2 steps
    (542.0, 0.60, 2.5, 7.5),     # AMD-like: em ~45 -> 6.8 -> 3 steps
    (895.0, 0.59, 5.0, 10.0),    # MU-like: em ~73 -> 11 -> 2 steps
])
def test_recommended_width_is_fifteen_percent_of_the_expected_move_rounded_to_the_strike_step(
        spot, iv, step, expected):
    rec = core.recommend_width(_chain_for(spot, iv, step), spot, 7)
    assert rec["width"] == expected
    em = core.expected_move(spot, rec["atm_iv"], 7)
    assert rec["expected_move"] == pytest.approx(em)
    assert rec["raw"] == pytest.approx(em * core.WIDTH_EM_FRACTION)
    assert rec["step"] == step and rec["capped"] is False


def test_recommended_width_is_never_below_one_strike_step():
    rec = core.recommend_width(_chain_for(100.0, 0.05, 1.0), 100.0, 7)
    assert rec["width"] == 1.0


def test_recommended_width_is_capped_at_five_percent_of_spot_but_never_below_one_step():
    rec = core.recommend_width(_chain_for(100.0, 4.0, 1.0), 100.0, 7)   # absurd IV -> huge raw width
    assert rec["width"] == 5.0 and rec["capped"] is True
    coarse = make_chain(spot=20.0, iv=0.60, days=7, strikes=[5.0, 10.0, 15.0, 20.0])
    tiny = core.recommend_width(coarse, 20.0, 7)   # 5% of 20 is 1 < one step of 5; window falls back
    assert tiny["width"] == 5.0


def test_recommended_width_none_when_it_cannot_be_estimated():
    assert core.recommend_width([], 100.0, 7) is None
    no_iv = [dict(r, iv=None) for r in _chain_for(100.0, 0.4, 1.0)]
    assert core.recommend_width(no_iv, 100.0, 7) is None


def test_longer_horizon_recommends_a_wider_spread():
    short = core.recommend_width(_chain_for(895.0, 0.59, 5.0, 7), 895.0, 7)
    long_ = core.recommend_width(_chain_for(895.0, 0.59, 5.0, 28), 895.0, 28)
    assert long_["width"] > short["width"]


def test_strike_step_widens_its_window_for_cheap_stocks_with_coarse_strikes():
    coarse = [{"strike": k} for k in (5.0, 10.0, 15.0, 20.0)]
    assert core.strike_step(coarse, 20.0) == 5.0


def test_candidate_reports_the_margin_the_broker_freezes_and_net_capital_is_the_max_loss():
    c = core.build_candidates(100.0, 7, make_chain(), width=5.0)[1]
    assert c["margin"] == pytest.approx(500.0)                    # width x 100
    assert c["margin"] - c["credit"] * 100 == pytest.approx(c["max_loss"], abs=0.01)


def test_atm_iv_refuses_a_strike_far_from_the_spot():
    far_only = [{"strike": 80.0, "iv": 0.50}, {"strike": 99.0, "iv": 1e-5}]   # 99 is a placeholder IV
    assert core.atm_iv(far_only, 100.0) is None
    assert core.atm_iv([{"strike": 96.0, "iv": 0.50}], 100.0) == pytest.approx(0.50)   # 4% away is fine


def test_recommend_width_falls_back_when_only_far_strikes_have_a_usable_iv():
    chain = [dict(r, iv=(r["iv"] if r["strike"] <= 85.0 else None)) for r in _chain_for(100.0, 0.4, 1.0)]
    assert core.recommend_width(chain, 100.0, 7) is None


# --- cash-secured put -----------------------------------------------------

def test_csp_expiry_otm_keeps_full_premium():
    assert core.csp_expiry_pnl(100.0, 1.50, 105.0) == (150.0, "win")


def test_csp_expiry_exactly_at_strike_is_win():
    assert core.csp_expiry_pnl(100.0, 1.50, 100.0) == (150.0, "win")


def test_csp_expiry_assigned_loss():
    assert core.csp_expiry_pnl(100.0, 1.50, 90.0) == (-850.0, "assigned")


def test_csp_expiry_assigned_but_still_profitable():
    pnl, outcome = core.csp_expiry_pnl(100.0, 3.00, 98.0)
    assert (pnl, outcome) == (100.0, "assigned")
    assert pnl > 0


def test_csp_expiry_scales_with_contracts():
    assert core.csp_expiry_pnl(100.0, 1.50, 105.0, contracts=2) == (300.0, "win")
    assert core.csp_expiry_pnl(100.0, 1.50, 90.0, contracts=2) == (-1700.0, "assigned")


def test_csp_flags_take_profit_at_half_premium():
    assert core.csp_threshold_flags(2.00, 100.0)["take_profit"] is True
    assert core.csp_threshold_flags(2.00, 99.0)["take_profit"] is False


def test_csp_flags_stop_at_two_premiums():
    assert core.csp_threshold_flags(2.00, -400.0)["stop"] is True
    assert core.csp_threshold_flags(2.00, -399.0)["stop"] is False


# --- covered call primitives ------------------------------------------------

def test_call_delta_matches_put_parity():
    for strike in (90.0, 100.0, 110.0):
        total = core.call_delta(100.0, strike, 0.35, 9) + core.put_delta(100.0, strike, 0.35, 9)
        assert total == pytest.approx(1.0, abs=1e-9)


def test_call_delta_intrinsic_when_expired():
    assert core.call_delta(105.0, 100.0, 0.4, 0) == 1.0
    assert core.call_delta(95.0, 100.0, 0.4, 0) == 0.0
    assert core.call_delta(105.0, 100.0, 0.0, 5) == 1.0


def test_cc_expiry_pnl_worthless_vs_called_away():
    assert core.cc_expiry_pnl(110.0, 1.00, 105.0) == (100.0, "win")
    assert core.cc_expiry_pnl(110.0, 1.00, 110.0) == (100.0, "called_away")
    assert core.cc_expiry_pnl(110.0, 1.00, 120.0) == (100.0, "called_away")
    assert core.cc_expiry_pnl(110.0, 1.00, 105.0, contracts=2)[0] == 200.0


def test_cc_stock_leg_pnl():
    assert core.cc_stock_leg_pnl(110.0, 100.0) == 1000.0
    assert core.cc_stock_leg_pnl(110.0, 100.0, contracts=2) == 2000.0
    assert core.cc_stock_leg_pnl(95.0, 100.0) == -500.0


def test_quote_carries_broker_delta_and_from_close():
    row = {"strike": 100.0, "bid": None, "ask": None, "last": 1.5, "iv": 0.4,
           "delta": 0.27, "from_close": True}
    q = core.quote_map([row])[100.0]
    assert q["delta"] == 0.27
    assert q["from_close"] is True
    assert q["stale"] is True
    assert q["spread_pct"] is None


def test_quote_without_delta_unchanged():
    q = core.quote_map([{"strike": 100.0, "bid": 1.0, "ask": 1.2, "last": 1.1, "iv": 0.4}])[100.0]
    assert q["delta"] is None
    assert q["from_close"] is False
    assert q["mid"] == pytest.approx(1.1)
    assert q["stale"] is False


def test_delta_for_returns_local_when_no_broker():
    q = {"strike": 95.0, "iv": 0.40, "delta": None}
    delta, warn = core.delta_for("P", q, 100.0, 30)
    assert delta == pytest.approx(core.put_delta(100.0, 95.0, 0.40, 30))
    assert warn is None
    cdelta, cwarn = core.delta_for("C", {"strike": 105.0, "iv": 0.40}, 100.0, 30)
    assert cdelta == pytest.approx(core.call_delta(100.0, 105.0, 0.40, 30))
    assert cwarn is None


def test_delta_for_broker_within_tolerance_no_warning():
    local = core.put_delta(100.0, 95.0, 0.40, 30)
    delta, warn = core.delta_for("P", {"strike": 95.0, "iv": 0.40, "delta": local + 0.02}, 100.0, 30)
    assert delta == pytest.approx(local + 0.02) and warn is None


def test_delta_crosscheck_tolerance_is_calibrated_value():
    # 1.5x the measured p95 (0.015) is below the 0.03 floor, so the floor applies
    assert core.DELTA_CROSSCHECK_TOL == 0.03


def test_delta_for_warns_just_above_calibrated_tolerance():
    local = core.put_delta(100.0, 95.0, 0.40, 30)
    broker = local + 0.04
    _, warn = core.delta_for("P", {"strike": 95.0, "iv": 0.40, "delta": broker}, 100.0, 30)
    assert warn is not None and "相差 0.04" in warn


def test_delta_for_warns_when_crosscheck_exceeds_tolerance():
    local = core.put_delta(100.0, 95.0, 0.40, 30)
    broker = local + 0.12
    delta, warn = core.delta_for("P", {"strike": 95.0, "iv": 0.40, "delta": broker}, 100.0, 30)
    assert delta == broker
    assert warn == f"IBKR Delta {broker:.2f} 与本地 Black-Scholes {local:.2f} 相差 {0.12:.2f}"


def test_delta_for_unusable_iv_with_broker_delta_no_crosscheck():
    delta, warn = core.delta_for("P", {"strike": 95.0, "iv": None, "delta": 0.21}, 100.0, 30)
    assert delta == 0.21 and warn is None


def test_delta_for_negative_broker_delta_is_absolute():
    delta, _ = core.delta_for("P", {"strike": 95.0, "iv": None, "delta": -0.21}, 100.0, 30)
    assert delta == pytest.approx(0.21)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 1.5, -2, "x", None])
def test_has_delta_rejects_inf_and_out_of_range(bad):
    assert core.has_delta({"delta": bad}) is False
    assert core.has_delta({"delta": 0.0}) and core.has_delta({"delta": -0.3})


def test_delta_for_raises_without_delta_or_iv():
    with pytest.raises(ValueError, match="no usable delta or iv"):
        core.delta_for("P", {"strike": 95.0, "iv": None, "delta": None}, 100.0, 30)
    with pytest.raises(ValueError):
        core.delta_for("C", {"strike": 105.0, "iv": -1, "delta": float("inf")}, 100.0, 30)


# --- collar / protective put (signed, per-share premiums, dollar results) ---

def test_collar_unrealized_pnl_net_debit_and_credit():
    # net debit: sold call 1.00, paid put 2.50; call now 0.40, put now 3.10
    # (1.00-0.40) + (3.10-2.50) = 1.20/share -> 120 dollars
    assert core.collar_unrealized_pnl(1.00, 2.50, 0.40, 3.10, 1) == pytest.approx(120.0)
    # net credit: sold call 3.00, paid put 1.00; call now 4.00, put now 0.50
    # (3.00-4.00) + (0.50-1.00) = -1.50/share -> -150 dollars (not clamped)
    assert core.collar_unrealized_pnl(3.00, 1.00, 4.00, 0.50, 1) == pytest.approx(-150.0)


def test_collar_unrealized_pnl_protective_put_has_no_call_leg():
    assert core.collar_unrealized_pnl(0.0, 2.00, 0.0, 2.60, 1) == pytest.approx(60.0)


def test_collar_close_pnl_matches_signed_formula():
    assert core.collar_close_pnl(1.00, 2.50, 0.40, 3.10, 2) == pytest.approx(240.0)


def test_collar_expiry_result_boundaries():
    assert core.collar_expiry_result(90.0, 110.0, 110.0) == "called_away"
    assert core.collar_expiry_result(90.0, 110.0, 120.0) == "called_away"
    assert core.collar_expiry_result(90.0, 110.0, 90.0) == "expired_worthless"
    assert core.collar_expiry_result(90.0, 110.0, 100.0) == "expired_worthless"
    assert core.collar_expiry_result(90.0, 110.0, 89.99) == "protected"
    # protective put has no call leg: never called away
    assert core.collar_expiry_result(90.0, None, 500.0) == "expired_worthless"
    assert core.collar_expiry_result(90.0, None, 80.0) == "protected"


def test_collar_expiry_option_pnl_adds_put_intrinsic_only_below_put_strike():
    # net_credit -0.50/share
    assert core.collar_expiry_option_pnl(-0.50, 90.0, 95.0, 1) == pytest.approx(-50.0)
    assert core.collar_expiry_option_pnl(-0.50, 90.0, 90.0, 1) == pytest.approx(-50.0)
    # close 80: put intrinsic 10/share -> (-0.50 + 10) * 100
    assert core.collar_expiry_option_pnl(-0.50, 90.0, 80.0, 1) == pytest.approx(950.0)


def test_collar_stock_leg_pnl_capped_at_call_strike():
    assert core.collar_stock_leg_pnl(100.0, 130.0, 110.0, 1) == pytest.approx(1000.0)
    assert core.collar_stock_leg_pnl(100.0, 105.0, 110.0, 1) == pytest.approx(500.0)
    assert core.collar_stock_leg_pnl(100.0, 80.0, 110.0, 1) == pytest.approx(-2000.0)


def test_collar_stock_leg_pnl_protective_put_uses_close():
    assert core.collar_stock_leg_pnl(100.0, 130.0, None, 1) == pytest.approx(3000.0)


def test_collar_pnl_scales_with_contracts():
    assert core.collar_unrealized_pnl(1.00, 2.50, 0.40, 3.10, 3) == pytest.approx(360.0)
    assert core.collar_expiry_option_pnl(-0.50, 90.0, 80.0, 3) == pytest.approx(2850.0)
    assert core.collar_stock_leg_pnl(100.0, 130.0, 110.0, 3) == pytest.approx(3000.0)


# --- iron condor money math. Per-share inputs; pnl outputs in dollars.
# Fixture: short put 90, short call 110, width 5, net credit 1.50.

def test_condor_breakevens():
    assert core.condor_breakevens(90, 110, 1.50) == (88.5, 111.5)


def test_condor_cost_to_close_clamps_each_side():
    assert core.condor_cost_to_close(1.0, 0.4, 0.8, 0.3, 5) == pytest.approx(1.1)
    # put-side noise above width is capped at width; call side stays as is
    assert core.condor_cost_to_close(7.0, 0.5, 0.8, 0.3, 5) == pytest.approx(5.5)
    # negative side (long mid above short mid) clamps to 0
    assert core.condor_cost_to_close(0.2, 0.5, 0.8, 0.3, 5) == pytest.approx(0.5)
    assert core.condor_cost_to_close(0.2, 0.5, 0.1, 0.3, 5) == 0.0
    # call-side noise above width is capped at width; put side stays as is
    assert core.condor_cost_to_close(1.0, 0.4, 7.0, 0.5, 5) == pytest.approx(5.6)


def test_condor_unrealized_pnl_signed_and_scaled():
    assert core.condor_unrealized_pnl(1.50, 1.00, 1) == 50.0
    assert core.condor_unrealized_pnl(1.50, 2.00, 2) == -100.0
    assert core.condor_unrealized_pnl(1.50, 1.50, 3) == 0.0


def test_condor_close_pnl_zero_is_loss():
    assert core.condor_close_pnl(1.50, 0.50, 1) == (100.0, "win")
    assert core.condor_close_pnl(1.50, 2.50, 2) == (-200.0, "loss")
    assert core.condor_close_pnl(1.50, 1.50, 1) == (0.0, "loss")


def test_condor_expiry_losses_boundaries():
    f = core.condor_expiry_losses
    assert f(90, 110, 5, 90) == (0.0, 0.0)
    assert f(90, 110, 5, 110) == (0.0, 0.0)
    assert f(90, 110, 5, 100) == (0.0, 0.0)
    assert f(90, 110, 5, 88) == (2.0, 0.0)
    assert f(90, 110, 5, 112) == (0.0, 2.0)
    assert f(90, 110, 5, 85) == (5.0, 0.0)
    assert f(90, 110, 5, 60) == (5.0, 0.0)
    assert f(90, 110, 5, 115) == (0.0, 5.0)
    assert f(90, 110, 5, 200) == (0.0, 5.0)


def test_condor_expiry_pnl_between_shorts_keeps_full_credit():
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 100) == (150.0, "win", "none", 0.0)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 90) == (150.0, "win", "none", 0.0)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 110) == (150.0, "win", "none", 0.0)


def test_condor_expiry_pnl_put_breach_partial_and_full():
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 88) == (-50.0, "loss", "put", 2.0)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 85) == (-350.0, "loss", "put", 5.0)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 50) == (-350.0, "loss", "put", 5.0)


def test_condor_expiry_pnl_call_breach_partial_and_full():
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 112) == (-50.0, "loss", "call", 2.0)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 120) == (-350.0, "loss", "call", 5.0)


def test_condor_expiry_pnl_breached_side_labels():
    # breach but still profitable: win with the breached side recorded
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 89) == (50.0, "win", "put", 1.0)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 111) == (50.0, "win", "call", 1.0)
    # exactly breakeven pnl == 0 is a loss, side still labelled
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 88.5) == (0.0, "loss", "put", 1.5)
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 111.5) == (0.0, "loss", "call", 1.5)


def test_condor_expiry_pnl_scales_with_contracts():
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 100, 3)[0] == 450.0
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 88, 2)[0] == -100.0
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 120, 4)[0] == -1400.0


def test_condor_never_both_sides_lose():
    for close in range(40, 161, 1):
        put_loss, call_loss = core.condor_expiry_losses(90, 110, 5, close)
        assert put_loss == 0.0 or call_loss == 0.0
        assert 0.0 <= put_loss <= 5.0 and 0.0 <= call_loss <= 5.0


def test_condor_thin_credit_ratio_constant():
    assert core.CONDOR_THIN_CREDIT_RATIO == 0.15


# --- final review: -0.0 pnl and float slack on the breached side

def test_condor_close_pnl_never_returns_negative_zero():
    import math
    pnl, result = core.condor_close_pnl(0.3, 0.1 + 0.2, 1)  # credit - cost ~ -5.6e-17
    assert pnl == 0.0 and math.copysign(1.0, pnl) == 1.0 and result == "loss"
    assert str(pnl) == "0.0"


def test_condor_expiry_pnl_float_noise_is_not_a_breach():
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 90 - 1e-12)[2] == "none"
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 110 + 1e-12)[2] == "none"
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 89.99)[2] == "put"
    assert core.condor_expiry_pnl(1.50, 90, 110, 5, 110.01)[2] == "call"


# --- bull call spread (debit): money math. Invented numbers: long 100 / short 105,
# width 5, debit 1.90 per share. Max profit (5 - 1.90) * 100 = 310; max loss 190.

def test_bullcall_breakeven_and_required_win_rate():
    assert core.bullcall_breakeven(100, 1.90) == pytest.approx(101.90)  # 100 + 1.90
    assert core.bullcall_required_win_rate(1.90, 5) == pytest.approx(0.38)  # 1.90 / 5


def test_bullcall_max_profit_and_loss_scale():
    assert core.bullcall_max_profit(1.90, 5) == pytest.approx(310.0)  # (5 - 1.90) * 100
    assert core.bullcall_max_loss(1.90) == pytest.approx(190.0)  # 1.90 * 100
    assert core.bullcall_max_profit(1.90, 5, 2) == pytest.approx(620.0)
    assert core.bullcall_max_loss(1.90, 2) == pytest.approx(380.0)


def test_bullcall_expiry_value_boundaries():
    got = [core.bullcall_expiry_value(100, 5, c) for c in (99, 100, 101.90, 103, 105, 120)]
    assert got == pytest.approx([0, 0, 1.90, 3, 5, 5])  # clamp(C - 100, 0, 5)


def test_bullcall_pnl_at_breakeven_is_zero_loss_not_negative_zero():
    import math
    value = core.bullcall_expiry_value(100, 5, 101.90)  # 1.9000000000000057 float noise
    pnl, result = core.bullcall_pnl(1.90, value)
    assert pnl == 0.0 and math.copysign(1, pnl) == 1 and result == "loss"
    pnl, _ = core.bullcall_pnl(0.3, 0.1 + 0.2)  # value - debit ~ 5.6e-17 or -0.0 noise
    assert math.copysign(1, pnl) == 1
    # value - debit = -5.6e-17 -> round(..., 2) is -0.0 without the `+ 0.0` guard
    assert math.copysign(1, round((0.3 - 0.30000000000000004) * 100, 2)) == -1
    pnl, result = core.bullcall_pnl(0.30000000000000004, 0.3)
    assert pnl == 0.0 and math.copysign(1, pnl) == 1 and result == "loss"


def test_bullcall_pnl_table():
    # (value - 1.90) * 100: C = 99, 100, 101.90, 103, 105, 120
    cases = [(99, -190.0, "loss"), (100, -190.0, "loss"), (101.90, 0.0, "loss"),
             (103, 110.0, "win"), (105, 310.0, "win"), (120, 310.0, "win")]
    for close, want_pnl, want_result in cases:
        value = core.bullcall_expiry_value(100, 5, close)
        pnl, result = core.bullcall_pnl(1.90, value)
        assert (pnl, result) == (want_pnl, want_result), close
    value = core.bullcall_expiry_value(100, 5, 103)
    assert core.bullcall_pnl(1.90, value, 2) == (220.0, "win")  # 1.10 * 100 * 2


def test_bullcall_close_value_clamps():
    assert core.bullcall_close_value(4.00, 1.60, 5) == pytest.approx(2.40)  # 4.00 - 1.60
    assert core.bullcall_close_value(6.00, 0.50, 5) == 5  # 5.50 clamped to width
    assert core.bullcall_close_value(0.90, 1.00, 5) == 0  # -0.10 clamped to 0


def test_bullcall_fraction_captured():
    # pnl / max profit (310): -190, 0, 110, 310
    assert core.bullcall_fraction_captured(-190, 1.90, 5) == pytest.approx(-0.6129, abs=1e-4)
    assert core.bullcall_fraction_captured(0, 1.90, 5) == 0.0
    assert core.bullcall_fraction_captured(110, 1.90, 5) == pytest.approx(0.3548, abs=1e-4)
    assert core.bullcall_fraction_captured(310, 1.90, 5) == pytest.approx(1.0)
    assert core.bullcall_fraction_captured(620, 1.90, 5, 2) == pytest.approx(1.0)


def test_bullcall_threshold_flags_inclusive():
    # take profit at 50% of 310 = 155; stop at 50% of 190 = 95
    assert core.bullcall_threshold_flags(1.90, 5, 155.0) == {"stop": False, "take_profit": True}
    assert core.bullcall_threshold_flags(1.90, 5, -95.0) == {"stop": True, "take_profit": False}
    assert core.bullcall_threshold_flags(1.90, 5, 154.99) == {"stop": False, "take_profit": False}
    assert core.bullcall_threshold_flags(1.90, 5, -94.99) == {"stop": False, "take_profit": False}
    assert core.bullcall_threshold_flags(1.90, 5, -190.0, 2)["stop"] is True  # half of 380
    assert core.bullcall_threshold_flags(1.90, 5, -189.99, 2)["stop"] is False


def test_bullcall_constants():
    assert core.BULLCALL_DELTA_BUCKETS == (0.60, 0.50, 0.40)
    assert (core.BULLCALL_MIN_DAYS, core.BULLCALL_POOR_ODDS_RATIO) == (30, 0.60)
    assert (core.BULLCALL_TAKE_PROFIT_FRACTION, core.BULLCALL_STOP_FRACTION) == (0.50, 0.50)
    assert (core.BULLCALL_TIME_STOP_DAYS, core.BULLCALL_ROUNDING_TOL) == (10, 1e-4)


def test_seller_threshold_flags_unchanged():
    # credit 0.8, width 5: max loss 420 -> stop at -210; max profit 80 -> take profit at 40
    assert core.threshold_flags(0.8, 5, -210.0) == {"stop": True, "take_profit": False}
    assert core.threshold_flags(0.8, 5, -209.0) == {"stop": False, "take_profit": False}
    assert core.threshold_flags(0.8, 5, 40.0) == {"stop": False, "take_profit": True}
    assert core.threshold_flags(0.8, 5, 39.0) == {"stop": False, "take_profit": False}
