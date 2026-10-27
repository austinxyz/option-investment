"""Tests for scripts/spread_report.py — pure stats and rendering."""
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import option_source_default
import spread_report as rep
from option_source_default import PUBLIC_SOURCE_LABEL as LABEL  # edition-driven front matter


def _s(id_, status="expired", pnl=100.0, bucket=0.10, expiry="2026-10-23",
       ticker="ABC", gap=None, result=None, **extra):
    base = {
        "id": id_, "ticker": ticker, "status": status, "contracts": 1,
        "short_strike": 95.0, "long_strike": 90.0, "expiry": expiry,
        "delta_bucket": bucket, "pnl": pnl if status != "open" else None,
        "result": result or ("win" if pnl > 0 else "loss"), "monday_gap_pct": gap,
    }
    base.update(extra)
    return base


def test_stats_empty_ledger():
    st = rep.compute_stats([])
    assert st["n_settled"] == 0 and st["win_rate"] is None
    assert st["passes"] is None and st["enough_sample"] is False
    assert st["total_pnl"] == 0


def test_stats_win_rate_and_breakeven_from_actual_payoffs():
    spreads = [_s(1, pnl=100), _s(2, pnl=100), _s(3, pnl=100), _s(4, pnl=-300)]
    st = rep.compute_stats(spreads)
    assert st["n_settled"] == 4 and st["wins"] == 3
    assert st["win_rate"] == pytest.approx(0.75)
    assert st["avg_win"] == pytest.approx(100) and st["avg_loss"] == pytest.approx(300)
    assert st["breakeven_win_rate"] == pytest.approx(0.75)  # 300 / (100 + 300)
    assert st["passes"] is False                            # 0.75 is not > 0.75
    assert st["total_pnl"] == 0


def test_stats_passes_when_win_rate_exceeds_breakeven():
    spreads = [_s(i, pnl=100) for i in range(1, 6)] + [_s(6, pnl=-300)]
    assert rep.compute_stats(spreads)["passes"] is True


def test_stats_flat_trade_is_not_a_win_and_not_a_loss():
    st = rep.compute_stats([_s(1, pnl=0.0), _s(2, pnl=100)])
    assert st["wins"] == 1 and st["win_rate"] == pytest.approx(0.5)
    assert st["avg_loss"] is None and st["passes"] is None


def test_stats_open_trades_are_not_counted_as_settled():
    st = rep.compute_stats([_s(1, status="open"), _s(2, pnl=100)])
    assert st["n_settled"] == 1 and st["n_open"] == 1


def test_stats_closed_early_counts_as_settled():
    assert rep.compute_stats([_s(1, status="closed_early", pnl=-50)])["n_settled"] == 1


def test_stats_by_bucket_and_expiry():
    spreads = [_s(1, bucket=0.10, pnl=100), _s(2, bucket=0.10, pnl=-300),
               _s(3, bucket=0.20, pnl=100, expiry="2026-10-30")]
    st = rep.compute_stats(spreads)
    assert st["by_bucket"][0.10] == {"n": 2, "wins": 1, "win_rate": 0.5, "total_pnl": -200.0}
    assert st["by_bucket"][0.20]["n"] == 1
    assert st["by_expiry"]["2026-10-30"]["total_pnl"] == 100.0


def test_stats_sample_threshold():
    assert rep.compute_stats([_s(i) for i in range(1, 30)])["enough_sample"] is False
    assert rep.compute_stats([_s(i, ticker=f"T{i}") for i in range(1, 31)])["enough_sample"] is True


def test_stats_average_absolute_monday_gap_ignores_missing():
    st = rep.compute_stats([_s(1, gap=2.0), _s(2, gap=-4.0), _s(3, gap=None)])
    assert st["avg_abs_gap"] == pytest.approx(3.0)


def test_render_empty_report_has_every_section():
    text = rep.render_report([], None, "2026-10-16")
    for heading in ("## 未平仓", "## 已结算", "## 累积统计", "## 分档对比", "## 开仓价假设的偏差"):
        assert heading in text
    assert "还没有已结算" in text


def test_render_shows_marks_flags_and_sample_warning():
    spreads = [_s(1, status="open"), _s(2, pnl=100)]
    marks = {1: {"pnl": -250.0, "dist_pct": 2.1, "stale": False,
                 "flags": {"stop": True, "take_profit": False}}}
    text = rep.render_report(spreads, marks, "2026-10-16")
    assert "-$250" in text and "2.1%" in text and "止损线" in text
    assert "样本不足" in text
    assert "+$100" in text


def test_render_open_trade_without_marks_says_run_status():
    text = rep.render_report([_s(1, status="open")], None, "2026-10-16")
    assert "运行 status 查看" in text


def test_render_open_trade_with_mark_error_shows_the_error():
    text = rep.render_report([_s(1, status="open")], {1: {"error": "期权链里找不到这两腿"}}, "2026-10-16")
    assert "期权链里找不到这两腿" in text


def test_format_suggestions_table_and_notes():
    cands = [
        {"bucket": 0.10, "available": True, "short_strike": 93.0, "long_strike": 88.0, "credit": 0.30,
         "max_profit": 30.0, "max_loss": 470.0, "breakeven": 92.7, "credit_ratio": 0.06,
         "breakeven_win_rate": 0.94, "short_delta": 0.09, "em_multiple": 1.3, "warnings": ["报价陈旧"]},
        {"bucket": 0.15, "available": False, "reason": "找不到合适的长腿行权价"},
    ]
    text = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "yes", 5.0, cands)
    assert "ABC" in text and "到期前有财报" in text
    assert "93/88" in text and "+$30/-$470" in text
    assert "无合适合约" in text and "报价陈旧" in text and "找不到合适的长腿" in text


def _one_candidate():
    return [{"bucket": 0.10, "available": True, "short_strike": 93.0, "long_strike": 88.0, "credit": 0.30,
             "max_profit": 30.0, "max_loss": 470.0, "breakeven": 92.7, "credit_ratio": 0.06,
             "breakeven_win_rate": 0.94, "short_delta": 0.09, "em_multiple": 1.3, "warnings": []}]


def test_format_suggestions_header_says_close_by_default_and_intraday_when_flagged():
    close = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "no", 5.0, _one_candidate())
    live = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "no", 5.0, _one_candidate(),
                                  intraday=True)
    assert "2026-10-16 收盘 $100.00" in close.splitlines()[0]
    assert "2026-10-16 盘中 $100.00" in live.splitlines()[0] and "收盘" not in live.splitlines()[0]


def test_format_suggestions_shows_capital_needed_for_one_contract():
    text = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "no", 5.0, _one_candidate())
    assert "| 占用资金/张 |" in text and "| $470 |" in text


def test_format_suggestions_header_names_the_width_source():
    auto = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "no", 10.0, _one_candidate(),
                                  width_info={"source": "auto"})
    manual = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "no", 5.0, _one_candidate(),
                                    width_info={"source": "manual", "recommended": 10.0})
    plain = rep.format_suggestions("ABC", "2026-10-16", 100.0, "2026-10-23", 7, "no", 5.0, _one_candidate())
    assert "宽度 $10（自动推荐）" in auto.splitlines()[0]
    assert "宽度 $5（你指定，推荐 $10）" in manual.splitlines()[0]
    assert "宽度 $5 ·" in plain.splitlines()[0]


# ---- strategies: spread vs cash-secured put -----------------------------------

def _csp(id_, status="expired", pnl=-300.0, result="assigned", ticker="CSPX", **extra):
    base = {"id": id_, "ticker": ticker, "status": status, "contracts": 1,
            "strategy": "cash_secured_put", "short_strike": 100.0, "long_strike": None,
            "width": None, "credit": 1.5, "expiry": "2026-10-23",
            "pnl": pnl if status != "open" else None,
            "result": result if status != "open" else None, "monday_gap_pct": None,
            "short_delta": 0.2, "delta_target": 0.2, "tier": "mid"}
    base.update(extra)
    return base


def _golden_ledger():
    return [_s(1, pnl=100.0, bucket=0.10, ticker="ABC", gap=0.5),
            _s(2, status="closed_early", pnl=-300.0, bucket=0.20, ticker="XYZ", gap=-1.25),
            _s(3, pnl=100.0, bucket=0.10, expiry="2026-10-30", ticker="DEF"),
            _s(4, status="open", bucket=0.15, expiry="2026-11-06", ticker="GHI")]


GOLDEN = ('# 垂直 Put 贷方价差与现金担保 Put 纸上交易报告\n\n*由 `/spread-sim` 自动生成于 2026-10-18，只读。纸上交易，不含真实持仓。*\n\n## 未平仓\n\n未平仓占用资金合计 $500；浮盈亏合计 -$20（-4.0%）\n\n| # | 标的 | 短/长腿 | 到期 | 张数 | 浮盈亏 | 占用资金 | 盈亏% | 距短腿 | 提示 |\n|---|------|---------|------|------|--------|----------|-------|--------|------|\n| 4 | GHI | 95/90 | 2026-11-06 | 1 | -$20 | $500 | -4.0% | 6.5% | 🟢 已到止盈线（权利金的50%）；报价陈旧 |\n\n## 已结算\n\n**到期 2026-10-30**\n\n| # | 标的 | 短/长腿 | 档位 | 结果 | 方式 | 盈亏 | 占用资金 | 盈亏% | 次日开盘跳空 |\n|---|------|---------|------|------|------|------|----------|-------|----------|\n| 3 | DEF | 95/90 | 0.10 | win | 到期结算 | +$100 | $500 | +20.0% | — |\n\n**到期 2026-10-23**\n\n| # | 标的 | 短/长腿 | 档位 | 结果 | 方式 | 盈亏 | 占用资金 | 盈亏% | 次日开盘跳空 |\n|---|------|---------|------|------|------|------|----------|-------|----------|\n| 1 | ABC | 95/90 | 0.10 | win | 到期结算 | +$100 | $500 | +20.0% | +0.50% |\n| 2 | XYZ | 95/90 | 0.20 | loss | 提前平仓 | -$300 | $500 | -60.0% | -1.25% |\n\n## 累积统计\n\n| 项目 | 数值 |\n|------|------|\n| 已结算笔数 | 3（未平仓 1）|\n| 胜率（盈亏>0 算赢）| 67% |\n| 总盈亏 | -$100 |\n| 平均盈利 | +$100 |\n| 平均亏损 | -$300 |\n| 按实际盈亏比算的盈亏平衡胜率 | 75% |\n| 是否过线 | ❌ 实际胜率低于盈亏平衡胜率 |\n\n> ⚠️ 样本不足（3 < 30），别下结论。\n\n## 分档对比\n\n| 档位（delta）| 笔数 | 胜率 | 总盈亏 |\n|--------------|------|------|--------|\n| 0.10 | 2 | 100% | +$200 |\n| 0.20 | 1 | 0% | -$300 |\n\n## 开仓价假设的偏差\n\n下一个交易日开盘相对快照价的平均绝对跳空：0.88%。真实世界无法在快照价成交，这个数字越大，模拟结果越乐观。\n\n---\n*口径：每张合约 ×100 的美元；中间价成交；不含手续费。占用资金：Put 贷方价差=最大亏损（宽度−权利金）×100×张数，现金担保 Put=行权价×100×张数（需备足的现金）；盈亏%=盈亏÷占用资金。*\n')


def test_report_without_csp_is_identical_to_legacy():
    marks = {4: {"pnl": -20.0, "dist_pct": 6.5, "flags": {"stop": False, "take_profit": True}, "stale": True}}
    assert rep.render_report(_golden_ledger(), marks, "2026-10-18") == GOLDEN


def test_report_with_csp_adds_strategy_section():
    out = rep.render_report([_s(1, pnl=100.0), _csp(2, pnl=-300.0)], None, "2026-10-18")
    assert "## 按策略对比" in out and "## 现金担保 Put 记录" in out
    assert "bull_put_spread" in out and "cash_secured_put" in out
    assert "100%" in out and "0%" in out
    assert "100 Put" in out and "assigned" in out
    assert "样本不足" in out.split("## 按策略对比")[1]


def test_report_legacy_sections_ignore_csp_records():
    legacy = rep.render_report([_s(1, pnl=100.0)], None, "2026-10-18")
    mixed = rep.render_report([_s(1, pnl=100.0), _csp(2, pnl=-300.0)], None, "2026-10-18")
    assert mixed.startswith(legacy.split("---\n*口径")[0])


def test_report_with_open_csp_row():
    out = rep.render_report([_csp(1, status="open")], None, "2026-10-18")
    assert "CSPX" in out and "100 Put" in out


def test_compute_stats_tolerates_missing_delta_bucket():
    st = rep.compute_stats([_s(1, pnl=100.0), _csp(2, pnl=-300.0)])
    assert st["n_settled"] == 2
    assert list(st["by_bucket"]) == [0.10]


def test_compute_strategy_stats_groups_by_strategy():
    out = rep.compute_strategy_stats([_s(1, pnl=100.0), _csp(2, pnl=-300.0), _csp(3, pnl=50.0, result="win")])
    assert set(out) == {"bull_put_spread", "cash_secured_put"}
    assert out["bull_put_spread"]["win_rate"] == 1.0
    assert out["cash_secured_put"]["n_settled"] == 2 and out["cash_secured_put"]["win_rate"] == 0.5


def test_legs_label():
    assert rep.legs_label({"short_strike": 150.0, "long_strike": 145.0}) == "150/145"
    assert rep.legs_label({"short_strike": 150.0, "long_strike": None}) == "150 Put"


def test_csp_rows_state_their_own_threshold_basis_and_spread_rows_keep_old_text():
    marks = {1: {"pnl": -250.0, "dist_pct": 2.1, "stale": False,
                 "flags": {"stop": True, "take_profit": True}},
             2: {"pnl": -250.0, "dist_pct": 2.1, "stale": False,
                 "flags": {"stop": True, "take_profit": True}}}
    csp = _s(2, status="open", strategy="cash_secured_put", long_strike=None)
    text = rep.render_report([_s(1, status="open"), csp], marks, "2026-10-16")
    assert "🔴 已到止损线（最大亏损的50%）" in text            # spread row, legacy wording
    assert "🔴 已到止损线（浮亏≥权利金的2倍）" in text
    assert "🟢 已到止盈线（权利金的50%）" in text


# ---- capital at risk and pnl percent ------------------------------------------

def _mk(pnl, dist=2.0):
    return {"pnl": pnl, "dist_pct": dist, "stale": False,
            "flags": {"stop": False, "take_profit": False}}


def test_capital_at_risk_spread_is_max_loss():
    s = _s(1, status="open", width=5.0, credit=0.525)
    assert rep.capital_at_risk(s) == pytest.approx(447.5)
    assert rep.capital_at_risk({**s, "contracts": 2}) == pytest.approx(895.0)


def test_capital_at_risk_csp_is_cash_secured():
    c = _csp(1, status="open", contracts=2, short_strike=100.0)
    assert rep.capital_at_risk(c) == pytest.approx(20000.0)


def test_open_table_has_capital_and_pnl_pct_columns():
    spread = _s(1, status="open", width=5.0, credit=0.525)
    text = rep.render_report([spread], {1: _mk(22.0)}, "2026-10-19")
    assert "| 浮盈亏 | 占用资金 | 盈亏% | 距短腿 | 提示 |" in text
    assert "| +$22 | $447 | +4.9% | 2.0% |" in text


def test_open_summary_line_spread_only():
    spread = _s(1, status="open", width=5.0, credit=0.525)
    text = rep.render_report([spread], {1: _mk(22.0)}, "2026-10-19")
    assert "未平仓占用资金合计 $447；浮盈亏合计 +$22（+4.9%）" in text
    assert text.index("未平仓占用资金合计") < text.index("| # | 标的")


def test_open_summary_notes_positions_without_mark():
    a = _s(1, status="open", width=5.0, credit=0.5)
    b = _s(2, status="open", width=5.0, credit=0.5)
    text = rep.render_report([a, b], {1: _mk(-45.0), 2: {"error": "x"}}, "2026-10-19")
    assert "未平仓占用资金合计 $900；浮盈亏合计 -$45（-10.0%）" in text
    assert "1 笔无报价" in text


def test_mark_error_pct_is_dash():
    spread = _s(1, status="open", width=5.0, credit=0.5)
    text = rep.render_report([spread], {1: {"error": "boom"}}, "2026-10-19")
    assert "| — | $450 | — | — | boom |" in text


def test_open_summary_splits_strategies():
    spread = _s(1, status="open", width=5.0, credit=0.5)
    csp = _csp(2, status="open", short_strike=100.0, contracts=1)
    text = rep.render_report([spread, csp], {1: _mk(45.0), 2: _mk(30.0)}, "2026-10-19")
    assert "未平仓占用资金合计 $10,450（Put 贷方价差 $450 / 现金担保 Put $10,000）；浮盈亏合计 +$75（+0.7%）" in text
    assert "| 浮盈亏 | 占用资金 | 盈亏% |" in text.split("## 现金担保 Put 记录")[1]


def test_no_open_keeps_placeholder_and_no_summary():
    text = rep.render_report([_s(1, pnl=100.0, width=5.0, credit=0.5)], None, "2026-10-19")
    assert "（无未平仓）" in text and "未平仓占用资金合计" not in text


def test_settled_tables_have_capital_and_pnl_pct():
    sp = _s(1, pnl=100.0, width=5.0, credit=0.5)
    csp = _csp(2, pnl=-300.0, short_strike=100.0)
    text = rep.render_report([sp, csp], None, "2026-10-19")
    assert "| 盈亏 | 占用资金 | 盈亏% | 次日开盘跳空 |" in text
    assert "| +$100 | $450 | +22.2% |" in text
    assert "| 盈亏 | 占用资金 | 盈亏% |" in text.split("## 现金担保 Put 记录")[1]
    assert "| -$300 | $10,000 | -3.0% |" in text


def test_footer_defines_capital_and_pnl_pct():
    text = rep.render_report([], None, "2026-10-19")
    footer = text.split("---\n")[-1]
    assert "占用资金" in footer and "盈亏%" in footer


# --- covered-call report helpers --------------------------------------------

def _cc_rec(**extra):
    base = {"strategy": "covered_call", "short_strike": 110.0, "contracts": 2,
            "cost_basis": 90.0, "premium": 1.0}
    base.update(extra)
    return base


def test_legs_label_call():
    assert rep.legs_label(_cc_rec()) == "110 Call"


def test_capital_at_risk_zero_for_covered_call():
    assert rep.capital_at_risk(_cc_rec()) == 0.0


def test_pnl_base_uses_cost_basis():
    assert rep.pnl_base(_cc_rec()) == 90.0 * 100 * 2
    csp = {"strategy": "cash_secured_put", "short_strike": 50.0, "contracts": 1}
    assert rep.pnl_base(csp) == rep.capital_at_risk(csp)
    assert rep._pnl_ratio(180.0, _cc_rec()) == pytest.approx(0.01)


# --- covered-call report sections (task 7) -------------------------------------

def _cc(id_, status="expired", pnl=150.0, result="win", ticker="CCX", **extra):
    base = {"id": id_, "ticker": ticker, "status": status, "contracts": 2,
            "strategy": "covered_call", "short_strike": 110.0, "long_strike": None,
            "width": None, "credit": 1.0, "cost_basis": 90.0, "expiry": "2026-10-23",
            "pnl": pnl if status != "open" else None,
            "result": result if status != "open" else None, "monday_gap_pct": None,
            "short_delta": 0.2, "delta_target": 0.2, "tier": "mid"}
    base.update(extra)
    return base


def test_report_has_covered_call_section_with_stock_leg_column():
    recs = [_cc(1, pnl=150.0, result="called_away", stock_leg_pnl=2000.0),
            _cc(2, pnl=80.0, ticker="CCY"),
            _cc(3, status="open", ticker="CCZ"), _csp(4, status="open", ticker="CSPY")]
    out = rep.render_report(recs, {3: _mk(40.0), 4: _mk(10.0)}, "2026-10-21")
    cc = out.split("## 备兑 Call（Covered Call）记录")[1]
    assert "含股票腿" in cc
    assert "+$2,150" in cc                       # 150 + 2000
    assert "110 Call" in cc and "CCZ" in cc and "+$40" in cc
    assert "| — |" in cc                          # no stock leg for the expired one
    assert "## 现金担保 Put 记录" in out
    assert "CCX" not in out.split("## 现金担保 Put 记录")[1].split("## 备兑 Call（Covered Call）记录")[0]
    assert "备兑 Call=不占用额外现金（股票已持有），盈亏%=期权腿盈亏÷(成本×股数)" in out


def test_report_open_summary_ignores_covered_call_capital():
    base = rep.render_report([_csp(1, status="open")], {1: _mk(30.0)}, "2026-10-21")
    withcc = rep.render_report([_csp(1, status="open"), _cc(2, status="open")],
                               {1: _mk(30.0)}, "2026-10-21")
    line = "未平仓占用资金合计 $10,000；"
    assert line in base and line in withcc


def test_strategy_comparison_lists_covered_call_with_option_leg_pnl():
    out = rep.render_report([_cc(1, pnl=150.0, result="called_away", stock_leg_pnl=2000.0)],
                            None, "2026-10-21")
    row = [ln for ln in out.split("## 按策略对比")[1].splitlines() if ln.startswith("| covered_call")][0]
    assert "+$150" in row and "2,150" not in row


def test_report_existing_sections_unchanged_for_puts_only():
    out = rep.render_report([_s(1, pnl=100.0), _csp(2, pnl=-300.0)], None, "2026-10-21")
    assert "Covered Call" not in out


def test_open_summary_pct_uses_cost_basis_for_covered_call():
    recs = [_csp(1, status="open"), _cc(2, status="open")]   # csp base 10000, cc base 90*100*2=18000
    out = rep.render_report(recs, {1: _mk(30.0), 2: _mk(60.0)}, "2026-10-21")
    assert "浮盈亏合计 +$90（+0.3%）" in out                      # 90 / 28000


def test_open_summary_only_covered_calls_pct_from_cost_basis():
    out = rep.render_report([_cc(2, status="open")], {2: _mk(180.0)}, "2026-10-21")
    assert "未平仓占用资金合计 $0；浮盈亏合计 +$180（+1.0%）" in out


def test_report_called_away_on_real_lot_marked_holding_unchanged():
    real = {"lot_ref": {"source": "real", "lot_id": None, "ticker": "CCX"}}
    paper = {"lot_ref": {"source": "manual", "lot_id": 1, "ticker": "CCY"}}
    out = rep.render_report(
        [_cc(1, pnl=150.0, result="called_away", stock_leg_pnl=2000.0, **real),
         _cc(2, pnl=150.0, result="called_away", stock_leg_pnl=2000.0, ticker="CCY", **paper)],
        None, "2026-10-21")
    rows = [ln for ln in out.split("## 备兑 Call（Covered Call）记录")[1].splitlines() if ln.startswith("| 1 ")
            or ln.startswith("| 2 ")]
    assert "called_away（真实持仓未变）" in rows[1] or "called_away（真实持仓未变）" in rows[0]
    assert sum("真实持仓未变" in r for r in rows) == 1


def _cc_record(**extra):
    return {**{"id": 9, "ticker": "ZZZ", "strategy": "covered_call", "status": "open",
            "short_strike": 50.0, "long_strike": None, "expiry": "2026-11-06",
            "contracts": 1, "credit": 1.0, "lot_ref": "real", "shares": 100}, **extra}


def test_public_report_has_front_matter_and_title():
    text = rep.render_public_report([], {}, "2026-10-24")
    assert text.startswith(f"---\nsource: {LABEL}\n---\n\n# 模拟盘报告")
    assert "2026-10-24" in text


def test_public_report_omits_covered_calls():
    text = rep.render_public_report([_cc_record()], {}, "2026-10-24")
    assert "ZZZ" not in text and "备兑 Call（Covered Call）记录" not in text


def test_write_public_report_only_in_real_layout(tmp_path):
    import spread_sim
    from datetime import date
    ledger = {"spreads": []}
    plain = tmp_path / "x"
    plain.mkdir()
    spread_sim._write_public_report(plain, ledger, {}, date(2026, 10, 24))  # no wiki/options: no-op
    real = tmp_path / "data" / "paper-trading"
    real.mkdir(parents=True)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    spread_sim._write_public_report(real, ledger, {}, date(2026, 10, 24))
    assert (tmp_path / "wiki" / "options" / "paper-trading.md").read_text(encoding="utf-8").startswith(f"---\nsource: {LABEL}\n")


def test_write_public_report_skips_open_without_marks(tmp_path):
    import spread_sim
    from datetime import date
    real = tmp_path / "data" / "paper-trading"
    real.mkdir(parents=True)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    spread_sim._write_public_report(real, {"spreads": [{"status": "open"}]}, {}, date(2026, 10, 24))
    assert not (tmp_path / "wiki" / "options" / "paper-trading.md").exists()


def _cap_rec(i, short, long, opened, status="open", exit_date=None):
    return {"id": i, "ticker": "ZZZ", "strategy": "bull_put_spread", "status": status,
            "short_strike": short, "long_strike": long, "width": short - long, "credit": 0.0,
            "contracts": 1, "snapshot_date": opened, "exit_date": exit_date,
            "expiry": "2026-12-01"}


def test_peak_capital_counts_overlap_and_frees_on_exit_day():
    recs = [_cap_rec(1, 105, 100, "2026-10-01", "expired", "2026-10-08"),   # 500
            _cap_rec(2, 110, 100, "2026-10-05", "expired", "2026-10-12"),   # 1000, overlaps #1
            _cap_rec(3, 107, 100, "2026-10-08")]                            # 700, opens the day #1 exits
    assert rep.peak_capital(recs) == (1700.0, "2026-10-08")


def test_peak_capital_none_without_capital():
    assert rep.peak_capital([]) is None


def test_public_report_labels_contract_counts():
    rec = _cap_rec(5, 200, 197.5, "2026-10-23")
    rec.update({"delta_bucket": 0.15, "credit": 0.5})
    text = rep.render_public_report([rec], {}, "2026-10-24")
    row = next(l for l in text.splitlines() if l.startswith("| 5 |"))
    assert " 1 张 " in row


# ---- broker fill + slippage columns (Sell Put / Covered Call tables only) ----


def _priv(records, marks, day, **kw):
    """The private report.md: broker columns explicitly on (the default is off, fail closed)."""
    return rep.render_report(records, marks, day, broker_cols=True, **kw)


def test_render_report_default_has_no_broker_columns():
    recs = [_csp(1, status="open", broker=_broker(1.9)), _csp(2, pnl=50.0, result="win", broker=_broker(1.9))]
    out = rep.render_report(recs, None, "2026-10-24")
    assert "券商成交" not in out and "滑点" not in out and "1.90" not in out

def _broker(fill, status="filled", **extra):
    return {"status": status, "fill_price": fill, **extra}


def test_report_shows_broker_fill_and_slippage():
    # paper credit 2.00, broker filled 1.90, 3 contracts: (1.90-2.00)*100*3 = -$30
    open_ = _csp(1, status="open", credit=2.0, contracts=3, broker=_broker(1.90))
    done = _csp(2, credit=2.0, contracts=1, pnl=200.0, result="win", broker=_broker(1.90))
    out = _priv([open_, done], None, "2026-10-24")
    assert "券商成交" in out and "滑点" in out
    open_row = next(l for l in out.split("\n") if l.startswith("| cash_secured_put | 1 | CSPX"))
    done_row = next(l for l in out.split("\n") if l.startswith("| cash_secured_put | 2 | CSPX"))
    assert "$1.90" in open_row and "-$30" in open_row
    assert "$1.90" in done_row and "-$10" in done_row


def test_report_slippage_positive_is_signed():
    out = _priv([_csp(1, status="open", credit=1.5, broker=_broker(1.6))], None, "2026-10-24")
    row = next(l for l in out.split("\n") if l.startswith("| cash_secured_put | 1 | CSPX"))
    assert "+$10" in row


def test_report_covered_call_rows_show_slippage():
    cc = _cc_record(status="open", credit=2.0, contracts=2, broker=_broker(1.95, status="partial"))
    out = _priv([cc], None, "2026-10-24")
    row = next(l for l in out.split("\n") if l.startswith("| %s |" % cc["id"]))
    assert "$1.95" in row and "-$10" in row


def _row(out: str, prefix: str) -> dict:
    """Row cells keyed by the header of the table the row sits in."""
    header = None
    for line in out.splitlines():
        if line.startswith("| ") and "---" not in line and not line.startswith(prefix):
            header = [c.strip() for c in line.split("|")]
        if line.startswith(prefix):
            return dict(zip(header, [c.strip() for c in line.split("|")]))
    raise AssertionError(prefix)


def test_report_dash_without_broker_block():
    out = _priv([_csp(1, status="open"), _csp(2, pnl=50.0, result="win")], None, "2026-10-24")
    for i in (1, 2):
        row = _row(out, f"| cash_secured_put | {i} | CSPX")
        assert row["券商成交"] == "—" and row["滑点"] == "—"


@pytest.mark.parametrize("broker", [
    _broker(None), _broker(None, status="partial"), _broker(1.9, status="submitted"),
    _broker(1.9, status="cancelled"), _broker(1.9, status="pending"), _broker("1.9"),
    _broker(True), {"status": "filled"},
    {"status": "submitted", "close_order": {"status": "filled", "fill_price": 0.5}},
])
def test_report_ignores_unfilled_or_non_numeric_broker(broker):
    out = _priv([_csp(1, status="open", credit=2.0, broker=broker)], None, "2026-10-24")
    row = next(l for l in out.split("\n") if l.startswith("| cash_secured_put | 1 | CSPX"))
    assert "$0.00" not in row and "$2.00" not in row
    assert "-$200" not in row and "-$0" not in row
    cells = [c.strip() for c in row.split("|")]
    assert "—" in cells


def test_report_bull_put_tables_have_no_broker_columns():
    out = _priv([_s(1, status="open", broker=_broker(1.0)), _s(2, pnl=100.0, broker=_broker(1.0))],
                            None, "2026-10-24")
    assert "券商成交" not in out and "滑点" not in out


def test_public_report_omits_broker_columns():
    recs = [_csp(1, status="open", credit=2.0, broker=_broker(1.90)),
            _csp(2, pnl=200.0, result="win", broker=_broker(1.90, order_ref="X1", perm_id=99)),
            _cc_record(broker=_broker(1.9))]
    text = rep.render_public_report(recs, {}, "2026-10-24")
    assert "CSPX" in text
    assert "券商成交" not in text and "滑点" not in text
    assert "1.90" not in text and "X1" not in text


def test_write_report_private_md_has_broker_columns_but_public_page_does_not(tmp_path):
    import spread_sim
    from datetime import date
    root = tmp_path / "data" / "paper-trading"
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    root.mkdir(parents=True)
    ledger = {"spreads": [_csp(1, credit=2.0, pnl=200.0, result="win", broker=_broker(1.9))]}
    spread_sim._write_report(root, ledger, {}, date(2026, 10, 24))
    assert "券商成交" in (root / "report.md").read_text(encoding="utf-8")
    pub = tmp_path / "wiki" / "options" / "paper-trading.md"
    assert pub.exists()
    for page in [pub]:
        assert "券商成交" not in page.read_text(encoding="utf-8")


# ---- collar / protective put report (task 5) -----------------------------------

def _collar(id_, status="expired", pnl=60.0, result="called_away", ticker="ZZZQ", **extra):
    base = {"id": id_, "ticker": ticker, "status": status, "contracts": 7,
            "strategy": "collar", "put_strike": 71.5, "put_debit": 1.2,
            "call_strike": 93.25, "call_credit": 1.8, "net_credit": 0.6,
            "covered_shares": 777, "cost_basis": 83.37, "reference_price": 83.37,
            "lot_ref": {"source": "real"}, "expiry": "2026-11-20",
            "snapshot_date": "2026-10-20",
            "pnl": pnl if status != "open" else None,
            "result": result if status != "open" else None,
            "stock_leg_pnl": 1234.0 if status != "open" else None}
    base.update(extra)
    return base


def _pput(id_, status="expired", pnl=-100.0, result="expired_worthless", **extra):
    base = _collar(id_, status=status, pnl=pnl, result=result, ticker="ZZZP",
                   strategy="protective_put", call_strike=None, call_credit=0,
                   put_debit=0.5, net_credit=-0.5, contracts=3, covered_shares=333)
    base.update(extra)
    return base


def _old_ledger():
    return [_s(1, pnl=100.0), _s(2, status="open"), _csp(3, pnl=-300.0),
            _csp(4, status="open"),
            _cc(5, pnl=150.0, result="called_away", stock_leg_pnl=2000.0),
            _cc(6, status="open")]


OLD_MARKS = {2: _mk(-20.0), 4: _mk(10.0), 6: _mk(40.0)}


def test_existing_report_output_unchanged_for_old_strategies():
    out = rep.render_report(_old_ledger(), OLD_MARKS, "2026-10-21").split("\n")
    for line in [
        "未平仓占用资金合计 $10,500（Put 贷方价差 $500 / 现金担保 Put $10,000）；浮盈亏合计 +$30（+0.1%）",
        "| 2 | ABC | 95/90 | 2026-10-23 | 1 | -$20 | $500 | -4.0% | 2.0% | — |",
        "| bull_put_spread | 1 | 100% | +$100 | +$100 | — | — | — |",
        "| covered_call | 1 | 100% | +$150 | +$150 | — | — | — |",
        "| cash_secured_put | 3 | CSPX | 100 Put | 2026-10-23 | assigned | 到期结算 | -$300 | $10,000 | -3.0% |",
        "| 5 | CCX | 110 Call | 2026-10-23 | called_away | 到期结算 | +$150 | +$2,150 | +0.8% |",
        "## 现金担保 Put 记录",
        "## 备兑 Call（Covered Call）记录",
    ]:
        assert line in out
    assert not any("Collar" in l or "保护性" in l for l in out)


def test_capital_at_risk_collar_net_debit_only():
    assert rep.capital_at_risk(_pput(1)) == pytest.approx(0.5 * 100 * 3)
    assert rep.capital_at_risk(_collar(1, net_credit=-0.25)) == pytest.approx(0.25 * 100 * 7)


def test_capital_at_risk_collar_net_credit_is_zero():
    assert rep.capital_at_risk(_collar(1, net_credit=0.6)) == 0.0
    assert rep.capital_at_risk(_collar(1, net_credit=0.0)) == 0.0


def test_pnl_base_collar_uses_cost_times_shares():
    assert rep.pnl_base(_collar(1)) == pytest.approx(83.37 * 777)
    assert rep.pnl_base(_pput(1)) == pytest.approx(83.37 * 333)


def test_legs_label_collar_and_protective_put():
    assert rep.legs_label(_collar(1)) == "71.5 Put / 93.25 Call"
    assert rep.legs_label(_pput(1)) == "71.5 Put"


def test_stats_exclude_collar_and_protective_put_from_win_rate():
    old = _old_ledger()
    new = old + [_collar(7, pnl=500.0), _pput(8, pnl=-900.0), _collar(9, status="open")]
    assert rep.compute_stats(new) == rep.compute_stats(old)
    assert rep.compute_strategy_stats(new) == rep.compute_strategy_stats(old)
    assert set(rep.compute_strategy_stats(new)) == {"bull_put_spread", "cash_secured_put", "covered_call"}
    assert rep._strategy_section(new) == rep._strategy_section(old)
    spreads = [s for s in new if rep.strategy_of(s) == "bull_put_spread"]
    assert rep.compute_stats(spreads + [_collar(7)]) == rep.compute_stats(spreads)


def test_settled_collar_does_not_change_any_existing_report_section():
    old = rep.render_report(_old_ledger(), OLD_MARKS, "2026-10-21")
    new = rep.render_report(_old_ledger() + [_collar(7, pnl=500.0), _pput(8, net_credit=0.1)], OLD_MARKS, "2026-10-21")
    head, rest = new.split("## 保护性 Put 与 Collar 记录")
    tail = rest.split("---\n*口径")[1]
    note = "保护性 Put / Collar=占用资金为净支出（净收入记 0），盈亏%=期权腿盈亏÷(成本×股数)；"
    assert head + "---\n*口径" + tail.replace(note, "") == old


def test_report_has_collar_section_with_floor_cap_net_cost_cap_cost():
    recs = [_collar(1, status="open"), _collar(2, pnl=60.0, result="called_away"),
            _pput(3, status="open"), _pput(4, pnl=-150.0, result="protected", stock_leg_pnl=-80.0)]
    marks = {1: {"pnl": -35.0, "stale": False}, 3: {"pnl": 12.0, "stale": False}}
    out = rep.render_report(recs, marks, "2026-10-21")
    assert "## 保护性 Put 与 Collar 记录" in out
    sec = out.split("## 保护性 Put 与 Collar 记录")[1].split("---\n*口径")[0]
    assert "71.5" in sec and "93.25" in sec               # floor and cap
    assert "净收入 $0.60/股（$420）" in sec               # 0.6 * 100 * 7, credit side
    assert "净支出 $0.50/股（$150）" in sec               # protective put debit
    assert "锁定区间 71.5 ~ 93.25" in sec and "不封顶" in sec
    assert "上限代价" in sec
    assert "-$35" in sec and "+$12" in sec                # floating pnl
    assert "called_away" in sec and "protected" in sec
    assert "+$60" in sec and "+$1,234" in sec and "+$1,294" in sec   # option, stock, total
    assert "现金流" in sec and "机会成本" in sec
    assert "盈亏平衡胜率" not in sec and "是否过线" not in sec


def test_collar_section_without_marks_says_no_quote():
    sec = rep.render_report([_collar(1, status="open")], None, "2026-10-21")
    assert "运行 status 查看" in sec


def test_collar_section_shows_zero_net_cost():
    out = rep.render_report([_collar(1, status="open", net_credit=0.0)], None, "2026-10-21")
    assert "净成本 $0（零成本）" in out


def test_report_open_summary_does_not_label_collar_as_put_spread():
    recs = [_s(1, status="open"), _pput(2, status="open")]
    line = rep._open_summary(recs, {})[0]
    assert "Put 贷方价差 $500" in line
    assert "保护性 Put / Collar 净支出 $150" in line
    assert "现金担保" not in line
    only = rep._open_summary([_pput(2, status="open")], {})[0]
    assert "Put 贷方价差" not in only and "未平仓占用资金合计 $150" in only


def test_report_with_only_collar_has_no_by_strategy_section():
    out = rep.render_report([_collar(1)], None, "2026-10-21")
    assert "## 按策略对比" not in out and "## 保护性 Put 与 Collar 记录" in out


def test_collar_summary_block_counts_results_without_win_rate():
    recs = [_collar(1, result="called_away", pnl=60.0), _collar(2, result="protected", pnl=-20.0),
            _collar(3, result="expired_worthless", pnl=40.0), _collar(4, status="open")]
    sec = rep.render_report(recs, None, "2026-10-21").split("## 保护性 Put 与 Collar 记录")[1]
    assert "已结算 3" in sec and "called_away 1" in sec and "protected 1" in sec
    assert "expired_worthless 1" in sec and "+$80" in sec


UNKNOWN = {"id": 99, "ticker": "ZZZU", "strategy": "future_thing", "status": "open",
           "contracts": 9, "covered_shares": 4242, "cost_basis": 61.11, "expiry": "2026-11-20"}


def test_public_report_allowlist_excludes_collar_and_protective_put():
    recs = [_s(1, pnl=100.0, ticker="ABC"), _csp(2, pnl=-300.0, ticker="CSPX"),
            _collar(3, status="open"), _collar(4), _pput(5, status="open"), _pput(6), UNKNOWN]
    marks = {1: _mk(1.0), 3: {"pnl": -35.0, "stale": False}}
    text = rep.render_public_report(recs, marks, "2026-10-24")
    for secret in ("ZZZQ", "ZZZP", "ZZZU", "777", "333", "4242", "83.37", "71.5", "93.25",
                   "61.11", "future_thing", "collar", "protective_put"):
        assert secret not in text
    assert "ABC" in text and "CSPX" in text


def test_public_report_unchanged_by_new_strategy_records():
    old = _old_ledger()
    base = rep.render_public_report(old, {}, "2026-10-24")
    more = rep.render_public_report(old + [_collar(7), _pput(8), _collar(9, status="open"), UNKNOWN],
                                    {}, "2026-10-24")
    assert base == more


def test_write_public_report_skips_new_strategies(tmp_path):
    import spread_sim
    from datetime import date
    real = tmp_path / "data" / "paper-trading"
    real.mkdir(parents=True)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    ledger = {"spreads": [_s(1, pnl=100.0), _collar(2, status="open"), _collar(3), _pput(4), UNKNOWN]}
    spread_sim._write_public_report(real, ledger, {1: _mk(1.0)}, date(2026, 10, 24))
    text = (tmp_path / "wiki" / "options" / "paper-trading.md").read_text(encoding="utf-8")
    for secret in ("ZZZQ", "ZZZP", "ZZZU", "777", "333", "4242", "83.37", "71.5", "93.25"):
        assert secret not in text


def test_write_public_report_open_collar_without_marks_does_not_block(tmp_path):
    import spread_sim
    from datetime import date
    real = tmp_path / "data" / "paper-trading"
    real.mkdir(parents=True)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    spread_sim._write_public_report(real, {"spreads": [_collar(1, status="open")]}, {}, date(2026, 10, 24))
    assert (tmp_path / "wiki" / "options" / "paper-trading.md").exists()


def test_write_report_private_md_has_collar_section(tmp_path):
    import spread_sim
    from datetime import date
    root = tmp_path / "data" / "paper-trading"
    root.mkdir(parents=True)
    spread_sim._write_report(root, {"spreads": [_collar(1)]}, {}, date(2026, 10, 24))
    assert "## 保护性 Put 与 Collar 记录" in (root / "report.md").read_text(encoding="utf-8")
    assert "ZZZQ" in (root / "report.html").read_text(encoding="utf-8")


# ---- fix round 1: unknown strategies, peak, denominators, footers ----------------

def test_unknown_strategy_has_zero_capital_and_base():
    assert rep.capital_at_risk(UNKNOWN) == 0.0 and rep.pnl_base(UNKNOWN) == 0.0


def test_unknown_open_record_is_counted_nowhere_and_does_not_raise():
    line = rep._open_summary([_s(1, status="open"), UNKNOWN], {99: {"pnl": 999.0}})
    assert line[0] == "未平仓占用资金合计 $500（另有 1 笔无报价，未计入浮盈亏）" or "$500" in line[0]
    assert "999" not in line[0]
    assert rep._open_summary([UNKNOWN], {}) == []
    assert rep.peak_capital([{**UNKNOWN, "snapshot_date": "2026-10-20"}]) is None
    out = rep.render_report([UNKNOWN], None, "2026-10-21")
    assert "ZZZU" not in out


def test_unknown_record_leaves_private_and_public_reports_unaffected():
    old = _old_ledger()
    assert rep.render_report(old + [UNKNOWN], OLD_MARKS, "2026-10-21").split("## 按策略对比")[0] \
        == rep.render_report(old, OLD_MARKS, "2026-10-21").split("## 按策略对比")[0]
    assert rep.render_public_report(old + [UNKNOWN], {}, "2026-10-24") \
        == rep.render_public_report(old, {}, "2026-10-24")


def test_write_report_does_not_abort_on_unknown_strategy(tmp_path):
    import spread_sim
    from datetime import date
    root = tmp_path / "data" / "paper-trading"
    root.mkdir(parents=True)
    spread_sim._write_report(root, {"spreads": [_s(1, pnl=1.0), UNKNOWN]}, {}, date(2026, 10, 24))
    assert (root / "report.md").exists() and (root / "report.html").exists()


def test_private_peak_includes_collar_debit_public_peak_does_not():
    spread = _s(1, status="open", snapshot_date="2026-10-20")
    pput = _pput(2, status="open")
    assert rep.peak_capital([spread, pput])[0] == 650.0
    priv = rep.render_report([spread, pput], None, "2026-10-21")
    assert "历史最大同时占用资金 $650" in priv
    pub = rep.render_public_report([spread, pput], {}, "2026-10-24")
    assert "历史最大同时占用资金 $500" in pub
    assert pub == rep.render_public_report([spread], {}, "2026-10-24")


def test_public_header_names_all_withheld_strategies():
    text = rep.render_public_report([], {}, "2026-10-24")
    assert "涉及真实持股的策略（备兑 Call、保护性 Put、Collar）不在此公开" in text


def test_open_summary_pct_excludes_collar_base():
    spread = _s(1, status="open", short_strike=50.0, long_strike=45.0, credit=1.0)  # $400 capital
    spread["credit"] = 1.0
    collar = _collar(2, status="open", cost_basis=10.3, covered_shares=777, net_credit=-0.1)
    line = rep._open_summary([spread, collar], {1: _mk(-20.0), 2: {"pnl": 10.0, "stale": False}})[0]
    assert "浮盈亏合计 -$20（-5.0%）" in line


def test_footer_defines_collar_capital_and_pnl_pct():
    out = rep.render_report([_collar(1)], None, "2026-10-21")
    foot = out.split("---\n*口径")[1]
    assert "净支出" in foot and "成本×股数" in foot


def test_collar_called_away_on_real_lot_noted_in_report_and_not_for_paper():
    out = rep.render_report([_collar(1, result="called_away")], None, "2026-10-21")
    sec = out.split("## 保护性 Put 与 Collar 记录")[1]
    assert "called_away（真实持仓未变）" in sec
    paper = _collar(2, result="called_away", lot_ref={"source": "manual", "lot_id": 1})
    out = rep.render_report([paper], None, "2026-10-21")
    assert "真实持仓未变" not in out.split("## 保护性 Put 与 Collar 记录")[1]
    assert rep.result_text(_collar(3, result="protected")) == "protected"


def test_collar_cap_cost_label_says_measured_from_reference_price():
    text = rep.lot_put_cap_cost(_collar(1, status="open"))
    assert "相对参考价（真实持仓为平均成本）" in text and "%" in text
    out = rep.render_report([_collar(1, status="open")], None, "2026-10-21")
    assert "相对参考价（真实持仓为平均成本）" in out


# ---- iron condor report (task 5) ------------------------------------------------
import hashlib


def _condor(id_, status="expired", pnl=150.0, result="win", breached="none", ticker="ZZZQ",
            **extra):
    """Full iron-condor record (Global Constraints fields): 85/90 Put, 110/115 Call, width 5,
    net credit 1.50 = 0.80 + 0.70 -> breakevens 88.5 / 111.5, max loss (5 - 1.5) x 100 = $350."""
    settled = status != "open"
    base = {"id": id_, "ticker": ticker, "strategy": "iron_condor", "status": status,
            "put_short_strike": 90.0, "put_long_strike": 85.0,
            "call_short_strike": 110.0, "call_long_strike": 115.0, "width": 5.0,
            "put_credit": 0.8, "call_credit": 0.7, "net_credit": 1.5,
            "breakeven_low": 88.5, "breakeven_high": 111.5, "delta_bucket": 0.15,
            "put_short_delta": 0.15, "call_short_delta": 0.15,
            "put_short_iv": 0.3, "call_short_iv": 0.28, "underlying_price": 100.0,
            "contracts": 1, "expiry": "2026-11-20", "snapshot_date": "2026-10-20",
            "suggestion_file": "2026-10-20-ZZZQ-condor.json", "earnings_in_window": False}
    if settled:
        base.update(result=result, pnl=pnl, exit_cost=round(1.5 - pnl / 100, 4),
                    exit_underlying=100.0, exit_date=base["expiry"])
        if status == "expired":
            base["breached_side"] = breached  # early close never writes it
    base.update(extra)
    return base


def _odd_condor(id_, **kw):
    """Distinctive numbers that must never reach the public page: 83.5/88.5/111.5/116.5,
    net 1.37 -> breakevens 87.13 / 112.87; 3 contracts -> capital (5 - 1.37) x 300 = $1,089."""
    return _condor(id_, put_long_strike=83.5, put_short_strike=88.5, call_short_strike=111.5,
                   call_long_strike=116.5, put_credit=0.71, call_credit=0.66, net_credit=1.37,
                   breakeven_low=87.13, breakeven_high=112.87, contracts=3, **kw)


CONDOR_SECRETS = ("ZZZQ", "83.5", "116.5", "87.13", "112.87", "1,089", "1.37", "iron_condor", "铁鹰")


def _all_old_ledger():
    return _old_ledger() + [_collar(7, ticker="ZZZC"), _pput(8), _collar(9, status="open", ticker="ZZZC")]


PIN_PRIVATE = "12c81e45e5f4c26986f1d4c04a917d5ddfd50bf3e6ec6e3d13594ed736b78f87"
PIN_BROKER = "d42599b0856fe881ea8eac4737abe59da7381f91c6482ae780863277b2728f40"
PIN_PUBLIC = "8eae3c156a03e8ffef95ab943aca4450d9ee19a1fe61a911c73a2be4cd9f8147"
# Same page without the edition-driven front matter (Task 9), so both editions pin the body.
PIN_PUBLIC_BODY = "ae7b12453d0bbe3f084e48029e947109e1bceec12f4f3875fd8d04f162f8b690"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_existing_report_output_unchanged_for_old_strategies_pinned_bytes():
    # Hashes captured from the pre-condor code (commit 4ab8539) for all five old strategies.
    marks = {**OLD_MARKS, 9: {"pnl": -35.0, "stale": False}}
    assert _sha(rep.render_report(_all_old_ledger(), marks, "2026-10-21")) == PIN_PRIVATE
    assert _sha(rep.render_report(_all_old_ledger(), marks, "2026-10-21", broker_cols=True)) == PIN_BROKER
    public = rep.render_public_report(_all_old_ledger(), marks, "2026-10-24")
    front, body = public.split("\n---\n\n", 1)
    assert front == f"---\nsource: {LABEL}" and _sha(body) == PIN_PUBLIC_BODY
    if option_source_default.EDITION == "overlay":   # the share edition writes its own label
        assert _sha(public) == PIN_PUBLIC


def test_public_report_unchanged_with_condor_records_present():
    old = _all_old_ledger()
    condors = [_odd_condor(20, status="open"), _odd_condor(21, pnl=411.0),
               _odd_condor(22, status="closed_early", pnl=-111.0, result="loss"),
               _odd_condor(23, pnl=-1089.0, result="loss", breached="call")]
    marks = {**OLD_MARKS, 20: {"pnl": 77.0, "cost": 1.11, "underlying": 101.0, "dist_pct": 9.4,
                               "flags": {"stop": False, "take_profit": False}, "stale": False}}
    base = rep.render_public_report(old, OLD_MARKS, "2026-10-24")
    more = rep.render_public_report(old + condors, marks, "2026-10-24")
    assert more.encode("utf-8") == base.encode("utf-8")
    for token in CONDOR_SECRETS:
        assert token not in more, token
    assert rep.public_records(condors) == []
    assert "iron_condor" not in rep.PUBLIC_STRATEGIES


def test_capital_at_risk_condor_width_minus_net_credit():
    assert rep.capital_at_risk(_condor(1, status="open")) == pytest.approx(350.0)   # (5-1.5)x100
    assert rep.capital_at_risk(_condor(1, status="open", contracts=2)) == pytest.approx(700.0)
    assert rep.capital_at_risk(_odd_condor(1)) == pytest.approx(1089.0)           # 3.63x300
    assert rep.pnl_base(_condor(1)) == rep.capital_at_risk(_condor(1))


def test_legs_label_condor_four_strikes():
    assert rep.legs_label(_condor(1)) == "85/90/110/115"
    assert rep.legs_label(_odd_condor(1)) == "83.5/88.5/111.5/116.5"


def test_open_summary_labels_condor_capital_separately():
    recs = [_s(1, status="open"), _condor(2, status="open")]          # $500 spread + $350 condor
    line = rep._open_summary(recs, {1: _mk(-20.0), 2: _mk(90.0)})[0]
    # 70 / 850 = 8.2%
    assert line == "未平仓占用资金合计 $850（Put 贷方价差 $500 / 铁鹰 $350）；浮盈亏合计 +$70（+8.2%）"
    only = rep._open_summary([_condor(2, status="open")], {2: _mk(90.0)})[0]
    assert only == "未平仓占用资金合计 $350；浮盈亏合计 +$90（+25.7%）"       # 90 / 350
    assert "Put 贷方价差" not in only


def _condor_mix():
    return [_condor(1, status="open"),
            _condor(2, pnl=150.0),                                   # C between shorts
            _condor(3, pnl=-50.0, result="loss", breached="put"),     # C=88: 1.5-2 = -0.5
            _condor(4, status="closed_early", pnl=60.0)]              # cost 0.90, no breached_side


def test_report_has_condor_section():
    marks = {1: {"pnl": 90.0, "cost": 0.6, "underlying": 100.0, "dist_pct": 10.0,
                 "flags": {"stop": False, "take_profit": True}, "stale": False}}
    out = rep.render_report(_condor_mix(), marks, "2026-10-21", broker_cols=True)
    sec = out.split("## 铁鹰（Iron Condor）记录")[1].split("---\n*口径")[0]
    assert "85/90/110/115" in sec and "88.50 / 111.50" in sec
    assert "$1.50" in sec and "$350" in sec and "70%" in sec           # (5-1.5)/5
    assert "+$90" in sec and "10.0%" in sec and "止盈线" in sec
    assert "+$150" in sec and "-$50" in sec and "+$60" in sec
    assert "未突破" in sec and "Put 侧" in sec and "提前平仓" in sec
    row4 = [ln for ln in sec.splitlines() if ln.startswith("| 4 ")][0]
    assert row4.rstrip().endswith("| — |")                            # early close: side unknown
    assert "实际盈亏比" in sec                                         # basis is spelled out
    assert "铁鹰=" in out.split("---\n*口径")[1]


def test_condor_not_in_compute_stats_or_delta_buckets():
    old = _old_ledger()
    new = old + _condor_mix()
    assert rep.compute_stats(new) == rep.compute_stats(old)
    assert rep.compute_stats(_condor_mix())["n_settled"] == 0
    before = rep.render_report(old, OLD_MARKS, "2026-10-21")
    after = rep.render_report(new, OLD_MARKS, "2026-10-21")
    seg = lambda t: t.split("## 已结算")[1].split("## 按策略对比")[0]
    assert seg(after) == seg(before) and "ZZZQ" not in seg(after)
    table = lambda t: [ln for ln in t.split("## 未平仓")[1].split("## 已结算")[0].splitlines()
                       if ln.startswith("|")]
    assert table(after) == table(before) and table(before)


def test_by_strategy_table_has_condor_row_with_theoretical_breakeven():
    recs = _old_ledger() + [
        _condor(10, pnl=150.0),                                       # theory 3.5/5 = 70%
        _condor(11, pnl=-400.0, result="loss", breached="call", net_credit=1.0, put_credit=0.5,
                call_credit=0.5, breakeven_low=89.0, breakeven_high=111.0)]  # theory 4/5 = 80%
    sec = rep.render_report(recs, None, "2026-10-21").split("## 按策略对比")[1].split("## 现金担保")[0]
    # actual: 400 / (150 + 400) = 72.7%; 50% < 72.7% -> fails
    assert "| iron_condor | 2 | 50% | -$250 | +$150 | -$400 | 73% | ❌ |" in sec
    assert "| bull_put_spread | 1 | 100% | +$100 | +$100 | — | — | — |" in sec
    note = [ln for ln in sec.splitlines() if "理论盈亏平衡胜率" in ln][0]
    assert "75%" in note and "口径不同" in note                         # (70% + 80%) / 2


def test_condor_small_sample_warning():
    out = rep.render_report([_condor(1), _condor(2, pnl=-50.0, result="loss", breached="put")],
                            None, "2026-10-21")
    assert "iron_condor 样本不足（2 < 30）" in out
    assert "样本不足（2 < 30）" in out.split("## 铁鹰（Iron Condor）记录")[1]


def test_unknown_strategy_still_counts_nowhere():
    c = _condor(1, status="open")
    assert rep.capital_at_risk(UNKNOWN) == 0.0
    assert "future_thing" not in rep.compute_strategy_stats([UNKNOWN, _condor(2)])
    assert rep._open_summary([UNKNOWN], {}) == []
    assert rep.render_report([c, UNKNOWN], None, "2026-10-21") == rep.render_report([c], None, "2026-10-21")
    only = rep.render_report([UNKNOWN], None, "2026-10-21")
    assert "ZZZU" not in only and "铁鹰" not in only and "## 按策略对比" not in only


def test_write_report_with_condor_private_has_section_public_does_not(tmp_path):
    import spread_sim
    from datetime import date
    root = tmp_path / "data" / "paper-trading"
    root.mkdir(parents=True)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    ledger = {"spreads": [_s(1, pnl=100.0), _odd_condor(2, pnl=411.0),
                          _odd_condor(3, status="closed_early", pnl=-111.0, result="loss")]}
    spread_sim._write_report(root, ledger, {}, date(2026, 10, 24))
    assert "## 铁鹰（Iron Condor）记录" in (root / "report.md").read_text(encoding="utf-8")
    assert "83.5/88.5/111.5/116.5" in (root / "report.html").read_text(encoding="utf-8")
    public = (tmp_path / "wiki" / "options" / "paper-trading.md").read_text(encoding="utf-8")
    assert public == rep.render_public_report([_s(1, pnl=100.0)], {}, "2026-10-24")
    for token in CONDOR_SECRETS:
        assert token not in public, token


def test_condor_summary_counts_none_breached_side_as_early_close():
    recs = [_condor(1, pnl=150.0), _condor(2, pnl=60.0, breached_side=None),
            _condor(3, status="closed_early", pnl=40.0)]
    sec = rep.render_report(recs, None, "2026-10-21").split("## 铁鹰（Iron Condor）记录")[1]
    assert "未突破 1 / Put 侧 0 / Call 侧 0（另有 2 笔提前平仓，不记突破侧）" in sec


def test_condor_note_says_theory_assumes_full_win_or_full_loss():
    assert "满额盈利或满额最大亏损" in rep.CONDOR_NOTE


def test_condor_breakevens_label_two_decimals():
    assert rep.condor_breakevens_label({"breakeven_low": 92.59375, "breakeven_high": 111.5})         == "92.59 / 111.50"


# ---- bull call debit spread report (task 5) ------------------------------------
import spread_html


def _bullcall(id_, status="expired", pnl=110.0, result="win", ticker="ZQBC", **extra):
    """Design section 10: buy 100 Call 3.20 / sell 105 Call 1.30, 1 contract -> debit 1.90,
    width 5, breakeven 101.90, max loss $190, max profit $310, required win rate 38%.
    Settled defaults to C=103: value 3.00, pnl +$110, captured 110 / 310 = 0.3548."""
    base = {"id": id_, "ticker": ticker, "strategy": "bull_call_spread", "status": status,
            "long_call_strike": 100.0, "short_call_strike": 105.0, "width": 5.0,
            "long_call_mid": 3.2, "short_call_mid": 1.3, "debit": 1.9, "breakeven": 101.9,
            "breakeven_move_pct": 0.019, "max_profit_move_pct": 0.05, "delta_bucket": 0.5,
            "long_call_delta": 0.52, "short_call_delta": 0.31, "long_call_iv": 0.3,
            "short_call_iv": 0.28, "underlying_price": 100.0, "contracts": 1,
            "expiry": "2026-11-27", "snapshot_date": "2026-10-20",
            "suggestion_file": "2026-10-20-ZQBC-bullcall.json", "earnings_in_window": False,
            "exit_date": None, "exit_value": None, "exit_underlying": None, "pnl": None,
            "result": None, "fraction_of_max_captured": None}
    if status != "open":
        value = round(1.9 + pnl / 100, 4)
        base.update(result=result, pnl=pnl, exit_value=value, exit_underlying=100.0 + value,
                    exit_date=base["expiry"], fraction_of_max_captured=round(pnl / 310, 4))
    base.update(extra)
    return base


def _bc_mark(pnl=50.0, dist=2.3, stop=False, tp=False):
    # long 4.00 / short 1.60 -> value 2.40 -> (2.40 - 1.90) x 100 = +$50
    return {"value": 2.4, "pnl": pnl, "underlying": 102.4, "dist_pct": dist, "stale": False,
            "flags": {"stop": stop, "take_profit": tp}, "long_mid": 4.0, "short_mid": 1.6,
            "long_iv": 0.3, "short_iv": None}


def _six_old_ledger():
    condors = [_condor(11, status="open"), _condor(12, pnl=150.0),
               _condor(13, pnl=-50.0, result="loss", breached="put"),
               _condor(14, status="closed_early", pnl=60.0)]
    # the open bull put gets the fields the html payoff card reads
    full = _s(2, status="open", width=5.0, credit=1.0, underlying_price=100.0)
    return [full if r["id"] == 2 else r for r in _all_old_ledger()] + condors


SIX_MARKS = {**OLD_MARKS, 2: {**_mk(-20.0), "underlying": 101.0}, 9: {"pnl": -35.0, "stale": False},
             11: {"pnl": 90.0, "cost": 0.6, "underlying": 100.0, "dist_pct": 10.0,
                  "flags": {"stop": False, "take_profit": True}, "stale": False}}
PIN6_PRIVATE = "8a12d539721732887d58250fb2c0de44f08a4712b93f51c4211bbea316903c47"
PIN6_BROKER = "f2ad4e8d1347ad110251828f1c6c88feab99f8e7452c015af29822d24723800a"
PIN6_HTML = "c96b4818bded9ecb61af5b3f3a15a98e2c392a66f3950150c19fe6e4e1b2b401"


def test_existing_report_output_unchanged_for_six_old_strategies():
    # Hashes captured from the pre-bull-call code (commit dbd252a) for all six old strategies.
    recs = _six_old_ledger()
    assert _sha(rep.render_report(recs, SIX_MARKS, "2026-10-21")) == PIN6_PRIVATE
    assert _sha(rep.render_report(recs, SIX_MARKS, "2026-10-21", broker_cols=True)) == PIN6_BROKER
    assert _sha(spread_html.render_report_html(recs, SIX_MARKS, "2026-10-21")) == PIN6_HTML


BULLCALL_SECRETS = ("ZQBC", "101.9", "1.9", "310", "bull_call_spread", "Call 借方价差", "100/105")


def test_public_report_unchanged_with_bullcall_records_present():
    old = _six_old_ledger()
    bullcalls = [_bullcall(30, status="open"), _bullcall(31, pnl=310.0),
                 _bullcall(32, status="closed_early", pnl=50.0),
                 _bullcall(33, pnl=-190.0, result="loss")]
    marks = {**SIX_MARKS, 30: _bc_mark()}
    base = rep.render_public_report(old, SIX_MARKS, "2026-10-24")
    more = rep.render_public_report(old + bullcalls, marks, "2026-10-24")
    assert more.encode("utf-8") == base.encode("utf-8")
    for token in BULLCALL_SECRETS:
        assert token not in more, token
    assert rep.public_records(bullcalls) == []
    assert "bull_call_spread" not in rep.PUBLIC_STRATEGIES


BC_HEAD = "## Call 借方价差（Bull Call Debit Spread）记录"


def _bc_three():
    # design section 10 journal example: +$110 (C=103) / +$310 (C=105) / -$190 (C=99)
    return [_bullcall(41, pnl=110.0), _bullcall(42, pnl=310.0),
            _bullcall(43, pnl=-190.0, result="loss")]


def _bc_section(out: str) -> str:
    return out.split(BC_HEAD)[1].split("---\n*口径")[0]


def test_capital_at_risk_bullcall_is_debit():
    assert rep.capital_at_risk(_bullcall(1, status="open")) == pytest.approx(190.0)   # 1.90 x 100
    assert rep.capital_at_risk(_bullcall(1, status="open", contracts=2)) == pytest.approx(380.0)
    assert rep.pnl_base(_bullcall(1)) == rep.capital_at_risk(_bullcall(1))


def test_legs_label_bullcall():
    assert rep.legs_label(_bullcall(1)) == "100/105 Call"           # long / short
    assert rep.legs_label(_bullcall(1, long_call_strike=97.5, short_call_strike=102.5)) \
        == "97.5/102.5 Call"


def test_open_summary_labels_bullcall_capital_separately():
    recs = [_s(1, status="open"), _bullcall(2, status="open")]       # $500 spread + $190 debit
    line = rep._open_summary(recs, {1: _mk(-20.0), 2: _bc_mark(50.0)})[0]
    # (-20 + 50) / 690 = +4.3%
    assert line == "未平仓占用资金合计 $690（Put 贷方价差 $500 / Call 借方价差 $190）；浮盈亏合计 +$30（+4.3%）"
    only = rep._open_summary([_bullcall(2, status="open")], {2: _bc_mark(50.0)})[0]
    assert only == "未平仓占用资金合计 $190；浮盈亏合计 +$50（+26.3%）"        # 50 / 190


def _bc_mix():
    return [_bullcall(1, status="open"),
            _bullcall(2, pnl=110.0),                                    # C=103
            _bullcall(3, pnl=310.0),                                    # C=105
            _bullcall(4, pnl=-190.0, result="loss"),                    # C=99
            _bullcall(5, pnl=0.0, result="loss"),                       # C=101.90, breakeven
            _bullcall(6, status="closed_early", pnl=50.0,               # long 4.00 / short 1.60
                      exit_long_call_mid=4.0, exit_short_call_mid=1.6)]


def test_report_has_bullcall_section_with_hand_numbers():
    marks = {1: _bc_mark(50.0, tp=False)}
    out = rep.render_report(_bc_mix(), marks, "2026-10-21", broker_cols=True)
    sec = _bc_section(out)
    assert "100/105 Call" in sec and "$1.90" in sec and "101.90" in sec
    assert "+$310 / -$190" in sec and "38%" in sec                  # 1.90 / 5
    assert "+1.9%" in sec                                           # breakeven move 0.019 x 100
    assert "+$50" in sec and "+26.3%" in sec and "2.3%" in sec      # open mark, 50 / 190
    row2 = [ln for ln in sec.splitlines() if ln.startswith("| 2 ")][0]
    assert "win" in row2 and "+$110" in row2 and "+57.9%" in row2 and "+35.5%" in row2
    row4 = [ln for ln in sec.splitlines() if ln.startswith("| 4 ")][0]
    assert "-$190" in row4 and "-100.0%" in row4 and "-61.3%" in row4
    row5 = [ln for ln in sec.splitlines() if ln.startswith("| 5 ")][0]
    assert "loss" in row5 and "| $0 |" in row5 and "+0.0%" in row5
    row6 = [ln for ln in sec.splitlines() if ln.startswith("| 6 ")][0]
    assert "提前平仓" in row6 and "+$50" in row6 and "+16.1%" in row6  # 50 / 310
    assert "-$0" not in sec and "-0.0%" not in sec
    assert "实际盈亏比" in sec and "口径不同" in sec
    assert "Call 借方价差=" in out.split("---\n*口径")[1]


def test_bullcall_settled_negative_zero_never_renders():
    out = rep.render_report([_bullcall(1, pnl=-0.0, result="loss", fraction_of_max_captured=-0.0)],
                            None, "2026-10-21")
    assert "-$0" not in out and "-0.0%" not in out


def test_bullcall_not_in_compute_stats_or_delta_buckets():
    old = _old_ledger()
    new = old + _bc_mix()
    assert rep.compute_stats(new) == rep.compute_stats(old)
    assert rep.compute_stats(_bc_mix())["n_settled"] == 0
    assert "bull_call_spread" not in rep.SINGLE_SPREAD_STATS
    before = rep.render_report(old, OLD_MARKS, "2026-10-21")
    after = rep.render_report(new, OLD_MARKS, "2026-10-21")
    seg = lambda t: t.split("## 已结算")[1].split("## 按策略对比")[0]
    assert seg(after) == seg(before) and "ZQBC" not in seg(after)
    table = lambda t: [ln for ln in t.split("## 未平仓")[1].split("## 已结算")[0].splitlines()
                       if ln.startswith("|")]
    assert table(after) == table(before) and table(before)


def test_by_strategy_table_has_bullcall_row_and_required_note():
    sec = rep.render_report(_old_ledger() + _bc_three(), None, "2026-10-21")
    sec = sec.split("## 按策略对比")[1].split("## 现金担保")[0]
    # 2/3 = 67%; avg win (110 + 310) / 2 = 210; actual breakeven 190 / (210 + 190) = 47.5% -> 48%
    assert "| bull_call_spread | 3 | 67% | +$230 | +$210 | -$190 | 48% | ✅ |" in sec
    note = [ln for ln in sec.splitlines() if "所需胜率" in ln][0]
    assert "38%" in note and "逐笔平均" in note and "口径不同" in note


def test_bullcall_captured_avg_winners_only():
    import spread_report_bullcall as bc
    recs = _bc_three() + [_bullcall(44, status="open"), _condor(45, pnl=150.0)]
    assert bc.bullcall_captured_avg(recs) == pytest.approx(0.6774)   # (0.3548 + 1.0) / 2
    assert bc.bullcall_required_avg(recs) == pytest.approx(0.38)     # 1.90 / 5 each
    assert bc.bullcall_captured_avg([_bullcall(1, pnl=-190.0, result="loss")]) is None
    assert bc.bullcall_required_avg([_bullcall(1, status="open")]) is None
    sec = _bc_section(rep.render_report(_bc_three(), None, "2026-10-21"))
    summary = [ln for ln in sec.splitlines() if ln.startswith("- 已结算")][0]
    # winners' captured average is unsigned (always >= 0); win rate like the condor block
    assert "最大收益的 67.7%" in summary and "+67.7%" not in summary
    assert "胜率 67%" in summary and "+$230" in summary


def test_bullcall_note_says_at_or_below_long_strike():
    import spread_report_bullcall as bc
    assert "长腿行权价或以下" in bc.BULLCALL_NOTE and "长腿行权价之下" not in bc.BULLCALL_NOTE


def test_bullcall_required_note_only_open_says_none_settled():
    import spread_report_bullcall as bc
    only_open = [_bullcall(1, status="open")]
    note = "".join(bc.bullcall_required_note(only_open))
    assert "暂无已结算" in note and "—" not in note
    page = spread_html.render_report_html(only_open, None, "2026-10-21")
    html_note = page.split("bull_call_spread 所需胜率")[1].split("</div>")[0]
    assert "暂无已结算" in html_note and "—" not in html_note
    settled = "".join(bc.bullcall_required_note(_bc_three()))
    assert "38%" in settled and "暂无已结算" not in settled


def test_bullcall_small_sample_warning():
    out = rep.render_report(_bc_three(), None, "2026-10-21")
    assert "bull_call_spread 样本不足（3 < 30）" in out
    assert "样本不足（3 < 30）" in _bc_section(out)


def test_no_breached_side_column_for_bullcall():
    marks = {1: _bc_mark()}
    md = rep.render_report(_bc_mix(), marks, "2026-10-21")
    page = spread_html.render_report_html(_bc_mix(), marks, "2026-10-21")
    for text in (md, page):
        assert "突破侧" not in text and "breached" not in text


def test_unknown_strategy_still_counts_nowhere_with_bullcall():
    b = _bullcall(1, status="open")
    assert "future_thing" not in rep.compute_strategy_stats([UNKNOWN, _bullcall(2)])
    assert rep.render_report([b, UNKNOWN], None, "2026-10-21") == rep.render_report([b], None, "2026-10-21")
    assert rep._open_summary([UNKNOWN, b], {})[0].startswith("未平仓占用资金合计 $190")
    only = rep.render_report([UNKNOWN], None, "2026-10-21")
    assert "ZZZU" not in only and "Call 借方价差" not in only


def test_write_report_with_bullcall_private_has_section_public_does_not(tmp_path):
    import spread_sim
    from datetime import date
    root = tmp_path / "data" / "paper-trading"
    root.mkdir(parents=True)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    ledger = {"spreads": [_s(1, pnl=100.0), _bullcall(2, pnl=310.0),
                          _bullcall(3, status="closed_early", pnl=50.0)]}
    spread_sim._write_report(root, ledger, {}, date(2026, 10, 24))
    md = (root / "report.md").read_text(encoding="utf-8")
    page = (root / "report.html").read_text(encoding="utf-8")
    assert BC_HEAD in md and "| 2 | ZQBC |" in md and "| 3 | ZQBC |" in md
    assert "100/105 Call" in page and "+$310" in page
    public = (tmp_path / "wiki" / "options" / "paper-trading.md").read_text(encoding="utf-8")
    assert public == rep.render_public_report([_s(1, pnl=100.0)], {}, "2026-10-24")
