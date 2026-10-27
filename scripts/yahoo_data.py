"""Yahoo Finance provider for the option tools (design 8.3-8.5): same row shape as the IBKR layer.

yfinance is reached ONLY through `option_settings.yf_module()` (tests inject a fake; demo mode
will inject its own market). Rows look exactly like `ibkr_chain.ticker_to_row` rows
(`strike, bid, ask, last, iv, delta, und_price, close`) with `delta / und_price / close = None`
(spread_core.delta_for falls back to local Black-Scholes), plus `last_trade_date` (a New York
date) when Yahoo reports one. Strikes are always float.

Stale quotes: when bid / ask cannot be used as a pair (Yahoo serves 0 after the close) the row
keeps only `last`, so spread_core marks it stale. A stale *window* row whose last trade is older
than the snapshot trading day is dropped and counted (`stale_drop_count`); explicitly requested
strikes (open legs) are never dropped.

Failures: every network call is throttled, rate limits back off once, and `_classify` maps each
exception to SourceUnavailable (abort the run), DataError (reject one ticker) or `_Unclassified`
(a DataError, until YAHOO_MAX_UNCLASSIFIED_STREAK different tickers fail that way in a row).
`_clock` / `_sleep` are module attributes so tests replace them (no real sleeping).
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ibkr_chain
import option_settings
import spread_core
import spread_data
from spread_data import DataError, SourceUnavailable

# Yahoo throttles bursts from one IP; 0.4 s between requests keeps a 30-ticker put-screen scan
# (~3 requests each) to ~40 s of extra waiting, which the design accepts (8.4).
YAHOO_MIN_INTERVAL_SECONDS = 0.4
# A rate-limit answer usually clears within seconds; one 5 s back-off then a single retry. A
# second refusal means Yahoo is refusing us for this run, so the whole run aborts.
YAHOO_BACKOFF_SECONDS = 5
# One odd exception is one bad ticker; the same unknown failure on 3 different tickers in a
# row means Yahoo changed its interface, and a summary made only of rejects would mislead.
YAHOO_MAX_UNCLASSIFIED_STREAK = 3
# chain_strikes has no cheap chain-wide API on Yahoo: union the strikes of the expiries a
# strategy can use (within 60 days), capped at 6 chains to bound the requests per ticker.
CHAIN_STRIKES_HORIZON_DAYS = 60
CHAIN_STRIKES_MAX_EXPIRIES = 6
# A liquid symbol whose daily bars always exist: if it answers, Yahoo is reachable.
PROBE_SYMBOL = "SPY"
PROBE_PERIOD = "5d"
# The demo market only knows its invented tickers, so its probe uses one of those.
DEMO_ERROR_NAME = "DemoDataError"
# 7 calendar days always contain at least one completed session, even over a long weekend.
CLOSE_PERIOD = "7d"
NY = ZoneInfo("America/New_York")

UNREACHABLE = "连不上 Yahoo Finance：{}"
PROBE_HINT = "（请检查网络或代理设置后重试）"
RATE_LIMITED = "被 Yahoo 限流，请稍后再试（{}）"
NO_OPTIONS = "{symbol} 没有期权"
NO_QUOTES = "没有拿到 {symbol} {expiry} 的期权报价（Yahoo 休市后常没有买卖报价）"
NO_STRIKES = "{symbol} {expiry} 在现价 {spot:g} 附近没有可用行权价"
UNKNOWN_STREAK = "连续 {n} 只标的出现无法识别的错误（Yahoo 可能改版）：{exc}"

# Class names are matched along the MRO (yfinance / requests / curl_cffi class paths move
# between versions); the message patterns catch the same failures when only text is left.
RATE_LIMIT_NAMES = frozenset({"YFRateLimitError"})
RATE_LIMIT_PATTERN = re.compile(r"too many requests|rate limit|\b429\b", re.I)
NETWORK_NAMES = frozenset({"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout",
                           "TimeoutError", "SSLError", "ProxyError", "ChunkedEncodingError"})
NETWORK_PATTERN = re.compile(
    r"connection|timed out|timeout|failed to perform|could not resolve|name resolution"
    r"|max retries", re.I)
DATA_NAMES = frozenset({"YFTickerMissingError", "YFPricesMissingError", "YFTzMissingError",
                        "YFInvalidPeriodError"})
DATA_PATTERN = re.compile(r"delisted|no data found|not found|cannot be found|no options"
                          r"|no timezone", re.I)


class _Unclassified(Exception):
    """Marker returned by _classify for exceptions it cannot attribute."""


_memo: dict = {"module": None, "ticker": {}, "options": {}, "chain": {}, "close": {},
               "strikes": {}, "stale": {}}
_state: dict = {"last_request": None, "unclassified": ()}
_clock = time.monotonic
_sleep = time.sleep


def _empty_caches() -> dict:
    return {"ticker": {}, "options": {}, "chain": {}, "close": {}, "strikes": {}, "stale": {}}


def reset_memo() -> None:
    """Forget every cached answer, the throttle timestamp and the unclassified streak."""
    _memo.update(module=None, **_empty_caches())
    _state.update(last_request=None, unclassified=())


def _module():
    """The yfinance-shaped module; a different module (fake, demo) invalidates the caches."""
    yf = option_settings.yf_module()
    if _memo["module"] is not yf:
        _memo.update(module=yf, **_empty_caches())
    return yf


def _key(symbol: str) -> str:
    return symbol.strip().upper()


def _ticker(key: str):
    yf = _module()
    if key not in _memo["ticker"]:
        _memo["ticker"][key] = yf.Ticker(key)
    return _memo["ticker"][key]


# --- error classification ------------------------------------------------------

def _names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _is_rate_limited(exc: BaseException) -> bool:
    return bool(_names(exc) & RATE_LIMIT_NAMES) or bool(RATE_LIMIT_PATTERN.search(str(exc)))


def _is_network(exc: BaseException) -> bool:
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return True
    return bool(_names(exc) & NETWORK_NAMES) or bool(NETWORK_PATTERN.search(str(exc)))


def _is_data(exc: BaseException) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return False  # a reply we cannot parse is Yahoo changing shape, not one bad ticker
    if isinstance(exc, (DataError, LookupError, ValueError)):
        return True
    return bool(_names(exc) & DATA_NAMES) or bool(DATA_PATTERN.search(str(exc)))


def _classify(exc: BaseException) -> type[Exception]:
    """SourceUnavailable (rate limit, connection, timeout), DataError (this ticker's data is
    missing or bad) or _Unclassified (anything else)."""
    if isinstance(exc, SourceUnavailable) or _is_rate_limited(exc) or _is_network(exc):
        return SourceUnavailable
    if _is_data(exc):
        return DataError
    return _Unclassified


def _translate(key: str, what: str, exc: Exception) -> DataError:
    if DEMO_ERROR_NAME in _names(exc):
        # Not an outage and not a Yahoo failure: the demo market says why it has no answer
        # (a real ticker, or a date before its epoch). Matched by name: no demo import here.
        return spread_data.DemoDataRefused(str(exc))
    kind = _classify(exc)
    if kind is SourceUnavailable:
        return exc if isinstance(exc, SourceUnavailable) else SourceUnavailable(
            UNREACHABLE.format(exc))
    if kind is _Unclassified:
        streak = _state["unclassified"]
        _state["unclassified"] = streak if key in streak else streak + (key,)
        count = len(_state["unclassified"])
        if count >= YAHOO_MAX_UNCLASSIFIED_STREAK:
            return SourceUnavailable(UNREACHABLE.format(UNKNOWN_STREAK.format(n=count, exc=exc)))
    return exc if isinstance(exc, DataError) else DataError(f"取 {key} {what}失败：{exc}")


# --- throttled requests --------------------------------------------------------

def _throttle() -> None:
    if option_settings.data_source() == "demo":
        return  # nothing is requested from anyone: waiting would only slow the demo down
    last = _state["last_request"]
    if last is not None:
        wait = last + YAHOO_MIN_INTERVAL_SECONDS - _clock()
        if wait > 0:
            _sleep(wait)
    _state["last_request"] = _clock()


def _attempt(fetch):
    _throttle()
    result = fetch()
    _state["unclassified"] = ()
    return result


def _request(key: str, what: str, fetch):
    """One network call: throttled; a rate limit backs off once and retries once."""
    try:
        return _attempt(fetch)
    except Exception as exc:  # yfinance raises many unrelated types; _classify sorts them
        if not _is_rate_limited(exc):
            raise _translate(key, what, exc) from exc
    _sleep(YAHOO_BACKOFF_SECONDS)
    try:
        return _attempt(fetch)
    except Exception as exc:
        if _is_rate_limited(exc):
            raise SourceUnavailable(UNREACHABLE.format(RATE_LIMITED.format(exc))) from exc
        raise _translate(key, what, exc) from exc


# --- probe, expirations, last close --------------------------------------------

def _probe_symbol() -> str:
    if option_settings.data_source() == "demo":
        import demo_market
        return demo_market.DEMO_TICKERS[0]
    return PROBE_SYMBOL


def ensure_available() -> None:
    """Probe Yahoo (never cached) before anything is written; any failure is SourceUnavailable."""
    _module()
    probe = _probe_symbol()
    try:
        hist = _request(probe, "探测行情", lambda: _ticker(probe).history(period=PROBE_PERIOD))
    except SourceUnavailable as exc:
        if exc.__cause__ is not None and _is_rate_limited(exc.__cause__):
            raise  # already says to wait; a network hint would mislead
        raise SourceUnavailable(f"{exc}{PROBE_HINT}") from exc
    except DataError as exc:
        raise SourceUnavailable(UNREACHABLE.format(f"{exc}{PROBE_HINT}")) from exc
    if hist is None or hist.empty:
        raise SourceUnavailable(UNREACHABLE.format(f"{probe} 探测没有返回行情{PROBE_HINT}"))


def _empty_answer(err: DataError) -> DataError:
    """An empty reply is a bad symbol only if Yahoo still answers the probe: by default
    yfinance swallows connection errors and returns empty data, so an outage mid-run would
    otherwise turn every remaining ticker into a reject. Raises SourceUnavailable on outage."""
    ensure_available()
    return err


def expirations(symbol: str) -> list[str]:
    """Listed expiries (ISO, ascending); none listed is DataError '{T} 没有期权'."""
    _module()
    key = _key(symbol)
    if key not in _memo["options"]:
        raw = _request(key, "期权到期日", lambda: tuple(_ticker(key).options))
        if not raw:
            raise _empty_answer(DataError(NO_OPTIONS.format(symbol=key)))
        _memo["options"][key] = tuple(sorted(str(e) for e in raw))
    listed = _memo["options"][key]
    return list(listed)


def _minute_close_on(key: str, day: date) -> float | None:
    """Last usable 1-minute close dated `day`, or None (used when the daily bar is still NaN)."""
    try:
        mins = _request(key, "分钟线", lambda: _ticker(key).history(
            period="1d", interval="1m", auto_adjust=False))
    except SourceUnavailable:
        raise
    except DataError:
        return None
    if mins is None or mins.empty:
        return None
    for ts, close in reversed(list(zip(mins.index, mins["Close"]))):
        if ts.date() == day and ibkr_chain.usable(close):
            return float(close)
    return None


def _fetch_last_close(key: str) -> tuple[date, float]:
    hist = _request(key, "行情", lambda: _ticker(key).history(period=CLOSE_PERIOD,
                                                             auto_adjust=False))
    if hist is None or hist.empty:
        raise _empty_answer(DataError(f"{key} 没有行情数据（代码是否正确？）"))
    bars = [(ts.date(), c) for ts, c in zip(hist.index, hist["Close"])]
    if ibkr_chain.usable(bars[-1][1]):
        return bars[-1][0], float(bars[-1][1])
    minute = _minute_close_on(key, bars[-1][0])
    if minute is not None:
        return bars[-1][0], minute
    for day, close in reversed(bars[:-1]):
        if ibkr_chain.usable(close):
            return day, float(close)
    raise DataError(f"{key} 取不到有效收盘价（日线和分钟线都是空值）")


def last_close(symbol: str) -> tuple[date, float]:
    """(snapshot trading day, close). Right after the close Yahoo can serve today's daily bar
    with Close=NaN: fall back to that day's last 1-minute close, then to the previous valid
    daily bar (with its own date). Never returns NaN."""
    _module()
    key = _key(symbol)
    if key not in _memo["close"]:
        _memo["close"][key] = _fetch_last_close(key)
    return _memo["close"][key]


# --- option chains -------------------------------------------------------------

def _chain(key: str, expiry: str) -> tuple:
    """(puts, calls) frames for one expiry, fetched once per process."""
    if (key, expiry) not in _memo["chain"]:
        chain = _request(key, f"{expiry} 期权链", lambda: _ticker(key).option_chain(expiry))
        _memo["chain"][(key, expiry)] = (chain.puts, chain.calls)
    return _memo["chain"][(key, expiry)]


def _records(frame) -> list[dict]:
    return [] if frame is None or frame.empty else frame.to_dict("records")


def _finite(x) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and v not in (float("inf"), float("-inf"))


def _horizon_expiries(listed: list[str], snapshot: date) -> list[str]:
    """Expiries within CHAIN_STRIKES_HORIZON_DAYS (at most CHAIN_STRIKES_MAX_EXPIRIES); when
    none is that near, the first upcoming one so the union is never empty by construction."""
    upcoming = [e for e in listed if date.fromisoformat(e) >= snapshot]
    horizon = snapshot + timedelta(days=CHAIN_STRIKES_HORIZON_DAYS)
    near = [e for e in upcoming if date.fromisoformat(e) <= horizon]
    return (near or upcoming[:1])[:CHAIN_STRIKES_MAX_EXPIRIES]


def chain_strikes(symbol: str) -> list[float]:
    """Union of put and call strikes over the near expiries (a proxy for the chain-wide grid);
    the chains fetched here are cached and reused by chain_rows."""
    _module()
    key = _key(symbol)
    if key not in _memo["strikes"]:
        snapshot, _ = last_close(key)
        union: set[float] = set()
        for expiry in _horizon_expiries(expirations(key), snapshot):
            for frame in _chain(key, expiry):
                union |= {float(r["strike"]) for r in _records(frame) if _finite(r.get("strike"))}
        _memo["strikes"][key] = tuple(sorted(union))
    return list(_memo["strikes"][key])


def _by_strike(records: list[dict]) -> dict[float, dict]:
    """float strike -> record (first listing wins)."""
    out: dict[float, dict] = {}
    for rec in records:
        if _finite(rec.get("strike")):
            out.setdefault(float(rec["strike"]), rec)
    return out


def _clean(x) -> float | None:
    return float(x) if ibkr_chain.usable(x) else None


def _sane_iv(x) -> float | None:
    """Yahoo's impliedVolatility outside spread_core's usable band (0.01-5.0) is missing."""
    iv = _clean(x)
    return iv if iv is not None and spread_core.IV_MIN <= iv <= spread_core.IV_MAX else None


def _ny_day(value) -> date | None:
    """lastTradeDate (UTC timestamp; naive is taken as UTC) as a New York calendar date."""
    if isinstance(value, datetime):
        if value != value:  # NaT
            return None
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(NY).date()
    return value if isinstance(value, date) else None


def _to_row(strike: float, rec: dict) -> dict | None:
    """An IBKR-shaped row; None when it has neither a usable price nor a usable iv."""
    bid, ask, last = _clean(rec.get("bid")), _clean(rec.get("ask")), _clean(rec.get("lastPrice"))
    if bid is None or ask is None or ask < bid:
        bid = ask = None  # not a usable pair: only the last trade is left (stale)
    iv = _sane_iv(rec.get("impliedVolatility"))
    if bid is None and last is None and iv is None:
        return None
    row = {"strike": strike, "bid": bid, "ask": ask, "last": last, "iv": iv,
           "delta": None, "und_price": None, "close": None}
    traded = _ny_day(rec.get("lastTradeDate"))
    if traded is not None:
        row["last_trade_date"] = traded
    return row


def _outdated_last_only(row: dict, snapshot: date) -> bool:
    """Priced only by a last trade from before the snapshot day (or of unknown age)."""
    if row["bid"] is not None or row["last"] is None:
        return False
    traded = row.get("last_trade_date")
    return traded is None or traded < snapshot


def _select_rows(records: dict[float, dict], wanted: list[float], explicit: set[float],
                 snapshot: date) -> tuple[list[dict], int]:
    rows, dropped = [], 0
    for strike in wanted:
        row = _to_row(strike, records[strike])
        if row is None:
            continue
        if strike not in explicit and _outdated_last_only(row, snapshot):
            dropped += 1
            continue
        rows.append(row)
    return rows, dropped


def _window(listed: list[float], spot: float, right: str, window_pct, window_limit) -> list[float]:
    # Omitted args fall through to strike_window's own defaults (15%, 40): the IBKR window.
    extra = {k: v for k, v in (("pct", window_pct), ("limit", window_limit)) if v is not None}
    return ibkr_chain.strike_window(listed, spot, right, **extra)


def chain_rows(symbol: str, expiry: str, right: str, strikes=(),
               window_pct: float | None = None, window_limit: int | None = None) -> list[dict]:
    """Rows for the OTM window of one expiry / side plus explicitly requested listed strikes.

    `window_pct=0, window_limit=0` returns only the explicit strikes. Outdated last-only window
    rows are dropped and counted; no priced row left is DataError(NO_QUOTES)."""
    if right not in ("P", "C"):
        raise ValueError(f"right must be 'P' or 'C', got {right!r}")
    _module()
    key = _key(symbol)
    snapshot, spot = last_close(key)
    puts, calls = _chain(key, expiry)
    records = _by_strike(_records(puts if right == "P" else calls))
    if not records:
        raise DataError(NO_QUOTES.format(symbol=key, expiry=expiry))
    explicit = {float(s) for s in strikes} & set(records)
    wanted = sorted(set(_window(sorted(records), spot, right, window_pct, window_limit))
                    | explicit)
    if not wanted:
        raise DataError(NO_STRIKES.format(symbol=key, expiry=expiry, spot=spot))
    rows, dropped = _select_rows(records, wanted, explicit, snapshot)
    _memo["stale"][(key, expiry, right)] = dropped
    if not any(r["bid"] is not None or r["last"] is not None for r in rows):
        raise DataError(NO_QUOTES.format(symbol=key, expiry=expiry))
    return rows


def stale_drop_count(symbol: str, expiry: str) -> int:
    """Window rows dropped as outdated last-only quotes by the latest fetch of each side."""
    key = _key(symbol)
    return sum(n for (s, e, _), n in _memo["stale"].items() if s == key and e == expiry)
