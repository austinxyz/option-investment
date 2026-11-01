#!/usr/bin/env python3
"""Rendering for the bear call credit spread simulator (pure: no I/O, no network, no clock).

Per-ticker walkthrough, the day's summary table and the JSON twin that `open` reads.
breakeven_move_pct / max_loss_move_pct are FRACTIONS (0.062 -> "+6.20%").
See docs/superpowers/specs/2026-10-27-bearish-and-new-strategies-design.md (5.1-5.5, 5.12, 8).
"""
from __future__ import annotations

from bearcall_sim_rules import WARN_BEAR_CALL_UPSIDE, WARN_DRIFT
import option_labels
from put_screen_render import source_fields, source_line
from spread_ledger import BEARCALL_LABEL, STRATEGY_BEAR_CALL

STRATEGY = STRATEGY_BEAR_CALL
VERSION = 1
OPEN_CMD = "./.venv/Scripts/python.exe scripts/bearcall_sim.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报", "no": "到期前无财报", "unknown": "财报日期未知"}
WIDTH_SOURCE = {"auto": "按 Call 链自动推荐", "fallback": "链太薄，用默认值", "manual": "手动指定"}
RULES_LINE = ("规则：到期日取距快照日至少 7 天的第一个；短腿取虚值 Call，Delta 约 "
              "0.10 / 0.15 / 0.20（偏离 > 0.05 剔除）；长腿恰好在 短腿 + 宽度；"
              "两条腿买卖价差都 ≤ 5%；净权利金须在 (0, 宽度) 之内")
IBKR_SPREAD_RULE = "买卖价差都 ≤ 5%"
# Shown once as a banner; per-bucket lists leave them out (they are on every bucket).
FIXED_WARNINGS = (WARN_BEAR_CALL_UPSIDE, WARN_DRIFT)


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


def _banner() -> list[str]:
    return [f"> ⚠️ {w}。" for w in FIXED_WARNINGS] + [""]


def _tips(c: dict) -> list[str]:
    return [w for w in c["warnings"] if w not in FIXED_WARNINGS]


def shared_short_notes(candidates: list[dict]) -> list[str]:
    """Buckets that picked the same short call (a coarse chain): only one of them can be opened."""
    by_short: dict[float, list[float]] = {}
    for c in candidates:
        if c["status"] == "ok":
            by_short.setdefault(c["short_strike"], []).append(c["delta_bucket"])
    return [f"Delta {' / '.join(f'{b:.2f}' for b in buckets)} 档选到同一短腿 Call {strike:g}："
            "同一 ticker 同一到期日只能开一笔，开了其中一档，另一档会被拒绝"
            for strike, buckets in by_short.items() if len(buckets) > 1]


def _bucket_lines(c: dict) -> list[str]:
    head = f"### Delta {c['delta_bucket']:.2f} 档"
    if c["status"] != "ok":
        return [head, "", f"- 剔除 — {c['reason']}", ""]
    lines = [
        head, "",
        f"- 卖 Call {c['short_strike']:g}（中间价 ${c['short_mid']:.2f}，Delta "
        f"{c['short_delta']:.2f}） / 买 Call {c['long_strike']:g}（中间价 "
        f"${c['long_mid']:.2f}），宽度 ${c['width']:g}",
        f"- 净权利金 ${c['credit']:.2f}/股 = 最大收益：每张 ${c['max_profit']:,.0f}"
        f"（到期收盘 ≤ 短腿 {c['short_strike']:g}）",
        f"- 最大亏损 / 占用资金：每张 ${c['max_loss']:,.0f}（到期收盘 ≥ 长腿 {c['long_strike']:g}）",
        f"- 盈亏平衡价 {c['breakeven']:.2f}（回本幅度 {_move(c['breakeven_move_pct'])}），"
        f"满额亏损幅度 {_move(c['max_loss_move_pct'])}",
        f"- 所需胜率（1 − 权利金 ÷ 宽度）：{c['required_win_rate'] * 100:.0f}%",
    ]
    return lines + [f"- 提示：{w}" for w in _tips(c)] + [""]


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
    lines += [f"> {note}。" for note in shared_short_notes(doc["candidates"])]
    return lines + ["", "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", ""]


def _contract_lines(doc: dict) -> list[str]:
    width_src = WIDTH_SOURCE.get(doc.get("width_source"), "")
    ex_div = f"；除息日 {doc['ex_dividend']}" if doc.get("ex_dividend") else ""
    return [
        "## 合约", "",
        f"- 到期 {doc['expiry']}，还有 {doc['days']} 天；现价 ${doc['spot']:,.2f}；"
        f"{EARNINGS_TEXT.get(doc['earnings'], doc['earnings'])}{ex_div}。",
        f"- 宽度 ${doc['width']:g}（{width_src}）。", ""]


def render_candidate_md(doc: dict) -> str:
    """The per-ticker walkthrough the user reads before deciding to open."""
    intraday = bool(doc.get("intraday", False))
    lines = [
        f"# {doc['ticker']} {BEARCALL_LABEL}候选 · 快照 {doc['snapshot_date']}"
        f"（{_quote_kind(intraday)}）", "",
        "*由 `bearcall_sim` 生成。看跌策略，纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(doc['snapshot_date'], intraday)}。*", "", f"*{rules_line()}。*", ""]
    lines += _banner() + _contract_lines(doc) + ["## 三档候选", ""]
    for c in doc["candidates"]:
        lines += _bucket_lines(c)
    return option_labels.with_banner("\n".join(lines + _open_section(doc) + [FOOTER, ""]))


TABLE_HEAD = ("| 标的 | 档位 | 短 Call | 长 Call | 宽度 | 短腿中间价 | 长腿中间价 | 权利金/股 "
              "| 盈亏平衡 | 最大收益/张 | 最大亏损/张 | 所需胜率 | 回本幅度 | 满额亏损幅度 | 提示 |")
TABLE_RULE = "|" + "------|" * 15


def _row(ticker: str, c: dict) -> str:
    head = f"| {ticker} | {c['delta_bucket']:.2f} |"
    if c["status"] != "ok":
        return head + " — |" * 12 + f" 剔除：{_cell(c['reason'])} |"
    return (head + f" {c['short_strike']:g} | {c['long_strike']:g} | ${c['width']:g} "
            f"| ${c['short_mid']:.2f} | ${c['long_mid']:.2f} | ${c['credit']:.2f} "
            f"| {c['breakeven']:.2f} | ${c['max_profit']:,.0f} | ${c['max_loss']:,.0f} "
            f"| {c['required_win_rate'] * 100:.0f}% | {_move(c['breakeven_move_pct'])} "
            f"| {_move(c['max_loss_move_pct'])} | {_cell('；'.join(_tips(c))) or '—'} |")


def _rejected(docs: list[dict]) -> list[str]:
    out = [f"- {d['ticker']} Delta {c['delta_bucket']:.2f} — {c['reason']}"
           for d in docs for c in d["candidates"] if c["status"] != "ok"]
    return out or ["无。"]


def _shared(docs: list[dict]) -> list[str]:
    notes = [f"- {d['ticker']}：{note}" for d in docs for note in shared_short_notes(d["candidates"])]
    return ["## 同一短腿", ""] + notes + [""] if notes else []


def render_summary_md(docs: list[dict], snapshot: str, generated_on: str,
                      unreadable: list[str] | None = None) -> str:
    """The day's table: one row per ticker and delta bucket (rejected buckets give the reason)."""
    intraday = any(d.get("intraday") for d in docs)
    lines = [f"# {BEARCALL_LABEL}候选汇总 · 快照 {snapshot}（{_quote_kind(intraday)}）", "",
             f"*生成于 {generated_on}。看跌策略。{rules_line()}。*", "",
             f"*{source_line(snapshot, intraday)}。*", ""] + _banner() + ["## 候选", ""]
    rows = [_row(d["ticker"], c) for d in docs for c in d["candidates"]]
    lines += ([TABLE_HEAD, TABLE_RULE] + rows if rows else ["无候选。"]) + [""]
    lines += ["“最大收益/张” = 权利金 × 100；“最大亏损/张”就是每张占用的资金：(宽度 − 权利金) × 100；"
              "“所需胜率” = 1 − 权利金 ÷ 宽度；回本幅度 = 盈亏平衡价 ÷ 现价 − 1，"
              "满额亏损幅度 = 长腿 ÷ 现价 − 1。", ""] + _shared(docs) + ["## 被剔除", ""] + _rejected(docs)
    if unreadable:
        lines += ["", "## 未列入（文件损坏）", ""] + [f"- {name}" for name in unreadable]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
