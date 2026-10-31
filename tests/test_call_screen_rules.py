from datetime import date

import pytest

from spread_fixtures import make_call_chain  # also puts scripts/ on sys.path
import call_screen_rules as rules
import put_screen_rules as put_rules
import spread_core as core
import spread_data
from holdings import HeldLot

SNAP = date(2026, 10, 21)
WEEKLY = "2026-11-03"  # 13 days after SNAP
STRIKES = [float(k) for k in range(101, 131)]


def _lot(shares=300.0, cost_basis=90.0, original_cost=92.0, accounts=1, estimated=False):
    return HeldLot(source="manual", lot_id=7, ticker="ABC", shares=shares,
                   cost_basis=cost_basis, original_cost=original_cost,
                   reference_price=95.0, cost_estimated=estimated, accounts=accounts,
                   label="ABC（假设 #7）")


def _eval(lot=None, *, chain=None, available=3, expirations=(WEEKLY,), earnings="no",
          ex_div=None, ma50=95.0, ma200=90.0, month_return=3.0, spot=100.0, **kw):
    chain = chain if chain is not None else make_call_chain(
        spot=spot, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    return rules.evaluate_lot(
        lot or _lot(), snapshot=SNAP, spot=spot, ma50=ma50, ma200=ma200,
        month_return=month_return, contracts_available=available,
        expirations=list(expirations), earnings_for=lambda e: earnings,
        ex_div_for=lambda e: ex_div, calls_for=lambda e: chain, **kw)


def _wide_at(chain, strikes):
    out = []
    for r in chain:
        r = dict(r)
        if r["strike"] in strikes:
            r["bid"], r["ask"] = 0.10, 0.40
        out.append(r)
    return out


def test_pick_call_nearest_delta_otm_only():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13,
                            strikes=[95.0, 98.0] + STRIKES, quote_spread=0.02)
    quote, delta, _ = rules.pick_call(chain, 100.0, 13)
    best = min(STRIKES, key=lambda k: abs(core.call_delta(100.0, k, 0.35, 13) - 0.20))
    assert quote["strike"] == best > 100.0
    assert delta == pytest.approx(core.call_delta(100.0, best, 0.35, 13))


def test_pick_call_rejects_large_delta_gap_with_numbers():
    chain = make_call_chain(spot=100.0, iv=0.25, days=13,
                            strikes=[float(k) for k in range(140, 151)])
    reason = rules.pick_call(chain, 100.0, 13)
    assert isinstance(reason, str) and reason.startswith(put_rules.REASON_DELTA_GAP)
    assert "行权价" in reason and "Delta" in reason and "间距过粗" in reason


def test_pick_call_walks_up_on_wide_spread():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    first = rules.pick_call(chain, 100.0, 13)[0]["strike"]
    quote, _, warnings = rules.pick_call(_wide_at(chain, {first}), 100.0, 13)
    assert quote["strike"] > first
    assert any("已偏离" in w for w in warnings)


def test_pick_call_wide_throughout():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    wide = _wide_at(chain, {r["strike"] for r in chain})
    assert rules.pick_call(wide, 100.0, 13) == put_rules.REASON_WIDE


def test_pick_call_no_usable_iv():
    chain = [dict(r, iv=None) for r in make_call_chain(spot=100.0, days=13, strikes=STRIKES, quote_spread=0.02)]
    assert rules.pick_call(chain, 100.0, 13) == put_rules.REASON_NO_IV


def test_pick_call_stale_quote_warning():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, stale=True)
    quote, _, warnings = rules.pick_call(chain, 100.0, 13)
    assert quote["stale"] is True and "报价陈旧" in warnings


def test_evaluate_lot_rejects_fractional_shares():
    got = _eval(_lot(shares=36.68), available=0)
    assert got == put_rules.Reject("ABC（假设 #7）", "股数不足 100 股")


def test_evaluate_lot_rejects_when_fully_covered():
    got = _eval(available=0)
    assert got == put_rules.Reject("ABC（假设 #7）", "已被未平仓 call 覆盖")


def test_evaluate_lot_rejects_earnings_before_expiry():
    assert _eval(earnings="yes") == put_rules.Reject("ABC（假设 #7）", "到期前有财报")


def test_evaluate_lot_unknown_earnings_warns():
    got = _eval(earnings="unknown")
    assert isinstance(got, dict) and "财报日期未知" in got["warnings"]


def test_evaluate_lot_chain_error_rejects():
    def boom(e):
        raise spread_data.DataError("x")
    got = rules.evaluate_lot(
        _lot(), snapshot=SNAP, spot=100.0, ma50=None, ma200=None, month_return=None,
        contracts_available=1, expirations=[WEEKLY], earnings_for=lambda e: "no",
        ex_div_for=lambda e: None, calls_for=boom)
    assert got == put_rules.Reject("ABC（假设 #7）", "取不到期权链")


def test_evaluate_lot_warns_below_cost_not_rejects():
    got = _eval(_lot(cost_basis=118.0, original_cost=120.0))
    assert isinstance(got, dict)
    warn = next(w for w in got["warnings"] if "低于成本" in w)
    assert "每股亏" in warn
    assert "120.00" in warn and "118.00" in warn
    loss_orig = 120.0 - (got["strike"] + got["mid"])
    loss_eff = 118.0 - (got["strike"] + got["mid"])
    assert f"{loss_orig:.2f}" in warn and f"{loss_eff:.2f}" in warn


def test_evaluate_lot_no_below_cost_warning_when_strike_above_cost():
    got = _eval(_lot(cost_basis=90.0, original_cost=92.0))
    assert not any("低于成本" in w for w in got["warnings"])


def test_evaluate_lot_ex_dividend_warning():
    got = _eval(ex_div=date(2026, 10, 30))
    assert any("除息" in w and "2026-10-30" in w for w in got["warnings"])
    assert got["ex_dividend"] == "2026-10-30"


def test_evaluate_lot_estimated_and_merged_warnings():
    got = _eval(_lot(estimated=True, accounts=3))
    assert "成本为估算值" in got["warnings"]
    assert "来自 3 个账户合并" in got["warnings"]


def test_evaluate_lot_weekly_no_expiry_reason():
    got = _eval(expirations=["2026-12-18"])
    assert got == put_rules.Reject("ABC（假设 #7）", put_rules.REASON_NO_WEEKLY)


def test_evaluate_lot_monthly_no_expiry_reason():
    got = _eval(expirations=["2026-12-18"], dte_mode="monthly")
    assert got == put_rules.Reject("ABC（假设 #7）", put_rules.REASON_NO_MONTHLY)


def test_candidate_reports_margins_vs_ma50_ma200_when_missing_mas_none():
    got = _eval(ma50=80.0, ma200=125.0)
    assert got["ma50_pct"] == pytest.approx(25.0)
    assert got["ma200_pct"] == pytest.approx(-20.0)
    none = _eval(ma50=None, ma200=None, month_return=None)
    assert none["ma50_pct"] is None and none["ma200_pct"] is None
    assert none["month_return_pct"] is None


def test_candidate_numbers():
    got = _eval()
    assert got["premium_per_contract"] == pytest.approx(got["mid"] * 100)
    assert got["upside_to_strike_pct"] == pytest.approx((got["strike"] - 100.0) / 100.0 * 100)
    assert got["annualized_pct"] == pytest.approx(got["mid"] / 100.0 * 365 / 13 * 100)
    assert got["ticker"] == "ABC" and got["source"] == "manual" and got["lot_id"] == 7
    assert got["contracts_available"] == 3 and got["days"] == 13 and got["in_window"] is True
    assert got["snapshot_date"] == "2026-10-21" and got["ex_dividend"] is None


def test_evaluate_lot_rejects_unknown_cost():
    got = _eval(_lot(cost_basis=0.0, original_cost=0.0))
    assert isinstance(got, put_rules.Reject) and got.reason == "成本未知"


def test_unknown_cost_reason_comes_after_coverage():
    got = _eval(_lot(shares=50.0, cost_basis=0.0, original_cost=0.0), available=0)
    assert got.reason == rules.REASON_SHORT_SHARES


def _calls_with_delta(shift):
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    return [{**r, "delta": core.call_delta(100.0, r["strike"], r["iv"], 13) + shift} for r in chain]


def test_pick_call_prefers_broker_delta_over_bs():
    quote, delta, warnings = rules.pick_call(_calls_with_delta(0.03), 100.0, 13)
    assert delta == pytest.approx(quote["delta"])
    assert delta == pytest.approx(core.call_delta(100.0, quote["strike"], 0.35, 13) + 0.03)
    assert not any("Black-Scholes" in w for w in warnings)


def test_pick_call_warns_when_crosscheck_exceeds_tolerance():
    quote, delta, warnings = rules.pick_call(_calls_with_delta(-0.10), 100.0, 13)
    assert delta == pytest.approx(quote["delta"])
    assert sum("Black-Scholes" in w for w in warnings) == 1


def test_pick_call_legacy_rows_unchanged():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    quote, delta, warnings = rules.pick_call(chain, 100.0, 13)
    assert delta == pytest.approx(core.call_delta(100.0, quote["strike"], 0.35, 13))
    assert not any("Black-Scholes" in w for w in warnings)


def test_pick_call_warning_only_for_chosen_strike_not_skipped_ones():
    chain = _calls_with_delta(0.0)
    chosen = rules.pick_call(chain, 100.0, 13)[0]["strike"]
    bad = [{**r, "delta": r["delta"] + 0.25} if r["strike"] == STRIKES[-1] else r for r in chain]
    quote, _, warnings = rules.pick_call(bad, 100.0, 13)
    assert quote["strike"] == chosen and not any("Black-Scholes" in w for w in warnings)


def test_pick_call_no_warning_when_chosen_in_tolerance_but_others_are_not():
    chain = _calls_with_delta(0.0)
    chosen = rules.pick_call(chain, 100.0, 13)[0]["strike"]
    bad = [r if r["strike"] == chosen else {**r, "delta": 0.0} for r in chain]
    quote, _, warnings = rules.pick_call(bad, 100.0, 13)
    assert quote["strike"] == chosen and not any("Black-Scholes" in w for w in warnings)


def test_pick_call_keeps_row_with_broker_delta_and_no_iv():
    chain = [{**r, "iv": None} for r in _calls_with_delta(0.0)]
    quote, delta, warnings = rules.pick_call(chain, 100.0, 13)
    assert delta == pytest.approx(quote["delta"]) and not any("Black-Scholes" in w for w in warnings)


def test_pick_call_zero_broker_delta_counts_as_present():
    chain = [{**r, "iv": None, "delta": 0.0} for r in _calls_with_delta(0.0)]
    got = rules.pick_call(chain, 100.0, 13)
    assert isinstance(got, str) and got != rules.REASON_NO_IV


def test_pick_call_skips_greekless_strike():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    first = rules.pick_call(chain, 100.0, 13)[0]["strike"]
    holed = [dict(r, iv=None, delta=None) if r["strike"] == first else r for r in chain]
    got = rules.pick_call(holed, 100.0, 13)
    assert not isinstance(got, str) and got[0]["strike"] != first


# --- --delta target: shared constants, walk-up floor, target-aware texts -------------------------
import re
from pathlib import Path

WIDE_UP = [float(k) for k in range(101, 160, 2)]


def _call_delta(strike):
    return core.call_delta(100.0, strike, 0.35, 13)


def _wide_from(chain, floor):
    """Wide everywhere a walk may stand (Delta >= floor); tight only beyond the floor."""
    return [dict(r, bid=0.10, ask=0.40) if _call_delta(r["strike"]) >= floor else r for r in chain]


def _chain():
    return make_call_chain(spot=100.0, iv=0.35, days=13, strikes=WIDE_UP, quote_spread=0.01)


def test_call_rules_import_shared_constants():
    assert rules.walk_floor is put_rules.walk_floor
    assert rules.is_default_delta is put_rules.is_default_delta


def test_pick_call_target_015_starts_nearest_015():
    quote, _, warnings = rules.pick_call(_chain(), 100.0, 13, target_delta=0.15)
    best = min(WIDE_UP, key=lambda k: abs(_call_delta(k) - 0.15))
    assert quote["strike"] == best
    assert not any("已偏离" in w for w in warnings)


def test_pick_call_walks_up_strike_not_below_floor():
    chain = _chain()
    first = rules.pick_call(chain, 100.0, 13, target_delta=0.15)[0]["strike"]
    stuck = _wide_from(chain, 0.10)
    assert rules.pick_call(stuck, 100.0, 13, target_delta=0.15) == put_rules.REASON_WIDE
    one_wide = _wide_at(chain, {first})
    quote, delta, _ = rules.pick_call(one_wide, 100.0, 13, target_delta=0.15)
    assert quote["strike"] > first and delta >= 0.10


def test_pick_call_target_010_never_walks():
    chain = _chain()
    best = min(WIDE_UP, key=lambda k: abs(_call_delta(k) - 0.10))
    assert rules.pick_call(_wide_at(chain, {best}), 100.0, 13,
                           target_delta=0.10) == put_rules.REASON_WIDE


def test_pick_call_gap_and_drift_text_use_target():
    far = make_call_chain(spot=100.0, iv=0.25, days=13, strikes=[float(k) for k in range(140, 151)])
    gap = rules.pick_call(far, 100.0, 13, target_delta=0.15)
    assert isinstance(gap, str) and "最接近 0.15 的行权价" in gap
    chain = _chain()
    first = rules.pick_call(chain, 100.0, 13, target_delta=0.15)[0]["strike"]
    _, _, warnings = rules.pick_call(_wide_at(chain, {first}), 100.0, 13, target_delta=0.15)
    assert any(w.endswith("已偏离 0.15") for w in warnings)


def test_pick_call_default_texts_unchanged():
    chain = make_call_chain(spot=100.0, iv=0.35, days=13, strikes=STRIKES, quote_spread=0.02)
    first = rules.pick_call(chain, 100.0, 13)[0]["strike"]
    wide = _wide_at(chain, {first})
    plain = rules.pick_call(wide, 100.0, 13)
    assert plain == rules.pick_call(wide, 100.0, 13, target_delta=0.2)
    assert any(w.endswith("已偏离 0.20") for w in plain[2])
    far = make_call_chain(spot=100.0, iv=0.25, days=13, strikes=[float(k) for k in range(140, 151)])
    assert "最接近 0.20 的行权价" in rules.pick_call(far, 100.0, 13)


def test_candidate_target_key_only_when_non_default():
    chain = _chain()
    assert "target_delta" not in _eval(chain=chain)
    assert "target_delta" not in _eval(chain=chain, target_delta=0.2)
    assert _eval(chain=chain, target_delta=0.15)["target_delta"] == 0.15


def test_no_new_delta_literals_in_call_rules():
    src = Path(rules.__file__).read_text(encoding="utf-8").splitlines()
    loose = [ln for ln in src if not re.match(r"[A-Z_]+ = ", ln)
             and re.search(r"0\.05|0\.40|0\.15|0\.10", ln)]
    assert loose == []
