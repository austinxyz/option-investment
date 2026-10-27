#!/usr/bin/env python3
"""Data adapters for /strategy-pick: prices, option snapshot, wiki thesis, holdings.

Every function that touches the network or a file is a separate stub point for tests. Positions and
the wiki are read-only here: no writer in `positions` is ever called, and a missing
data/positions.md is reported (positions_missing) rather than created. See design 6.1-6.4, 6.11,
section 9. Task 5 passes `holding_from_read(read)` to the tree, which also covers the missing file.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import option_settings
import positions
import spread_core
import spread_data
from ibkr_session import GatewayUnavailable
from put_screen_wiki import parse_sector_tiers, read_status
import strategy_pick_rules as R
from strategy_pick_inputs import Holding, holding_state, select_iv_expiry

HISTORY_PERIOD = "1y"
VERDICT_MAX_CHARS = 200
ATM_STRIKES_EACH_SIDE = 5
STRIKE_BAND_PCT = 0.15
MIN_WINDOW_ROWS = 6
WIDE_STRIKES_EACH_SIDE = 12
# Real headers: "> **Verdict**:" (blockquote) and "**Verdict（2026-10-16）**:"; "**Moat Verdict**" is not it.
_VERDICT = re.compile(r"^[> ]*\*\*Verdict(?:[（(][^)）\n]*[)）])?\*\*\s*[:：]\s*(.*)$", re.M)
_AS_OF = re.compile(r"\*\*As of\*\*\s*:\s*(\d{4}-\d{2}-\d{2})", re.I)
_LAST_UPDATED = re.compile(r"\*\*Last updated\*\*\s*:\s*(\d{4}-\d{2}-\d{2})", re.I)
_TYPE_TAG = re.compile(r"【类型[:：]\s*([^|｜】]+)")
MIXED_TYPES_WARNING = "同一票在不同账户的“类型”不一致，取第一个非空值"
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class MarketDataError(Exception):
    """The year of daily history could not be loaded or is unusable."""


# ---- result types (separate from the decision inputs; Task 5 maps them) ----

@dataclass(frozen=True)
class PriceHistory:
    dates: tuple[date, ...]
    closes: tuple[float, ...]    # adjusted for splits and dividends
    volumes: tuple[float, ...]


@dataclass(frozen=True)
class OptionSnapshot:
    snapshot_date: date
    spot: float
    expiries: tuple[date, ...]
    expiry_used: date | None
    puts: tuple[dict, ...]       # raw rows near the money; bids kept so the gate can judge zeros
    calls: tuple[dict, ...]
    atm_iv: float | None
    atm_put: dict | None
    atm_call: dict | None
    strikes_15pct: int | None
    # Counted from the chain-wide strike union, which overcounts coarse weekly grids; Task 5 labels it.
    strikes_15pct_proxy: bool = True


@dataclass(frozen=True)
class ThesisInfo:
    status: str | None
    verdict_text: str | None
    age_days: int | None
    is_etf_page: bool
    found: bool            # both overview.md and thesis.md exist (design 6.3)
    has_overview: bool = False
    has_thesis: bool = False


@dataclass(frozen=True)
class HoldingRow:
    shares: float
    ltcg_reached: bool
    source: str                  # active / etf; no account name, cost or P&L is kept


@dataclass(frozen=True)
class HoldingRead:
    rows: tuple[HoldingRow, ...]  # one per account row, never merged
    stock_type: str | None
    in_etf_table: bool
    positions_missing: bool
    warnings: tuple[str, ...] = ()


# ---- yfinance ----

def _ticker(symbol: str):
    return option_settings.yf_module().Ticker(symbol.strip().upper())


def _finite_positive(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x)) and float(x) > 0
    except (TypeError, ValueError):
        return False


def _clean_rows(hist) -> list[tuple[date, float, float]]:
    rows = []
    for idx, close, vol in zip(hist.index, hist["Close"], hist["Volume"]):
        if not _finite_positive(close):   # NaN / zero closes would poison returns and MAs
            continue
        rows.append((idx.date(), float(close), float(vol) if _finite_positive(vol) else 0.0))
    return rows


def load_history(ticker: str) -> PriceHistory:
    """One year of split- and dividend-adjusted daily bars; any failure is a MarketDataError."""
    try:
        hist = _ticker(ticker).history(period=HISTORY_PERIOD, auto_adjust=True)
        rows = [] if hist is None or hist.empty else _clean_rows(hist)
    except Exception as exc:
        raise MarketDataError(f"取 {ticker} 行情失败：{exc}") from exc
    if not rows:
        raise MarketDataError(f"{ticker} 没有行情数据（代码是否正确？）")
    dates, closes, volumes = zip(*rows)
    return PriceHistory(dates, closes, volumes)


def load_info(ticker: str) -> dict:
    """market_cap and quote_type; a missing field or a failed fetch is None, never an error."""
    try:
        info = _ticker(ticker).info or {}
    except Exception:
        info = {}
    cap, quote_type = info.get("marketCap"), info.get("quoteType")
    return {"market_cap": float(cap) if _finite_positive(cap) else None,
            "quote_type": quote_type if isinstance(quote_type, str) and quote_type else None}


def next_earnings(ticker: str, snapshot: date) -> date | None:
    """First calendar earnings date after the snapshot (same source as earnings_status)."""
    try:
        cal = _ticker(ticker).calendar
        raw = cal.get("Earnings Date", []) if isinstance(cal, dict) else []
        found = [d if isinstance(d, date) and not hasattr(d, "hour") else d.date() for d in raw]
    except Exception:
        return None
    future = sorted(d for d in found if d > snapshot)
    return future[0] if future else None


def _oi_at(frame, strike: float) -> int | None:
    try:
        hits = frame[frame["strike"] == strike]["openInterest"]
        values = [float(v) for v in hits if v == v]
    except Exception:
        return None
    return int(sum(values)) if values else None


def _listed_strike(chain, strike: float) -> float | None:
    """`strike` itself, else the nearest strike this expiry lists (equally near -> the lower).
    The OI expiry can be the monthly while `strike` came from a finer weekly grid."""
    listed = sorted({float(s) for s in list(chain.puts["strike"]) + list(chain.calls["strike"])})
    return min(listed, key=lambda s: (abs(s - strike), s)) if listed else None


def _oi_unavailable(chain, at: float) -> bool:
    """True when every put and call within GATE_STRIKE_RANGE_PCT of `at` has OI 0 or NaN.
    Yahoo blanks OI outside regular hours (2026-10-26: AAPL / SPY / QQQ all 0 near the money, only
    stale deep-ITM strikes kept a value); a real thin name still shows some OI near the money."""
    band = R.GATE_STRIKE_RANGE_PCT * at
    for frame in (chain.puts, chain.calls):
        near = frame[(frame["strike"] - at).abs() <= band]["openInterest"]
        if any(v == v and float(v) > 0 for v in near):
            return False
    return True


def atm_open_interest(ticker: str, expiry: date, strike: float) -> int | None:
    """Put + call open interest at one strike (nearest listed); None on any failure, when
    neither side has it, or when the whole near-money chain reads 0 / NaN (data missing)."""
    try:
        chain = _ticker(ticker).option_chain(expiry.isoformat())
        at = _listed_strike(chain, strike)
        if at is None or _oi_unavailable(chain, at):
            return None
        parts = [oi for oi in (_oi_at(chain.puts, at), _oi_at(chain.calls, at)) if oi is not None]
    except Exception:
        return None
    return sum(parts) if parts else None


# ---- IBKR option snapshot ----

def _nearest(rows: list[dict], spot: float) -> dict | None:
    usable = [r for r in rows if r.get("strike") is not None]
    return min(usable, key=lambda r: abs(float(r["strike"]) - spot)) if usable else None


def _band_count(strikes: list[float], spot: float) -> int:
    return sum(1 for s in strikes if abs(s - spot) <= STRIKE_BAND_PCT * spot)


def _near_money(strikes: list[float], spot: float) -> list[float]:
    ordered = sorted(set(strikes))
    below = [s for s in ordered if s <= spot][-ATM_STRIKES_EACH_SIDE:]
    above = [s for s in ordered if s > spot][:ATM_STRIKES_EACH_SIDE]
    return below + above


def _wide_targets(strikes: list[float], spot: float) -> list[float]:
    ordered = sorted(set(strikes))
    below = [s for s in ordered if s <= spot][-WIDE_STRIKES_EACH_SIDE:]
    above = [s for s in ordered if s > spot][:WIDE_STRIKES_EACH_SIDE]
    return below + above


def _fetch_side(fetch, ticker: str, expiry: str, listed: list[float], spot: float):
    """Rows for one side, or the error when the broker has nothing; a lost gateway always raises."""
    rows, err = [], None
    # The strike list is the union over expiries, so a coarse expiry qualifies fewer contracts than
    # asked; widen once when the narrow request leaves too few rows. Window 0: only requested strikes.
    for targets in (_near_money(listed, spot), _wide_targets(listed, spot)):
        try:
            rows = list(fetch(ticker, expiry, strikes=targets, window_pct=0.0, window_limit=0))
            err = None
        except spread_data.SourceUnavailable:
            raise
        except spread_data.DataError as exc:
            rows, err = [], exc
        if len(rows) >= MIN_WINDOW_ROWS:
            break
    return rows, err


def _chain_strikes(ticker: str) -> list[float]:
    try:
        return [float(s) for s in spread_data.chain_strikes(ticker)]
    except spread_data.SourceUnavailable:
        raise
    except spread_data.DataError:
        return []


def _no_quotes(ticker: str, err: Exception | None) -> spread_data.DataError:
    """No quotes on either side. IBKR: unchanged GatewayUnavailable (usually a competing
    session). Other sources: a plain per-ticker DataError (design 8.5) — the source answered,
    the chain simply had no usable quotes."""
    if option_settings.is_ibkr():
        return GatewayUnavailable(str(err or f"没有拿到 {ticker} 的期权报价"))
    return spread_data.DataError(str(err or f"没有拿到 {ticker} 的期权报价（休市后常没有买卖报价）"))


def option_snapshot(ticker: str, snapshot: date) -> OptionSnapshot:
    """Chain facts for the gate and IV: a small ATM window of the expiry nearest 30 days (20-60).

    Gateway down, dropped mid-run, or no quotes at all (e.g. error 10197) raises GatewayUnavailable
    (IBKR); on Yahoo / demo it is a plain DataError.
    A DataError from last_close / expirations (stock not found, no options) propagates as a plain
    DataError. strikes_15pct counts the chain-wide strike union (no cheap per-expiry API), so it can
    overcount coarse weekly grids: strikes_15pct_proxy is True and the report must say so.
    """
    spot = float(spread_data.last_close(ticker)[1])
    expiries = tuple(sorted(date.fromisoformat(e) for e in spread_data.expirations(ticker)))
    used = select_iv_expiry(list(expiries), snapshot)
    if used is None:
        return OptionSnapshot(snapshot, spot, expiries, None, (), (), None, None, None, None)
    listed = _chain_strikes(ticker)
    puts, put_err = _fetch_side(spread_data.fetch_puts, ticker, used.isoformat(), listed, spot)
    calls, call_err = _fetch_side(spread_data.fetch_calls, ticker, used.isoformat(), listed, spot)
    if not puts and not calls:
        raise _no_quotes(ticker, put_err or call_err)
    iv = spread_core.atm_iv(puts, spot)
    iv = iv if iv is not None else spread_core.atm_iv(calls, spot)
    strikes_15 = _band_count(listed, spot) if listed else None
    return OptionSnapshot(snapshot, spot, expiries, used, tuple(puts), tuple(calls), iv,
                          _nearest(puts, spot), _nearest(calls, spot), strikes_15)


# ---- wiki (read-only) ----

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        return ""


def _page_dir(ticker: str, wiki_root: Path) -> tuple[Path, bool]:
    etf = Path(wiki_root) / "etf" / ticker.upper()
    if etf.is_dir():
        return etf, True
    return Path(wiki_root) / "tickers" / ticker.upper(), False


def _latest_date(*found: re.Match | None) -> date | None:
    days = [date.fromisoformat(m.group(1)) for m in found if m]
    return max(days) if days else None


def read_thesis(ticker: str, wiki_root: Path, today: date | None = None) -> ThesisInfo:
    """Status from overview.md, display-only Verdict (<=200 chars) and staleness from thesis.md."""
    page, is_etf = _page_dir(ticker, wiki_root)
    has_overview, has_thesis = (page / "overview.md").exists(), (page / "thesis.md").exists()
    overview, thesis = _read(page / "overview.md"), _read(page / "thesis.md")
    verdict = _VERDICT.search(thesis)
    stamp = _latest_date(_AS_OF.search(thesis), _LAST_UPDATED.search(thesis),
                         _LAST_UPDATED.search(overview))
    age = ((today or date.today()) - stamp).days if stamp else None
    return ThesisInfo(read_status(page / "overview.md") if has_overview else None,
                      verdict.group(1).strip()[:VERDICT_MAX_CHARS] if verdict else None,
                      age, is_etf, has_overview and has_thesis, has_overview, has_thesis)


def wiki_tier(ticker: str, wiki_root: Path) -> str | None:
    """Tier emoji from the sector carrier tables, None when the ticker is in none of them."""
    hit = parse_sector_tiers(Path(wiki_root) / "sectors").get(ticker.upper())
    return hit[0] if hit else None


# ---- positions (read-only) ----

def _ltcg_reached(cell: str, today: date) -> bool:
    cell = (cell or "").strip()
    if "✅" in cell:
        return True
    m = _ISO_DAY.search(cell)
    return bool(m) and date.fromisoformat(m.group(0)) <= today


def _held_a_year(entry: str, today: date) -> bool:
    """ETF rows have no LTCG column: approximate with the first-lot Entry Date (DCA is first batch)."""
    m = _ISO_DAY.search(entry or "")
    return bool(m) and date.fromisoformat(m.group(0)) <= today - timedelta(days=365)


def _lot_type(lot) -> str | None:
    """The 类型 column when it carries a known tier emoji, else the notes tag 【类型: ...】."""
    column = (lot.stock_type or "").strip()
    if any(e in column for e in R.CLASS_BY_TIER_EMOJI):
        return column
    tag = _TYPE_TAG.search(lot.notes or "")
    return tag.group(1).strip() if tag else (column if column not in ("", "—") else None)


def read_holding(ticker: str, today: date | None = None) -> HoldingRead:
    """Per-account rows from the Active and ETF tables; a missing file is reported, not created."""
    if not positions.POSITIONS_FILE.exists():
        return HoldingRead((), None, False, True)
    day, sym = today or date.today(), ticker.upper()
    lots = positions.read_lots(sym)
    etf_rows = [r for r in positions.list_etf() if r.ticker.upper() == sym]
    rows = [HoldingRow(p.shares, _ltcg_reached(p.ltcg, day), "active") for p in lots]
    rows += [HoldingRow(r.shares, _held_a_year(r.entry_date, day), "etf") for r in etf_rows]
    types = [t for t in (_lot_type(p) for p in lots) if t]
    warnings = (MIXED_TYPES_WARNING,) if len(set(types)) > 1 else ()
    return HoldingRead(tuple(rows), types[0] if types else None, bool(etf_rows), False, warnings)


def holding_from_read(read: HoldingRead) -> Holding:
    """Tree input for any read, including the all-empty one from a missing positions file."""
    return holding_state([r.shares for r in read.rows], [r.ltcg_reached for r in read.rows])


def portfolio_value() -> float | None:
    try:
        return positions.read_portfolio_value()
    except (FileNotFoundError, ValueError):
        return None
