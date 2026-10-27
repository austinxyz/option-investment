"""CLI tests for scripts/put_review.py with bull call debit spreads (bull call plan task 6).

Kept apart from test_put_review_cli.py so that file stays near its size budget. Market data
is faked; the ledger lives in tmp_path.

ZZZQ (invented, design section 10): long 100 Call mid 3.20 / short 105 Call mid 1.30,
width 5, debit 1.90, 1 contract. Max profit (5 - 1.90) x 100 = $310, max loss $190.
"""
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ibkr_session  # noqa: E402
import spread_core  # noqa: E402
import spread_data  # noqa: E402
from test_put_review_cli import EXPIRY, TODAY, record, row, run, seed, snap  # noqa: E402

LONG_EXPIRY = "2026-11-20"  # 35 days after TODAY (2026-10-16)


@pytest.fixture
def bc_market(monkeypatch):
    m = {"spot": 100.0, "calls": [row(100.0, 3.20), row(105.0, 1.30)],
         "puts": [row(95.0, 0.40)], "earnings": "no", "ex_div": None, "asked": []}

    def fetch(kind):
        def _f(t, e, strikes=()):
            m["asked"].append((kind, tuple(strikes)))
            chain = m["puts" if kind == "P" else "calls"]
            return [r for r in chain if r["strike"] in strikes]  # only what was asked for
        return _f

    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("P"))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("C"))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


def bc_record(**kw):
    base = {
        "strategy": "bull_call_spread", "ticker": "ZZZQ", "contracts": 1,
        "snapshot_date": "2026-10-09", "expiry": LONG_EXPIRY, "underlying_price": 100.0,
        "suggestion_file": "2026-10-09-ZZZQ-bullcall.md",
        "long_call_strike": 100.0, "short_call_strike": 105.0, "width": 5.0,
        "long_call_mid": 3.20, "short_call_mid": 1.30, "debit": 1.90, "breakeven": 101.90,
        "breakeven_move_pct": 0.019, "max_profit_move_pct": 0.05, "delta_bucket": 0.50,
        "long_call_delta": 0.55, "short_call_delta": 0.33, "long_call_iv": 0.40,
        "short_call_iv": 0.38, "earnings_in_window": False, "status": "open",
        "exit_date": None, "exit_value": None, "exit_underlying": None, "pnl": None,
        "result": None, "fraction_of_max_captured": None,
    }
    base.update(kw)
    return base


def _boom_seller_helpers(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("seller helper used for a bull call")
    for name in ("threshold_flags", "cost_to_close", "unrealized_pnl", "csp_threshold_flags",
                 "close_pnl", "expiry_pnl", "breakeven_win_rate"):
        monkeypatch.setattr(spread_core, name, boom)


def test_neutral_bullcall_does_not_crash_review_run(bc_market, tmp_path):
    seed(tmp_path, bc_record(), record(ticker="ZZZR"),
         bc_record(ticker="ZZZS", expiry="2026-10-16"))
    assert run(tmp_path) == 0
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    assert got["ZZZQ"]["error"] is None and got["ZZZQ"]["advice"] == "hold"
    assert got["ZZZR"]["error"] is None
    assert got["ZZZS"]["advice"] == "await_settlement" and got["ZZZS"]["data_quality"] == "none"
    assert got["ZZZS"]["long_call_strike"] == 100.0 and got["ZZZS"]["delta"] is None


def test_review_requests_two_explicit_strikes(bc_market, tmp_path):
    seed(tmp_path, bc_record())
    assert run(tmp_path) == 0
    assert bc_market["asked"] == [("C", (100.0, 105.0))]
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None and p["data_quality"] == "ok"


def test_review_snapshot_records_bullcall_fields(bc_market, tmp_path, monkeypatch):
    _boom_seller_helpers(monkeypatch)
    seed(tmp_path, bc_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    # mids 3.20 - 1.30 = 1.90 = the debit -> pnl $0, captured 0 of $310
    assert p["strategy"] == "bull_call_spread" and p["data_quality"] == "ok"
    assert p["long_call_strike"] == 100.0 and p["short_call_strike"] == 105.0
    assert p["debit"] == 1.90 and p["width"] == 5.0 and p["spot"] == 100.0
    assert p["value"] == pytest.approx(1.90) and p["pnl"] == 0.0
    assert p["captured"] == pytest.approx(0.0)
    assert p["stop_flag"] is False and p["take_profit_flag"] is False
    assert p["long_call_delta"] == pytest.approx(
        spread_core.call_delta(100.0, 100.0, 0.40, 35), abs=1e-6)
    assert p["delta"] is None and p["ex_div"] is None and p["earnings"] == "no"
    assert p["advice"] == "hold" and p["rule"] == 5
    for legacy in ("credit", "short_strike", "long_strike", "net_credit", "cost_to_close"):
        assert legacy not in p
    md = (tmp_path / "reviews" / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "| bull_call_spread | ZZZQ | 100/105 |" in md


@pytest.mark.parametrize("missing", [100.0, 105.0])
def test_review_bullcall_missing_quote_attention_in_cli(bc_market, tmp_path, missing):
    bc_market["calls"] = [r for r in bc_market["calls"] if r["strike"] != missing]
    seed(tmp_path, bc_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "attention" and p["rule"] == 0 and p["data_quality"] == "none"
    assert p["pnl"] is None and p["captured"] is None and p["value"] is None


def test_review_bullcall_take_profit_close_in_cli(bc_market, tmp_path):
    # long 4.50 / short 1.00 -> value 3.50 -> +$160 >= +$155; 160 / 310 = 0.5161
    bc_market["spot"] = 103.0
    bc_market["calls"] = [row(100.0, 4.50), row(105.0, 1.00)]
    seed(tmp_path, bc_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(160.0) and p["captured"] == pytest.approx(160 / 310)
    assert p["take_profit_flag"] is True and p["advice"] == "close" and p["rule"] == 3


def test_review_bullcall_short_itm_ex_div_in_cli(bc_market, tmp_path):
    # spot 106 > short 105: ex-div looked up; long 6.10 / short 1.20 -> 4.90 -> +$300
    bc_market["spot"] = 106.0
    bc_market["ex_div"] = date(2026, 10, 20)
    bc_market["calls"] = [row(100.0, 6.10), row(105.0, 1.20)]
    seed(tmp_path, bc_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["ex_div"] == "2026-10-20" and p["pnl"] == pytest.approx(300.0)
    assert p["advice"] == "attention" and p["rule"] == 4 and "2026-10-20" in p["reason"]


@pytest.mark.parametrize("spot", [100.0, 105.0])  # below / exactly at the short strike
def test_review_bullcall_ex_div_not_looked_up_unless_short_itm(bc_market, tmp_path,
                                                               monkeypatch, spot):
    def boom(*a, **k):
        raise AssertionError("ex-dividend looked up with the short call not in the money")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", boom)
    bc_market["spot"] = spot
    seed(tmp_path, bc_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None and p["ex_div"] is None and p["delta"] is None


def test_review_bullcall_stale_quote_is_stale_not_error(bc_market, tmp_path):
    bc_market["calls"] = [{**r, "bid": 0, "ask": 0} for r in bc_market["calls"]]
    seed(tmp_path, bc_record(expiry=EXPIRY))
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None and p["data_quality"] == "stale"
    assert p["long_call_delta"] is None and p["delta"] is None
