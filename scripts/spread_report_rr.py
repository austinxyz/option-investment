#!/usr/bin/env python3
"""Risk reversal (short put + long call) report pieces (pure, no I/O), shared by report.md
and report.html.

Own basis: capital is the conservative cash-secured figure, max profit is uncapped (never a
number), and there is no required win rate. Kept out of compute_stats and the Delta-bucket
table, and withheld from the public page. See docs/superpowers/specs/2026-10-27-risk-reversal-design.md
sections 5.4, 5.6, 5.8, 5.10 and 5.11.
"""
from __future__ import annotations

import spread_core as core
from rr_sim_rules import FIXED_WARNINGS
from spread_ledger import RR_LABEL, STRATEGY_RR, strategy_of
from spread_report import (MIN_SAMPLE, RR_ASSIGNMENT_HINT, RR_THRESHOLD_LABELS, RR_ZONE_LABELS,
                           SETTLED, _pnl_ratio, _split_status, _spct, _table_head,
                           _usd, capital_at_risk, rr_legs_text)

RR_HEAD = "## 风险反转（Risk Reversal）记录"
CAPITAL_BASIS = "现金担保口径，券商实际保证金因家而异"
CAPITAL_HEAD = f"占用资金（{CAPITAL_BASIS}）"
NO_CAP = "无上限"
NO_VALUE = "—"
RR_FOOTER = (f"风险反转=占用资金按{CAPITAL_BASIS}：（短 Put 行权价−净权利金）×100×张数，"
             "最大收益无上限；")
RR_COMPARE_NOTE = ("> risk_reversal 上方无封顶，没有「净支出÷宽度」这类理论口径，上表只按已结算的实际盈亏比算"
                   f"盈亏平衡胜率；占用资金按{CAPITAL_BASIS}（保守）。")
RR_TERMS_HEAD = ["净权利金", CAPITAL_HEAD, "下行盈亏平衡价", "死区", "最大收益"]
RR_LEGS_HEAD = "腿（卖 Put / 买 Call）"


def is_rr(s: dict) -> bool:
    return strategy_of(s) == STRATEGY_RR


def settled_rr(records: list[dict]) -> list[dict]:
    return [s for s in records if is_rr(s) and s["status"] in SETTLED]


def whole_dollars(x: float) -> str:
    """Signed whole dollars; rounding first so a sub-dollar loss never prints as '-$0'."""
    r = round(x)
    return f"{'+' if r > 0 else '-' if r < 0 else ''}${abs(r):,}"


def pct_text(x: float | None) -> str:
    """One-decimal signed percent; a rounded zero prints without a sign ('+0.0%' only if positive)."""
    return NO_VALUE if x is None else _spct(round(x, 3) + 0.0)


def premium_label(s: dict) -> str:
    credit = s["net_credit"]
    return f"{'净收入' if credit >= 0 else '净支出'} ${abs(credit):.2f}"


def dead_zone(s: dict) -> str:
    return f"{s['short_put_strike']:g} ～ {s['long_call_strike']:g}"


def term_cells(s: dict) -> tuple[str, str, str, str, str]:
    """(net premium, conservative capital, downside breakeven, dead zone, max profit)."""
    return (premium_label(s), _usd(capital_at_risk(s)), f"{s['breakeven_down']:.2f}",
            dead_zone(s), NO_CAP)


def mark_cells(m: dict | None) -> tuple[str, str, str]:
    """(pnl, distance above the put strike, note) for one open record's mark; whole dollars,
    so a sub-dollar loss never prints as '-$0'."""
    if m is None or "error" in m:
        return NO_VALUE, NO_VALUE, m["error"] if m else "运行 status 查看"
    tips = [text for text, hit in ((RR_THRESHOLD_LABELS[0], m["flags"]["stop"]),
                                   (RR_THRESHOLD_LABELS[1], m["flags"]["take_profit"]),
                                   ("报价陈旧", m.get("stale"))) if hit]
    return whole_dollars(m["pnl"]), f"{m['dist_pct']:.1f}%", "；".join(tips) or NO_VALUE


def how_label(s: dict) -> str:
    return "提前平仓" if s["status"] == "closed_early" else "到期结算"


def zone_label(s: dict) -> str:
    """Outcome zone of an expiry settlement; an early close or an unrecorded zone shows a dash.
    The down zone carries the assignment hint."""
    zone = s.get("outcome_zone")
    if s["status"] != "expired" or zone not in RR_ZONE_LABELS:
        return NO_VALUE
    label = RR_ZONE_LABELS[zone]
    return f"{label}；{RR_ASSIGNMENT_HINT}" if zone == "down" else label


def expiry_close_cell(s: dict) -> tuple[str, str]:
    """(expiry close, option-leg value per share), recomputed from the stored close because
    exit_value is not stored; both dashes for an early close."""
    if s["status"] != "expired" or s.get("exit_underlying") is None:
        return NO_VALUE, NO_VALUE
    value = core.rr_expiry_value(s["short_put_strike"], s["long_call_strike"], s["exit_underlying"])
    return f"{s['exit_underlying']:.2f}", f"{round(value, 2) + 0.0:.2f}"


def zone_counts(records: list[dict]) -> dict[str, int]:
    settled = [s for s in settled_rr(records) if s["status"] == "expired"]
    return {z: sum(1 for s in settled if s.get("outcome_zone") == z) for z in RR_ZONE_LABELS}


def reminder_lines() -> list[str]:
    """The fixed risk reminders, once at the top of the section."""
    return [f"> ⚠️ {w}" for w in FIXED_WARNINGS] + [""]


def _open_rows(open_: list[dict], marks: dict) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = _table_head(["#", "标的", RR_LEGS_HEAD, "到期", "张数", *RR_TERMS_HEAD,
                         "浮盈亏", "盈亏%", "距 Put 行权价", "提示"], False)
    for s in open_:
        m = marks.get(s["id"])
        pnl, dist, note = mark_cells(m)
        usable = None if m is None or "error" in m else m["pnl"]
        lines.append(f"| {s['id']} | {s['ticker']} | {rr_legs_text(s)} | {s['expiry']} "
                     f"| {s['contracts']} | {' | '.join(term_cells(s))} | {pnl} "
                     f"| {pct_text(_pnl_ratio(usable, s))} | {dist} | {note} |")
    return lines + [""]


def _settled_rows(settled: list[dict]) -> list[str]:
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = _table_head(["#", "标的", RR_LEGS_HEAD, "到期", *RR_TERMS_HEAD, "到期收盘",
                         "期权腿合计/股", "方式", "结果", "盈亏", "盈亏%", "结局区"], False)
    for s in sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        close, value = expiry_close_cell(s)
        lines.append(f"| {s['id']} | {s['ticker']} | {rr_legs_text(s)} | {s['expiry']} "
                     f"| {' | '.join(term_cells(s))} | {close} | {value} | {how_label(s)} "
                     f"| {s['result']} | {whole_dollars(s['pnl'])} "
                     f"| {pct_text(_pnl_ratio(s['pnl'], s))} | {zone_label(s)} |")
    return lines + [""]


def zone_summary_text(records: list[dict]) -> str:
    counts = zone_counts(records)
    return " / ".join(f"{RR_ZONE_LABELS[z].split('，')[0]} {n}" for z, n in counts.items())


def _summary(rows: list[dict]) -> list[str]:
    settled = settled_rr(rows)
    n = len(settled)
    wins = sum(1 for s in settled if s["pnl"] > 0)
    rate = "—" if not n else f"{wins / n * 100:.0f}%"
    line = (f"- 已结算 {n}，胜率 {rate}，总盈亏 {whole_dollars(sum(s['pnl'] for s in settled))}，"
            f"到期结局：{zone_summary_text(rows)}（提前平仓不记结局区）")
    warn = [f"> ⚠️ 样本不足（{n} < {MIN_SAMPLE}），别下结论。", ""] if n < MIN_SAMPLE else []
    return [line, "", *warn]


def rr_compare_note(records: list[dict]) -> list[str]:
    """Line under the by-strategy table; the row has no required win rate."""
    if not any(is_rr(s) for s in records):
        return []
    return [RR_COMPARE_NOTE, ""]


def rr_section(rows: list[dict], marks: dict) -> list[str]:
    open_, settled = _split_status(rows)
    return [RR_HEAD, "", *reminder_lines(), *_summary(rows),
            "**未平仓**", "", *_open_rows(open_, marks),
            "**已结算**", "", *_settled_rows(settled)]
