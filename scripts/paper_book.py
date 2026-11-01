#!/usr/bin/env python3
"""Paper-book overview: total, per-strategy and per-trade P&L across every paper strategy.

    paper_book.py [report]          # offline (default): no network, unrealized shown as "—"
    paper_book.py status | --live   # marks open trades at the current mid (read-only)

Reads the ledger (spreads.json) and writes book.md next to report.md; never writes the ledger,
the old reports, wiki/, outputs/ or data/positions.md. Live mode only reads marks through
spread_sim._mark: it never settles or closes anything (run `spread_sim.py status` for that).
See docs/superpowers/specs/2026-10-27-paper-book-overview-design.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import option_labels
import option_settings
import paper_book_render as book_render
import spread_data
import spread_ledger as L
import spread_report
import spread_sim

BOOK_NAME = "book.md"
LEDGER_NAME = "spreads.json"
# Fixed display order; also the only strategies this module knows (no fallthrough).
STRATEGY_ORDER = (L.STRATEGY_BULL_PUT, L.STRATEGY_CSP, L.STRATEGY_COVERED_CALL, L.STRATEGY_COLLAR,
                  L.STRATEGY_PROTECTIVE_PUT, L.STRATEGY_IRON_CONDOR, L.STRATEGY_BULL_CALL,
                  L.STRATEGY_RR, L.STRATEGY_BEAR_CALL, L.STRATEGY_BEAR_PUT, L.STRATEGY_LONG_PUT)
# Signed opening cash per share: + is premium received, - is a net debit paid.
OPENING_CASH = {
    L.STRATEGY_BULL_PUT: lambda s: s["credit"],
    L.STRATEGY_CSP: lambda s: s["credit"],
    L.STRATEGY_COVERED_CALL: lambda s: s["credit"],
    L.STRATEGY_COLLAR: lambda s: s["net_credit"],
    L.STRATEGY_PROTECTIVE_PUT: lambda s: s["net_credit"],
    L.STRATEGY_IRON_CONDOR: lambda s: s["net_credit"],
    L.STRATEGY_BULL_CALL: lambda s: -s["debit"],
    L.STRATEGY_RR: lambda s: s["net_credit"],  # signed: credit is +, a net debit is -
    L.STRATEGY_BEAR_CALL: lambda s: s["credit"],
    L.STRATEGY_BEAR_PUT: lambda s: -s["debit"],
    L.STRATEGY_LONG_PUT: lambda s: -s["debit"],
}
SETTLED = spread_report.SETTLED
MISSING = "行情缺失"
PENDING = "待结算"
pct1 = book_render.pct1


def _strategy(s: dict) -> str:
    name = L.strategy_of(s)
    if name not in OPENING_CASH:
        raise L.LedgerError(f"#{s.get('id', '?')} 的策略 {name!r} 本总账不认识，没有生成总账")
    return name


def _mark_state(s: dict, marks: dict | None) -> tuple[float | None, str | None]:
    """(unrealized pnl, note) of an open record; offline marks=None gives (None, None)."""
    if marks is None:
        return None, None
    m = marks.get(s["id"])
    if m is None or "error" in m:
        return None, PENDING if (m or {}).get("pending") else MISSING
    return float(m["pnl"]), None


def trade_row(s: dict, marks: dict | None) -> dict:
    """One record as the overview sees it (raises LedgerError on an unknown strategy)."""
    strategy = _strategy(s)
    settled = s["status"] in SETTLED
    unrealized, note = (None, None) if settled else _mark_state(s, marks)
    pnl = float(s["pnl"]) if settled else unrealized
    return {
        "id": s["id"], "strategy": strategy, "record": s, "settled": settled,
        "cash": float(OPENING_CASH[strategy](s)), "capital": spread_report.capital_at_risk(s),
        "pnl": pnl if settled else None, "unrealized": unrealized, "note": note,
        "ratio": None if pnl is None else spread_report._pnl_ratio(pnl, s),
    }


def trade_rows(records: list[dict], marks: dict | None, today: date) -> list[dict]:
    """Rows grouped by strategy (fixed order), then open (snapshot) date, then id."""
    del today  # the rows do not depend on the date; kept so callers pass one consistently
    rows = []
    for s in records:
        try:
            rows.append(trade_row(s, marks))
        except (KeyError, TypeError, ValueError) as exc:
            raise L.LedgerError(
                f"#{s.get('id', '?')} 记录缺字段或格式不对（{exc!r}），没有生成总账") from exc
    return sorted(rows, key=lambda r: (STRATEGY_ORDER.index(r["strategy"]),
                                       r["record"].get("snapshot_date") or "", r["id"]))


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _win_stats(settled: list[dict]) -> dict:
    sample = [r for r in settled if r["strategy"] in spread_report.WIN_RATE_STRATEGIES]
    wins = [r["pnl"] for r in sample if r["pnl"] > 0]
    losses = [-r["pnl"] for r in sample if r["pnl"] < 0]
    return {"n_win_sample": len(sample), "wins": len(wins),
            "win_rate": len(wins) / len(sample) if sample else None,
            "avg_win": sum(wins) / len(wins) if wins else None,
            "avg_loss": sum(losses) / len(losses) if losses else None}


def summarize(rows: list[dict], live: bool) -> dict:
    """Totals of any set of rows: the overview and each strategy row use this same function."""
    settled = [r for r in rows if r["settled"]]
    open_ = [r for r in rows if not r["settled"]]
    marked = [r for r in open_ if r["unrealized"] is not None]
    realized = sum(r["pnl"] for r in settled)
    unrealized = sum(r["unrealized"] for r in marked) if live else None
    total = realized + (unrealized or 0.0)
    settled_capital = sum(r["capital"] for r in settled)
    counted_capital = settled_capital + sum(r["capital"] for r in marked)
    return {
        "n_settled": len(settled), "n_open": len(open_),
        "n_missing": sum(1 for r in open_ if r["note"] == MISSING),
        "n_pending": sum(1 for r in open_ if r["note"] == PENDING),
        "realized": realized, "unrealized": unrealized, "total": total,
        "settled_capital": settled_capital, "counted_capital": counted_capital,
        "current_capital": sum(r["capital"] for r in open_),
        "realized_return": _ratio(realized, settled_capital),
        "total_return": _ratio(total, counted_capital),
        **_win_stats(settled),
    }


def by_strategy(rows: list[dict], live: bool) -> list[tuple[str, dict]]:
    """(strategy, summary) for each strategy that has records, in STRATEGY_ORDER."""
    present = {r["strategy"] for r in rows}
    return [(name, summarize([r for r in rows if r["strategy"] == name], live))
            for name in STRATEGY_ORDER if name in present]


def render(records: list[dict], marks: dict | None, today: date) -> str:
    """The whole book.md text (pure: `today` is passed in, marks None means offline)."""
    rows = trade_rows(records, marks, today)
    live = marks is not None
    total = summarize(rows, live)
    peak = spread_report.peak_capital(records)
    tagged = {r["id"]: r for r in option_labels.tag_records(records)}
    notes = option_labels.run_notes(option_labels.ledger_mix_line(records))
    text = book_render.render_book(rows, total, by_strategy(rows, live), peak, tagged, live,
                              today.isoformat(), option_labels.data_source_label())
    return option_labels.with_banner(text, notes)


def _needs_quote(s: dict, today: date) -> bool:
    return s["status"] == "open" and today <= date.fromisoformat(s["expiry"])


def collect_marks(records: list[dict], today: date) -> dict:
    """Live marks for open records (read-only). A record past expiry is pending settlement;
    one record's data error is that record's error; a lost link aborts (SourceUnavailable)."""
    marks: dict = {}
    open_ = [s for s in records if s["status"] == "open"]
    if any(_needs_quote(s, today) for s in open_):
        spread_data.ensure_source()  # nothing is written if the link is down
    for s in open_:
        if not _needs_quote(s, today):
            marks[s["id"]] = {"error": "已过到期日，先运行 spread_sim.py status 结算", "pending": True}
            continue
        try:
            marks[s["id"]] = spread_sim._mark(s)
        except spread_data.SourceUnavailable:
            raise
        except (spread_data.DataError, L.LedgerError, KeyError, TypeError, ValueError) as exc:
            marks[s["id"]] = {"error": str(exc)}
    return marks


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run(ledger_dir: Path, live: bool, today: date) -> int:
    records = L.load(ledger_dir / LEDGER_NAME)["spreads"]
    if not records:
        print("账本里还没有记录：先用任一开仓命令开一笔，再来看总账。")
        return 0
    trade_rows(records, None, today)  # unknown strategy / malformed record fails before any fetch
    marks = collect_marks(records, today) if live else None
    text = render(records, marks, today)
    path = ledger_dir / BOOK_NAME
    write_atomic(path, text)
    rows = trade_rows(records, marks, today)
    for line in book_render.console_lines(rows, summarize(rows, live),
                                     by_strategy(rows, live), live, path):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper_book", description="模拟盘总账（全部策略，只读）")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    p.add_argument("--live", action="store_true", help="取实时行情算浮动盈亏（同 status）")
    p.add_argument("cmd", nargs="?", choices=("report", "status"), default="report",
                   help="report=离线（默认），status=取行情")
    return p


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        option_labels.print_console_header()
        ledger_dir = Path(args.ledger_dir) if args.ledger_dir else spread_sim.default_ledger_dir()
        return run(ledger_dir, args.live or args.cmd == "status", today or date.today())
    except (spread_data.DataError, L.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
