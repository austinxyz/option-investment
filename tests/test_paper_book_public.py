"""Tests for scripts/paper_book_public.py: the public paper-trading page built from the book layers.

Pure rendering, no network. Tickers are invented (ZZZ*). Hand numbers next to each fixture.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import option_settings
import paper_book_public as pub
import paper_book_render
import spread_ledger as L
import spread_report

DAY = "2026-10-24"


def _mk(pnl, dist=2.0, **flags):
    return {"pnl": pnl, "dist_pct": dist, "stale": False,
            "flags": {"stop": flags.get("stop", False), "take_profit": flags.get("tp", False)}}


def ledger() -> list[dict]:
    """#1 bull put 100/95 credit 1.00 x1, expired +100, bucket 0.10, gap +0.50%   capital 400
    #2 bull put 50/45  credit 0.50 x2, OPEN (mark -30, 4.2% away)                   capital 900
    #3 CSP 40          credit 2.00 x1, closed early +200                            capital 4000
    realized 300; floating -30; total 270; win 2/2; settled capital 4400 -> +6.8%
    counted 4400 + 900 = 5300 -> 270/5300 = +5.1%; peak 10-09: 900 + 4000 = 4900"""
    return [
        {"id": 1, "ticker": "ZZZQ", "status": "expired", "contracts": 1, "short_strike": 100.0,
         "long_strike": 95.0, "width": 5.0, "credit": 1.00, "snapshot_date": "2026-10-02",
         "expiry": "2026-10-09", "exit_date": "2026-10-09", "pnl": 100.0, "result": "win",
         "delta_bucket": 0.10, "monday_gap_pct": 0.5},
        {"id": 2, "ticker": "ZZZR", "status": "open", "contracts": 2, "short_strike": 50.0,
         "long_strike": 45.0, "width": 5.0, "credit": 0.50, "snapshot_date": "2026-10-09",
         "expiry": "2026-10-30", "pnl": None, "result": None, "delta_bucket": 0.15},
        {"id": 3, "ticker": "ZZZS", "strategy": L.STRATEGY_CSP, "status": "closed_early",
         "contracts": 1, "short_strike": 40.0, "long_strike": None, "width": None, "credit": 2.00,
         "snapshot_date": "2026-10-09", "expiry": "2026-10-16", "exit_date": "2026-10-12",
         "pnl": 200.0, "result": "win"},
    ]


MARKS = {2: _mk(-30.0, dist=4.2)}
PRIVATE = [
    {"id": 8, "ticker": "ZZZP", "strategy": L.STRATEGY_COLLAR, "status": "open", "contracts": 3,
     "put_strike": 71.5, "call_strike": 93.25, "net_credit": -0.37, "covered_shares": 4242,
     "cost_basis": 61.11, "snapshot_date": "2026-10-09", "expiry": "2026-11-20"},
    {"id": 9, "ticker": "ZZZC", "strategy": L.STRATEGY_COVERED_CALL, "status": "expired",
     "contracts": 1, "short_strike": 120.0, "credit": 1.2, "cost_basis": 87.65, "pnl": 120.0,
     "result": "expired_worthless", "snapshot_date": "2026-10-02", "expiry": "2026-10-09",
     "exit_date": "2026-10-09"},
]


def page(records=None, marks=MARKS) -> str:
    return pub.render_public_page(ledger() if records is None else records, marks, DAY)


def lines() -> list[str]:
    return page().split("\n")


def row(prefix: str, text: str | None = None) -> str:
    return next(s for s in (text or page()).split("\n") if s.startswith(prefix))


def test_front_matter_title_and_intro_unchanged():
    out = lines()
    assert out[:3] == ["---", f"source: {option_settings.public_source_label()}", "---"]
    assert out[4] == "# 模拟盘报告（垂直 Put 贷方价差与现金担保 Put 纸上交易）" == pub.PUBLIC_TITLE
    assert out[6].startswith(f"*数据快照 {DAY}，非实时。纸上交易，不含真实持仓；")
    assert "（备兑 Call、保护性 Put、Collar）不在此公开" in out[6]


def test_sections_in_order():
    heads = [s for s in lines() if s.startswith("## ")]
    assert heads == ["## 一、总览", "## 二、分策略", "## 三、每笔明细", "## 四、分档对比",
                     "## 五、开仓价假设的偏差"]


def test_overview_three_numbers_and_returns():
    text = page()
    assert row("| 已实现盈亏（已结算 2 笔） |", text) == "| 已实现盈亏（已结算 2 笔） | +$300 |"
    assert row("| 浮动盈亏（未平仓 1 笔） |", text) == "| 浮动盈亏（未平仓 1 笔） | -$30 |"
    assert row("| 合计 |", text) == "| 合计 | +$270 |"
    assert row("| 胜率 |", text) == "| 胜率 | 100%（2/2，盈亏>0 算赢） |"
    assert row("| 当前占用资金 |", text) == "| 当前占用资金 | $900 |"
    assert row("| 历史最大同时占用资金 |", text) == "| 历史最大同时占用资金 | $4,900（2026-10-09 起） |"
    assert "| 已实现收益率 | +6.8%" in text and "| 合计收益率 | +5.1%" in text


def test_strategy_rows_and_total_row():
    text = page()
    assert row("| Bull Put 价差 |", text) == \
        "| Bull Put 价差 | 1 | 1 | 100% | +$100 | -$30 | +$70 | $1,300 | +5.4% |"
    assert row("| 现金担保 Put |", text) == \
        "| 现金担保 Put | 1 | 0 | 100% | +$200 | — | +$200 | $4,000 | +5.0% |"
    assert row("| **合计** |", text) == \
        "| **合计** | 2 | 1 | 100% | +$300 | -$30 | +$270 | $5,300 | +5.1% |"


def test_breakeven_verdict_and_sample_warning_per_strategy():
    text = page()
    assert "> Bull Put 价差：按实际盈亏比算的盈亏平衡胜率 —；暂无亏损（或盈利）样本，无法比较。" in text
    assert "> ⚠️ Bull Put 价差 样本不足（1 < 30），别下结论。" in text
    assert "> ⚠️ 现金担保 Put 样本不足（1 < 30），别下结论。" in text


def test_breakeven_verdict_with_a_loss():
    recs = ledger()[:1] + [{**ledger()[0], "id": 4, "pnl": -300.0, "result": "loss"}]
    text = page(recs, {})
    assert "> Bull Put 价差：按实际盈亏比算的盈亏平衡胜率 75%；❌ 实际胜率低于盈亏平衡胜率。" in text


def test_detail_rows_grouped_and_carry_old_columns():
    text = page()
    head = row("| # |", text)
    for col in ("档位", "张数", "距短腿", "提示", "次日开盘跳空", "盈亏%", "开仓现金流"):
        assert f" {col} " in head, col
    ids = [s.split("|")[1].strip() for s in text.split("## 三、每笔明细")[1].split("## 四")[0]
           .split("\n") if s.startswith("| ") and s.split("|")[1].strip().isdigit()]
    assert ids == ["1", "2", "3"]
    assert row("| 1 |", text) == ("| 1 | ZZZQ | Bull Put 价差 | 100/95 | 0.10 | 2026-10-02 | 2026-10-09 "
                                  "| 1 张 | 收入 $1.00/股（$100.00/张） | $400 | 到期结算 | 2026-10-09 "
                                  "| 盈利 | +$100 | +25.0% | — | — | +0.50% |")
    assert row("| 2 |", text) == ("| 2 | ZZZR | Bull Put 价差 | 50/45 | 0.15 | 2026-10-09 | 2026-10-30 "
                                  "| 2 张 | 收入 $0.50/股（$50.00/张） | $900 | 未平仓 | — | — | -$30 "
                                  "| -3.3% | 4.2% | — | — |")
    assert row("| 3 |", text).startswith("| 3 | ZZZS | 现金担保 Put | 40 Put | — | 2026-10-09 ")


def test_open_trade_tips_use_the_strategy_basis():
    text = page(marks={2: _mk(80.0, tp=True)})
    assert spread_report.threshold_labels(False)[1] in row("| 2 |", text)


def test_open_without_mark_shows_dash_like_today():
    other = {**ledger()[1], "id": 4, "snapshot_date": "2026-10-10"}
    text = page(ledger() + [other], {4: _mk(5.0)})  # a status run, but no mark for #2
    assert row("| 2 |", text).split("|")[14].strip() == "—"
    assert "运行 status 查看" in row("| 2 |", text)
    assert "（另有 1 笔行情缺失，未计入）" in row("| 浮动盈亏", text)


def test_open_with_error_mark_shows_dash_and_reason():
    text = page(marks={2: {"error": "没有拿到 ZZZR 2026-10-30 的期权报价"}})
    cells = [c.strip() for c in row("| 2 |", text).split("|")]
    assert cells[14] == "—" and cells[15] == "—"
    assert cells[17] == "没有拿到 ZZZR 2026-10-30 的期权报价"


def test_no_marks_and_nothing_open_shows_zero_floating():
    text = page([r for r in ledger() if r["status"] != "open"], {})
    assert row("| 浮动盈亏", text) == "| 浮动盈亏（未平仓 0 笔） | $0 |"


def test_no_marks_with_open_trade_is_offline_dash():
    text = page(marks={})
    assert row("| 浮动盈亏", text) == f"| 浮动盈亏（未平仓 1 笔） | {paper_book_render.OFFLINE_FLOAT} |"


def test_bucket_and_gap_sections_are_bull_put_only():
    text = page()
    assert "| 0.10 | 1 | 100% | +$100 |" in text
    assert "平均绝对跳空：0.50%" in text


def test_non_public_records_change_nothing_and_never_appear():
    base = page()
    more = page(ledger() + PRIVATE, {**MARKS, 8: {"pnl": -35.0, "stale": False}})
    assert more.encode("utf-8") == base.encode("utf-8")
    for token in ("ZZZP", "ZZZC", "4242", "61.11", "71.5", "93.25", "87.65", "collar",
                  "covered_call", "铁鹰"):
        assert token not in more, token


def test_footer_defines_book_terms_without_withheld_strategies():
    foot = page().rstrip("\n").split("\n")[-1]
    assert foot.startswith("*口径：每张合约 ×100 的美元；中间价成交；不含手续费。")
    for term in ("已实现", "浮动", "合计收益率", paper_book_render.REALIZED_FORMULA):
        assert term in foot
    for token in ("备兑", "Collar", "铁鹰", "保护性"):
        assert token not in foot


def test_missing_credit_counts_as_zero_and_does_not_raise():
    rec = {k: v for k, v in ledger()[0].items() if k != "credit"}
    assert "| 1 | ZZZQ |" in page([rec], {})


def test_empty_ledger_renders():
    text = page([], {})
    assert "## 一、总览" in text and "| 合计 | $0 |" in text


def test_spread_report_delegates_to_this_module():
    assert spread_report.render_public_report(ledger(), MARKS, DAY) == page()


def test_book_overview_default_win_note_unchanged():
    t = {"win_rate": 0.5, "wins": 1, "n_win_sample": 2}
    assert paper_book_render._win_cell(t) == f"50%（1/2，{paper_book_render.WIN_NOTE}）"
