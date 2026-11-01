#!/usr/bin/env python3
"""Rendering for the long put simulator (pure: no I/O, no network, no clock).

Per-ticker walkthrough, the day's summary table and the JSON twin that `open` reads.
breakeven_move_pct is a FRACTION (-0.074 -> "-7.40%"). Single buyer leg: no width, no spread
columns, no required win rate.
See docs/superpowers/specs/2026-10-30-long-put-design.md (sections 2-3).
"""
from __future__ import annotations

import option_labels
import spread_core as core
from longput_sim_rules import WARN_THETA_DECAY
from put_screen_render import source_fields, source_line
from spread_ledger import LONGPUT_LABEL, STRATEGY_LONG_PUT

STRATEGY = STRATEGY_LONG_PUT
VERSION = 1
OPEN_CMD = "./.venv/Scripts/python.exe scripts/longput_sim.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报", "no": "到期前无财报", "unknown": "财报日期未知"}
NO_REQUIRED = "无所需胜率（收益没有上限，复盘看平均赢额 ÷ 平均亏额与回本需下跌幅度）"
RULES_LINE = ("规则：到期日取距快照日至少 --min-days（默认 30）天的第一个；单腿 Put |Delta| 约 "
              "0.50 / 0.40 / 0.30（偏离 > 0.05 剔除）；买卖价差 ≤ 5%；净支出 = 中间价，须 > 0")
IBKR_SPREAD_RULE = "买卖价差 ≤ 5%"
# Shown once as a banner; per-bucket lists leave it out (it is on every bucket).
FIXED_WARNINGS = (WARN_THETA_DECAY,)


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


def _money(dollars: float) -> str:
    """Signed whole dollars, never "-$0"."""
    value = round(dollars) + 0
    return f"+${value:,.0f}" if value >= 0 else f"-${-value:,.0f}"


def _quote_kind(intraday: bool) -> str:
    return "盘中快照" if intraday else "收盘"


def _banner() -> list[str]:
    return [f"> ⚠️ {w}。" for w in FIXED_WARNINGS] + [""]


def _tips(c: dict) -> list[str]:
    return [w for w in c["warnings"] if w not in FIXED_WARNINGS]


def shared_strike_notes(candidates: list[dict]) -> list[str]:
    """Buckets that picked the same put (a coarse chain): only one of them can be opened."""
    by_strike: dict[float, list[float]] = {}
    for c in candidates:
        if c["status"] == "ok":
            by_strike.setdefault(c["put_strike"], []).append(c["delta_bucket"])
    return [f"Delta {' / '.join(f'{b:.2f}' for b in buckets)} 档选到同一 Put {strike:g}："
            "同一 ticker 同一到期日只能开一笔，开了其中一档，另一档会被拒绝"
            for strike, buckets in by_strike.items() if len(buckets) > 1]


def _bucket_lines(c: dict) -> list[str]:
    head = f"### Delta {c['delta_bucket']:.2f} 档"
    if c["status"] != "ok":
        return [head, "", f"- 剔除 — {c['reason']}", ""]
    loss = c["max_loss"]
    lines = [
        head, "",
        f"- 买 Put {c['put_strike']:g}（中间价 ${c['put_mid']:.2f}，|Delta| {c['put_delta']:.2f}）",
        f"- 净支出 ${c['debit']:.2f}/股 = 最大亏损 / 占用资金：每张 ${loss:,.0f}"
        f"（到期收盘 ≥ 行权价 {c['put_strike']:g}）",
        f"- 最大收益（仅展示，标的跌到 0）：每张 ${c['max_profit']:,.0f}",
        f"- 盈亏平衡价 {c['breakeven']:.2f}（回本幅度 {_move(c['breakeven_move_pct'])}）",
        f"- 止盈参考 {_money(core.LONGPUT_TAKE_PROFIT_FRACTION * loss)}"
        f"（浮盈 ≥ 净支出的 {core.LONGPUT_TAKE_PROFIT_FRACTION * 100:.0f}%），"
        f"止损参考 {_money(-core.LONGPUT_STOP_FRACTION * loss)}"
        f"（浮亏 ≥ 净支出的 {core.LONGPUT_STOP_FRACTION * 100:.0f}%）",
        f"- {NO_REQUIRED}",
    ]
    return lines + [f"- 提示：{w}" for w in _tips(c)] + [""]


def _open_section(doc: dict) -> list[str]:
    ok = [c for c in doc["candidates"] if c["status"] == "ok"]
    if not ok:
        return ["## 怎么开仓", "", "三档都被剔除，本次没有可记录的候选。", ""]
    lines = ["## 怎么开仓", "",
             "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
             "```bash"]
    lines += [f"{OPEN_CMD} {doc['ticker']} {c['delta_bucket']:.2f}" for c in ok]
    lines += ["```", ""]
    lines += [f"> {note}。" for note in shared_strike_notes(doc["candidates"])]
    return lines + ["", "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", ""]


def _contract_lines(doc: dict) -> list[str]:
    ex_div = f"；除息日 {doc['ex_dividend']}" if doc.get("ex_dividend") else ""
    return [
        "## 合约", "",
        f"- 到期 {doc['expiry']}，还有 {doc['days']} 天（至少 {doc.get('min_days', '—')} 天）；"
        f"现价 ${doc['spot']:,.2f}；{EARNINGS_TEXT.get(doc['earnings'], doc['earnings'])}{ex_div}。",
        ""]


def render_candidate_md(doc: dict) -> str:
    """The per-ticker walkthrough the user reads before deciding to open."""
    intraday = bool(doc.get("intraday", False))
    lines = [
        f"# {doc['ticker']} {LONGPUT_LABEL}候选 · 快照 {doc['snapshot_date']}"
        f"（{_quote_kind(intraday)}）", "",
        "*由 `longput_sim` 生成。看跌策略（买方单腿），纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(doc['snapshot_date'], intraday)}。*", "", f"*{rules_line()}。*", ""]
    lines += _banner() + _contract_lines(doc) + ["## 三档候选", ""]
    for c in doc["candidates"]:
        lines += _bucket_lines(c)
    return option_labels.with_banner("\n".join(lines + _open_section(doc) + [FOOTER, ""]))


TABLE_HEAD = ("| 标的 | 档位 | Put 行权价 | 中间价 | |Delta| | 净支出/股 | 盈亏平衡 "
              "| 最大亏损/张 | 最大收益/张（跌到 0） | 回本幅度 | 提示 |")
TABLE_RULE = "|" + "------|" * 11


def _row(ticker: str, c: dict) -> str:
    head = f"| {ticker} | {c['delta_bucket']:.2f} |"
    if c["status"] != "ok":
        return head + " — |" * 8 + f" 剔除：{_cell(c['reason'])} |"
    return (head + f" {c['put_strike']:g} | ${c['put_mid']:.2f} | {c['put_delta']:.2f} "
            f"| ${c['debit']:.2f} | {c['breakeven']:.2f} | ${c['max_loss']:,.0f} "
            f"| ${c['max_profit']:,.0f} | {_move(c['breakeven_move_pct'])} "
            f"| {_cell('；'.join(_tips(c))) or '—'} |")


def _rejected(docs: list[dict]) -> list[str]:
    out = [f"- {d['ticker']} Delta {c['delta_bucket']:.2f} — {c['reason']}"
           for d in docs for c in d["candidates"] if c["status"] != "ok"]
    return out or ["无。"]


def _shared(docs: list[dict]) -> list[str]:
    notes = [f"- {d['ticker']}：{note}" for d in docs for note in shared_strike_notes(d["candidates"])]
    return ["## 同一 Put", ""] + notes + [""] if notes else []


def render_summary_md(docs: list[dict], snapshot: str, generated_on: str,
                      unreadable: list[str] | None = None) -> str:
    """The day's table: one row per ticker and delta bucket (rejected buckets give the reason)."""
    intraday = any(d.get("intraday") for d in docs)
    lines = [f"# {LONGPUT_LABEL}候选汇总 · 快照 {snapshot}（{_quote_kind(intraday)}）", "",
             f"*生成于 {generated_on}。看跌策略（买方单腿）。{rules_line()}。*", "",
             f"*{source_line(snapshot, intraday)}。*", ""] + _banner() + ["## 候选", ""]
    rows = [_row(d["ticker"], c) for d in docs for c in d["candidates"]]
    lines += ([TABLE_HEAD, TABLE_RULE] + rows if rows else ["无候选。"]) + [""]
    lines += ["“最大亏损/张”就是每张占用的资金：净支出 × 100；“最大收益/张（跌到 0）” = "
              "(行权价 − 净支出) × 100，仅作展示；回本幅度 = 盈亏平衡价 ÷ 现价 − 1。"
              f"{NO_REQUIRED}。", ""] + _shared(docs) + ["## 被剔除", ""] + _rejected(docs)
    if unreadable:
        lines += ["", "## 未列入（文件损坏）", ""] + [f"- {name}" for name in unreadable]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
