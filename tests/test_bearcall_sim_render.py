"""Tests for scripts/bearcall_sim_render.py - pure markdown / json rendering.

Invented ticker ZQBR. Hand-computed example (bearish design section 8): spot 100, sell 105 Call
mid 2.10, buy 110 Call mid 0.90, width 5 -> credit 1.20/share, max profit $120, max loss
(5 - 1.20) x 100 = $380, breakeven 106.20, required win rate 1 - 1.20 / 5 = 76%, breakeven
move +6.20%, max-loss move (up to the long strike) +10.00%.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bearcall_sim_render as render
import bearcall_sim_rules


def _ok(bucket=0.15, **over):
    c = {"delta_bucket": bucket, "status": "ok", "option_type": "C",
         "short_strike": 105.0, "long_strike": 110.0, "width": 5.0,
         "short_mid": 2.10, "long_mid": 0.90, "credit": 1.20, "breakeven": 106.2,
         "breakeven_move_pct": 0.062, "max_loss_move_pct": 0.10,
         "short_delta": 0.15, "short_iv": 0.38, "short_spread_pct": 0.0095,
         "long_spread_pct": 0.0222, "underlying_price": 100.0,
         "max_profit": 120.0, "max_loss": 380.0, "required_win_rate": 0.76,
         "warnings": [bearcall_sim_rules.WARN_BEAR_CALL_UPSIDE, bearcall_sim_rules.WARN_DRIFT,
                      "到期前有财报｜示例"]}
    c.update(over)
    return c


def _doc(candidates=None, **over):
    doc = {"version": 1, "strategy": "bear_call_spread", "generated_on": "2026-10-17",
           "data_source": "IBKR", "frozen": True, "intraday": False,
           "ticker": "ZQBR", "snapshot_date": "2026-10-16", "spot": 100.0,
           "expiry": "2026-10-23", "days": 7, "earnings": "no",
           "ex_dividend": None, "width": 5.0, "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               {"delta_bucket": 0.10, "status": "unavailable",
                "reason": "上方没有等宽的长腿（需要 Call 111 的有效报价）"},
               _ok(),
               _ok(0.20, short_strike=104.0, long_strike=109.0)]}
    doc.update(over)
    return doc


def test_render_summary_shows_required_columns():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for col in ("标的", "档位", "短 Call", "长 Call", "宽度", "短腿中间价", "长腿中间价",
                "权利金/股", "盈亏平衡", "最大收益/张", "最大亏损/张", "所需胜率",
                "回本幅度", "满额亏损幅度", "提示"):
        assert col in text, col
    row = next(line for line in text.splitlines() if line.startswith("| ZQBR | 0.15 |"))
    for value in ("| 105 |", "| 110 |", "$5", "$2.10", "$0.90", "$1.20", "106.20", "$120",
                  "$380", "76%", "+6.20%", "+10.00%"):
        assert value in row, value
    assert row.count("|") == 16                             # 15 columns
    assert "到期前有财报｜示例" in row                      # pipe stays inside the cell
    for fixed in (bearcall_sim_rules.WARN_BEAR_CALL_UPSIDE, bearcall_sim_rules.WARN_DRIFT):
        assert fixed not in row                             # shown once above the table instead
        assert fixed in text
    rejected = next(line for line in text.splitlines() if line.startswith("| ZQBR | 0.10 |"))
    assert rejected.count("|") == 16 and "剔除" in rejected
    assert "ZQBR Delta 0.10 — 上方没有等宽的长腿" in text


def test_render_summary_lists_unreadable_files():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17",
                                    ["2026-10-16-ZQBS-bearcall.json"])
    assert "2026-10-16-ZQBS-bearcall.json" in text


def test_render_summary_without_docs():
    text = render.render_summary_md([], "2026-10-16", "2026-10-17")
    assert "无候选" in text


def test_render_candidate_md_hand_numbers_and_warnings():
    md = render.render_candidate_md(_doc())
    assert "ZQBR" in md and "2026-10-23" in md and "7 天" in md
    for value in ("卖 Call 105", "买 Call 110", "$2.10", "$0.90", "$1.20/股", "$380",
                  "$120", "106.20", "+6.20%", "+10.00%", "76%"):
        assert value in md, value
    for fixed in (bearcall_sim_rules.WARN_BEAR_CALL_UPSIDE, bearcall_sim_rules.WARN_DRIFT):
        assert md.count(fixed) == 1                         # banner only, not per bucket
        assert f"- 提示：{fixed}" not in md
    assert "- 提示：到期前有财报｜示例" in md               # other warnings still per bucket
    assert "上方没有等宽的长腿（需要 Call 111 的有效报价）" in md
    assert "数据源 IBKR" in md


def test_render_candidate_md_open_commands_carry_manual_width():
    md = render.render_candidate_md(_doc())
    assert f"{render.OPEN_CMD} ZQBR 0.15 --width 5" in md
    assert f"{render.OPEN_CMD} ZQBR 0.20 --width 5" in md
    assert f"{render.OPEN_CMD} ZQBR 0.10" not in md
    auto = render.render_candidate_md(_doc(width_source="auto"))
    assert f"{render.OPEN_CMD} ZQBR 0.15\n" in auto


def test_render_candidate_md_all_rejected():
    doc = _doc(candidates=[{"delta_bucket": b, "status": "unavailable", "reason": "无可用的短 Call"}
                           for b in (0.10, 0.15, 0.20)])
    md = render.render_candidate_md(doc)
    assert "三档都被剔除" in md and render.OPEN_CMD not in md


def test_render_shared_short_strike_note():
    doc = _doc(candidates=[_ok(0.10), _ok(0.15),
                           {"delta_bucket": 0.20, "status": "unavailable", "reason": "x"}])
    md = render.render_candidate_md(doc)
    summary = render.render_summary_md([doc], "2026-10-16", "2026-10-17")
    for text in (md, summary):
        assert "0.10 / 0.15 档选到同一短腿 Call 105" in text
    assert "同一短腿" not in render.render_candidate_md(_doc())


def test_render_negative_move_has_no_negative_zero():
    c = _ok(breakeven_move_pct=-0.0000001, max_loss_move_pct=-0.012)
    text = render.render_summary_md([_doc(candidates=[c])], "2026-10-16", "2026-10-17")
    assert "-0.00%" not in text and "+0.00%" in text and "-1.20%" in text


def test_candidate_to_json_marks_frozen_and_strategy():
    doc = render.candidate_to_json({"ticker": "ZQBR", "intraday": False, "candidates": []},
                                   "2026-10-17")
    assert doc["strategy"] == "bear_call_spread" and doc["version"] == render.VERSION
    assert doc["frozen"] is True and doc["data_source"] == "IBKR"
    live = render.candidate_to_json({"ticker": "ZQBR", "intraday": True}, "2026-10-17")
    assert live["frozen"] is False and live["intraday"] is True


def test_render_has_no_bull_call_wording():
    md = render.render_candidate_md(_doc())
    assert "借方" not in md and "净支出" not in md
    assert "Call 贷方价差" in md
