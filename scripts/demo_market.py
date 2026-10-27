"""A deterministic, offline stand-in for yfinance: the `demo` data source (share plan, Task 7).

Why it exists: a friend can try the whole toolkit with no account, no network and no market
hours, and the numbers are the same every time they run it. Everything is fictional: three
invented tickers, closes from a closed-form formula, option prices from Black-Scholes.

The module itself is the yfinance-shaped object (`demo_market.Ticker(sym)`): same surface as
tests/fake_yfinance.py (`.options`, `.option_chain(exp).puts/.calls`, `.history(...)`,
`.calendar`, `.info`). Nothing here sleeps, draws random numbers or reads the clock, except the
single default of `_today` (see below).

Determinism rules:
  * close(n) is a pure function of (ticker, n), n = index of the trading day since DEMO_EPOCH
    (weekends and NYSE holidays have no bar), so any day has data and a past expiry can be
    settled with `history(start, end)`.
  * the option chain is a pure function of (ticker, snapshot day, expiry): Black-Scholes with a
    fixed IV smile; bid / ask sit around the theory price with a fixed relative spread (about
    3% at the money, 6% in the wings, rounding included below 10%) so the demo always yields
    candidates.
  * "today" comes from `set_today` / the `today=` argument (the CLIs and tests know the date),
    else from the environment variable OPTION_DEMO_TODAY=YYYY-MM-DD (reproducible docs and
    screenshots). Only when none is given does `_today` read the wall clock, once per process.
"""
from __future__ import annotations

import math
import os
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from types import SimpleNamespace

import pandas as pd

import spread_core

DEMO_TICKERS = ("ZZZQ", "ZZQB", "ZQIC")
DEMO_EPOCH = date(2025, 1, 2)       # a Thursday and a trading day: bar n = 0. A year and more
                                    # before any realistic run date, so MA200, its 20-day slope
                                    # (220 bars) and "one year" (252) exist from the first day
CURVE_ORIGIN = date(2026, 1, 2)     # the price curves are anchored here (bar index below)
CURVE_ORIGIN_N = 251                # trading days from DEMO_EPOCH to CURVE_ORIGIN
DEMO_RISK_FREE = 0.04
NY_TZ = "America/New_York"
CHAIN_COLUMNS = ("strike", "bid", "ask", "lastPrice", "impliedVolatility", "lastTradeDate",
                 "openInterest")
NOT_FICTIONAL = "演示数据只有虚构标的，{symbol} 不可用；可用的有：{allowed}"
BEFORE_EPOCH = "演示数据从 {epoch} 起，{day} 没有数据"
WEEKLY_EXPIRIES = 10
YEAR_SESSIONS = 253                 # one session more than the 252 the screens ask for
STRIKE_RANGE = 0.40                 # strikes from spot*(1-0.4) to spot*(1+0.4)
MIN_LISTED_MID = 0.02               # cheaper options are not listed
CHEAP_LIMIT = 1.0                   # below this price quotes carry a tenth of a cent: a whole
                                    # cent of rounding would push a cheap wing past the 10% rule
PENNY_LIMIT = 0.10                  # below this, a hundredth of a cent (same reason)
IV_SWING = 0.30                     # ATM IV moves +-30% around its base over a slow cycle, so the
                                    # IV position (IV vs the year of realised vol) differs by date
IV_CYCLE_STEP = 0.031
MONTHLY_HORIZON_DAYS = 190
SPREAD_ATM, SPREAD_WING = 0.03, 0.06
WING_MONEYNESS = 0.15               # |ln(K/S)| at which the spread reaches SPREAD_WING
SMILE_SKEW, SMILE_CURVE = 0.35, 1.0
IV_FLOOR = 0.08
TRADE_TIME = "19:45"                # UTC time stamped on every last trade of the snapshot day
ONE_MINUTE_BARS = "1m"


class DemoDataError(Exception):
    """The demo market has no answer (real ticker, or a date before the demo epoch)."""


@dataclass(frozen=True)
class _Spec:
    base: float          # close on the epoch day
    drift: float         # log growth per trading day
    waves: tuple         # ((amplitude, angular step, phase), ...) added to the log price
    iv: float            # at-the-money implied volatility
    quote_type: str
    market_cap: float
    earnings_day: int    # 0 = an ETF with no earnings; else day of month (Jan / Apr / Jul / Oct)
    dividend_day: int    # 0 = pays none; else ex-dividend day of month (Mar / Jun / Sep / Dec)
    volume: int


# Drift and slow waves are tuned so that on about 95% of days at least one ticker sits above
# its 50- and 200-day averages (the screens' trend gate), and on 2026-10-23 all three do: a
# first-time user always gets candidates.
SPECS = {
    "ZZZQ": _Spec(100.0, 0.0010, ((0.050, 0.23, 0.4), (0.030, 0.047, 1.1), (0.007, 2.1, 2.0)),
                  0.22, "EQUITY", 6.0e10, 14, 0, 4_200_000),
    "ZZQB": _Spec(48.5, 0.0008, ((0.060, 0.19, 2.2), (0.040, 0.039, 0.3), (0.008, 1.9, 0.7)),
                  0.24, "EQUITY", 2.4e10, 21, 14, 2_600_000),
    "ZQIC": _Spec(212.0, 0.0005, ((0.030, 0.21, 1.3), (0.020, 0.043, 2.6), (0.004, 2.3, 1.5)),
                  0.125, "ETF", 8.0e9, 0, 20, 6_800_000),
}


# --- trading calendar ----------------------------------------------------------

def _easter(year: int) -> date:
    """Anonymous Gregorian computus: Good Friday is two days before."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_monday_of_may(year: int) -> date:
    last = date(year, 5, 31)
    return last - timedelta(days=last.weekday())


def _observed(holiday: date) -> date | None:
    """Saturday holidays are observed on Friday, Sunday ones on Monday."""
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=None)
def _holidays(year: int) -> frozenset[date]:
    fixed = [_observed(date(year, 6, 19)), _observed(date(year, 7, 4)),
             _observed(date(year, 12, 25))]
    new_year = date(year, 1, 1)                      # a Saturday New Year is NOT moved back
    if new_year.weekday() != 5:
        fixed.append(_observed(new_year))
    floating = [_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
                _easter(year) - timedelta(days=2), _last_monday_of_may(year),
                _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4)]
    return frozenset(fixed + floating)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _holidays(day.year)


def last_trading_day(day: date) -> date:
    """`day` itself when markets are open, else the session before it."""
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def _sessions(through: date) -> tuple[date, ...]:
    """Every trading day from the epoch to `through` (inclusive)."""
    days, day = [], DEMO_EPOCH
    while day <= through:
        if is_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def _check_known(symbol: str) -> str:
    key = str(symbol).strip().upper()
    if key not in SPECS:
        raise DemoDataError(NOT_FICTIONAL.format(symbol=key, allowed="、".join(DEMO_TICKERS)))
    return key


def _check_epoch(day: date) -> None:
    if day < DEMO_EPOCH:
        raise DemoDataError(BEFORE_EPOCH.format(epoch=DEMO_EPOCH, day=day))


# --- prices --------------------------------------------------------------------

def close_for(symbol: str, n: int) -> float:
    """Close of the n-th trading day since the epoch (n = 0 is DEMO_EPOCH)."""
    spec = SPECS[_check_known(symbol)]
    m = n - CURVE_ORIGIN_N          # negative before the anchor: the curve simply runs backwards
    wobble = sum(a * math.sin(w * m + p) for a, w, p in spec.waves)
    return round(spec.base * math.exp(spec.drift * m + wobble), 2)


def _open_for(symbol: str, n: int) -> float:
    """A small deterministic gap from the previous close."""
    prev = close_for(symbol, max(n - 1, 0))
    return round(prev * (1.0 + 0.004 * math.sin(1.7 * n + len(symbol))), 2)


def _bars(symbol: str, first: date, last: date) -> pd.DataFrame:
    sessions = _sessions(last)
    rows = [(d, n) for n, d in enumerate(sessions) if d >= first]
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(NY_TZ) for d, _ in rows])
    spec = SPECS[symbol]
    return pd.DataFrame({
        "Open": [_open_for(symbol, n) for _, n in rows],
        "Close": [close_for(symbol, n) for _, n in rows],
        "Volume": [int(spec.volume * (1.0 + 0.3 * math.sin(0.9 * n))) for _, n in rows],
    }, index=index)


# --- options -------------------------------------------------------------------

def _strike_step(spot: float) -> float:
    # fine grids keep neighbouring strikes close enough for three distinct delta tiers: one
    # weekly standard deviation is only 2-4% of the price at the demo's volatilities
    for limit, step in ((75, 0.5), (400, 1.0)):
        if spot < limit:
            return step
    return 10.0


def atm_iv(symbol: str, n: int) -> float:
    """At-the-money IV on trading day n: the base level on a slow, fixed cycle."""
    spec = SPECS[symbol]
    return spec.iv * (1.0 + IV_SWING * math.sin(IV_CYCLE_STEP * n + spec.waves[0][2]))


def _iv(symbol: str, spot: float, strike: float, n: int) -> float:
    """Fixed smile: higher volatility for lower strikes (put skew) and both wings."""
    moneyness = math.log(strike / spot)
    smile = -SMILE_SKEW * moneyness + SMILE_CURVE * moneyness * moneyness
    return max(atm_iv(symbol, n) + smile, IV_FLOOR)


def bs_price(right: str, spot: float, strike: float, iv: float, days: float) -> float:
    t = days / 365.0
    vol_t = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + (DEMO_RISK_FREE + iv * iv / 2.0) * t) / vol_t
    d2 = d1 - vol_t
    discounted = strike * math.exp(-DEMO_RISK_FREE * t)
    if right == "P":
        return discounted * spread_core.norm_cdf(-d2) - spot * spread_core.norm_cdf(-d1)
    return spot * spread_core.norm_cdf(d1) - discounted * spread_core.norm_cdf(d2)


def _relative_spread(spot: float, strike: float) -> float:
    reach = min(1.0, abs(math.log(strike / spot)) / WING_MONEYNESS)
    return SPREAD_ATM + (SPREAD_WING - SPREAD_ATM) * reach


def _open_interest(symbol: str, expiry: date, strike: float, right: str) -> int:
    # crc32, not hash(): str hashes change between processes
    seed = zlib.crc32(f"{symbol}|{expiry}|{strike:.2f}|{right}".encode())
    return 50 + seed % 4950


def _third_friday(year: int, month: int) -> date:
    return _nth_weekday(year, month, 4, 3)


def _expiries(snapshot: date) -> list[date]:
    """The next WEEKLY_EXPIRIES Fridays, then monthly (third Friday) expiries out to about six
    months. A holiday Friday moves to Thursday."""
    friday = snapshot + timedelta(days=(4 - snapshot.weekday()) % 7)
    out: list[date] = []
    while len(out) < WEEKLY_EXPIRIES:
        expiry = last_trading_day(friday)  # a holiday Friday can fall back onto the snapshot day
        if expiry > snapshot:
            out.append(expiry)
        friday += timedelta(days=7)
    horizon, year, month = snapshot + timedelta(days=MONTHLY_HORIZON_DAYS), out[-1].year, out[-1].month
    while True:
        monthly = last_trading_day(_third_friday(year, month))
        if monthly > horizon:
            return out
        if monthly > out[-1]:
            out.append(monthly)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _row(symbol: str, right: str, spot: float, strike: float, expiry: date, snapshot: date,
         traded: pd.Timestamp, n: int) -> dict | None:
    iv = _iv(symbol, spot, strike, n)
    theory = bs_price(right, spot, strike, iv, (expiry - snapshot).days)
    if theory < MIN_LISTED_MID:
        return None
    half = theory * _relative_spread(spot, strike) / 2.0
    digits = 4 if theory < PENNY_LIMIT else 3 if theory < CHEAP_LIMIT else 2
    return {"strike": float(strike), "bid": round(theory - half, digits),
            "ask": round(theory + half, digits), "lastPrice": round(theory, digits), "impliedVolatility": round(iv, 4),
            "lastTradeDate": traded, "openInterest": _open_interest(symbol, expiry, strike, right)}


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([r for r in rows if r is not None], columns=list(CHAIN_COLUMNS))


def _strikes(spot: float) -> list[float]:
    step = _strike_step(spot)
    low = math.ceil(spot * (1.0 - STRIKE_RANGE) / step)
    high = math.floor(spot * (1.0 + STRIKE_RANGE) / step)
    return [round(k * step, 2) for k in range(low, high + 1)]


# --- calendar ------------------------------------------------------------------

def _quarter_dates(year: int, months: tuple[int, ...], day: int) -> list[date]:
    out = []
    for month in months:
        d = date(year, month, day)
        while not is_trading_day(d):
            d += timedelta(days=1)
        out.append(d)
    return out


def _next_after(today: date, months: tuple[int, ...], day: int) -> date:
    candidates = [d for y in (today.year, today.year + 1) for d in _quarter_dates(y, months, day)]
    return min(d for d in candidates if d >= today)


# --- the clock edge ------------------------------------------------------------

ENV_TODAY = "OPTION_DEMO_TODAY"      # YYYY-MM-DD: pins "today" so docs and screenshots reproduce
_state: dict = {"today": None, "wall": None}


def set_today(day: date | None) -> None:
    """Fix the date the module answers for (None goes back to the wall clock)."""
    _state["today"] = day


def _today() -> date:
    """set_today() beats OPTION_DEMO_TODAY beats the wall clock, which is read once per process."""
    if _state["today"] is not None:
        return _state["today"]
    override = os.environ.get(ENV_TODAY, "").strip()
    if override:
        try:
            return date.fromisoformat(override)
        except ValueError as exc:
            raise DemoDataError(f"{ENV_TODAY} 要写成 YYYY-MM-DD，当前是 {override!r}") from exc
    if _state["wall"] is None:
        _state["wall"] = datetime.now().date()  # the only wall-clock read, then cached
    return _state["wall"]


def _parse_period(period: str) -> tuple[str, int]:
    digits = "".join(c for c in period if c.isdigit())
    unit = period[len(digits):]
    if not digits or unit not in ("d", "mo", "y"):
        raise DemoDataError(f"演示数据不支持的 period：{period!r}")
    return unit, int(digits)


class Ticker:
    """yfinance.Ticker look-alike. Constructing one with a real symbol fails at once."""

    def __init__(self, symbol: str, today: date | None = None):
        self.ticker = _check_known(symbol)
        self._today_arg = today

    def _now(self) -> date:
        day = self._today_arg or _today()
        _check_epoch(day)
        return day

    def _snapshot(self) -> date:
        return last_trading_day(self._now())

    @property
    def options(self) -> tuple[str, ...]:
        return tuple(e.isoformat() for e in _expiries(self._snapshot()))

    def option_chain(self, expiry: str):
        snapshot = self._snapshot()
        listed = {e.isoformat(): e for e in _expiries(snapshot)}
        if expiry not in listed:
            raise ValueError(f"Expiration `{expiry}` cannot be found.")
        n = _sessions(snapshot).index(snapshot)
        spot = close_for(self.ticker, n)
        traded = pd.Timestamp(f"{snapshot} {TRADE_TIME}", tz="UTC")
        strikes = _strikes(spot)
        make = lambda right: _frame([  # noqa: E731 - one-line local helper
            _row(self.ticker, right, spot, k, listed[expiry], snapshot, traded, n) for k in strikes])
        return SimpleNamespace(puts=make("P"), calls=make("C"))

    def history(self, period: str | None = None, interval: str = "1d", start=None, end=None,
                auto_adjust: bool = True, **_ignored) -> pd.DataFrame:
        if interval == ONE_MINUTE_BARS:
            return pd.DataFrame({"Close": []})  # no intraday bars: callers fall back to daily
        if start is not None:
            return self._range(start, end)
        unit, count = _parse_period(period or "1mo")
        snapshot = self._snapshot()
        if unit == "d":  # yfinance: the last `count` sessions
            sessions = _sessions(snapshot)
            return _bars(self.ticker, sessions[max(len(sessions) - count, 0)], snapshot)
        if unit == "y":  # a full year of sessions, never one short of 252 (yfinance can be)
            sessions = _sessions(snapshot)
            return _bars(self.ticker, sessions[max(len(sessions) - YEAR_SESSIONS * count, 0)],
                         snapshot)
        return _bars(self.ticker, max(snapshot - timedelta(days=31 * count), DEMO_EPOCH), snapshot)

    def _range(self, start, end) -> pd.DataFrame:
        first = pd.Timestamp(start).date()
        _check_epoch(first)
        last = self._snapshot() if end is None else min(
            pd.Timestamp(end).date() - timedelta(days=1), self._snapshot())
        if last < first:
            return pd.DataFrame({"Close": []})
        return _bars(self.ticker, first, last)

    @property
    def calendar(self) -> dict:
        spec, today = SPECS[self.ticker], self._now()
        cal: dict = {"Earnings Date": [_next_after(today, (1, 4, 7, 10), spec.earnings_day)]
                     if spec.earnings_day else []}
        if spec.dividend_day:
            cal["Ex-Dividend Date"] = _next_after(today, (3, 6, 9, 12), spec.dividend_day)
        return cal

    @property
    def info(self) -> dict:
        spec = SPECS[self.ticker]
        return {"marketCap": spec.market_cap, "quoteType": spec.quote_type}
