"""Gateway-backed expirations, option chains and spot for the IBKR options layer.

Read-only: never places or cancels orders (market-data subscriptions are
cancelled). Gateway failures propagate as ibkr_session.GatewayUnavailable;
per-ticker problems are plain DataError.

Live facts (paper gateway, market-data type 4, market closed):
  * reqTickers returns IMMEDIATELY with nan quotes and modelGreeks=None (error
    10091 wakes it early), so it is never used. We subscribe with
    reqMktData(contract, "", False, False) and poll: `close` appears after
    ~2 s, bid/ask and modelGreeks (delta, impliedVol, undPrice) after ~4 s and
    then stay. Up to two batches of <= 20 contracts (<= 40 lines) are polled together every
    POLL_SECONDS until every ticker has usable greeks or GREEKS_TIMEOUT_SECONDS elapse; all
    subscriptions are cancelled in a finally.
  * reqHistoricalData(stock, '', '2 D', '1 day', 'TRADES', useRTH=True) returns
    daily bars (date objects); the last bar is the last session, so it is the
    primary snapshot-day source.
  * Stock ticker.time is the wall-clock moment of the request, NOT the quote's
    time (wrong day after hours), so it is only a fallback behind the bar.
  * Stock close matches the last historical bar close.
Snapshot day order: historical last bar, then ticker.time (New York date).
"""
from __future__ import annotations

import asyncio
import functools
from datetime import date, datetime
from zoneinfo import ZoneInfo

import ibkr_chain as chain_lib
import ibkr_session as sess
from spread_data import DataError

BATCH = 20
CONCURRENT_BATCHES = 2  # 40 active lines, well under the 100-line market-data limit
WINDOW_PCT = 0.15
WINDOW_LIMIT = 40
POLL_SECONDS = 0.5
GREEKS_TIMEOUT_SECONDS = 10
PROBE_TRIES = 5
# 10091 and 354 are non-fatal and simply ignored here; ibkr_session's log filter also
# hides their (and 200/10167/300) ib_async log lines. Only errorEvent sees every code.
COMPETING_ERROR = 10197
NY = ZoneInfo("America/New_York")
_DROP_ERRORS = (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError, RuntimeError)

_memo: dict = {"stock": {}, "chain": {}, "spot": {}, "competing": False}
_call = {"depth": 0}
_hooked: list = []  # ib objects that already carry our error handler


def reset_memo() -> None:
    _memo.update(stock={}, chain={}, spot={}, competing=False)


def _on_error(req_id, code, *rest) -> None:
    if code == COMPETING_ERROR:
        _memo["competing"] = True


def _ib():
    ib = sess.get_ib()
    if not any(h is ib for h in _hooked):
        event = getattr(ib, "errorEvent", None)
        if event is not None:
            event += _on_error
        _hooked.append(ib)
    return ib


def _alive(ib) -> bool:
    try:
        return bool(ib.isConnected())
    except Exception:
        return False


def _guarded(fn):
    """Map mid-call gateway loss to GatewayUnavailable; reset per-request state."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ib = _ib()
        if _call["depth"] == 0:
            _memo["competing"] = False
        _call["depth"] += 1
        try:
            return fn(*args, **kwargs)
        except sess.GatewayUnavailable:
            raise
        except DataError as exc:
            if _alive(ib):
                raise
            raise sess.GatewayUnavailable(sess.DROPPED_HINT) from exc
        except Exception as exc:
            if isinstance(exc, _DROP_ERRORS) or not _alive(ib):
                raise sess.GatewayUnavailable(sess.DROPPED_HINT) from exc
            raise
        finally:
            _call["depth"] -= 1
    return wrapper


def _to_ibkr(expiry_iso: str) -> str:
    return expiry_iso.replace("-", "")


def _to_iso(expiry: str) -> str:
    return f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"


def _stock(symbol: str):
    if symbol not in _memo["stock"]:
        from ib_async import Stock  # lazy
        contract = Stock(symbol, "SMART", "USD")
        _ib().qualifyContracts(contract)
        if not getattr(contract, "conId", 0):
            raise DataError(f"{symbol} 在 IBKR 找不到这只股票")
        _memo["stock"][symbol] = contract
    return _memo["stock"][symbol]


def _chain(symbol: str):
    if symbol not in _memo["chain"]:
        stock = _stock(symbol)
        chains = _ib().reqSecDefOptParams(stock.symbol, "", "STK", stock.conId)
        chosen = chain_lib.select_chain(chains or [], symbol)
        if chosen is None:
            raise DataError(f"{symbol} 没有期权")
        _memo["chain"][symbol] = chosen
    return _memo["chain"][symbol]


@_guarded
def expirations(symbol: str) -> list[str]:
    """ISO expiry dates (ascending) of the selected standard chain."""
    return sorted(_to_iso(e) for e in _chain(symbol).expirations)


@_guarded
def chain_strikes(symbol: str) -> list[float]:
    """Strikes of the selected chain (sorted, chain-wide: the union over all expiries); no quotes."""
    return sorted(float(k) for k in _chain(symbol).strikes)


def _greeks_done(ticker) -> bool:
    greeks = getattr(ticker, "modelGreeks", None)
    return greeks is not None and chain_lib.usable(getattr(greeks, "impliedVol", None))


def _close_done(ticker) -> bool:
    return chain_lib.usable(getattr(ticker, "close", None))


def _stream(ib, contracts, done, extract) -> list:
    """Subscribe, poll until every ticker is done or timeout, extract, always cancel."""
    subscribed, tickers = [], []
    try:
        for contract in contracts:
            tickers.append(ib.reqMktData(contract, "", False, False))
            subscribed.append(contract)
        waited = 0.0
        while waited < GREEKS_TIMEOUT_SECONDS and not all(done(t) for t in tickers):
            ib.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            if not _alive(ib):
                raise sess.GatewayUnavailable(sess.DROPPED_HINT)
        return [extract(t) for t in tickers]
    finally:
        for contract in subscribed:
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass


def _stock_close(symbol: str):
    ib = _ib()
    extract = lambda t: (getattr(t, "close", None), getattr(t, "time", None))  # noqa: E731
    return _stream(ib, [_stock(symbol)], _close_done, extract)[0]


def _qualified(ib, contracts) -> list:
    ib.qualifyContracts(*contracts)
    return [c for c in contracts if getattr(c, "conId", 0)]


def _row_and_quote(ticker):
    return chain_lib.ticker_to_row(ticker), _has_quote(ticker)


def _probe_und_price(symbol: str, close):
    """undPrice of an ATM probe option on the nearest expiry, or None."""
    from ib_async import Option  # lazy
    chosen = _chain(symbol)
    today = date.today().strftime("%Y%m%d")
    upcoming = sorted(e for e in chosen.expirations if e >= today)
    strikes = sorted(chosen.strikes)
    if not upcoming or not strikes:
        return None
    ref = float(close) if chain_lib.usable(close) else strikes[len(strikes) // 2]
    nearest = sorted(strikes, key=lambda s: abs(s - ref))[:PROBE_TRIES]
    ib = _ib()
    for strike in nearest:
        probe = Option(symbol, upcoming[0], strike, "P", "SMART", "100", "USD")
        if _qualified(ib, [probe]):
            row, _ = _stream(ib, [probe], _greeks_done, _row_and_quote)[0]
            return row["und_price"] if row else None
    return None


def _bar_date(bar):
    raw = getattr(bar, "date", None)
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw)[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _snapshot_date(symbol: str, ticker_time):
    bars = _ib().reqHistoricalData(_stock(symbol), "", "2 D", "1 day", "TRADES", useRTH=True)
    for bar in reversed(list(bars or [])):
        found = _bar_date(bar)
        if found:
            return found
    if isinstance(ticker_time, datetime):
        aware = ticker_time if ticker_time.tzinfo else ticker_time.replace(tzinfo=ZoneInfo("UTC"))
        return aware.astimezone(NY).date()
    raise DataError(f"取不到交易日：{symbol} 没有历史日线，行情也没有时间戳")


@_guarded
def last_close(symbol: str) -> tuple[date, float]:
    """(snapshot trading day, spot) for symbol; spot prefers modelGreeks.undPrice."""
    if symbol not in _memo["spot"]:
        close, ticker_time = _stock_close(symbol)
        und = _probe_und_price(symbol, close)
        spot = chain_lib.spot_from_rows([{"und_price": und}], close)
        if spot is None:
            raise DataError(f"取不到 {symbol} 的现价")
        _memo["spot"][symbol] = (_snapshot_date(symbol, ticker_time), spot)
    return _memo["spot"][symbol]


def _no_quotes_error(symbol: str, expiry: str) -> DataError:
    hint = (sess.COMPETING_SESSION_HINT if _memo["competing"]
            else "可能休市且没有冻结数据/没有行情权限")
    return DataError(f"没有拿到 {symbol} {expiry} 的期权报价（{hint}）")


def _has_quote(ticker) -> bool:
    return any(chain_lib.usable(getattr(ticker, f, None)) for f in ("bid", "ask", "last", "close"))


def _collect_rows(ib, contracts) -> tuple[list[dict], int]:
    """Rows for all contracts and the count of tickers that had any quote.

    Up to CONCURRENT_BATCHES batches of BATCH lines share one polling round, so a
    40-strike window costs one wait instead of two."""
    rows: list[dict] = []
    quoted = 0
    for group in chain_lib.batched(contracts, BATCH * CONCURRENT_BATCHES):
        for row, has_quote in _stream(ib, group, _greeks_done, _row_and_quote):
            quoted += has_quote
            if row is not None:
                rows.append(row)
    return rows, quoted


@_guarded
def chain_rows(symbol: str, expiry: str, right: str, strikes=(),
               window_pct: float | None = None, window_limit: int | None = None) -> list[dict]:
    """Quote rows for the OTM strike window of one expiry/side, plus any listed
    `strikes` requested explicitly (e.g. an ITM leg of an open position).

    `window_pct` / `window_limit` default (None) to WINDOW_PCT / WINDOW_LIMIT. A limit
    above BATCH * CONCURRENT_BATCHES still never has more than that many lines
    subscribed at once; _collect_rows just needs an extra polling round per 40.

    Priced rows without greeks are kept (iv / delta None)."""
    from ib_async import Option  # lazy
    _, spot = last_close(symbol)
    listed = _chain(symbol).strikes
    window = chain_lib.strike_window(
        listed, spot, right,
        pct=WINDOW_PCT if window_pct is None else window_pct,
        limit=WINDOW_LIMIT if window_limit is None else window_limit)
    extra = set(strikes) & set(listed)
    wanted_strikes = sorted(set(window) | extra)
    if not wanted_strikes:
        raise DataError(f"{symbol} {expiry} 在现价 {spot:g} 附近没有可用行权价")
    ib = _ib()
    wanted = [Option(symbol, _to_ibkr(expiry), s, right, "SMART", "100", "USD")
              for s in wanted_strikes]
    rows, _ = _collect_rows(ib, _qualified(ib, wanted))
    if rows:
        return rows
    raise _no_quotes_error(symbol, expiry)
