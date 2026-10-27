#!/usr/bin/env python3
"""Rendering for the collar / protective-put screen (pure: no I/O, no network, no clock).

Per-lot walkthrough, summary table and the JSON twins that `open` reads.
See docs/superpowers/specs/2026-10-25-collar-design.md (5, 6, 10).
"""
from __future__ import annotations

import option_labels
from put_screen_render import source_fields, source_line

OPEN_CMD = "./.venv/Scripts/python.exe scripts/collar_screen.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报", "no": "到期前无财报", "unknown": "财报日期未知"}
RULES_LINES = {
    "monthly": "规则：30–60 天最近到期；下限取现价 90% / 85% / 80% 附近不高于目标的真实行权价；"
               "Call 取权利金最接近 Put 保费的虚值行权价；两条腿买卖价差 ≤ 权利金的 5%",
    "long": "规则：90–180 天最近到期；下限取现价 90% / 85% / 80% 附近不高于目标的真实行权价；"
            "Call 取权利金最接近 Put 保费的虚值行权价；两条腿买卖价差 ≤ 权利金的 5%",
}
MODE_LABEL = {"monthly": "中期", "long": "远月"}
SOURCE_LABEL = {"real": "真实", "manual": "假设", "assigned_put": "被指派"}
ZERO_COST_NOTE = ("“零成本”指现金流：Call 收到的权利金大致抵掉 Put 的支出，不是机会成本为零。"
                  "真正的代价是 Call 行权价以上的涨幅被让出。")
PUT_ONLY_LABEL = "仅保护性 Put"
COLLAR_LABEL = "Collar"
IBKR_SPREAD_RULE = "买卖价差 ≤ 权利金的 5%"


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def rules_line(mode: str) -> str:
    return option_labels.rules_text(RULES_LINES[mode], IBKR_SPREAD_RULE)


def _quote_kind(intraday: bool) -> str:
    return "盘中快照" if intraday else "收盘"


def candidate_to_json(c: dict, generated_on: str, intraday: bool) -> dict:
    """Machine-readable twin of the markdown file; `open` reads this one."""
    return {"version": 1, "strategy": "collar", "generated_on": generated_on,
            "intraday": bool(intraday), **source_fields(), "frozen": not intraday, **c}


def summary_to_json(candidates: list[dict], rejects: list, snapshot: str, generated_on: str,
                    intraday: bool, rules: dict) -> dict:
    return {
        "version": 1, "strategy": "collar", "snapshot": snapshot,
        "generated_on": generated_on, "intraday": bool(intraday),
        **source_fields(), "frozen": not intraday, "rules": dict(rules),
        "candidates": [dict(c) for c in candidates],
        "rejects": [{"ticker": r.ticker, "reason": r.reason} for r in rejects],
    }


def _lot_arg(c: dict) -> str:
    return "real" if c["lot_id"] is None else str(c["lot_id"])


def _open_command(c: dict, tier: int, put_only: bool) -> str:
    tail = " --put-only" if put_only else ""
    return f"{OPEN_CMD} {c['ticker']} {tier} --lot {_lot_arg(c)}{tail}"


def _range_text(v: dict) -> str:
    cap = "无上限" if v["cap"] is None else f"${v['cap']:g}"
    return f"${v['floor']:g} ～ {cap}"


def _cap_cost_text(v: dict) -> str:
    return "无" if v["cap_cost"] is None else f"${v['cap_cost']:,.0f}"


def _variant_lines(name: str, v: dict | None, reason: str | None) -> list[str]:
    if v is None:
        return [f"- **{name}**：剔除 — {reason}"]
    net = v["net_credit"]
    lines = [f"- **{name}**",
             f"  - 买入 Put：行权价 ${v['put_strike']:g}（现价以下 {v['put_pct_below_spot']:.1f}%），"
             f"权利金 ${v['put_debit']:.2f}/股"
             + (f"，买卖价差 {v['put_spread_pct'] * 100:.1f}%" if v["put_spread_pct"] is not None else "")]
    if v["call_strike"] is None:
        lines.append("  - 卖出 Call：无（只买保险，没有上限）")
    else:
        lines.append(f"  - 卖出 Call：行权价 ${v['call_strike']:g}，权利金 ${v['call_credit']:.2f}/股"
                     + (f"，买卖价差 {v['call_spread_pct'] * 100:.1f}%"
                        if v["call_spread_pct"] is not None else ""))
    lines += [
        f"  - 净权利金（负数为净支出）：每股 {_signed(net)}，每张 {_signed(net * 100)}",
        f"  - 锁定区间：{_range_text(v)}",
        f"  - 上限代价：每张 {_cap_cost_text(v)}（现价到 Call 行权价的空间；涨过上限的部分被让出）",
    ]
    lines += [f"  - 提示：{w}" for w in v["warnings"]]
    return lines


def _tier_section(t: dict) -> list[str]:
    lines = [f"### {t['tier']}% 档", ""]
    lines += _variant_lines(COLLAR_LABEL, t["collar"], t["collar_reject"])
    lines += _variant_lines(PUT_ONLY_LABEL, t["put_only"], t["put_only_reject"])
    return lines + [""]


def _open_section(c: dict) -> list[str]:
    lines = ["## 怎么开仓", "",
             "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
             "```bash"]
    for t in c["tiers"]:
        if t["collar"] is not None:
            lines.append(_open_command(c, t["tier"], False))
        if t["put_only"] is not None:
            lines.append(_open_command(c, t["tier"], True))
    return lines + ["```", "", "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", ""]


def render_lot_md(c: dict, intraday: bool) -> str:
    """The per-lot walkthrough the user reads before deciding to open."""
    mode = c.get("dte_mode", "monthly")
    lines = [
        f"# {c['label']} Collar / 保护性 Put（{MODE_LABEL[mode]}） · 快照 {c['snapshot_date']}"
        f"（{_quote_kind(intraday)}）", "",
        "*由 `collar_screen` 生成。纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(c['snapshot_date'], intraday)}。*", "", f"*{rules_line(mode)}。*", "",
        "## 合约", "",
        f"- 到期 {c['expiry']}，还有 {c['days']} 天；现价 ${c['spot']:,.2f}；"
        f"财报：{EARNINGS_TEXT[c['earnings']]}。",
        f"- 持有 {c['shares']:g} 股，成本 ${c['cost_basis']:.2f}，参考价 ${c['reference_price']:.2f}；"
        f"Collar 最多 {c['contracts_available']} 张，保护性 Put 最多 "
        f"{c['contracts_available_for_put']} 张（每张 100 股）。", ""]
    if c.get("note"):
        lines += [f"> {c['note']}", ""]
    lines += ["## 三档候选", ""]
    for t in c["tiers"]:
        lines += _tier_section(t)
    lines += [f"> {ZERO_COST_NOTE}", ""] + _open_section(c) + [FOOTER, ""]
    return option_labels.with_banner("\n".join(lines))


def _cell(text: str) -> str:
    """Free text inside a markdown table cell: a literal pipe would split the row."""
    return text.replace("|", "｜")


def _row(c: dict, tier: int, name: str, v: dict | None, reason: str | None) -> str:
    head = f"| {SOURCE_LABEL.get(c['source'], c['source'])} | {_cell(c['label'])} | {tier}% | {name} |"
    if v is None:
        return head + f" — | — | — | — | — | — | — | — | 剔除：{_cell(reason or '')} |"
    call = "—" if v["call_strike"] is None else f"{v['call_strike']:g}"
    call_px = "—" if v["call_strike"] is None else f"${v['call_credit']:.2f}"
    return (head + f" {v['put_strike']:g}（-{v['put_pct_below_spot']:.1f}%） | ${v['put_debit']:.2f} "
            f"| {call} | {call_px} | {_signed(v['net_credit'])} | {_signed(v['net_credit'] * 100)} "
            f"| {_range_text(v)} | {_cap_cost_text(v)} | {_cell('；'.join(v['warnings'])) or '—'} |")


def _table(candidates: list[dict]) -> list[str]:
    lines = ["| 来源 | 标的 | 档位 | 方案 | Put 行权价 | Put 权利金/股 | Call 行权价 | Call 权利金/股 "
             "| 净权利金/股 | 净权利金/张 | 锁定区间 | 上限代价/张 | 提示 |",
             "|------|------|------|------|------------|---------------|-------------|----------------"
             "|-------------|-------------|----------|-------------|------|"]
    for c in candidates:
        for t in c["tiers"]:
            lines.append(_row(c, t["tier"], COLLAR_LABEL, t["collar"], t["collar_reject"]))
            lines.append(_row(c, t["tier"], PUT_ONLY_LABEL, t["put_only"], t["put_only_reject"]))
    return lines


def render_summary_md(candidates: list[dict], rejects: list, snapshot: str, generated_on: str,
                      intraday: bool, *, dte_mode: str = "monthly") -> str:
    lines = [f"# Collar / 保护性 Put 筛选（{MODE_LABEL[dte_mode]}） · 快照 {snapshot}"
             f"（{_quote_kind(intraday)}）", "",
             f"*生成于 {generated_on}。{rules_line(dte_mode)}。*", "",
             f"*{source_line(snapshot, intraday)}。*", "", "## 候选", ""]
    lines += (_table(candidates) if candidates else ["无候选。"]) + [""]
    notes = [f"- {_cell(c['label'])} — {c['note']}" for c in candidates if c.get("note")]
    if notes:
        lines += ["## 说明", ""] + notes + [""]
    lines += [f"> {ZERO_COST_NOTE}", "", "## 被剔除", ""]
    lines += [f"- {r.ticker} — {r.reason}" for r in rejects] or ["无。"]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
