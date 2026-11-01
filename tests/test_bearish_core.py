"""Money math for Bear Call, Bear Put and Long Put (hand-computed examples from the designs)."""
import math

import pytest

import spread_core as sc


# --- constants ---

def test_bearish_constants():
    assert sc.BEARPUT_DELTA_BUCKETS == (0.60, 0.50, 0.40)
    assert sc.BEARPUT_MIN_DAYS == 30
    assert sc.LONGPUT_DELTA_BUCKETS == (0.50, 0.40, 0.30)
    assert sc.LONGPUT_MIN_DAYS == 30
    assert sc.BEARCALL_ROUNDING_TOL == 1e-4
    assert sc.BEARPUT_ROUNDING_TOL == 1e-4
    assert sc.LONGPUT_ROUNDING_TOL == 1e-4
    assert sc.BEARPUT_TAKE_PROFIT_FRACTION == 0.50
    assert sc.BEARPUT_STOP_FRACTION == 0.50
    assert sc.BEARPUT_TIME_STOP_DAYS == 10
    assert sc.LONGPUT_TAKE_PROFIT_FRACTION == 1.00
    assert sc.LONGPUT_STOP_FRACTION == 0.50
    assert sc.LONGPUT_TIME_STOP_DAYS == 10


# --- Bear Call: short 105 call, width 5, credit 1.20 ---

BC_CLOSES = (100.0, 105.0, 106.2, 108.0, 110.0, 120.0)


def test_bearcall_expiry_cost_table():
    costs = [sc.bearcall_expiry_cost(105.0, 5.0, c) for c in BC_CLOSES]
    assert costs == pytest.approx([0.0, 0.0, 1.2, 3.0, 5.0, 5.0])


def test_bearcall_pnl_table():
    rows = [sc.bearcall_pnl(1.20, sc.bearcall_expiry_cost(105.0, 5.0, c)) for c in BC_CLOSES]
    assert [p for p, _ in rows] == [120.0, 120.0, 0.0, -180.0, -380.0, -380.0]
    assert [r for _, r in rows] == ["win", "win", "loss", "loss", "loss", "loss"]
    breakeven_pnl = rows[2][0]
    assert math.copysign(1, breakeven_pnl) == 1


def test_bearcall_pnl_scales_with_contracts():
    assert sc.bearcall_pnl(1.20, 3.0, contracts=2) == (-360.0, "loss")


def test_bearcall_breakeven_and_required_win_rate():
    assert sc.bearcall_breakeven(105.0, 1.20) == pytest.approx(106.20)
    assert sc.breakeven_win_rate(1.20, 5.0) == pytest.approx(0.76)


def test_bearcall_close_cost_clamps():
    assert sc.bearcall_close_cost(1.40, 0.50, 5.0) == pytest.approx(0.90)
    assert sc.bearcall_close_cost(0.10, 0.05, 5.0) == pytest.approx(0.05)
    assert sc.bearcall_close_cost(0.05, 0.06, 5.0) == 0.0
    assert sc.bearcall_close_cost(6.00, 0.50, 5.0) == 5.0


def test_bearcall_in_the_money_is_strict():
    assert sc.bearcall_in_the_money(105.01, 105.0) is True
    assert sc.bearcall_in_the_money(105.0, 105.0) is False
    assert sc.bearcall_in_the_money(104.0, 105.0) is False


def test_bearcall_flags_via_seller_threshold_flags_unchanged():
    # max profit 120 -> take-profit +60; max loss 380 -> stop -190; inclusive.
    assert sc.threshold_flags(1.20, 5.0, 60.0) == {"stop": False, "take_profit": True}
    assert sc.threshold_flags(1.20, 5.0, 59.99) == {"stop": False, "take_profit": False}
    assert sc.threshold_flags(1.20, 5.0, -190.0) == {"stop": True, "take_profit": False}
    assert sc.threshold_flags(1.20, 5.0, -189.99) == {"stop": False, "take_profit": False}


# --- Bear Put: long 100 put, short 90 put, width 10, debit 3.00 ---

BP_CLOSES = (105.0, 100.0, 97.0, 95.0, 90.0, 80.0)


def test_bearput_breakeven_and_required_win_rate():
    assert sc.bearput_breakeven(100.0, 3.0) == pytest.approx(97.00)
    assert sc.bearput_required_win_rate(3.0, 10.0) == pytest.approx(0.30)


def test_bearput_max_profit_and_loss():
    assert sc.bearput_max_profit(3.0, 10.0) == 700.0
    assert sc.bearput_max_loss(3.0) == 300.0
    assert sc.bearput_max_profit(3.0, 10.0, contracts=2) == 1400.0
    assert sc.bearput_max_loss(3.0, contracts=2) == 600.0


def test_bearput_expiry_value_table():
    values = [sc.bearput_expiry_value(100.0, 10.0, c) for c in BP_CLOSES]
    assert values == pytest.approx([0.0, 0.0, 3.0, 5.0, 10.0, 10.0])


def test_bearput_pnl_table():
    rows = [sc.bearput_pnl(3.0, sc.bearput_expiry_value(100.0, 10.0, c)) for c in BP_CLOSES]
    assert [p for p, _ in rows] == [-300.0, -300.0, 0.0, 200.0, 700.0, 700.0]
    assert [r for _, r in rows] == ["loss", "loss", "loss", "win", "win", "win"]
    assert math.copysign(1, rows[2][0]) == 1


def test_bearput_fraction_captured():
    fracs = [sc.bearput_fraction_captured(p, 3.0, 10.0) for p in (-300.0, 0.0, 200.0, 700.0)]
    assert fracs == pytest.approx([-0.4286, 0.0, 0.2857, 1.0], abs=1e-4)


def test_bearput_close_value_clamps():
    assert sc.bearput_close_value(4.50, 1.20, 10.0) == pytest.approx(3.30)
    assert sc.bearput_close_value(0.05, 0.06, 10.0) == 0.0
    assert sc.bearput_close_value(12.0, 0.50, 10.0) == 10.0


def test_bearput_threshold_flags_inclusive():
    # take-profit at +350 (half of 700), stop at -150 (half of the 300 debit).
    assert sc.bearput_threshold_flags(3.0, 10.0, 350.0) == {"stop": False, "take_profit": True}
    assert sc.bearput_threshold_flags(3.0, 10.0, 349.99) == {"stop": False, "take_profit": False}
    assert sc.bearput_threshold_flags(3.0, 10.0, -150.0) == {"stop": True, "take_profit": False}
    assert sc.bearput_threshold_flags(3.0, 10.0, -149.99) == {"stop": False, "take_profit": False}


# --- Long Put: long 95 put, debit 2.40 ---

LP_CLOSES = (100.0, 95.0, 92.6, 90.0, 80.0)


def test_longput_breakeven_and_max_loss():
    assert sc.longput_breakeven(95.0, 2.40) == pytest.approx(92.60)
    assert sc.longput_max_loss(2.40) == 240.0
    assert sc.longput_max_loss(2.40, contracts=3) == 720.0


def test_longput_max_profit_display():
    assert sc.longput_max_profit_display(95.0, 2.40) == 9260.0


def test_longput_expiry_value_table():
    values = [sc.longput_expiry_value(95.0, c) for c in LP_CLOSES]
    assert values == pytest.approx([0.0, 0.0, 2.4, 5.0, 15.0])
    assert sc.longput_expiry_value(95.0, 0.0) == pytest.approx(95.0)  # no upper clamp


def test_longput_pnl_table():
    rows = [sc.longput_pnl(2.40, sc.longput_expiry_value(95.0, c)) for c in LP_CLOSES]
    assert [p for p, _ in rows] == [-240.0, -240.0, 0.0, 260.0, 1260.0]
    assert [r for _, r in rows] == ["loss", "loss", "loss", "win", "win"]
    assert math.copysign(1, rows[2][0]) == 1


def test_longput_fraction_of_debit():
    fracs = [sc.longput_fraction_of_debit(p, 2.40) for p in (-240.0, 0.0, 260.0, 1260.0)]
    assert fracs == [-1.0, 0.0, 1.0833, 5.25]


def test_longput_threshold_flags_inclusive():
    # take-profit at +240 (1x debit), stop at -120 (half the debit).
    assert sc.longput_threshold_flags(2.40, 240.0) == {"stop": False, "take_profit": True}
    assert sc.longput_threshold_flags(2.40, 239.99) == {"stop": False, "take_profit": False}
    assert sc.longput_threshold_flags(2.40, -120.0) == {"stop": True, "take_profit": False}
    assert sc.longput_threshold_flags(2.40, -119.99) == {"stop": False, "take_profit": False}


# --- existing seller helpers stay as they were ---

def test_existing_seller_functions_unchanged():
    assert sc.threshold_flags(1.0, 5.0, 50.0) == {"stop": False, "take_profit": True}
    assert sc.threshold_flags(1.0, 5.0, -200.0) == {"stop": True, "take_profit": False}
    assert sc.threshold_flags(1.0, 5.0, 0.0) == {"stop": False, "take_profit": False}
    assert sc.breakeven_win_rate(1.0, 5.0) == pytest.approx(0.8)
    assert sc.expiry_pnl(100.0, 95.0, 1.0, 101.0) == (100.0, "win")
    assert sc.expiry_pnl(100.0, 95.0, 1.0, 98.0) == (-100.0, "partial")
    assert sc.expiry_pnl(100.0, 95.0, 1.0, 90.0) == (-400.0, "loss")
