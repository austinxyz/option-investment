#!/usr/bin/env python3
"""Markdown and console text for the paper-book overview (pure, no I/O).

Money formatting reuses spread_report's helpers; strategy names reuse
strategy_pick_text.STRATEGY_LABELS. See docs/superpowers/specs/2026-10-27-paper-book-overview-design.md.
"""
from __future__ import annotations

from pathlib import Path

import spread_report
from spread_report import LOT_PUT_STRATEGIES, _money, _pct, _usd
from spread_ledger import (BEARCALL_LABEL, BEARPUT_LABEL, LONGPUT_LABEL, RR_LABEL,
                           STRATEGY_BEAR_CALL, STRATEGY_BEAR_PUT, STRATEGY_LONG_PUT, STRATEGY_RR)
from strategy_pick_text import STRATEGY_LABELS

TITLE = "# 模拟盘总账（全部策略）"
DISCLAIMER = "> 模拟 / 研究用途，非投资建议；按中间价成交，不含佣金。"
OFFLINE_FLOAT = "—（需 status 取行情）"
EXCLUDES_FLOAT = "（不含浮动盈亏）"
STATUS_LABELS = {"open": "未平仓", "closed_early": "提前平仓", "expired": "到期结算"}
WIN_NOTE = "盈亏>0 算赢；保护性 Put / Collar 不计"
REALIZED_FORMULA = "已实现盈亏 ÷ 已结算各笔占用资金之和"
TOTAL_FORMULA = "合计 ÷（已结算各笔 + 计入浮动的未平仓各笔）占用资金之和"
FOOTER = (
    "*口径：盈亏只含期权腿（备兑 Call 被行权、Collar / 保护性 Put 的股票腿不计入），每张合约 ×100 的美元，"
    "中间价成交、不含佣金；金额显示取整到美元，合计按未取整的金额算。已实现 = 已结算各笔盈亏之和；"
    "浮动 = 有有效行情的未平仓各笔按当前中间价的浮盈亏之和；合计 = 已实现 + 浮动（离线时不含浮动）。"
    "占用资金与 report.md 同口径：价差 = 最大亏损，现金担保 Put = 行权价，备兑 Call = 0，"
    "Collar / 保护性 Put = 净支出（净收入记 0），铁鹰 = 单侧宽度 − 净权利金，Call 借方价差 = 净支出，风险反转 = (Put 行权价 − 净权利金) × 100 × 张数（现金担保保守口径）。"
    f"已实现收益率 = {REALIZED_FORMULA}；合计收益率 = {TOTAL_FORMULA}；"
    "备兑 Call 和零成本 Collar 的盈亏计入分子而占用资金为 0，会抬高收益率。"
    "每笔盈亏% 与 report.md 相同（备兑 Call / Collar / 保护性 Put 以持股成本为分母）。*"
)


def pct1(x: float | None) -> str:
    """Signed one-decimal percent of a fraction; never '-0.0%'."""
    if x is None:
        return "—"
    value = round(x * 100, 1)
    return "0.0%" if value == 0 else f"{value:+.1f}%"


# Ledger labels for these strategies come from spread_ledger, not strategy_pick_text: /strategy-pick
# recommends the bear call / bear put spreads under its own labels (Bear Call 价差 / Bear Put 价差)
# and never risk reversal or the long put, while the paper book keeps the ledger wording.
LEDGER_LABELS = {STRATEGY_RR: RR_LABEL, STRATEGY_BEAR_CALL: BEARCALL_LABEL,
                 STRATEGY_BEAR_PUT: BEARPUT_LABEL, STRATEGY_LONG_PUT: LONGPUT_LABEL}


def label(strategy: str) -> str:
    if strategy in LEDGER_LABELS:
        return LEDGER_LABELS[strategy]
    return STRATEGY_LABELS[strategy]


def _uncounted(t: dict) -> str:
    parts = ([f"{t['n_missing']} 笔行情缺失"] if t["n_missing"] else []) + (
        [f"{t['n_pending']} 笔已过到期日待结算"] if t["n_pending"] else [])
    return f"（另有 {'、'.join(parts)}，未计入）" if parts else ""


def float_cell(t: dict, live: bool) -> str:
    return _money(t["unrealized"]) + _uncounted(t) if live else OFFLINE_FLOAT


def total_cell(t: dict, live: bool) -> str:
    return _money(t["total"]) + ("" if live else EXCLUDES_FLOAT)


def _win_cell(t: dict, note: str = WIN_NOTE) -> str:
    if t["win_rate"] is None:
        return "—"
    return f"{_pct(t['win_rate'])}（{t['wins']}/{t['n_win_sample']}，{note}）"


def _avg(x: float | None, sign: int) -> str:
    return "—" if x is None else _money(sign * x)


def _peak(peak: tuple[float, str] | None) -> str:
    return "—" if peak is None else f"{_usd(peak[0])}（{peak[1]} 起）"


def overview(t: dict, peak: tuple[float, str] | None, live: bool,
             win_note: str = WIN_NOTE) -> list[str]:
    rows = [
        (f"已实现盈亏（已结算 {t['n_settled']} 笔）", _money(t["realized"])),
        (f"浮动盈亏（未平仓 {t['n_open']} 笔）", float_cell(t, live)),
        ("合计", total_cell(t, live)),
        ("胜率", _win_cell(t, win_note)),
        ("平均盈利", _avg(t["avg_win"], 1)),
        ("平均亏损", _avg(t["avg_loss"], -1)),
        ("当前占用资金", _usd(t["current_capital"])),
        ("历史最大同时占用资金", _peak(peak)),
        ("已实现收益率", f"{pct1(t['realized_return'])}（{REALIZED_FORMULA} "
                    f"{_usd(t['settled_capital'])}）"),
        ("合计收益率", f"{pct1(t['total_return'])}（{TOTAL_FORMULA} {_usd(t['counted_capital'])}）"),
    ]
    return ["| 项目 | 数值 |", "|------|------|", *(f"| {k} | {v} |" for k, v in rows), ""]


def _strategy_float(t: dict, live: bool) -> str:
    if not live or t["n_open"] == 0:
        return "—"
    missing = t["n_missing"] + t["n_pending"]
    return _money(t["unrealized"]) + (f"（{missing} 笔未计入）" if missing else "")


def _strategy_win(name: str | None, t: dict) -> str:
    if name in LOT_PUT_STRATEGIES:
        return "不计"
    return _pct(t["win_rate"])


def _strategy_line(name_cell: str, name: str | None, t: dict, live: bool) -> str:
    cells = (name_cell, t["n_settled"], t["n_open"], _strategy_win(name, t), _money(t["realized"]),
             _strategy_float(t, live), _money(t["total"]), _usd(t["counted_capital"]),
             pct1(t["total_return"]))
    return "| " + " | ".join(str(c) for c in cells) + " |"


def strategy_table(groups: list[tuple[str, dict]], total: dict, live: bool) -> list[str]:
    head = ["| 策略 | 已结算 | 未平仓 | 胜率 | 已实现 | 浮动 | 合计 | 占用资金 | 收益率 |",
            "|------|--------|--------|------|--------|------|------|----------|--------|"]
    body = [_strategy_line(label(name), name, t, live) for name, t in groups]
    return [*head, *body, _strategy_line("**合计**", None, total, live), "",
            "> 占用资金 = 计入盈亏的那些笔的占用资金之和；收益率 = 合计 ÷ 占用资金。", ""]


def cash_cell(cash: float) -> str:
    side = "收入" if cash > 0 else "支出" if cash < 0 else "净"
    return f"{side} ${abs(cash):.2f}/股（${abs(cash) * 100:,.2f}/张）"


def _result(pnl: float) -> str:
    return "盈利" if pnl > 0 else "亏损" if pnl < 0 else "持平"


def _outcome_cells(r: dict) -> tuple[str, str, str]:
    """(平仓日, 结果, 盈亏) cells."""
    s = r["record"]
    if r["settled"]:
        return s.get("exit_date") or "—", _result(r["pnl"]), _money(r["pnl"])
    if r["note"]:
        return "—", "—", r["note"]
    return "—", "—", "—" if r["unrealized"] is None else _money(r["unrealized"])


def detail_line(r: dict, ticker: str) -> str:
    s = r["record"]
    exit_day, result, pnl = _outcome_cells(r)
    cells = (s["id"], ticker, label(r["strategy"]), spread_report.legs_label(s),
             s.get("snapshot_date") or "—", s["expiry"], s["contracts"], cash_cell(r["cash"]),
             _usd(r["capital"]), STATUS_LABELS.get(s["status"], s["status"]), exit_day, result,
             pnl, pct1(r["ratio"]))
    return "| " + " | ".join(str(c) for c in cells) + " |"


def detail_table(rows: list[dict], tagged: dict) -> list[str]:
    head = ["| # | 标的 | 策略 | 腿 | 开仓日 | 到期日 | 张数 | 开仓现金流 | 占用资金 | 状态 | 平仓日 "
            "| 结果 | 盈亏 | 盈亏% |",
            "|---|------|------|----|--------|--------|------|------------|----------|------|--------"
            "|------|------|-------|"]
    return [*head, *(detail_line(r, tagged[r["id"]]["ticker"]) for r in rows), ""]


def render_book(rows: list[dict], total: dict, groups: list[tuple[str, dict]],
                peak: tuple[float, str] | None, tagged: dict, live: bool,
                generated_on: str, source_label: str) -> str:
    quotes = "已取实时行情（中间价）" if live else "未取行情（离线；浮动盈亏需运行 status）"
    out = [TITLE, "", f"生成日期 {generated_on} · 数据来源 {source_label} · {quotes}", "",
           DISCLAIMER, "",
           "## 一、总览", "", *overview(total, peak, live),
           "## 二、分策略", "", *strategy_table(groups, total, live),
           "## 三、每笔明细", "", *detail_table(rows, tagged),
           "---", FOOTER, ""]
    return "\n".join(out)


def console_lines(rows: list[dict], total: dict, groups: list[tuple[str, dict]], live: bool,
                  path: Path) -> list[str]:
    head = (f"模拟盘总账：已实现 {_money(total['realized'])} · 浮动 {float_cell(total, live)} · "
            f"合计 {total_cell(total, live)} · 合计收益率 {pct1(total['total_return'])}")
    lines = [head]
    for name, t in groups:
        lines.append(f"  - {label(name)}：已结算 {t['n_settled']}、未平仓 {t['n_open']}，"
                     f"已实现 {_money(t['realized'])}，浮动 {_strategy_float(t, live)}，"
                     f"合计 {_money(t['total'])}，收益率 {pct1(t['total_return'])}")
    for r in rows:
        if r["note"]:
            lines.append(f"  提示：#{r['id']} {r['record']['ticker']} {r['note']}，未计入浮动")
    lines.append(f"总账：{path}")
    return lines
