#!/usr/bin/env python3
"""Rendering for the risk reversal simulator (pure: no I/O, no network, no clock).

Per-ticker walkthrough, the day's summary table and the JSON twin that `open` reads.
put_move_pct / call_move_pct are FRACTIONS (-0.10 -> "-10.00%"). net_credit > 0 is a net
credit (净收入), < 0 a net debit (净支出). Max profit is unlimited; max loss is the
conservative cash-secured capital (the stock can fall to zero).
See docs/superpowers/specs/2026-10-27-risk-reversal-design.md (5.3, 5.4, 5.8, 5.13, 8).
"""
from __future__ import annotations

import option_labels
from put_screen_render import source_fields, source_line
from rr_sim_rules import FIXED_WARNINGS
from spread_ledger import RR_LABEL, STRATEGY_RR

STRATEGY = STRATEGY_RR
VERSION = 1
OPEN_CMD = "./.venv/Scripts/python.exe scripts/rr_sim.py open"
FOOTER = "模拟/研究用途，非投资建议"
EARNINGS_TEXT = {"yes": "到期前有财报", "no": "到期前无财报", "unknown": "财报日期未知"}
CAPITAL_NOTE = "现金担保口径，券商实际保证金因家而异"
RULES_LINE = ("规则：到期日取 30–60 天内最近的一个（--min-days 可放宽下限）；卖出的 Put 按 |Delta| "
              "0.30 / 0.20 / 0.15 选档（偏离 > 0.05 剔除，且必须是虚值）；买入的 Call 取虚值 Call 里"
              "中间价最接近短 Put 的一个；两条腿买卖价差都 ≤ 5%；净权利金可正可负")
IBKR_SPREAD_RULE = "买卖价差都 ≤ 5%"
UNLIMITED = "无上限"
UPSIDE_SAFE = "Call 行权价以下已不亏"


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


def premium_label(net_credit: float) -> str:
    return "净收入" if net_credit >= 0 else "净支出"


def premium_text(net_credit: float) -> str:
    return f"{premium_label(net_credit)} ${abs(net_credit):.2f}/股"


def dead_zone_text(c: dict) -> str:
    low, high = c["dead_zone"]
    return f"{low:g} ～ {high:g}"


def upside_text(c: dict) -> str:
    if c.get("upside_breakeven") is None:
        return UPSIDE_SAFE
    return f"{c['upside_breakeven']:.2f}（Call 行权价 + 净支出）"


def _leg_lines(c: dict) -> list[str]:
    call_delta = "—" if c["long_call_delta"] is None else f"{c['long_call_delta']:.2f}"
    return [
        f"- 卖 Put {c['short_put_strike']:g}（中间价 ${c['short_put_mid']:.2f}，Delta "
        f"{c['short_put_delta']:.2f}，较现价 {_move(c['put_move_pct'])}） / 买 Call "
        f"{c['long_call_strike']:g}（中间价 ${c['long_call_mid']:.2f}，Delta {call_delta}，"
        f"较现价 {_move(c['call_move_pct'])}）",
        f"- {premium_text(c['net_credit'])}（卖 Put − 买 Call）",
    ]


def _money_lines(c: dict) -> list[str]:
    return [
        f"- 占用资金（保守口径）：每张 ${c['capital_basis']:,.0f}（{CAPITAL_NOTE}）；"
        f"最大亏损同此数（股价归零时；下行亏损一直算到归零）",
        f"- 最大收益 {UNLIMITED}；下行盈亏平衡价 {c['breakeven_down']:.2f}",
        f"- 死区 {dead_zone_text(c)}（两腿都作废）；上方回本价：{upside_text(c)}",
    ]


def _bucket_lines(c: dict) -> list[str]:
    head = f"### 短 Put Delta {c['delta_bucket']:.2f} 档"
    if c["status"] != "ok":
        return [head, "", f"- 剔除 — {c['reason']}", ""]
    lines = [head, ""] + _leg_lines(c) + _money_lines(c)
    return lines + [f"- 提示：{w}" for w in c["warnings"]] + [""]


def _open_section(doc: dict) -> list[str]:
    ok = [c for c in doc["candidates"] if c["status"] == "ok"]
    if not ok:
        return ["## 怎么开仓", "", "三档都被剔除，本次没有可记录的候选。", ""]
    lines = ["## 怎么开仓", "",
             "这是候选，**未写入账本**。确认后运行，记录的就是你刚读过的这份候选，不会重新取价：", "",
             "```bash"]
    lines += [f"{OPEN_CMD} {doc['ticker']} {c['delta_bucket']:.2f}" for c in ok]
    lines += ["```", "", "同一 ticker 同一到期日只能开一笔（任意策略）。",
              "模拟结果会略偏乐观：真实市场无法恰好按快照价成交。", ""]
    return lines


def _contract_lines(doc: dict) -> list[str]:
    if doc.get("expiry") is None:
        return ["## 合约", "", f"- 现价 ${doc['spot']:,.2f}；没有可用的到期日（见下方原因）。", ""]
    ex_div = f"；除息日 {doc['ex_dividend']}" if doc.get("ex_dividend") else ""
    return ["## 合约", "",
            f"- 到期 {doc['expiry']}，还有 {doc['days']} 天（至少 {doc.get('min_days', '—')} 天）；"
            f"现价 ${doc['spot']:,.2f}；{EARNINGS_TEXT.get(doc['earnings'], doc['earnings'])}{ex_div}。",
            ""]


def _banner() -> list[str]:
    return [f"> ⚠️ {w}。" for w in FIXED_WARNINGS] + [""]


def render_candidate_md(doc: dict) -> str:
    """The per-ticker walkthrough the user reads before deciding to open."""
    intraday = bool(doc.get("intraday", False))
    lines = [
        f"# {doc['ticker']} {RR_LABEL}候选 · 快照 {doc['snapshot_date']}"
        f"（{_quote_kind(intraday)}）", "",
        "*由 `rr_sim` 生成。纸上交易，未写入账本；确认后用 `open` 记录。*", "",
        f"*{source_line(doc['snapshot_date'], intraday)}。*", "", f"*{rules_line()}。*", ""]
    lines += _contract_lines(doc) + ["## 三档候选", ""]
    for c in doc["candidates"]:
        lines += _bucket_lines(c)
    return option_labels.with_banner("\n".join(lines + _open_section(doc) + [FOOTER, ""]))


TABLE_HEAD = ("| 标的 | 档位 | 卖 Put | 买 Call | Put 中间价 | Call 中间价 | 净权利金/股 "
              "| 占用资金/张（保守） | 下行盈亏平衡 | 死区 | Put 幅度 | Call 幅度 | 上方回本价 | 提示 |")
TABLE_RULE = "|" + "------|" * 14


def _row(ticker: str, c: dict) -> str:
    head = f"| {ticker} | {c['delta_bucket']:.2f} |"
    if c["status"] != "ok":
        return head + " — |" * 11 + f" 剔除：{_cell(c['reason'])} |"
    tips = [w for w in c["warnings"] if w not in FIXED_WARNINGS]
    return (head + f" {c['short_put_strike']:g} | {c['long_call_strike']:g} "
            f"| ${c['short_put_mid']:.2f} | ${c['long_call_mid']:.2f} "
            f"| {premium_text(c['net_credit'])} | ${c['capital_basis']:,.0f} "
            f"| {c['breakeven_down']:.2f} | {dead_zone_text(c)} | {_move(c['put_move_pct'])} "
            f"| {_move(c['call_move_pct'])} | {_cell(upside_text(c))} "
            f"| {_cell('；'.join(tips)) or '—'} |")


def _rejected(docs: list[dict]) -> list[str]:
    out = [f"- {d['ticker']} Delta {c['delta_bucket']:.2f} — {c['reason']}"
           for d in docs for c in d["candidates"] if c["status"] != "ok"]
    return out or ["无。"]


def render_summary_md(docs: list[dict], snapshot: str, generated_on: str,
                      unreadable: list[str] | None = None) -> str:
    """The day's table: one row per ticker and delta bucket (rejected buckets give the reason)."""
    intraday = any(d.get("intraday") for d in docs)
    lines = [f"# {RR_LABEL}候选汇总 · 快照 {snapshot}（{_quote_kind(intraday)}）", "",
             f"*生成于 {generated_on}。{rules_line()}。*", "",
             f"*{source_line(snapshot, intraday)}。*", ""] + _banner() + ["## 候选", ""]
    rows = [_row(d["ticker"], c) for d in docs for c in d["candidates"]]
    lines += ([TABLE_HEAD, TABLE_RULE] + rows if rows else ["无候选。"]) + [""]
    lines += [f"占用资金按（短 Put 行权价 − 净权利金）× 100 算，{CAPITAL_NOTE}；最大收益{UNLIMITED}；"
              "最大亏损 = 占用资金（股价归零）；Put 幅度 = Put 行权价 ÷ 现价 − 1，Call 幅度 = Call "
              "行权价 ÷ 现价 − 1；没有“所需胜率”。", "", "## 被剔除", ""] + _rejected(docs)
    if unreadable:
        lines += ["", "## 未列入（文件损坏）", ""] + [f"- {name}" for name in unreadable]
    return option_labels.with_banner("\n".join(lines + ["", FOOTER, ""]))
