"""Tests for scripts/bullcall_sim_render.py - pure markdown / json rendering.

Invented ticker ZZZQ. Hand-computed example (design section 10): spot 100, buy 100 Call mid
3.20, sell 105 Call mid 1.30 -> debit 1.90/share, width 5, breakeven 101.90, max loss $190,
max profit (5 - 1.90) x 100 = $310, required win rate 1.90 / 5 = 38%, breakeven move +1.90%,
max-profit move +5.00%.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bullcall_sim_render as render
import bullcall_sim_rules


def _ok(bucket=0.50, **over):
    c = {"delta_bucket": bucket, "status": "ok",
         "long_call_strike": 100.0, "short_call_strike": 105.0, "width": 5.0,
         "long_call_mid": 3.20, "short_call_mid": 1.30, "debit": 1.90, "breakeven": 101.9,
         "breakeven_move_pct": 0.019, "max_profit_move_pct": 0.05,
         "long_call_delta": 0.50, "short_call_delta": 0.33,
         "long_call_iv": 0.40, "short_call_iv": 0.38, "underlying_price": 100.0,
         "max_profit": 310.0, "max_loss": 190.0, "required_win_rate": 0.38,
         "warnings": [bullcall_sim_rules.WARN_FULL_DEBIT, "到期前有财报｜示例"]}
    c.update(over)
    return c


def _doc(candidates=None, **over):
    doc = {"version": 1, "strategy": "bull_call_spread", "generated_on": "2026-10-17",
           "data_source": "IBKR", "frozen": True, "intraday": False,
           "ticker": "ZZZQ", "snapshot_date": "2026-10-16", "spot": 100.0,
           "expiry": "2026-11-20", "days": 35, "min_days": 30, "earnings": "no",
           "ex_dividend": None, "width": 5.0, "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               _ok(0.60, long_call_strike=97.0, short_call_strike=102.0),
               _ok(),
               {"delta_bucket": 0.40, "status": "unavailable",
                "reason": "上方没有等宽的短腿（需要 Call 108 的有效报价）"}]}
    doc.update(over)
    return doc


def test_render_summary_shows_required_columns():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for col in ("标的", "档位", "长 Call", "短 Call", "宽度", "长腿中间价", "短腿中间价",
                "净支出/股", "盈亏平衡", "最大收益/张", "最大亏损/张", "所需胜率",
                "回本幅度", "满额幅度", "提示"):
        assert col in text, col
    row = next(line for line in text.splitlines() if line.startswith("| ZZZQ | 0.50 |"))
    for value in ("| 100 |", "| 105 |", "$5", "$3.20", "$1.30", "$1.90", "101.90", "$310",
                  "$190", "38%", "+1.90%", "+5.00%"):
        assert value in row, value
    assert "到期前有财报｜示例" in row                    # pipe stays inside the cell
    assert bullcall_sim_rules.WARN_FULL_DEBIT not in row  # shown once above the table instead
    assert bullcall_sim_rules.WARN_FULL_DEBIT in text
    assert "ZZZQ Delta 0.40 — 上方没有等宽的短腿" in text


def test_render_summary_lists_unreadable_files():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17",
                                    ["2026-10-16-ZZZR-bullcall.json"])
    assert "2026-10-16-ZZZR-bullcall.json" in text


def test_render_candidate_md_hand_numbers_and_warnings():
    md = render.render_candidate_md(_doc())
    assert "ZZZQ" in md and "2026-11-20" in md and "35 天" in md
    for value in ("买 Call 100", "卖 Call 105", "$3.20", "$1.30", "$1.90/股", "$190",
                  "$310", "101.90", "+1.90%", "+5.00%", "38%"):
        assert value in md, value
    assert md.count(bullcall_sim_rules.WARN_FULL_DEBIT) == 1   # banner only, not per bucket
    assert f"- 提示：{bullcall_sim_rules.WARN_FULL_DEBIT}" not in md
    assert "- 提示：到期前有财报｜示例" in md                  # other warnings still per bucket
    assert "上方没有等宽的短腿（需要 Call 108 的有效报价）" in md
    assert "数据源 IBKR" in md


def test_render_candidate_md_open_commands_carry_manual_width():
    md = render.render_candidate_md(_doc())
    assert f"{render.OPEN_CMD} ZZZQ 0.60 --width 5" in md
    assert f"{render.OPEN_CMD} ZZZQ 0.50 --width 5" in md
    assert f"{render.OPEN_CMD} ZZZQ 0.40" not in md
    auto = render.render_candidate_md(_doc(width_source="auto"))
    assert f"{render.OPEN_CMD} ZZZQ 0.50\n" in auto


def test_render_candidate_md_all_rejected():
    doc = _doc(candidates=[{"delta_bucket": b, "status": "unavailable", "reason": "无可用的长 Call"}
                           for b in (0.60, 0.50, 0.40)])
    md = render.render_candidate_md(doc)
    assert "三档都被剔除" in md and render.OPEN_CMD not in md


def test_render_shared_long_strike_note():
    doc = _doc(candidates=[_ok(0.60), _ok(0.50),
                           {"delta_bucket": 0.40, "status": "unavailable", "reason": "x"}])
    md = render.render_candidate_md(doc)
    summary = render.render_summary_md([doc], "2026-10-16", "2026-10-17")
    for text in (md, summary):
        assert "0.60 / 0.50 档选到同一长腿 Call 100" in text
    assert "同一长腿" not in render.render_candidate_md(_doc())


def test_render_negative_move_has_no_negative_zero():
    c = _ok(breakeven_move_pct=-0.0000001, max_profit_move_pct=-0.012)
    text = render.render_summary_md([_doc(candidates=[c])], "2026-10-16", "2026-10-17")
    assert "-0.00%" not in text and "+0.00%" in text and "-1.20%" in text


def test_candidate_to_json_marks_frozen_and_strategy():
    doc = render.candidate_to_json({"ticker": "ZZZQ", "intraday": False, "candidates": []},
                                   "2026-10-17")
    assert doc["strategy"] == "bull_call_spread" and doc["version"] == render.VERSION
    assert doc["frozen"] is True and doc["data_source"] == "IBKR"
    live = render.candidate_to_json({"ticker": "ZZZQ", "intraday": True}, "2026-10-17")
    assert live["frozen"] is False and live["intraday"] is True
