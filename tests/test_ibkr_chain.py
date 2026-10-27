"""Tests for the pure IBKR chain/quote helpers (no network, no ib_async)."""
from pathlib import Path
from types import SimpleNamespace as NS
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ibkr_chain as ch


def _chain(exchange="SMART", tc="SPY", mult="100", exps=("20261120", "20261218"),
           strikes=(500.0, 505.0, 510.0)):
    return NS(exchange=exchange, tradingClass=tc, multiplier=mult,
              expirations=set(exps), strikes=set(strikes))


def test_select_chain_prefers_trading_class_equal_symbol_and_smart_100():
    big_other = _chain(tc="SPXW", exps=[f"2026{m:02d}20" for m in range(1, 13)],
                       strikes=[float(s) for s in range(300, 700)])
    std = _chain(tc="SPY")
    assert ch.select_chain([big_other, std], "SPY") is std


def test_select_chain_never_picks_two_expiry_group():
    groups = [_chain(exchange="CBOE", tc="SPY")]
    groups += [_chain(tc="2SPY", exps=("20261120", "20261218"), strikes=(500.0, 505.0))]
    std = _chain(tc="SPY", exps=[f"2026{m:02d}20" for m in range(1, 13)],
                 strikes=[float(s) for s in range(400, 600)])
    groups.append(std)
    assert ch.select_chain(groups, "SPY") is std
    # even without the symbol-named group, the standard large one beats 2SPY
    std2 = _chain(tc="SPYW", exps=[f"2026{m:02d}20" for m in range(1, 13)],
                  strikes=[float(s) for s in range(400, 600)])
    assert ch.select_chain([groups[1], std2], "SPY") is std2


def test_select_chain_falls_back_to_largest_group():
    small = _chain(tc="AAA", exps=("20261120",), strikes=(1.0, 2.0))
    large = _chain(tc="BBB", exps=("20261120", "20261218"), strikes=(1.0, 2.0, 3.0))
    assert ch.select_chain([small, large], "SPY") is large


def test_select_chain_none_when_no_smart():
    assert ch.select_chain([_chain(exchange="CBOE"), _chain(mult="10")], "SPY") is None
    assert ch.select_chain([], "SPY") is None


def test_strike_window_puts_below_spot_nearest_first_limited():
    strikes = [float(s) for s in range(80, 121)]
    out = ch.strike_window(strikes, 100.0, "P", pct=0.15, limit=5)
    assert out == [95.0, 96.0, 97.0, 98.0, 99.0]
    assert out == sorted(out)


def test_strike_window_calls_above_and_pct_cap():
    strikes = [float(s) for s in range(80, 140)]
    out = ch.strike_window(strikes, 100.0, "C", pct=0.10, limit=40)
    assert out[0] > 100.0
    assert out[-1] == 110.0
    assert out == sorted(out)
    assert all(100.0 < s <= 110.0 for s in out)


def _ticker(bid=1.0, ask=1.2, last=1.1, close=1.05, strike=95.0, right="P",
            greeks="default"):
    g = NS(impliedVol=0.25, delta=-0.3, undPrice=100.0) if greeks == "default" else greeks
    return NS(contract=NS(strike=strike, right=right), bid=bid, ask=ask, last=last,
              close=close, modelGreeks=g)


def test_ticker_to_row_uses_greeks_and_abs_delta_for_puts():
    row = ch.ticker_to_row(_ticker())
    assert row["strike"] == 95.0
    assert row["bid"] == 1.0 and row["ask"] == 1.2 and row["last"] == 1.1
    assert row["iv"] == 0.25
    assert row["delta"] == 0.3
    assert row["und_price"] == 100.0
    assert row["close"] == 1.05
    assert not row.get("from_close")


def test_ticker_to_row_minus_one_sentinels_become_none():
    row = ch.ticker_to_row(_ticker(bid=-1, ask=float("nan"), last=-1, close=-1))
    assert row["bid"] is None and row["ask"] is None
    assert row["last"] is None and row["close"] is None
    assert not row.get("from_close")


def test_ticker_to_row_falls_back_to_close_and_flags_it():
    row = ch.ticker_to_row(_ticker(bid=-1, ask=-1, last=-1, close=2.5))
    assert row["last"] == 2.5
    assert row["from_close"] is True


def test_ticker_to_row_keeps_priced_row_without_greeks():
    row = ch.ticker_to_row(_ticker(greeks=None))
    assert row["iv"] is None and row["delta"] is None and row["und_price"] is None
    assert row["bid"] == 1.0 and row["ask"] == 1.2
    row = ch.ticker_to_row(_ticker(greeks=NS(impliedVol=0.2, delta=None, undPrice=100.0)))
    assert row["iv"] == 0.2 and row["delta"] is None


def test_ticker_to_row_keeps_delta_when_iv_sentinel():
    row = ch.ticker_to_row(_ticker(greeks=NS(impliedVol=-1, delta=-0.3, undPrice=100.0)))
    assert row["delta"] == 0.3 and row["iv"] is None


def test_ticker_to_row_rejects_bad_delta_sentinels():
    for bad in (float("nan"), float("inf"), -2, "x"):
        row = ch.ticker_to_row(_ticker(greeks=NS(impliedVol=0.25, delta=bad, undPrice=100.0)))
        assert row["delta"] is None and row["iv"] == 0.25
    row = ch.ticker_to_row(_ticker(greeks=NS(impliedVol=0.25, delta=-0.3, undPrice="x")))
    assert row["und_price"] is None


def test_ticker_to_row_close_fallback_for_one_sided_quote():
    row = ch.ticker_to_row(_ticker(bid=-1, ask=0.35, last=-1, close=0.30, greeks=None))
    assert row["last"] == 0.30 and row["from_close"] is True
    assert row["ask"] == 0.35 and row["bid"] is None


def test_ticker_to_row_none_when_no_price_and_no_greeks():
    t = _ticker(bid=-1, ask=-1, last=-1, close=-1, greeks=None)
    assert ch.ticker_to_row(t) is None
    t = _ticker(bid=-1, ask=-1, last=-1, close=-1,
                greeks=NS(impliedVol=-1, delta=float("nan"), undPrice=100.0))
    assert ch.ticker_to_row(t) is None


def test_spot_prefers_und_price_then_close_then_none():
    rows = [{"und_price": 100.0}, {"und_price": 102.0}, {"und_price": None}]
    assert ch.spot_from_rows(rows, 90.0) == 101.0
    outlier = [{"und_price": 100.0}, {"und_price": 101.0}, {"und_price": 150.0},
               {"und_price": -1}]
    assert ch.spot_from_rows(outlier, 90.0) == 101.0  # median, not mean (~117)
    assert ch.spot_from_rows([{"und_price": None}], 90.0) == 90.0
    assert ch.spot_from_rows([], -1) is None


def test_batched():
    assert list(ch.batched(range(5), 2)) == [[0, 1], [2, 3], [4]]
    assert list(ch.batched([], 3)) == []
