#!/usr/bin/env python3
"""Rendering for the bull call debit spread simulator (pure: no I/O, no network, no clock).

Per-ticker walkthrough, the day's summary table and the JSON twin that `open` reads.
breakeven_move_pct / max_profit_move_pct are FRACTIONS (0.019 -> "+1.90%").
See docs/superpowers/specs/2026-10-25-bull-call-spread-design.md (5, 8, 12).
"""
from __future__ import annotations

from bullcall_sim_rules import WARN_FULL_DEBIT
import option_labels
from put_screen_render import source_fields, source_line
from spread_ledger import BULLCALL_LABEL, STRATEGY_BULL_CALL

STRATEGY = STRATEGY_BULL_CALL
VERSION = 1
OPEN_CMD = "./.venv/Scripts/python.exe scripts/bullcall_sim.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报", "no": "到期前无财报", "unknown": "财报日期未知"}
WIDTH_SOURCE = {"auto": "按 Call 链自动推荐", "fallback": "链太薄，用默认值", "manual": "手动指定"}
RULES_LINE = ("规则：到期日取距快照日至少 --min-days（默认 30）天的第一个；长腿 Call Delta 约 "
              "0.60 / 0.50 / 0.40（偏离 > 0.05 剔除，可以是实值）；短腿恰好在 长腿 + 宽度；"
              "两条腿买卖价差都 ≤ 5%；净支出须在 (0, 宽度) 之内")
FULL_DEBIT_NOTE = f"⚠️ {WARN_FULL_DEBIT}。"
IBKR_SPREAD_RULE = "买卖价差都 ≤ 5%"


def rules_line() -> str:
    return option_labels.rules_text(RULES_LINE, IBKR_SPREAD_RULE)


def candidate_to_json(snap: dict, generated_on: str) -> dict:
    """Machine-readable twin of the markdown file; `open` reads this one."""
    intraday = bool(snap.get("intraday", False))
    return {"version": VERSION, "strategy": STRATEGY, "generated_on": generated_on,
            **source_fields(), "frozen": not intraday, **snap, "intraday": intraday}


def _cell(text: str) -> str:
    """Free text inside a markdown table cell: a literal pipe would split the row."""
    return text.replace("|", "｜")


def _move(fraction: float) -> str:
    """Signed percent of a stored fraction, never "-0.00%"."""
    return f"{round(fraction * 100, 2) + 0.0:+.2f}%"


def _quote_kind(intraday: bool) -> str:
    return "盘中快照" if intraday else "收盘"


def shared_long_notes(candidates: list[dict]) -> list[str]:
    """Buckets that picked the same long call (a coarse chain): only one of them can be opened."""
    by_long: dict[float, list[float]] = {}
    for c in candidates:
        if c["status"] == "ok":
            by_long.setdefault(c["long_call_strike"], []).append(c["delta_bucket"])
    return [f"Delta {' / '.join(f'{b:.2f}' for b in buckets)} 档选到同一长腿 Call {strike:g}："
            "同一 ticker 同一到期日只能开一笔，开了其中一档，另一档会被拒绝"
            for strike, buckets in by_long.items() if len(buckets) > 1]


def _bucket_lines(c: dict) -> list[str]:
    head = f"### Delta {c['delta_bucket']:.2f} 档"
    if c["status"] != "ok":
        return [head, "", f"- 剔除 — {c['reason']}", ""]
    lines = [
        head, "",
        f"- 买 Call {c['long_call_strike']:g}（中间价 ${c['long_call_mid']:.2f}，Delta "
        f"{c['long_call_delta']:.2f}） / 卖 Call {c['short_call_strike']:g}（中间价 "
        f"${c['short_call_mid']:.2f}，Delta {c['short_call_delta']:.2f}），宽度 ${c['width']:g}",
        f"- 净支出 ${c['debit']:.2f}/股 = 最大亏损 / 占用资金：每张 ${c['max_loss']:,.0f}",
        f"- 最大收益：每张 ${c['max_profit']:,.0f}（到期收盘 ≥ 短腿 {c['short_call_strike']:g}）",
        f"- 盈亏平衡价 {c['breakeven']:.2f}（回本幅度 {_move(c['breakeven_move_pct'])}），"
        f"满额幅度 {_move(c['max_profit_move_pct'])}",
        f"- 所需胜率（净支出 ÷ 宽度）：{c['required_win_rate'] * 100:.0f}%",
    ]
    # the full-debit warning is the banner above; repeating it per bucket is noise
    return lines + [f"- 提示：{w}" for w in c["warnings"] if w != WARN_FULL_DEBIT] + [""]


def _open_section(doc: dict) -> list[str]:
    ok = [c for c in doc["candidates"] if c["status"] == "ok"]
    if not ok:
        return ["## 怎么开仓", "", "三档都被剔除，本次没有可记录的候选。", ""]
    width = f" --width {doc['width']:g}" if doc.get("width_source") == "manual" else ""
    lines = ["## 怎么开仓", "",
             "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
             "```bash"]
    lines += [f"{OPEN_CMD} {doc['ticker']} {c['delta_bucket']:.2f}{width}" for c in ok]
    lines += ["```", ""]
    lines += [f"> {note}。" for note in shared_long_notes(doc["candidates"])]
    return lines + ["", "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", ""]


def _contract_lines(doc: dict) -> list[str]:
    width_src = WIDTH_SOURCE.get(doc.get("width_source"), "")
    ex_div = f"；除息日 {doc['ex_dividend']}" if doc.get("ex_dividend") else ""
    return [
        "## 合约", "",
        f"- 到期 {doc['expiry']}，还有 {doc['days']} 天（至少 {doc.get('min_days', '—')} 天）；"
        f"现价 ${doc['spot']:,.2f}；{EARNINGS_TEXT.get(doc['earnings'], doc['earnings'])}{ex_div}。",
        f"- 宽度 ${doc['width']:g}（{width_src}）。", ""]


def render_candidate_md(doc: dict) -> str:
    """The per-ticker walkthrough the user reads before deciding to open."""
    intraday = bool(doc.get("intraday", False))
    lines = [
        f"# {doc['ticker']} {BULLCALL_LABEL}候选 · 快照 {doc['snapshot_date']}"
        f"（{_quote_kind(intraday)}）", "",
        "*由 `bullcall_sim` 生成。纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(doc['snapshot_date'], intraday)}。*", "", f"*{rules_line()}。*", "",
        f"> {FULL_DEBIT_NOTE}", ""]
    lines += _contract_lines(doc) + ["## 三档候选", ""]
    for c in doc["candidates"]:
        lines += _bucket_lines(c)
    return option_labels.with_banner("\n".join(lines + _open_section(doc) + [FOOTER, ""]))


TABLE_HEAD = ("| 标的 | 档位 | 长 Call | 短 Call | 宽度 | 长腿中间价 | 短腿中间价 | 净支出/股 "
              "| 盈亏平衡 | 最大收益/张 | 最大亏损/张 | 所需胜率 | 回本幅度 | 满额幅度 | 提示 |")
TABLE_RULE = "|" + "------|" * 15


def _row(ticker: str, c: dict) -> str:
    head = f"| {ticker} | {c['delta_bucket']:.2f} |"
    if c["status"] != "ok":
        return head + " — |" * 12 + f" 剔除：{_cell(c['reason'])} |"
    tips = [w for w in c["warnings"] if w != WARN_FULL_DEBIT]
    return (head + f" {c['long_call_strike']:g} | {c['short_call_strike']:g} | ${c['width']:g} "
            f"| ${c['long_call_mid']:.2f} | ${c['short_call_mid']:.2f} | ${c['debit']:.2f} "
            f"| {c['breakeven']:.2f} | ${c['max_profit']:,.0f} | ${c['max_loss']:,.0f} "
            f"| {c['required_win_rate'] * 100:.0f}% | {_move(c['breakeven_move_pct'])} "
            f"| {_move(c['max_profit_move_pct'])} | {_cell('；'.join(tips)) or '—'} |")


def _rejected(docs: list[dict]) -> list[str]:
    out = [f"- {d['ticker']} Delta {c['delta_bucket']:.2f} — {c['reason']}"
           for d in docs for c in d["candidates"] if c["status"] != "ok"]
    return out or ["无。"]


def _shared(docs: list[dict]) -> list[str]:
    notes = [f"- {d['ticker']}：{note}" for d in docs for note in shared_long_notes(d["candidates"])]
    return ["## 同一长腿", ""] + notes + [""] if notes else []


def render_summary_md(docs: list[dict], snapshot: str, generated_on: str,
                      unreadable: list[str] | None = None) -> str:
    """The day's table: one row per ticker and delta bucket (rejected buckets give the reason)."""
    intraday = any(d.get("intraday") for d in docs)
    lines = [f"# {BULLCALL_LABEL}候选汇总 · 快照 {snapshot}（{_quote_kind(intraday)}）", "",
             f"*生成于 {generated_on}。{rules_line()}。*", "",
             f"*{source_line(snapshot, intraday)}。*", "", f"> {FULL_DEBIT_NOTE}", "",
             "## 候选", ""]
    rows = [_row(d["ticker"], c) for d in docs for c in d["candidates"]]
    lines += ([TABLE_HEAD, TABLE_RULE] + rows if rows else ["无候选。"]) + [""]
    lines += ["“最大亏损/张”就是每张占用的资金：净支出 × 100；“最大收益/张” = (宽度 − 净支出) × 100；"
              "“所需胜率” = 净支出 ÷ 宽度；回本幅度 = 盈亏平衡价 ÷ 现价 − 1，满额幅度 = 短腿 ÷ 现价 − 1。",
              ""] + _shared(docs) + ["## 被剔除", ""] + _rejected(docs)
    if unreadable:
        lines += ["", "## 未列入（文件损坏）", ""] + [f"- {name}" for name in unreadable]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
