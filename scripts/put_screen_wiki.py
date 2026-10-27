#!/usr/bin/env python3
"""Wiki universe for the put screen: ticker status and carrier tier.

Status comes from `wiki/tickers/<T>/overview.md` (`**Status**:` line); tier
(🏦/🚀/⚡) comes from carrier tables in `wiki/sectors/*.md` (English pages only).
See docs/superpowers/specs/2026-10-18-put-screen-design.md.
"""
from __future__ import annotations
import codecs
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TIER_EMOJIS = ("🏦", "🚀", "⚡")
CONFLICT_NOTE = "档位在多个赛道页中不一致"
NO_DATE = "0000-00-00"

_STATUS_KEYWORDS = (
    (re.compile(r"avoid|回避", re.I), "avoid"),
    (re.compile(r"under\s+review", re.I), "under_review"),
    (re.compile(r"watch|观察", re.I), "watch"),
    (re.compile(r"\bactive\b", re.I), "active"),
    (re.compile(r"\buse\b", re.I), "use"),
)
_STATUS_LINE = re.compile(r"^[> ]*\*\*Status(?:\*\*\s*:|:\*\*)\s*(.*)$", re.M)
_DATE_LINE = re.compile(r"\*\*Last updated\*\*\s*:\s*(\d{4}-\d{2}-\d{2})")
_TICKER = re.compile(r"^[A-Z]{1,5}$")


@dataclass(frozen=True)
class UniverseRow:
    ticker: str
    status: str
    tier: str | None
    notes: tuple[str, ...]


def parse_status(line: str) -> str:
    """Earliest status keyword in the text after `**Status**:` wins."""
    best: tuple[int, str] | None = None
    for pattern, status in _STATUS_KEYWORDS:
        m = pattern.search(line)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), status)
    return best[1] if best else "unrecognized"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _row_tier(text: str) -> tuple[str, str] | None:
    """Return (ticker, tier) for a carrier-table row, else None."""
    line = text.strip()
    if not line.startswith("|"):
        return None
    cells = line.strip("|").split("|")
    first = cells[0].replace("*", "").replace("`", "").strip()
    if not _TICKER.match(first):
        return None
    hits = [(line.find(e), e) for e in TIER_EMOJIS if e in line]
    if not hits:
        return None
    return first, min(hits)[1]


def parse_sector_tiers(sector_dir: Path) -> dict[str, tuple[str, bool]]:
    """ticker -> (tier, conflict); latest page date wins, ties by filename."""
    per_page: dict[str, list[tuple[str, str, str]]] = {}
    for path in sorted(Path(sector_dir).glob("*.md")):
        if path.name.endswith(".zh.md"):
            continue
        text = _read(path)
        m = _DATE_LINE.search(text)
        date = m.group(1) if m else NO_DATE
        for raw in text.split("\n"):
            hit = _row_tier(raw)
            if hit:
                per_page.setdefault(hit[0], []).append((date, path.name, hit[1]))
    result: dict[str, tuple[str, bool]] = {}
    for ticker, entries in per_page.items():
        latest = max(entries, key=lambda e: (e[0], e[1]))
        result[ticker] = (latest[2], len({e[2] for e in entries}) > 1)
    return result


def _read_status(overview: Path) -> str:
    m = _STATUS_LINE.search(_read(overview))
    return parse_status(m.group(1)) if m else "unrecognized"


def read_status(overview: Path) -> str:
    """Parsed Status of one overview page (same rule the universe builder uses)."""
    return _read_status(overview)


def build_universe(wiki_dir: Path) -> list[UniverseRow]:
    wiki_dir = Path(wiki_dir)
    tiers = parse_sector_tiers(wiki_dir / "sectors")
    rows = []
    for overview in sorted((wiki_dir / "tickers").glob("*/overview.md")):
        ticker = overview.parent.name
        tier, conflict = tiers.get(ticker, (None, False))
        rows.append(UniverseRow(
            ticker=ticker,
            status=_read_status(overview),
            tier=tier,
            notes=(CONFLICT_NOTE,) if conflict else (),
        ))
    return sorted(rows, key=lambda r: r.ticker)


# --- ticker lists: --tickers and data/watchlist.txt (no wiki needed) -------------------------

# Same shape as spread_sim.TICKER_PATTERN (a test pins the two together): no slash, backslash,
# or space, so a ticker can never steer a file name outside the ledger folder.
LIST_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,9}")
DEFAULT_STATUS = "watch"
_VARIATION_SELECTOR = chr(0xFE0F)  # emoji presentation selector, e.g. in "⚡️"


def _list_row(ticker: str, status: str = DEFAULT_STATUS, tier: str | None = None) -> UniverseRow:
    return UniverseRow(ticker=ticker, status=status, tier=tier, notes=())


def _checked_ticker(text: str) -> str:
    ticker = text.strip().upper()
    if not LIST_TICKER.fullmatch(ticker):
        raise ValueError(f"代码 {text!r} 不合法：只能是字母、数字、点和连字符，最长 10 位")
    return ticker


def universe_from_tickers(csv: str) -> list[UniverseRow]:
    """Rows for `--tickers AAA,BBB`: upper case, repeats keep the first, no tier, status watch."""
    seen: dict[str, UniverseRow] = {}
    for part in csv.split(","):
        if part.strip():
            ticker = _checked_ticker(part)
            seen.setdefault(ticker, _list_row(ticker))
    if not seen:
        raise ValueError("--tickers 里没有任何代码")
    return list(seen.values())


def _decode(raw: bytes) -> str:
    """Notepad writes UTF-8 with a BOM, or UTF-16 when 'Unicode' is picked; both are read."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16").lstrip(chr(0xFEFF))  # a doubled BOM is seen in the wild
    return raw.decode("utf-8-sig")


def _clean_line(line: str) -> str:
    """NFKC turns full-width space, letters and hash into ASCII; then drop the comment."""
    text = unicodedata.normalize("NFKC", line).replace(_VARIATION_SELECTOR, "")
    return text.split("#", 1)[0].strip()


def _split_tier(text: str) -> tuple[str, str | None]:
    """(text with tier emoji replaced by spaces, the earliest tier emoji in it or None)."""
    hits = sorted((text.find(e), e) for e in TIER_EMOJIS if e in text)
    for emoji in TIER_EMOJIS:
        text = text.replace(emoji, " ")
    return text, (hits[0][1] if hits else None)


def _parse_list_line(line: str, number: int) -> UniverseRow | None:
    text = _clean_line(line)
    if not text:
        return None
    text, tier = _split_tier(text)
    head, *tail = text.split(None, 1) or [""]  # any whitespace: space, tab
    try:
        ticker = _checked_ticker(head)
    except ValueError as exc:
        raise ValueError(f"第 {number} 行：{exc}") from exc
    rest = tail[0].strip() if tail else ""
    return _list_row(ticker, parse_status(rest) if rest else DEFAULT_STATUS, tier)


def universe_from_list(path: Path) -> list[UniverseRow]:
    """Rows from `TICKER [🏦|🚀|⚡] [watch|avoid|under review]` lines; repeats keep the first.

    Written by hand on Windows, so BOM / UTF-16, full-width characters, lower case, blank
    lines and `#` comments are all accepted. A status word nobody knows becomes
    "unrecognized", which the screen shows as a reject instead of silently dropping the line.
    """
    try:
        text = _decode(Path(path).read_bytes())
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"读不了 {Path(path).name}：{exc}") from exc
    seen: dict[str, UniverseRow] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        row = _parse_list_line(line, number)
        if row is not None:
            seen.setdefault(row.ticker, row)
    return list(seen.values())
