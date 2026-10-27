from datetime import date

from spread_fixtures import make_call_chain  # also puts scripts/ on sys.path
import collar_screen_rules as rules
from holdings import HeldLot
from put_screen_rules import Reject

SNAP = date(2026, 10, 25)
EXPIRY = "2026-11-27"  # 33 days after SNAP
DAYS = 33
PUT_STRIKES = [float(k) for k in range(70, 100)]
CALL_STRIKES = [float(k) for k in range(101, 131)]


def _row(strike, bid, ask):
    return {"strike": strike, "bid": bid, "ask": ask, "last": (bid + ask) / 2, "iv": 0.35}


def _lot(shares=300.0, cost_basis=90.0, taxable=None, entry=None, source="real"):
    return HeldLot(source=source, lot_id=None, ticker="ZZZQ", shares=shares,
                   cost_basis=cost_basis, original_cost=cost_basis, reference_price=cost_basis,
                   cost_estimated=False, accounts=1, label="ZZZQ",
                   has_taxable_account=taxable, earliest_entry_date=entry)


def _chains():
    # Flat 1% spread so deep OTM puts still pass the 5% leg rule (BS tails are pennies).
    puts = [_row(k, 1.0 + (100 - k) * 0.1, (1.0 + (100 - k) * 0.1) * 1.01) for k in PUT_STRIKES]
    calls = make_call_chain(spot=100.0, iv=0.35, days=DAYS, strikes=CALL_STRIKES, quote_spread=0.02)
    return puts, calls


def _eval(lot=None, *, puts=None, calls=None, earnings="no", ex_div=None,
          avail_put=3, avail_call=3, spot=100.0):
    dp, dc = _chains()
    return rules.evaluate_lot(
        lot or _lot(), snapshot=SNAP, spot=spot, puts=puts if puts is not None else dp,
        calls=calls if calls is not None else dc, expiry=EXPIRY, earnings=earnings,
        ex_div=ex_div, available_for_put=avail_put, available_for_call=avail_call)


def test_pick_put_nearest_strike_not_above_target():
    puts = [_row(k, 1.0, 1.02) for k in (84.0, 86.0, 88.0, 91.0)]
    assert rules.pick_put(puts, 100.0, 0.85)["strike"] == 84.0
    assert rules.pick_put(puts, 100.0, 0.90)["strike"] == 88.0


def test_pick_put_rejects_wide_spread_with_reason():
    out = rules.pick_put([_row(90.0, 1.0, 1.20)], 100.0, 0.90)
    assert isinstance(out, Reject) and "价差" in out.reason


def test_pick_put_rejects_missing_quote():
    out = rules.pick_put([{"strike": 90.0, "bid": 0, "ask": 0, "last": 0}], 100.0, 0.90)
    assert isinstance(out, Reject) and "报价" in out.reason


def test_pick_zero_cost_call_closest_premium():
    calls = [_row(105.0, 3.00, 3.02), _row(110.0, 1.50, 1.52), _row(120.0, 0.40, 0.41)]
    assert rules.pick_zero_cost_call(calls, 100.0, 1.6)["strike"] == 110.0


def test_pick_zero_cost_call_skips_wide_spread():
    calls = [_row(110.0, 1.0, 1.60), _row(120.0, 0.40, 0.41)]
    assert rules.pick_zero_cost_call(calls, 100.0, 1.3)["strike"] == 120.0


def test_pick_zero_cost_call_returns_none_when_no_otm_calls():
    assert rules.pick_zero_cost_call([_row(95.0, 6.0, 6.02), _row(100.0, 3.0, 3.02)],
                                     100.0, 2.0) is None


def test_evaluate_lot_reports_net_cost_floor_cap_and_cap_cost():
    puts = [_row(90.0, 1.99, 2.01)]
    calls = [_row(110.0, 1.99, 2.01), _row(120.0, 0.5, 0.51)]
    out = _eval(puts=puts, calls=calls)
    t = out["tiers"][0]
    assert t["tier"] == 0.90 and t["floor"] == 90.0 and t["cap"] == 110.0
    assert t["net_credit"] == 0.0
    assert t["cap_cost"] == 1000.0  # (110 - 100 spot) * 100 shares, per contract
    assert t["put"]["strike"] == 90.0 and t["call"]["strike"] == 110.0


def test_evaluate_lot_net_debit_when_no_exact_zero_cost():
    puts = [_row(90.0, 2.99, 3.01)]
    calls = [_row(110.0, 1.99, 2.01)]
    t = _eval(puts=puts, calls=calls)["tiers"][0]
    assert abs(t["net_credit"] - (-1.0)) < 1e-9


def test_evaluate_lot_put_only_candidate_per_tier():
    out = _eval()
    assert [p["tier"] for p in out["put_only"]] == [0.90, 0.85, 0.80]
    first = out["put_only"][0]
    assert first["call"] is None and first["cap"] is None and first["cap_cost"] is None
    assert first["floor"] == first["put"]["strike"] == 90.0
    assert abs(first["net_credit"] - (-2.01)) < 1e-9


def test_evaluate_lot_rejects_short_shares():
    short = _eval(_lot(shares=50.0), avail_put=0, avail_call=0)
    assert isinstance(short, Reject) and short.reason == "股数不足 100 股"


def test_evaluate_lot_fully_covered_returns_put_only():
    out = _eval(avail_put=3, avail_call=0)
    assert not isinstance(out, Reject)
    assert out["tiers"] == [] and "只出保护性 Put" in out["note"]
    assert [p["tier"] for p in out["put_only"]] == [0.90, 0.85, 0.80]


def test_evaluate_lot_earnings_is_info_not_reject():
    out = _eval(earnings="yes")
    assert not isinstance(out, Reject)
    assert any("财报" in w for w in out["tiers"][0]["warnings"])


def test_evaluate_lot_ex_div_with_itm_call_warns():
    puts = [_row(90.0, 1.99, 2.01)]
    near = [_row(102.0, 1.99, 2.01)]
    far = [_row(115.0, 1.99, 2.01)]
    ex = date(2026, 11, 10)
    assert any("除息" in w for w in _eval(puts=puts, calls=near, ex_div=ex)["tiers"][0]["warnings"])
    assert not any("除息" in w for w in _eval(puts=puts, calls=far, ex_div=ex)["tiers"][0]["warnings"])


def test_evaluate_lot_call_strike_below_cost_warns():
    puts = [_row(90.0, 1.99, 2.01)]
    calls = [_row(110.0, 1.99, 2.01)]
    low = _eval(_lot(cost_basis=120.0), puts=puts, calls=calls)["tiers"][0]["warnings"]
    unknown = _eval(_lot(cost_basis=0.0), puts=puts, calls=calls)
    assert any("低于成本" in w for w in low)
    assert not isinstance(unknown, Reject)
    assert any("成本未知" in w for w in unknown["tiers"][0]["warnings"])


def test_tax_warning_taxable_under_one_year():
    msg = rules.tax_warning(_lot(taxable=True, entry="2026-03-01"), SNAP)
    assert msg and "应税账户" in msg and "策略页" in msg


def test_tax_warning_taxable_unknown_entry_date():
    assert rules.tax_warning(_lot(taxable=True, entry=None), SNAP)
    assert rules.tax_warning(_lot(taxable=True, entry="not-a-date"), SNAP)


def test_tax_warning_roth_none():
    assert rules.tax_warning(_lot(taxable=False, entry="2026-03-01"), SNAP) is None
    assert rules.tax_warning(_lot(taxable=None, entry="2026-03-01"), SNAP) is None


def test_tax_warning_over_one_year_none():
    assert rules.tax_warning(_lot(taxable=True, entry="2025-01-01"), SNAP) is None


def test_tax_warning_text_has_no_account_names():
    msg = rules.tax_warning(_lot(taxable=True, entry=None), SNAP)
    for name in ("Roth-A", "Taxable-A", "Roth", "IRA", "401"):
        assert name not in msg


def test_tax_warning_not_applied_to_paper_lots():
    assert rules.tax_warning(_lot(source="manual", taxable=True, entry=None), SNAP) is None


def test_pick_put_tier_boundary_exact_and_float_noise():
    assert rules.pick_put([_row(90.0, 1.0, 1.02)], 100.0, 0.90)["strike"] == 90.0
    # 111 * 0.9 = 99.9 (float noise): strike 99.9 must still be eligible
    assert rules.pick_put([_row(99.9, 1.0, 1.02)], 111.0, 0.90)["strike"] == 99.9


def test_pick_put_rejects_sparse_chain_naming_strike_and_pct():
    out = rules.pick_put([_row(80.0, 1.0, 1.02)], 100.0, 0.90)
    assert isinstance(out, Reject) and "80" in out.reason and "80.0%" in out.reason


def test_pick_put_empty_chain_rejects():
    assert isinstance(rules.pick_put([], 100.0, 0.90), Reject)


def test_evaluate_lot_one_tier_rejected_others_survive():
    puts = [_row(90.0, 1.99, 2.01), _row(79.0, 0.99, 1.01)]
    out = _eval(puts=puts, calls=[_row(110.0, 1.99, 2.01), _row(120.0, 0.99, 1.01)])
    kinds = [isinstance(x, Reject) for x in out["tiers"]]
    assert kinds == [False, True, False]
    assert [isinstance(x, Reject) for x in out["put_only"]] == kinds


def test_evaluate_lot_same_strike_warns_on_later_tier():
    puts = [_row(80.0, 0.99, 1.01), _row(84.0, 1.5, 1.52), _row(85.0, 1.6, 1.62)]
    out = _eval(puts=puts)
    assert not any("相同" in w for w in out["put_only"][0]["warnings"])
    assert any("与上一档行权价相同" in w for w in out["put_only"][1]["warnings"])


def test_evaluate_lot_wide_put_rejects_tier_but_put_only_also_rejected():
    out = _eval(puts=[_row(90.0, 1.0, 1.30)], calls=[_row(110.0, 1.99, 2.01)])
    assert isinstance(out["tiers"][0], Reject) and isinstance(out["put_only"][0], Reject)


def test_evaluate_lot_wide_call_rejects_collar_keeps_put_only():
    out = _eval(puts=[_row(90.0, 1.99, 2.01)], calls=[_row(110.0, 1.0, 2.0)])
    assert isinstance(out["tiers"][0], Reject)
    assert "Call" in out["tiers"][0].reason
    assert out["put_only"][0]["put"]["strike"] == 90.0


def test_evaluate_lot_empty_chains():
    empty_calls = _eval(calls=[{"strike": 110.0, "bid": 0, "ask": 0, "last": 0}])
    assert all(isinstance(x, Reject) for x in empty_calls["tiers"])
    assert not any(isinstance(x, Reject) for x in empty_calls["put_only"])
    empty_puts = _eval(puts=[])
    assert all(isinstance(x, Reject) for x in empty_puts["tiers"] + empty_puts["put_only"])


def _ex_div_warns(call_strike):
    out = _eval(puts=[_row(90.0, 1.99, 2.01)], calls=[_row(call_strike, 1.99, 2.01)],
                ex_div=date(2026, 11, 10))
    return any("除息" in w for w in out["tiers"][0]["warnings"])


def test_ex_div_three_percent_boundary():
    assert _ex_div_warns(103.0) is True
    assert _ex_div_warns(103.5) is False


def test_ex_div_outside_window_does_not_warn():
    base = dict(puts=[_row(90.0, 1.99, 2.01)], calls=[_row(102.0, 1.99, 2.01)])
    for ex in (date(2026, 10, 1), date(2026, 12, 15)):
        out = _eval(ex_div=ex, **base)
        assert not any("除息" in w for w in out["tiers"][0]["warnings"])


def test_stale_quote_and_unknown_earnings_warn():
    stale = {"strike": 90.0, "bid": 0, "ask": 0, "last": 2.0, "iv": 0.35}
    out = _eval(puts=[stale], calls=[_row(110.0, 1.99, 2.01)], earnings="unknown")
    w = out["tiers"][0]["warnings"]
    assert any("陈旧" in x for x in w) and any("财报日期未知" in x for x in w)


def test_cost_unknown_warning_only_on_collar_variants():
    out = _eval(_lot(cost_basis=0.0), puts=[_row(90.0, 1.99, 2.01)],
                calls=[_row(110.0, 1.99, 2.01)])
    assert any("成本未知" in w for w in out["tiers"][0]["warnings"])
    assert not any("成本未知" in w for w in out["put_only"][0]["warnings"])


def test_covered_lot_with_rejected_put_tier_keeps_tiers_empty():
    puts = [_row(90.0, 1.99, 2.01), _row(85.0, 1.5, 1.52)]  # 80% tier has no strike
    out = _eval(puts=puts, avail_put=3, avail_call=0)
    assert out["tiers"] == [] and out["note"]
    kinds = [isinstance(x, Reject) for x in out["put_only"]]
    assert kinds == [False, False, True]
    assert sum(kinds) == 1 and len(kinds) - sum(kinds) == 2


def test_uncovered_lot_rejected_tier_stays_in_tiers():
    puts = [_row(90.0, 1.99, 2.01), _row(85.0, 1.5, 1.52)]
    out = _eval(puts=puts, calls=[_row(110.0, 1.99, 2.01), _row(120.0, 1.5, 1.52)])
    assert len(out["tiers"]) == 3 and isinstance(out["tiers"][2], Reject)


def test_lot_with_puts_used_up_gets_its_own_reason():
    out = _eval(_lot(shares=300.0), avail_put=0, avail_call=3)
    assert isinstance(out, Reject)
    assert "已用完" in out.reason and "股数不足" not in out.reason


def test_lot_warnings_cost_estimated_and_merged_accounts():
    lot = HeldLot(source="real", lot_id=None, ticker="ZZZQ", shares=300.0, cost_basis=90.0,
                  original_cost=90.0, reference_price=90.0, cost_estimated=True, accounts=3,
                  label="ZZZQ")
    out = _eval(lot)
    for variant in (out["put_only"][0], out["tiers"][0]):
        assert "成本为估算值" in variant["warnings"]
        assert "来自 3 个账户合并" in variant["warnings"]
    plain = _eval()
    assert not any("估算" in w or "账户合并" in w for w in plain["put_only"][0]["warnings"])
