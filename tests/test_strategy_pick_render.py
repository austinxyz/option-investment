"""Report tests for /strategy-pick: JSON field set, markdown sections, hand numbers (design 6.13).

ZZZQ is invented. Hand arithmetic (section 10):
  ATM put spread 0.12 / 2.96 = 0.04054 -> 4.05%     RV1y 0.0189 x 15.875 -> 30.0%
  RV21 windows 252 - 21 = 231; 174 / 231 = 0.75325 -> 75.3% (high)
  sigma = 100 x 0.32 x sqrt(28 / 365) = 8.863037 -> 8.86
  Delta 0.20 strike 100 - 0.84 x 8.863037 = 92.555 -> 92.56
  92.56 x 100 / 200,000 = 0.04628 -> flag rounds to 0.05 (约 5%)
"""
import json

import pytest

from strategy_pick_cli_stubs import base_result
import strategy_pick_data as D
import strategy_pick_render as render
import strategy_pick_rules as rules

TOP_KEYS = {"ticker", "run_date", "snapshot_date", "asset_class", "class_source", "gate", "view",
            "view_source", "thesis_status", "thesis_verdict_text", "thesis_age_days", "metrics",
            "vol_tag", "direction_tag", "regime_leaf", "recent_change_hint", "iv", "holding",
            "earnings", "leaf_id", "picks", "not_chosen", "warnings", "thresholds_used"}
IV_KEYS = {"atm_iv", "expiry_used", "days", "percentile", "label", "method", "iv_rv_ratio"}
HOLDING_KEYS = {"held_shares_total", "max_lot_shares", "lot_count", "ltcg_reached",
                "treated_as_held"}
PICK_KEYS = {"strategy", "rank", "delta_target", "reference_level", "next_command", "flags"}
NOT_CHOSEN_KEYS = {"strategy", "reason_code", "reason_text", "page"}
GATE_KEYS = {"passed", "items"}
GATE_ITEM_KEYS = {"name", "constant", "value", "threshold", "result"}
SECTIONS = ("## 结论与下一步", "## 七步逐步表", "## 走势指标", "## IV 位置（代理）",
            "## 为什么不选", "## 警告", "## 局限")


def test_json_has_exactly_spec_fields(monkeypatch, tmp_path):
    doc, _ = base_result(monkeypatch, tmp_path)
    assert set(doc) == TOP_KEYS
    assert set(doc["iv"]) == IV_KEYS
    assert set(doc["holding"]) == HOLDING_KEYS
    assert set(doc["gate"]) == GATE_KEYS
    assert doc["gate"]["items"] and all(set(i) == GATE_ITEM_KEYS for i in doc["gate"]["items"])
    assert doc["picks"] and all(set(p) == PICK_KEYS for p in doc["picks"])
    assert doc["not_chosen"] and all(set(n) == NOT_CHOSEN_KEYS for n in doc["not_chosen"])
    assert doc["leaf_id"] == "bull.not_held.steady_up.high"
    assert [p["strategy"] for p in doc["picks"]] == ["cash_secured_put", "bull_put_spread"]
    assert doc["iv"]["method"] == "iv_vs_rv" and doc["iv"]["expiry_used"] == "2026-11-20"


def test_json_holding_has_no_account_cost_or_pnl(monkeypatch, tmp_path):
    read = D.HoldingRead((D.HoldingRow(150.0, True, "active"),), "🏦 蓝筹", False, False)
    doc, _ = base_result(monkeypatch, tmp_path, read_holding=read)
    assert doc["holding"] == {"held_shares_total": 150.0, "max_lot_shares": 150.0,
                              "lot_count": 1, "ltcg_reached": True, "treated_as_held": True}
    text = json.dumps(doc, ensure_ascii=False).lower()
    for word in ("account", "cost", "pnl", "p&l", "成本", "盈亏", "账户名"):
        assert word not in text


def test_json_has_no_portfolio_value(monkeypatch, tmp_path):
    # A distinctive invented value so a leak cannot hide inside other numbers.
    doc, md = base_result(monkeypatch, tmp_path, portfolio_value=271_828.0)
    text = json.dumps(doc)
    for leak in ("271828", "271,828", "271.8"):
        assert leak not in text and leak not in md
    # 92.56 x 100 / 271,828 = 0.03405 -> rounded to 2 decimals in the flag
    assert "portfolio_share=0.03" in doc["picks"][0]["flags"]
    assert "占组合约 3%" in md
    doc2, _ = base_result(monkeypatch, tmp_path)
    assert "portfolio_share=0.05" in doc2["picks"][0]["flags"]


def test_thresholds_used_generated_from_rules(monkeypatch, tmp_path):
    doc, _ = base_result(monkeypatch, tmp_path)
    used = doc["thresholds_used"]
    assert used["MIN_PRICE"] == 10.0 and used["VOL_HIGH"] == 0.35
    for name in ("GATE_MAX_SPREAD_PCT", "IV_HIGH_PCTL", "IV_LOW_PCTL", "LOOKBACK_DAYS",
                 "CSP_FIRST_CLASSES", "Z_BY_DELTA", "NORMAL_DELTA", "MIN_TOUCHES",
                 "IV_EXPIRY_TARGET_DAYS"):
        assert name in used
    assert "ZERO" not in used and "MA200" not in used
    monkeypatch.setattr(rules, "MIN_PRICE", 12.0)
    monkeypatch.setitem(rules.CLASS_THRESHOLDS["bluechip"], "VOL_HIGH", 0.31)
    doc2, _ = base_result(monkeypatch, tmp_path)
    assert doc2["thresholds_used"]["MIN_PRICE"] == 12.0
    assert doc2["thresholds_used"]["VOL_HIGH"] == 0.31


def test_markdown_sections_in_order(monkeypatch, tmp_path):
    _, md = base_result(monkeypatch, tmp_path)
    positions = [md.index(s) for s in SECTIONS]
    assert positions == sorted(positions)
    assert md.rstrip().splitlines()[-1].count("不构成投资建议") == 1
    assert md.index("不构成投资建议") > positions[-1]


def _step_rows(md: str) -> list[list[str]]:
    body = md.split("## 七步逐步表", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in body.splitlines() if line.startswith("|")]
    return [[c.strip() for c in r.strip("|").split("|")] for r in rows[2:]]


def test_markdown_every_step_has_value_threshold_constant_result(monkeypatch, tmp_path):
    _, md = base_result(monkeypatch, tmp_path)
    rows = _step_rows(md)
    assert len(rows) > 15
    for row in rows:
        assert len(row) == 6 and all(row), row
    assert {r[0] for r in rows} == {"①", "②", "③", "④", "⑤", "⑥", "⑦"}
    header = md.split("## 七步逐步表", 1)[1].splitlines()
    head = next(line for line in header if line.startswith("|"))
    for col in ("值", "阈值", "常量", "结果"):
        assert col in head
    assert any("VOL_HIGH" in r[4] and "否" in r[5] for r in rows)
    assert any("GATE_MAX_SPREAD_PCT" in r[4] and "4.05%" in r[2] for r in rows)


def test_markdown_iv_marked_proxy(monkeypatch, tmp_path):
    _, md = base_result(monkeypatch, tmp_path)
    iv_section = md.split("## IV 位置（代理）", 1)[1].split("\n## ", 1)[0]
    assert "代理" in iv_section and "75.3%" in iv_section
    strikes_row = next(r for r in _step_rows(md) if "MIN_STRIKES_15PCT" in r[4])
    assert "代理" in strikes_row[1]


def test_markdown_iv_fallback_says_rv_vs_rv(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import snapshot
    doc, md = base_result(monkeypatch, tmp_path, option_snapshot=snapshot(atm_iv=None))
    assert doc["iv"]["method"] == "rv_vs_rv"
    assert all(p["reference_level"] is None for p in doc["picks"])
    assert "实现波动率自比" in md and "参考价位不给" in md


def test_markdown_has_limits_and_disclaimer(monkeypatch, tmp_path):
    _, md = base_result(monkeypatch, tmp_path)
    limits = md.split("## 局限", 1)[1]
    for phrase in ("一年只是一个行情阶段", "百分位会误导", "IV 位置是代理", "工具不预测方向",
                   "由你决定"):
        assert phrase in limits
    assert render.DISCLAIMER in md


def test_markdown_base_example_numbers(monkeypatch, tmp_path):
    doc, md = base_result(monkeypatch, tmp_path)
    for number in ("4.05%", "30.0%", "231", "75.3%", "8.86", "92.56", "约 5%"):
        assert number in md, number
    assert "/put-screen suggest --dte monthly" in md and "/spread-sim suggest ZZZQ" in md
    assert doc["iv"]["percentile"] == 174 / 231


def test_render_markdown_matches_written_file(monkeypatch, tmp_path):
    doc, md = base_result(monkeypatch, tmp_path)
    assert render.render_markdown(doc) == md


def test_markdown_earnings_imminent_pair_is_readable(monkeypatch, tmp_path):
    from datetime import date
    doc, md = base_result(monkeypatch, tmp_path, argv=("ZZZQ", "--class", "hot"),
                          next_earnings=date(2026, 10, 28))
    assert [p["strategy"] for p in doc["picks"]][0] == "wait_earnings"
    conclusion = md.split("## 七步逐步表", 1)[0]
    assert "等财报后再看" in conclusion and "/strategy-pick ZZZQ" in conclusion


def test_console_summary_one_page_with_both_paths(monkeypatch, tmp_path):
    doc, _ = base_result(monkeypatch, tmp_path)
    md_path, json_path = tmp_path / "a.md", tmp_path / "a.json"
    text = render.render_console(doc, md_path, json_path)
    assert str(md_path) in text and str(json_path) in text
    assert "现金担保 Put" in text and "bull.not_held.steady_up.high" in text
    assert len(text.splitlines()) <= 40


def test_share_note_on_one_pick_only(monkeypatch, tmp_path):
    doc, _ = base_result(monkeypatch, tmp_path)
    carriers = [p["strategy"] for p in doc["picks"]
                if any(f.startswith("portfolio_share=") for f in p["flags"])]
    assert carriers == ["cash_secured_put"]


def test_no_rank_row_without_ranking(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import snapshot
    doc, md = base_result(monkeypatch, tmp_path, option_snapshot=snapshot(
        expiry_used=None, atm_iv=None, atm_put=None, atm_call=None, puts=(), calls=(),
        strikes_15pct=None))
    assert doc["leaf_id"] == "bull.not_held.steady_up.mid"
    assert "现金担保 Put 资金占组合" not in md
    assert "CSP_MAX_PORTFOLIO_SHARE" not in doc["thresholds_used"]


def test_history_short_has_no_iv_label(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import history
    doc, md = base_result(monkeypatch, tmp_path, load_history=history(150))
    assert doc["iv"]["label"] is None and doc["iv"]["percentile"] is None
    assert "不分类、不给策略" in md


def _row(md, needle):
    from test_strategy_pick_render import _step_rows
    return next(r for r in _step_rows(md) if needle in r[1] or needle in r[4])


def test_rank_row_shows_class(monkeypatch, tmp_path):
    _, md = base_result(monkeypatch, tmp_path)
    row = _row(md, "CSP_MAX_PORTFOLIO_SHARE")
    assert "约 5%" in row[2] and "大盘蓝筹" in row[2] and "在 CSP_FIRST_CLASSES" in row[2]
    _, md_hot = base_result(monkeypatch, tmp_path, argv=("ZZZQ", "--class", "hot"))
    hot = _row(md_hot, "CSP_MAX_PORTFOLIO_SHARE")
    assert "热门" in hot[2] and "不在 CSP_FIRST_CLASSES" in hot[2] and "Bull Put" in hot[5]


def test_v8_share_note_not_on_bull_put(monkeypatch, tmp_path):
    from datetime import date
    doc, md = base_result(monkeypatch, tmp_path, argv=("ZZZQ", "--class", "hot"),
                          next_earnings=date(2026, 10, 28))
    assert [p["strategy"] for p in doc["picks"]] == ["wait_earnings", "bull_put_spread"]
    assert not any(f.startswith("portfolio_share=") for f in doc["picks"][1]["flags"])
    assert "资金占组合约" not in md.split("## 七步逐步表", 1)[0]


def test_history_short_iv_section_says_not_computed(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import history
    doc, md = base_result(monkeypatch, tmp_path, load_history=history(150))
    section = md.split("## IV 位置（代理）", 1)[1].split("## 为什么不选", 1)[0]
    assert "历史不足，不计算 IV 位置" in section
    assert "平值 IV：" not in section and "IV 缺失" not in section
    assert doc["iv"]["atm_iv"] is None
    assert any("不计算 IV 位置" in w for w in doc["warnings"])


def test_trend_rows_carry_rule_labels(monkeypatch, tmp_path):
    from test_strategy_pick_render import _step_rows
    _, md = base_result(monkeypatch, tmp_path)
    items = [r[1] for r in _step_rows(md) if r[0] == "④"]
    assert "高波动条件：一年实现波动率" in items and "上行条件：最新收盘" in items
    assert "下行条件：最新收盘" in items and "盘整条件：上沿触碰" in items
    slope = next(r for r in _step_rows(md) if r[1] == "上行条件：MA200 斜率")
    assert slope[2] == "2.04%"
    assert "+2.04%" in md.split("## 走势指标", 1)[1]


def test_reduce_hint_is_warning_not_pick_note(monkeypatch, tmp_path):
    rows = (D.HoldingRow(60.0, True, "active"), D.HoldingRow(50.0, False, "active"))
    from strategy_pick_cli_stubs import thesis
    doc, md = base_result(monkeypatch, tmp_path, read_thesis=thesis(status="watch"),
                          read_holding=D.HoldingRead(rows, None, False, False))
    conclusion = md.split("## 七步逐步表", 1)[0]
    assert "考虑减仓" not in conclusion
    assert any("考虑减仓" in w for w in doc["warnings"])


def test_etf_market_cap_row_not_applicable(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import thesis
    _, md = base_result(monkeypatch, tmp_path, argv=("ZZQE",),
                        load_info={"market_cap": None, "quote_type": "ETF"},
                        read_thesis=thesis(status="use", is_etf_page=True))
    row = _row(md, "MIN_MARKET_CAP")
    assert row[5] == "不适用（ETF）"


def test_thresholds_used_names_step_table_constants(monkeypatch, tmp_path):
    doc, _ = base_result(monkeypatch, tmp_path)
    used = doc["thresholds_used"]
    assert used["CLASS_BY_TIER_EMOJI"] == {"🏦": "bluechip", "🚀": "hot", "⚡": "hot"}
    leaf = used["LEAF_TABLE"]
    assert set(leaf) == {doc["leaf_id"]}
    entry = leaf[doc["leaf_id"]]
    picks, warns = rules.LEAF_TABLE[doc["leaf_id"]]
    assert [p["strategy"] for p in entry["picks"]] == [p.strategy for p in picks]
    assert entry["warnings"] == list(warns) and set(entry) == {"picks", "warnings"}


@pytest.mark.parametrize("status,label", [("under_review", "复审中"), ("unrecognized", "无法识别")])
def test_view_row_shows_chinese_status_label(monkeypatch, tmp_path, status, label):
    from strategy_pick_cli_stubs import thesis
    _, md = base_result(monkeypatch, tmp_path, argv=("ZZZQ", "--view", "中性"),
                        read_thesis=thesis(status=status))
    row = _row(md, "STATUS_TO_VIEW")
    assert label in row[2] and status not in row[2]


def test_unused_needs_user_statuses_removed():
    assert not hasattr(rules, "NEEDS_USER_STATUSES")
