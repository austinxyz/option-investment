#!/usr/bin/env python3
"""Rendering for the cash-secured put screen (pure: no I/O, no network, no clock).

Per-ticker walkthrough, summary table and the JSON twins that `open` reads.
See docs/superpowers/specs/2026-10-18-put-screen-design.md.
"""
from __future__ import annotations

import option_labels
from put_screen_rules import DELTA_RULE_NOTE, TARGET_DELTA, is_default_delta, walk_floor

OPEN_CMD = "./.venv/Scripts/python.exe scripts/put_screen.py open"
FOOTER = "模拟/研究用途，非投资建议"
RISK_NOTE = "高 Beta 标的，来源建议单只不超过总资金 10%–15%，只作收益增强"
ANN_HEADER = "年化（毛，不含被指派后的下跌损失）"
UNRECOGNIZED = "状态无法识别"
EARNINGS_TEXT = {"yes": "到期前有财报 ⚠️", "no": "到期前无财报", "unknown": "财报日期未知 ⚠️"}
RULES_LINES = {
    "monthly": "规则：30–45 天月度到期、Delta 0.20、买卖价差 ≤5%",
    "weekly": "规则：7–14 天最近到期（含周度）、Delta 0.20、买卖价差 ≤5%",
}
MODE_LABEL = {"monthly": "月度", "weekly": "周度"}
WEEKLY_WARNING = "周度合约权利金小、对股价变动更敏感（Gamma 高），到期前一周的波动比月度大"
TIER_SECTIONS = (("🏦", "🏦 蓝筹长持"), ("🚀", "🚀 翻倍候选"), ("⚡", "⚡ 热赛道动量"), (None, "未分类"))
RISKY_TIERS = ("🚀", "⚡")


# The source is read per call (option_labels), never bound at import: the old module constant
# DATA_SOURCE = "IBKR" was imported by name into four renderers and outlived a Yahoo switch.
IBKR_SPREAD_RULE = "买卖价差 ≤5%"


def source_line(snapshot: str, intraday: bool) -> str:
    """Which feed the numbers came from and whether it is the frozen close or delayed intraday."""
    return option_labels.source_line(snapshot, intraday)


def rules_line(mode: str, target: float = TARGET_DELTA) -> str:
    """The rules sentence; a non-default --delta swaps the Delta words before the Yahoo spread swap."""
    text = RULES_LINES[mode]
    if not is_default_delta(target):
        note = DELTA_RULE_NOTE.format(floor=walk_floor(target))
        text = text.replace(f"Delta {TARGET_DELTA:.2f}", f"Delta {target:.2f}（{note}）")
    return option_labels.rules_text(text, IBKR_SPREAD_RULE)


def source_fields() -> dict:
    """`data_source` (+ `data_quality` outside IBKR) for every candidate / summary json."""
    return {"data_source": option_labels.data_source_label(), **option_labels.quality_fields()}


def _quote_kind(c_stale: bool, intraday: bool) -> str:
    if c_stale:
        return "报价陈旧"
    return "盘中快照" if intraday else "收盘"


def candidate_to_json(c: dict, generated_on: str, intraday: bool) -> dict:
    """Machine-readable twin of the markdown file; `open` reads this one."""
    return {"version": 1, "strategy": "cash_secured_put", "generated_on": generated_on,
            "intraday": bool(intraday), **source_fields(), "frozen": not intraday, **c}


def summary_to_json(candidates: list[dict], rejects: list, snapshot: str, generated_on: str,
                    intraday: bool, rules: dict) -> dict:
    return {
        "version": 1, "strategy": "cash_secured_put", "snapshot": snapshot,
        "generated_on": generated_on, "intraday": bool(intraday),
        **source_fields(), "frozen": not intraday, "rules": dict(rules),
        "candidates": [dict(c) for c in candidates],
        "rejects": [{"ticker": r.ticker, "reason": r.reason} for r in rejects],
    }


def render_ticker_md(c: dict, intraday: bool) -> str:
    """The per-ticker walkthrough the user reads before deciding to open."""
    t = c["ticker"]
    tier = f" {c['tier']}" if c.get("tier") else ""
    kind = _quote_kind(c["stale"], intraday)
    keep = (1 - abs(c["delta"])) * 100
    mode = c.get("dte_mode", "monthly")
    window = "" if c["in_window"] else "（不在 30–45 天窗口内，取最接近的月度到期）"
    lines = [
        f"# {t}{tier} 现金担保 Put（{MODE_LABEL[mode]}） · 快照 {c['snapshot_date']}（{kind}）", "",
        "*由 `put_screen` 生成。纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(c['snapshot_date'], intraday)}。*", "",
        f"*{rules_line(mode, c.get('target_delta', TARGET_DELTA))}。*", ""] + ([f"> {WEEKLY_WARNING}", ""] if mode == "weekly" else []) + [
        "## 合约", "",
        f"- **卖出 {c['expiry']} 到期 ${c['strike']:g} Put**，到期还有 {c['days']} 天{window}；现价 ${c['spot']:,.2f}。",
        f"- 权利金中间价 ${c['mid']:.2f}/股，买卖价差 {c['spread_pct'] * 100:.1f}%"
        if c.get("spread_pct") is not None else f"- 权利金中间价 ${c['mid']:.2f}/股",
        f"- Delta {c['delta']:.2f}，隐含波动率 {c['iv'] * 100:.0f}%：约 {keep:.0f}% 不被指派"
        "（风险中性估计，不是预测）。",
        f"- 财报：{EARNINGS_TEXT[c['earnings']]}", "",
        "## 数字", "",
        f"- **占用现金** ${c['cash_required']:,.0f}（行权价 × 100，全额现金担保）",
        f"- **盈亏平衡价** ${c['breakeven']:.2f}（行权价 − 权利金）",
        f"- **缓冲** {c['buffer_pct']:.1f}%：现价需跌这么多才会碰到行权价",
        f"- **毛年化** {c['annualized_pct']:.1f}%（权利金 ÷ 行权价，折算成一年；不含被指派后的下跌损失）", "",
        "## 被指派会怎样", "",
        f"- 到期收盘价低于 ${c['strike']:g}：按行权价买入 100 股，成本 ${c['breakeven']:.2f}/股（已扣权利金）。",
        f"- 最大亏损 = ${c['max_loss_assigned']:,.0f}（若股价归零）。",
        f"- 到期收盘价 ≥ ${c['strike']:g}：不被指派，保留全部权利金 ${c['mid'] * 100:,.0f}。", "",
    ]
    if c["warnings"]:
        lines += ["## 需要注意", ""] + [f"- {w}" for w in c["warnings"]] + [""]
    lines += [
        "## 怎么开仓", "",
        "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
        "```bash", f"{OPEN_CMD} {t}", "```", "",
        "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", "",
        FOOTER, "",
    ]
    return option_labels.with_banner("\n".join(lines))


def _cell(text: str) -> str:
    """Free text inside a markdown table cell: a literal pipe would split the row."""
    return text.replace("|", "｜")


def _table(rows: list[dict]) -> list[str]:
    lines = [f"| 标的 | 档位 | 价格 | 行权价 | 到期（天数） | 权利金 | 占用现金 | 盈亏平衡价 | 缓冲 "
             f"| Delta | {ANN_HEADER} | 被指派最大亏损 | 提示 |",
             "|------|------|------|--------|--------------|--------|----------|------------|------"
             "|-------|------|----------------|------|"]
    for c in rows:
        lines.append(
            f"| {_cell(c['ticker'])} | {c.get('tier') or '—'} | ${c['spot']:,.2f} | {c['strike']:g} "
            f"| {c['expiry']}（{c['days']} 天） | ${c['mid']:.2f} | ${c['cash_required']:,.0f} "
            f"| ${c['breakeven']:.2f} | {c['buffer_pct']:.1f}% | {c['delta']:.2f} "
            f"| {c['annualized_pct']:.1f}% | ${c['max_loss_assigned']:,.0f} "
            f"| {_cell('；'.join(c['warnings'])) or '—'} |")
    return lines


def render_summary_md(candidates: list[dict], rejects: list, snapshot: str, generated_on: str,
                      intraday: bool, *, message: str | None = None,
                      dte_mode: str = "monthly", target: float = TARGET_DELTA) -> str:
    kind = "盘中快照" if intraday else "收盘"
    lines = [f"# 现金担保 Put 筛选（{MODE_LABEL[dte_mode]}） · 快照 {snapshot}（{kind}）", "",
             f"*生成于 {generated_on}。{rules_line(dte_mode, target)}。*", "",
             f"*{source_line(snapshot, intraday)}。*", ""]
    if message is not None:
        return option_labels.with_banner("\n".join(lines + [message, "", FOOTER, ""]))
    for tier, title in TIER_SECTIONS:
        rows = [c for c in candidates if c.get("tier") == tier]
        lines += [f"## {title}", ""]
        if tier in RISKY_TIERS:
            lines += [f"> {RISK_NOTE}", ""]
        lines += (_table(rows) if rows else ["无候选。"]) + [""]
    if dte_mode == "weekly":
        lines += [f"> {WEEKLY_WARNING}", ""]
    lines += ["## 被剔除", ""]
    plain = [r for r in rejects if r.reason != UNRECOGNIZED]
    lines += [f"- {r.ticker} — {r.reason}" for r in plain] or ["无。"]
    lines.append("")
    unknown = [r for r in rejects if r.reason == UNRECOGNIZED]
    if unknown:
        lines += [f"### {UNRECOGNIZED}", "",
                  "wiki 里这些标的的状态没能识别，请检查：", ""]
        lines += [f"- {r.ticker}" for r in unknown] + [""]
    return option_labels.with_banner("\n".join(lines + [FOOTER, ""]))
