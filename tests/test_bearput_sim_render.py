"""Tests for scripts/bearput_sim_render.py - pure markdown / json rendering.

Invented ticker ZQBP. Hand-computed example (bearish design section 8): spot 100, buy 100 Put
mid 4.80, sell 90 Put mid 1.80, width 10 -> debit 3.00/share, max loss $300, max profit
(10 - 3.00) x 100 = $700, breakeven 97.00, required win rate 3.00 / 10 = 30%, breakeven move
-3.00%, max-profit move (down to the short strike) -10.00%.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bearcall_sim_rules
import bearput_sim_render as render
import bearput_sim_rules


def _ok(bucket=0.50, **over):
    c = {"delta_bucket": bucket, "status": "ok",
         "long_put_strike": 100.0, "short_put_strike": 90.0, "width": 10.0,
         "long_put_mid": 4.80, "short_put_mid": 1.80, "debit": 3.00, "breakeven": 97.0,
         "breakeven_move_pct": -0.03, "max_profit_move_pct": -0.10,
         "long_put_delta": 0.50, "short_put_delta": 0.30,
         "long_put_iv": 0.40, "short_put_iv": None, "underlying_price": 100.0,
         "max_profit": 700.0, "max_loss": 300.0, "required_win_rate": 0.30,
         "warnings": [bearput_sim_rules.WARN_FULL_DEBIT_BEARPUT, bearcall_sim_rules.WARN_DRIFT,
                      "到期前有财报｜示例"]}
    c.update(over)
    return c


def _doc(candidates=None, **over):
    doc = {"version": 1, "strategy": "bear_put_spread", "generated_on": "2026-10-17",
           "data_source": "IBKR", "frozen": True, "intraday": False,
           "ticker": "ZQBP", "snapshot_date": "2026-10-16", "spot": 100.0,
           "expiry": "2026-11-20", "days": 35, "min_days": 30, "earnings": "no",
           "ex_dividend": None, "width": 10.0, "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               _ok(0.60, long_put_strike=105.0, short_put_strike=95.0),
               _ok(),
               {"delta_bucket": 0.40, "status": "unavailable",
                "reason": "下方没有等宽的短腿（需要 Put 85 的有效报价）"}]}
    doc.update(over)
    return doc


def test_render_summary_shows_required_columns():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for col in ("标的", "档位", "长 Put", "短 Put", "宽度", "长腿中间价", "短腿中间价",
                "净支出/股", "盈亏平衡", "最大收益/张", "最大亏损/张", "所需胜率",
                "回本幅度", "满额幅度", "提示"):
        assert col in text, col
    row = next(line for line in text.splitlines() if line.startswith("| ZQBP | 0.50 |"))
    for value in ("| 100 |", "| 90 |", "$10", "$4.80", "$1.80", "$3.00", "97.00", "$700",
                  "$300", "30%", "-3.00%", "-10.00%"):
        assert value in row, value
    assert row.count("|") == 16                             # 15 columns
    assert "到期前有财报｜示例" in row                      # pipe stays inside the cell
    for fixed in (bearput_sim_rules.WARN_FULL_DEBIT_BEARPUT, bearcall_sim_rules.WARN_DRIFT):
        assert fixed not in row                             # shown once above the table instead
        assert fixed in text
    rejected = next(line for line in text.splitlines() if line.startswith("| ZQBP | 0.40 |"))
    assert rejected.count("|") == 16 and "剔除" in rejected
    assert "ZQBP Delta 0.40 — 下方没有等宽的短腿" in text


def test_render_summary_lists_unreadable_files():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17",
                                    ["2026-10-16-ZQBS-bearput.json"])
    assert "2026-10-16-ZQBS-bearput.json" in text


def test_render_summary_without_docs():
    text = render.render_summary_md([], "2026-10-16", "2026-10-17")
    assert "无候选" in text


def test_render_candidate_md_hand_numbers_and_warnings():
    md = render.render_candidate_md(_doc())
    assert "ZQBP" in md and "2026-11-20" in md and "35 天" in md and "至少 30 天" in md
    for value in ("买 Put 100", "卖 Put 90", "$4.80", "$1.80", "$3.00/股", "$300",
                  "$700", "97.00", "-3.00%", "-10.00%", "30%"):
        assert value in md, value
    for fixed in (bearput_sim_rules.WARN_FULL_DEBIT_BEARPUT, bearcall_sim_rules.WARN_DRIFT):
        assert md.count(fixed) == 1                         # banner only, not per bucket
        assert f"- 提示：{fixed}" not in md
    assert "- 提示：到期前有财报｜示例" in md               # other warnings still per bucket
    assert "下方没有等宽的短腿（需要 Put 85 的有效报价）" in md
    assert "数据源 IBKR" in md


def test_render_candidate_md_open_commands_carry_manual_width():
    md = render.render_candidate_md(_doc())
    assert f"{render.OPEN_CMD} ZQBP 0.60 --width 10" in md
    assert f"{render.OPEN_CMD} ZQBP 0.50 --width 10" in md
    assert f"{render.OPEN_CMD} ZQBP 0.40" not in md
    auto = render.render_candidate_md(_doc(width_source="auto"))
    assert f"{render.OPEN_CMD} ZQBP 0.50\n" in auto


def test_render_candidate_md_all_rejected():
    doc = _doc(candidates=[{"delta_bucket": b, "status": "unavailable", "reason": "无可用的长 Put"}
                           for b in (0.60, 0.50, 0.40)])
    md = render.render_candidate_md(doc)
    assert "三档都被剔除" in md and render.OPEN_CMD not in md


def test_render_shared_long_strike_note():
    doc = _doc(candidates=[_ok(0.60), _ok(0.50),
                           {"delta_bucket": 0.40, "status": "unavailable", "reason": "x"}])
    md = render.render_candidate_md(doc)
    summary = render.render_summary_md([doc], "2026-10-16", "2026-10-17")
    for text in (md, summary):
        assert "0.60 / 0.50 档选到同一长腿 Put 100" in text
    assert "同一长腿" not in render.render_candidate_md(_doc())


def test_render_negative_move_has_no_negative_zero():
    c = _ok(breakeven_move_pct=-0.0000001, max_profit_move_pct=-0.012)
    text = render.render_summary_md([_doc(candidates=[c])], "2026-10-16", "2026-10-17")
    assert "-0.00%" not in text and "+0.00%" in text and "-1.20%" in text


def test_candidate_to_json_marks_frozen_and_strategy():
    doc = render.candidate_to_json({"ticker": "ZQBP", "intraday": False, "candidates": []},
                                   "2026-10-17")
    assert doc["strategy"] == "bear_put_spread" and doc["version"] == render.VERSION
    assert doc["frozen"] is True and doc["data_source"] == "IBKR"
    live = render.candidate_to_json({"ticker": "ZQBP", "intraday": True}, "2026-10-17")
    assert live["frozen"] is False and live["intraday"] is True


def test_render_has_no_seller_wording():
    md = render.render_candidate_md(_doc())
    summary = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for text in (md, summary):
        assert "贷方" not in text and "权利金" not in text and "Call" not in text
        assert "Put 借方价差" in text and "看跌" in text
