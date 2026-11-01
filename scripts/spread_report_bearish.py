#!/usr/bin/env python3
"""Bear call credit spread, bear put debit spread and long put report pieces (pure, no I/O),
shared by report.md and report.html.

All three are bearish. Own bases: bear call max loss = (width - credit) x 100 x n and required
win rate 1 - credit / width; bear put max loss = debit x 100 x n and required win rate
debit / width; long put max loss = debit x 100 x n and NO required win rate (average win /
average loss instead). Kept out of compute_stats, the Delta-bucket table and the public page.
See docs/superpowers/specs/2026-10-27-bearish-and-new-strategies-design.md sections 5.4 and
5.10-5.13, and docs/superpowers/specs/2026-10-30-long-put-design.md sections 2-3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import spread_core as core
from bearcall_sim_rules import WARN_BEAR_CALL_UPSIDE, WARN_DRIFT
from bearput_sim_rules import WARN_FULL_DEBIT_BEARPUT
from longput_sim_rules import WARN_THETA_DECAY
from spread_ledger import (BEARCALL_LABEL, BEARPUT_LABEL, LONGPUT_LABEL, STRATEGY_BEAR_CALL,
                           STRATEGY_BEAR_PUT, STRATEGY_LONG_PUT, strategy_of)
from spread_report import (BEARCALL_DIST_LABEL, BEARCALL_THRESHOLD_LABELS, BEARPUT_DIST_LABEL,
                           BEARPUT_THRESHOLD_LABELS, LONGPUT_DIST_LABEL, LONGPUT_THRESHOLD_LABELS,
                           MIN_SAMPLE, SETTLED, _pct, _pnl_ratio, _split_status, _table_head,
                           _usd, capital_at_risk, legs_label)
from spread_report_bullcall import NO_SETTLED, how_label, signed_pct, unsigned_pct
from spread_report_rr import whole_dollars

NO_VALUE = "—"
LONGPUT_NO_REQUIRED = "无所需胜率"

BEARCALL_HEAD = "## Call 贷方价差（Bear Call Credit Spread，看跌）记录"
BEARPUT_HEAD = "## Put 借方价差（Bear Put Debit Spread，看跌）记录"
LONGPUT_HEAD = "## 买入 Put（Long Put，看跌）记录"
BEARCALL_HTML_TITLE = f"{BEARCALL_LABEL}（Bear Call Credit Spread，看跌）"
BEARPUT_HTML_TITLE = f"{BEARPUT_LABEL}（Bear Put Debit Spread，看跌）"
LONGPUT_HTML_TITLE = f"{LONGPUT_LABEL}（Long Put，看跌）"
BEARCALL_HTML_SETTLED_TITLE = f"{BEARCALL_LABEL}（Bear Call Credit Spread，看跌，已结算）"
BEARPUT_HTML_SETTLED_TITLE = f"{BEARPUT_LABEL}（Bear Put Debit Spread，看跌，已结算）"
LONGPUT_HTML_SETTLED_TITLE = f"{LONGPUT_LABEL}（Long Put，看跌，已结算）"

BEARCALL_NOTE = ("> 看跌策略（卖方）：到期股价在短腿行权价或以下就是满额盈利。所需胜率 = 1 − 权利金 ÷ 宽度，"
                 "只按开仓时的权利金和宽度算，是逐笔的理论口径；"
                 "「按策略对比」表里的盈亏平衡胜率按实际盈亏比算，两者口径不同。")
BEARPUT_NOTE = ("> 看跌策略（买方）：到期股价在短腿行权价或以下就是满额收益。所需胜率 = 净支出 ÷ 宽度，"
                "只按开仓时的净支出和宽度算，是逐笔的理论口径；"
                "「按策略对比」表里的盈亏平衡胜率按实际盈亏比算，两者口径不同。")
LONGPUT_NOTE = (f"> 看跌策略（买方单腿）：跌得越多赚得越多，收益没有上限，{LONGPUT_NO_REQUIRED}；"
                "改看平均赢额 / 平均亏额和回本需下跌幅度。最大收益按标的跌到 0 算，仅作展示。")

BEARCALL_FOOTER = "Call 贷方价差（看跌）=最大亏损（宽度−权利金）×100×张数（上涨亏钱）；"
BEARPUT_FOOTER = "Put 借方价差（看跌）=最大亏损（净支出）×100×张数（方向错了亏掉全部净支出）；"
LONGPUT_FOOTER = "买入 Put（看跌）=最大亏损（净支出）×100×张数（无所需胜率，最大收益仅作展示）；"


# ---- per-record numbers ------------------------------------------------------------------

def _settled(records: list[dict], key: str) -> list[dict]:
    return [s for s in records if strategy_of(s) == key and s["status"] in SETTLED]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def bearcall_required_win_rate(s: dict) -> float:
    return core.breakeven_win_rate(s["credit"], s["width"])


def bearput_required_win_rate(s: dict) -> float:
    return core.bearput_required_win_rate(s["debit"], s["width"])


def bearcall_required_avg(records: list[dict]) -> float | None:
    """Per-trade average of 1 - credit / width over settled bear calls (theoretical)."""
    return _avg([bearcall_required_win_rate(s) for s in _settled(records, STRATEGY_BEAR_CALL)])


def bearput_required_avg(records: list[dict]) -> float | None:
    """Per-trade average of debit / width over settled bear puts (theoretical)."""
    return _avg([bearput_required_win_rate(s) for s in _settled(records, STRATEGY_BEAR_PUT)])


def bearput_captured_avg(records: list[dict]) -> float | None:
    """Average fraction of max profit captured, over the winning settled bear puts only."""
    return _avg([s["fraction_of_max_captured"] for s in _settled(records, STRATEGY_BEAR_PUT)
                 if s["pnl"] > 0 and s.get("fraction_of_max_captured") is not None])


def longput_win_loss_avg(records: list[dict]) -> tuple[float | None, float | None]:
    """(average win, average loss) in dollars over settled long puts, both positive magnitudes;
    a flat trade counts in neither."""
    settled = _settled(records, STRATEGY_LONG_PUT)
    return (_avg([s["pnl"] for s in settled if s["pnl"] > 0]),
            _avg([-s["pnl"] for s in settled if s["pnl"] < 0]))


def _dollars_or_dash(x: float | None) -> str:
    return NO_VALUE if x is None else _usd(x)


def _required_label(avg: float | None) -> str:
    return NO_SETTLED if avg is None else _pct(avg)


def _bearcall_terms(s: dict) -> tuple[str, ...]:
    max_profit = round(s["credit"] * 100 * s["contracts"], 2) + 0.0
    breakeven = core.bearcall_breakeven(s["short_strike"], s["credit"])
    return (f"${s['credit']:.2f}", f"{breakeven:.2f}",
            f"{whole_dollars(max_profit)} / {whole_dollars(-capital_at_risk(s))}",
            _pct(bearcall_required_win_rate(s)))


def _bearput_terms(s: dict) -> tuple[str, ...]:
    max_profit = core.bearput_max_profit(s["debit"], s["width"], s["contracts"])
    breakeven = core.bearput_breakeven(s["long_put_strike"], s["debit"])
    return (f"${s['debit']:.2f}", f"{breakeven:.2f}",
            f"{whole_dollars(max_profit)} / {whole_dollars(-capital_at_risk(s))}",
            _pct(bearput_required_win_rate(s)))


def _longput_terms(s: dict) -> tuple[str, ...]:
    display = core.longput_max_profit_display(s["put_strike"], s["debit"], s["contracts"])
    breakeven = core.longput_breakeven(s["put_strike"], s["debit"])
    return (f"${s['debit']:.2f}", f"{breakeven:.2f}", whole_dollars(-capital_at_risk(s)),
            whole_dollars(display))


def _exit_cost(s: dict) -> tuple[str, ...]:
    cost = s.get("exit_cost")
    return (NO_VALUE if cost is None else f"${round(cost, 2) + 0.0:.2f}",)


def _captured(s: dict) -> tuple[str, ...]:
    return (signed_pct(s.get("fraction_of_max_captured")),)


def _no_tail(s: dict) -> tuple[str, ...]:
    del s  # a long put's pnl% already is its fraction of the debit
    return ()


# ---- summaries -----------------------------------------------------------------------------

def _head_counts(rows: list[dict], key: str) -> tuple[int, str, str]:
    settled = _settled(rows, key)
    n = len(settled)
    wins = sum(1 for s in settled if s["pnl"] > 0)
    return n, _pct(wins / n if n else None), whole_dollars(sum(s["pnl"] for s in settled))


def _bearcall_summary(rows: list[dict]) -> str:
    n, rate, total = _head_counts(rows, STRATEGY_BEAR_CALL)
    return (f"- 已结算 {n}，胜率 {rate}，所需胜率（逐笔平均，1 − 权利金÷宽度）"
            f"{_pct(bearcall_required_avg(rows))}，总盈亏 {total}")


def _bearput_summary(rows: list[dict]) -> str:
    n, rate, total = _head_counts(rows, STRATEGY_BEAR_PUT)
    return (f"- 已结算 {n}，胜率 {rate}，所需胜率（逐笔平均，净支出÷宽度）"
            f"{_pct(bearput_required_avg(rows))}，"
            f"赢的笔平均捕获最大收益的 {unsigned_pct(bearput_captured_avg(rows))}，总盈亏 {total}")


def _longput_summary(rows: list[dict]) -> str:
    n, rate, total = _head_counts(rows, STRATEGY_LONG_PUT)
    win, loss = longput_win_loss_avg(rows)
    return (f"- 已结算 {n}，胜率 {rate}，平均赢额 {_dollars_or_dash(win)}，"
            f"平均亏额 {_dollars_or_dash(loss)}，总盈亏 {total}")


# ---- specs -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class BearishSpec:
    key: str
    label: str
    head: str
    html_title: str
    html_settled_title: str
    note: str
    warnings: tuple[str, ...]
    legs_head: str
    terms_head: tuple[str, ...]
    terms: Callable[[dict], tuple[str, ...]]
    move_head: str
    dist_head: str
    threshold_labels: tuple[str, str]
    tail_head: tuple[str, ...]
    tail: Callable[[dict], tuple[str, ...]]
    summary: Callable[[list[dict]], str]
    footer: str


BEARCALL = BearishSpec(
    STRATEGY_BEAR_CALL, BEARCALL_LABEL, BEARCALL_HEAD, BEARCALL_HTML_TITLE,
    BEARCALL_HTML_SETTLED_TITLE, BEARCALL_NOTE, (WARN_BEAR_CALL_UPSIDE, WARN_DRIFT), "腿（短/长）",
    ("权利金", "盈亏平衡价", "最大收益/最大亏损", "所需胜率"), _bearcall_terms, "回本幅度",
    BEARCALL_DIST_LABEL, BEARCALL_THRESHOLD_LABELS, ("每股平仓成本",), _exit_cost,
    _bearcall_summary, BEARCALL_FOOTER)
BEARPUT = BearishSpec(
    STRATEGY_BEAR_PUT, BEARPUT_LABEL, BEARPUT_HEAD, BEARPUT_HTML_TITLE,
    BEARPUT_HTML_SETTLED_TITLE, BEARPUT_NOTE, (WARN_FULL_DEBIT_BEARPUT, WARN_DRIFT), "腿（长/短）",
    ("净支出", "盈亏平衡价", "最大收益/最大亏损", "所需胜率"), _bearput_terms, "回本幅度",
    BEARPUT_DIST_LABEL, BEARPUT_THRESHOLD_LABELS, ("捕获比例",), _captured,
    _bearput_summary, BEARPUT_FOOTER)
LONGPUT = BearishSpec(
    STRATEGY_LONG_PUT, LONGPUT_LABEL, LONGPUT_HEAD, LONGPUT_HTML_TITLE,
    LONGPUT_HTML_SETTLED_TITLE, LONGPUT_NOTE, (WARN_THETA_DECAY,), "腿",
    ("净支出", "盈亏平衡价", "最大亏损", "最大收益（跌到 0，仅展示）"), _longput_terms,
    "回本需下跌幅度", LONGPUT_DIST_LABEL, LONGPUT_THRESHOLD_LABELS, (), _no_tail,
    _longput_summary, LONGPUT_FOOTER)
# Fixed render order (md sections, html tables, KPI pairs, footer).
SPECS = (BEARCALL, BEARPUT, LONGPUT)


def rows_of(spec: BearishSpec, records: list[dict]) -> list[dict]:
    return [s for s in records if strategy_of(s) == spec.key]


# ---- table cells (plain text; html escapes them) ---------------------------------------------

def mark_cells(m: dict | None, labels: tuple[str, str]) -> tuple[str, str, str]:
    """(pnl, distance, note) for one open record's mark; whole dollars and a rounded distance,
    so a sub-dollar loss never prints as '-$0' nor a tiny distance as '-0.0%'."""
    if m is None or "error" in m:
        return NO_VALUE, NO_VALUE, m["error"] if m else "运行 status 查看"
    tips = [text for text, hit in ((labels[0], m["flags"]["stop"]),
                                   (labels[1], m["flags"]["take_profit"]),
                                   ("报价陈旧", m.get("stale"))) if hit]
    return (whole_dollars(m["pnl"]), f"{round(m['dist_pct'], 1) + 0.0:.1f}%",
            "；".join(tips) or NO_VALUE)


def open_head(spec: BearishSpec) -> list[str]:
    return ["#", "标的", spec.legs_head, "到期", "张数", *spec.terms_head, spec.move_head,
            "浮盈亏", "盈亏%", spec.dist_head, "提示"]


def open_cells(spec: BearishSpec, s: dict, marks: dict) -> list[str]:
    m = marks.get(s["id"])
    pnl, dist, note = mark_cells(m, spec.threshold_labels)
    usable = None if m is None or "error" in m else m["pnl"]
    return [str(s["id"]), s["ticker"], legs_label(s), s["expiry"], str(s["contracts"]),
            *spec.terms(s), signed_pct(s.get("breakeven_move_pct")), pnl,
            signed_pct(_pnl_ratio(usable, s)), dist, note]


def settled_head(spec: BearishSpec) -> list[str]:
    return ["#", "标的", spec.legs_head, "到期", *spec.terms_head, "结果", "方式", "盈亏",
            "盈亏%", *spec.tail_head]


def settled_cells(spec: BearishSpec, s: dict) -> list[str]:
    return [str(s["id"]), s["ticker"], legs_label(s), s["expiry"], *spec.terms(s),
            str(s["result"]), how_label(s), whole_dollars(s["pnl"]),
            signed_pct(_pnl_ratio(s["pnl"], s)), *spec.tail(s)]


def sorted_settled(rows: list[dict]) -> list[dict]:
    settled = [s for s in rows if s["status"] in SETTLED]
    return sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True)


def summary_lines(spec: BearishSpec, rows: list[dict]) -> list[str]:
    n = len(_settled(rows, spec.key))
    warn = [f"> ⚠️ 样本不足（{n} < {MIN_SAMPLE}），别下结论。", ""] if n < MIN_SAMPLE else []
    return [spec.summary(rows), "", *warn]


def reminder_lines(spec: BearishSpec) -> list[str]:
    return [f"> ⚠️ {w}" for w in spec.warnings] + [""]


# ---- markdown ----------------------------------------------------------------------------------

def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _open_rows(spec: BearishSpec, open_: list[dict], marks: dict) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    return [*_table_head(open_head(spec), False),
            *(_md_row(open_cells(spec, s, marks)) for s in open_), ""]


def _settled_rows(spec: BearishSpec, rows: list[dict]) -> list[str]:
    settled = sorted_settled(rows)
    if not settled:
        return ["（还没有已结算的）", ""]
    return [*_table_head(settled_head(spec), False),
            *(_md_row(settled_cells(spec, s)) for s in settled), ""]


def section(spec: BearishSpec, rows: list[dict], marks: dict) -> list[str]:
    open_, _ = _split_status(rows)
    return [spec.head, "", spec.note, "", *reminder_lines(spec), *summary_lines(spec, rows),
            "**未平仓**", "", *_open_rows(spec, open_, marks),
            "**已结算**", "", *_settled_rows(spec, rows)]


def sections(others: list[dict], marks: dict) -> list[str]:
    out: list[str] = []
    for spec in SPECS:
        rows = rows_of(spec, others)
        if rows:
            out += section(spec, rows, marks)
    return out


def compare_notes(records: list[dict]) -> list[str]:
    """Lines under the by-strategy table, one per bearish strategy present."""
    out: list[str] = []
    if rows_of(BEARCALL, records):
        out += [f"> bear_call_spread 所需胜率（已结算逐笔平均，1 − 权利金÷宽度）"
                f"{_required_label(bearcall_required_avg(records))}，是理论的逐笔平均；"
                "上表的盈亏平衡胜率按实际盈亏比算，口径不同。", ""]
    if rows_of(BEARPUT, records):
        out += [f"> bear_put_spread 所需胜率（已结算逐笔平均，净支出÷宽度）"
                f"{_required_label(bearput_required_avg(records))}，是理论的逐笔平均；"
                "上表的盈亏平衡胜率按实际盈亏比算，口径不同。", ""]
    if rows_of(LONGPUT, records):
        win, loss = longput_win_loss_avg(records)
        out += [f"> long_put {LONGPUT_NO_REQUIRED}（单腿收益无上限，没有「净支出÷宽度」这类理论口径），"
                f"改看平均赢额 {_dollars_or_dash(win)} / 平均亏额 {_dollars_or_dash(loss)}；"
                "上表的盈亏平衡胜率按实际盈亏比算。", ""]
    return out


def footers(records: list[dict]) -> str:
    return "".join(spec.footer for spec in SPECS if rows_of(spec, records))
