"""Yahoo data layer (design 8.3-8.5, 10.2) against the scriptable fake yfinance. No network,
no real sleeping: `yahoo_data._clock` / `_sleep` are replaced by a fake clock.

ZZZQ and friends are invented. Snapshot trading day: Friday 2026-10-23, spot 100, expiry
2026-11-06 (14 days), strikes 80..120 step 1.
"""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

import ibkr_chain
import spread_core
import spread_data
import yahoo_data
from fake_yfinance import FakeYF, Scene, chain_frame, daily, install, minutes, utc
from spread_data import DataError, SourceUnavailable

pytestmark = pytest.mark.data_source("yahoo")

SNAP = date(2026, 10, 23)
EXP = "2026-11-06"
STRIKES = [float(s) for s in range(80, 121)]
FRIDAY_UTC = "2026-10-23 19:45"
IBKR_ROW_KEYS = ["strike", "bid", "ask", "last", "iv", "delta", "und_price", "close"]


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    yahoo_data.reset_memo()
    fake = FakeClock()
    monkeypatch.setattr(yahoo_data, "_clock", fake.clock)
    monkeypatch.setattr(yahoo_data, "_sleep", fake.sleep)
    yield fake
    yahoo_data.reset_memo()


def quote(strike, bid=None, ask=None, last=None, iv=0.35, traded=FRIDAY_UTC) -> dict:
    mid = max(0.05, round((100 - strike) * 0.1 + 0.3, 2)) if strike < 100 else 0.5
    return {"strike": strike, "bid": mid - 0.02 if bid is None else bid,
            "ask": mid + 0.02 if ask is None else ask, "lastPrice": mid if last is None else last,
            "impliedVolatility": iv, "lastTradeDate": utc(traded) if traded else None,
            "openInterest": 10}


def frame(overrides: dict | None = None, strikes=STRIKES):
    overrides = overrides or {}
    return chain_frame([overrides.get(s, quote(s)) for s in strikes])


def bars(last_close=100.0):
    return daily([(date(2026, 10, d), 99.0 + 0.1 * d) for d in (19, 20, 21, 22)]
                 + [(SNAP, last_close)])


def scene(puts=None, calls=None, options=(EXP,), **kw) -> Scene:
    chains = {EXP: (puts if puts is not None else frame(), calls if calls is not None else frame())}
    return Scene(options=options, chains=chains, history=bars(), **kw)


def setup(monkeypatch, **scenes) -> FakeYF:
    return install(monkeypatch, FakeYF(scenes or {"ZZZQ": scene()}))


class YFRateLimitError(Exception):
    """Same class name as yfinance's rate-limit error."""


class YFTickerMissingError(Exception):
    pass


class ConnectionError(Exception):  # noqa: A001 - mirrors requests / curl_cffi naming
    pass


class ReadTimeout(Exception):
    pass


# --- row shape and window ------------------------------------------------------

def test_row_shape_matches_ibkr_rows(monkeypatch):
    setup(monkeypatch)
    ibkr_row = ibkr_chain.ticker_to_row(SimpleNamespace(
        bid=1.0, ask=1.1, last=1.05, close=1.0, contract=SimpleNamespace(strike=95.0),
        modelGreeks=SimpleNamespace(impliedVol=0.3, delta=-0.2, undPrice=100.0)))
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P")
    assert rows
    for row in rows:
        assert list(row) == list(ibkr_row) + ["last_trade_date"] == IBKR_ROW_KEYS + [
            "last_trade_date"]
        assert row["delta"] is None and row["und_price"] is None and row["close"] is None
        assert isinstance(row["strike"], float) and row["last_trade_date"] == SNAP
    no_date = setup(monkeypatch, ZZQB=scene(puts=frame({95.0: quote(95.0, traded=None)})))
    assert no_date
    row95 = [r for r in yahoo_data.chain_rows("ZZQB", EXP, "P") if r["strike"] == 95.0][0]
    assert list(row95) == IBKR_ROW_KEYS


def test_window_equals_ibkr_strike_window(monkeypatch):
    setup(monkeypatch)
    for right in ("P", "C"):
        got = [r["strike"] for r in yahoo_data.chain_rows("ZZZQ", EXP, right)]
        assert got == ibkr_chain.strike_window(STRIKES, 100.0, right)
    wide = [r["strike"] for r in yahoo_data.chain_rows("ZZZQ", EXP, "P", window_pct=0.05,
                                                        window_limit=3)]
    assert wide == ibkr_chain.strike_window(STRIKES, 100.0, "P", pct=0.05, limit=3) == [
        97.0, 98.0, 99.0]


def test_explicit_strikes_intersect_listed(monkeypatch):
    setup(monkeypatch)
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P", strikes=(70.0, 100.5, 105))
    got = [r["strike"] for r in rows]
    assert got == ibkr_chain.strike_window(STRIKES, 100.0, "P") + [105.0]


def test_window_zero_returns_only_explicit(monkeypatch):
    setup(monkeypatch)
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "C", strikes=(95, 101.0, 130),
                                 window_pct=0.0, window_limit=0)
    assert [r["strike"] for r in rows] == [95.0, 101.0]


def test_float_strike_95_matches_request_95(monkeypatch):
    int_strikes = chain_frame([quote(s) for s in range(90, 101)])
    int_strikes["strike"] = int_strikes["strike"].astype(int)
    setup(monkeypatch, ZZZQ=scene(puts=int_strikes))
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P", strikes=(95,), window_pct=0.0, window_limit=0)
    assert [r["strike"] for r in rows] == [95.0]
    assert type(rows[0]["strike"]) is float
    assert 95.0 in spread_core.quote_map(rows)


# --- stale quotes --------------------------------------------------------------

def test_bid_zero_row_is_stale_last_only(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({93.0: quote(93.0, bid=0.0, ask=0.55, last=0.40)})))
    row = [r for r in yahoo_data.chain_rows("ZZZQ", EXP, "P") if r["strike"] == 93.0][0]
    assert (row["bid"], row["ask"], row["last"]) == (None, None, 0.40)
    q = spread_core.quote_map([row])[93.0]
    assert q["stale"] is True and q["mid"] == 0.40 and q["spread_pct"] is None


def test_stale_window_row_older_than_snapshot_dropped_and_counted(monkeypatch):
    # Design 10.2: run on Saturday, snapshot Friday. 93 traded Friday -> kept (stale);
    # 92 last traded 9 days earlier -> dropped and counted.
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        93.0: quote(93.0, bid=0.0, ask=0.0, last=0.40, traded="2026-10-23 18:00"),
        92.0: quote(92.0, bid=0.0, ask=0.0, last=0.35, traded="2026-10-14 18:00")})))
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P")
    strikes = [r["strike"] for r in rows]
    assert 93.0 in strikes and 92.0 not in strikes
    assert spread_core.quote_map(rows)[93.0]["stale"] is True
    assert yahoo_data.stale_drop_count("ZZZQ", EXP) == 1
    assert spread_data.stale_drop_count("ZZZQ", EXP) == 1
    assert yahoo_data.stale_drop_count("ZZZQ", "2026-11-13") == 0


def test_stale_explicit_strike_never_dropped(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        92.0: quote(92.0, bid=0.0, ask=0.0, last=0.35, traded="2026-10-14 18:00")})))
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P", strikes=(92,))
    row = [r for r in rows if r["strike"] == 92.0][0]
    assert row["last"] == 0.35 and row["last_trade_date"] == date(2026, 10, 14)
    assert spread_core.quote_map([row])[92.0]["stale"] is True
    assert yahoo_data.stale_drop_count("ZZZQ", EXP) == 0
    only = yahoo_data.chain_rows("ZZZQ", EXP, "P", strikes=(92.0,), window_pct=0, window_limit=0)
    assert [r["strike"] for r in only] == [92.0]


def test_last_trade_date_utc_converted_to_new_york_day(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        # Friday 21:30 UTC is Friday 17:30 in New York.
        97.0: quote(97.0, traded="2026-10-23 21:30"),
        # Saturday 01:30 UTC is still Friday evening in New York: a stale row is kept.
        96.0: quote(96.0, bid=0.0, ask=0.0, last=0.5, traded="2026-10-24 01:30"),
        # Friday 02:00 UTC is Thursday 22:00 in New York: older than the snapshot -> dropped.
        95.0: quote(95.0, bid=0.0, ask=0.0, last=0.6, traded="2026-10-23 02:00")})))
    rows = {r["strike"]: r for r in yahoo_data.chain_rows("ZZZQ", EXP, "P")}
    assert rows[97.0]["last_trade_date"] == SNAP
    assert rows[96.0]["last_trade_date"] == SNAP
    assert 95.0 not in rows
    assert yahoo_data._ny_day(datetime(2026, 10, 24, 1, 30)) == SNAP  # naive is taken as UTC
    assert yahoo_data._ny_day(None) is None


def test_all_rows_dropped_raises_data_error_text(monkeypatch):
    old = {s: quote(s, bid=0.0, ask=0.0, last=0.3, traded="2026-10-14 18:00") for s in STRIKES}
    setup(monkeypatch, ZZZQ=scene(puts=frame(old)))
    with pytest.raises(DataError) as err:
        yahoo_data.chain_rows("ZZZQ", EXP, "P")
    assert not isinstance(err.value, SourceUnavailable)
    assert str(err.value) == "没有拿到 ZZZQ 2026-11-06 的期权报价（Yahoo 休市后常没有买卖报价）"
    nothing = {s: quote(s, bid=0.0, ask=0.0, last=0.0, iv=0.0, traded=None) for s in STRIKES}
    setup(monkeypatch, ZZQB=scene(calls=frame(nothing)))
    with pytest.raises(DataError, match="没有拿到 ZZQB 2026-11-06 的期权报价"):
        yahoo_data.chain_rows("ZZQB", EXP, "C")


def test_no_options_data_error_text(monkeypatch):
    # An empty answer is only "no options" while the SPY re-probe is healthy (see outage tests).
    setup(monkeypatch, ZZZQ=scene(options=()), SPY=scene())
    with pytest.raises(DataError) as err:
        yahoo_data.expirations("zzzq")
    assert str(err.value) == "ZZZQ 没有期权"
    assert not isinstance(err.value, SourceUnavailable)


def test_iv_out_of_range_is_missing_and_4_5_kept(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({94.0: quote(94.0, iv=0.005),
                                              95.0: quote(95.0, iv=4.5),
                                              96.0: quote(96.0, iv=5.5)})))
    rows = {r["strike"]: r for r in yahoo_data.chain_rows("ZZZQ", EXP, "P")}
    assert rows[94.0]["iv"] is None and rows[96.0]["iv"] is None
    assert rows[95.0]["iv"] == 4.5
    q95 = spread_core.quote_map([rows[95.0]])[95.0]
    delta, warning = spread_core.delta_for("P", q95, 100.0, 14)
    assert delta == spread_core.put_delta(100.0, 95.0, 4.5, 14) and warning is None
    with pytest.raises(ValueError):
        spread_core.delta_for("P", spread_core.quote_map([rows[94.0]])[94.0], 100.0, 14)


# --- last close ----------------------------------------------------------------

def test_last_close_three_level_fallback(monkeypatch):
    nan = float("nan")
    fake = FakeYF({
        "ZZZQ": Scene(history=bars(101.5)),
        "ZZQB": Scene(history=bars(nan), minute=minutes([
            (datetime(2026, 10, 23, 15, 58), 100.7), (datetime(2026, 10, 23, 15, 59), 100.9),
            (datetime(2026, 10, 23, 16, 0), nan)])),
        "ZQIC": Scene(history=bars(nan), minute=minutes([(datetime(2026, 10, 22, 15, 59), 99.0)])),
        "ZQSP": Scene(history=daily([(SNAP, nan)])),
        "SPY": Scene(history=bars()),  # healthy re-probe: ZZQX's empty reply is a bad symbol
    })
    install(monkeypatch, fake)
    assert yahoo_data.last_close("ZZZQ") == (SNAP, 101.5)
    assert yahoo_data.last_close("ZZQB") == (SNAP, 100.9)
    assert yahoo_data.last_close("ZQIC") == (date(2026, 10, 22), 99.0 + 0.1 * 22)
    with pytest.raises(DataError, match="取不到有效收盘价"):
        yahoo_data.last_close("ZQSP")
    with pytest.raises(DataError, match="没有行情数据"):
        yahoo_data.last_close("ZZQX")
    daily_calls = [c for c in fake.network_calls("history") if ("period", "7d") in c[2][0]]
    assert len(daily_calls) == 5


# --- chain_strikes, memo, throttle ---------------------------------------------

def test_chain_strikes_union_60_days_max_6_and_cached(monkeypatch):
    weekly = ["2026-10-30", "2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27",
              "2026-12-04", "2026-12-11", "2026-12-18"]
    listed = weekly + ["2027-01-15"]
    chains = {e: (frame(strikes=[80.0 + i, 100.0]), frame(strikes=[110.0 + i]))
              for i, e in enumerate(listed)}
    fake = install(monkeypatch, FakeYF({"ZZZQ": Scene(options=tuple(reversed(listed)),
                                                      chains=chains, history=bars())}))
    got = yahoo_data.chain_strikes("ZZZQ")
    assert got == sorted({80.0 + i for i in range(6)} | {100.0} | {110.0 + i for i in range(6)})
    assert all(type(s) is float for s in got)
    assert [c[2][0] for c in fake.network_calls("option_chain")] == weekly[:6]
    assert yahoo_data.chain_strikes("ZZZQ") == got
    yahoo_data.chain_rows("ZZZQ", "2026-11-06", "P", strikes=(81.0,))
    assert len(fake.network_calls("option_chain")) == 6
    near = yahoo_data._horizon_expiries(["2027-01-15", "2027-03-19"], SNAP)
    assert near == ["2027-01-15"]


def test_memo_hits_avoid_second_call(monkeypatch):
    fake = setup(monkeypatch)
    for _ in range(2):
        yahoo_data.expirations("ZZZQ")
        yahoo_data.last_close("ZZZQ")
        yahoo_data.chain_rows("ZZZQ", EXP, "P")
        yahoo_data.chain_rows("ZZZQ", EXP, "C")
    assert [c[1] for c in fake.calls] == ["options", "history", "option_chain"]
    assert fake.tickers_made == ["ZZZQ"]
    yahoo_data.reset_memo()
    yahoo_data.expirations("ZZZQ")
    assert len(fake.network_calls("options")) == 2


def test_throttle_waits_0_4s_between_calls(monkeypatch, clock):
    setup(monkeypatch, ZZZQ=scene(), ZZQB=scene())
    yahoo_data.expirations("ZZZQ")
    assert clock.sleeps == []
    yahoo_data.expirations("ZZQB")
    assert clock.sleeps == [yahoo_data.YAHOO_MIN_INTERVAL_SECONDS] == [0.4]
    clock.now += 0.1
    yahoo_data.last_close("ZZZQ")
    assert clock.sleeps == [0.4, 0.3]
    clock.now += 5.0
    yahoo_data.last_close("ZZQB")
    assert clock.sleeps == [0.4, 0.3]


def test_rate_limit_backoff_5s_then_retry_once(monkeypatch, clock):
    # Matched by class NAME alone: the message carries no rate-limit keyword.
    fake = setup(monkeypatch, ZZZQ=scene(raises={1: YFRateLimitError("please hold")}))
    assert yahoo_data.expirations("ZZZQ") == [EXP]
    assert len(fake.network_calls("options")) == 2
    assert yahoo_data.YAHOO_BACKOFF_SECONDS == 5
    assert clock.sleeps[0] == 5


def test_rate_limit_twice_is_source_unavailable(monkeypatch, clock):
    limited = Exception("429 Client Error: Too Many Requests for url")
    fake = setup(monkeypatch, ZZZQ=scene(raises={1: limited, 2: limited}))
    with pytest.raises(SourceUnavailable) as err:
        yahoo_data.expirations("ZZZQ")
    assert str(err.value).startswith("连不上 Yahoo Finance：被 Yahoo 限流，请稍后再试（")
    assert len(fake.network_calls("options")) == 2
    assert clock.sleeps.count(5) == 1


# --- classification ------------------------------------------------------------

@pytest.mark.parametrize("exc, kind", [
    (YFRateLimitError("Too Many Requests. Rate limited."), SourceUnavailable),
    (YFRateLimitError("please hold"), SourceUnavailable),
    (Exception("you are being rate limited"), SourceUnavailable),
    (Exception("ZZZQ: possibly delisted"), DataError),
    (ValueError("odd shape"), DataError),
    (Exception("Too Many Requests"), SourceUnavailable),
    (Exception("HTTP Error 429"), SourceUnavailable),
    (ConnectionError("Failed to perform"), SourceUnavailable),
    (ReadTimeout("read"), SourceUnavailable),
    (TimeoutError(), SourceUnavailable),
    (OSError("network disabled in share tests"), SourceUnavailable),
    (Exception("curl: (28) Operation timed out after 10001 ms"), SourceUnavailable),
    (Exception("Could not resolve host: query2.finance.yahoo.com"), SourceUnavailable),
    (YFTickerMissingError("ZZZQ: possibly delisted; no price data found"), DataError),
    (ValueError("Expiration `2026-11-06` cannot be found."), DataError),
    (KeyError("impliedVolatility"), DataError),
    (DataError("bad"), DataError),
    (RuntimeError("something new"), yahoo_data._Unclassified),
    (Exception("strike 4290 odd"), yahoo_data._Unclassified),
    (__import__("json").JSONDecodeError("Expecting value", "", 0), yahoo_data._Unclassified),
])
def test_classify_by_type_and_message_keywords(exc, kind):
    assert yahoo_data._classify(exc) is kind


def test_three_unclassified_in_a_row_is_source_unavailable(monkeypatch):
    boom = {1: RuntimeError("new failure mode")}
    setup(monkeypatch, ZZZQ=scene(raises=boom), ZZQB=scene(raises=dict(boom)),
          ZQIC=scene(raises=dict(boom)))
    for sym in ("ZZZQ", "ZZQB"):
        with pytest.raises(DataError) as err:
            yahoo_data.expirations(sym)
        assert not isinstance(err.value, SourceUnavailable)
    with pytest.raises(SourceUnavailable, match="连不上 Yahoo Finance：连续 3 只"):
        yahoo_data.expirations("ZQIC")


def test_unclassified_streak_resets_after_success(monkeypatch):
    boom = {1: RuntimeError("new failure mode")}
    setup(monkeypatch, ZZZQ=scene(raises=dict(boom)), ZZQB=scene(raises=dict(boom)),
          ZQSP=scene(), ZZQC=scene(raises=dict(boom)), ZZQD=scene(raises=dict(boom)),
          ZZQE=scene(raises=dict(boom)))
    for sym in ("ZZZQ", "ZZQB"):
        with pytest.raises(DataError):
            yahoo_data.expirations(sym)
    yahoo_data.expirations("ZQSP")
    for sym in ("ZZQC", "ZZQD"):
        with pytest.raises(DataError) as err:
            yahoo_data.expirations(sym)
        assert not isinstance(err.value, SourceUnavailable)
    with pytest.raises(SourceUnavailable):
        yahoo_data.expirations("ZZQE")


def test_probe_failure_text(monkeypatch):
    fake = install(monkeypatch, FakeYF({"SPY": Scene(history=bars())}))
    yahoo_data.ensure_available()
    assert fake.calls == [("SPY", "history", ((("period", "5d"),),))]
    spread_data.ensure_source()
    for spy in (Scene(), Scene(history=bars(), raises={1: OSError("no route")}),
                Scene(history=bars(), raises={1: ValueError("odd payload")}),
                Scene(history=bars(), raises={1: RuntimeError("odd")})):
        yahoo_data.reset_memo()
        install(monkeypatch, FakeYF({"SPY": spy}))
        with pytest.raises(SourceUnavailable) as err:
            yahoo_data.ensure_available()
        assert str(err.value).startswith("连不上 Yahoo Finance：")
        assert str(err.value).endswith("（请检查网络或代理设置后重试）")
        assert "连不上 Yahoo Finance：连不上" not in str(err.value)


def test_spread_core_quote_keys_unchanged_without_last_trade_date():
    ibkr_row = {"strike": 95, "bid": 1.0, "ask": 1.1, "last": 1.05, "iv": 0.3, "delta": 0.2,
                "und_price": 100.0, "close": 1.0}
    q = spread_core.quote_map([ibkr_row])[95.0]
    assert list(q) == ["strike", "mid", "stale", "spread_pct", "iv", "delta", "from_close"]
    yahoo_row = dict(ibkr_row, last_trade_date=SNAP)
    q2 = spread_core.quote_map([yahoo_row])[95.0]
    assert list(q2) == list(q) + ["last_trade_date"] and q2["last_trade_date"] == SNAP


def test_facade_routes_to_yahoo_layer(monkeypatch):
    setup(monkeypatch)
    assert spread_data._yahoo_layer() is yahoo_data
    assert spread_data.expirations("ZZZQ") == [EXP]
    assert spread_data.fetch_puts("ZZZQ", EXP, strikes=(95,), window_pct=0, window_limit=0)[0][
        "strike"] == 95.0


# --- boundaries and survivors ----------------------------------------------------

def test_throttle_sleeps_only_the_remaining_gap(monkeypatch, clock):
    setup(monkeypatch, ZZZQ=scene(), ZZQB=scene())
    yahoo_data.expirations("ZZZQ")
    clock.now += 0.35
    yahoo_data.expirations("ZZQB")
    assert clock.sleeps == [0.05]


def test_rate_limit_message_only_also_retries(monkeypatch, clock):
    fake = setup(monkeypatch, ZZZQ=scene(raises={1: Exception("rate limit exceeded")}))
    assert yahoo_data.expirations("ZZZQ") == [EXP]
    assert len(fake.network_calls("options")) == 2 and 5 in clock.sleeps


def test_window_row_without_last_trade_date_is_dropped_and_counted(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        93.0: quote(93.0, bid=0.0, ask=0.0, last=0.40, traded=None)})))
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P")
    assert 93.0 not in [r["strike"] for r in rows]
    assert yahoo_data.stale_drop_count("ZZZQ", EXP) == 1
    kept = yahoo_data.chain_rows("ZZZQ", EXP, "P", strikes=(93,), window_pct=0, window_limit=0)
    assert [r["strike"] for r in kept] == [93.0] and "last_trade_date" not in kept[0]


def test_stale_count_stable_across_repeated_calls(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        92.0: quote(92.0, bid=0.0, ask=0.0, last=0.35, traded="2026-10-14 18:00")})))
    for _ in range(3):
        yahoo_data.chain_rows("ZZZQ", EXP, "P")
    assert yahoo_data.stale_drop_count("ZZZQ", EXP) == 1


def test_equal_bid_ask_is_a_pair_crossed_is_stale(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        95.0: quote(95.0, bid=1.0, ask=1.0, last=0.9),
        96.0: quote(96.0, bid=1.2, ask=1.1, last=1.15)})))
    rows = {r["strike"]: r for r in yahoo_data.chain_rows("ZZZQ", EXP, "P")}
    assert (rows[95.0]["bid"], rows[95.0]["ask"]) == (1.0, 1.0)
    assert (rows[96.0]["bid"], rows[96.0]["ask"], rows[96.0]["last"]) == (None, None, 1.15)


def test_iv_band_edges_are_inclusive(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({94.0: quote(94.0, iv=0.01),
                                              95.0: quote(95.0, iv=5.0)})))
    rows = {r["strike"]: r for r in yahoo_data.chain_rows("ZZZQ", EXP, "P")}
    assert rows[94.0]["iv"] == 0.01 and rows[95.0]["iv"] == 5.0


def test_int_dtype_window_strikes_come_back_float(monkeypatch):
    ints = chain_frame([quote(s) for s in range(80, 100)])
    ints["strike"] = ints["strike"].astype(int)
    setup(monkeypatch, ZZZQ=scene(puts=ints))
    rows = yahoo_data.chain_rows("ZZZQ", EXP, "P")
    assert rows and all(type(r["strike"]) is float for r in rows)


def test_row_without_price_or_iv_is_left_out(monkeypatch):
    setup(monkeypatch, ZZZQ=scene(puts=frame({
        97.0: quote(97.0, bid=0.0, ask=0.0, last=0.0, iv=0.0, traded=None)})))
    assert 97.0 not in [r["strike"] for r in yahoo_data.chain_rows("ZZZQ", EXP, "P")]


def test_iv_only_rows_without_price_raise_no_quotes(monkeypatch):
    iv_only = {s: quote(s, bid=0.0, ask=0.0, last=0.0, iv=0.3, traded=None) for s in STRIKES}
    setup(monkeypatch, ZZZQ=scene(puts=frame(iv_only)))
    with pytest.raises(DataError, match="没有拿到 ZZZQ 2026-11-06 的期权报价"):
        yahoo_data.chain_rows("ZZZQ", EXP, "P", strikes=(95,))


def test_missing_side_frame_is_no_quotes_not_no_strikes(monkeypatch):
    setup(monkeypatch, ZZZQ=Scene(options=(EXP,), chains={EXP: (None, frame())},
                                  history=bars()))
    with pytest.raises(DataError) as err:
        yahoo_data.chain_rows("ZZZQ", EXP, "P")
    assert str(err.value) == "没有拿到 ZZZQ 2026-11-06 的期权报价（Yahoo 休市后常没有买卖报价）"


def test_same_ticker_retries_count_once_toward_the_streak(monkeypatch):
    boom = RuntimeError("new failure mode")
    setup(monkeypatch, ZZZQ=scene(raises={1: boom, 2: boom, 3: boom}),
          ZZQB=scene(raises={1: boom}), ZQIC=scene(raises={1: boom}))
    for _ in range(3):
        with pytest.raises(DataError) as err:
            yahoo_data.expirations("ZZZQ")
        assert not isinstance(err.value, SourceUnavailable)
    with pytest.raises(DataError) as err:
        yahoo_data.expirations("ZZQB")
    assert not isinstance(err.value, SourceUnavailable)
    with pytest.raises(SourceUnavailable):
        yahoo_data.expirations("ZQIC")


def test_module_switch_invalidates_caches(monkeypatch):
    setup(monkeypatch)
    assert yahoo_data.expirations("ZZZQ") == [EXP]
    install(monkeypatch, FakeYF({"ZZZQ": scene(options=("2026-11-13",))}))
    assert yahoo_data.expirations("ZZZQ") == ["2026-11-13"]


def test_chain_strikes_result_is_memoized(monkeypatch):
    setup(monkeypatch)
    got = yahoo_data.chain_strikes("ZZZQ")
    assert yahoo_data._memo["strikes"]["ZZZQ"] == tuple(got)

    def no_chain(*a):
        raise AssertionError("chain_strikes recomputed instead of using its memo")
    monkeypatch.setattr(yahoo_data, "_chain", no_chain)
    assert yahoo_data.chain_strikes("ZZZQ") == got


@pytest.mark.parametrize("expiry, included", [
    ("2026-10-23", True),    # same-day expiry is still upcoming
    ("2026-12-22", True),    # exactly 60 days out
    ("2026-12-23", False),   # 61 days out
])
def test_chain_strikes_horizon_boundaries(monkeypatch, expiry, included):
    listed = tuple(sorted({EXP, expiry, "2027-06-18"}))
    chains = {e: (frame(strikes=[90.0 + i]), frame(strikes=[])) for i, e in enumerate(listed)}
    fake = install(monkeypatch, FakeYF({"ZZZQ": Scene(options=listed, chains=chains,
                                                      history=bars())}))
    yahoo_data.chain_strikes("ZZZQ")
    fetched = [c[2][0] for c in fake.network_calls("option_chain")]
    assert (expiry in fetched) is included
