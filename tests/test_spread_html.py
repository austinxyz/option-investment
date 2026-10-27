"""Tests for scripts/spread_html.py — the friendly HTML view of the paper-trading ledger."""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_html as sh
import spread_report as rep  # noqa: E402


def _s(id_, ticker="ABC", status="open", pnl=None, bucket=0.15, expiry="2026-10-23", short=100.0, long_=95.0,
       credit=1.0, contracts=1, under=110.0, earnings=False, gap=None, result=None, **extra):
    base = {
        "id": id_, "ticker": ticker, "status": status, "contracts": contracts, "short_strike": short,
        "long_strike": long_, "width": short - long_, "credit": credit, "expiry": expiry,
        "snapshot_date": "2026-10-16", "underlying_price": under, "delta_bucket": bucket,
        "earnings_in_window": earnings, "monday_gap_pct": gap, "pnl": pnl,
        "result": result or (None if pnl is None else ("win" if pnl > 0 else "loss")),
    }
    base.update(extra)
    return base


def _mark(pnl, dist=8.0, under=108.0, stop=False, tp=False, stale=False):
    return {"pnl": pnl, "dist_pct": dist, "underlying": under, "cost": 1.0, "stale": stale,
            "flags": {"stop": stop, "take_profit": tp}}


def test_empty_ledger_renders_a_complete_page():
    page = sh.render_report_html([], None, "2026-10-16")
    assert page.startswith("<!doctype html>") and "垂直 Put 贷方价差与现金担保 Put 纸上交易" in page
    assert "还没有未平仓" in page and "还没有已结算" in page


def test_open_trade_card_shows_strikes_expiry_and_capital():
    page = sh.render_report_html([_s(1, "TSLA", short=325, long_=320, credit=0.53)], None, "2026-10-16")
    assert "TSLA" in page and "卖 325 / 买 320 Put" in page and "2026-10-23" in page
    assert "占用资金" in page and "$447" in page          # (5 - 0.53) * 100


def test_open_trade_without_a_mark_says_to_run_status():
    assert "运行 status 查看" in sh.render_report_html([_s(1)], None, "2026-10-16")


def test_mark_shows_pnl_distance_and_current_price():
    page = sh.render_report_html([_s(1)], {1: _mark(-22.0, dist=8.4, under=354.9)}, "2026-10-16")
    assert "-$22" in page and "8.4%" in page and "354.90" in page


def test_stop_and_take_profit_banners():
    stop = sh.render_report_html([_s(1)], {1: _mark(-250.0, stop=True)}, "2026-10-16")
    take = sh.render_report_html([_s(1)], {1: _mark(60.0, tp=True)}, "2026-10-16")
    assert "已到止损线" in stop and "已到止盈线" not in stop
    assert "已到止盈线" in take and "已到止损线" not in take
    assert "只是提示" in stop


def test_stale_quotes_are_flagged():
    assert "报价陈旧" in sh.render_report_html([_s(1)], {1: _mark(-10.0, stale=True)}, "2026-10-16")


def test_mark_error_is_shown_and_escaped():
    page = sh.render_report_html([_s(1)], {1: {"error": "期权链 <b>缺</b> 两腿"}}, "2026-10-16")
    assert "期权链 &lt;b&gt;缺&lt;/b&gt; 两腿" in page and "<b>缺</b>" not in page


def test_each_open_trade_gets_a_payoff_chart_with_its_levels():
    page = sh.render_report_html([_s(1, short=100, long_=95, credit=1.0), _s(2, "XYZ", short=50, long_=45)],
                                 {1: _mark(-5.0, under=98.0)}, "2026-10-16")
    assert page.count("<svg") == 2
    assert "短腿 100" in page and "长腿 95" in page and "平衡 99.0" in page and "现价 98" in page


def test_progress_bar_marks_the_stop_and_take_profit_levels():
    page = sh.render_report_html([_s(1)], {1: _mark(-50.0)}, "2026-10-16")
    assert "止损线" in page and "止盈线" in page and 'class="bar"' in page


def test_contracts_scale_capital_and_chart_amounts():
    page = sh.render_report_html([_s(1, credit=1.0, contracts=3)], None, "2026-10-16")
    assert "$1,200" in page            # (5 - 1) * 100 * 3


def test_summary_totals_capital_and_unrealized_pnl():
    spreads = [_s(1, credit=1.0), _s(2, "XYZ", short=50, long_=45, credit=0.5)]
    page = sh.render_report_html(spreads, {1: _mark(-100.0), 2: _mark(30.0)}, "2026-10-16")
    assert "$850" in page              # 400 + 450 capital
    assert "-$70" in page              # -100 + 30


def test_earnings_trades_are_tagged():
    assert "带财报" in sh.render_report_html([_s(1, earnings=True)], None, "2026-10-16")
    assert "带财报" not in sh.render_report_html([_s(1, earnings=False)], None, "2026-10-16")


def test_settled_table_and_stats():
    spreads = [_s(1, status="expired", pnl=100.0), _s(2, "XYZ", status="expired", pnl=-300.0, expiry="2026-10-30"),
               _s(3, "QQQ", status="closed_early", pnl=60.0)]
    page = sh.render_report_html(spreads, None, "2026-10-31")
    assert "+$100" in page and "-$300" in page and "+$60" in page
    assert "提前平仓" in page and "到期结算" in page
    assert "67%" in page                          # 2 of 3 win
    assert "盈亏平衡胜率" in page and "样本不足" in page


def test_stats_say_when_there_is_nothing_to_compare_yet():
    assert "暂无" in sh.render_report_html([_s(1)], None, "2026-10-16")


def test_page_is_self_contained_and_themed():
    page = sh.render_report_html([_s(1)], None, "2026-10-16")
    assert "prefers-color-scheme: dark" in page and "http://" not in page.replace("http://www.w3.org", "")
    assert "<script src" not in page and "<link " not in page


def test_footer_states_the_assumptions():
    page = sh.render_report_html([], None, "2026-10-16")
    assert "每张合约 ×100" in page and "不含手续费" in page and "非投资建议" in page


def _csp_rec(id_=7, status="open", pnl=None, **extra):
    base = {"id": id_, "ticker": "CSPX", "status": status, "contracts": 1, "strategy": "cash_secured_put",
            "short_strike": 100.0, "long_strike": None, "width": None, "credit": 1.5, "expiry": "2026-10-23",
            "snapshot_date": "2026-10-16", "underlying_price": 110.0, "monday_gap_pct": None, "pnl": pnl,
            "result": None if pnl is None else ("win" if pnl > 0 else "assigned"),
            "earnings_in_window": False}
    base.update(extra)
    return base


def test_html_renders_open_csp_without_chart():
    page = sh.render_report_html([_csp_rec()], None, "2026-10-16")
    assert "CSPX" in page and "100 Put" in page
    assert "<svg" not in page


def test_html_csp_mixed_with_spread_and_settled_csp():
    recs = [_s(1), _csp_rec(7), _csp_rec(8, status="expired", pnl=-300.0, expiry="2026-10-16")]
    page = sh.render_report_html(recs, {7: _mark(30.0)}, "2026-10-16")
    assert page.count("<svg") == 1                      # only the spread has a payoff chart
    assert "100 Put" in page and "-$300" in page
    assert "占用资金合计" in page


def test_legacy_only_html_has_no_extra_blank_line():
    page = sh.render_report_html([_s(1), _s(2, status="expired", pnl=100.0)], None, "2026-10-16")
    assert "\n\n" not in page  # byte-identical to the pre-Sell-Put output (checked vs 9489e75)


def test_csp_open_table_states_threshold_basis():
    mark = _mark(30.0)
    mark["flags"] = {"stop": True, "take_profit": True}
    page = sh.render_report_html([_csp_rec()], {7: mark}, "2026-10-16")
    assert "浮亏≥权利金的2倍" in page and "权利金的50%" in page
    assert "最大亏损的50%" not in page.split("现金担保 Put")[1]


# --- covered-call html (task 7) -------------------------------------------------

def _cc_html(id_=9, status="open", pnl=None, **extra):
    base = _csp_rec(id_, status=status, pnl=pnl, strategy="covered_call", ticker="CCX",
                    short_strike=110.0, cost_basis=90.0, contracts=2)
    base.update(extra)
    return base


def test_html_renders_covered_call_table_and_not_under_sell_put():
    page = sh.render_report_html([_csp_rec(), _cc_html()], {9: _mark(40.0)}, "2026-10-16")
    assert "<h3>备兑 Call（Covered Call）</h3>" in page and "110 Call" in page
    sell_put = page.split("现金担保 Put（Cash-Secured Put）")[1].split("<h3>备兑 Call（Covered Call）</h3>")[0]
    assert "CCX" not in sell_put and "CSPX" in sell_put


def test_html_strategy_table_includes_covered_call():
    recs = [_cc_html(9, status="expired", pnl=150.0, result="win", expiry="2026-10-16")]
    page = sh.render_report_html(recs, None, "2026-10-16")
    assert "covered_call" in page and "110 Call" in page and "+$150" in page


def test_html_called_away_on_real_lot_marked_holding_unchanged():
    real = {"lot_ref": {"source": "real", "lot_id": None, "ticker": "CCX"}}
    recs = [_cc_html(9, status="expired", pnl=150.0, result="called_away", expiry="2026-10-16", **real)]
    assert "called_away（真实持仓未变）" in sh.render_report_html(recs, None, "2026-10-16")
    paper = [_cc_html(9, status="expired", pnl=150.0, result="called_away", expiry="2026-10-16",
                      lot_ref={"source": "manual", "lot_id": 1, "ticker": "CCX"})]
    assert "真实持仓未变" not in sh.render_report_html(paper, None, "2026-10-16")


# ---- collar / protective put html (task 5) -------------------------------------

def _collar(id_, status="open", pnl=None, result=None, **extra):
    base = {"id": id_, "ticker": "ZZZQ", "status": status, "contracts": 7, "strategy": "collar",
            "put_strike": 71.5, "put_debit": 1.2, "call_strike": 93.25, "call_credit": 1.8,
            "net_credit": 0.6, "covered_shares": 777, "cost_basis": 83.37,
            "reference_price": 83.37, "expiry": "2026-11-20", "snapshot_date": "2026-10-20",
            "pnl": pnl, "result": result, "stock_leg_pnl": None if status == "open" else 1234.0}
    base.update(extra)
    return base


def test_html_renders_collar_without_keyerror():
    recs = [_collar(1), _collar(2, status="expired", pnl=60.0, result="called_away"),
            _collar(3, strategy="protective_put", call_strike=None, call_credit=0,
                    net_credit=-0.5, put_debit=0.5)]
    page = sh.render_report_html(recs, {1: {"pnl": -35.0, "stale": False}}, "2026-10-21")
    assert "保护性 Put 与 Collar" in page and "71.5" in page and "93.25" in page
    assert "锁定区间" in page and "不封顶" in page and "called_away" in page
    assert "-$35" in page and "机会成本" in page


def test_html_collar_not_in_csp_table():
    page = sh.render_report_html([_collar(1)], None, "2026-10-21")
    assert "现金担保 Put（Cash-Secured Put）" not in page
    assert "备兑 Call（Covered Call）" not in page
    assert "还没有未平仓" not in page


def test_html_collar_not_in_spread_settled_table_or_strategy_stats():
    page = sh.render_report_html([_s(1, status="expired", pnl=100.0),
                                  _collar(2, status="expired", pnl=60.0, result="called_away")],
                                 None, "2026-10-21")
    assert "按策略对比" not in page
    assert page.count("called_away") == 1


def test_html_unknown_strategy_does_not_crash():
    odd = {"id": 9, "ticker": "ZZZU", "strategy": "future_thing", "status": "open",
           "contracts": 1, "expiry": "2026-11-20"}
    page = sh.render_report_html([odd], None, "2026-10-21")
    assert page.startswith("<!doctype html>")


def test_html_kpi_capital_includes_collar_net_debit_and_footer_defines_it():
    page = sh.render_report_html([_s(1), _collar(2, net_credit=-0.5)], None, "2026-10-21")
    assert "$750" in page.split("占用资金合计")[1].split("</b>")[0]
    assert "净支出" in page.split('class="foot"')[1] and "成本 × 股数" in page


def test_html_collar_called_away_on_real_lot_noted():
    real = {"lot_ref": {"source": "real"}}
    page = sh.render_report_html([_collar(1, status="expired", pnl=60.0, result="called_away", **real)],
                                 None, "2026-10-21")
    assert "called_away（真实持仓未变）" in page
    paper = {"lot_ref": {"source": "manual", "lot_id": 1}}
    page = sh.render_report_html([_collar(1, status="expired", pnl=60.0, result="called_away", **paper)],
                                 None, "2026-10-21")
    assert "真实持仓未变" not in page


def test_html_kpi_floating_pnl_excludes_collar_marks():
    recs = [_s(1), _collar(2)]
    marks = {1: {"pnl": 10.0, "underlying": 100.0, "dist_pct": 5.0, "stale": False,
                 "flags": {"stop": False, "take_profit": False}},
             2: {"pnl": -500.0, "stale": False}}
    page = sh.render_report_html(recs, marks, "2026-10-21")
    kpi = page.split("浮盈亏合计")[1].split("</b>")[0]
    assert "10" in kpi and "490" not in kpi and "500" not in kpi


# ---- iron condor html (task 5) --------------------------------------------------
import hashlib


def _condor(id_, status="open", pnl=None, result=None, breached="none", **extra):
    """Full iron-condor record: 85/90 Put, 110/115 Call, width 5, net 1.50 -> max loss $350."""
    base = {"id": id_, "ticker": "ZZZQ", "strategy": "iron_condor", "status": status,
            "put_short_strike": 90.0, "put_long_strike": 85.0,
            "call_short_strike": 110.0, "call_long_strike": 115.0, "width": 5.0,
            "put_credit": 0.8, "call_credit": 0.7, "net_credit": 1.5,
            "breakeven_low": 88.5, "breakeven_high": 111.5, "delta_bucket": 0.15,
            "put_short_delta": 0.15, "call_short_delta": 0.15,
            "put_short_iv": 0.3, "call_short_iv": 0.28, "underlying_price": 100.0,
            "contracts": 1, "expiry": "2026-11-20", "snapshot_date": "2026-10-20",
            "suggestion_file": "2026-10-20-ZZZQ-condor.json", "earnings_in_window": False}
    if status != "open":
        base.update(result=result, pnl=pnl, exit_cost=round(1.5 - pnl / 100, 4),
                    exit_underlying=100.0, exit_date=base["expiry"])
        if status == "expired":
            base["breached_side"] = breached
    base.update(extra)
    return base


def _old_html_ledger():
    return [_s(1), _s(2, status="expired", pnl=100.0, result="win"), _csp_rec(7),
            _csp_rec(8, status="expired", pnl=-300.0, expiry="2026-10-16"), _cc_html(9),
            _collar(10), _collar(11, status="expired", pnl=60.0, result="called_away")]


OLD_HTML_MARKS = {1: _mark(-22.0), 7: _mark(30.0), 9: _mark(40.0), 10: {"pnl": -35.0, "stale": False}}
PIN_HTML = "5faf1b3289a7f5c4572bc5cbd084cb44392d7e7bf1279a295e22a0450005f3a8"


def test_existing_html_output_unchanged_for_old_strategies():
    # Hash captured from the pre-condor code (commit 4ab8539).
    page = sh.render_report_html(_old_html_ledger(), OLD_HTML_MARKS, "2026-10-21")
    assert hashlib.sha256(page.encode("utf-8")).hexdigest() == PIN_HTML


def _condor_mark(pnl=90.0):
    return {"pnl": pnl, "cost": 0.6, "underlying": 100.0, "dist_pct": 10.0, "stale": False,
            "flags": {"stop": False, "take_profit": False}}


def test_html_renders_condor_without_keyerror():
    recs = [_condor(1), _condor(2), _condor(3),
            _condor(4, status="expired", pnl=150.0, result="win"),
            _condor(5, status="expired", pnl=-50.0, result="loss", breached="put"),
            _condor(6, status="closed_early", pnl=60.0, result="win")]
    marks = {1: _condor_mark(), 3: {"error": "期权链里找不到这四条腿的有效报价"}}
    page = sh.render_report_html(recs, marks, "2026-10-21")
    assert "铁鹰（Iron Condor）" in page and "85/90/110/115" in page and "88.50 / 111.50" in page
    assert "$350" in page and "70%" in page and "$1.50" in page    # max loss, (5-1.5)/5
    assert "+$90" in page and "运行 status 查看" in page and "四条腿" in page
    assert "+$150" in page and "-$50" in page and "Put 侧" in page and "未突破" in page
    assert "<svg" not in page                                       # no single-spread chart
    assert "iron_condor" in page and "理论盈亏平衡胜率" in page


def test_html_kpi_matches_list_for_condor():
    page = sh.render_report_html([_condor(1)], {1: _condor_mark()}, "2026-10-21")
    assert "<span>未平仓</span><b>1 笔</b>" in page and "还没有未平仓" not in page
    assert "<span>占用资金合计</span><b>$350</b>" in page
    assert "+$90" in page.split("浮盈亏合计")[1].split("</b>")[0]
    odd = {"id": 9, "ticker": "ZZZU", "strategy": "future_thing", "status": "open",
           "contracts": 1, "expiry": "2026-11-20"}
    both = sh.render_report_html([_condor(1), odd], {1: _condor_mark()}, "2026-10-21")
    assert "<span>未平仓</span><b>1 笔</b>" in both and "ZZZU" not in both
    alone = sh.render_report_html([odd], None, "2026-10-21")
    assert "<span>未平仓</span><b>0 笔</b>" in alone and "还没有未平仓" in alone


def test_html_only_condor_settled_has_no_empty_placeholder_or_zero_kpi():
    recs = [_condor(1, status="expired", pnl=150.0, result="win"),
            _condor(2, status="expired", pnl=-50.0, result="loss", breached="put")]
    page = sh.render_report_html(recs, None, "2026-10-21")
    assert "还没有已结算的。" not in page and "铁鹰（Iron Condor，已结算）" in page
    assert "<span>已结算</span><b>0 笔</b>" not in page
    assert "<span>铁鹰已结算</span><b>2 笔</b>" in page     # +150 - 50 = +$100
    assert "<span>铁鹰已实现盈亏</span><b>+$100</b>" in page
    assert "<span>已结算（Put 贷方价差）</span><b>0 笔</b>" in page
    empty = sh.render_report_html([], None, "2026-10-21")
    assert "还没有已结算的。" in empty and "铁鹰已结算" not in empty


def test_html_condor_tables_show_pnl_pct_like_report_md():
    # max loss $350: open mark +90 -> +25.7%; settled +150 -> +42.9%, -50 -> -14.3%
    recs = [_condor(1), _condor(2), _condor(4, status="expired", pnl=150.0, result="win"),
            _condor(5, status="expired", pnl=-50.0, result="loss", breached="put")]
    page = sh.render_report_html(recs, {1: _condor_mark()}, "2026-10-21")
    open_tbl = page.split("<h3>铁鹰（Iron Condor）</h3>")[1].split("</table>")[0]
    settled_tbl = page.split("<h3>铁鹰（Iron Condor，已结算）</h3>")[1].split("</table>")[0]
    assert "<th>盈亏%</th>" in open_tbl and "+25.7%" in open_tbl
    assert open_tbl.count("<td>—</td>") >= 1                      # #2 has no mark
    assert "<th>盈亏%</th>" in settled_tbl
    assert "+42.9%" in settled_tbl and "-14.3%" in settled_tbl
    md = rep.render_report(recs, {1: _condor_mark()}, "2026-10-21")
    for cell in ("+25.7%", "+42.9%", "-14.3%"):
        assert cell in md
    # KPI and list stay consistent
    assert "<span>未平仓</span><b>2 笔</b>" in page and "<span>铁鹰已结算</span><b>2 笔</b>" in page


# ---- bull call debit spread (task 5) -------------------------------------------

def _bullcall(id_, status="open", pnl=None, result=None, **extra):
    """Design section 10: 100/105 Call, debit 1.90, breakeven 101.90, max $310 / -$190."""
    base = {"id": id_, "ticker": "ZQBC", "strategy": "bull_call_spread", "status": status,
            "long_call_strike": 100.0, "short_call_strike": 105.0, "width": 5.0,
            "long_call_mid": 3.2, "short_call_mid": 1.3, "debit": 1.9, "breakeven": 101.9,
            "breakeven_move_pct": 0.019, "max_profit_move_pct": 0.05, "delta_bucket": 0.5,
            "long_call_delta": 0.52, "short_call_delta": 0.31, "underlying_price": 100.0,
            "contracts": 1, "expiry": "2026-11-27", "snapshot_date": "2026-10-20",
            "earnings_in_window": False, "pnl": None, "result": None,
            "fraction_of_max_captured": None}
    if status != "open":
        base.update(pnl=pnl, result=result or ("win" if pnl > 0 else "loss"),
                    exit_value=round(1.9 + pnl / 100, 4), exit_date=base["expiry"],
                    fraction_of_max_captured=round(pnl / 310, 4))
    base.update(extra)
    return base


def _bc_mark(pnl=50.0, tp=False):
    return {"value": 2.4, "pnl": pnl, "underlying": 102.4, "dist_pct": 2.3, "stale": False,
            "flags": {"stop": False, "take_profit": tp}, "long_mid": 4.0, "short_mid": 1.6,
            "long_iv": None, "short_iv": None}


def _bc_open_table(page: str) -> str:
    return page.split("<h3>Call 借方价差（Bull Call Debit Spread）</h3>")[1].split("</table>")[0]


def test_html_renders_bullcall_without_keyerror():
    recs = [_bullcall(1), _bullcall(2), _bullcall(3),
            _bullcall(4, status="expired", pnl=110.0), _bullcall(5, status="expired", pnl=-190.0),
            _bullcall(6, status="closed_early", pnl=50.0), _bullcall(7, status="expired", pnl=0.0)]
    marks = {1: _bc_mark(155.0, tp=True), 3: {"error": "期权链里找不到这两腿的有效报价"}}
    page = sh.render_report_html(recs, marks, "2026-10-21")
    assert "100/105 Call" in page and "$1.90" in page and "101.90" in page
    assert "+$310 / -$190" in page and "38%" in page
    assert "+$155" in page and "最大收益的50%" in page               # bull call tip text
    assert "权利金的50%" not in _bc_open_table(page)
    assert "运行 status 查看" in page and "两腿" in page
    settled = page.split("<h3>Call 借方价差（Bull Call Debit Spread，已结算）</h3>")[1].split("</table>")[0]
    assert "+$110" in settled and "-$190" in settled and "+35.5%" in settled and "-61.3%" in settled
    assert "提前平仓" in settled and "-$0" not in settled and "-0.0%" not in settled
    assert "<svg" not in page                                       # no bull put payoff card
    assert "bull_call_spread" in page and "所需胜率" in page and "口径不同" in page
    assert "Call 借方价差=" in page.split('<div class="foot">')[1]


def test_html_open_kpi_equals_rendered_rows():
    recs = [_bullcall(1), _bullcall(2), _s(3)]
    page = sh.render_report_html(recs, {1: _bc_mark()}, "2026-10-21")
    rows = _bc_open_table(page).count("<tr>") - 1                   # minus header row
    assert rows == 2 and "<span>未平仓</span><b>3 笔</b>" in page    # 2 bull calls + 1 bull put
    # (5 - 1) x 100 = $400 bull put + 2 x $190
    assert "<span>占用资金合计</span><b>$780</b>" in page
    alone = sh.render_report_html([_bullcall(1)], None, "2026-10-21")
    assert "<span>未平仓</span><b>1 笔</b>" in alone and "还没有未平仓" not in alone


def test_html_settled_kpis_include_bullcall_pair():
    recs = [_bullcall(1, status="expired", pnl=110.0), _bullcall(2, status="expired", pnl=-190.0)]
    page = sh.render_report_html(recs, None, "2026-10-21")
    assert "还没有已结算的。" not in page
    assert "<span>已结算（Put 贷方价差）</span><b>0 笔</b>" in page
    assert "<span>Call 借方价差已结算</span><b>2 笔</b>" in page     # 110 - 190 = -$80
    assert "<span>Call 借方价差已实现盈亏</span><b>-$80</b>" in page
    empty = sh.render_report_html([], None, "2026-10-21")
    assert "Call 借方价差已结算" not in empty
