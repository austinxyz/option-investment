#!/usr/bin/env python3
"""Market-data access for the paper-trading simulator.

Order-facing functions (`expirations`, `chain_strikes`, `fetch_puts`, `fetch_calls`,
`last_close`) dispatch at call time on `option_settings.data_source()`: IBKR via `ibkr_data`
(imported lazily; no fallback between sources), Yahoo / demo via the Yahoo layer. The
calendar/history helpers (moving averages, earnings, ex-dividend, month return, close_on,
monday gap) use the yfinance-shaped module from `option_settings.yf_module()`.

Pure helpers (`pick_expiry`, `classify_earnings`, `gap_pct`) are unit-tested;
the thin network wrappers are checked by a manual smoke test only.
Every network failure surfaces as DataError so callers never write a partial ledger.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta

import option_settings

MIN_DAYS_TO_EXPIRY = 7


class DataError(Exception):
    """Market data could not be fetched or is unusable."""


class DemoDataRefused(DataError):
    """The demo market has no answer (a real ticker, a date before its epoch): not a failure."""


def fetch_failure_reason(exc: Exception) -> str:
    """Reject reason for an unexpected fetch failure; the demo's own refusal reads plainly."""
    if isinstance(exc, DemoDataRefused):
        return f"取行情失败：{exc}"
    return f"取行情失败：{type(exc).__name__}: {exc}"


class SourceUnavailable(DataError):
    """The configured data source cannot be reached (or dropped mid-run): abort the whole run."""


# --- pure helpers ---------------------------------------------------------

def pick_expiry(expirations: list[str], snapshot: date,
                min_days: int = MIN_DAYS_TO_EXPIRY) -> str | None:
    """First listed expiry at least `min_days` after the snapshot date."""
    for exp in sorted(expirations):
        if (date.fromisoformat(exp) - snapshot).days >= min_days:
            return exp
    return None


def classify_earnings(dates: list[date], snapshot: date, expiry: date) -> str:
    """"yes" if an earnings date falls in (snapshot, expiry]; "no" if the next
    known date is later; "unknown" when there is no usable future date."""
    future = sorted(d for d in dates if d > snapshot)
    if not future:
        return "unknown"
    return "yes" if future[0] <= expiry else "no"


def gap_pct(prev_close: float, next_open: float) -> float:
    return round((next_open / prev_close - 1.0) * 100.0, 2)


def is_market_open(now_et: datetime) -> bool:
    """US regular session, Eastern wall-clock time: weekdays 09:30 <= t < 16:00. Holidays are
    not modelled; callers also compare the last bar's date with today, which covers them."""
    if now_et.weekday() >= 5:
        return False
    return time(9, 30) <= now_et.time() < time(16, 0)


def market_open_now() -> bool:
    if option_settings.data_source() == "demo":
        return False  # the demo market has no sessions, and must not look at the real clock
    from zoneinfo import ZoneInfo
    return is_market_open(datetime.now(ZoneInfo("America/New_York")))


# --- network wrappers -----------------------------------------------------

def _ticker(symbol: str):
    return option_settings.yf_module().Ticker(symbol.strip().upper())


def _ibkr_layer():
    import ibkr_data  # lazy: ibkr_session imports DataError from this module
    return ibkr_data


def _yahoo_layer():
    import yahoo_data  # lazy: yahoo_data imports DataError / SourceUnavailable from here
    return yahoo_data


def _layer():
    # Resolved on every call so a changed setting (or a test's monkeypatch) takes effect.
    return _ibkr_layer() if option_settings.is_ibkr() else _yahoo_layer()


def ensure_source() -> None:
    """Probe the configured source once before anything is written; failure is SourceUnavailable."""
    if option_settings.is_ibkr():
        import ibkr_session
        # Attribute looked up now, not bound at import: tests monkeypatch ensure_gateway.
        ibkr_session.ensure_gateway()
    else:
        _yahoo_layer().ensure_available()


def stale_drop_count(symbol: str, expiry: str) -> int:
    """Window rows the source dropped as stale last-trade quotes; IBKR never drops any."""
    if option_settings.is_ibkr():
        return 0
    return _yahoo_layer().stale_drop_count(symbol, expiry)


def _usable(x) -> bool:
    """True for a finite, positive number (NaN / 0 / None are not usable prices)."""
    try:
        return x is not None and float(x) == float(x) and float(x) > 0 and float(x) != float("inf")
    except (TypeError, ValueError):
        return False


def last_close(symbol: str) -> tuple[date, float]:
    """Latest close (date, price) from the configured source; errors propagate, no fallback."""
    return _layer().last_close(symbol)


def moving_averages(symbol: str) -> dict:
    """Last close with its 50- and 200-day simple moving averages (unadjusted closes)."""
    try:
        hist = _ticker(symbol).history(period="1y", auto_adjust=False)
    except Exception as exc:
        raise DataError(f"取 {symbol} 行情失败：{exc}") from exc
    if hist is None or hist.empty:
        raise DataError(f"{symbol} 没有行情数据（代码是否正确？）")
    closes = hist["Close"].dropna()
    if len(closes) < 200:
        raise DataError(f"{symbol} 历史行情不足 200 个交易日，算不出 MA200")
    return {"snapshot_date": closes.index[-1].date(), "close": float(closes.iloc[-1]),
            "ma50": float(closes.iloc[-50:].mean()), "ma200": float(closes.iloc[-200:].mean())}


def expirations(symbol: str) -> list[str]:
    """Listed option expiries (ISO) from the configured source; errors propagate, no fallback."""
    return _layer().expirations(symbol)


def chain_strikes(symbol: str) -> list[float]:
    """Chain-wide strikes (the union over expiries, not one expiry's grid)."""
    return _layer().chain_strikes(symbol)


def _chain_rows(symbol: str, expiry: str, right: str, strikes, window_pct, window_limit) -> list[dict]:
    layer = _layer()
    # Omitted window args are not forwarded, so default calls keep the exact old shape.
    extra = {k: v for k, v in (("window_pct", window_pct), ("window_limit", window_limit))
             if v is not None}
    return layer.chain_rows(symbol, expiry, right, strikes=strikes, **extra)


def fetch_puts(symbol: str, expiry: str, strikes=(), window_pct: float | None = None,
               window_limit: int | None = None) -> list[dict]:
    """Put chain rows from the configured source (IBKR rows carry the broker delta)."""
    return _chain_rows(symbol, expiry, "P", strikes, window_pct, window_limit)


def fetch_calls(symbol: str, expiry: str, strikes=(), window_pct: float | None = None,
                window_limit: int | None = None) -> list[dict]:
    """Call chain rows from the configured source (IBKR rows carry the broker delta)."""
    return _chain_rows(symbol, expiry, "C", strikes, window_pct, window_limit)


def ex_dividend_in_window(symbol: str, snapshot: date, expiry: date) -> date | None:
    """Ex-dividend date within (snapshot, expiry], else None (also on any error / missing key)."""
    try:
        raw = _ticker(symbol).calendar.get("Ex-Dividend Date")
        ex = raw.date() if isinstance(raw, datetime) else raw
    except Exception:
        return None
    if isinstance(ex, date) and snapshot < ex <= expiry:
        return ex
    return None


def month_return_pct(symbol: str) -> float | None:
    """Return over the last ~21 trading days in percent; None when history is too short."""
    try:
        hist = _ticker(symbol).history(period="3mo", auto_adjust=False)
        closes = hist["Close"].dropna()
    except Exception:
        return None
    if len(closes) < 22 or not _usable(closes.iloc[-22]) or not _usable(closes.iloc[-1]):
        return None
    return (float(closes.iloc[-1]) / float(closes.iloc[-22]) - 1.0) * 100.0


def earnings_status(symbol: str, snapshot: date, expiry: date) -> str:
    try:
        cal = _ticker(symbol).calendar
        raw = cal.get("Earnings Date", []) if isinstance(cal, dict) else []
        dates = [d if isinstance(d, date) else d.date() for d in raw]
    except Exception:
        dates = []
    return classify_earnings(dates, snapshot, expiry)


def close_on(symbol: str, day: date) -> float | None:
    """Close on `day`, or None if there was no trading session that day / no data yet."""
    try:
        hist = _ticker(symbol).history(start=day.isoformat(),
                                       end=(day + timedelta(days=1)).isoformat(),
                                       auto_adjust=False)
    except Exception as exc:
        raise DataError(f"取 {symbol} {day} 收盘价失败：{exc}") from exc
    if hist is None or hist.empty or hist.index[0].date() != day:
        return None
    close = hist["Close"].iloc[0]
    return float(close) if _usable(close) else None


def monday_gap_pct(symbol: str, snapshot: date, snapshot_close: float) -> float | None:
    """Open of the first session after the snapshot vs the snapshot close, in %."""
    try:
        hist = _ticker(symbol).history(start=(snapshot + timedelta(days=1)).isoformat(),
                                       end=(snapshot + timedelta(days=8)).isoformat(),
                                       auto_adjust=False)
    except Exception as exc:
        raise DataError(f"取 {symbol} 周一开盘价失败：{exc}") from exc
    if hist is None or hist.empty:
        return None
    opens = hist["Open"].dropna()
    if opens.empty or not _usable(opens.iloc[0]):
        return None
    return gap_pct(snapshot_close, float(opens.iloc[0]))
