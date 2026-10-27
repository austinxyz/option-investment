#!/usr/bin/env python3
"""Pure price-history metrics for /strategy-pick (no I/O, no network, no thresholds).

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.5). Inputs are split- and
dividend-adjusted closes in ascending date order; comparing a metric against a class threshold
is the regime module's job, so nothing here decides anything.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from strategy_pick_rules import (ANNUALIZE, LOOKBACK_DAYS, MA_LONG, MA_SHORT, MA_SLOPE_LAG,
                                 MIN_HISTORY_DAYS, MIN_SLOPE_DAYS, RECENT_DAYS, RV_WINDOW,
                                 TOUCH_BAND_PCT, TOUCH_GAP_DAYS)


@dataclass(frozen=True)
class Metrics:
    n_days: int
    last_close: float
    r1y: float
    ma50: float | None
    ma200: float | None
    ma200_slope: float | None
    max_dd: float
    rv1y: float
    rv21_series: tuple[float, ...]
    rv21_current: float | None
    range_width: float
    touches_high: int
    touches_low: int
    r3m: float | None
    rv63: float | None
    crossed_ma200_63d: bool
    dollar_volume_median: float
    missing_sessions: int
    history_short: bool
    history_under_year: bool


# Copy of morning_scan.MARKET_HOLIDAYS / missing_sessions, kept here so this module does not pull
# yfinance in at import; a test pins both to the originals so they cannot drift.
MARKET_HOLIDAYS = frozenset({
    # 2025 includes 01-09 (national day of mourning, President Carter).
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    # 2028-01-01 is a Saturday and NYSE does not observe it on 2027-12-31.
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
})


def missing_sessions(dates: list[date], lookback: int = 10) -> list[str]:
    """Weekdays absent from `dates` within its own last `lookback` span, holidays excluded."""
    if len(dates) < 2:
        return []
    recent = sorted(dates)[-lookback:]
    have, cur, gaps = set(recent), recent[0], []
    while cur < recent[-1]:
        cur += timedelta(days=1)
        if cur.weekday() < 5 and cur.isoformat() not in MARKET_HOLIDAYS and cur not in have:
            gaps.append(cur.isoformat())
    return gaps


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def annualized_vol(returns: list[float]) -> float:
    """Sample standard deviation (ddof=1) scaled by sqrt(ANNUALIZE)."""
    if len(returns) < 2:
        raise ValueError("need at least 2 returns for a sample standard deviation")
    return statistics.stdev(returns) * math.sqrt(ANNUALIZE)


def rolling_rv(closes: list[float], window: int = RV_WINDOW) -> list[float]:
    """One annualized vol per `window`-return window; N closes give N - window of them."""
    rets = log_returns(closes)
    return [annualized_vol(rets[i:i + window]) for i in range(len(rets) - window + 1)]


def sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def ma200_slope(closes: list[float]) -> float | None:
    """Today's MA200 over the MA200 MA_SLOPE_LAG sessions ago, minus 1; None under MIN_SLOPE_DAYS."""
    if len(closes) < MIN_SLOPE_DAYS:
        return None
    return sma(closes, MA_LONG) / sma(closes[:-MA_SLOPE_LAG], MA_LONG) - 1


def max_drawdown(closes: list[float]) -> float:
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = max(worst, (peak - c) / peak)
    return worst


def range_width(closes: list[float]) -> float:
    return (max(closes) - min(closes)) / statistics.median(closes)


def _count_touches(hits: list[int]) -> int:
    """Closes in the band form one touch until TOUCH_GAP_DAYS sessions pass without one."""
    count, prev = 0, None
    for i in hits:
        if prev is None or i - prev >= TOUCH_GAP_DAYS:
            count += 1
        prev = i
    return count


def touch_counts(closes: list[float]) -> tuple[int, int]:
    hi, lo = max(closes), min(closes)
    margin = TOUCH_BAND_PCT * (hi - lo)
    upper = [i for i, c in enumerate(closes) if c >= hi - margin]
    lower = [i for i, c in enumerate(closes) if c <= lo + margin]
    return _count_touches(upper), _count_touches(lower)


def percentile_le(value: float, dist: list[float]) -> float:
    """Share of `dist` at or below `value` (ties count: reaching the value is a hit)."""
    return sum(1 for d in dist if d <= value) / len(dist)


def median_dollar_volume(closes: list[float], volumes: list[float]) -> float:
    """Median of volume x close; zero-volume sessions stay in (the median shrugs them off)."""
    return statistics.median(c * v for c, v in zip(closes, volumes))


def _bad_close(c) -> bool:
    return c is None or math.isnan(c) or c <= 0


def _volume_or_zero(v) -> float:
    return 0.0 if v is None or math.isnan(v) else float(v)


def _clean(closes, volumes, dates):
    """Drop NaN/non-positive closes (with their volume/date), keep the latest LOOKBACK_DAYS.

    A zero or negative close would make log returns blow up; missing volumes count as zero.
    """
    keep = [i for i, c in enumerate(closes) if not _bad_close(c)][-LOOKBACK_DAYS:]
    return ([closes[i] for i in keep], [_volume_or_zero(volumes[i]) for i in keep],
            [dates[i] for i in keep])


def _crossed_ma200(closes: list[float], ma200: float | None) -> bool:
    # Fixed at today's MA200: a rolling MA200 over the last 63 days would need 263 closes.
    if ma200 is None:
        return False
    side = [c > ma200 for c in closes[-RECENT_DAYS:]]
    return any(a != b for a, b in zip(side, side[1:]))


def _recent_fields(closes: list[float]) -> tuple[float | None, float | None]:
    if len(closes) <= RECENT_DAYS:
        return None, None
    tail = closes[-RECENT_DAYS - 1:]
    return closes[-1] / tail[0] - 1, annualized_vol(log_returns(tail))


def compute_metrics(closes: list[float], volumes: list[float], dates: list[date]) -> Metrics:
    if not len(closes) == len(volumes) == len(dates):
        raise ValueError(f"length mismatch: closes={len(closes)} volumes={len(volumes)} "
                         f"dates={len(dates)}")
    closes, volumes, dates = _clean(closes, volumes, dates)
    if len(closes) < 3:
        raise ValueError("need at least 3 valid closes")
    rv21 = rolling_rv(closes)
    ma200 = sma(closes, MA_LONG)
    r3m, rv63 = _recent_fields(closes)
    up, low = touch_counts(closes)
    return Metrics(
        n_days=len(closes), last_close=closes[-1], r1y=closes[-1] / closes[0] - 1, ma50=sma(closes, MA_SHORT),
        ma200=ma200, ma200_slope=ma200_slope(closes), max_dd=max_drawdown(closes),
        rv1y=annualized_vol(log_returns(closes)), rv21_series=tuple(rv21),
        rv21_current=rv21[-1] if rv21 else None, range_width=range_width(closes),
        touches_high=up, touches_low=low, r3m=r3m, rv63=rv63,
        crossed_ma200_63d=_crossed_ma200(closes, ma200),
        dollar_volume_median=median_dollar_volume(closes, volumes),
        missing_sessions=len(missing_sessions(dates, lookback=len(dates))),
        history_short=len(closes) < MIN_HISTORY_DAYS,
        history_under_year=len(closes) < LOOKBACK_DAYS)
