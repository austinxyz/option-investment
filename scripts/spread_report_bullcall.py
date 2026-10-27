#!/usr/bin/env python3
"""Bull call debit spread report pieces (pure, no I/O), shared by report.md and report.html.

The buyer's basis: max loss = the debit paid, required win rate = debit / width. Kept out of
compute_stats and the Delta-bucket table. See
docs/superpowers/specs/2026-10-25-bull-call-spread-design.md sections 5.15 and 12.
"""
from __future__ import annotations

import spread_core as core
from spread_ledger import STRATEGY_BULL_CALL, strategy_of
from spread_report import (BULLCALL_THRESHOLD_LABELS, MIN_SAMPLE, SETTLED, _mark_cells, _money,
                           _pct, _pnl_ratio, _split_status, _table_head, legs_label)

BULLCALL_HEAD = "## Call 借方价差（Bull Call Debit Spread）记录"
BULLCALL_FOOTER = "Call 借方价差=最大亏损（净支出）×100×张数（方向错了亏掉全部净支出）；"
BULLCALL_NOTE = ("> 所需胜率 = 净支出 ÷ 宽度，只按开仓时的净支出和宽度算，是逐笔的理论口径："
                 "它假设每笔结果只有满额收益或亏掉全部净支出两种；"
                 "「按策略对比」表里的盈亏平衡胜率按实际盈亏比算，两者口径不同。"
                 "买方所需胜率低于一半是正常的，靠赔率赚钱；到期股价在长腿行权价或以下就是满额亏损。")
BULLCALL_LEGS_HEAD = "腿（长/短）"
NO_SETTLED = "暂无已结算"
BULLCALL_TERMS_HEAD = ["净支出", "盈亏平衡价", "最大收益/最大亏损", "所需胜率"]


def _is_bullcall(s: dict) -> bool:
    return strategy_of(s) == STRATEGY_BULL_CALL


def _settled(records: list[dict]) -> list[dict]:
    return [s for s in records if _is_bullcall(s) and s["status"] in SETTLED]


def signed_pct(x: float | None) -> str:
    """One-decimal signed percent of a fraction; rounding first so a tiny negative or -0.0
    never prints as '-0.0%'."""
    return "—" if x is None else f"{round(x * 100, 1) + 0.0:+.1f}%"


def unsigned_pct(x: float | None) -> str:
    """One-decimal unsigned percent (winners' captured average is always >= 0)."""
    return "—" if x is None else f"{round(x * 100, 1) + 0.0:.1f}%"


def required_avg_label(records: list[dict]) -> str:
    """The settled per-trade required rate, or '暂无已结算' while only open bull calls exist."""
    avg = bullcall_required_avg(records)
    return NO_SETTLED if avg is None else _pct(avg)


def required_win_rate(s: dict) -> float:
    return core.bullcall_required_win_rate(s["debit"], s["width"])


def bullcall_required_avg(records: list[dict]) -> float | None:
    """Per-trade average of debit / width over settled bull calls (theoretical, not payoff-based)."""
    settled = _settled(records)
    return sum(map(required_win_rate, settled)) / len(settled) if settled else None


def bullcall_captured_avg(records: list[dict]) -> float | None:
    """Average fraction of max profit captured, over the winning settled bull calls only."""
    wins = [s["fraction_of_max_captured"] for s in _settled(records)
            if s["pnl"] > 0 and s.get("fraction_of_max_captured") is not None]
    return sum(wins) / len(wins) if wins else None


def term_cells(s: dict) -> tuple[str, str, str, str]:
    """(debit, breakeven, max profit / max loss, required win rate) display strings."""
    max_profit = core.bullcall_max_profit(s["debit"], s["width"], s["contracts"])
    max_loss = core.bullcall_max_loss(s["debit"], s["contracts"])
    return (f"${s['debit']:.2f}", f"{s['breakeven']:.2f}",
            f"{_money(max_profit)} / {_money(-max_loss)}", _pct(required_win_rate(s)))


def pnl_pct(s: dict, pnl: float | None) -> str:
    return signed_pct(_pnl_ratio(pnl, s))


def captured_label(s: dict) -> str:
    return signed_pct(s.get("fraction_of_max_captured"))


def how_label(s: dict) -> str:
    return "提前平仓" if s["status"] == "closed_early" else "到期结算"


def bullcall_required_note(records: list[dict]) -> list[str]:
    """Line under the by-strategy table; the average there differs from the payoff-based rate."""
    if not any(_is_bullcall(s) for s in records):
        return []
    return [f"> bull_call_spread 所需胜率（已结算逐笔平均，净支出÷宽度）{required_avg_label(records)}，"
            "是理论的逐笔平均；上表的盈亏平衡胜率按实际盈亏比算，口径不同。", ""]


def _open_rows(open_: list[dict], marks: dict) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = _table_head(["#", "标的", BULLCALL_LEGS_HEAD, "到期", "张数", *BULLCALL_TERMS_HEAD,
                         "回本幅度", "浮盈亏", "盈亏%", "距长腿", "提示"], False)
    for s in open_:
        m = marks.get(s["id"])
        pnl, dist, note = _mark_cells(m, labels=BULLCALL_THRESHOLD_LABELS)
        usable = None if m is None or "error" in m else m["pnl"]
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} | {s['expiry']} "
                     f"| {s['contracts']} | {' | '.join(term_cells(s))} "
                     f"| {signed_pct(s.get('breakeven_move_pct'))} | {pnl} "
                     f"| {pnl_pct(s, usable)} | {dist} | {note} |")
    return lines + [""]


def _settled_rows(settled: list[dict]) -> list[str]:
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = _table_head(["#", "标的", BULLCALL_LEGS_HEAD, "到期", *BULLCALL_TERMS_HEAD,
                         "结果", "方式", "盈亏", "盈亏%", "捕获比例"], False)
    for s in sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} | {s['expiry']} "
                     f"| {' | '.join(term_cells(s))} | {s['result']} | {how_label(s)} "
                     f"| {_money(s['pnl'])} | {pnl_pct(s, s['pnl'])} | {captured_label(s)} |")
    return lines + [""]


def _summary(rows: list[dict]) -> list[str]:
    """Settled count, actual vs required win rate, winners' captured share, sample warning."""
    settled = _settled(rows)
    n = len(settled)
    wins = sum(1 for s in settled if s["pnl"] > 0)
    line = (f"- 已结算 {n}，胜率 {_pct(wins / n if n else None)}，"
            f"所需胜率（逐笔平均，净支出÷宽度）{_pct(bullcall_required_avg(rows))}，"
            f"赢的笔平均捕获最大收益的 {unsigned_pct(bullcall_captured_avg(rows))}，"
            f"总盈亏 {_money(sum(s['pnl'] for s in settled))}")
    warn = [f"> ⚠️ 样本不足（{n} < {MIN_SAMPLE}），别下结论。", ""] if n < MIN_SAMPLE else []
    return [line, "", *warn]


def bullcall_section(rows: list[dict], marks: dict) -> list[str]:
    open_, settled = _split_status(rows)
    return [BULLCALL_HEAD, "", BULLCALL_NOTE, "", *_summary(rows),
            "**未平仓**", "", *_open_rows(open_, marks),
            "**已结算**", "", *_settled_rows(settled)]
