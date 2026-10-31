#!/usr/bin/env python3
"""Rendering for the covered-call screen (pure: no I/O, no network, no clock).

Per-lot walkthrough, summary table and the JSON twins that `open` reads.
See docs/superpowers/specs/2026-10-21-call-screen-design.md.
"""
from __future__ import annotations

OPEN_CMD = "./.venv/Scripts/python.exe scripts/call_screen.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报 ⚠️", "no": "到期前无财报", "unknown": "财报日期未知 ⚠️"}
RULES_LINES = {
    "monthly": "规则：30–45 天月度到期、Delta 0.20（虚值）、买卖价差 ≤5%",
    "weekly": "规则：7–14 天最近到期（含周度）、Delta 0.20（虚值）、买卖价差 ≤5%",
}
import option_labels
from put_screen_render import IBKR_SPREAD_RULE, source_fields, source_line
from put_screen_rules import DELTA_RULE_NOTE, TARGET_DELTA, is_default_delta, walk_floor

MODE_LABEL = {"monthly": "月度", "weekly": "周度"}
SOURCE_LABEL = {"real": "真实", "manual": "假设", "assigned_put": "被指派"}
WEEKLY_WARNING = "周度合约权利金小、对股价变动更敏感（Gamma 高），到期前一周的波动比月度大"
CALL_AWAY_NOTE = "上涨超过行权价的部分被让出：卖出 call 的代价是放弃行权价以上的涨幅"


def rules_line(mode: str, target: float = TARGET_DELTA) -> str:
    """The rules sentence; a non-default --delta swaps the Delta words before the Yahoo spread swap."""
    text = RULES_LINES[mode]
    if not is_default_delta(target):
        note = DELTA_RULE_NOTE.format(floor=walk_floor(target))
        text = text.replace(f"Delta {TARGET_DELTA:.2f}（虚值）", f"Delta {target:.2f}（虚值，{note}）")
    return option_labels.rules_text(text, IBKR_SPREAD_RULE)


def _quote_kind(stale: bool, intraday: bool) -> str:
    if stale:
        return "报价陈旧"
    return "盘中快照" if intraday else "收盘"


def candidate_to_json(c: dict, generated_on: str, intraday: bool) -> dict:
    """Machine-readable twin of the markdown file; `open` reads this one."""
    return {"version": 1, "strategy": "covered_call", "generated_on": generated_on,
            "intraday": bool(intraday), **source_fields(), "frozen": not intraday, **c}


def summary_to_json(candidates: list[dict], rejects: list, snapshot: str, generated_on: str,
                    intraday: bool, rules: dict) -> dict:
    return {
        "version": 1, "strategy": "covered_call", "snapshot": snapshot,
        "generated_on": generated_on, "intraday": bool(intraday),
        **source_fields(), "frozen": not intraday, "rules": dict(rules),
        "candidates": [dict(c) for c in candidates],
        "rejects": [{"ticker": r.ticker, "reason": r.reason} for r in rejects],
    }


def _open_command(c: dict) -> str:
    lot = "real" if c["lot_id"] is None else str(c["lot_id"])
    return f"{OPEN_CMD} {c['ticker']} --lot {lot}"


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def _called_away_lines(c: dict) -> list[str]:
    strike, mid, ref = c["strike"], c["mid"], c["reference_price"]
    return [
        f"- 到期收盘价 ≥ ${strike:g} 被行权：股票按 ${strike:g} 卖出"
        f"（最多可卖 {c['contracts_available']} 张，open 默认 1 张，用 --contracts 调整；每张 100 股）。",
        f"- 股票这一腿：行权价 ${strike:g} 对比参考价 ${ref:.2f}，每股 {_signed(strike - ref)}；"
        f"加上权利金 ${mid:.2f}/股，每股合计 {_signed(strike - ref + mid)}。",
        f"- 对比原始成本（每股 ${c['original_cost']:.2f}）：每股 {_signed(strike + mid - c['original_cost'])}；"
        f"对比有效成本 ${c['cost_basis']:.2f}：每股 {_signed(strike + mid - c['cost_basis'])}。",
        f"- {CALL_AWAY_NOTE}。",
        f"- 到期收盘价 < ${strike:g} 不被行权：保留股票和全部权利金 "
        f"${c['premium_per_contract']:,.0f}/张。",
    ]


def render_lot_md(c: dict, intraday: bool) -> str:
    """The per-lot walkthrough the user reads before deciding to open."""
    mode = c.get("dte_mode", "weekly")
    kind = _quote_kind(c["stale"], intraday)
    window = "" if c["in_window"] else "（不在偏好窗口内，取最接近的到期）"
    keep = (1 - abs(c["delta"])) * 100
    lines = [
        f"# {c['label']} 备兑 Call（{MODE_LABEL[mode]}） · 快照 {c['snapshot_date']}（{kind}）", "",
        "*由 `call_screen` 生成。纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(c['snapshot_date'], intraday)}。*", "",
        f"*{rules_line(mode, c.get('target_delta', TARGET_DELTA))}。*", ""] + ([f"> {WEEKLY_WARNING}", ""] if mode == "weekly" else []) + [
        "## 合约", "",
        f"- **卖出 {c['expiry']} 到期 ${c['strike']:g} Call**，到期还有 {c['days']} 天{window}；"
        f"现价 ${c['spot']:,.2f}。",
        f"- 持有 {c['shares']:g} 股，可覆盖 {c['contracts_available']} 张合约。",
        f"- 权利金中间价 ${c['mid']:.2f}/股（每张 ${c['premium_per_contract']:,.0f}）"
        + (f"，买卖价差 {c['spread_pct'] * 100:.1f}%" if c.get("spread_pct") is not None else ""),
        f"- Delta {c['delta']:.2f}，隐含波动率 {c['iv'] * 100:.0f}%：约 {keep:.0f}% 不被行权"
        "（风险中性估计，不是预测）。",
        f"- 财报：{EARNINGS_TEXT[c['earnings']]}", "",
        "## 数字", "",
        f"- **每张权利金** ${c['premium_per_contract']:,.0f}",
        f"- **到行权价的涨幅** {c['upside_to_strike_pct']:.1f}%：现价涨这么多才会被行权",
        f"- **毛年化** {c['annualized_pct']:.1f}%（权利金 ÷ 现价，折算成一年）", "",
        "## 被行权会怎样", "",
    ] + _called_away_lines(c) + [""]
    if c["warnings"]:
        lines += ["## 需要注意", ""] + [f"- {w}" for w in c["warnings"]] + [""]
    lines += [
        "## 怎么开仓", "",
        "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
        "```bash", _open_command(c), "```", "",
        "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", "", FOOTER, "",
    ]
    return option_labels.with_banner("\n".join(lines))


def _cell(text: str) -> str:
    """Free text inside a markdown table cell: a literal pipe would split the row."""
    return text.replace("|", "｜")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f}%".replace("+-", "-")


def _table(rows: list[dict]) -> list[str]:
    lines = ["| 来源 | 标的 | 可卖张数 | 价格 | 对 MA50 | 对 MA200 | 近 1 月 "
             "| 行权价 | 到期（天数） | Delta | 每张权利金 | 提示 |",
             "|------|------|----------|------|---------|----------|---------"
             "|--------|--------------|-------|------------|------|"]
    for c in rows:
        lines.append(
            f"| {SOURCE_LABEL.get(c['source'], c['source'])} | {_cell(c['label'])} "
            f"| {c['contracts_available']} | ${c['spot']:,.2f} | {_pct(c['ma50_pct'])} "
            f"| {_pct(c['ma200_pct'])} | {_pct(c['month_return_pct'])} | {c['strike']:g} "
            f"| {c['expiry']}（{c['days']} 天） | {c['delta']:.2f} "
            f"| ${c['premium_per_contract']:,.0f} | {_cell('；'.join(c['warnings'])) or '—'} |")
    return lines


def render_summary_md(candidates: list[dict], rejects: list, snapshot: str, generated_on: str,
                      intraday: bool, *, message: str | None = None,
                      dte_mode: str = "weekly", target: float = TARGET_DELTA) -> str:
    kind = "盘中快照" if intraday else "收盘"
    lines = [f"# 备兑 Call 筛选（{MODE_LABEL[dte_mode]}） · 快照 {snapshot}（{kind}）", "",
             f"*生成于 {generated_on}。{rules_line(dte_mode, target)}。*", "",
             f"*{source_line(snapshot, intraday)}。*", ""]
    if message is not None:
        return option_labels.with_banner("\n".join(lines + [message, "", FOOTER, ""]))
    lines += ["## 候选", ""] + (_table(candidates) if candidates else ["无候选。"]) + [""]
    if dte_mode == "weekly":
        lines += [f"> {WEEKLY_WARNING}", ""]
    lines += ["## 被剔除", ""]
    lines += [f"- {r.ticker} — {r.reason}" for r in rejects] or ["无。"]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
