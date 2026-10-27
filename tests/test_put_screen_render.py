import json

from spread_fixtures import make_chain  # noqa: F401  (puts scripts/ on sys.path)
import put_screen_render as render
from put_screen_rules import Reject

FOOTER = "模拟/研究用途，非投资建议"
RISK_NOTE = "高 Beta 标的，来源建议单只不超过总资金 10%–15%，只作收益增强"
ANN_HEADER = "年化（毛，不含被指派后的下跌损失）"


def _cand(ticker="AAPL", tier="🏦", **kw):
    c = {
        "ticker": ticker, "tier": tier, "status": "Active",
        "snapshot_date": "2026-10-18", "spot": 100.0, "expiry": "2026-11-20",
        "days": 33, "in_window": True, "strike": 90.0, "mid": 1.5, "stale": False,
        "spread_pct": 0.02, "delta": -0.20, "iv": 0.35, "earnings": "no",
        "cash_required": 9000.0, "breakeven": 88.5, "buffer_pct": 10.0,
        "annualized_pct": 18.4, "max_loss_assigned": 8850.0, "warnings": [],
    }
    c.update(kw)
    return c


def test_ticker_md_contains_key_numbers_and_open_command():
    md = render.render_ticker_md(_cand(), intraday=False)
    assert "AAPL" in md and "2026-10-18" in md and "收盘" in md
    assert "90" in md and "88.50" in md and "9,000" in md
    assert "不被指派" in md
    assert "./.venv/Scripts/python.exe scripts/put_screen.py open AAPL" in md
    assert "8,850" in md
    assert "偏乐观" in md
    assert md.rstrip().endswith(FOOTER)


def test_ticker_md_quote_kinds():
    assert "盘中快照" in render.render_ticker_md(_cand(), intraday=True)
    assert "报价陈旧" in render.render_ticker_md(_cand(stale=True), intraday=False)


def test_ticker_md_lists_warnings():
    md = render.render_ticker_md(_cand(warnings=["到期前有财报", "非 30-45 天窗口"]), intraday=False)
    assert "- 到期前有财报" in md and "- 非 30-45 天窗口" in md


def test_summary_groups_by_tier_and_adds_risk_note_for_speculative_tiers():
    cands = [_cand("AAPL", "🏦"), _cand("NVDA", "🚀"), _cand("TSLL", "⚡"), _cand("XYZ", None)]
    md = render.render_summary_md(cands, [], "2026-10-18", "2026-10-18", intraday=False)
    pos = [md.index(t) for t in ("AAPL", "NVDA", "TSLL", "XYZ")]
    assert pos == sorted(pos)
    assert md.count(RISK_NOTE) == 2
    assert ANN_HEADER in md
    assert "未分类" in md
    assert md.rstrip().endswith(FOOTER)


def test_summary_lists_rejects_and_unrecognized():
    rejects = [Reject("BAD", "买卖价差过宽"), Reject("WEIRD", "状态无法识别")]
    md = render.render_summary_md([_cand()], rejects, "2026-10-18", "2026-10-18", intraday=True)
    assert "被剔除" in md and "BAD — 买卖价差过宽" in md
    assert "状态无法识别" in md and "WEIRD" in md
    assert "盘中" in md


def test_summary_message_replaces_tables():
    md = render.render_summary_md([_cand()], [Reject("BAD", "x")], "2026-10-18", "2026-10-18",
                                  intraday=False, message="本日无合适月度到期")
    assert "本日无合适月度到期" in md
    assert "AAPL" not in md and "被剔除" not in md
    assert md.rstrip().endswith(FOOTER)


def test_candidate_json_roundtrips_through_json_dumps():
    d = render.candidate_to_json(_cand(), "2026-10-18", False)
    back = json.loads(json.dumps(d))
    assert back["strategy"] == "cash_secured_put" and back["version"] == 1
    assert back["generated_on"] == "2026-10-18" and back["intraday"] is False
    assert back["strike"] == 90.0


def test_summary_json_roundtrips():
    d = render.summary_to_json([_cand()], [Reject("BAD", "x")], "2026-10-18", "2026-10-18",
                               False, {"delta": 0.2})
    back = json.loads(json.dumps(d))
    assert back["rejects"] == [{"ticker": "BAD", "reason": "x"}]
    assert back["candidates"][0]["ticker"] == "AAPL" and back["rules"] == {"delta": 0.2}


def test_summary_tier_titles_match_spec():
    cands = [_cand("AAPL", "🏦"), _cand("NVDA", "🚀"), _cand("TSLL", "⚡"), _cand("XYZ", None)]
    md = render.render_summary_md(cands, [], "2026-10-18", "2026-10-18", intraday=False)
    for title in ("## 🏦 蓝筹长持", "## 🚀 翻倍候选", "## ⚡ 热赛道动量", "## 未分类"):
        assert title in md
    assert "杠杆" not in md


def test_summary_table_has_spec_columns_and_values():
    md = render.render_summary_md([_cand(warnings=["a|b"])], [], "2026-10-18", "2026-10-18",
                                  intraday=False)
    header = next(line for line in md.splitlines() if line.startswith("| 标的"))
    cols = [c.strip() for c in header.strip("|").split("|")]
    assert cols == ["标的", "档位", "价格", "行权价", "到期（天数）", "权利金", "占用现金",
                    "盈亏平衡价", "缓冲", "Delta", ANN_HEADER, "被指派最大亏损", "提示"]
    row = next(line for line in md.splitlines() if line.startswith("| AAPL"))
    assert "$100.00" in row and "$88.50" in row and "$8,850" in row and "2026-11-20（33 天）" in row
    assert len(row.strip("|").split("|")) == len(cols)  # the pipe in the warning was escaped


WEEKLY_RULES = "规则：7–14 天最近到期（含周度）、Delta 0.20、买卖价差 ≤5%"
MONTHLY_RULES = "规则：30–45 天月度到期、Delta 0.20、买卖价差 ≤5%"
WEEKLY_WARN = "周度合约权利金小、对股价变动更敏感（Gamma 高），到期前一周的波动比月度大"


def test_summary_header_per_mode():
    args = ([_cand()], [], "2026-10-18", "2026-10-18", False)
    monthly = render.render_summary_md(*args)
    weekly = render.render_summary_md(*args, dte_mode="weekly")
    assert MONTHLY_RULES in monthly and "（月度）" in monthly.splitlines()[0]
    assert WEEKLY_RULES in weekly and "（周度）" in weekly.splitlines()[0]
    assert MONTHLY_RULES not in weekly and WEEKLY_RULES not in monthly


def test_weekly_warning_only_in_weekly_output():
    args = ([_cand(dte_mode="weekly")], [], "2026-10-18", "2026-10-18", False)
    assert WEEKLY_WARN in render.render_summary_md(*args, dte_mode="weekly")
    assert WEEKLY_WARN not in render.render_summary_md(*args)
    assert WEEKLY_WARN in render.render_ticker_md(_cand(dte_mode="weekly"), False)
    assert WEEKLY_WARN not in render.render_ticker_md(_cand(), False)
    assert WEEKLY_RULES in render.render_ticker_md(_cand(dte_mode="weekly"), False)
    assert MONTHLY_RULES in render.render_ticker_md(_cand(), False)


def test_summary_message_is_mode_aware_and_json_carries_mode():
    md = render.render_summary_md([], [], "2026-10-18", "2026-10-18", False,
                                  message="本日无合适周度到期", dte_mode="weekly")
    assert "本日无合适周度到期" in md and WEEKLY_RULES in md
    d = render.candidate_to_json(_cand(dte_mode="weekly"), "2026-10-18", False)
    assert d["dte_mode"] == "weekly"


def test_data_source_labelling_frozen_and_intraday():
    c = _cand()
    j = render.candidate_to_json(c, "2026-10-18", False)
    assert j["data_source"] == "IBKR" and j["frozen"] is True
    assert render.candidate_to_json(c, "2026-10-18", True)["frozen"] is False
    sj = render.summary_to_json([c], [], "2026-10-18", "2026-10-18", True, {})
    assert sj["data_source"] == "IBKR" and sj["frozen"] is False
    frozen = "数据源 IBKR 模拟网关 · 基于 2026-10-18 收盘（冻结行情）"
    assert frozen in render.render_summary_md([c], [], "2026-10-18", "2026-10-18", intraday=False)
    assert frozen in render.render_ticker_md(c, intraday=False)
    live = "数据源 IBKR 模拟网关 · 基于 2026-10-18 收盘（盘中延迟行情）"
    assert live in render.render_summary_md([c], [], "2026-10-18", "2026-10-18", intraday=True)
    assert live in render.render_ticker_md(c, intraday=True)
