"""Tests for the pure helpers in scripts/spread_data.py (no network)."""
from datetime import date, datetime
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_data as data

FRI = date(2026, 10, 16)


def test_pick_expiry_friday_snapshot_picks_next_friday():
    exps = ["2026-10-16", "2026-10-19", "2026-10-21", "2026-10-23", "2026-10-30"]
    assert data.pick_expiry(exps, FRI) == "2026-10-23"


def test_pick_expiry_unsorted_input_and_exact_seven_days():
    assert data.pick_expiry(["2026-10-30", "2026-10-23"], FRI) == "2026-10-23"


def test_pick_expiry_none_when_nothing_far_enough():
    assert data.pick_expiry(["2026-10-16", "2026-10-21"], FRI) is None
    assert data.pick_expiry([], FRI) is None


def test_classify_earnings_inside_window():
    assert data.classify_earnings([date(2026, 10, 21)], FRI, date(2026, 10, 23)) == "yes"


def test_classify_earnings_on_expiry_day_counts():
    assert data.classify_earnings([date(2026, 10, 23)], FRI, date(2026, 10, 23)) == "yes"


def test_classify_earnings_after_expiry_is_no():
    assert data.classify_earnings([date(2026, 10, 28)], FRI, date(2026, 10, 23)) == "no"


def test_classify_earnings_unknown_when_no_future_date():
    assert data.classify_earnings([], FRI, date(2026, 10, 23)) == "unknown"
    assert data.classify_earnings([date(2026, 7, 1)], FRI, date(2026, 10, 23)) == "unknown"


def test_gap_pct():
    assert data.gap_pct(100.0, 102.5) == 2.5
    assert data.gap_pct(100.0, 97.0) == -3.0


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 10, 14, 10, 0), True),    # Wednesday morning
    (datetime(2026, 10, 14, 9, 30), True),    # the open
    (datetime(2026, 10, 14, 9, 29), False),   # one minute before
    (datetime(2026, 10, 14, 15, 59), True),
    (datetime(2026, 10, 14, 16, 0), False),   # the close
    (datetime(2026, 10, 17, 11, 0), False),   # Saturday
    (datetime(2026, 10, 18, 11, 0), False),   # Sunday
])
def test_is_market_open(when, expected):
    assert data.is_market_open(when) is expected


def test_pick_expiry_works_from_any_weekday_not_just_friday():
    wed = date(2026, 10, 14)
    assert data.pick_expiry(["2026-10-16", "2026-10-19", "2026-10-21", "2026-10-23"], wed) == "2026-10-21"
    thu = date(2026, 10, 15)   # a Friday-only name: 7 days later is a Thursday that is not listed
    assert data.pick_expiry(["2026-10-16", "2026-10-23", "2026-10-30"], thu) == "2026-10-23"


# --- raw (unadjusted) prices ------------------------------------------------

class _FakeTicker:
    def __init__(self, frame, calls):
        self._frame, self._calls = frame, calls

    def history(self, **kw):
        self._calls.append(kw)
        return self._frame


def _frame():
    import pandas as pd
    idx = pd.DatetimeIndex(["2026-10-23"], tz="America/New_York")
    return pd.DataFrame({"Open": [101.0], "Close": [102.5]}, index=idx)


def test_history_wrappers_use_unadjusted_prices(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(data, "_ticker", lambda s: _FakeTicker(_frame(), calls))
    data.close_on("abc", date(2026, 10, 23))
    data.monday_gap_pct("abc", date(2026, 10, 22), 100.0)
    assert len(calls) == 2
    assert all(kw.get("auto_adjust") is False for kw in calls)


def test_close_on_returns_close_for_requested_day_only(monkeypatch):
    monkeypatch.setattr(data, "_ticker", lambda s: _FakeTicker(_frame(), []))
    assert data.close_on("abc", date(2026, 10, 23)) == 102.5
    assert data.close_on("abc", date(2026, 10, 24)) is None


# --- NaN-robust network wrappers (fake _ticker, no network) ---------------

import pandas as pd


def _nanframe(rows):
    """rows: list of (iso date or timestamp str, close, open)."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0], tz="America/New_York") for r in rows])
    return pd.DataFrame({"Close": [r[1] for r in rows], "Open": [r[2] for r in rows]}, index=idx)


class FakeTicker:
    def __init__(self, daily=None, minute=None, minute_error=False):
        self.daily, self.minute, self.minute_error = daily, minute, minute_error
        self.calls = []

    def history(self, **kw):
        self.calls.append(kw)
        if kw.get("interval") == "1m":
            if self.minute_error:
                raise RuntimeError("boom")
            return self.minute if self.minute is not None else pd.DataFrame()
        return self.daily


NAN = float("nan")


def _patch(monkeypatch, fake):
    monkeypatch.setattr(data, "_ticker", lambda s: fake)
    return fake


def test_moving_averages_skips_trailing_nan_bar(monkeypatch):
    days = pd.bdate_range("2025-10-01", periods=250)
    rows = [(d.strftime("%Y-%m-%d"), 100.0 + i, 100.0) for i, d in enumerate(days)]
    rows[-1] = (rows[-1][0], NAN, 100.0)
    _patch(monkeypatch, FakeTicker(daily=_nanframe(rows)))
    got = data.moving_averages("TSLA")
    assert got["snapshot_date"] == days[-2].date()
    assert got["close"] == 100.0 + 248
    assert got["ma50"] == pytest.approx(sum(100.0 + i for i in range(199, 249)) / 50)


def test_close_on_nan_close_is_none(monkeypatch):
    _patch(monkeypatch, FakeTicker(daily=_nanframe([("2026-10-19", NAN, 361.0)])))
    assert data.close_on("TSLA", date(2026, 10, 19)) is None
    _patch(monkeypatch, FakeTicker(daily=_nanframe([("2026-10-19", 368.0, 361.0)])))
    assert data.close_on("TSLA", date(2026, 10, 19)) == 368.0


def test_monday_gap_nan_open_is_none(monkeypatch):
    _patch(monkeypatch, FakeTicker(daily=_nanframe([("2026-10-19", 368.0, NAN)])))
    assert data.monday_gap_pct("TSLA", date(2026, 10, 16), 360.0) is None
    _patch(monkeypatch, FakeTicker(daily=_nanframe([("2026-10-19", 368.0, 363.6)])))
    assert data.monday_gap_pct("TSLA", date(2026, 10, 16), 360.0) == 1.0


# --- order-facing functions delegate to IBKR (no Yahoo fallback) -------------

def _boom_ticker(symbol):
    raise AssertionError("Yahoo _ticker must not be touched by order-facing functions")


def test_spread_data_order_facing_functions_delegate_to_ibkr_data(monkeypatch):
    import ibkr_data
    calls = []
    monkeypatch.setattr(data, "_ticker", _boom_ticker)
    monkeypatch.setattr(ibkr_data, "expirations", lambda s: calls.append(("exp", s)) or ["2026-10-23"])
    monkeypatch.setattr(ibkr_data, "chain_rows",
                        lambda s, e, r, strikes=(): calls.append(("rows", s, e, r, tuple(strikes))) or [{"strike": 1.0}])
    monkeypatch.setattr(ibkr_data, "last_close",
                        lambda s: calls.append(("close", s)) or (date(2026, 10, 19), 368.0))
    assert data.expirations("TSLA") == ["2026-10-23"]
    assert data.fetch_puts("TSLA", "2026-10-23") == [{"strike": 1.0}]
    assert data.fetch_calls("TSLA", "2026-10-23") == [{"strike": 1.0}]
    assert data.fetch_puts("TSLA", "2026-10-23", strikes=(400.0,)) == [{"strike": 1.0}]
    assert data.last_close("TSLA") == (date(2026, 10, 19), 368.0)
    assert calls == [("exp", "TSLA"), ("rows", "TSLA", "2026-10-23", "P", ()),
                     ("rows", "TSLA", "2026-10-23", "C", ()),
                     ("rows", "TSLA", "2026-10-23", "P", (400.0,)), ("close", "TSLA")]


def test_spread_data_has_no_yahoo_fallback_for_chains(monkeypatch):
    import ibkr_data
    import ibkr_session
    monkeypatch.setattr(data, "_ticker", _boom_ticker)

    def down(*a, **k):
        raise ibkr_session.GatewayUnavailable("gateway down")

    for name in ("expirations", "chain_rows", "last_close"):
        monkeypatch.setattr(ibkr_data, name, down)
    with pytest.raises(ibkr_session.GatewayUnavailable):
        data.expirations("TSLA")
    with pytest.raises(ibkr_session.GatewayUnavailable):
        data.fetch_puts("TSLA", "2026-10-23")
    with pytest.raises(ibkr_session.GatewayUnavailable):
        data.fetch_calls("TSLA", "2026-10-23")
    with pytest.raises(ibkr_session.GatewayUnavailable):
        data.last_close("TSLA")


def test_spread_data_does_not_import_ibkr_at_import_time():
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); import spread_data; "
            "print([m for m in sys.modules if m.startswith('ibkr')])" % str(REPO_ROOT / "scripts"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_yahoo_order_facing_leftovers_removed():
    assert not hasattr(data, "_minute_close_on")


def test_ex_dividend_in_window_inside_outside_and_missing(monkeypatch):
    class _T:
        def __init__(self, cal):
            self.calendar = cal

    snap, exp = date(2026, 10, 16), date(2026, 10, 23)
    monkeypatch.setattr(data, "_ticker", lambda s: _T({"Ex-Dividend Date": date(2026, 10, 20)}))
    assert data.ex_dividend_in_window("ABC", snap, exp) == date(2026, 10, 20)
    monkeypatch.setattr(data, "_ticker", lambda s: _T({"Ex-Dividend Date": exp}))
    assert data.ex_dividend_in_window("ABC", snap, exp) == exp
    monkeypatch.setattr(data, "_ticker", lambda s: _T({"Ex-Dividend Date": snap}))
    assert data.ex_dividend_in_window("ABC", snap, exp) is None
    monkeypatch.setattr(data, "_ticker", lambda s: _T({"Ex-Dividend Date": date(2026, 11, 2)}))
    assert data.ex_dividend_in_window("ABC", snap, exp) is None
    monkeypatch.setattr(data, "_ticker", lambda s: _T({}))
    assert data.ex_dividend_in_window("ABC", snap, exp) is None
    monkeypatch.setattr(data, "_ticker", lambda s: _T(None))
    assert data.ex_dividend_in_window("ABC", snap, exp) is None


def test_month_return_pct_none_on_short_history(monkeypatch):
    short = _nanframe([("2026-10-16", 100.0, 100.0), ("2026-10-19", 101.0, 100.0)])
    _patch(monkeypatch, FakeTicker(daily=short))
    assert data.month_return_pct("ABC") is None


def test_month_return_pct_computes_percent(monkeypatch):
    days = pd.bdate_range("2026-09-01", periods=30)
    closes = [100.0] * 30
    closes[-1] = 110.0
    frame = pd.DataFrame({"Close": closes, "Open": closes},
                         index=days.tz_localize("America/New_York"))
    _patch(monkeypatch, FakeTicker(daily=frame))
    assert data.month_return_pct("ABC") == pytest.approx(10.0)


def test_fetch_puts_and_calls_forward_window_args(monkeypatch):
    import ibkr_data
    seen = []
    monkeypatch.setattr(ibkr_data, "chain_rows",
                        lambda s, e, r, **kw: seen.append((r, kw)) or [{"strike": 1.0}])
    data.fetch_puts("ZZZQ", "2026-11-20", window_pct=0.25, window_limit=60)
    data.fetch_calls("ZZZQ", "2026-11-20", strikes=(5.0,), window_pct=0.25, window_limit=60)
    data.fetch_puts("ZZZQ", "2026-11-20")
    assert seen[0] == ("P", {"strikes": (), "window_pct": 0.25, "window_limit": 60})
    assert seen[1] == ("C", {"strikes": (5.0,), "window_pct": 0.25, "window_limit": 60})
    assert seen[2] == ("P", {"strikes": ()})  # omitted -> not forwarded, old call shape


def test_chain_strikes_delegates_to_ibkr(monkeypatch):
    import ibkr_data
    monkeypatch.setattr(ibkr_data, "chain_strikes", lambda s: [80.0, 90.0])
    assert data.chain_strikes("ZZZQ") == [80.0, 90.0]
