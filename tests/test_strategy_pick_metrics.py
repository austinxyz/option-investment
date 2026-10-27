"""Tests for scripts/strategy_pick_metrics.py - pure math, hand-computed numbers (ZZZQ, invented)."""
from pathlib import Path
import math
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import strategy_pick_metrics as m
import strategy_pick_rules as rules
from strategy_pick_fixtures import (base_closes, base_rv21, split_closes_adjusted,
                                    split_closes_unadjusted, weekday_dates)


def _alternating(sd: float, n: int = 40) -> list[float]:
    """Returns +/-k with k chosen so the sample std (ddof=1) is exactly sd."""
    k = sd * math.sqrt((n - 1) / n)
    return [k if i % 2 == 0 else -k for i in range(n)]


def test_rv21_window_count_is_closes_minus_21():
    assert len(m.rolling_rv([100.0 + (i % 5) for i in range(252)])) == 231
    assert len(m.rolling_rv([100.0 + (i % 5) for i in range(210)])) == 189


def test_annualized_vol_hand_example():
    # sd 0.0189 * sqrt(252)=15.8745 -> 0.3000 ; 0.0252 * 15.8745 -> 0.4000
    assert m.annualized_vol(_alternating(0.0189)) == pytest.approx(0.300, abs=0.001)
    assert m.annualized_vol(_alternating(0.0252)) == pytest.approx(0.400, abs=0.001)


def test_max_drawdown_hand_example():
    # (104.00 - 88.40) / 104.00 = 0.15
    assert m.max_drawdown(base_closes()) == pytest.approx(0.15)


def test_range_width_hand_example():
    # (104 - 78) / 92 = 0.2826
    assert m.range_width(base_closes()) == pytest.approx(26 / 92)


def test_base_fixture_matches_section_10():
    c = base_closes()
    assert len(c) == 252 and c[0] == 80.0 and c[-1] == 100.0
    assert m.sma(c, 50) == pytest.approx(96.0) and m.sma(c, 200) == pytest.approx(90.0)
    assert c[-64] == 92.0
    out = m.compute_metrics(c, [1.0] * 252, weekday_dates(252))
    assert out.r3m == pytest.approx(100 / 92 - 1)  # +8.70%


def test_ma200_slope_hand_example():
    # 90.00 / 88.20 - 1 = 0.0204
    assert m.ma200_slope(base_closes()) == pytest.approx(90.0 / 88.2 - 1)


def test_ma200_slope_none_below_220_closes():
    assert m.ma200_slope(base_closes()[-219:]) is None
    assert m.ma200_slope(base_closes()[-220:]) is not None


def test_percentile_le_hand_examples():
    # 174/231 = 0.7532 ; 58/231 = 0.2511 ; a window equal to the current value (0.32) counts
    assert m.percentile_le(0.32, base_rv21(174)) == pytest.approx(174 / 231)
    assert m.percentile_le(0.32, base_rv21(58)) == pytest.approx(58 / 231)
    assert 0.32 in base_rv21(174) and len(base_rv21(174)) == 231


def _closes_with_touches(days: list[int]) -> list[float]:
    """Flat 90, one low of 80 at idx 0, highs of 100 on the given indices."""
    c = [90.0] * 60
    c[0] = 80.0
    for d in days:
        c[d] = 100.0
    return c


def test_touch_counts_respect_gap_days():
    assert m.touch_counts(_closes_with_touches([20, 29]))[0] == 1   # 9 sessions apart
    assert m.touch_counts(_closes_with_touches([20, 30]))[0] == 2   # 10 sessions apart
    assert m.touch_counts(_closes_with_touches([20, 30]))[1] == 1   # the lone low


def test_dollar_volume_median():
    # 4,200,000 x 92.00 = 386.4e6, and a zero-volume session stays in the sample
    closes, vols = [92.0] * 5, [4_200_000.0, 4_200_000.0, 4_200_000.0, 0.0, 4_200_000.0]
    assert m.median_dollar_volume(closes, vols) == pytest.approx(386.4e6)


def test_nan_closes_dropped():
    closes = base_closes()
    closes[10] = float("nan")
    out = m.compute_metrics(closes, [1.0] * 252, weekday_dates(252))
    assert out.n_days == 251 and not math.isnan(out.rv1y)


def test_missing_sessions_counted():
    dates = weekday_dates(40)
    gappy = dates[:10] + dates[11:20] + dates[21:]   # two interior weekdays absent
    closes = [100.0 + (i % 4) for i in range(len(gappy))]
    assert m.compute_metrics(closes, [1.0] * len(gappy), gappy).missing_sessions == 2


def test_adjusted_split_has_no_fake_drawdown():
    assert m.max_drawdown(split_closes_unadjusted()) == pytest.approx(0.5, abs=0.01)
    assert m.max_drawdown(split_closes_adjusted()) < 0.2


def test_history_flags_200_and_252():
    def flags(n):
        out = m.compute_metrics(base_closes()[-n:], [1.0] * n, weekday_dates(n))
        return out.history_short, out.history_under_year
    assert flags(199) == (True, True)
    assert flags(200) == (False, True)
    assert flags(251) == (False, True)
    assert flags(252) == (False, False)


def test_compute_metrics_keeps_latest_lookback_and_is_frozen():
    out = m.compute_metrics([50.0] * 20 + base_closes(), [1.0] * 272, weekday_dates(272))
    assert out.n_days == rules.LOOKBACK_DAYS and len(out.rv21_series) == 231
    assert out.max_dd == pytest.approx(0.15) and not out.crossed_ma200_63d
    with pytest.raises(Exception):
        out.n_days = 1


def test_thresholds_for_flattens_class_and_shared():
    t = rules.thresholds_for("hot")
    assert t["VOL_HIGH"] == 0.60 and t["MIN_ATM_OI"] == 500
    with pytest.raises(ValueError):
        rules.thresholds_for("nope")


def test_no_threshold_literals_in_metrics_module():
    src = (REPO_ROOT / "scripts" / "strategy_pick_metrics.py").read_text(encoding="utf-8")
    for literal in ("0.35", "0.25", "0.70", "0.60", "0.40", "0.12", "0.30", "0.20"):
        assert literal not in src


def test_last_close_is_last_cleaned_close():
    closes = base_closes()
    out = m.compute_metrics(closes + [float("nan")], [1.0] * 253, weekday_dates(253))
    assert out.last_close == closes[-1] == 100.0


def test_non_positive_closes_dropped_before_logs():
    closes = base_closes()
    closes[10], closes[20] = 0.0, -5.0
    out = m.compute_metrics(closes, [1.0] * 252, weekday_dates(252))
    assert out.n_days == 250 and not math.isnan(out.rv1y)


def test_nan_and_none_volumes_count_as_zero():
    vols = [4_000_000.0] * 252
    vols[3], vols[4] = float("nan"), None
    out = m.compute_metrics(base_closes(), vols, weekday_dates(252))
    assert out.dollar_volume_median > 0 and not math.isnan(out.dollar_volume_median)


def test_length_mismatch_raises_clear_error():
    with pytest.raises(ValueError, match="length mismatch"):
        m.compute_metrics(base_closes(), [1.0] * 251, weekday_dates(252))
    with pytest.raises(ValueError, match="length mismatch"):
        m.compute_metrics(base_closes(), [1.0] * 252, weekday_dates(251))


def test_missing_sessions_helper_matches_morning_scan():
    # morning_scan is not part of the option-investment share edition; skip there, run here.
    morning_scan = pytest.importorskip("morning_scan")
    assert m.MARKET_HOLIDAYS == morning_scan.MARKET_HOLIDAYS
    dates = weekday_dates(60)
    for sample in (dates, dates[:10] + dates[12:30] + dates[31:], dates[:1], []):
        for lb in (10, 40, 100):
            assert m.missing_sessions(sample, lookback=lb) == morning_scan.missing_sessions(sample, lookback=lb)


def test_importing_metrics_does_not_import_yfinance():
    import subprocess
    code = ("import sys; sys.path.insert(0, 'scripts'); import strategy_pick_metrics; "
            "sys.exit(1 if 'yfinance' in sys.modules or 'morning_scan' in sys.modules else 0)")
    root = Path(__file__).resolve().parent.parent
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0


# NYSE full-day closures, written out independently of the module under test.
_NYSE_CLOSED_2025_2026 = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


def test_full_year_calendar_correct_series_has_no_missing_sessions():
    from datetime import date, timedelta
    sessions, cur = [], date(2026, 10, 23)
    while len(sessions) < 252:
        if cur.weekday() < 5 and cur.isoformat() not in _NYSE_CLOSED_2025_2026:
            sessions.append(cur)
        cur -= timedelta(days=1)
    sessions.reverse()
    assert m.missing_sessions(sessions, lookback=len(sessions)) == []


def test_holiday_table_covers_2025_and_2028():
    for d in ("2025-01-09", "2025-04-18", "2025-11-27", "2025-12-25",
              "2028-01-17", "2028-04-14", "2028-11-23", "2028-12-25"):
        assert d in m.MARKET_HOLIDAYS
