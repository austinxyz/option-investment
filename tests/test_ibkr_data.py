"""Tests for scripts/ibkr_data.py (fake IB only, no network)."""
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import re
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ibkr_data as data
import ibkr_session as sess
import spread_data

NAN = float("nan")
STRIKES = [60 + 0.25 * i for i in range(321)]  # 60..140 step 0.25
EXPIRIES = ["20261030", "20261023", "20261106"]  # unsorted on purpose


def greeks(delta=-0.2, iv=0.3, und=100.0):
    return SimpleNamespace(impliedVol=iv, delta=delta, undPrice=und)


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def fire(self, *a):
        for h in self.handlers:
            h(*a)


class FakeIB:
    """Market data only appears after `greeks_delay` / `stock_delay` sleep() calls."""

    def __init__(self, *, chains=None, bars=None, stock_close=100.0, und=100.0,
                 greeks_holes=(), no_greeks=False, ticker_time=None, errors=(),
                 no_data=False, greeks_delay=0, stock_delay=0, unqualifiable=(),
                 raise_on_sub=None, qualify_error=None, disconnect_after=None):
        self.disconnect_after = disconnect_after
        self.chains = chains if chains is not None else [self._chain()]
        self.bars = bars if bars is not None else [SimpleNamespace(date=date(2026, 10, 21), close=100.0)]
        self.stock_close, self.und = stock_close, und
        self.holes, self.no_greeks, self.no_data = set(greeks_holes), no_greeks, no_data
        self.ticker_time = ticker_time
        self.greeks_delay, self.stock_delay = greeks_delay, stock_delay
        self.unqualifiable = set(unqualifiable)
        self.raise_on_sub = raise_on_sub
        self.qualify_error = qualify_error
        self.pending_errors = list(errors)
        self.connected = True
        self.errorEvent = _Event()
        self.qualify_calls, self.subs, self.cancelled = [], [], []
        self.active, self.max_active, self.sleeps = [], 0, 0

    @staticmethod
    def _chain(**kw):
        base = dict(exchange="SMART", multiplier="100", tradingClass="TST",
                    expirations=list(EXPIRIES), strikes=list(STRIKES))
        base.update(kw)
        return SimpleNamespace(**base)

    def isConnected(self):
        return self.connected

    def placeOrder(self, *a, **k):  # tripwire
        raise AssertionError("order API called")

    def reqTickers(self, *a, **k):  # tripwire: returns nan snapshots on the real gateway
        raise AssertionError("reqTickers must not be used")

    def qualifyContracts(self, *contracts):
        if self.qualify_error:
            raise self.qualify_error
        self.qualify_calls.append(list(contracts))
        for i, c in enumerate(contracts, 1):
            c.conId = 0 if getattr(c, "strike", None) in self.unqualifiable else i
        return list(contracts)

    def reqSecDefOptParams(self, symbol, exch, sectype, conid):
        return self.chains

    def reqHistoricalData(self, contract, end, dur, bar, what, useRTH=True, **kw):
        return list(self.bars)

    def reqMktData(self, contract, generic="", snapshot=False, regulatory=False):
        errors, self.pending_errors = self.pending_errors, []
        for code in errors:
            self.errorEvent.fire(1, code, "msg", None)
        if self.raise_on_sub is not None and len(self.subs) == self.raise_on_sub:
            raise ValueError("boom")
        t = SimpleNamespace(contract=contract, bid=NAN, ask=NAN, last=NAN, close=NAN,
                            modelGreeks=None, time=None, marketPrice=lambda: 1.0,
                            born=self.sleeps)
        self.subs.append(contract)
        self.active.append(t)
        self.max_active = max(self.max_active, len(self.active))
        self._fill(t)
        return t

    def cancelMktData(self, contract):
        self.cancelled.append(contract)
        self.active = [t for t in self.active if t.contract is not contract]

    def sleep(self, secs=0):
        self.sleeps += 1
        if self.disconnect_after is not None and self.sleeps >= self.disconnect_after:
            self.connected = False
            return  # sleep on a dead link is a no-op
        for t in self.active:
            self._fill(t)

    def _fill(self, t):  # delays count sleeps since this ticker's own subscription
        waited = self.sleeps - t.born
        if getattr(t.contract, "secType", "") == "STK":
            if waited >= self.stock_delay:
                t.close, t.time = self.stock_close, self.ticker_time
            return
        if self.no_data or waited < self.greeks_delay:
            return
        t.bid, t.ask, t.last, t.close = 1.0, 1.2, 1.1, 1.1
        if not (self.no_greeks or t.contract.strike in self.holes):
            t.modelGreeks = greeks(und=self.und)


@pytest.fixture(autouse=True)
def _clean():
    data.reset_memo()
    yield
    data.reset_memo()


def use(monkeypatch, **kw):
    fake = FakeIB(**kw)
    monkeypatch.setattr(sess, "get_ib", lambda: fake)
    return fake


def test_expirations_iso_sorted_from_selected_chain(monkeypatch):
    use(monkeypatch)
    assert data.expirations("TST") == ["2026-10-23", "2026-10-30", "2026-11-06"]


def test_expirations_no_chain_raises_data_error(monkeypatch):
    use(monkeypatch, chains=[])
    with pytest.raises(spread_data.DataError, match="TST 没有期权"):
        data.expirations("TST")


def test_chain_rows_puts_use_window_below_spot_two_concurrent_batches_of_20(monkeypatch):
    fake = use(monkeypatch)
    data.last_close("TST")
    fake.qualify_calls.clear()
    fake.subs.clear()
    fake.cancelled.clear()
    fake.max_active = 0
    rows = data.chain_rows("TST", "2026-10-30", "P")
    assert len(fake.qualify_calls) == 1
    qualified = fake.qualify_calls[0]
    assert len(qualified) == 40
    assert all(c.right == "P" and c.lastTradeDateOrContractMonth == "20261030" for c in qualified)
    assert all(85 <= c.strike < 100 for c in qualified)  # +/-15% of spot 100, puts below
    assert fake.max_active == 40 and len(fake.subs) == 40  # two batches share one polling round
    assert len(fake.cancelled) == len(fake.subs) and not fake.active
    assert len(rows) == 40 and {"strike", "iv", "delta"} <= set(rows[0])


def test_chain_rows_includes_requested_itm_strike(monkeypatch):
    fake = use(monkeypatch)
    data.last_close("TST")
    fake.qualify_calls.clear()
    fake.subs.clear()
    rows = data.chain_rows("TST", "2026-10-30", "P", strikes=(105.0,))
    qualified = fake.qualify_calls[0]
    assert 105.0 in [c.strike for c in qualified] and len(qualified) == 41
    assert 105.0 in [c.strike for c in fake.subs]
    assert 105.0 in [r["strike"] for r in rows]
    assert [c.strike for c in qualified] == sorted(c.strike for c in qualified)


def test_chain_rows_ignores_unlisted_extra_strike(monkeypatch):
    fake = use(monkeypatch)
    data.last_close("TST")
    fake.qualify_calls.clear()
    rows = data.chain_rows("TST", "2026-10-30", "P", strikes=(105.1, 999.0))
    assert len(fake.qualify_calls[0]) == 40
    assert 105.1 not in [r["strike"] for r in rows]


def test_collect_rows_never_exceeds_two_batches_of_active_lines(monkeypatch):
    from types import SimpleNamespace as NS
    fake = use(monkeypatch)
    contracts = [NS(strike=float(s), secType="OPT") for s in range(1, 61)]
    rows, quoted = data._collect_rows(fake, contracts)
    assert len(rows) == 60 and quoted == 60
    assert fake.max_active == data.BATCH * data.CONCURRENT_BATCHES == 40
    assert len(fake.cancelled) == 60 and not fake.active


def test_concurrent_group_cancels_every_line_when_subscription_fails(monkeypatch):
    from types import SimpleNamespace as NS
    fake = use(monkeypatch, raise_on_sub=35)
    contracts = [NS(strike=float(s), secType="OPT") for s in range(1, 41)]
    with pytest.raises(ValueError):
        data._collect_rows(fake, contracts)
    assert len(fake.cancelled) == 35 == len(fake.subs) and not fake.active


def test_concurrent_group_fails_fast_and_cancels_all_on_disconnect(monkeypatch):
    from types import SimpleNamespace as NS
    fake = use(monkeypatch, no_greeks=True, disconnect_after=2)
    contracts = [NS(strike=float(s), secType="OPT") for s in range(1, 41)]
    with pytest.raises(sess.GatewayUnavailable):
        data._collect_rows(fake, contracts)
    assert fake.sleeps <= 3
    assert len(fake.cancelled) == 40 and not fake.active


def test_chain_rows_collects_late_arriving_greeks(monkeypatch):
    fake = use(monkeypatch, greeks_delay=6)
    data.last_close("TST")  # probe polling must not pre-warm the window tickers
    before = fake.sleeps
    rows = data.chain_rows("TST", "2026-10-30", "P")
    assert len(rows) == 40  # an immediate single read would see no greeks at all
    assert 6 <= fake.sleeps - before < 12  # one shared polling round, not one per batch


def test_chain_rows_timeout_keeps_holes_and_still_cancels(monkeypatch):
    fake = use(monkeypatch, greeks_holes={95.0})
    rows = data.chain_rows("TST", "2026-10-30", "P")
    held = [r for r in rows if r["strike"] == 95.0]
    assert len(rows) == 40 and held[0]["iv"] is None  # kept, priced, no greeks
    assert len(fake.cancelled) == len(fake.subs) and not fake.active
    assert fake.sleeps >= data.GREEKS_TIMEOUT_SECONDS / data.POLL_SECONDS


def test_chain_rows_returns_priced_rows_without_greeks(monkeypatch):
    use(monkeypatch, no_greeks=True)
    rows = data.chain_rows("TST", "2026-10-30", "P")
    assert len(rows) == 40
    assert all(r["iv"] is None and r["delta"] is None and r["last"] for r in rows)


def test_all_subscriptions_cancelled_when_exception_mid_batch(monkeypatch):
    fake = use(monkeypatch, raise_on_sub=30)  # stock+probe use 2 subs; fails inside window batch 2
    with pytest.raises(ValueError):
        data.chain_rows("TST", "2026-10-30", "P")
    assert len(fake.cancelled) == len(fake.subs) and not fake.active


def test_unqualifiable_window_contracts_are_dropped(monkeypatch):
    fake = use(monkeypatch, unqualifiable={95.0, 95.25})
    data.last_close("TST")
    fake.subs.clear()
    rows = data.chain_rows("TST", "2026-10-30", "P")
    strikes = [r["strike"] for r in rows]
    assert 95.0 not in strikes and 95.25 not in strikes and len(rows) == 38
    assert all(c.conId for c in fake.subs)


def test_chain_rows_empty_includes_competing_session_hint_when_10197(monkeypatch):
    use(monkeypatch, no_data=True, errors=[10091, 354, 10197])
    with pytest.raises(spread_data.DataError) as ei:
        data.chain_rows("TST", "2026-10-30", "P")
    msg = str(ei.value)
    assert "没有拿到 TST 2026-10-30 的期权报价" in msg
    assert sess.COMPETING_SESSION_HINT in msg


def test_chain_rows_empty_without_10197_mentions_closed_market(monkeypatch):
    use(monkeypatch, no_data=True, errors=[10091, 354])
    with pytest.raises(spread_data.DataError) as ei:
        data.chain_rows("TST", "2026-10-30", "P")
    assert "休市" in str(ei.value)
    assert sess.COMPETING_SESSION_HINT not in str(ei.value)


def test_competing_session_does_not_taint_next_symbol(monkeypatch):
    fake = use(monkeypatch, no_data=True, errors=[10197])
    with pytest.raises(spread_data.DataError) as first:
        data.chain_rows("AAA", "2026-10-30", "P")
    assert sess.COMPETING_SESSION_HINT in str(first.value)
    with pytest.raises(spread_data.DataError) as second:
        data.chain_rows("BBB", "2026-10-30", "P")
    assert sess.COMPETING_SESSION_HINT not in str(second.value)


def test_error_handler_registered_once_per_ib(monkeypatch):
    fake = use(monkeypatch)
    for _ in range(3):
        data.reset_memo()
        data.expirations("TST")
    assert len(fake.errorEvent.handlers) == 1


def test_last_close_prefers_und_price_over_market_price(monkeypatch):
    fake = use(monkeypatch, und=101.5, stock_close=99.0, greeks_delay=4)
    d, price = data.last_close("TST")
    assert price == 101.5  # waited for undPrice; decoy marketPrice() is 1.0
    assert d == date(2026, 10, 21)
    assert fake.sleeps >= 4 and len(fake.cancelled) == len(fake.subs)


def test_last_close_waits_for_stock_close(monkeypatch):
    use(monkeypatch, no_greeks=True, stock_close=99.0, stock_delay=3)
    assert data.last_close("TST")[1] == 99.0


def test_last_close_falls_back_to_close_then_fails(monkeypatch):
    use(monkeypatch, no_greeks=True, stock_close=99.0)
    assert data.last_close("TST")[1] == 99.0
    data.reset_memo()
    use(monkeypatch, no_greeks=True, stock_close=NAN)
    with pytest.raises(spread_data.DataError):
        data.last_close("TST")


def test_probe_tries_next_strikes_until_one_qualifies(monkeypatch):
    fake = use(monkeypatch, und=101.5, unqualifiable={100.0, 99.75, 100.25})
    assert data.last_close("TST")[1] == 101.5
    probes = [c for call in fake.qualify_calls for c in call if getattr(c, "right", "") == "P"]
    assert len(probes) == 4


def test_probe_gives_up_after_five_and_uses_stock_close(monkeypatch):
    near = {99.0, 98.75, 99.25, 98.5, 99.5}  # the five nearest to the stock close
    fake = use(monkeypatch, und=101.5, stock_close=99.0, unqualifiable=near)
    assert data.last_close("TST")[1] == 99.0
    probes = [c for call in fake.qualify_calls for c in call if getattr(c, "right", "") == "P"]
    assert len(probes) == 5


BAR_20 = SimpleNamespace(date=date(2026, 10, 20), close=1)
BAR_21 = SimpleNamespace(date=date(2026, 10, 21), close=1)
# 2026-10-22 02:00 UTC is 2026-10-21 22:00 in New York
TIME_NY_21 = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)
TIME_NY_22 = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("bars,ticker_time,expected", [
    ([BAR_20, BAR_21], None, date(2026, 10, 21)),
    ([SimpleNamespace(date=datetime(2026, 10, 20, 0, 0), close=1)], None, date(2026, 10, 20)),
    ([BAR_20], TIME_NY_22, date(2026, 10, 20)),  # both present and different: bar wins
    ([], TIME_NY_21, date(2026, 10, 21)),  # no bars: ticker time in New York
    ([], TIME_NY_22, date(2026, 10, 22)),
])
def test_snapshot_date_uses_historical_bar_then_ticker_time(monkeypatch, bars, ticker_time, expected):
    use(monkeypatch, bars=bars, ticker_time=ticker_time)
    assert data.last_close("TST")[0] == expected


def test_snapshot_date_unavailable_raises(monkeypatch):
    use(monkeypatch, bars=[], ticker_time=None)
    with pytest.raises(spread_data.DataError, match="取不到交易日"):
        data.last_close("TST")


def test_mid_call_drop_maps_to_gateway_unavailable(monkeypatch):
    fake = use(monkeypatch)
    fake.qualify_error = ValueError("socket closed")
    fake.connected = False
    with pytest.raises(sess.GatewayUnavailable) as ei:
        data.expirations("TST")
    assert isinstance(ei.value.__cause__, ValueError)


def test_connection_error_maps_even_if_still_connected(monkeypatch):
    fake = use(monkeypatch)
    fake.qualify_error = ConnectionResetError("reset")
    with pytest.raises(sess.GatewayUnavailable):
        data.expirations("TST")


def test_drop_during_polling_raises_gateway_unavailable_fast(monkeypatch):
    fake = use(monkeypatch, no_greeks=True, disconnect_after=3)
    with pytest.raises(sess.GatewayUnavailable) as ei:
        data.chain_rows("TST", "2026-10-30", "P")
    assert str(ei.value) == sess.DROPPED_HINT
    assert fake.sleeps <= 4  # did not wait out the 10 s timeout
    assert len(fake.cancelled) == len(fake.subs) and not fake.active


def test_data_error_on_dead_link_becomes_gateway_unavailable(monkeypatch):
    fake = use(monkeypatch, chains=[])
    fake.connected = False
    with pytest.raises(sess.GatewayUnavailable):
        data.expirations("TST")


def test_nested_guard_does_not_rewrap_gateway_unavailable(monkeypatch):
    fake = use(monkeypatch, no_greeks=True, disconnect_after=3)
    with pytest.raises(sess.GatewayUnavailable) as ei:
        data.chain_rows("TST", "2026-10-30", "P")  # last_close (inner guard) raises first
    assert ei.value.__cause__ is None
    assert str(ei.value) == sess.DROPPED_HINT


def test_other_errors_stay_raw_when_connected(monkeypatch):
    fake = use(monkeypatch)
    fake.qualify_error = ValueError("bad")
    with pytest.raises(ValueError):
        data.expirations("TST")


def stock_qualifications(fake):
    return [c for call in fake.qualify_calls for c in call if getattr(c, "secType", "") == "STK"]


def test_memo_one_stock_qualification_per_symbol(monkeypatch):
    fake = use(monkeypatch)
    data.expirations("TST")
    data.last_close("TST")
    data.chain_rows("TST", "2026-10-30", "P")
    data.last_close("TST")
    assert len(stock_qualifications(fake)) == 1


def test_reset_memo(monkeypatch):
    fake = use(monkeypatch)
    data.expirations("TST")
    data.reset_memo()
    data.expirations("TST")
    assert len(stock_qualifications(fake)) == 2


def test_no_order_apis_in_ibkr_data():
    pattern = re.compile(r"placeOrder|cancelOrder|reqGlobalCancel|whatIfOrder|MarketOrder|LimitOrder")
    text = (REPO_ROOT / "scripts" / "ibkr_data.py").read_text(encoding="utf-8")
    assert not pattern.search(text)
    assert "reqTickers" not in text.replace("reqTickers returns", "")


def _unit_strikes_fake(monkeypatch, **kw):
    # step-1 strikes so a 25% window is not swallowed by the nearest-N cap
    chain = FakeIB._chain(strikes=[float(s) for s in range(60, 141)])
    fake = use(monkeypatch, chains=[chain], **kw)
    data.last_close("TST")
    fake.qualify_calls.clear()
    fake.subs.clear()
    return fake


def test_chain_rows_default_window_unchanged(monkeypatch):
    fake = _unit_strikes_fake(monkeypatch)
    data.chain_rows("TST", "2026-10-30", "P")
    strikes = [c.strike for c in fake.qualify_calls[0]]
    assert min(strikes) == 85.0 and max(strikes) < 100  # 0.15 window, as before
    fake2 = _unit_strikes_fake(monkeypatch)
    data.chain_rows("TST", "2026-10-30", "P", window_pct=None, window_limit=None)
    assert [c.strike for c in fake2.qualify_calls[0]] == strikes


def test_chain_rows_wider_window_reaches_otm_strike(monkeypatch):
    fake = _unit_strikes_fake(monkeypatch)
    rows = data.chain_rows("TST", "2026-10-30", "P", window_pct=0.25, window_limit=60)
    strikes = [c.strike for c in fake.qualify_calls[0]]
    assert 80.0 in strikes and 75.0 in strikes
    assert len(fake.subs) <= 60 and len(rows) == len(strikes)
    assert fake.max_active <= data.BATCH * data.CONCURRENT_BATCHES
    assert not fake.active


def test_chain_rows_window_limit_caps_subscribed_lines(monkeypatch):
    fake = _unit_strikes_fake(monkeypatch)
    data.chain_rows("TST", "2026-10-30", "P", window_pct=0.25, window_limit=10)
    assert len(fake.subs) == 10


def test_chain_strikes_returns_sorted_chain_strikes_without_quotes(monkeypatch):
    chain = FakeIB._chain(strikes=[110.0, 90.0, 100.0])
    fake = use(monkeypatch, chains=[chain])
    assert data.chain_strikes("TST") == [90.0, 100.0, 110.0]
    assert not fake.subs and not fake.qualify_calls[1:]  # read-only: no market-data lines
