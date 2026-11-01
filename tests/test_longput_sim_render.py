"""Tests for scripts/longput_sim_render.py - pure markdown / json rendering.

Invented ticker ZQLP. Hand-computed example (long put design section 3): spot 100, buy 95 Put
mid 2.40, 1 contract -> debit 2.40/share, max loss $240, breakeven 95 - 2.40 = 92.60,
breakeven move 92.60 / 100 - 1 = -7.40%, display max profit (95 - 2.40) x 100 = $9,260.
Take-profit line +$240 (1.00 x debit), stop line -$120 (0.50 x debit).
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import longput_sim_render as render
import longput_sim_rules


def _ok(bucket=0.40, **over):
    c = {"delta_bucket": bucket, "status": "ok",
         "put_strike": 95.0, "put_mid": 2.40, "debit": 2.40, "breakeven": 92.6,
         "breakeven_move_pct": -0.074, "put_delta": 0.40, "put_iv": 0.35,
         "underlying_price": 100.0, "max_profit": 9260.0, "max_loss": 240.0,
         "warnings": [longput_sim_rules.WARN_THETA_DECAY, "到期前有财报｜示例"]}
    c.update(over)
    return c


def _doc(candidates=None, **over):
    doc = {"version": 1, "strategy": "long_put", "generated_on": "2026-10-17",
           "data_source": "IBKR", "frozen": True, "intraday": False,
           "ticker": "ZQLP", "snapshot_date": "2026-10-16", "spot": 100.0,
           "expiry": "2026-11-20", "days": 35, "min_days": 30, "earnings": "no",
           "ex_dividend": None,
           "candidates": candidates if candidates is not None else [
               _ok(0.50, put_strike=100.0, put_mid=4.37, debit=4.37, breakeven=95.63,
                   breakeven_move_pct=-0.0437, put_delta=0.50, max_profit=9563.0,
                   max_loss=437.0),
               _ok(),
               {"delta_bucket": 0.30, "status": "unavailable",
                "reason": "Put 买卖价差 8.0% 超过 5%"}]}
    doc.update(over)
    return doc


def test_render_summary_shows_required_columns():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for col in ("标的", "档位", "Put 行权价", "中间价", "|Delta|", "净支出/股", "盈亏平衡",
                "最大亏损/张", "最大收益/张（跌到 0）", "回本幅度", "提示"):
        assert col in text, col
    row = next(line for line in text.splitlines() if line.startswith("| ZQLP | 0.40 |"))
    for value in ("| 95 |", "$2.40", "0.40", "92.60", "$240", "$9,260", "-7.40%"):
        assert value in row, value
    assert row.count("|") == 12                             # 11 columns
    assert "到期前有财报｜示例" in row                      # pipe stays inside the cell
    assert longput_sim_rules.WARN_THETA_DECAY not in row    # shown once above the table
    assert longput_sim_rules.WARN_THETA_DECAY in text
    rejected = next(line for line in text.splitlines() if line.startswith("| ZQLP | 0.30 |"))
    assert rejected.count("|") == 12 and "剔除" in rejected
    assert "ZQLP Delta 0.30 — Put 买卖价差 8.0% 超过 5%" in text


def test_render_summary_has_no_width_or_spread_columns():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for word in ("宽度", "短腿", "长腿", "所需胜率", "满额幅度"):
        assert word not in text.split("## 被剔除")[0].replace("无所需胜率", ""), word


def test_render_summary_lists_unreadable_files():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17",
                                    ["2026-10-16-ZQLS-longput.json"])
    assert "2026-10-16-ZQLS-longput.json" in text


def test_render_summary_without_docs():
    assert "无候选" in render.render_summary_md([], "2026-10-16", "2026-10-17")


def test_render_candidate_md_hand_numbers_and_warnings():
    md = render.render_candidate_md(_doc())
    assert "ZQLP" in md and "2026-11-20" in md and "35 天" in md and "至少 30 天" in md
    for value in ("买 Put 95", "$2.40/股", "$240", "$9,260", "92.60", "-7.40%",
                  "+$240", "-$120", "无所需胜率"):
        assert value in md, value
    assert md.count(longput_sim_rules.WARN_THETA_DECAY) == 1     # banner only
    assert f"- 提示：{longput_sim_rules.WARN_THETA_DECAY}" not in md
    assert "- 提示：到期前有财报｜示例" in md
    assert "Put 买卖价差 8.0% 超过 5%" in md
    assert "数据源 IBKR" in md
    assert "宽度" not in md and "短腿" not in md


def test_render_candidate_md_open_commands_have_no_width():
    md = render.render_candidate_md(_doc())
    assert f"{render.OPEN_CMD} ZQLP 0.50\n" in md
    assert f"{render.OPEN_CMD} ZQLP 0.40\n" in md
    assert f"{render.OPEN_CMD} ZQLP 0.30" not in md
    assert "--width" not in md


def test_render_candidate_md_all_rejected():
    doc = _doc(candidates=[{"delta_bucket": b, "status": "unavailable", "reason": "无可用的 Put"}
                           for b in (0.50, 0.40, 0.30)])
    md = render.render_candidate_md(doc)
    assert "三档都被剔除" in md and render.OPEN_CMD not in md


def test_render_shared_strike_note():
    doc = _doc(candidates=[_ok(0.50), _ok(0.40),
                           {"delta_bucket": 0.30, "status": "unavailable", "reason": "x"}])
    md = render.render_candidate_md(doc)
    summary = render.render_summary_md([doc], "2026-10-16", "2026-10-17")
    for text in (md, summary):
        assert "0.50 / 0.40 档选到同一 Put 95" in text
    assert "同一 Put" not in render.render_candidate_md(_doc())


def test_render_negative_move_has_no_negative_zero():
    c = _ok(breakeven_move_pct=-0.0000001)
    text = render.render_summary_md([_doc(candidates=[c])], "2026-10-16", "2026-10-17")
    assert "-0.00%" not in text and "+0.00%" in text


def test_candidate_to_json_marks_frozen_and_strategy():
    doc = render.candidate_to_json({"ticker": "ZQLP", "intraday": False, "candidates": []},
                                   "2026-10-17")
    assert doc["strategy"] == "long_put" and doc["version"] == render.VERSION
    assert doc["frozen"] is True and doc["data_source"] == "IBKR"
    live = render.candidate_to_json({"ticker": "ZQLP", "intraday": True}, "2026-10-17")
    assert live["frozen"] is False and live["intraday"] is True


def test_render_has_no_seller_wording():
    md = render.render_candidate_md(_doc())
    summary = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for text in (md, summary):
        assert "贷方" not in text and "借方价差" not in text and "Call" not in text
        assert "买入 Put" in text and "看跌" in text
