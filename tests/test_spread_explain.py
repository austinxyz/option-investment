"""Tests for scripts/spread_explain.py — pure rendering of the saved suggestion file."""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_core as core
import spread_explain as ex
from spread_fixtures import make_chain


def _snap(earnings="no", stale=False, puts=None, width=5.0):
    puts = puts if puts is not None else make_chain(stale=stale)
    return {
        "ticker": "ABC", "snapshot_date": date(2026, 10, 16), "spot": 100.0,
        "expiry": "2026-10-23", "days": 7, "earnings": earnings, "width": width,
        "candidates": core.build_candidates(100.0, 7, puts, width, earnings),
    }


def test_markdown_has_every_section_in_order():
    text = ex.render_suggestion_md(_snap())
    heads = ["# ABC 价差候选", "## 基本信息", "## 候选一览", "## 逐档讲解", "## 三档怎么权衡", "## 怎么开仓"]
    positions = [text.index(h) for h in heads]
    assert positions == sorted(positions)
    assert "未写入账本" in text


def test_each_available_bucket_is_explained_with_real_numbers():
    snap = _snap()
    text = ex.render_suggestion_md(snap)
    for c in snap["candidates"]:
        assert f"### 档位 {c['bucket']:.2f}：卖 {c['short_strike']:g} Put / 买 {c['long_strike']:g} Put" in text
        below = (100.0 - c["short_strike"]) / 100.0 * 100
        assert f"低 {below:.1f}%" in text
        assert f"{c['em_multiple']:.2f} 倍到期前预期波动（7 天" in text
        assert f"盈亏平衡胜率 {c['breakeven_win_rate']:.0%}" in text
        assert f"每 100 笔里要赢约 {c['breakeven_win_rate'] * 100:.0f} 笔" in text
        assert f"+${c['max_profit']:,.0f}" in text and f"-${c['max_loss']:,.0f}" in text


def test_win_condition_has_four_ranges_and_calls_the_middle_ones_correctly():
    snap = _snap()
    c = snap["candidates"][1]
    text = ex.render_suggestion_md(snap)
    short, long_, be = c["short_strike"], c["long_strike"], c["breakeven"]
    assert f"到期收盘价 ≥ {short:g}：保留全部权利金" in text
    # between breakeven and short strike the trade still PROFITS (reduced); only below breakeven it loses
    assert f"介于 {be:.2f} 和 {short:g} 之间：仍然盈利，但赚得比全额少" in text
    assert f"介于 {long_:g} 和 {be:.2f} 之间：亏损" in text
    assert f"≤ {long_:g}：亏满最大亏损" in text
    assert "部分亏损（低于" not in text


def test_expected_move_wording_follows_the_actual_horizon():
    snap = _snap()
    snap["days"] = 10
    assert "倍到期前预期波动（10 天" in ex.render_suggestion_md(snap)


def test_probability_wording_says_it_is_a_rough_risk_neutral_estimate():
    text = ex.render_suggestion_md(_snap())
    assert "风险中性" in text and "不是预测" in text


def test_unavailable_bucket_states_the_reason_and_has_no_open_command():
    puts = [r for r in make_chain() if r["strike"] in (93.0, 95.0, 96.0)]
    text = ex.render_suggestion_md(_snap(puts=puts))
    assert "### 档位 0.10 — 无合适合约" in text
    assert "找不到合适的长腿行权价" in text
    assert "spread_sim.py open ABC 0.10" not in text


def test_open_commands_only_for_available_buckets():
    text = ex.render_suggestion_md(_snap())
    for b in ("0.10", "0.15", "0.20"):
        assert f"spread_sim.py open ABC {b}" in text
    assert "--contracts" in text


def test_all_buckets_unavailable_says_so_instead_of_listing_commands():
    text = ex.render_suggestion_md(_snap(puts=[]))
    assert "没有可用档位" in text
    assert "spread_sim.py open" not in text


@pytest.mark.parametrize("earnings,needle", [
    ("yes", "到期前有财报"), ("unknown", "财报日期未知"), ("no", "到期前无财报"),
])
def test_earnings_state_is_shown_in_the_header(earnings, needle):
    assert needle in ex.render_suggestion_md(_snap(earnings=earnings))


def test_warnings_are_listed_under_their_bucket():
    text = ex.render_suggestion_md(_snap(earnings="yes"))
    assert "需要注意" in text
    assert "按分享页规则建议跳过" in text


def test_stale_quotes_are_called_out_in_data_quality():
    assert "报价陈旧" in ex.render_suggestion_md(_snap(stale=True))
    assert "中间价" in ex.render_suggestion_md(_snap(stale=False))


def test_tradeoff_section_explains_delta_direction():
    text = ex.render_suggestion_md(_snap())
    assert "delta 越小" in text and "delta 越大" in text


def test_json_roundtrip_keeps_candidates_and_converts_the_date():
    snap = _snap()
    doc = ex.snapshot_to_json(snap, "2026-10-16")
    back = json.loads(json.dumps(doc, ensure_ascii=False))
    assert back["snapshot_date"] == "2026-10-16" and back["ticker"] == "ABC"
    assert back["generated_on"] == "2026-10-16" and back["version"] == 1
    assert back["candidates"] == snap["candidates"]
    assert back["earnings"] == "no" and back["width"] == 5.0
    assert back["intraday"] is False


def test_exactly_seven_days_has_no_cycle_note():
    assert "非整 7 天" not in ex.render_suggestion_md(_snap())


def test_non_seven_day_expiry_is_called_out_in_the_header():
    snap = _snap()
    snap["days"] = 8
    text = ex.render_suggestion_md(snap)
    assert "（8 天，非整 7 天" in text
    assert "没有恰好 7 天后的到期日" in text


def test_intraday_snapshot_is_flagged_in_data_quality():
    snap = _snap()
    snap["intraday"] = True
    text = ex.render_suggestion_md(snap)
    assert "盘中快照" in text
    assert ex.snapshot_to_json(snap, "2026-10-14")["intraday"] is True
    assert "盘中快照" not in ex.render_suggestion_md(_snap())


def test_wording_does_not_assume_friday_or_weekend():
    text = ex.render_suggestion_md(_snap())
    assert "同一周" not in text and "周五" not in text and "周末" not in text


def _with_width_info(info, width=10.0):
    snap = _snap(width=width)
    snap["width_info"] = info
    return snap


AUTO = {"source": "auto", "recommended": 10.0, "expected_move": 73.0, "raw": 10.95, "step": 5.0,
        "atm_iv": 0.59, "fraction": 0.15, "capped": False}


def test_every_bucket_states_the_capital_needed_for_one_contract():
    snap = _snap()
    text = ex.render_suggestion_md(snap)
    avail = [c for c in snap["candidates"] if c["available"]]
    assert text.count("做一张需要资金") == len(avail) == 3
    for c in avail:
        line = next(l for l in text.splitlines() if l.startswith("- **做一张需要资金**")
                    and f"净占用 ${c['max_loss']:,.0f}" in l)
        assert f"券商冻结 ${c['width'] * 100:,.0f}" in line
        assert f"收到权利金 ${c['max_profit']:,.0f}" in line
        assert "未含手续费" in line


def test_no_capital_line_for_an_unavailable_bucket():
    puts = [r for r in make_chain() if r["strike"] in (93.0, 95.0, 96.0)]
    text = ex.render_suggestion_md(_snap(puts=puts))
    assert text.count("做一张需要资金") == sum(1 for c in _snap(puts=puts)["candidates"] if c["available"])


def test_auto_width_section_shows_the_working():
    text = ex.render_suggestion_md(_with_width_info(AUTO))
    assert "## 为什么是这个宽度" in text
    assert "自动推荐" in text and "±$73.00" in text and "15%" in text
    assert "取整到行权价间距 $5" in text and "→ $10" in text
    assert text.index("## 基本信息") < text.index("## 为什么是这个宽度") < text.index("## 候选一览")


def test_manual_width_section_compares_with_the_recommendation():
    text = ex.render_suggestion_md(_with_width_info(dict(AUTO, source="manual"), width=5.0))
    assert "你指定的宽度 $5" in text and "系统推荐 $10" in text


def test_manual_width_with_no_recommendation_says_so():
    text = ex.render_suggestion_md(_with_width_info({"source": "manual", "recommended": None}, width=5.0))
    assert "你指定的宽度 $5" in text and "无法估算推荐宽度" in text


def test_fallback_width_explains_why():
    text = ex.render_suggestion_md(_with_width_info({"source": "fallback", "recommended": None}, width=5.0))
    assert "无法估算推荐宽度" in text and "默认 $5" in text


def test_capped_recommendation_is_called_out():
    text = ex.render_suggestion_md(_with_width_info(dict(AUTO, capped=True)))
    assert "现价的 5%" in text


def test_width_section_says_width_does_not_change_the_odds():
    text = ex.render_suggestion_md(_with_width_info(AUTO))
    assert "基本不改变赢面" in text and "摩擦" in text and "略降" in text


def test_json_carries_width_info():
    doc = ex.snapshot_to_json(_with_width_info(AUTO), "2026-10-16")
    assert doc["width_info"]["source"] == "auto"
    assert ex.snapshot_to_json(_snap(), "2026-10-16")["width_info"] is None
