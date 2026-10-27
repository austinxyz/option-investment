#!/usr/bin/env python3
"""Position tracker for /morning-check command.

Reads/writes `data/positions.md` with two markdown tables:
- Active Positions
- Closed Positions
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, asdict
import mdtable
from pathlib import Path


# Shared markdown-table primitives. Aliased at module level rather than called
# through `mdtable.` at each site so every existing call keeps working -- this
# is a de-duplication, not an API change. See scripts/mdtable.py for why these
# must not be re-copied.
_split_row = mdtable.split_row
_is_table_separator = mdtable.is_table_separator
_sanitize_cell = mdtable.sanitize_cell
_fmt_shares = mdtable.fmt_shares

REPO_ROOT = Path(__file__).resolve().parent.parent
POSITIONS_FILE = REPO_ROOT / "data" / "positions.md"
CONFIG_FILE = REPO_ROOT / "data" / "config.md"


class AmbiguousLotError(ValueError):
    """Raised when a ticker has several account lots and none was specified.

    Falling back to the first matching row is how a 5-share AVGO position
    gets 2 shares closed while the other 3 sit untouched and unreported.
    Refusing to guess is the whole point.
    """


@dataclass
class Position:
    ticker: str
    shares: float  # supports fractional shares (e.g. 75.587)
    avg_cost: float
    entry_date: str
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    status: str = "Active"
    notes: str = ""
    account: str = ""     # 账户 column，自由文本（账户标识不入公开文件）
    stock_type: str = ""  # 类型 column (🏦 蓝筹 / 🚀 翻倍 / ⚡ 动量 / 宽基 ...)
    ltcg: str = ""        # LTCG日 column (date, ✅, or —)
    order_placed: str = ""  # 挂单 column: broker stop/take-profit order note (date + kind)
    lot_count: int = 1    # >1 means this is an aggregate across accounts
    # True when the Avg Cost cell carried a leading "~" (cost is an estimate,
    # not a confirmed figure — e.g. "~$65.00"). Derived from the avg_cost cell
    # text, not a column of its own; must round-trip through parse -> format.
    cost_estimated: bool = False


@dataclass
class ClosedPosition:
    ticker: str
    shares: float  # supports fractional shares
    entry_date: str
    exit_date: str
    avg_cost: float
    avg_exit: float
    pnl_dollar: float
    pnl_pct: float
    reason: str
    closed_date: str
    # A round is (ticker, account, entry_date). `kind` distinguishes a full close
    # from a partial trim — trims have to be recorded or the discipline review
    # cannot see a sequence like ZZZQ's "50 shares → trim 13 → trim 17 → 20 left", which
    # is exactly the sequence its breached trailing stop hid inside.
    account: str = "—"
    round_id: str = "—"
    kind: str = "全平"  # 全平 | 减仓


@dataclass
class EtfPosition:
    sector: str            # "存储" | "光互连" | "LEO" | "电网"
    ticker: str            # ETF ticker or "—" if not yet selected
    shares: float
    avg_cost: float
    target_pct: float      # target portfolio allocation %
    status: str = "Watch"  # "Active" | "Watch"
    notes: str = ""
    account: str = ""      # 账户 column（账户标识不入公开文件）
    entry_date: str = ""   # Entry Date column（首批建仓日；DCA 仓记第一批）
    stop: float | None = None     # 止损（收盘价口径）
    target1: float | None = None  # 止盈 1
    target2: float | None = None  # 止盈 2
    order_placed: str = ""        # 挂单 column: broker stop/take-profit order note
    # True when the Avg Cost cell carried a leading "~" (estimate, not a
    # confirmed figure). Derived from the avg_cost cell text; not a column.
    cost_estimated: bool = False


ETF_SECTION_HEADER = "# ETF 赛道仓位"
ETF_TABLE_HEADER = "| 赛道 | Ticker | 账户 | Shares | Avg Cost | Entry Date | 目标% | 止损 | 止盈1 | 止盈2 | 挂单 | Status | 备注 |"
ETF_TABLE_SEP    = "|------|--------|------|--------|----------|------------|-------|------|-------|-------|------|--------|------|"

# Maps an ETF-table header cell (lowercased) to an EtfPosition field name.
# Header-driven like ACTIVE_HEADER_FIELDS — positional parsing broke the moment
# a column was inserted, which is exactly what this table is about to get.
ETF_HEADER_FIELDS = {
    "赛道": "sector",
    "sector": "sector",
    "ticker": "ticker",
    "shares": "shares",
    "avg cost": "avg_cost",
    "目标%": "target_pct",
    "status": "status",
    "备注": "notes",
    "notes": "notes",
    "账户": "account",
    "account": "account",
    "entry date": "entry_date",
    "entry_date": "entry_date",
    "止损": "stop",
    "stop": "stop",
    "止盈1": "target1",
    "target 1": "target1",
    "target1": "target1",
    "止盈2": "target2",
    "target 2": "target2",
    "target2": "target2",
    "挂单": "order_placed",
}

# Layout assumed when the ETF table has no parseable header row.
LEGACY_ETF_FIELDS = [
    "sector", "ticker", "shares", "avg_cost", "target_pct", "status", "notes",
]


TEMPLATE = """# Active Positions

> Last updated: {date}
> Maintained by `/morning-check`. Manual edits OK.

| Ticker | Shares | Avg Cost | Entry Date | Stop | Target 1 | Target 2 | Status | Notes |
|--------|--------|----------|------------|------|----------|----------|--------|-------|

# Closed Positions

| Ticker | 账户 | 轮次 | 类型 | Shares | Entry | Exit | Avg Cost | Avg Exit | P&L $ | P&L % | Reason | Closed Date |
|--------|------|------|------|--------|-------|------|----------|----------|-------|-------|--------|-------------|
"""


def _fmt_optional_dollar(value: float | None) -> str:
    return f"${value:.2f}" if value is not None else ""


# Maps an Active-table header cell (lowercased) to a Position field name.
# Columns absent from this map are preserved verbatim on update but ignored.
ACTIVE_HEADER_FIELDS = {
    "ticker": "ticker",
    "账户": "account",
    "类型": "stock_type",
    "shares": "shares",
    "avg cost": "avg_cost",
    "entry date": "entry_date",
    "ltcg日": "ltcg",
    "ltcg": "ltcg",
    "stop": "stop",
    "target 1": "target1",
    "target1": "target1",
    "target 2": "target2",
    "target2": "target2",
    "挂单": "order_placed",
    "status": "status",
    "notes": "notes",
}

# Layout assumed when the Active table has no parseable header row.
LEGACY_ACTIVE_FIELDS = [
    "ticker", "shares", "avg_cost", "entry_date",
    "stop", "target1", "target2", "status", "notes",
]


def _active_fields(content: str | None = None) -> list[str | None]:
    """Column order of the Active table as Position field names.

    Returns one entry per column: a field name, or None for a column this
    script doesn't model (preserved verbatim rather than dropped). Falls back
    to LEGACY_ACTIVE_FIELDS when no header row is found.
    """
    if content is None:
        if not POSITIONS_FILE.exists():
            return list(LEGACY_ACTIVE_FIELDS)
        content = POSITIONS_FILE.read_text(encoding="utf-8")
    idx = content.find("# Closed Positions")
    active_section = content[:idx] if idx != -1 else content

    for line in active_section.splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if cells and cells[0].strip().lower() == "ticker":
            return [ACTIVE_HEADER_FIELDS.get(c.strip().lower()) for c in cells]
    return list(LEGACY_ACTIVE_FIELDS)


def _cell_for(pos: Position, field: str) -> str:
    """Render one Position field as a markdown table cell."""
    if field == "shares":
        return _fmt_shares(pos.shares)
    if field == "avg_cost":
        if pos.avg_cost is None:
            return ""
        prefix = "~" if pos.cost_estimated else ""
        return f"{prefix}${pos.avg_cost:.2f}"
    if field in ("stop", "target1", "target2"):
        return _fmt_optional_dollar(getattr(pos, field))
    return _sanitize_cell(str(getattr(pos, field) or ""))


def _format_active_row(
    pos: Position,
    fields: list[str | None] | None = None,
    original: list[str] | None = None,
) -> str:
    """Format a Position as a row matching the Active table's column layout.

    `original` (the row's existing cells) is used as the base so columns this
    script doesn't model survive an update instead of being blanked out.
    """
    if fields is None:
        fields = _active_fields()
    cells = list(original) if original else []
    cells += [""] * (len(fields) - len(cells))
    for i, field in enumerate(fields):
        if field is not None:
            cells[i] = _cell_for(pos, field)
    return "| " + " | ".join(cells) + " |"


def _format_closed_row(c: ClosedPosition) -> str:
    """Format a closed position as a markdown table row."""
    pnl_sign = "+" if c.pnl_dollar >= 0 else ""
    pct_sign = "+" if c.pnl_pct >= 0 else ""
    return (
        f"| {c.ticker} | {c.account} | {c.round_id} | {c.kind} | "
        f"{_fmt_shares(c.shares)} | {c.entry_date} | {c.exit_date} | "
        f"${c.avg_cost:.2f} | ${c.avg_exit:.2f} | "
        f"{pnl_sign}${c.pnl_dollar:.0f} | {pct_sign}{c.pnl_pct:.1f}% | "
        f"{_sanitize_cell(c.reason)} | {c.closed_date} |"
    )


CLOSED_HEADER = (
    "| Ticker | 账户 | 轮次 | 类型 | Shares | Entry | Exit | Avg Cost | Avg Exit "
    "| P&L $ | P&L % | Reason | Closed Date |\n"
    "|--------|------|------|------|--------|-------|------|----------|----------"
    "|-------|-------|--------|-------------|"
)


def _etf_section(content: str) -> str:
    """Return just the ETF table section of positions.md (empty if absent)."""
    idx = content.find(ETF_SECTION_HEADER)
    if idx == -1:
        return ""
    section = content[idx + len(ETF_SECTION_HEADER):]
    next_h1 = section.find("\n# ")
    return section if next_h1 == -1 else section[:next_h1]


def _etf_fields(content: str | None = None) -> list[str | None]:
    """Column order of the ETF table as EtfPosition field names.

    One entry per column: a field name, or None for a column this script
    doesn't model (preserved verbatim rather than dropped).
    """
    if content is None:
        if not POSITIONS_FILE.exists():
            return list(LEGACY_ETF_FIELDS)
        content = POSITIONS_FILE.read_text(encoding="utf-8")
    for line in _etf_section(content).splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if cells and cells[0].strip().lower() in ("赛道", "sector"):
            return [ETF_HEADER_FIELDS.get(c.strip().lower()) for c in cells]
    return list(LEGACY_ETF_FIELDS)


def _etf_cell_for(pos: EtfPosition, field: str) -> str:
    """Render one EtfPosition field as a markdown table cell."""
    if field == "shares":
        return _fmt_shares(pos.shares) if pos.shares > 0 else "0"
    if field == "avg_cost":
        if pos.avg_cost <= 0:
            return "—"
        prefix = "~" if pos.cost_estimated else ""
        return f"{prefix}${pos.avg_cost:.2f}"
    if field == "target_pct":
        return f"{pos.target_pct:.1f}%"
    if field in ("stop", "target1", "target2"):
        return _fmt_optional_dollar(getattr(pos, field))
    return _sanitize_cell(str(getattr(pos, field) or ""))


def _format_etf_row(
    pos: EtfPosition,
    fields: list[str | None] | None = None,
    original: list[str] | None = None,
) -> str:
    """Format an EtfPosition as a row matching the ETF table's column layout."""
    if fields is None:
        fields = _etf_fields()
    cells = list(original) if original else []
    cells += [""] * (len(fields) - len(cells))
    for i, field in enumerate(fields):
        if field is not None:
            cells[i] = _etf_cell_for(pos, field)
    return "| " + " | ".join(cells) + " |"


def _parse_etf_row(
    line: str, fields: list[str | None] | None = None
) -> EtfPosition | None:
    """Parse an ETF table row into an EtfPosition. None if not a data row."""
    if fields is None:
        fields = _etf_fields()
    cells = _split_row(line)
    if not cells:
        return None
    if cells[0].strip().lower() in ("赛道", "sector") or "---" in cells[0]:
        return None

    values: dict[str, str] = {}
    for i, field in enumerate(fields):
        if field is not None and i < len(cells):
            values[field] = cells[i]

    sector = values.get("sector", "").strip()
    if not sector:
        return None
    try:
        avg_cost_cell = values.get("avg_cost", "")
        cost_estimated = "~" in avg_cost_cell
        avg_raw = avg_cost_cell.replace("$", "").replace(",", "").replace("~", "").strip()
        avg_cost = float(avg_raw) if avg_raw and avg_raw != "—" else 0.0
        shares_raw = values.get("shares", "").replace(",", "").strip()
        shares = float(shares_raw) if shares_raw and shares_raw != "—" else 0.0
        target_raw = values.get("target_pct", "").replace("%", "").strip()
        return EtfPosition(
            sector=sector,
            ticker=values.get("ticker", "").strip(),
            shares=shares,
            avg_cost=avg_cost,
            target_pct=float(target_raw) if target_raw else 0.0,
            status=values.get("status", "").strip() or "Watch",
            notes=values.get("notes", ""),
            account=values.get("account", ""),
            entry_date=values.get("entry_date", ""),
            stop=_parse_dollar(values.get("stop", "")),
            target1=_parse_dollar(values.get("target1", "")),
            target2=_parse_dollar(values.get("target2", "")),
            order_placed=values.get("order_placed", ""),
            cost_estimated=cost_estimated,
        )
    except (ValueError, IndexError):
        return None


def _looks_like_closed_row(line: str) -> bool:
    """A Closed Positions row (10 cells, 3rd is a date) that drifted into another table.

    Observed 2026-07: five closed rounds sat under the ETF header. _parse_etf_row
    returned None for each and they were dropped without a word, so the rounds were
    invisible to anything reading either table.
    """
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    # 10 cells = pre-2026-07-27 schema (date in col 2); 13 = with 账户/轮次/类型 (dates in 5–6).
    if len(cells) == 10:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}|—", cells[2] or ""))
    if len(cells) == 13:
        # The Active table is also 13 columns wide (…Entry Date | LTCG日 … | 挂单 | Status | Notes)
        # with dates in the same cells. A Closed row carries P&L % in cell 10; an Active row's
        # cell 10 is the 挂单 note, so require a percent (or dash) there.
        if not re.fullmatch(r"[-+]?\d+(\.\d+)?%|—", cells[10] or ""):
            return False
        return any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", c or "") for c in cells[5:7])
    return False


def _iter_etf_rows() -> list[EtfPosition]:
    if not POSITIONS_FILE.exists():
        return []
    content = POSITIONS_FILE.read_text(encoding="utf-8")
    etf_section = _etf_section(content)
    if not etf_section:
        return []
    fields = _etf_fields(content)
    rows = []
    for line in etf_section.splitlines():
        if not line.startswith("|") or _is_table_separator(line):
            continue
        pos = _parse_etf_row(line, fields)
        if pos is not None:
            rows.append(pos)
            continue
        # Header row is a legitimate skip; anything else is malformed and must be loud.
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0].lower() in ("赛道", "sector"):
            continue
        if _looks_like_closed_row(line):
            print(
                f"ERROR: Closed Positions row found under '{ETF_SECTION_HEADER.strip()}' "
                f"— move it back to the Closed table: {line.strip()}",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: unparseable ETF row (skipped): {line.strip()}", file=sys.stderr)
    return rows


def _ensure_file_exists() -> None:
    if not POSITIONS_FILE.exists():
        from datetime import date
        POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        POSITIONS_FILE.write_text(
            TEMPLATE.format(date=date.today().isoformat()),
            encoding="utf-8",
        )


def add(pos: Position) -> None:
    """Append a new position row to the Active table."""
    _ensure_file_exists()
    content = POSITIONS_FILE.read_text(encoding="utf-8")

    # Insert new row before the "# Closed Positions" section
    closed_marker = "# Closed Positions"
    idx = content.find(closed_marker)
    if idx == -1:
        raise ValueError("positions.md missing '# Closed Positions' section")

    # Find end of Active table separator line and insert after it
    active_section = content[:idx]
    new_row = _format_active_row(pos) + "\n"
    new_content = active_section.rstrip() + "\n" + new_row + "\n" + content[idx:]
    POSITIONS_FILE.write_text(new_content, encoding="utf-8")


def _parse_dollar(s: str) -> float | None:
    """Parse a dollar cell like '$11.20', '~$65.00' or '' to float.

    Returns None for empty cells and for placeholders like '—' rather than
    raising, so one odd cell can't silently drop an entire position row.
    """
    s = s.strip().replace("~", "").replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_active_row(
    line: str, fields: list[str | None] | None = None
) -> Position | None:
    """Parse a single | A | B | C | ... | row into a Position. None if not a
    data row (header, separator, or missing a ticker).

    Column meaning comes from the table's header row, so adding a column to
    positions.md doesn't shift every field by one.
    """
    if fields is None:
        fields = _active_fields()
    cells = _split_row(line)
    if not cells:
        return None
    if cells[0].lower() == "ticker" or "---" in cells[0]:
        return None

    values: dict[str, str] = {}
    for i, field in enumerate(fields):
        if field is not None and i < len(cells):
            values[field] = cells[i]

    ticker = values.get("ticker", "").strip()
    if not ticker:
        return None
    try:
        shares = float(values.get("shares", "").replace(",", "").strip() or 0)
    except ValueError:
        return None

    return Position(
        ticker=ticker,
        shares=shares,
        avg_cost=_parse_dollar(values.get("avg_cost", "")),
        entry_date=values.get("entry_date", ""),
        stop=_parse_dollar(values.get("stop", "")),
        target1=_parse_dollar(values.get("target1", "")),
        target2=_parse_dollar(values.get("target2", "")),
        status=values.get("status", "").strip() or "Active",
        notes=values.get("notes", ""),
        account=values.get("account", ""),
        stock_type=values.get("stock_type", ""),
        ltcg=values.get("ltcg", ""),
        order_placed=values.get("order_placed", ""),
        cost_estimated="~" in values.get("avg_cost", ""),
    )


def _iter_active_rows() -> list[Position]:
    """Read all active position rows from positions.md.

    Warns on stderr for any table row that fails to parse — a silently dropped
    row reads downstream as 'no such position', which is worse than noisy.
    """
    if not POSITIONS_FILE.exists():
        return []
    content = POSITIONS_FILE.read_text(encoding="utf-8")
    closed_marker = "# Closed Positions"
    idx = content.find(closed_marker)
    active_section = content[:idx] if idx != -1 else content
    fields = _active_fields(content)

    rows = []
    for line in active_section.splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if not cells or cells[0].lower() == "ticker" or "---" in cells[0]:
            continue
        pos = _parse_active_row(line, fields)
        if pos is not None:
            rows.append(pos)
        elif _looks_like_closed_row(line):
            print(
                f"ERROR: Closed Positions row found in the Active table "
                f"— move it below '# Closed Positions': {line.strip()}",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: unparseable Active row (skipped): {line.strip()}", file=sys.stderr)
    return rows


def read_lots(ticker: str) -> list[Position]:
    """All active lots for a ticker, one per account row."""
    ticker = ticker.upper()
    return [p for p in _iter_active_rows() if p.ticker.upper() == ticker]


def _aggregate_lots(lots: list[Position]) -> Position:
    """Combine multiple account lots into one Position.

    Shares sum; cost is share-weighted.

    `stop`, `target1`, `target2`, `entry_date`, and `ltcg` are factual claims
    about the position, not labels — taking the largest lot's value would
    present one lot's number as the whole position's, which is how a split
    once silently changed a stop that no lot actually had, and would just as
    silently misreport an LTCG date with real tax consequences. So each of
    these is reported only when every lot agrees; otherwise the aggregate
    carries "no single value" (None for the float fields, "" for the string
    fields — see `_agreed` below), which downstream already renders as "not
    set" rather than presenting a guess as fact.

    `dominant` (the lot with the largest share count) is still used, but only
    for the prose/label fields — `status`, `notes`, `stock_type` — where
    picking one lot's text is a reasonable summary rather than a factual
    claim. A tie on share count falls back to file order for THOSE fields
    only; the fields above no longer depend on `dominant` at all, so a tie
    cannot make them wrong.
    """
    total_shares = sum(l.shares for l in lots)
    priced = [l for l in lots if l.avg_cost is not None]
    if priced and total_shares:
        weighted = sum(l.shares * l.avg_cost for l in priced)
        avg_cost = weighted / sum(l.shares for l in priced)
    else:
        avg_cost = None

    def _agreed(field: str, missing):
        """Single value only when every lot agrees; `missing` when they diverge.

        `missing` is the type-appropriate "not set" sentinel: None for the
        Optional[float] fields, "" for the str fields (entry_date/ltcg already
        treat "" — and, for ltcg, "—" — as "not set" elsewhere in this file,
        e.g. the round_id fallback below; reusing that convention here keeps
        the field's static type as str instead of surprising callers with
        Optional[str] on a field the dataclass declares as str).
        """
        vals = {getattr(l, field) for l in lots}
        return vals.pop() if len(vals) == 1 else missing

    dominant = max(lots, key=lambda l: l.shares)
    accounts = sorted({l.account.strip() for l in lots if l.account.strip()})
    return Position(
        ticker=dominant.ticker,
        shares=total_shares,
        avg_cost=avg_cost,
        entry_date=_agreed("entry_date", ""),
        stop=_agreed("stop", None),
        target1=_agreed("target1", None),
        target2=_agreed("target2", None),
        status=dominant.status,
        notes=dominant.notes,
        account=" + ".join(accounts),
        stock_type=dominant.stock_type,
        ltcg=_agreed("ltcg", ""),
        order_placed=_agreed("order_placed", ""),
        lot_count=len(lots),
    )


def read(ticker: str, account: str | None = None) -> Position | EtfPosition | None:
    """Position for a ticker. Aggregated across accounts unless one is named.

    Falls back to the ETF 赛道仓位 table when the ticker has no Active-table
    lot at all — ZZGR/ZQIC/etc. live there, not in read_lots()'s Active-only
    scope. The fallback only fires when the Active table has *nothing* for
    this ticker, so behavior for every ticker that already has an Active row
    is unchanged.

    Returns None if not found in either table.
    """
    lots = read_lots(ticker)
    if account is not None:
        wanted = account.strip().lower()
        for lot in lots:
            if lot.account.strip().lower() == wanted:
                return lot
        if lots:
            return None  # Active ticker exists, just not under this account.
        try:
            return _resolve_etf_lot(ticker, account)
        except (KeyError, AmbiguousLotError):
            return None
    if lots:
        if len(lots) == 1:
            return lots[0]
        return _aggregate_lots(lots)
    try:
        return _resolve_etf_lot(ticker, account)
    except (KeyError, AmbiguousLotError):
        return None


def _resolve_etf_lot(ticker: str, account: str | None) -> EtfPosition:
    """Pick exactly one ETF-table lot by ticker, or raise. Mirrors _resolve_lot.

    The ETF table is keyed by sector (a sector can hold one ticker, e.g. ZZGR,
    or several, e.g. 光互连's ZZQB+ZZZQ), not by ticker+account the way the
    Active table is. This indexes it by ticker instead, because that is what
    callers coming through trade()/close() actually have.
    """
    ticker = ticker.upper()
    rows = [r for r in _iter_etf_rows() if r.ticker.upper() == ticker]
    if not rows:
        raise KeyError(f"Ticker {ticker} not found in ETF positions")
    if account is not None:
        wanted = account.strip().lower()
        for r in rows:
            if r.account.strip().lower() == wanted:
                return r
        if len(rows) == 1 and not rows[0].account.strip():
            return rows[0]
        known = ", ".join(repr(r.account or "—") for r in rows)
        raise KeyError(f"{ticker} has no ETF lot in account {account!r}. Known: {known}")
    if len(rows) > 1:
        known = ", ".join(repr(r.account or "—") for r in rows)
        raise AmbiguousLotError(
            f"{ticker} has {len(rows)} ETF lots across accounts ({known}). "
            f"Pass account=... (CLI: --account) to choose one. "
            f"Refusing to guess which one you meant."
        )
    return rows[0]


def _resolve_lot(ticker: str, account: str | None) -> Position | EtfPosition:
    """Pick exactly one lot, or raise. Never silently takes the first match.

    Checks the Active table first; if the ticker has no Active row at all,
    falls back to the ETF 赛道仓位 table (see _resolve_etf_lot) rather than
    raising immediately — ZZGR/ZQIC/etc. only exist there, trade()/close()
    need to reach them too, not just the read-only `list --type ETF` path.
    """
    lots = read_lots(ticker)
    if not lots:
        try:
            return _resolve_etf_lot(ticker, account)
        except KeyError:
            raise KeyError(f"Ticker {ticker.upper()} not found in active positions") from None
    if account is not None:
        wanted = account.strip().lower()
        for lot in lots:
            if lot.account.strip().lower() == wanted:
                return lot
        if len(lots) == 1 and not lots[0].account.strip():
            # No ambiguity to resolve — the lot carries no real account, so
            # `account` here is closed-row metadata (e.g. close()'s round
            # labeling), not a discriminator. Callers that predate multi-lot
            # support pass it for single-lot tickers all the time; matching by
            # name would break them for no safety benefit. But if the lot DOES
            # carry a real account, a mismatch means the caller named the wrong
            # one — silently accepting it would write the wrong account label
            # onto the Closed-table row, the same silently-wrong-data failure
            # AmbiguousLotError exists to prevent, just scoped to metadata
            # instead of shares.
            return lots[0]
        known = ", ".join(repr(l.account or "—") for l in lots)
        raise KeyError(f"{ticker.upper()} has no lot in account {account!r}. Known: {known}")
    if len(lots) > 1:
        known = ", ".join(repr(l.account or "—") for l in lots)
        raise AmbiguousLotError(
            f"{ticker.upper()} has {len(lots)} lots across accounts ({known}). "
            f"Pass account=... (CLI: --account) to choose one. "
            f"Refusing to guess which one you meant."
        )
    return lots[0]


def list_active() -> list[Position]:
    """Return all positions with Active or Trimmed status."""
    return [p for p in _iter_active_rows() if p.status in ("Active", "Trimmed")]


def add_etf(pos: EtfPosition) -> None:
    """Append a new ETF position row to the ETF table, creating the section if needed."""
    existing = list_etf(sector=pos.sector)
    if existing:
        raise ValueError(
            f"ETF position for sector {pos.sector!r} already exists. Use update_etf() to modify it."
        )
    _ensure_file_exists()
    content = POSITIONS_FILE.read_text(encoding="utf-8")
    if ETF_SECTION_HEADER not in content:
        # No section yet: derive fields from the header we're about to write,
        # not the no-arg _etf_fields() default (which would fall back to
        # LEGACY_ETF_FIELDS since no header exists in `content` yet) — that
        # mismatch produced a row with fewer cells than the header columns.
        fields = [ETF_HEADER_FIELDS.get(c.strip().lower()) for c in _split_row(ETF_TABLE_HEADER)]
        new_row = _format_etf_row(pos, fields=fields) + "\n"
        etf_block = (
            f"\n{ETF_SECTION_HEADER}\n\n"
            f"{ETF_TABLE_HEADER}\n{ETF_TABLE_SEP}\n"
            f"{new_row}"
        )
        POSITIONS_FILE.write_text(content.rstrip() + "\n" + etf_block, encoding="utf-8")
    else:
        fields = _etf_fields(content)
        new_row = _format_etf_row(pos, fields=fields) + "\n"
        idx = content.find(ETF_SECTION_HEADER)
        after_header = content[idx + len(ETF_SECTION_HEADER):]
        next_h1 = after_header.find("\n# ")
        if next_h1 != -1:
            insert_at = idx + len(ETF_SECTION_HEADER) + next_h1
            content = content[:insert_at].rstrip() + "\n" + new_row + "\n" + content[insert_at:]
        else:
            content = content.rstrip() + "\n" + new_row
        POSITIONS_FILE.write_text(content, encoding="utf-8")


def list_etf(sector: str | None = None) -> list[EtfPosition]:
    """Return all ETF positions, optionally filtered by sector."""
    rows = _iter_etf_rows()
    if sector:
        rows = [r for r in rows if r.sector == sector]
    return rows


def read_portfolio_value() -> float:
    """Read portfolio_value from data/config.md."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "data/config.md not found. Create it with a line: portfolio_value: 38000"
        )
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("portfolio_value:"):
            return float(line.split(":")[1].strip())
    raise ValueError("'portfolio_value:' key not found in data/config.md")


def fetch_market_price(ticker: str) -> float | None:
    """Live price for a ticker, or None if unavailable.

    Imported lazily and failure-tolerant so positions.py stays usable offline
    and in tests — the network call only happens when a caller wants
    market-value gaps.

    yfinance logging is muted for the duration of the call: synthetic basket
    rows (e.g. "ZZQB+ZZZQ") are expected to 404, and the caller already has an
    explicit avg_cost fallback, so the stack trace is pure noise on stderr.
    """
    import logging

    yf_log = logging.getLogger("yfinance")
    prior_level, prior_disabled = yf_log.level, yf_log.disabled
    yf_log.setLevel(logging.CRITICAL)
    yf_log.disabled = True
    try:
        import yfinance as yf

        price = yf.Ticker(ticker).info.get("regularMarketPrice")
        return float(price) if price else None
    except Exception:
        return None
    finally:
        yf_log.setLevel(prior_level)
        yf_log.disabled = prior_disabled


def get_etf_gap(
    sector: str,
    price_lookup: Callable[[str], float | None] | None = None,
) -> dict:
    """Return gap-to-target info for an ETF sector, valued at market price.

    The gap must be measured against what the position is *worth*, not what it
    cost. Valuing at avg_cost understates the gap whenever the position is
    underwater — e.g. a sector reporting a $700 gap at cost when the true
    market-value gap is $860 (invented figures).

    price_lookup is injectable for tests; it falls back to avg_cost whenever a
    live price is unavailable (offline, delisted, or synthetic basket rows such
    as "ZZQA+ZZQB" whose avg_cost is already a market-value snapshot).
    """
    portfolio_value = read_portfolio_value()
    matches = list_etf(sector=sector)
    if not matches:
        # Sector has no ETF row configured yet (e.g. a newly-tracked sector
        # whose DCA target has not been set). Return a graceful result instead
        # of raising so callers (/etf-entry, /sector-check) can fall back to a
        # "target not configured" path rather than crashing.
        return {
            "sector": sector,
            "configured": False,
            "portfolio_value": portfolio_value,
        }
    pos = matches[0]

    lookup = price_lookup if price_lookup is not None else fetch_market_price
    market_price = lookup(pos.ticker)
    if market_price:
        unit_price, price_source = float(market_price), "market"
    else:
        unit_price, price_source = pos.avg_cost, "avg_cost"

    cost_basis = pos.shares * pos.avg_cost
    current_value = pos.shares * unit_price
    target_value = (pos.target_pct / 100) * portfolio_value
    gap = target_value - current_value
    current_pct = (current_value / portfolio_value * 100) if portfolio_value > 0 else 0.0
    return {
        "sector": pos.sector,
        "configured": True,
        "ticker": pos.ticker,
        "shares": pos.shares,
        "avg_cost": pos.avg_cost,
        "price": round(unit_price, 2),
        "price_source": price_source,
        "cost_basis": round(cost_basis, 2),
        "current_value": round(current_value, 2),
        "target_pct": pos.target_pct,
        "current_pct": round(current_pct, 2),
        "target_value": round(target_value, 2),
        "gap": round(gap, 2),
        "shares_to_target": round(gap / unit_price, 2) if unit_price else None,
        "portfolio_value": portfolio_value,
    }


def update_etf(sector: str, match_ticker: str | None = None, **fields) -> None:
    """Update fields on an existing ETF position row identified by sector.

    A sector can hold several tickers (e.g. 光互连 = ZZQB + ZZZQ); pass
    `match_ticker` to pick the row, otherwise the first row of the sector is
    updated. (`ticker` in **fields is the new value to write, not a selector.)
    """
    allowed = {"ticker", "shares", "avg_cost", "target_pct", "status", "notes",
               "account", "entry_date", "stop", "target1", "target2", "order_placed"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Unknown ETF fields: {bad}")
    if not POSITIONS_FILE.exists():
        raise KeyError(f"positions.md not found; sector {sector!r} not found")
    content = POSITIONS_FILE.read_text(encoding="utf-8")
    etf_fields = _etf_fields(content)
    lines = content.splitlines()
    in_etf = False
    found = False
    for i, line in enumerate(lines):
        if ETF_SECTION_HEADER in line:
            in_etf = True
            continue
        if in_etf and line.startswith("# "):
            break
        if in_etf and line.startswith("|"):
            pos = _parse_etf_row(line, etf_fields)
            if pos and pos.sector == sector and (
                match_ticker is None or pos.ticker.upper() == match_ticker.upper()
            ):
                for key, val in fields.items():
                    setattr(pos, key, val)
                lines[i] = _format_etf_row(pos, etf_fields, _split_row(line))
                found = True
                break
    if not found:
        target = f"{sector!r}" + (f" / {match_ticker.upper()}" if match_ticker else "")
        raise KeyError(f"Sector {target} not found in ETF positions")
    POSITIONS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_etf_row_by_ticker(ticker: str, account: str, **fields) -> None:
    """Update fields on one ETF-table row identified by ticker+account.

    Sibling of update_etf(), which is keyed by sector/match_ticker for the
    /etf-entry CLI path. This one is keyed by ticker+account because that is
    what _resolve_lot already resolved for update()/close() — re-deriving the
    sector here would just be another way to land on the wrong row.
    """
    content = POSITIONS_FILE.read_text(encoding="utf-8")
    etf_fields = _etf_fields(content)
    lines = content.splitlines()
    in_etf = False
    found = False
    wanted = account.strip().lower()
    for i, line in enumerate(lines):
        if ETF_SECTION_HEADER in line:
            in_etf = True
            continue
        if in_etf and line.startswith("# "):
            break
        if in_etf and line.startswith("|"):
            pos = _parse_etf_row(line, etf_fields)
            if (pos and pos.ticker.upper() == ticker.upper()
                    and pos.account.strip().lower() == wanted):
                for key, val in fields.items():
                    setattr(pos, key, val)
                lines[i] = _format_etf_row(pos, etf_fields, _split_row(line))
                found = True
                break
    if not found:
        raise KeyError(f"{ticker.upper()} / {account!r} not found in ETF positions")
    POSITIONS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update(ticker: str, account: str | None = None, **fields) -> None:
    """Update fields on one active lot (Active table, or the ETF table — see
    _resolve_lot's fallback).

    Allowed fields: shares, avg_cost, stop, target1, target2, status, notes
    (ETF-table rows additionally reject stock_type/ltcg — those columns don't
    exist there)

    Raises AmbiguousLotError if the ticker has several account lots and
    `account` was not given.
    """
    ticker = ticker.upper()
    # NOTE: "account" is intentionally absent. It is a named parameter above,
    # so it is always captured before reaching **fields — listing it here
    # would wrongly imply update(account=...) can write it via **fields.
    allowed = {"shares", "avg_cost", "stop", "target1", "target2", "status",
               "notes", "stock_type", "ltcg", "order_placed"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Unknown fields: {bad}")

    if not POSITIONS_FILE.exists():
        raise KeyError(f"positions.md does not exist; ticker {ticker} not found")

    target = _resolve_lot(ticker, account)

    if isinstance(target, EtfPosition):
        etf_bad = set(fields) - {"shares", "avg_cost", "stop", "target1",
                                  "target2", "status", "notes", "order_placed"}
        if etf_bad:
            raise ValueError(
                f"{ticker}: {etf_bad} not supported for an ETF-table position "
                f"(no stock_type/ltcg column there)")
        _update_etf_row_by_ticker(ticker, target.account, **fields)
        return

    content = POSITIONS_FILE.read_text(encoding="utf-8")
    lines = content.splitlines()
    active_fields = _active_fields(content)
    found = False

    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        pos = _parse_active_row(line, active_fields)
        if pos is None:
            continue
        if (pos.ticker.upper() == ticker
                and pos.account.strip() == target.account.strip()):
            for key, val in fields.items():
                setattr(pos, key, val)
            lines[i] = _format_active_row(pos, active_fields, _split_row(line))
            found = True
            break

    if not found:
        raise KeyError(f"Ticker {ticker} not found in active positions")

    POSITIONS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_to_closed_table(content: str, row: ClosedPosition) -> str:
    """Insert a row at the end of the Closed Positions *table*, not the end of the file.

    positions.md has an "# ETF 赛道仓位" section after Closed Positions. Appending to
    everything-from-the-Closed-header-onward lands the row past that table, where the
    ETF parser silently drops it — which is how five closed rounds went missing before
    2026-07-27. The row must go before whatever heading follows.
    """
    marker = "# Closed Positions"
    idx = content.find(marker)
    if idx == -1:
        raise ValueError("positions.md missing '# Closed Positions' section")
    nxt = content.find("\n# ", idx + len(marker))
    end = len(content) if nxt == -1 else nxt
    section = content[idx:end].rstrip()
    return content[:idx] + section + "\n" + _format_closed_row(row) + "\n" + content[end:]


def close(
    ticker: str,
    avg_exit: float,
    reason: str,
    closed_date: str,
    shares: float | None = None,
    account: str | None = None,
    round_id: str | None = None,
) -> None:
    """Record an exit. Full close by default; pass `shares` to log a partial trim.

    A trim keeps the position Active with the remaining share count and appends a
    减仓 row to the Closed table, so a round like ZZWF's (50 → trim 13 → trim 17 →
    20 held) stays reconstructable. Without this the trims live only in a prose
    notes field and the discipline review cannot see them.

    `account` selects which lot to close. It is REQUIRED when the ticker has
    more than one lot — see AmbiguousLotError.
    """
    ticker = ticker.upper()
    pos = _resolve_lot(ticker, account)

    qty = pos.shares if shares is None else float(shares)
    if qty <= 0:
        raise ValueError(f"shares must be positive, got {qty}")
    if qty > pos.shares:
        raise ValueError(f"cannot exit {qty} shares; only {pos.shares} held")
    is_partial = qty < pos.shares

    pnl_dollar = (avg_exit - pos.avg_cost) * qty
    pnl_pct = (avg_exit / pos.avg_cost - 1) * 100

    closed = ClosedPosition(
        ticker=pos.ticker,
        shares=qty,
        entry_date=pos.entry_date,
        exit_date=closed_date,
        avg_cost=pos.avg_cost,
        avg_exit=avg_exit,
        pnl_dollar=pnl_dollar,
        pnl_pct=pnl_pct,
        reason=reason,
        closed_date=closed_date,
        account=account or getattr(pos, "account", None) or "—",
        round_id=round_id or (pos.entry_date if pos.entry_date not in ("", "—") else "—"),
        kind="减仓" if is_partial else "全平",
    )

    if isinstance(pos, EtfPosition):
        # The ETF row is a sector slot (ZZGR, 光互连's ZZQB+ZZZQ, ...), not a
        # one-shot position — unlike an Active-table exit, it is never
        # deleted. A partial trim just shrinks shares; a full exit zeros
        # shares and marks Closed so /etf-entry and /sector-check can still
        # see the sector's target_pct and history for a future re-entry,
        # same as update_etf() already assumes for every other field.
        if is_partial:
            _update_etf_row_by_ticker(ticker, pos.account, shares=pos.shares - qty)
        else:
            _update_etf_row_by_ticker(ticker, pos.account, shares=0.0, status="Closed")
        POSITIONS_FILE.write_text(
            _append_to_closed_table(POSITIONS_FILE.read_text(encoding="utf-8"), closed),
            encoding="utf-8",
        )
        return

    if is_partial:
        # Keep the position Active with the remainder; only log the trim.
        update(ticker, account=pos.account, shares=pos.shares - qty, status="Trimmed")
        POSITIONS_FILE.write_text(
            _append_to_closed_table(POSITIONS_FILE.read_text(encoding="utf-8"), closed),
            encoding="utf-8",
        )
        return

    # Split into active + closed sections so we only remove from active
    content = POSITIONS_FILE.read_text(encoding="utf-8")
    closed_marker = "# Closed Positions"
    idx = content.find(closed_marker)
    if idx == -1:
        raise ValueError("positions.md missing '# Closed Positions' section")

    active_part = content[:idx]
    closed_part = content[idx:]

    # Remove the matching row from active_part only
    active_lines = active_part.splitlines()
    new_active_lines = []
    for line in active_lines:
        if line.startswith("|"):
            pos_check = _parse_active_row(line)
            if (pos_check and pos_check.ticker.upper() == ticker
                    and pos_check.account.strip() == pos.account.strip()):
                continue
        new_active_lines.append(line)

    new_active = "\n".join(new_active_lines).rstrip() + "\n\n"
    POSITIONS_FILE.write_text(
        _append_to_closed_table(new_active + closed_part, closed), encoding="utf-8"
    )


def _resolve_positions_file() -> None:
    """Allow override via env var POSITIONS_FILE (used in tests)."""
    import os
    global POSITIONS_FILE
    env_path = os.environ.get("POSITIONS_FILE")
    if env_path:
        POSITIONS_FILE = Path(env_path)


def _cmd_read(args) -> None:
    pos = read(args.ticker, account=getattr(args, "account", None))
    print(json.dumps(asdict(pos), ensure_ascii=False) if pos else "null")


def _cmd_list(args) -> None:
    if getattr(args, "type", None) == "ETF":
        etf_list = list_etf(sector=getattr(args, "sector", None))
        print(json.dumps([asdict(p) for p in etf_list], indent=2))
        return
    all_rows = _iter_active_rows()
    if args.status:
        positions_list = [p for p in all_rows if p.status == args.status]
    else:
        positions_list = [p for p in all_rows if p.status in ("Active", "Trimmed")]
    print(json.dumps([asdict(p) for p in positions_list], indent=2))


def _cmd_add(args) -> None:
    # A real holding needs both an account and a mapped type, or validator
    # rules #6 and #7 reject the row the moment it is written -- the CLI would
    # report ok and the PostToolUse hook would immediately fail. Reject here
    # instead, where the message is next to the cause.
    #
    # Watch rows and zero-share rows are exempt by the same rules: a candidate
    # is not held anywhere and has no type yet, and /market-weekly adds them
    # in bulk.
    is_holding = args.shares and args.shares > 0 and args.status != "Watch"
    if is_holding:
        if not (args.account or "").strip():
            raise ValueError(
                f"{args.ticker.upper()}: shares>0 的仓位必须给 --account，"
                f"否则校验规则 #6（账户必填）会立刻拒绝这一行")
        if not (args.stock_type or "").strip():
            raise ValueError(
                f"{args.ticker.upper()}: shares>0 的仓位必须给 --type，"
                f"否则校验规则 #7（类型须已映射）会立刻拒绝这一行")

    if (args.stock_type or "").strip():
        # Fail before writing rather than after: bucket_for() raises
        # UnknownBucketError by design, and that design only helps if it fires
        # before the unmapped value is in the file.
        import portfolio_buckets
        portfolio_buckets.bucket_for(args.stock_type)

    pos = Position(
        ticker=args.ticker.upper(), shares=args.shares, avg_cost=args.avg_cost,
        entry_date=args.entry_date, stop=args.stop,
        target1=args.target1, target2=args.target2,
        status=args.status, notes=args.notes or "",
        account=(args.account or "").strip(),
        stock_type=(args.stock_type or "").strip(),
    )
    add(pos)
    print(json.dumps({"ok": True, "added": asdict(pos)}))


def _cmd_update(args) -> None:
    fields = {k: v for k, v in vars(args).items()
              if k in {"shares", "avg_cost", "stop", "target1", "target2", "status", "notes",
                       "order_placed"}
              and v is not None}
    update(args.ticker, account=args.account, **fields)
    print(json.dumps({"ok": True, "ticker": args.ticker.upper(),
                      "account": args.account, "updated": fields}, ensure_ascii=False))


def _cmd_close(args) -> None:
    close(args.ticker, args.avg_exit, args.reason, args.closed_date,
          shares=args.shares, account=args.account, round_id=args.round_id)
    print(json.dumps({
        "ok": True, "ticker": args.ticker.upper(),
        "kind": "减仓" if args.shares else "全平",
        "shares": args.shares,
    }, ensure_ascii=False))


def _cmd_etf_add(args) -> None:
    pos = EtfPosition(
        sector=args.sector,
        ticker=args.ticker.upper() if args.ticker != "—" else "—",
        shares=args.shares,
        avg_cost=args.avg_cost,
        target_pct=args.target_pct,
        status=args.status,
        notes=args.notes,
        account=args.account,
        entry_date=args.entry_date,
    )
    add_etf(pos)
    print(json.dumps({"ok": True, "added": asdict(pos)}, ensure_ascii=False))


def _cmd_etf_update(args) -> None:
    fields = {k: v for k, v in vars(args).items()
              if k in {"ticker", "shares", "avg_cost", "target_pct", "status", "notes",
                       "account", "entry_date", "stop", "target1", "target2", "order_placed"}
              and v is not None}
    update_etf(args.sector, match_ticker=args.match_ticker, **fields)
    print(json.dumps({"ok": True, "sector": args.sector, "updated": fields}))


def _cmd_gap(args) -> None:
    # --no-market skips the network call and values the position at avg_cost,
    # which understates the gap on underwater positions — offline use only.
    lookup = (lambda _t: None) if args.no_market else None
    result = get_etf_gap(args.sector, price_lookup=lookup)
    print(json.dumps(result, indent=2))


def _cmd_trade(args) -> None:
    """Record a buy/sell: appends to the transaction log AND moves the position."""
    import transactions  # deferred so transactions.py can import positions
    transactions._resolve_transactions_file()

    action = transactions.BUY if args.buy is not None else transactions.SELL
    qty = args.buy if args.buy is not None else args.sell
    from datetime import date as _date
    txn = transactions.record_trade(
        ticker=args.ticker, account=args.account, action=action,
        shares=qty, price=args.price,
        date=args.date or _date.today().isoformat(),
        fees=args.fees, note=args.notes or "",
    )
    pos = read(args.ticker, account=txn.account)
    # `pos and pos.shares > 0`, not just `pos`: an ETF-table full close keeps
    # its row (zeroed, status=Closed — see close()) rather than deleting it
    # like an Active-table close does, so read() finds a real-but-empty
    # EtfPosition here instead of None. Without the shares check this would
    # report a hollow {"shares": 0.0, ...} instead of "closed".
    print(json.dumps({
        "ok": True, "action": action, "ticker": txn.ticker,
        "account": txn.account, "shares": txn.shares, "price": txn.price,
        "position_after": {"shares": pos.shares, "avg_cost": pos.avg_cost}
                          if pos and pos.shares > 0 else "closed",
    }, ensure_ascii=False))


def _force_utf8_stdout() -> None:
    """Force UTF-8 output (Windows cp1252 fix).

    Called from main() rather than at import time: reconfiguring sys.stdout as a
    side effect of importing this module mutated pytest's captured stream and
    broke subprocess handle setup for every test that ran afterwards
    (WinError 6: the handle is invalid). A library module has no business
    rewriting global stdio just because someone imported it.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _force_utf8_stdout()
    _resolve_positions_file()
    # When run as `python scripts/positions.py`, this module loads as
    # "__main__". transactions.record_trade() does `import positions`, and
    # since sys.modules has no "positions" entry, Python loads a SECOND,
    # independent copy of this same file — with its own POSITIONS_FILE that
    # never saw the override above. _cmd_trade then reads through this
    # (__main__) module while record_trade writes through the other one.
    # Aliasing "positions" to this already-running module makes the later
    # `import positions` resolve to the same object instead of a fresh copy.
    # setdefault, not assignment: when this file is imported normally (not
    # run as a script), __name__ is already "positions" and the slot is
    # already correctly occupied by this same module — assignment would be a
    # harmless no-op in that case too, but setdefault is the version that
    # can never clobber a distinct, already-imported "positions" module.
    sys.modules.setdefault("positions", sys.modules[__name__])
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read", help="Read single ticker position")
    p_read.add_argument("--ticker", required=True)
    p_read.add_argument("--account", help="Pick a single account lot instead of the aggregate")
    p_read.set_defaults(func=_cmd_read)

    p_list = sub.add_parser("list", help="List active positions")
    p_list.add_argument("--status", help="Filter by status (Active/Trimmed/Watching)")
    p_list.set_defaults(func=_cmd_list)

    p_add = sub.add_parser("add", help="Add new position")
    p_add.add_argument("--ticker", required=True)
    p_add.add_argument("--account", default="",
                       help="Required for shares>0 (validator rule #6)")
    p_add.add_argument("--type", dest="stock_type", default="",
                       help="Required for shares>0; must already be mapped in "
                            "portfolio_buckets.BUCKET_BY_TYPE (rule #7)")
    p_add.add_argument("--shares", type=float, required=True)
    p_add.add_argument("--avg-cost", type=float, required=True, dest="avg_cost")
    p_add.add_argument("--entry-date", required=True, dest="entry_date")
    p_add.add_argument("--stop", type=float)
    p_add.add_argument("--target1", type=float)
    p_add.add_argument("--target2", type=float)
    p_add.add_argument("--status", default="Active")
    p_add.add_argument("--notes", default="")
    p_add.set_defaults(func=_cmd_add)

    p_upd = sub.add_parser("update", help="Update active position fields")
    p_upd.add_argument("--ticker", required=True)
    p_upd.add_argument("--shares", type=float)
    p_upd.add_argument("--avg-cost", type=float, dest="avg_cost")
    p_upd.add_argument("--stop", type=float)
    p_upd.add_argument("--order-placed", dest="order_placed")
    p_upd.add_argument("--target1", type=float)
    p_upd.add_argument("--target2", type=float)
    p_upd.add_argument("--status")
    p_upd.add_argument("--notes")
    p_upd.add_argument("--account", help="Which account lot to update (required if >1)")
    p_upd.set_defaults(func=_cmd_update)

    p_cls = sub.add_parser("close", help="Close a position, or log a partial trim with --shares")
    p_cls.add_argument("--ticker", required=True)
    p_cls.add_argument("--avg-exit", type=float, required=True, dest="avg_exit")
    p_cls.add_argument("--reason", required=True,
                       help="Use single quotes in bash — $1/$6 in a double-quoted "
                            "string get shell-expanded away (corrupted 3 rows in 2026-07)")
    p_cls.add_argument("--closed-date", required=True, dest="closed_date")
    p_cls.add_argument("--shares", type=float,
                       help="Partial trim; omit to close the whole position")
    p_cls.add_argument("--account", help="Which account lot to close (required if >1)")
    p_cls.add_argument("--round", dest="round_id",
                       help="Round id; defaults to the position's entry date")
    p_cls.set_defaults(func=_cmd_close)

    # Extend p_list with ETF filtering
    p_list.add_argument("--type", choices=["ETF"], help="Set to ETF to list ETF positions")
    p_list.add_argument("--sector", help="Filter ETF by sector (use with --type ETF)")

    # New: etf-add
    p_etf_add = sub.add_parser("etf-add", help="Add ETF sector position")
    p_etf_add.add_argument("--sector", required=True)
    p_etf_add.add_argument("--ticker", required=True)
    p_etf_add.add_argument("--shares", type=float, default=0.0)
    p_etf_add.add_argument("--avg-cost", type=float, default=0.0, dest="avg_cost")
    p_etf_add.add_argument("--target-pct", type=float, required=True, dest="target_pct")
    p_etf_add.add_argument("--status", default="Watch")
    p_etf_add.add_argument("--notes", default="")
    p_etf_add.add_argument("--account", default="")
    p_etf_add.add_argument("--entry-date", default="", dest="entry_date")
    p_etf_add.set_defaults(func=_cmd_etf_add)

    # New: etf-update
    p_etf_upd = sub.add_parser("etf-update", help="Update ETF position fields by sector")
    p_etf_upd.add_argument("--sector", required=True)
    p_etf_upd.add_argument("--ticker")
    p_etf_upd.add_argument("--shares", type=float)
    p_etf_upd.add_argument("--avg-cost", type=float, dest="avg_cost")
    p_etf_upd.add_argument("--target-pct", type=float, dest="target_pct")
    p_etf_upd.add_argument("--status")
    p_etf_upd.add_argument("--notes")
    p_etf_upd.add_argument("--account")
    p_etf_upd.add_argument("--entry-date", dest="entry_date")
    p_etf_upd.add_argument("--match-ticker", dest="match_ticker",
                           help="select the row by ticker when a sector holds several")
    p_etf_upd.add_argument("--stop", type=float)
    p_etf_upd.add_argument("--target1", type=float)
    p_etf_upd.add_argument("--target2", type=float)
    p_etf_upd.add_argument("--order-placed", dest="order_placed")
    p_etf_upd.set_defaults(func=_cmd_etf_update)

    # New: gap
    p_gap = sub.add_parser("gap", help="Show ETF sector gap to target allocation (valued at market price)")
    p_gap.add_argument("--sector", required=True)
    p_gap.add_argument(
        "--no-market", action="store_true",
        help="Skip the live price lookup and value at avg_cost (offline; understates gap when underwater)",
    )
    p_gap.set_defaults(func=_cmd_gap)

    # New: trade
    p_trade = sub.add_parser("trade", help="Record a buy/sell and update the position")
    p_trade.add_argument("--ticker", required=True)
    p_trade.add_argument("--account", help="Required when the ticker has >1 lot")
    grp = p_trade.add_mutually_exclusive_group(required=True)
    grp.add_argument("--buy", type=float, metavar="SHARES")
    grp.add_argument("--sell", type=float, metavar="SHARES")
    p_trade.add_argument("--price", type=float, required=True, help="Per-share price")
    p_trade.add_argument("--date", help="Trade date (default: today)")
    p_trade.add_argument("--fees", type=float, default=0.0)
    p_trade.add_argument("--notes", help="Free text; ASCII '|' is converted to ｜")
    p_trade.set_defaults(func=_cmd_trade)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
