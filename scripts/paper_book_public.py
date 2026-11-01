#!/usr/bin/env python3
"""The public paper-trading page (wiki/options/paper-trading.md) built from the paper-book layers.

Same three layers as book.md (paper_book + paper_book_render): overview, per strategy, per
trade; restricted to spread_report.PUBLIC_STRATEGIES (an allowlist: anything else, including a
strategy added later, never reaches the page). Pure, no I/O; spread_sim._write_public_report
writes it. See docs/superpowers/specs/2026-10-27-public-paper-trading-from-book-design.md.
"""
from __future__ import annotations

import option_settings
import paper_book
import paper_book_render as book_render
import spread_report as rep
from paper_book_render import REALIZED_FORMULA, TOTAL_FORMULA, label, pct1

PUBLIC_TITLE = "# 模拟盘报告（垂直 Put 贷方价差与现金担保 Put 纸上交易）"
INTRO = ("*数据快照 {day}，非实时。纸上交易，不含真实持仓；"
         "涉及真实持股的策略（备兑 Call、保护性 Put、Collar）不在此公开。不构成投资建议。*")
WIN_NOTE = "盈亏>0 算赢"
FOOTER = (
    "*口径：每张合约 ×100 的美元；中间价成交；不含手续费。"
    "占用资金：Put 贷方价差=最大亏损（宽度−权利金）×100×张数，"
    "现金担保 Put=行权价×100×张数（需备足的现金）；盈亏%=盈亏÷占用资金。"
    "已实现 = 已结算各笔盈亏之和；浮动 = 有有效行情的未平仓各笔按当前中间价的浮盈亏之和；"
    "合计 = 已实现 + 浮动；金额显示取整到美元，合计按未取整的金额算。"
    f"已实现收益率 = {REALIZED_FORMULA}；合计收益率 = {TOTAL_FORMULA}。*"
)
DETAIL_HEAD = ["#", "标的", "策略", "腿", "档位", "开仓日", "到期日", "张数", "开仓现金流",
               "占用资金", "状态", "平仓日", "结果", "盈亏", "盈亏%", "距短腿", "提示", "次日开盘跳空"]


def _shareable(records: list[dict]) -> list[dict]:
    """Public records only; a missing credit counts as 0, as capital_at_risk already does."""
    return [s if "credit" in s else {**s, "credit": 0.0} for s in rep.public_records(records)]


def _live_marks(records: list[dict], marks: dict | None) -> dict | None:
    """Marks of the public records, or None (offline) when an open one has none at all.

    Only public ids decide, so a page with private records present is byte-identical."""
    ids = {s["id"] for s in records}
    public = {k: v for k, v in (marks or {}).items() if k in ids}
    if public or not any(s["status"] == "open" for s in records):
        return public
    return None


def _verdict(st: dict) -> str:
    if st["passes"] is None:
        return "暂无亏损（或盈利）样本，无法比较"
    return "✅ 实际胜率高于盈亏平衡胜率" if st["passes"] else "❌ 实际胜率低于盈亏平衡胜率"


def strategy_notes(records: list[dict], groups: list[tuple[str, dict]]) -> list[str]:
    """Per strategy: breakeven win rate from actual payoffs, verdict, small-sample warning."""
    out: list[str] = []
    for name, _ in groups:
        st = rep._win_stats([s for s in records if rep.strategy_of(s) == name])
        if st["n_settled"] == 0:
            out += [f"> {label(name)}：还没有已结算的，暂无胜率统计。", ""]
            continue
        out += [f"> {label(name)}：按实际盈亏比算的盈亏平衡胜率 {rep._pct(st['breakeven_win_rate'])}；"
                f"{_verdict(st)}。", ""]
        if not st["enough_sample"]:
            out += [f"> ⚠️ {label(name)} 样本不足（{st['n_settled']} < {rep.MIN_SAMPLE}），别下结论。", ""]
    return out


def _outcome(r: dict, marks: dict | None) -> tuple[str, str, str, str, str, str]:
    """(平仓日, 结果, 盈亏, 盈亏%, 距短腿, 提示) cells. An open trade without a usable mark
    shows '—' with the reason in 提示, as the old page did."""
    s = r["record"]
    if r["settled"]:
        result = "盈利" if r["pnl"] > 0 else "亏损" if r["pnl"] < 0 else "持平"
        return s.get("exit_date") or "—", result, rep._money(r["pnl"]), pct1(r["ratio"]), "—", "—"
    pnl, dist, tip = rep._mark_cells((marks or {}).get(s["id"]),
                                     csp=rep.strategy_of(s) == rep.STRATEGY_CSP)
    return "—", "—", pnl, pct1(r["ratio"]), dist, tip


def _bucket(s: dict) -> str:
    return "—" if s.get("delta_bucket") is None else f"{s['delta_bucket']:.2f}"


def _gap(s: dict) -> str:
    return "—" if s.get("monday_gap_pct") is None else f"{s['monday_gap_pct']:+.2f}%"


def detail_line(r: dict, marks: dict | None) -> str:
    s = r["record"]
    cells = (s["id"], s["ticker"], label(r["strategy"]), rep.legs_label(s), _bucket(s),
             s.get("snapshot_date") or "—", s["expiry"], f"{s['contracts']} 张",
             book_render.cash_cell(r["cash"]), rep._usd(r["capital"]),
             book_render.STATUS_LABELS.get(s["status"], s["status"]), *_outcome(r, marks), _gap(s))
    return "| " + " | ".join(str(c) for c in cells) + " |"


def detail_table(rows: list[dict], marks: dict | None) -> list[str]:
    head = ["| " + " | ".join(DETAIL_HEAD) + " |", "|" + "|".join("---" for _ in DETAIL_HEAD) + "|"]
    return [*head, *(detail_line(r, marks) for r in rows), ""]


def _body(records: list[dict], marks: dict | None, generated_on: str) -> list[str]:
    live = marks is not None
    rows = paper_book.trade_rows(records, marks, None)
    total = paper_book.summarize(rows, live)
    groups = paper_book.by_strategy(rows, live)
    st = rep.compute_stats([s for s in records if rep.strategy_of(s) == rep.STRATEGY_BULL_PUT])
    return [PUBLIC_TITLE, "", INTRO.format(day=generated_on), "",
            "## 一、总览", "",
            *book_render.overview(total, rep.peak_capital(records), live, WIN_NOTE),
            "## 二、分策略", "", *book_render.strategy_table(groups, total, live),
            *strategy_notes(records, groups),
            "## 三、每笔明细", "", *detail_table(rows, marks),
            "## 四、分档对比", "", *rep._bucket_section(st),
            "## 五、开仓价假设的偏差", "", *rep.gap_lines(st),
            "---", FOOTER, ""]


def render_public_page(records: list[dict], marks: dict | None, generated_on: str) -> str:
    """The whole public page: front matter (edition label), title, intro, layers, footer."""
    shareable = _shareable(records)
    body = _body(shareable, _live_marks(shareable, marks), generated_on)
    return f"---\nsource: {option_settings.public_source_label()}\n---\n\n" + "\n".join(body)
