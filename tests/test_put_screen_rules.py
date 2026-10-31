from datetime import date

import pytest

from spread_fixtures import make_chain  # also puts scripts/ on sys.path
import put_screen_rules as rules
import spread_core as core
import spread_data
from put_screen_wiki import UniverseRow

SNAP = date(2026, 10, 18)
# 2026-11-20 is the third Friday of Nov (33 days after SNAP).
NOV_MONTHLY = "2026-11-20"
STRIKES = [float(k) for k in range(70, 100)]


def test_monthly_expirations_keeps_third_friday_only():
    listed = ["2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27", "2026-12-18"]
    assert rules.monthly_expirations(listed) == ["2026-11-20", "2026-12-18"]


def test_monthly_thursday_when_friday_holiday():
    assert rules.monthly_expirations(["2026-11-19"]) == ["2026-11-19"]
    assert rules.monthly_expirations(["2026-11-19", "2026-11-20"]) == ["2026-11-20"]


def test_pick_expiry_prefers_window():
    got = rules.pick_monthly_expiry(["2026-11-20", "2026-12-18"], SNAP)
    assert got == ("2026-11-20", True)


def test_pick_expiry_falls_back_to_nearest_to_37_and_flags():
    # Oct 16 is 15 days from Oct 1 (too near), Nov 20 is 50 days: only Nov 20 is in 21-50.
    assert rules.pick_monthly_expiry(["2026-10-16", "2026-11-20"], date(2026, 10, 1)) == ("2026-11-20", False)
    # Nov 20 is 26 days from Oct 25: outside 30-45, inside 21-50.
    assert rules.pick_monthly_expiry([NOV_MONTHLY], date(2026, 10, 25)) == (NOV_MONTHLY, False)
    # Nov 20 = 22 days, Dec 18 = 50 days from Oct 29: both outside 30-45; 50 is nearer to 37.
    assert rules.pick_monthly_expiry([NOV_MONTHLY, "2026-12-18"], date(2026, 10, 29)) == ("2026-12-18", False)


def test_pick_expiry_none_when_outside_21_50():
    assert rules.pick_monthly_expiry([NOV_MONTHLY], date(2026, 11, 10)) is None
    assert rules.pick_monthly_expiry([NOV_MONTHLY], date(2026, 9, 1)) is None


def test_pick_put_targets_delta_020():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES)
    got = rules.pick_put(chain, 100.0, 37)
    assert isinstance(got, tuple)
    quote, delta, _ = got
    best = min(STRIKES, key=lambda k: abs(core.put_delta(100.0, k, 0.35, 37) - 0.20))
    assert quote["strike"] == best
    assert delta == pytest.approx(core.put_delta(100.0, best, 0.35, 37))


def _wide_at(chain, strikes):
    out = []
    for r in chain:
        r = dict(r)
        if r["strike"] in strikes:
            r["bid"], r["ask"] = 0.10, 0.40
        out.append(r)
    return out


def test_pick_put_walks_down_when_spread_wide():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES)
    first = rules.pick_put(chain, 100.0, 37)[0]["strike"]
    quote, _, warnings = rules.pick_put(_wide_at(chain, {first}), 100.0, 37)
    assert quote["strike"] < first
    assert any("已偏离" in w for w in warnings)


def test_pick_put_gives_up_after_three_steps_or_delta_floor():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES)
    wide = _wide_at(chain, {r["strike"] for r in chain})
    assert rules.pick_put(wide, 100.0, 37) == "买卖价差过宽"


def test_pick_put_stale_accepted_with_warning():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES, stale=True)
    quote, _, warnings = rules.pick_put(chain, 100.0, 37)
    assert quote["stale"] is True
    assert "报价陈旧" in warnings


def test_pick_put_nan_bid_ask_iv_rows_are_skipped_not_crash():
    nan = float("nan")
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES)
    by_strike = {r["strike"]: r for r in chain}
    for r in (
        {"strike": 80.0, "bid": nan, "ask": nan, "last": nan, "iv": 0.35},
        {"strike": 81.0, "bid": None, "ask": None, "last": None, "iv": None},
        {"strike": 82.0, "bid": 1.0, "ask": 1.1, "last": 1.0, "iv": nan},
    ):
        by_strike[r["strike"]] = r
    got = rules.pick_put(list(by_strike.values()), 100.0, 37)
    assert isinstance(got, tuple)
    assert got[0]["strike"] not in (80.0, 81.0, 82.0)
    all_bad_iv = [{"strike": 90.0, "bid": 1.0, "ask": 1.1, "last": 1.0, "iv": nan},
                  {"strike": 91.0, "bid": 1.0, "ask": 1.1, "last": 1.0, "iv": None}]
    assert rules.pick_put(all_bad_iv, 100.0, 37) == "IV 缺失"
    no_price = [{"strike": 90.0, "bid": nan, "ask": nan, "last": nan, "iv": 0.3}]
    assert rules.pick_put(no_price, 100.0, 37) == "IV 缺失"


def _row(status="watch", tier="🏦", notes=()):
    return UniverseRow("ABC", status, tier, tuple(notes))


def _eval(row=None, *, spot=100.0, ma50=95.0, ma200=90.0, earnings="no", expirations=None):
    chain = make_chain(spot, 0.35, 33, STRIKES)
    return rules.evaluate_ticker(
        row or _row(), snapshot=SNAP, spot=spot, ma50=ma50, ma200=ma200,
        expirations=expirations if expirations is not None else [NOV_MONTHLY],
        earnings_for=lambda e: earnings, puts_for=lambda e: chain)


@pytest.mark.parametrize("kwargs,ticker,reason", [
    ({"row": UniverseRow("TSLL", "watch", None, ())}, "TSLL", "杠杆 ETF"),
    ({"row": _row("avoid")}, "ABC", "Status 为 Avoid"),
    ({"row": _row("unrecognized")}, "ABC", "状态无法识别"),
    ({"ma200": 105.0}, "ABC", "收盘价低于 MA200"),
    ({"expirations": ["2026-11-06"]}, "ABC", "没有可用的月度到期"),
    ({"earnings": "yes"}, "ABC", "到期前有财报"),
])
def test_evaluate_rejects_avoid_below_ma200_earnings_leveraged_unrecognized(kwargs, ticker, reason):
    assert _eval(**kwargs) == rules.Reject(ticker, reason)


def test_evaluate_data_error_rejects():
    def boom(expiry):
        raise spread_data.DataError("no chain")
    got = rules.evaluate_ticker(_row(), snapshot=SNAP, spot=100.0, ma50=95.0, ma200=90.0,
                                expirations=[NOV_MONTHLY], earnings_for=lambda e: "no", puts_for=boom)
    assert got == rules.Reject("ABC", "取不到期权链")


def test_evaluate_pick_put_reason_passes_through():
    nan = float("nan")
    chain = [{"strike": 90.0, "bid": 1.0, "ask": 1.1, "last": 1.0, "iv": nan}]
    got = rules.evaluate_ticker(_row(), snapshot=SNAP, spot=100.0, ma50=95.0, ma200=90.0,
                                expirations=[NOV_MONTHLY], earnings_for=lambda e: "no",
                                puts_for=lambda e: chain)
    assert got == rules.Reject("ABC", "IV 缺失")


def test_evaluate_unknown_earnings_keeps_candidate_with_warning():
    got = _eval(row=_row("under_review", notes=("n1",)), earnings="unknown", ma50=105.0)
    assert isinstance(got, dict)
    for w in ("财报日期未知", "论点复核中", "价格低于 MA50，被指派概率更高", "n1"):
        assert w in got["warnings"]
    assert got["earnings"] == "unknown"


def test_evaluate_candidate_fields_math():
    chain = [{"strike": 90.0, "bid": 1.48, "ask": 1.52, "last": 1.5,
              "iv": _iv_for_delta(100.0, 90.0, 37, 0.20)}]
    got = rules.evaluate_ticker(_row(), snapshot=date(2026, 10, 14), spot=100.0, ma50=95.0,
                                ma200=90.0, expirations=[NOV_MONTHLY],
                                earnings_for=lambda e: "no", puts_for=lambda e: chain)
    assert isinstance(got, dict)
    assert got["days"] == 37 and got["in_window"] is True
    assert got["snapshot_date"] == "2026-10-14" and got["expiry"] == NOV_MONTHLY
    assert got["cash_required"] == pytest.approx(9000)
    assert got["breakeven"] == pytest.approx(88.5)
    assert got["buffer_pct"] == pytest.approx(10.0)
    assert got["annualized_pct"] == pytest.approx(16.44, abs=0.01)
    assert got["max_loss_assigned"] == pytest.approx(8850)
    assert (got["ticker"], got["tier"], got["status"]) == ("ABC", "🏦", "watch")
    assert got["stale"] is False


def test_evaluate_out_of_window_warning():
    assert "到期日在偏好窗口之外" not in _eval()["warnings"]
    chain = make_chain(100.0, 0.35, 26, STRIKES)
    got = rules.evaluate_ticker(_row(), snapshot=date(2026, 10, 25), spot=100.0, ma50=95.0,
                                ma200=90.0, expirations=[NOV_MONTHLY],
                                earnings_for=lambda e: "no", puts_for=lambda e: chain)
    assert got["in_window"] is False and "到期日在偏好窗口之外" in got["warnings"]


def _iv_for_delta(spot, strike, days, target):
    lo, hi = 0.02, 4.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if core.put_delta(spot, strike, mid, days) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _single_row(delta):
    iv = _iv_for_delta(100.0, 90.0, 33, delta)
    return [{"strike": 90.0, "bid": 1.48, "ask": 1.52, "last": 1.5, "iv": iv}]


def test_pick_put_rejects_when_best_delta_far_from_target():
    chain = make_chain(spot=880.0, iv=0.25, days=33, strikes=[float(k) for k in range(40, 61)])
    reason = rules.pick_put(chain, 880.0, 33)
    assert isinstance(reason, str) and reason.startswith(rules.REASON_DELTA_GAP)
    assert "行权价" in reason and "Delta" in reason and "间距过粗" in reason
    got = rules.evaluate_ticker(_row(), snapshot=SNAP, spot=880.0, ma50=800.0, ma200=700.0,
                                expirations=[NOV_MONTHLY], earnings_for=lambda e: "no",
                                puts_for=lambda e: chain)
    assert got == rules.Reject("ABC", reason)


def test_pick_put_delta_gap_boundary():
    gap = core.MAX_DELTA_GAP
    assert isinstance(rules.pick_put(_single_row(0.20 - gap + 0.005), 100.0, 33), tuple)
    assert isinstance(rules.pick_put(_single_row(0.20 + gap - 0.005), 100.0, 33), tuple)
    for delta in (0.20 - gap - 0.005, 0.20 + gap + 0.005):
        got = rules.pick_put(_single_row(delta), 100.0, 33)
        assert isinstance(got, str) and got.startswith(rules.REASON_DELTA_GAP)
        assert "行权价 90 Delta" in got


def test_prescreen_reject_needs_no_market_data():
    assert rules.prescreen_reject(_row(status="avoid")) == "Status 为 Avoid"
    assert rules.prescreen_reject(_row(status="unrecognized")) == "状态无法识别"
    assert rules.prescreen_reject(UniverseRow("TSLL", "watch", None, ())) == "杠杆 ETF"
    assert rules.prescreen_reject(_row()) is None


# ---- weekly expiry mode -------------------------------------------------------------

WSNAP = date(2026, 10, 20)  # a Tuesday


def test_pick_weekly_expiry_skips_too_near_and_picks_earliest_in_window():
    assert rules.pick_weekly_expiry(["2026-10-23", "2026-10-30", "2026-11-20"], WSNAP) == ("2026-10-30", True)


def test_pick_weekly_expiry_boundaries_and_unsorted():
    assert rules.pick_weekly_expiry(["2026-10-27"], WSNAP) == ("2026-10-27", True)   # 7 days
    assert rules.pick_weekly_expiry(["2026-11-03"], WSNAP) == ("2026-11-03", True)   # 14 days
    assert rules.pick_weekly_expiry(["2026-11-04"], WSNAP) is None                   # 15 days
    assert rules.pick_weekly_expiry(["2026-10-26"], WSNAP) is None                   # 6 days
    assert rules.pick_weekly_expiry(["2026-11-03", "2026-10-30", "2026-10-27"], WSNAP) == ("2026-10-27", True)


def test_pick_weekly_expiry_none_when_only_monthly_far_out():
    assert rules.pick_weekly_expiry(["2026-11-19"], WSNAP) is None
    assert rules.pick_weekly_expiry([], WSNAP) is None


def _weekly_eval(*, expirations, earnings_for=lambda e: "no", dte_mode="weekly", snapshot=WSNAP):
    chain = make_chain(100.0, 0.35, 10, STRIKES, quote_spread=0.005)
    return rules.evaluate_ticker(
        _row(), snapshot=snapshot, spot=100.0, ma50=95.0, ma200=90.0, expirations=expirations,
        earnings_for=earnings_for, puts_for=lambda e: chain, dte_mode=dte_mode)


def test_evaluate_weekly_candidate_in_seven_to_fourteen_days():
    got = _weekly_eval(expirations=["2026-10-23", "2026-10-30", "2026-11-20"])
    assert isinstance(got, dict)
    assert got["expiry"] == "2026-10-30" and 7 <= got["days"] <= 14
    assert got["dte_mode"] == "weekly" and got["in_window"] is True
    assert "到期日在偏好窗口之外" not in got["warnings"]


def test_evaluate_weekly_rejects_when_only_monthly_listed():
    assert _weekly_eval(expirations=["2026-11-20"]) == rules.Reject("ABC", "没有可用的周度到期")


def test_evaluate_weekly_earnings_uses_weekly_expiry():
    # earnings fall after the weekly expiry but before the monthly one
    asked = []

    def earnings_for(expiry):
        asked.append(expiry)
        return "yes" if expiry == "2026-11-20" else "no"

    got = _weekly_eval(expirations=["2026-10-30", "2026-11-20"], earnings_for=earnings_for)
    assert isinstance(got, dict) and asked == ["2026-10-30"]
    mon = _weekly_eval(expirations=["2026-10-30", "2026-11-20"], earnings_for=earnings_for,
                       dte_mode="monthly", snapshot=date(2026, 10, 16))
    assert mon == rules.Reject("ABC", "到期前有财报")


def test_evaluate_monthly_mode_labels_candidate_and_keeps_old_reason():
    assert _eval()["dte_mode"] == "monthly"
    assert _eval(expirations=["2026-11-06"]) == rules.Reject("ABC", "没有可用的月度到期")


def _with_delta(chain, shift):
    return [{**r, "delta": core.put_delta(100.0, r["strike"], r["iv"], 37) + shift} for r in chain]


def test_pick_put_prefers_broker_delta_over_bs():
    chain = _with_delta(make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES), 0.03)
    quote, delta, warnings = rules.pick_put(chain, 100.0, 37)
    assert delta == pytest.approx(quote["delta"])
    assert delta == pytest.approx(core.put_delta(100.0, quote["strike"], 0.35, 37) + 0.03)
    assert not any("Black-Scholes" in w for w in warnings)


def test_pick_put_warns_when_crosscheck_exceeds_tolerance():
    chain = _with_delta(make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES), -0.10)
    quote, delta, warnings = rules.pick_put(chain, 100.0, 37)
    assert delta == pytest.approx(quote["delta"])
    assert sum("Black-Scholes" in w for w in warnings) == 1


def test_pick_put_legacy_rows_unchanged():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES)
    quote, delta, warnings = rules.pick_put(chain, 100.0, 37)
    assert delta == pytest.approx(core.put_delta(100.0, quote["strike"], 0.35, 37))
    assert not any("Black-Scholes" in w for w in warnings)


def _put_chain_bs():
    return _with_delta(make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES), 0.0)


def _is_cross(w):
    return "Black-Scholes" in w


def test_pick_put_warning_only_for_chosen_strike_not_skipped_ones():
    chain = _put_chain_bs()
    chosen = rules.pick_put(chain, 100.0, 37)[0]["strike"]
    bad = [{**r, "delta": r["delta"] + 0.25} if r["strike"] == STRIKES[0] else r for r in chain]
    quote, _, warnings = rules.pick_put(bad, 100.0, 37)
    assert quote["strike"] == chosen and not any(_is_cross(w) for w in warnings)


def test_pick_put_no_warning_when_chosen_in_tolerance_but_others_are_not():
    chain = _put_chain_bs()
    chosen = rules.pick_put(chain, 100.0, 37)[0]["strike"]
    bad = [r if r["strike"] == chosen else {**r, "delta": 0.0} for r in chain]
    quote, _, warnings = rules.pick_put(bad, 100.0, 37)
    assert quote["strike"] == chosen and not any(_is_cross(w) for w in warnings)


def test_pick_put_keeps_row_with_broker_delta_and_no_iv():
    chain = [{**r, "iv": None} for r in _put_chain_bs()]
    quote, delta, warnings = rules.pick_put(chain, 100.0, 37)
    assert delta == pytest.approx(quote["delta"]) and not any(_is_cross(w) for w in warnings)


def test_pick_put_zero_broker_delta_counts_as_present():
    chain = [{**r, "iv": None, "delta": 0.0} for r in _put_chain_bs()]
    got = rules.pick_put(chain, 100.0, 37)
    assert isinstance(got, str) and "Delta" in got  # kept as 0.0, not skipped as "no iv"
    assert got != rules.REASON_NO_IV


def test_pick_put_skips_greekless_strike_even_if_nearest():
    chain = _put_chain_bs()
    first = rules.pick_put(chain, 100.0, 37)[0]["strike"]
    holed = [dict(r, iv=None, delta=None) if r["strike"] == first else r for r in chain]
    got = rules.pick_put(holed, 100.0, 37)
    assert not isinstance(got, str) and got[0]["strike"] != first


def test_evaluate_ticker_all_greekless_rejects_no_iv():
    chain = [dict(r, iv=None, delta=None) for r in make_chain(100.0, 0.35, 33, STRIKES)]
    got = rules.evaluate_ticker(
        _row(), snapshot=SNAP, spot=100.0, ma50=95.0, ma200=90.0, expirations=[NOV_MONTHLY],
        earnings_for=lambda e: "no", puts_for=lambda e: chain)
    assert got.reason == rules.REASON_NO_IV


# --- --delta target: argument type, walk floor, target-aware pick_put ---------------------------
import argparse
import re
from pathlib import Path

WIDE_STRIKES = [float(k) for k in range(70, 100, 2)]


@pytest.mark.parametrize("text,value", [("0.15", 0.15), ("0.2", 0.2), ("0.05", 0.05), ("0.40", 0.4)])
def test_delta_arg_accepts(text, value):
    assert rules.delta_arg(text) == value


@pytest.mark.parametrize("text", ["abc", "", "nan", "inf", "-0.1", "0", "0.04", "0.41", "0.5", "0.125"])
def test_delta_arg_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError) as exc:
        rules.delta_arg(text)
    assert str(exc.value) == rules.DELTA_ARG_ERROR


def test_delta_arg_error_text_is_fixed():
    assert rules.DELTA_ARG_ERROR == "--delta 须是 0.05–0.40 之间、最多两位小数的数，例如 0.15（短腿 Delta 越小越价外）"


@pytest.mark.parametrize("target,floor", [(0.20, 0.10), (0.15, 0.10), (0.10, 0.10), (0.05, 0.05), (0.30, 0.10)])
def test_walk_floor_table(target, floor):
    assert rules.walk_floor(target) == floor


def test_is_default_delta():
    assert rules.is_default_delta(0.2) and rules.is_default_delta(0.20)
    assert not rules.is_default_delta(0.15)


def _nearest(target, strikes=STRIKES):
    return min(strikes, key=lambda k: abs(core.put_delta(100.0, k, 0.35, 37) - target))


def test_pick_put_target_015_starts_nearest_015():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES, quote_spread=0.01)
    quote, delta, warnings = rules.pick_put(chain, 100.0, 37, target_delta=0.15)
    assert quote["strike"] == _nearest(0.15)
    assert not any("已偏离" in w for w in warnings)


def _wide_below(chain, floor):
    """Wide everywhere a walk may stand (Delta >= floor); tight only beyond the floor."""
    return [dict(r, bid=0.10, ask=0.40) if core.put_delta(100.0, r["strike"], 0.35, 37) >= floor
            else r for r in chain]


def test_pick_put_walks_to_floor_not_below():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=WIDE_STRIKES, quote_spread=0.01)
    assert rules.pick_put(_wide_below(chain, 0.10), 100.0, 37,
                          target_delta=0.15) == rules.REASON_WIDE


def test_pick_put_target_010_never_walks():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES, quote_spread=0.01)
    best = _nearest(0.10)
    got = rules.pick_put(_wide_at(chain, {best}), 100.0, 37, target_delta=0.10)
    assert got == rules.REASON_WIDE


def test_pick_put_target_005_floor_equals_target():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES, quote_spread=0.01)
    best = _nearest(0.05)
    got = rules.pick_put(_wide_at(chain, {best}), 100.0, 37, target_delta=0.05)
    assert got == rules.REASON_WIDE


def test_pick_put_target_040_gap_reject_text():
    got = rules.pick_put(_single_row(0.30), 100.0, 33, target_delta=0.40)
    assert isinstance(got, str) and got.startswith(rules.REASON_DELTA_GAP)
    assert "最接近 0.40 的行权价" in got


def test_pick_put_drift_warning_uses_target():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES, quote_spread=0.01)
    first = _nearest(0.15)
    _, _, warnings = rules.pick_put(_wide_at(chain, {first}), 100.0, 37, target_delta=0.15)
    assert any(w.endswith("已偏离 0.15") for w in warnings)


def test_pick_put_default_texts_unchanged():
    chain = make_chain(spot=100.0, iv=0.35, days=37, strikes=STRIKES, quote_spread=0.01)
    wide = _wide_at(chain, {rules.pick_put(chain, 100.0, 37)[0]["strike"]})
    plain = rules.pick_put(wide, 100.0, 37)
    assert plain == rules.pick_put(wide, 100.0, 37, target_delta=0.2)
    assert any(w.endswith("已偏离 0.20") for w in plain[2])
    gap = rules.pick_put(_single_row(0.30), 100.0, 33)
    assert gap == rules.pick_put(_single_row(0.30), 100.0, 33, target_delta=0.2)
    assert "最接近 0.20 的行权价" in gap


def test_candidate_target_key_only_when_non_default():
    def run(**kw):
        chain = make_chain(100.0, 0.35, 33, STRIKES, quote_spread=0.01)
        return rules.evaluate_ticker(
            _row(), snapshot=SNAP, spot=100.0, ma50=95.0, ma200=90.0, expirations=[NOV_MONTHLY],
            earnings_for=lambda e: "no", puts_for=lambda e: chain, **kw)
    assert "target_delta" not in run()
    assert "target_delta" not in run(target_delta=0.2)
    assert run(target_delta=0.15)["target_delta"] == 0.15


def test_no_new_delta_literals_in_rules():
    """The new thresholds live on constant lines only (the plan's Global Constraints)."""
    src = (Path(rules.__file__)).read_text(encoding="utf-8").splitlines()
    loose = [ln for ln in src if not re.match(r"[A-Z_]+ = ", ln)
             and re.search(r"0\.05|0\.40|0\.15", ln)]
    assert loose == []
