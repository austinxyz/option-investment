from spread_fixtures import make_chain  # noqa: F401  (puts scripts/ on sys.path)
import put_review_rules as rules


def m(**kw):
    base = dict(days_left=20, itm=False, stop_flag=False, captured=0.30,
                delta=0.15, data_ok=True, earnings="no", error=None)
    base.update(kw)
    return base


def test_error_row_is_attention_with_reason():
    r = rules.review_position(m(error="boom"))
    assert r["advice"] == "attention" and r["rule"] == 0
    assert "取不到报价" in r["reason"]


def test_expired_today_awaits_settlement():
    for d in (0, -2):
        r = rules.review_position(m(days_left=d))
        assert r["advice"] == "await_settlement" and r["rule"] == 1


def test_itm_is_attention():
    r = rules.review_position(m(itm=True))
    assert r["advice"] == "attention" and r["rule"] == 2


def test_stop_flag_is_attention():
    r = rules.review_position(m(stop_flag=True))
    assert r["advice"] == "attention" and r["rule"] == 2


def test_itm_beats_high_capture():
    r = rules.review_position(m(itm=True, captured=0.95))
    assert r["advice"] == "attention" and r["rule"] == 2


def test_capture_exactly_80_percent_closes():
    r = rules.review_position(m(captured=0.80))
    assert r["advice"] == "close" and r["rule"] == 3
    assert rules.review_position(m(captured=0.7999))["advice"] != "close"


def test_capture_beats_earnings():
    r = rules.review_position(m(captured=0.85, earnings="yes"))
    assert r["advice"] == "close"


def test_earnings_in_window_is_attention():
    r = rules.review_position(m(earnings="yes"))
    assert r["advice"] == "attention" and r["rule"] == 4


def test_delta_exactly_035_is_attention_when_data_ok():
    r = rules.review_position(m(delta=0.35))
    assert r["advice"] == "attention" and r["rule"] == 4
    assert rules.review_position(m(delta=0.3499))["advice"] == "hold"


def test_delta_ignored_when_data_not_ok():
    r = rules.review_position(m(data_ok=False, delta=0.50))
    assert r["advice"] == "hold"
    assert "数据陈旧，Delta 未使用" in r["reason"]


def test_near_expiry_quiet_delta_hold_note():
    r = rules.review_position(m(days_left=5, delta=0.10))
    assert r["advice"] == "hold" and "等它自然到期归零" in r["reason"]
    assert "等它自然到期归零" not in rules.review_position(m(days_left=6, delta=0.10))["reason"]
    assert "等它自然到期归零" not in rules.review_position(m(days_left=5, delta=0.1001))["reason"]


def test_negative_capture_holds_unless_other_rule():
    assert rules.review_position(m(captured=-0.5))["advice"] == "hold"


def test_thresholds_dict_has_all_four():
    assert rules.thresholds() == {
        "CLOSE_CAPTURED": 0.80, "ATTENTION_DELTA": 0.35,
        "NEAR_EXPIRY_DAYS": 5, "QUIET_DELTA": 0.10}


def test_stale_note_ends_every_non_terminal_reason():
    for kw in (dict(itm=True), dict(captured=0.9), dict(earnings="yes"), dict()):
        assert rules.review_position(m(data_ok=False, **kw))["reason"].endswith("数据陈旧，Delta 未使用")


def test_capture_float_noise_still_closes():
    r = rules.review_position(m(captured=0.7999999999999999))
    assert r["advice"] == "close" and r["rule"] == 3


def test_itm_reason_is_strategy_aware():
    csp = rules.review_position(m(itm=True, strategy="cash_secured_put"))["reason"]
    spread = rules.review_position(m(itm=True, strategy="bull_put_spread"))["reason"]
    neutral = rules.review_position(m(itm=True))["reason"]
    assert "接受被指派" in csp and "最大亏损" in spread
    assert "评估风险" in neutral and "展期" not in neutral


def test_unknown_earnings_noted_on_hold_and_close():
    assert "财报日期未知" in rules.review_position(m(earnings="unknown"))["reason"]
    assert "财报日期未知" in rules.review_position(m(earnings="unknown", captured=0.9))["reason"]
    assert "财报日期未知" not in rules.review_position(m())["reason"]


def test_th_none_matches_defaults():
    for x in (m(), m(captured=0.9), m(delta=0.4)):
        base = rules.review_position(x)
        assert rules.review_position(x, None) == base
        assert rules.review_position(x, rules.thresholds()) == base


def test_th_close_captured_override():
    x = m(captured=0.55)
    assert rules.review_position(x)["advice"] != "close"
    r = rules.review_position(x, {"CLOSE_CAPTURED": 0.5})
    assert r["advice"] == "close" and r["rule"] == 3


def test_th_attention_delta_override():
    x = m(delta=0.30)
    assert rules.review_position(x)["advice"] == "hold"
    r = rules.review_position(x, {"ATTENTION_DELTA": 0.25})
    assert r["advice"] == "attention" and r["rule"] == 4


def test_th_partial_dict_falls_back_to_defaults():
    r = rules.review_position(m(delta=0.30), {"CLOSE_CAPTURED": 0.5})
    assert r["advice"] == "hold"


def test_th_float_tolerance_still_applies():
    r = rules.review_position(m(captured=0.7999999999999999), {"CLOSE_CAPTURED": 0.8})
    assert r["advice"] == "close" and r["rule"] == 3


def test_covered_call_itm_attention_with_assignment_wording():
    r = rules.review_position(m(itm=True, strategy="covered_call"))
    assert r["advice"] == "attention" and r["rule"] == 2
    assert r["reason"] == "现价已到行权价之上，可能被行权（股票按行权价卖出）；由你决定接受行权还是回购"


def test_covered_call_stop_flag_reason():
    r = rules.review_position(m(stop_flag=True, strategy="covered_call"))
    assert r["advice"] == "attention" and r["rule"] == 2
    assert r["reason"] == "已触及止损线（亏损≥权利金的2倍）：回购止损，或继续持有"


def test_existing_reasons_unchanged():
    base = "已进入价内或触及止损线："
    expected = {
        "cash_secured_put": base + "平仓止损，或接受被指派",
        "bull_put_spread": base + "平仓止损，或承担价差最大亏损",
        None: base + "平仓止损或评估风险",
    }
    for strategy, text in expected.items():
        for flag in ("itm", "stop_flag"):
            r = rules.review_position(m(strategy=strategy, **{flag: True}))
            assert r["reason"] == text and r["rule"] == 2


# ---- collar / protective put (task 6) -----------------------------------------------

def lm(**kw):
    base = dict(strategy="collar", days_left=20, spot=100.0, put_strike=90.0,
                call_strike=110.0, ex_div=None, error=None, data_ok=True)
    base.update(kw)
    return base


def test_review_collar_call_itm_needs_attention():
    v = rules.review_position(lm(spot=110.0))
    assert v["advice"] == "attention" and v["rule"] == 2
    assert "110" in v["reason"] and "卖出" in v["reason"]


def test_review_collar_put_itm_is_info_hold():
    v = rules.review_position(lm(spot=85.0))
    assert v["advice"] == "hold" and v["rule"] == 4
    assert "保护已生效" in v["reason"] and "90" in v["reason"]


def test_review_collar_ex_div_with_itm_call_attention():
    v = rules.review_position(lm(spot=112.0, ex_div="2026-11-05"))
    assert v["advice"] == "attention" and v["rule"] == 3
    assert "提前指派" in v["reason"] and "2026-11-05" in v["reason"]


def test_review_collar_ex_div_with_otm_call_is_ignored():
    assert rules.review_position(lm(spot=100.0, ex_div="2026-11-05"))["rule"] == 6


def test_review_collar_expiring_soon_reminds_protection_ends():
    v = rules.review_position(lm(days_left=7))
    assert v["advice"] == "hold" and v["rule"] == 5 and "保护" in v["reason"]
    assert rules.review_position(lm(days_left=8))["rule"] == 6


def test_review_collar_never_recommends_profit_close():
    for spot in (70.0, 85.0, 100.0, 109.0):
        v = rules.review_position(lm(spot=spot, captured=0.95, delta=0.5, earnings="yes"))
        assert v["advice"] in ("hold", "attention")


def test_review_collar_missing_quote_needs_attention():
    v = rules.review_position(lm(error="期权链里找不到这条腿的有效报价", spot=None))
    assert v["advice"] == "attention" and v["rule"] == 0 and "取不到报价" in v["reason"]


def test_review_collar_expiry_awaits_settlement():
    v = rules.review_position(lm(days_left=0, spot=120.0))
    assert v["advice"] == "await_settlement" and v["rule"] == 1


def test_review_protective_put_has_no_call_rules():
    v = rules.review_position(lm(strategy="protective_put", call_strike=None, spot=500.0,
                                 ex_div="2026-11-05"))
    assert v["advice"] == "hold" and v["rule"] == 6
    assert rules.review_position(lm(strategy="protective_put", call_strike=None,
                                    spot=85.0))["rule"] == 4


# ---- iron condor (spec 5.12 / 12.9) -------------------------------------------------
# ZZZQ condor: short put 90 / short call 110, spot 100 unless a test moves it.

def cm(**kw):
    base = dict(strategy="iron_condor", days_left=20, spot=100.0, put_short_strike=90.0,
                call_short_strike=110.0, stop_flag=False, captured=0.30,
                put_short_delta=0.15, call_short_delta=0.12, data_ok=True, earnings="no",
                error=None, ex_div=None)
    base.update(kw)
    return base


def test_review_condor_put_side_itm_needs_attention():
    r = rules.review_position(cm(spot=89.99))
    assert r["advice"] == "attention" and r["rule"] == 2 and "Put" in r["reason"]
    # equal to the short put settles with zero put-side loss, so it is not in the money
    assert rules.review_position(cm(spot=90.0))["rule"] == 5


def test_review_condor_call_side_itm_strict_inequality():
    r = rules.review_position(cm(spot=110.0))
    assert r["advice"] == "hold" and r["rule"] == 5
    r = rules.review_position(cm(spot=110.01))
    assert r["advice"] == "attention" and r["rule"] == 2 and "Call" in r["reason"]
    assert rules.condor_itm_side(110.0, 90.0, 110.0) is None
    assert rules.condor_itm_side(89.0, 90.0, 110.0) == "put"


def test_review_condor_call_itm_mentions_ex_div():
    r = rules.review_position(cm(spot=111.0, ex_div="2026-10-20"))
    assert r["rule"] == 2 and "2026-10-20" in r["reason"]


def test_review_condor_stop_flag_needs_attention():
    r = rules.review_position(cm(stop_flag=True, captured=0.9))
    assert r["advice"] == "attention" and r["rule"] == 2 and "止损" in r["reason"]


def test_review_condor_80_percent_captured_recommends_close():
    r = rules.review_position(cm(captured=0.80))
    assert r["advice"] == "close" and r["rule"] == 3 and "80%" in r["reason"]
    # closing beats a high delta on either side
    assert rules.review_position(cm(captured=0.85, call_short_delta=0.50))["rule"] == 3
    assert rules.review_position(cm(captured=0.79))["rule"] == 5


def test_review_condor_either_short_delta_high_attention():
    r = rules.review_position(cm(put_short_delta=0.35, call_short_delta=0.10))
    assert r["advice"] == "attention" and r["rule"] == 4 and "短 Put" in r["reason"]
    r = rules.review_position(cm(put_short_delta=0.10, call_short_delta=0.36))
    assert r["advice"] == "attention" and r["rule"] == 4 and "短 Call" in r["reason"]
    assert rules.review_position(cm(put_short_delta=0.34, call_short_delta=0.34))["rule"] == 5


def test_review_condor_earnings_attention_without_data_ok():
    r = rules.review_position(cm(earnings="yes", data_ok=False, put_short_delta=None,
                                 call_short_delta=None))
    assert r["advice"] == "attention" and r["rule"] == 4
    assert r["reason"].endswith(rules.STALE_NOTE)


def test_review_condor_stale_data_note_and_no_delta_rule():
    r = rules.review_position(cm(data_ok=False, put_short_delta=0.50, call_short_delta=0.50))
    assert r["advice"] == "hold" and r["rule"] == 5 and rules.STALE_NOTE in r["reason"]
    r = rules.review_position(cm(data_ok=False, spot=80.0))
    assert r["rule"] == 2 and r["reason"].endswith(rules.STALE_NOTE)


def test_review_condor_quiet_hold_variant():
    quiet = cm(days_left=5, put_short_delta=0.10, call_short_delta=0.08)
    r = rules.review_position(quiet)
    assert r["advice"] == "hold" and "等它自然到期归零" in r["reason"]
    for kw in ({"days_left": 6}, {"call_short_delta": 0.11}, {"data_ok": False}):
        assert "自然到期" not in rules.review_position({**quiet, **kw})["reason"]
    r = rules.review_position({**quiet, "earnings": "unknown"})
    assert r["reason"].endswith(rules.UNKNOWN_EARNINGS_NOTE)
    assert rules.review_position(cm(earnings="unknown"))["reason"] == "继续持有" + rules.UNKNOWN_EARNINGS_NOTE


def test_review_condor_missing_quote_and_expiry_rules():
    r = rules.review_position(cm(error="期权链里找不到这四条腿的有效报价"))
    assert r["advice"] == "attention" and r["rule"] == 0 and "取不到报价" in r["reason"]
    assert rules.review_position(cm(days_left=0))["advice"] == "await_settlement"
    assert rules.review_position(cm(days_left=-2))["rule"] == 1


def test_review_condor_th_override():
    assert rules.review_position(cm(captured=0.60), {"CLOSE_CAPTURED": 0.60})["rule"] == 3


# ---- bull call debit spread (bull call spec 5.13) -----------------------------------
# ZZZQ (invented, design section 10): long 100 Call / short 105 Call, width 5, debit 1.90,
# 1 contract -> max profit $310, max loss $190; take-profit at +$155, stop at -$95.

def bm(**kw):
    base = dict(strategy="bull_call_spread", days_left=35, spot=100.0, long_call_strike=100.0,
                short_call_strike=105.0, debit=1.90, width=5.0, value=1.90, pnl=0.0,
                captured=0.0, stop_flag=False, take_profit_flag=False, long_call_delta=0.55,
                delta=None, data_ok=True, earnings="no", ex_div=None, error=None)
    base.update(kw)
    return base


def test_review_bullcall_missing_quote_attention():
    r = rules.review_position(bm(error="期权链里找不到这两腿的有效报价", spot=None, pnl=None,
                                 captured=None))
    assert r["advice"] == "attention" and r["rule"] == 0 and "取不到报价" in r["reason"]


def test_review_bullcall_await_settlement():
    for days in (0, -3):
        r = rules.review_position(bm(days_left=days, spot=None, pnl=None, captured=None))
        assert r["advice"] == "await_settlement" and r["rule"] == 1


def test_review_bullcall_stop_flag_attention():
    # long 1.50 / short 0.60 -> value 0.90 -> (0.90 - 1.90) x 100 = -$100 <= -$95
    stopped = bm(value=0.90, pnl=-100.0, captured=-100 / 310, stop_flag=True)
    r = rules.review_position(stopped)
    assert r["advice"] == "attention" and r["rule"] == 2 and "止损" in r["reason"]
    # the stop beats a pending earnings date
    assert rules.review_position({**stopped, "earnings": "yes"})["rule"] == 2


def test_review_bullcall_take_profit_close():
    # long 4.50 / short 1.00 -> value 3.50 -> +$160 >= +$155; 160 / 310 = 51.6% -> "52%"
    tp = bm(spot=103.0, value=3.50, pnl=160.0, captured=160 / 310, take_profit_flag=True)
    r = rules.review_position(tp)
    assert r["advice"] == "close" and r["rule"] == 3 and "52%" in r["reason"]
    assert rules.review_position({**tp, "earnings": "yes"})["rule"] == 3
    # just under the line (+$154.99) stays a hold
    assert rules.review_position(bm(spot=103.0, pnl=154.99, captured=154.99 / 310))["rule"] == 5


def test_review_bullcall_touching_short_strike_close():
    # spot exactly at the short strike 105 closes even with only +$60 marked (value 2.50)
    r = rules.review_position(bm(spot=105.0, value=2.50, pnl=60.0, captured=60 / 310))
    assert r["advice"] == "close" and r["rule"] == 3 and "105" in r["reason"]
    assert rules.review_position(bm(spot=104.99, pnl=60.0, captured=60 / 310))["rule"] == 5


def test_review_bullcall_earnings_attention():
    r = rules.review_position(bm(earnings="yes"))
    assert r["advice"] == "attention" and r["rule"] == 4 and "财报" in r["reason"]


def test_review_bullcall_time_stop_attention():
    # 10 days left and no profit (pnl exactly 0) -> time stop; 11 days or +$1 -> hold
    r = rules.review_position(bm(days_left=10, pnl=0.0))
    assert r["advice"] == "attention" and r["rule"] == 4 and "10" in r["reason"]
    assert rules.review_position(bm(days_left=10, pnl=-50.0))["rule"] == 4
    assert rules.review_position(bm(days_left=11, pnl=0.0))["rule"] == 5
    assert rules.review_position(bm(days_left=10, pnl=1.0, captured=1 / 310))["rule"] == 5


def test_review_bullcall_between_strikes_near_expiry_attention():
    # 5 days left, spot 102 between 100 and 105, marked +$30 (so the time stop is not it)
    near = bm(days_left=5, spot=102.0, value=2.20, pnl=30.0, captured=30 / 310)
    r = rules.review_position(near)
    assert r["advice"] == "attention" and r["rule"] == 4 and "两腿之间" in r["reason"]
    assert rules.review_position({**near, "days_left": 6})["rule"] == 5
    # exactly at the long strike is not between the legs
    assert rules.review_position({**near, "spot": 100.0})["rule"] == 5


def test_review_bullcall_short_itm_ex_div_attention_strict():
    r = rules.review_position(bm(spot=105.01, value=4.90, pnl=300.0, captured=300 / 310,
                                 take_profit_flag=True, ex_div="2026-10-20"))
    assert r["advice"] == "attention" and r["rule"] == 4 and "2026-10-20" in r["reason"]
    # spot == short strike is not in the money: the touching-short close applies
    r = rules.review_position(bm(spot=105.0, pnl=60.0, captured=60 / 310, ex_div="2026-10-20"))
    assert r["advice"] == "close" and r["rule"] == 3
    # in the money without an ex-dividend date: plain close
    assert rules.review_position(bm(spot=105.01, pnl=60.0, captured=60 / 310))["rule"] == 3


def test_review_bullcall_hold_with_stale_and_unknown_notes():
    r = rules.review_position(bm(data_ok=False, earnings="unknown"))
    assert r["advice"] == "hold" and r["rule"] == 5
    assert r["reason"] == "继续持有；" + rules.STALE_NOTE + rules.UNKNOWN_EARNINGS_NOTE
    assert rules.review_position(bm())["reason"] == "继续持有"


def test_review_bullcall_never_uses_delta_rule():
    r = rules.review_position(bm(delta=0.9, long_call_delta=0.9))
    assert r["advice"] == "hold" and r["rule"] == 5 and "Delta" not in r["reason"]
    # no "quiet delta" variant either, even near expiry with a tiny delta and a profit
    quiet = bm(days_left=3, spot=99.0, delta=0.05, long_call_delta=0.05, pnl=10.0)
    assert "自然到期" not in rules.review_position(quiet)["reason"]
