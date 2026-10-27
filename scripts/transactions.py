#!/usr/bin/env python3
"""Transaction log for data/transactions.md.

Two tables in one file: the trades themselves, and a declaration of which
(ticker, account) pairs claim a complete record. The declaration exists so
backfilling can be incremental — a pair nobody has declared is skipped by
the reconciliation check rather than reported as broken. "Not backfilled"
and "backfilled but disagreeing" must stay distinguishable.

Deliberately imports nothing from positions.py: this is the data layer and
stays independently testable. The write path that touches both files lives
in record_trade(), which does import positions.

Both tables are stored in canonical form. `_format_txn_row` /
`_format_decl_row` regenerate every modelled cell from the parsed values
(`_money`, `_fmt_shares`, ...) — they do not preserve the original text of
any cell the field map understands. `original` only fills in columns the
map doesn't model. So parse -> format is idempotent on a canonical row, but
is not literally "preserve whatever bytes were there": a hand-edited row
using different spacing (`... | $0.00 ||` instead of `... | $0.00 |  |`) or
a differently-formatted number (`$12.340`, `10.0`) parses to the same
values and re-serializes to the *canonical* spelling, not the original one.
The validator built on this module's round-trip rule will flag such a row
even though it is semantically fine — the fix is to rewrite it canonically,
not to loosen the check.

This mirrors scripts/positions.py, which hit the identical situation on
data/positions.md (39 rows failed round-trip purely on spacing/number
formatting; the fix was a one-time normalization to canonical form, after
which the invariant held exactly — see positions_validate.py's
`_check_roundtrip`). The trade is worth it: round-tripping byte-for-byte is
what catches the parser and a human reading different data from the same
row, and that guarantee only holds if there is exactly one valid spelling
per value. Since this file is written by record_trade() and never hand-typed
in normal use, it is canonical by construction — the exposure is a human
editing a row by hand.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass
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
TRANSACTIONS_FILE = REPO_ROOT / "data" / "transactions.md"

DECL_SECTION_HEADER = "# 已完整补录的持仓"
TXN_SECTION_HEADER = "# 交易记录"

TXN_TABLE_HEADER = "| Date | Ticker | 账户 | Action | Shares | Price | Fees | Note |"
TXN_TABLE_SEP    = "|------|--------|------|--------|--------|-------|------|------|"
DECL_TABLE_HEADER = "| Ticker | 账户 | 自 |"
DECL_TABLE_SEP    = "|--------|------|-----|"

TXN_HEADER_FIELDS = {
    "date": "date", "ticker": "ticker", "账户": "account", "account": "account",
    "action": "action", "shares": "shares", "price": "price",
    "fees": "fees", "note": "note",
}
LEGACY_TXN_FIELDS = ["date", "ticker", "account", "action",
                     "shares", "price", "fees", "note"]

DECL_HEADER_FIELDS = {
    "ticker": "ticker", "账户": "account", "account": "account", "自": "since",
}
LEGACY_DECL_FIELDS = ["ticker", "account", "since"]

BUY, SELL = "buy", "sell"


@dataclass
class Transaction:
    date: str
    ticker: str
    account: str
    action: str          # BUY | SELL
    shares: float        # always positive; direction is carried by `action`
    price: float
    fees: float = 0.0
    note: str = ""


@dataclass
class Declaration:
    ticker: str
    account: str
    since: str


TEMPLATE = f"""{DECL_SECTION_HEADER}

以下 (ticker, 账户) 组合的交易记录自称完整 —— 校验器会用交易表重算股数与均价，
与 positions.md 比对，不一致即报错。**未列出的组合完全跳过校验。**

{DECL_TABLE_HEADER}
{DECL_TABLE_SEP}

{TXN_SECTION_HEADER}

{TXN_TABLE_HEADER}
{TXN_TABLE_SEP}
"""


def _money(value: float) -> str:
    return f"${value:.2f}"


def _parse_money(raw: str) -> float | None:
    s = raw.strip().replace("$", "").replace(",", "").replace("~", "")
    if not s or s == "—":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read() -> str:
    if not TRANSACTIONS_FILE.exists():
        return ""
    return TRANSACTIONS_FILE.read_text(encoding="utf-8")


def _section(content: str, header: str) -> str:
    idx = content.find(header)
    if idx == -1:
        return ""
    body = content[idx + len(header):]
    nxt = body.find("\n# ")
    return body if nxt == -1 else body[:nxt]


def txn_section(content: str) -> str:
    return _section(content, TXN_SECTION_HEADER)


def decl_section(content: str) -> str:
    return _section(content, DECL_SECTION_HEADER)


def _fields_from(section: str, header_map: dict, legacy: list[str],
                 first_cell: set[str]) -> list[str | None]:
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if cells and cells[0].strip().lower() in first_cell:
            return [header_map.get(c.strip().lower()) for c in cells]
    return list(legacy)


def _txn_fields(content: str | None = None) -> list[str | None]:
    if content is None:
        content = _read()
    return _fields_from(txn_section(content), TXN_HEADER_FIELDS,
                        LEGACY_TXN_FIELDS, {"date"})


def _decl_fields(content: str | None = None) -> list[str | None]:
    if content is None:
        content = _read()
    return _fields_from(decl_section(content), DECL_HEADER_FIELDS,
                        LEGACY_DECL_FIELDS, {"ticker"})


def _parse_txn_row(line: str, fields: list[str | None] | None = None) -> Transaction | None:
    if fields is None:
        fields = _txn_fields()
    cells = _split_row(line)
    if not cells or cells[0].strip().lower() == "date" or "---" in cells[0]:
        return None
    vals = {f: cells[i] for i, f in enumerate(fields) if f is not None and i < len(cells)}
    if not vals.get("date", "").strip():
        return None
    shares = _parse_money(vals.get("shares", ""))
    price = _parse_money(vals.get("price", ""))
    if shares is None or price is None:
        return None
    return Transaction(
        date=vals.get("date", "").strip(),
        ticker=vals.get("ticker", "").strip().upper(),
        account=vals.get("account", "").strip(),
        action=vals.get("action", "").strip().lower(),
        shares=shares,
        price=price,
        fees=_parse_money(vals.get("fees", "")) or 0.0,
        note=vals.get("note", ""),
    )


def _txn_cell_for(t: Transaction, field: str) -> str:
    if field == "shares":
        return _fmt_shares(t.shares)
    if field in ("price", "fees"):
        return _money(getattr(t, field))
    return _sanitize_cell(str(getattr(t, field) or ""))


def _format_txn_row(t: Transaction, fields: list[str | None] | None = None,
                    original: list[str] | None = None) -> str:
    if fields is None:
        fields = _txn_fields()
    cells = list(original) if original else []
    cells += [""] * (len(fields) - len(cells))
    for i, f in enumerate(fields):
        if f is not None:
            cells[i] = _txn_cell_for(t, f)
    return "| " + " | ".join(cells) + " |"


def _parse_decl_row(line: str, fields: list[str | None] | None = None) -> Declaration | None:
    if fields is None:
        fields = _decl_fields()
    cells = _split_row(line)
    if not cells or cells[0].strip().lower() == "ticker" or "---" in cells[0]:
        return None
    vals = {f: cells[i] for i, f in enumerate(fields) if f is not None and i < len(cells)}
    ticker = vals.get("ticker", "").strip()
    if not ticker:
        return None
    return Declaration(ticker=ticker.upper(),
                       account=vals.get("account", "").strip(),
                       since=vals.get("since", "").strip())


def _format_decl_row(d: Declaration, fields: list[str | None] | None = None,
                     original: list[str] | None = None) -> str:
    if fields is None:
        fields = _decl_fields()
    cells = list(original) if original else []
    cells += [""] * (len(fields) - len(cells))
    for i, f in enumerate(fields):
        if f is not None:
            cells[i] = _sanitize_cell(str(getattr(d, f) or ""))
    return "| " + " | ".join(cells) + " |"


def _iter_rows(section: str, fields, parse, label: str) -> list:
    rows = []
    for line in section.splitlines():
        if not line.startswith("|") or _is_table_separator(line):
            continue
        obj = parse(line, fields)
        if obj is not None:
            rows.append(obj)
            continue
        cells = _split_row(line)
        if cells and cells[0].strip().lower() in ("date", "ticker"):
            continue
        print(f"ERROR: unparseable {label} row (skipped): {line.strip()}",
              file=sys.stderr)
    return rows


def iter_transactions() -> list[Transaction]:
    content = _read()
    if not content:
        return []
    return _iter_rows(txn_section(content), _txn_fields(content),
                      _parse_txn_row, "transaction")


def iter_declarations() -> list[Declaration]:
    content = _read()
    if not content:
        return []
    return _iter_rows(decl_section(content), _decl_fields(content),
                      _parse_decl_row, "declaration")


def is_declared(ticker: str, account: str) -> bool:
    """Whether this pair claims a complete transaction record."""
    tk, acct = ticker.strip().upper(), account.strip().lower()
    return any(d.ticker == tk and d.account.strip().lower() == acct
               for d in iter_declarations())


def new_avg_cost(old_shares: float, old_avg: float,
                 buy_shares: float, buy_price: float) -> float:
    """Share-weighted average cost after a buy.

    Fees are deliberately NOT folded in: positions.md's avg_cost has always
    meant "average execution price per share", and mixing fees into it makes
    the number stop matching a brokerage statement. Fees live on the log row
    and are picked up when realised P&L is computed.
    """
    total = old_shares + buy_shares
    if total <= 0:
        raise ValueError(f"total shares must be positive, got {total}")
    return (old_shares * old_avg + buy_shares * buy_price) / total


def replay(txns: list[Transaction]) -> tuple[float, float | None]:
    """Fold a trade list into (shares, avg_cost) at full precision.

    Both `record_trade` and the reconcile rule call this. They used to fold
    separately — record_trade incrementally, rounding to the cent and storing
    after every trade; the validator in one full-precision pass. Those are
    not the same number. An 8-leg DCA can finish $0.02 apart, twice
    AVG_COST_TOLERANCE, so rule #9 would eventually flag data that is
    perfectly correct. A check that cries wolf stops being read.

    Rounding is left to the caller: the storage layer rounds to two decimals
    because that is how positions.md spells money, and the validator rounds
    the same way at the same moment, so both land on the same cent.

    Returns avg_cost of None when nothing is left — a closed-out position has
    no cost basis, and 0.0 would be a claim rather than an absence.
    """
    shares, cost_total = 0.0, 0.0
    for t in sorted(txns, key=lambda x: x.date):
        if t.action == BUY:
            cost_total += t.shares * t.price
            shares += t.shares
        else:
            if shares > 0:
                cost_total -= (cost_total / shares) * t.shares
            shares -= t.shares
    return shares, (cost_total / shares if shares > 0 else None)


def _resolve_transactions_file() -> None:
    """Allow override via env var TRANSACTIONS_FILE (used in tests).

    Mirrors positions.py's _resolve_positions_file(). Not called at import
    time — the CLI entry point (positions.py's _cmd_trade) calls it after
    importing this module, so a plain `import transactions` from other code
    keeps writing to the real data file unless it opts in.
    """
    import os
    global TRANSACTIONS_FILE
    env_path = os.environ.get("TRANSACTIONS_FILE")
    if env_path:
        TRANSACTIONS_FILE = Path(env_path)


def _ensure_file() -> None:
    if not TRANSACTIONS_FILE.exists():
        TRANSACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRANSACTIONS_FILE.write_text(TEMPLATE, encoding="utf-8")


def _append_row(content: str, section_header: str, row: str) -> str:
    """Insert `row` at the end of that section's table, not the end of file."""
    idx = content.find(section_header)
    if idx == -1:
        raise ValueError(f"transactions.md missing {section_header!r}")
    nxt = content.find("\n# ", idx + len(section_header))
    end = len(content) if nxt == -1 else nxt
    section = content[:end].rstrip()
    return section + "\n" + row + "\n" + content[end:]


def record_trade(ticker: str, account: str | None, action: str,
                 shares: float, price: float, date: str,
                 fees: float = 0.0, note: str = "") -> Transaction:
    """Append a trade AND update the position. One write path, by design.

    Recording the trade and moving the position are the same operation here.
    Split across two commands they can diverge — one gets run, the other
    forgotten — and a reconciliation check would only find that afterwards.
    A single entry point makes the divergence impossible for new trades.

    Every check that can raise runs BEFORE anything is written. There is no
    real transaction across two files; what is guaranteed is that a rejected
    trade leaves both files untouched.
    """
    import positions  # deferred: positions.py's CLI imports this module back

    if action not in (BUY, SELL):
        raise ValueError(f"action must be {BUY!r} or {SELL!r}, got {action!r}")
    if shares <= 0:
        raise ValueError(f"shares must be positive, got {shares}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")

    lot = positions._resolve_lot(ticker, account)   # raises on unknown/ambiguous
    if lot.shares > 0 and lot.avg_cost is None:
        raise ValueError(
            f"{ticker.strip().upper()} in {lot.account.strip()!r} holds "
            f"{lot.shares} shares with a blank avg_cost. Fill it in before "
            f"logging trades: on a buy, defaulting it to $0 would treat the "
            f"existing shares as free and quietly invent a cost basis; on a "
            f"sell to zero it would blow up inside close() *after* the log "
            f"row was written, leaving exactly the divergence this single "
            f"write path exists to prevent.")
    if action == SELL and shares > lot.shares:
        raise ValueError(
            f"cannot sell {shares} shares of {ticker.upper()} in "
            f"{lot.account!r}; only {lot.shares} held")

    txn = Transaction(date=date, ticker=ticker.strip().upper(),
                      account=lot.account.strip(), action=action,
                      shares=shares, price=price, fees=fees, note=note)

    starts_from_zero = lot.shares == 0
    if starts_from_zero or is_declared(txn.ticker, txn.account):
        # The log covers this position from inception, so replaying it is the
        # authoritative answer — and it is byte-for-byte the computation rule
        # #9 will redo, which is the point: incremental rounding drifts, a
        # shared replay cannot.
        prior = [t for t in iter_transactions()
                 if t.ticker == txn.ticker
                 and t.account.strip().lower() == txn.account.lower()]
        remaining, avg_full = replay(prior + [txn])
        avg = None if avg_full is None else round(avg_full, 2)
    elif action == BUY:
        # Pre-existing position: the log cannot see the original entry, so
        # fold onto the recorded cost instead. Rule #9 skips these pairs.
        remaining = lot.shares + shares
        avg = round(new_avg_cost(lot.shares, lot.avg_cost, shares, price), 2)
    else:
        remaining = lot.shares - shares
        avg = lot.avg_cost

    # ---- writes start here; everything above can raise safely ----
    _ensure_file()
    content = TRANSACTIONS_FILE.read_text(encoding="utf-8")
    content = _append_row(content, TXN_SECTION_HEADER,
                          _format_txn_row(txn, _txn_fields(content)))
    if starts_from_zero and not any(
            d.ticker == txn.ticker
            and d.account.strip().lower() == txn.account.lower()
            for d in _iter_rows(decl_section(content), _decl_fields(content),
                               _parse_decl_row, "declaration")):
        content = _append_row(
            content, DECL_SECTION_HEADER,
            _format_decl_row(Declaration(txn.ticker, txn.account, date),
                             _decl_fields(content)))
    TRANSACTIONS_FILE.write_text(content, encoding="utf-8")

    if remaining <= 0:
        positions.close(ticker, avg_exit=price,
                        reason=note or "transaction log: 卖光",
                        closed_date=date, account=lot.account)
    else:
        positions.update(ticker, account=lot.account,
                         shares=remaining, avg_cost=avg)
    return txn
