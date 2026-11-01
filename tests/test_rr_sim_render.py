"""Tests for scripts/rr_sim_render.py - pure markdown / json rendering.

Invented ticker ZZZQ. Hand-computed example (design section 8): spot 100, sell 90 Put mid 2.00,
buy 110 Call mid 1.50 -> net credit 0.50 (净收入), capital (90 - 0.50) x 100 = $8,950,
downside breakeven 89.50, dead zone 90 - 110, put move -10.00%, call move +10.00%.
Net debit example: Put 1.20 / Call 1.80 -> -0.60 (净支出), capital $9,060, upside breakeven 110.60.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import rr_sim_render as render
import rr_sim_rules

FIXED = rr_sim_rules.FIXED_WARNINGS


def _ok(bucket=0.30, **over):
    c = {"delta_bucket": bucket, "status": "ok",
         "short_put_strike": 90.0, "long_call_strike": 110.0, "short_put_mid": 2.0,
         "long_call_mid": 1.5, "net_credit": 0.5, "breakeven_down": 89.5,
         "put_move_pct": -0.1, "call_move_pct": 0.1, "capital_basis": 8950.0,
         "upside_breakeven": None, "dead_zone": [90.0, 110.0], "short_put_delta": 0.30,
         "long_call_delta": 0.25, "short_put_iv": 0.40, "long_call_iv": None,
         "underlying_price": 100.0, "expiry": "2026-11-20", "days": 35,
         "earnings_in_window": False, "warnings": list(FIXED) + ["到期前有财报｜示例"]}
    c.update(over)
    return c


def _debit(bucket=0.20):
    return _ok(bucket, short_put_strike=85.0, short_put_mid=1.2, long_call_mid=1.8,
               net_credit=-0.6, breakeven_down=85.6, capital_basis=9060.0,
               upside_breakeven=110.6, put_move_pct=-0.15, dead_zone=[85.0, 110.0],
               warnings=list(FIXED) + [rr_sim_rules.WARN_RR_DEBIT_HEAVY])


def _doc(candidates=None, **over):
    doc = {"version": 1, "strategy": "risk_reversal", "generated_on": "2026-10-17",
           "data_source": "IBKR", "frozen": True, "intraday": False,
           "ticker": "ZZZQ", "snapshot_date": "2026-10-16", "spot": 100.0,
           "expiry": "2026-11-20", "days": 35, "min_days": 30, "earnings": "no",
           "ex_dividend": None,
           "candidates": candidates if candidates is not None else [
               _ok(0.30), _debit(0.20),
               {"delta_bucket": 0.15, "status": "unavailable",
                "reason": "短 Put 最近的 Delta 0.05 离目标 0.15 超过 0.05"}]}
    doc.update(over)
    return doc


def test_candidate_to_json_carries_strategy_and_source():
    out = render.candidate_to_json({"ticker": "ZZZQ", "intraday": False}, "2026-10-17")
    assert out["strategy"] == "risk_reversal" and out["version"] == 1
    assert out["data_source"] == "IBKR" and out["frozen"] is True and out["ticker"] == "ZZZQ"
    assert render.candidate_to_json({"intraday": True}, "2026-10-17")["frozen"] is False


def test_candidate_md_hand_numbers_and_labels():
    text = render.render_candidate_md(_doc())
    for value in ("卖 Put 90（中间价 $2.00", "买 Call 110（中间价 $1.50", "Delta 0.30", "-10.00%",
                  "+10.00%", "净收入 $0.50/股", "占用资金（保守口径）：每张 $8,950",
                  "现金担保口径，券商实际保证金因家而异", "下行盈亏平衡价 89.50",
                  "死区 90 ～ 110", "最大收益 无上限", "Call 行权价以下已不亏"):
        assert value in text, value
    for warning in FIXED:
        assert warning in text
    assert "所需胜率" not in text


def test_candidate_md_net_debit_bucket():
    text = render.render_candidate_md(_doc())
    for value in ("净支出 $0.60/股", "$9,060", "上方回本价：110.60（Call 行权价 + 净支出）",
                  "Call 太贵，净支出偏大", "下行盈亏平衡价 85.60"):
        assert value in text, value


def test_candidate_md_rejected_bucket_reason_and_open_commands():
    text = render.render_candidate_md(_doc())
    assert "短 Put Delta 0.15 档" in text
    assert "剔除 — 短 Put 最近的 Delta 0.05 离目标 0.15 超过 0.05" in text
    assert "scripts/rr_sim.py open ZZZQ 0.30" in text and "open ZZZQ 0.20" in text
    assert "open ZZZQ 0.15" not in text
    assert "**未写入账本**" in text and "纸上交易" in text


def test_candidate_md_all_rejected_and_no_expiry():
    reject = rr_sim_rules.unavailable_candidates(rr_sim_rules.no_expiry_reason(30))
    text = render.render_candidate_md(_doc(reject, expiry=None, days=None))
    assert "三档都被剔除" in text and "没有 30–60 天内的到期日" in text
    assert "rr_sim.py open" not in text.replace("*由 `rr_sim` 生成", "")


def test_candidate_md_unknown_call_delta_and_intraday():
    text = render.render_candidate_md(_doc([_ok(long_call_delta=None)], intraday=True))
    assert "Delta —" in text and "盘中快照" in text


def test_summary_table_columns_and_rows():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17")
    for col in ("标的", "档位", "卖 Put", "买 Call", "Put 中间价", "Call 中间价", "净权利金/股",
                "占用资金/张（保守）", "下行盈亏平衡", "死区", "Put 幅度", "Call 幅度",
                "上方回本价", "提示"):
        assert col in text, col
    row = next(line for line in text.splitlines() if line.startswith("| ZZZQ | 0.30 |"))
    for value in ("| 90 |", "| 110 |", "$2.00", "$1.50", "净收入 $0.50/股", "$8,950", "89.50",
                  "90 ～ 110", "-10.00%", "+10.00%", "Call 行权价以下已不亏"):
        assert value in row, value
    assert "到期前有财报｜示例" in row                    # pipe stays inside the cell
    for warning in FIXED:
        assert warning not in row                        # shown once above the table instead
        assert warning in text
    debit = next(line for line in text.splitlines() if line.startswith("| ZZZQ | 0.20 |"))
    assert "净支出 $0.60/股" in debit and "$9,060" in debit and "110.60" in debit
    assert "ZZZQ Delta 0.15 — 短 Put 最近的 Delta 0.05" in text
    assert "现金担保口径，券商实际保证金因家而异" in text and "最大收益无上限" in text


def test_summary_lists_unreadable_files_and_empty_day():
    text = render.render_summary_md([_doc()], "2026-10-16", "2026-10-17", ["x-rr.json"])
    assert "未列入（文件损坏）" in text and "- x-rr.json" in text
    assert "无候选。" in render.render_summary_md([], "2026-10-16", "2026-10-17")


def test_negative_zero_move_never_rendered():
    text = render.render_candidate_md(_doc([_ok(put_move_pct=-0.00001)]))
    assert "-0.00%" not in text
