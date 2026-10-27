"""Tests for the /strategy-pick data adapters. No network: every I/O function is stubbed.

ZZZQ is invented. Window arithmetic: snapshot 2026-10-23, expiries 2026-11-13 (21d), 2026-11-20
(28d), 2026-12-18 (56d); 30 days is nearest to 28 -> 2026-11-20.
"""
from datetime import date, datetime

import pandas as pd
import pytest

import positions
import spread_data
import strategy_pick_data as D
from ibkr_session import GatewayUnavailable
from spread_data import DataError

SNAP = date(2026, 10, 23)
EXPIRIES = ["2026-10-30", "2026-11-13", "2026-11-20", "2026-12-18", "2027-03-19"]


class FakeTicker:
    def __init__(self, hist=None, info=None, calendar=None, chain=None, boom=False):
        self._hist, self.info_value, self.calendar_value = hist, info, calendar
        self._chain, self._boom, self.history_calls = chain, boom, []

    def history(self, **kw):
        self.history_calls.append(kw)
        if self._boom:
            raise RuntimeError("network down")
        return self._hist

    @property
    def info(self):
        if self._boom:
            raise RuntimeError("network down")
        return self.info_value

    @property
    def calendar(self):
        if self._boom:
            raise RuntimeError("network down")
        return self.calendar_value

    def option_chain(self, expiry):
        if self._boom:
            raise RuntimeError("network down")
        return self._chain


def use_ticker(monkeypatch, fake):
    monkeypatch.setattr(D, "_ticker", lambda symbol: fake)
    return fake


def bars(closes, volumes=None):
    idx = pd.date_range("2026-01-02", periods=len(closes), freq="B")
    vols = volumes if volumes is not None else [1000] * len(closes)
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=idx)


# ---- yfinance adapters ----

def test_load_history_uses_auto_adjust_true(monkeypatch):
    fake = use_ticker(monkeypatch, FakeTicker(hist=bars([10.0, 11.0, 12.0])))
    out = D.load_history("ZZZQ")
    assert fake.history_calls == [{"period": "1y", "auto_adjust": True}]
    assert out.closes == (10.0, 11.0, 12.0) and len(out.dates) == len(out.volumes) == 3


def test_load_history_drops_bad_rows_keeps_alignment(monkeypatch):
    use_ticker(monkeypatch, FakeTicker(hist=bars([10.0, float("nan"), 0.0, 12.0],
                                                 [100, 200, 300, float("nan")])))
    out = D.load_history("ZZZQ")
    assert out.closes == (10.0, 12.0) and out.volumes == (100.0, 0.0)


@pytest.mark.parametrize("fake", [FakeTicker(boom=True), FakeTicker(hist=pd.DataFrame()),
                                  FakeTicker(hist=None)])
def test_load_history_failure_is_market_data_error(monkeypatch, fake):
    use_ticker(monkeypatch, fake)
    with pytest.raises(D.MarketDataError):
        D.load_history("ZZZQ")


def test_load_info_missing_fields_none(monkeypatch):
    use_ticker(monkeypatch, FakeTicker(info={"marketCap": None}))
    assert D.load_info("ZZZQ") == {"market_cap": None, "quote_type": None}
    use_ticker(monkeypatch, FakeTicker(boom=True))
    assert D.load_info("ZZZQ") == {"market_cap": None, "quote_type": None}
    use_ticker(monkeypatch, FakeTicker(info={"marketCap": 5e9, "quoteType": "EQUITY"}))
    assert D.load_info("ZZZQ") == {"market_cap": 5e9, "quote_type": "EQUITY"}


def test_next_earnings_none_when_calendar_empty(monkeypatch):
    for cal in ({}, None, {"Earnings Date": []}):
        use_ticker(monkeypatch, FakeTicker(calendar=cal))
        assert D.next_earnings("ZZZQ", SNAP) is None
    use_ticker(monkeypatch, FakeTicker(boom=True))
    assert D.next_earnings("ZZZQ", SNAP) is None


def test_next_earnings_first_future_date(monkeypatch):
    cal = {"Earnings Date": [date(2026, 10, 1), datetime(2026, 11, 5, 16, 0), date(2026, 12, 1)]}
    use_ticker(monkeypatch, FakeTicker(calendar=cal))
    assert D.next_earnings("ZZZQ", SNAP) == date(2026, 11, 5)


def test_atm_oi_sums_put_and_call_and_none_on_error(monkeypatch):
    class Chain:
        puts = pd.DataFrame({"strike": [95.0, 100.0], "openInterest": [40, 300]})
        calls = pd.DataFrame({"strike": [100.0, 105.0], "openInterest": [250, 9]})
    use_ticker(monkeypatch, FakeTicker(chain=Chain()))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0) == 550
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 95.0) == 40  # one side only
    use_ticker(monkeypatch, FakeTicker(chain=type("Empty", (), {
        "puts": pd.DataFrame({"strike": [], "openInterest": []}),
        "calls": pd.DataFrame({"strike": [], "openInterest": []})})()))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 77.0) is None
    use_ticker(monkeypatch, FakeTicker(boom=True))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0) is None


def test_atm_oi_uses_nearest_listed_strike_when_exact_missing(monkeypatch):
    """The weekly ATM strike (IWM 277.5) need not exist on the monthly grid (277 / 278)."""
    class Chain:
        puts = pd.DataFrame({"strike": [277.0, 278.0, 279.0],
                             "openInterest": [7370, 1740, float("nan")]})
        calls = pd.DataFrame({"strike": [277.0, 278.0], "openInterest": [1857, 2947]})
    use_ticker(monkeypatch, FakeTicker(chain=Chain()))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 277.6) == 1740 + 2947
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 277.0) == 7370 + 1857
    # equally near -> the lower strike (deterministic)
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 277.5) == 7370 + 1857


def _chain(put_oi, call_oi, strikes=(90.0, 95.0, 100.0, 105.0, 110.0, 40.0)):
    return type("Chain", (), {
        "puts": pd.DataFrame({"strike": list(strikes), "openInterest": list(put_oi)}),
        "calls": pd.DataFrame({"strike": list(strikes), "openInterest": list(call_oi)})})()


def test_atm_oi_none_when_every_near_money_oi_is_zero(monkeypatch):
    """Real AAPL/SPY/QQQ 2026-10-26 after the close: Yahoo OI 0 on every near-money strike (only a
    few deep-ITM strikes kept a value). That is missing data, not a zero."""
    use_ticker(monkeypatch, FakeTicker(chain=_chain([0] * 5 + [12], [0] * 5 + [7])))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0) is None


def test_atm_oi_none_when_near_money_oi_is_nan_or_zero(monkeypatch):
    nan = float("nan")
    use_ticker(monkeypatch, FakeTicker(chain=_chain([nan, 0, nan, 0, nan, nan],
                                                    [0, nan, 0, nan, 0, nan])))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0) is None


def test_atm_oi_real_low_values_are_kept(monkeypatch):
    """A thin name: non-zero OI near the money, but low. The value stands (the gate fails it)."""
    use_ticker(monkeypatch, FakeTicker(chain=_chain([3, 0, 0, 25, 0, 0], [0, 4, 0, 0, 9, 0])))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0) == 0
    use_ticker(monkeypatch, FakeTicker(chain=_chain([3, 0, 60, 25, 0, 0], [0, 4, 40, 0, 9, 0])))
    assert D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0) == 100


# ---- option snapshot ----

STRIKES = [float(s) for s in range(80, 121)]


def row(strike, bid=1.0, ask=1.2, iv=0.3):
    return {"strike": float(strike), "bid": bid, "ask": ask, "last": 1.1, "iv": iv, "delta": 0.5}


def stub_chain(monkeypatch, *, expiries=EXPIRIES, puts=None, calls=None, spot=100.0,
               strikes=STRIKES):
    seen = {"puts": [], "calls": []}

    def fetch(side, default):
        def _f(symbol, expiry, strikes=(), window_pct=None, window_limit=None):
            seen[side].append((expiry, list(strikes), window_pct, window_limit))
            result = default if default is not None else [row(s) for s in strikes]
            if isinstance(result, Exception):
                raise result
            return result
        return _f

    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, spot))
    monkeypatch.setattr(spread_data, "expirations", lambda t: list(expiries))
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: list(strikes))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("puts", puts))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("calls", calls))
    return seen


def test_option_snapshot_picks_20_60_expiry(monkeypatch):
    seen = stub_chain(monkeypatch)
    snap = D.option_snapshot("ZZZQ", SNAP)
    assert snap.expiry_used == date(2026, 11, 20)
    assert snap.expiries[0] == date(2026, 10, 30) and len(snap.expiries) == 5
    assert seen["puts"][0][0] == "2026-11-20"
    # about five strikes each side of spot, not the whole chain
    assert seen["puts"][0][1] == [96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    assert seen["puts"][0][2:] == (0.0, 0)
    assert snap.strikes_15pct == 31   # 85..115 inclusive of 41 listed strikes
    assert snap.atm_put["strike"] == 100.0 and snap.atm_call["strike"] == 100.0


def test_option_snapshot_no_expiry_in_window_returns_none_expiry(monkeypatch):
    seen = stub_chain(monkeypatch, expiries=["2026-10-30", "2027-03-19"])
    snap = D.option_snapshot("ZZZQ", SNAP)
    assert snap.expiry_used is None and snap.puts == () and snap.calls == ()
    assert snap.atm_iv is None and snap.atm_put is None and snap.strikes_15pct is None
    assert seen == {"puts": [], "calls": []}   # no pointless gateway work
    assert snap.spot == 100.0 and len(snap.expiries) == 2


def test_option_snapshot_atm_iv_put_then_call_fallback(monkeypatch):
    stub_chain(monkeypatch, puts=[row(100, iv=None), row(99, iv=None)],
               calls=[row(100, iv=0.41)])
    assert D.option_snapshot("ZZZQ", SNAP).atm_iv == 0.41
    stub_chain(monkeypatch, puts=[row(100, iv=0.33)], calls=[row(100, iv=0.41)])
    assert D.option_snapshot("ZZZQ", SNAP).atm_iv == 0.33


def test_option_snapshot_keeps_raw_zero_bids(monkeypatch):
    stub_chain(monkeypatch, puts=[row(100, bid=0.0, ask=0.0)], calls=[row(100, bid=0.0, ask=0.0)])
    snap = D.option_snapshot("ZZZQ", SNAP)
    assert snap.atm_put["bid"] == 0.0 and snap.atm_call["bid"] == 0.0


def test_option_snapshot_gateway_down_propagates(monkeypatch):
    stub_chain(monkeypatch)
    err = GatewayUnavailable("down")

    def boom(t):
        raise err
    monkeypatch.setattr(spread_data, "expirations", boom)
    with pytest.raises(GatewayUnavailable) as info:
        D.option_snapshot("ZZZQ", SNAP)
    assert info.value is err


def test_option_snapshot_drop_midway_propagates(monkeypatch):
    err = GatewayUnavailable("dropped")
    stub_chain(monkeypatch, puts=err)
    with pytest.raises(GatewayUnavailable) as info:
        D.option_snapshot("ZZZQ", SNAP)
    assert info.value is err


def test_option_snapshot_chain_strikes_gateway_loss_propagates(monkeypatch):
    # Fixed rows: if the loss were swallowed, the snapshot would succeed from them.
    stub_chain(monkeypatch, puts=[row(s) for s in STRIKES], calls=[row(s) for s in STRIKES])
    err = GatewayUnavailable("dropped on chain strikes")

    def boom(t):
        raise err
    monkeypatch.setattr(spread_data, "chain_strikes", boom)
    with pytest.raises(GatewayUnavailable) as info:
        D.option_snapshot("ZZZQ", SNAP)
    assert info.value is err


def test_option_snapshot_no_quotes_at_all_is_gateway_error(monkeypatch):
    stub_chain(monkeypatch, puts=DataError("没有拿到报价 10197"), calls=DataError("none"))
    with pytest.raises(GatewayUnavailable, match="10197"):
        D.option_snapshot("ZZZQ", SNAP)
    stub_chain(monkeypatch, puts=[], calls=[])
    with pytest.raises(GatewayUnavailable):
        D.option_snapshot("ZZZQ", SNAP)


@pytest.mark.data_source("yahoo")
def test_option_snapshot_no_quotes_at_all_yahoo_is_plain_data_error(monkeypatch):
    # Non-IBKR sources (design 8.5): a per-ticker DataError, never a source outage.
    msg = "没有拿到 ZZZQ 2026-11-20 的期权报价（Yahoo 休市后常没有买卖报价）"
    stub_chain(monkeypatch, puts=DataError(msg), calls=DataError("none"))
    with pytest.raises(DataError) as err:
        D.option_snapshot("ZZZQ", SNAP)
    assert not isinstance(err.value, spread_data.SourceUnavailable) and str(err.value) == msg
    stub_chain(monkeypatch, puts=[], calls=[])
    with pytest.raises(DataError) as err:
        D.option_snapshot("ZZZQ", SNAP)
    assert not isinstance(err.value, spread_data.SourceUnavailable)
    assert str(err.value) == "没有拿到 ZZZQ 的期权报价（休市后常没有买卖报价）"


def test_option_snapshot_one_side_empty_is_kept(monkeypatch):
    stub_chain(monkeypatch, puts=DataError("no puts"), calls=[row(100, iv=0.4)])
    snap = D.option_snapshot("ZZZQ", SNAP)
    assert snap.puts == () and snap.atm_put is None and snap.atm_iv == 0.4


# ---- wiki ----

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_read_thesis_status_and_verdict_truncated(tmp_path):
    write(tmp_path / "tickers" / "ZZZQ" / "overview.md",
          "# ZZZQ\n\n**Last updated**: 2026-10-20\n**Status**: Watch - good company\n")
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md",
          "**As of**: 2026-10-01\n**Verdict**: " + "x" * 300 + "\n**Verdict**: later\n")
    info = D.read_thesis("zzzq", tmp_path, today=date(2026, 10, 25))
    assert info.status == "watch" and info.found and not info.is_etf_page
    assert info.verdict_text == "x" * 200


def test_read_thesis_age_uses_later_of_as_of_and_last_updated(tmp_path):
    write(tmp_path / "tickers" / "ZZZQ" / "overview.md",
          "**Last updated**: 2026-09-01 (note)\n**Status**: Active\n")
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md", "**As of**: 2026-10-15\n")
    assert D.read_thesis("ZZZQ", tmp_path, today=date(2026, 10, 25)).age_days == 10
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md", "**As of**: 2026-08-01\n")
    assert D.read_thesis("ZZZQ", tmp_path, today=date(2026, 10, 25)).age_days == 54


def test_read_thesis_etf_page(tmp_path):
    write(tmp_path / "etf" / "ZZZQ" / "overview.md", "**Status**: Use\n")
    write(tmp_path / "etf" / "ZZZQ" / "thesis.md", "no header here\n")
    info = D.read_thesis("ZZZQ", tmp_path, today=date(2026, 10, 25))
    assert info.is_etf_page and info.status == "use" and info.verdict_text is None
    assert info.age_days is None


def test_read_thesis_missing_and_unrecognized(tmp_path):
    assert D.read_thesis("ZZZQ", tmp_path) == D.ThesisInfo(None, None, None, False, False, False, False)
    write(tmp_path / "tickers" / "ZZZQ" / "overview.md", "no status line here\n")
    info = D.read_thesis("ZZZQ", tmp_path)
    assert info.has_overview and info.status == "unrecognized"


def test_wiki_tier_from_sector_tables(tmp_path):
    write(tmp_path / "sectors" / "demo.md",
          "**Last updated**: 2026-10-01\n\n| Ticker | Tier |\n|---|---|\n| ZZZQ | 🚀 hot |\n")
    assert D.wiki_tier("zzzq", tmp_path) == "🚀"
    assert D.wiki_tier("QQQZ", tmp_path) is None
    assert D.wiki_tier("ZZZQ", tmp_path / "nowhere") is None


# ---- positions ----

HEADER = ("| Ticker | 账户 | Shares | Avg Cost | Entry Date | 类型 | LTCG日 | Status | Notes |\n"
          "|---|---|---|---|---|---|---|---|---|\n")
ETF_HEADER = ("# ETF 赛道仓位\n\n| 赛道 | Ticker | 账户 | Shares | Avg Cost | Entry Date | 目标% | "
              "Status | 备注 |\n|---|---|---|---|---|---|---|---|---|\n")


@pytest.fixture
def pos_file(tmp_path, monkeypatch):
    path = tmp_path / "positions.md"
    monkeypatch.setattr(positions, "POSITIONS_FILE", path)
    monkeypatch.setattr(positions, "CONFIG_FILE", tmp_path / "config.md")
    return path


def test_read_holding_per_account_rows(pos_file):
    pos_file.write_text(
        "# Active Positions\n\n" + HEADER
        + "| ZZZQ | acctA | 60 | 10.00 | 2024-01-02 | 🏦 蓝筹 | ✅ | Active | |\n"
        + "| ZZZQ | acctB | 50 | 11.00 | 2026-03-01 | 🏦 蓝筹 | 2027-03-01 | Active | |\n"
        + "| QQQZ | acctA | 7 | 5.00 | 2026-01-01 | ⚡ 动量 | — | Active | |\n",
        encoding="utf-8")
    out = D.read_holding("zzzq", today=date(2026, 10, 25))
    assert [(r.shares, r.ltcg_reached) for r in out.rows] == [(60.0, True), (50.0, False)]
    assert out.stock_type == "🏦 蓝筹" and not out.in_etf_table and not out.positions_missing
    held = D.holding_from_read(out)
    assert held.held_shares_total == 110 and held.max_lot_shares == 60 and held.lot_count == 2


def test_read_holding_includes_etf_table_rows(pos_file):
    pos_file.write_text(
        "# Active Positions\n\n" + HEADER + "\n" + ETF_HEADER
        + "| 电网 | ZZZQ | acctA | 30 | 20.00 | 2026-01-05 | 5.0 | Active | |\n",
        encoding="utf-8")
    out = D.read_holding("ZZZQ")
    assert out.in_etf_table and [(r.shares, r.source) for r in out.rows] == [(30.0, "etf")]


def test_read_holding_missing_file_not_created(pos_file):
    out = D.read_holding("ZZZQ")
    assert out == D.HoldingRead((), None, False, True)
    assert not pos_file.exists()
    held = D.holding_from_read(out)   # Task 5 can hand this straight to the tree
    assert held.held_shares_total == 0 and held.branch == "not_held"


def test_portfolio_value_missing_is_none(pos_file):
    assert D.portfolio_value() is None
    (pos_file.parent / "config.md").write_text("portfolio_value: abc\n", encoding="utf-8")
    assert D.portfolio_value() is None
    (pos_file.parent / "config.md").write_text("portfolio_value: 38000\n", encoding="utf-8")
    assert D.portfolio_value() == 38000.0


# ---- pins and read-only guarantees ----

def test_existing_spread_data_outputs_unchanged(monkeypatch):
    assert spread_data.pick_expiry(["2026-10-29", "2026-11-06", "2026-11-13"], SNAP) == "2026-11-06"
    assert spread_data.pick_expiry(["2026-10-25"], SNAP) is None
    use_ticker_spread = FakeTicker(calendar={"Earnings Date": [date(2026, 11, 5)]})
    monkeypatch.setattr(spread_data, "_ticker", lambda s: use_ticker_spread)
    assert spread_data.earnings_status("ZZZQ", SNAP, date(2026, 11, 20)) == "yes"
    assert spread_data.earnings_status("ZZZQ", SNAP, date(2026, 11, 1)) == "no"


def test_data_module_never_calls_position_writers(pos_file, monkeypatch, tmp_path):
    def forbidden(*a, **k):
        raise AssertionError("positions writer called")
    for name in ("add", "update", "close", "add_etf", "update_etf"):
        monkeypatch.setattr(positions, name, forbidden)
    use_ticker(monkeypatch, FakeTicker(hist=bars([1.0, 2.0]), info={}, calendar={}))
    stub_chain(monkeypatch)
    D.load_history("ZZZQ"), D.load_info("ZZZQ"), D.next_earnings("ZZZQ", SNAP)
    D.atm_open_interest("ZZZQ", date(2026, 11, 20), 100.0)
    D.option_snapshot("ZZZQ", SNAP)
    D.read_thesis("ZZZQ", tmp_path), D.wiki_tier("ZZZQ", tmp_path)
    D.read_holding("ZZZQ"), D.portfolio_value()
    pos_file.write_text("# Active Positions\n\n" + HEADER, encoding="utf-8")
    D.read_holding("ZZZQ")


# ---- review fixes ----

def test_read_thesis_found_needs_both_pages(tmp_path):
    write(tmp_path / "tickers" / "ZZZQ" / "overview.md", "**Status**: Active\n")
    info = D.read_thesis("ZZZQ", tmp_path)
    assert (info.has_overview, info.has_thesis, info.found) == (True, False, False)
    assert info.status == "active"
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md", "**As of**: 2026-10-01\n")
    info = D.read_thesis("ZZZQ", tmp_path)
    assert (info.has_overview, info.has_thesis, info.found) == (True, True, True)
    write(tmp_path / "tickers" / "QQQZ" / "thesis.md", "**As of**: 2026-10-01\n")
    info = D.read_thesis("QQQZ", tmp_path)
    assert (info.has_overview, info.has_thesis, info.found) == (False, True, False)


@pytest.mark.parametrize("line", [
    "> **Verdict**: Hold - invented text",
    "**Verdict（2026-10-16）**: Hold - invented text",
    "**Verdict**: Hold - invented text",
])
def test_read_thesis_verdict_header_shapes(tmp_path, line):
    write(tmp_path / "tickers" / "ZZZQ" / "overview.md", "**Status**: Watch\n")
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md",
          "# T\n\n" + line + "\n**Moat Verdict**: not this one\n")
    assert D.read_thesis("ZZZQ", tmp_path).verdict_text == "Hold - invented text"


def test_read_thesis_last_updated_shapes(tmp_path):
    write(tmp_path / "tickers" / "ZZZQ" / "overview.md",
          "> **Last Updated**: 2026-10-14 (note)\n> **Status**: Active\n")
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md", "> **Last updated**: 2026-10-20\n")
    assert D.read_thesis("ZZZQ", tmp_path, today=date(2026, 10, 25)).age_days == 5
    write(tmp_path / "tickers" / "ZZZQ" / "thesis.md", "nothing\n")
    assert D.read_thesis("ZZZQ", tmp_path, today=date(2026, 10, 25)).age_days == 11


def test_public_read_status(tmp_path):
    import put_screen_wiki
    path = tmp_path / "overview.md"
    path.write_text("> **Status**: Watch - x\n", encoding="utf-8")
    assert put_screen_wiki.read_status(path) == "watch"


def test_option_snapshot_marks_strike_count_as_proxy(monkeypatch):
    stub_chain(monkeypatch)
    assert D.option_snapshot("ZZZQ", SNAP).strikes_15pct_proxy is True


def test_option_snapshot_widens_when_selected_expiry_is_coarse(monkeypatch):
    seen = {"n": 0}

    def fetch(symbol, expiry, strikes=(), window_pct=None, window_limit=None):
        seen["n"] += 1
        # the expiry lists only even strikes, so the first narrow request qualifies 2 strikes
        return [row(s) for s in strikes if s % 2 == 0]

    stub_chain(monkeypatch)
    monkeypatch.setattr(spread_data, "fetch_puts", fetch)
    monkeypatch.setattr(spread_data, "fetch_calls", fetch)
    snap = D.option_snapshot("ZZZQ", SNAP)
    assert seen["n"] == 4   # one narrow and one wide request per side
    assert len(snap.puts) >= D.MIN_WINDOW_ROWS and len(snap.calls) >= D.MIN_WINDOW_ROWS


def test_option_snapshot_data_error_without_options_is_not_gateway(monkeypatch):
    stub_chain(monkeypatch)

    def none(t):
        raise DataError("ZZZQ 没有期权")
    monkeypatch.setattr(spread_data, "expirations", none)
    with pytest.raises(DataError) as info:
        D.option_snapshot("ZZZQ", SNAP)
    assert not isinstance(info.value, GatewayUnavailable)


def test_read_holding_stock_type_from_notes_tag(pos_file):
    pos_file.write_text(
        "# Active Positions\n\n" + HEADER
        + "| ZZZQ | acctA | 60 | 10.00 | 2024-01-02 | — | ✅ | Active | 【类型: 🚀 翻倍候选 ｜ 目标: x】 |\n",
        encoding="utf-8")
    assert "🚀" in D.read_holding("ZZZQ").stock_type


def test_read_holding_column_type_wins_and_disagreement_warns(pos_file):
    pos_file.write_text(
        "# Active Positions\n\n" + HEADER
        + "| ZZZQ | acctA | 60 | 10.00 | 2024-01-02 | 🏦 蓝筹 | ✅ | Active | |\n"
        + "| ZZZQ | acctB | 50 | 11.00 | 2024-03-01 | ⚡ 动量 | ✅ | Active | |\n",
        encoding="utf-8")
    out = D.read_holding("ZZZQ")
    assert out.stock_type == "🏦 蓝筹" and len(out.warnings) == 1
    pos_file.write_text(
        "# Active Positions\n\n" + HEADER
        + "| ZZZQ | acctA | 60 | 10.00 | 2024-01-02 | 🏦 蓝筹 | ✅ | Active | |\n",
        encoding="utf-8")
    assert D.read_holding("ZZZQ").warnings == ()


def test_read_holding_etf_ltcg_from_entry_date(pos_file):
    pos_file.write_text(
        "# Active Positions\n\n" + HEADER + "\n" + ETF_HEADER
        + "| 电网 | ZZZQ | acctA | 30 | 20.00 | 2025-10-24 | 5.0 | Active | |\n"
        + "| 电网 | ZZZQ | acctB | 30 | 20.00 | 2025-10-26 | 5.0 | Active | |\n",
        encoding="utf-8")
    out = D.read_holding("ZZZQ", today=date(2026, 10, 25))
    assert [r.ltcg_reached for r in out.rows] == [True, False]
