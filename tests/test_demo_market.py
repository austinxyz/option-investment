"""The deterministic offline demo market (share plan Task 7) and the demo source end to end.

Unit tests drive `demo_market` directly (tickers are the invented ZZZQ / ZZQB / ZQIC); the end
to end tests run the real CLIs in a tmp repo with OPTION_DATA_SOURCE=demo and
OPTION_TESTS_NO_NETWORK=1, so any stray request fails loudly.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import demo_market as M
import option_settings
import put_screen
import spread_core
import spread_data
import spread_sim
import strategy_pick
import yahoo_data
from fake_yfinance import CHAIN_COLUMNS as FAKE_COLUMNS
from spread_data import DataError, DemoDataRefused

SNAP = date(2026, 10, 23)      # a Friday
SATURDAY = date(2026, 10, 24)
DEMO_BANNER = "> ⚠️ 演示数据（虚构），不代表任何真实行情。"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(M, "_state", {"today": None, "wall": None})
    monkeypatch.delenv(M.ENV_TODAY, raising=False)
    yahoo_data.reset_memo()
    yield
    yahoo_data.reset_memo()


def _closes(symbol, today):
    return M.Ticker(symbol, today=today).history(period="1y")["Close"]


def test_same_day_same_numbers():
    a, b = M.Ticker("ZZZQ", today=SNAP), M.Ticker("ZZZQ", today=SNAP)
    pd.testing.assert_frame_equal(a.history(period="1y"), b.history(period="1y"))
    expiry = a.options[0]
    for side in ("puts", "calls"):
        pd.testing.assert_frame_equal(getattr(a.option_chain(expiry), side),
                                      getattr(b.option_chain(expiry), side))
    assert a.options == b.options and a.calendar == b.calendar and a.info == b.info


def test_numbers_do_not_depend_on_process_state():
    """Hash randomisation must not leak in: a child interpreter reproduces the same chain."""
    code = ("import sys; sys.path.insert(0, 'scripts'); import demo_market as M; "
            "from datetime import date; t = M.Ticker('ZQIC', today=date(2026, 10, 23)); "
            "print(t.option_chain(t.options[1]).puts.to_csv())")
    outs = {subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True,
                           text=True, env={**os.environ, "PYTHONHASHSEED": seed},
                           check=True).stdout for seed in ("1", "2")}
    assert len(outs) == 1


def test_any_day_has_data_including_weekend_snapshot():
    friday = _closes("ZZZQ", SNAP)
    saturday = _closes("ZZZQ", SATURDAY)
    sunday = _closes("ZZZQ", date(2026, 10, 25))
    assert saturday.iloc[-1] == sunday.iloc[-1] == friday.iloc[-1]
    assert saturday.index[-1].date() == SNAP
    assert all(d.weekday() < 5 for d in saturday.index.date)
    assert M.Ticker("ZZZQ", today=SATURDAY).options == M.Ticker("ZZZQ", today=SNAP).options \
        or M.Ticker("ZZZQ", today=SATURDAY).options[0] > SNAP.isoformat()


def test_holidays_have_no_bar_and_snapshot_steps_back():
    # Thanksgiving 2026-11-26, Good Friday 2026-04-03, July 4th observed Friday 2026-07-03
    for holiday in (date(2026, 11, 26), date(2026, 4, 3), date(2026, 7, 3), date(2026, 6, 19),
                    date(2026, 1, 19), date(2026, 12, 25)):
        assert not M.is_trading_day(holiday)
        bars = _closes("ZZQB", holiday + timedelta(days=1))
        assert holiday not in set(bars.index.date)
    assert M.last_trading_day(date(2026, 11, 26)) == date(2026, 11, 25)
    # a Saturday New Year is not observed on the Friday before (NYSE rule)
    assert M.is_trading_day(date(2027, 12, 31))


def test_past_expiry_close_on_works(monkeypatch):
    M.set_today(date(2026, 11, 2))
    monkeypatch.setattr(option_settings, "yf_module", lambda: M)
    close = spread_data.close_on("ZZZQ", date(2026, 10, 30))
    expected = M.close_for("ZZZQ", M._sessions(date(2026, 10, 30)).index(date(2026, 10, 30)))
    assert close == expected
    assert spread_data.close_on("ZZZQ", date(2026, 10, 31)) is None       # a Saturday


def test_year_end_and_dst_boundaries():
    # across New Year and both clock changes the bars stay one per trading day, NY midnight
    bars = M.Ticker("ZQIC", today=date(2027, 3, 20)).history(period="1y")
    days = list(bars.index.date)
    assert days == sorted(set(days))
    assert date(2027, 1, 1) not in days and date(2026, 12, 31) in days
    assert all(ts.hour == 0 and str(ts.tz) == "America/New_York" for ts in bars.index)
    assert date(2026, 11, 2) in days and date(2027, 3, 15) in days       # across Nov / Mar DST
    chain = M.Ticker("ZQIC", today=date(2027, 3, 20)).options
    assert all(date.fromisoformat(e).weekday() in (3, 4) and
               M.is_trading_day(date.fromisoformat(e)) for e in chain)


def test_dates_before_epoch_raise_data_error(monkeypatch):
    with pytest.raises(M.DemoDataError):
        M.Ticker("ZZZQ", today=date(2024, 12, 31)).history(period="1y")
    with pytest.raises(M.DemoDataError):
        M.Ticker("ZZZQ", today=SNAP).history(start="2024-12-01", end="2024-12-02")
    # through the Yahoo layer it is an ordinary DataError, not an outage
    monkeypatch.setattr(option_settings, "yf_module", lambda: M)
    M.set_today(date(2024, 12, 31))
    with pytest.raises(DataError):
        yahoo_data.last_close("ZZZQ")
    assert DataError in type(_raises(lambda: yahoo_data.last_close("ZZZQ"))).__mro__


def _raises(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - returns whichever exception came out
        return exc
    raise AssertionError("did not raise")


def test_real_ticker_rejected_text(monkeypatch):
    with pytest.raises(M.DemoDataError, match="演示数据只有虚构标的"):
        M.Ticker("AAPL")
    monkeypatch.setattr(option_settings, "yf_module", lambda: M)
    M.set_today(SNAP)
    exc = _raises(lambda: yahoo_data.expirations("AAPL"))
    assert type(exc) is DemoDataRefused and isinstance(exc, DataError)
    assert str(exc).startswith("演示数据只有虚构标的")
    assert all(t in str(exc) for t in M.DEMO_TICKERS)


def test_chain_dataframe_columns_match_fake_yfinance():
    chain = M.Ticker("ZZZQ", today=SNAP).option_chain(M.Ticker("ZZZQ", today=SNAP).options[0])
    for frame in (chain.puts, chain.calls):
        assert tuple(frame.columns) == FAKE_COLUMNS and len(frame) > 10
        assert frame["lastTradeDate"].map(lambda t: t.tzinfo is not None).all()
    with pytest.raises(ValueError, match="cannot be found"):
        M.Ticker("ZZZQ", today=SNAP).option_chain("2026-10-24")


def test_quotes_are_sane_and_spreads_stay_within_the_rule():
    t = M.Ticker("ZZQB", today=SNAP)
    for expiry in t.options[:4]:
        chain = t.option_chain(expiry)
        for frame in (chain.puts, chain.calls):
            width = (frame["ask"] - frame["bid"]) / ((frame["ask"] + frame["bid"]) / 2)
            assert (width <= 0.10).all() and (frame["bid"] > 0).all()
            assert (frame["ask"] > frame["bid"]).all()
        atm = chain.puts.iloc[(chain.puts["strike"] - M.close_for("ZZQB", 454)).abs().argmin()]
        assert (atm["ask"] - atm["bid"]) / atm["lastPrice"] < 0.06
    # put-call parity-ish: a higher strike put is dearer, a higher strike call cheaper
    puts, calls = t.option_chain(t.options[1]).puts, t.option_chain(t.options[1]).calls
    assert puts["lastPrice"].is_monotonic_increasing
    assert calls["lastPrice"].is_monotonic_decreasing


def test_calendar_and_info_are_fictional_and_stable():
    for symbol in M.DEMO_TICKERS:
        t = M.Ticker(symbol, today=SNAP)
        assert all(d >= SNAP for d in t.calendar["Earnings Date"])
        assert t.info["quoteType"] in ("EQUITY", "ETF") and t.info["marketCap"] > 0
    assert M.Ticker("ZQIC", today=SNAP).info["quoteType"] == "ETF"
    assert M.Ticker("ZQIC", today=SNAP).calendar["Ex-Dividend Date"] == date(2026, 12, 21)
    assert "Ex-Dividend Date" not in M.Ticker("ZZZQ", today=SNAP).calendar


def test_history_shapes_used_by_the_data_layers():
    t = M.Ticker("ZZZQ", today=SNAP)
    assert len(t.history(period="1y", auto_adjust=False)) >= 200
    assert len(t.history(period="3mo")) >= 22
    assert len(t.history(period="5d")) == 5 and len(t.history(period="7d")) == 7
    assert t.history(period="1d", interval="1m").empty
    year = t.history(period="1y", auto_adjust=True)
    assert {"Open", "Close", "Volume"} <= set(year.columns) and year["Close"].gt(0).all()


def test_demo_paper_dir_is_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTION_DATA_SOURCE", "demo")
    assert option_settings.paper_dir(tmp_path) == tmp_path / "data" / "paper-trading-demo"
    monkeypatch.setenv("OPTION_DATA_SOURCE", "yahoo")
    assert option_settings.paper_dir(tmp_path) == tmp_path / "data" / "paper-trading"


def test_demo_market_is_pure():
    text = (REPO_ROOT / "scripts" / "demo_market.py").read_text(encoding="utf-8")
    for banned in ("import random", "time.sleep", "import requests", "import yfinance",
                   "import socket", "date.today"):
        assert banned not in text


def test_wall_clock_is_read_once_per_process(monkeypatch):
    reads = []

    class Counting(datetime):
        @classmethod
        def now(cls, tz=None):
            reads.append(1)
            return datetime(2026, 10, 24, 9, 0)

    monkeypatch.setattr(M, "datetime", Counting)
    for _ in range(5):
        t = M.Ticker("ZZZQ")
        t.options, t.history(period="5d"), t.calendar
    assert len(reads) == 1
    assert M.Ticker("ZZZQ").history(period="5d").index[-1].date() == SNAP


def test_env_override_pins_today_and_set_today_beats_it(monkeypatch):
    monkeypatch.setenv(M.ENV_TODAY, "2026-11-13")
    assert M.Ticker("ZZZQ").history(period="5d").index[-1].date() == date(2026, 11, 13)
    assert M.Ticker("ZZZQ").options[0] == "2026-11-20"
    M.set_today(SNAP)
    assert M.Ticker("ZZZQ").history(period="5d").index[-1].date() == SNAP
    M.set_today(None)
    monkeypatch.setenv(M.ENV_TODAY, "13 Nov")
    with pytest.raises(M.DemoDataError, match="YYYY-MM-DD"):
        M.Ticker("ZZZQ").options


def test_golden_values_pin_the_formulas():
    assert M.DEMO_RISK_FREE == 0.04 == spread_core.RISK_FREE
    assert M.DEMO_EPOCH == date(2025, 1, 2) and M.DEMO_TICKERS == ("ZZZQ", "ZZQB", "ZQIC")
    assert M.bs_price("P", 100, 95, 0.3, 30) == pytest.approx(1.33320, abs=1e-4)
    assert M.bs_price("C", 100, 105, 0.3, 30) == pytest.approx(1.66197, abs=1e-4)
    n = M._sessions(SNAP).index(SNAP)
    assert n == 454
    assert [M.close_for(t, n) for t in M.DEMO_TICKERS] == [120.03, 59.48, 229.62]
    assert M.close_for("ZZZQ", 0) == 77.53
    assert M._sessions(M.CURVE_ORIGIN).index(M.CURVE_ORIGIN) == M.CURVE_ORIGIN_N


def test_history_is_deep_from_the_first_demo_day():
    """MA200, its 20-day slope (220 bars) and 'one year' (252) all exist on any realistic day."""
    for day in (date(2026, 10, 24), date(2027, 6, 1)):
        year = M.Ticker("ZZZQ", today=day).history(period="1y")
        assert len(year) >= 252 and year["Close"].gt(0).all()


def test_monthly_expiries_reach_about_six_months():
    listed = [date.fromisoformat(e) for e in M.Ticker("ZZZQ", today=SATURDAY).options]
    assert listed == sorted(set(listed)) and listed[0] == date(2026, 10, 30)
    assert (listed[-1] - SNAP).days >= 150 and len(listed) >= 13
    far = listed[M.WEEKLY_EXPIRIES:]
    assert far and all(15 <= e.day <= 21 and e.weekday() == 4 for e in far)   # third Fridays


def test_holiday_friday_never_lands_on_the_snapshot_day():
    # Dec 25 2026 is a Friday holiday: the weekly moves to Thursday 24th, the snapshot day
    for day in (date(2026, 12, 24), date(2026, 12, 26)):
        options = M.Ticker("ZZZQ", today=day).options
        assert all(date.fromisoformat(e) > date(2026, 12, 24) for e in options)
        M.Ticker("ZZZQ", today=day).option_chain(options[0])


def test_iv_position_label_varies_across_dates_and_tickers():
    labels = set()
    for week in range(0, 60, 3):
        day = date(2026, 10, 24) + timedelta(days=7 * week)
        for symbol in M.DEMO_TICKERS:
            bars = M.Ticker(symbol, today=day).history(period="1y")["Close"]
            rv = pd.Series(bars).pct_change().rolling(21).std().dropna() * (252 ** 0.5)
            n = M._sessions(M.last_trading_day(day)).index(M.last_trading_day(day))
            iv = M.atm_iv(symbol, n)
            pct = float((rv <= iv).mean())
            labels.add("high" if pct >= 0.70 else "low" if pct <= 0.30 else "mid")
    assert labels == {"high", "mid", "low"}


# ---- end to end through the real CLIs ----------------------------------------------------

@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTION_DATA_SOURCE", "demo")
    monkeypatch.setenv("OPTION_TESTS_NO_NETWORK", "1")
    monkeypatch.setattr(spread_sim, "REPO", tmp_path)
    monkeypatch.setattr(put_screen, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(put_screen, "WATCHLIST", tmp_path / "data" / "watchlist.txt")
    monkeypatch.setattr(yahoo_data, "_clock", lambda: 0.0)
    monkeypatch.setattr(yahoo_data, "_sleep", lambda s: pytest.fail("demo must not sleep"))
    M.set_today(SATURDAY)
    return tmp_path


pytestmark_demo = pytest.mark.data_source("demo")


def test_demo_has_no_market_hours(monkeypatch):
    """Independent of the real clock: freeze a weekday 10:00 New York, when markets ARE open."""
    instant = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(spread_data, "datetime", Frozen)
    monkeypatch.setenv("OPTION_DATA_SOURCE", "ibkr")
    assert spread_data.market_open_now() is True
    monkeypatch.setenv("OPTION_DATA_SOURCE", "demo")
    assert spread_data.market_open_now() is False


def _first_paragraph(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    if lines[0] == "---":
        lines = lines[lines.index("---", 1) + 1:]
    return "\n".join(lines).strip("\n").split("\n", 1)[1].strip("\n").split("\n\n")[0]


@pytest.mark.data_source("demo")
def test_demo_end_to_end_spread_sim_and_put_screen(world, capsys):
    ledger = world / "data" / "paper-trading-demo"
    assert spread_sim.main(["suggest", "ZZZQ"], today=SATURDAY) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "数据源：Demo · 数据质量：演示"
    sims = sorted((ledger / "suggestions").glob("*-ZZZQ*.md"))
    assert sims and _first_paragraph(sims[0]) == DEMO_BANNER

    assert put_screen.main(["suggest"], today=SATURDAY) == 0       # no --tickers: DEMO_TICKERS
    summary = json.loads(next((ledger / "put-screen").glob("*.json")).read_text(encoding="utf-8"))
    assert (summary["data_source"], summary["data_quality"]) == ("Demo", "demo")
    seen = {c["ticker"] for c in summary["candidates"]} | {r["ticker"] for r in summary["rejects"]}
    assert seen == set(M.DEMO_TICKERS)
    assert summary["candidates"], "the demo must always yield candidates"
    for md in (ledger / "suggestions").glob("*.md"):
        assert _first_paragraph(md) == DEMO_BANNER
    assert not (world / "data" / "paper-trading").exists()
    assert "Yahoo" not in "".join(p.read_text(encoding="utf-8")
                                  for p in ledger.rglob("*.md")).replace("Yahoo 模式", "")


@pytest.mark.data_source("demo")
def test_demo_open_status_roundtrip_settles_with_demo_closes(world):
    ledger = world / "data" / "paper-trading-demo"
    assert put_screen.main(["suggest", "--tickers", "ZZZQ"], today=SATURDAY) == 0
    assert put_screen.main(["open", "ZZZQ"], today=SATURDAY) == 0
    record = json.loads((ledger / "spreads.json").read_text(encoding="utf-8"))["spreads"][0]
    assert (record["data_source"], record["data_quality"]) == ("Demo", "demo")
    expiry = date.fromisoformat(record["expiry"])
    M.set_today(expiry + timedelta(days=1))
    assert put_screen.main(["status"], today=expiry + timedelta(days=1)) == 0
    settled = json.loads((ledger / "spreads.json").read_text(encoding="utf-8"))["spreads"][0]
    assert settled["status"] == "expired"
    assert settled["exit_underlying"] == spread_data.close_on("ZZZQ", expiry)


@pytest.mark.data_source("demo")
def test_demo_strategy_pick_neutral_etf(world, monkeypatch, capsys):
    monkeypatch.setattr(strategy_pick, "OUTPUT_DIR", world / "out")
    monkeypatch.setattr(strategy_pick, "WIKI_ROOT", world / "wiki")
    code = strategy_pick.main(["ZQIC", "--view", "中性", "--class", "etf"], today=SATURDAY)
    assert code == 0, capsys.readouterr()
    assert list((world / "out").glob("*.md")) == []          # nothing mixes with real picks
    md = next((world / "out" / "demo").glob("*.md"))
    assert _first_paragraph(md) == DEMO_BANNER
    assert "neutral.not_held" in json.loads(
        md.with_suffix(".json").read_text(encoding="utf-8"))["leaf_id"]


@pytest.mark.data_source("demo")
def test_demo_real_ticker_is_a_clean_error(world, capsys):
    ledger = world / "data" / "paper-trading-demo"
    assert put_screen.main(["suggest", "--tickers", "AAPL"], today=SATURDAY) == 0
    summary = json.loads(next((ledger / "put-screen").glob("*.json")).read_text(encoding="utf-8"))
    assert summary["candidates"] == []
    assert "演示数据只有虚构标的" in summary["rejects"][0]["reason"]
    assert spread_sim.main(["suggest", "AAPL"], today=SATURDAY) == 1
    captured = capsys.readouterr()
    assert "演示数据只有虚构标的" in captured.err and "Traceback" not in captured.err
