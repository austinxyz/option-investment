#!/usr/bin/env python3
"""Rendering for the iron-condor simulator (pure: no I/O, no network, no clock).

Per-ticker walkthrough, the day's summary table and the JSON twin that `open` reads.
See docs/superpowers/specs/2026-10-25-iron-condor-design.md (5, 12).
"""
from __future__ import annotations

from condor_sim_rules import WARN_UP_MOVE
import option_labels
from put_screen_render import source_fields, source_line
from spread_ledger import CONDOR_STRIKE_KEYS

STRATEGY = "iron_condor"
VERSION = 1
OPEN_CMD = "./.venv/Scripts/python.exe scripts/condor_sim.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报", "no": "到期前无财报", "unknown": "财报日期未知"}
WIDTH_SOURCE = {"auto": "自动推荐", "fallback": "链太薄，用默认值", "manual": "手动指定"}
RULES_LINE = ("规则：到期日同 /spread-sim（距快照日至少 7 天的第一个）；两个短腿 |Delta| 约 0.10 / 0.15 / 0.20"
              "（偏离 > 0.05 剔除）；两侧等宽（Call 长翼恰好在 短 Call + Put 侧实际宽度）；"
              "两个短腿买卖价差 ≤ 5%，长翼价差 > 15% 只警告")
LIQUIDITY_NOTE = ("与策略页“四条腿都要过 5%”不同：长翼通常只有几美分，价差比例天然很大，"
                  "硬卡会剔除几乎所有档位，所以本工具只对两个短腿硬卡 5%，长翼只警告。")
UP_MOVE_NOTE = f"⚠️ {WARN_UP_MOVE}。"
IBKR_SPREAD_RULE = "买卖价差 ≤ 5%"
IBKR_SHORT_LEG_LIMIT = "只对两个短腿硬卡 5%"
YAHOO_SHORT_LEG_LIMIT = "只对两个短腿硬卡 10%（Yahoo 模式放宽；5%–10% 警告）"


def rules_line() -> str:
    return option_labels.rules_text(RULES_LINE, IBKR_SPREAD_RULE)


def liquidity_note() -> str:
    # "四条腿都要过 5%" quotes the strategy page (an IBKR-era rule) and stays as written.
    return option_labels.rules_text(LIQUIDITY_NOTE, IBKR_SHORT_LEG_LIMIT, YAHOO_SHORT_LEG_LIMIT)


def candidate_to_json(snap: dict, generated_on: str) -> dict:
    """Machine-readable twin of the markdown file; `open` reads this one."""
    intraday = bool(snap.get("intraday", False))
    return {"version": VERSION, "strategy": STRATEGY, "generated_on": generated_on,
            **source_fields(), "frozen": not intraday, **snap, "intraday": intraday}


def _cell(text: str) -> str:
    """Free text inside a markdown table cell: a literal pipe would split the row."""
    return text.replace("|", "｜")


def _strikes(c: dict) -> str:
    return "/".join(f"{c[k]:g}" for k in CONDOR_STRIKE_KEYS)


def _breakevens(c: dict) -> str:
    return f"{c['breakeven_low']:.2f} / {c['breakeven_high']:.2f}"


def _quote_kind(intraday: bool) -> str:
    return "盘中快照" if intraday else "收盘"


def _bucket_lines(c: dict) -> list[str]:
    head = f"### Delta {c['delta_bucket']:.2f} 档"
    if c["status"] != "ok":
        return [head, "", f"- 剔除 — {c['reason']}", ""]
    lines = [
        head, "",
        f"- 行权价（长 Put / 短 Put / 短 Call / 长 Call）：{_strikes(c)}，单侧宽度 ${c['width']:g}",
        f"- 短腿实际 Delta：Put {c['put_short_delta']:.2f}，Call {c['call_short_delta']:.2f}",
        f"- Put 侧权利金 ${c['put_credit']:.2f}/股，Call 侧权利金 ${c['call_credit']:.2f}/股，"
        f"净权利金 ${c['net_credit']:.2f}/股（每张 ${c['net_credit'] * 100:,.0f}）",
        f"- 盈亏平衡价：{_breakevens(c)}（收盘在两者之间才赚钱）",
        f"- 最大亏损 / 占用资金：每张 ${c['max_loss']:,.0f}",
        f"- 所需胜率（理论盈亏平衡）：{c['required_win_rate'] * 100:.0f}%",
    ]
    return lines + [f"- 提示：{w}" for w in c["warnings"]] + [""]


def _open_section(doc: dict) -> list[str]:
    ok = [c for c in doc["candidates"] if c["status"] == "ok"]
    if not ok:
        return ["## 怎么开仓", "", "三档都被剔除，本次没有可记录的候选。", ""]
    width = f" --width {doc['width']:g}" if doc.get("width_source") == "manual" else ""
    lines = ["## 怎么开仓", "",
             "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
             "```bash"]
    lines += [f"{OPEN_CMD} {doc['ticker']} {c['delta_bucket']:.2f}{width}" for c in ok]
    return lines + ["```", "", "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", ""]


def render_candidate_md(doc: dict) -> str:
    """The per-ticker walkthrough the user reads before deciding to open."""
    intraday = bool(doc.get("intraday", False))
    width_src = WIDTH_SOURCE.get(doc.get("width_source"), "")
    lines = [
        f"# {doc['ticker']} 铁鹰候选 · 快照 {doc['snapshot_date']}（{_quote_kind(intraday)}）", "",
        "*由 `condor_sim` 生成。纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(doc['snapshot_date'], intraday)}。*", "", f"*{rules_line()}。*", "",
        f"> {UP_MOVE_NOTE}", "",
        "## 合约", "",
        f"- 到期 {doc['expiry']}，还有 {doc['days']} 天；现价 ${doc['spot']:,.2f}；"
        f"{EARNINGS_TEXT.get(doc['earnings'], doc['earnings'])}"
        + (f"；除息日 {doc['ex_dividend']}" if doc.get("ex_dividend") else "") + "。",
        f"- 目标单侧宽度 ${doc['width']:g}（{width_src}；实际宽度以 Put 侧真实行权价为准）。", "",
        "## 三档候选", ""]
    for c in doc["candidates"]:
        lines += _bucket_lines(c)
    lines += [f"> {liquidity_note()}", ""] + _open_section(doc) + [FOOTER, ""]
    return option_labels.with_banner("\n".join(lines))


TABLE_HEAD = ("| 标的 | 档位 | 长 Put | 短 Put | 短 Call | 长 Call | 宽度 | Put 侧权利金 | Call 侧权利金 "
              "| 净权利金/股 | 盈亏平衡 | 最大亏损/张（占用资金） | 所需胜率 | 提示 |")
TABLE_RULE = "|" + "------|" * 14


def _row(ticker: str, c: dict) -> str:
    head = f"| {ticker} | {c['delta_bucket']:.2f} |"
    if c["status"] != "ok":
        return head + " — |" * 11 + f" 剔除：{_cell(c['reason'])} |"
    tips = [w for w in c["warnings"] if w != WARN_UP_MOVE]
    return (head + f" {c['put_long_strike']:g} | {c['put_short_strike']:g} | {c['call_short_strike']:g} "
            f"| {c['call_long_strike']:g} | ${c['width']:g} | ${c['put_credit']:.2f} "
            f"| ${c['call_credit']:.2f} | ${c['net_credit']:.2f} | {_breakevens(c)} "
            f"| ${c['max_loss']:,.0f} | {c['required_win_rate'] * 100:.0f}% "
            f"| {_cell('；'.join(tips)) or '—'} |")


def _rejected(docs: list[dict]) -> list[str]:
    out = [f"- {d['ticker']} Delta {c['delta_bucket']:.2f} — {c['reason']}"
           for d in docs for c in d["candidates"] if c["status"] != "ok"]
    return out or ["无。"]


def render_summary_md(docs: list[dict], snapshot: str, generated_on: str,
                      unreadable: list[str] | None = None) -> str:
    """The day's table: one row per ticker and delta bucket (rejected buckets give the reason)."""
    intraday = any(d.get("intraday") for d in docs)
    lines = [f"# 铁鹰候选汇总 · 快照 {snapshot}（{_quote_kind(intraday)}）", "",
             f"*生成于 {generated_on}。{rules_line()}。*", "",
             f"*{source_line(snapshot, intraday)}。*", "", f"> {UP_MOVE_NOTE}", "",
             "## 候选", ""]
    rows = [_row(d["ticker"], c) for d in docs for c in d["candidates"]]
    lines += ([TABLE_HEAD, TABLE_RULE] + rows if rows else ["无候选。"]) + [""]
    lines += ["“最大亏损/张”就是每张占用的资金：(宽度 − 净权利金) × 100；"
              "“所需胜率” = (宽度 − 净权利金) ÷ 宽度。", "",
              f"> {liquidity_note()}", "", "## 被剔除", ""] + _rejected(docs)
    if unreadable:
        lines += ["", "## 未列入（文件损坏）", ""] + [f"- {name}" for name in unreadable]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
