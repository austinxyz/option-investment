"""Tests for scripts/condor_sim_render.py - pure markdown / json rendering.

Invented ticker ZZZQ. Hand-computed example: 85/90 put side, 110/115 call side, width 5,
put credit 0.60 + call credit 0.90 = net 1.50/share; breakevens 88.50 / 111.50;
max loss (5 - 1.50) x 100 = $350 per contract; required win rate 3.5 / 5 = 70%.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import condor_sim_render as render
import condor_sim_rules


def _ok(bucket=0.15, **over):
    c = {"delta_bucket": bucket, "status": "ok",
         "warnings": [condor_sim_rules.WARN_UP_MOVE, "长翼 Call 115 买卖价差 20% > 15%"],
         "put_short_strike": 90.0, "put_long_strike": 85.0,
         "call_short_strike": 110.0, "call_long_strike": 115.0, "width": 5.0,
         "put_credit": 0.60, "call_credit": 0.90, "net_credit": 1.50,
         "breakeven_low": 88.5, "breakeven_high": 111.5,
         "put_short_delta": 0.15, "call_short_delta": 0.14,
         "put_short_iv": 0.40, "call_short_iv": 0.38, "underlying_price": 100.0,
         "max_loss": 350.0, "required_win_rate": 0.7}
    c.update(over)
    return c


def _doc(intraday=False):
    return {"version": 1, "strategy": "iron_condor", "generated_on": "2026-10-17",
            "data_source": "IBKR", "frozen": not intraday, "intraday": intraday,
            "ticker": "ZZZQ", "snapshot_date": "2026-10-16", "spot": 100.0,
            "expiry": "2026-10-23", "days": 7, "earnings": "no", "ex_dividend": None,
            "width": 5.0, "width_source": "manual",
            "candidates": [_ok(0.10), _ok(0.15),
                           {"delta_bucket": 0.20, "status": "unavailable",
                            "reason": "上方没有等宽的长腿（需要 Call 109 的有效报价）"}]}


def test_render_summary_shows_required_columns():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for col in ("档位", "长 Put", "短 Put", "短 Call", "长 Call", "Put 侧权利金", "Call 侧权利金",
                "净权利金", "盈亏平衡", "最大亏损", "占用资金", "所需胜率", "提示"):
        assert col in text, col
    assert "| ZZZQ | 0.15 | 85 | 90 | 110 | 115 |" in text
    assert "$0.60" in text and "$0.90" in text and "$1.50" in text
    assert "88.50 / 111.50" in text and "$350" in text and "70%" in text
    assert "剔除：上方没有等宽的长腿（需要 Call 109 的有效报价）" in text
    assert "大涨对铁鹰可能是满额亏损" in text
    assert "数据源 IBKR" in text and "冻结行情" in text


def test_render_summary_pipe_in_text_does_not_split_rows():
    doc = _doc()
    doc["candidates"][2]["reason"] = "a|b"
    assert "a｜b" in render.render_summary_md([doc], "2026-10-16", "2026-10-17")


def test_render_summary_lists_unreadable_files():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17",
                                    unreadable=["2026-10-16-ZZZR-condor.json"])
    assert "2026-10-16-ZZZR-condor.json" in text


def test_render_candidate_md_has_every_bucket_detail():
    text = render.render_candidate_md(_doc())
    assert "85/90/110/115" in text
    assert "Put 侧权利金 $0.60/股" in text and "Call 侧权利金 $0.90/股" in text
    assert "净权利金 $1.50/股" in text and "88.50 / 111.50" in text
    assert "$350" in text and "70%" in text
    assert "剔除 — 上方没有等宽的长腿" in text
    assert "open ZZZQ 0.10" in text and "open ZZZQ 0.15" in text
    assert "open ZZZQ 0.20" not in text
    assert "长翼 Call 115 买卖价差 20% > 15%" in text
    assert "数据源 IBKR" in text and "2026-10-16" in text and "冻结行情" in text
    assert "大涨对铁鹰可能是满额亏损" in text


def test_render_candidate_md_marks_intraday_snapshot():
    text = render.render_candidate_md(_doc(intraday=True))
    assert "盘中" in text and "冻结行情" not in text


def test_candidate_to_json_labels_source_and_frozen():
    snap = {k: v for k, v in _doc().items()
            if k not in ("version", "strategy", "generated_on", "data_source", "frozen")}
    doc = render.candidate_to_json(snap, "2026-10-17")
    assert doc["version"] == 1 and doc["strategy"] == "iron_condor"
    assert doc["data_source"] == "IBKR" and doc["frozen"] is True
    assert doc["generated_on"] == "2026-10-17" and doc["snapshot_date"] == "2026-10-16"
