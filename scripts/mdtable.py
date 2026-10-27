#!/usr/bin/env python3
"""Markdown-table primitives shared by positions.py and transactions.py.

These four functions existed as two byte-identical copies. The cost of a copy
is not its line count -- it is that copies evolve apart. positions.py already
carries the scar: its Active and ETF tables grew twin parsers (~150 duplicated
lines), and every column added since has had to be added twice, with the
`account` and `entry_date` columns each getting forgotten in one of the two
constructors on the way. One implementation, imported by both, is how that
stops happening again.

Deliberately dependency-free: no imports, no module state, no file access.
Anything that needs to know *which* file or *which* table belongs upstream in
the module that owns that table.
"""


def split_row(line: str) -> list[str]:
    """Split a markdown table row into cells, dropping only the edge empties.

    Only the leading and trailing empties are dropped -- an empty cell in the
    middle is real data (a blank Stop column is not the same as no column) and
    must survive, or the row's cell count changes and the round-trip check
    starts firing on rows that are fine.
    """
    cells = [p.strip() for p in line.split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def is_table_separator(line: str) -> bool:
    """Whether this is the `|---|---|` rule under a header row."""
    return "---" in line and set(line.strip()) <= set("|-: ")


def sanitize_cell(text: str) -> str:
    """Replace ASCII pipe with full-width pipe (U+FF5C) so free-text cells
    (notes, reason) don't break markdown table column parsing on round-trip.
    Visually near-identical; idempotent (｜ is left untouched)."""
    return text.replace("|", "｜")


def fmt_shares(value: float) -> str:
    """Format share count: int if whole, otherwise up to 4 decimals trimmed.

    Canonical spelling matters here: the round-trip rule requires that parsing
    a row and re-serializing it produces the same bytes, so there must be
    exactly one way to write any given share count.
    """
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
