"""CLI tests for scripts/put_review.py with risk reversals (rr plan task 5).

Market data is faked; the ledger lives in tmp_path. Invented numbers from design section 8:
spot 100, short 90 Put mid 2.00, long 110 Call mid 1.50, 1 contract -> net_credit 0.50;
take-profit line +$200, stop line -$400.
"""
import hashlib
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ibkr_session  # noqa: E402
import spread_core  # noqa: E402
import spread_data  # noqa: E402
from test_put_review_cli import (TODAY, cc_record, condor_record, lot_record,  # noqa: E402,F401
                                 record, row, run, seed, snap)
from test_put_review_cli_bullcall import LONG_EXPIRY, bc_record  # noqa: E402

PIN_WITH_BULLCALL_MD = "aefbdb935119eff06530af9e62c87458c6cc39c4db05148bcdbf704f155f9d31"
PIN_WITH_BULLCALL_JSON = "e121d4c9b937a232f8ef404aafc7c33a5363b39b36131689f445c7e193a1672e"


@pytest.fixture
def pin_market(monkeypatch):
    """Strike-filtering fake chain with a row for every leg of every older strategy."""
    m = {"spot": 100.0, "asked": [], "earnings": "no", "ex_div": None,
         "puts": [row(95.0, 0.40), row(90.0, 0.50), row(85.0, 0.20)],
         "calls": [row(100.0, 3.20), row(105.0, 1.30), row(110.0, 0.40), row(115.0, 0.10)]}

    def fetch(kind):
        def _f(t, e, strikes=()):
            m["asked"].append((kind, tuple(strikes)))
            return [r for r in m["puts" if kind == "P" else "calls"] if r["strike"] in strikes]
        return _f

    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("P"))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("C"))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


def _existing_ledger():
    """The seven pre-risk-reversal strategies (bull call included) plus an expiring one."""
    return [record(),
            record(strategy="bull_put_spread", ticker="ZZZB", short_strike=95.0,
                   long_strike=90.0, width=5.0, credit=1.50),
            cc_record(ticker="ZZZC", short_strike=105.0),
            lot_record(ticker="ZZZL", lot_ref={"source": "real", "ticker": "ZZZL"}),
            lot_record("protective_put", ticker="ZZZP",
                       lot_ref={"source": "real", "ticker": "ZZZP"}),
            condor_record(), bc_record(),
            record(ticker="ZZZE", expiry="2026-10-16")]


def _digests(tmp_path):
    folder = tmp_path / "reviews"
    out = []
    for suffix in ("md", "json"):
        data = (folder / f"{TODAY.isoformat()}.{suffix}").read_bytes()
        out.append(hashlib.sha256(data).hexdigest())
    return out


def test_review_output_pinned_for_existing_strategies(pin_market, tmp_path):
    seed(tmp_path, *_existing_ledger())
    assert run(tmp_path) == 0
    assert _digests(tmp_path) == [PIN_WITH_BULLCALL_MD, PIN_WITH_BULLCALL_JSON]


# ---- risk reversal -----------------------------------------------------------------

@pytest.fixture
def rr_market(monkeypatch):
    m = {"spot": 100.0, "puts": [row(90.0, 2.00)], "calls": [row(110.0, 1.50)],
         "earnings": "no", "asked": []}

    def fetch(kind):
        def _f(t, e, strikes=()):
            m["asked"].append((kind, tuple(strikes)))
            return [r for r in m["puts" if kind == "P" else "calls"] if r["strike"] in strikes]
        return _f

    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("P"))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("C"))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


def rr_record(**kw):
    base = {
        "strategy": "risk_reversal", "ticker": "ZZZQ", "contracts": 1,
        "snapshot_date": "2026-10-09", "expiry": LONG_EXPIRY, "underlying_price": 100.0,
        "suggestion_file": "2026-10-09-ZZZQ-rr.md",
        "short_put_strike": 90.0, "long_call_strike": 110.0, "short_put_mid": 2.00,
        "long_call_mid": 1.50, "net_credit": 0.50, "breakeven_down": 89.50,
        "put_move_pct": -0.10, "call_move_pct": 0.10, "delta_bucket": 0.20,
        "short_put_delta": 0.20, "long_call_delta": 0.18, "short_put_iv": 0.40,
        "long_call_iv": 0.38, "earnings_in_window": False, "status": "open",
        "exit_date": None, "exit_underlying": None, "pnl": None, "result": None,
        "outcome_zone": None,
    }
    base.update(kw)
    return base


STUBBED_MONEY_NAMES = (
    "threshold_flags", "cost_to_close", "unrealized_pnl", "csp_threshold_flags", "close_pnl",
    "expiry_pnl", "breakeven_win_rate", "bullcall_close_value", "bullcall_pnl",
    "bullcall_threshold_flags", "bullcall_fraction_captured", "bullcall_expiry_pnl",
    "bullcall_required_win_rate")


def _boom_other_strategies(monkeypatch):
    import put_review
    import put_review_rules

    def boom(*a, **k):
        raise AssertionError("another strategy's helper used for a risk reversal")
    for name in STUBBED_MONEY_NAMES:
        if hasattr(spread_core, name):
            monkeypatch.setattr(spread_core, name, boom)
    monkeypatch.setattr(put_review, "_measure_bullcall", boom)
    monkeypatch.setattr(put_review, "_measure_condor", boom)
    monkeypatch.setattr(put_review_rules, "review_bullcall", boom)
    monkeypatch.setattr(put_review_rules, "review_condor", boom)
    monkeypatch.setattr(put_review_rules, "_itm_reason", boom)


def test_neutral_rr_does_not_crash_review_run(rr_market, tmp_path):
    seed(tmp_path, rr_record(), rr_record(ticker="ZZZS", expiry="2026-10-16"))
    assert run(tmp_path) == 0
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    assert got["ZZZQ"]["error"] is None and got["ZZZQ"]["advice"] == "hold"
    assert got["ZZZS"]["advice"] == "await_settlement" and got["ZZZS"]["data_quality"] == "none"
    assert got["ZZZS"]["short_put_strike"] == 90.0 and got["ZZZS"]["delta"] is None


def test_review_requests_each_leg_on_its_own_chain(rr_market, tmp_path):
    seed(tmp_path, rr_record())
    assert run(tmp_path) == 0
    assert ("P", (90.0,)) in rr_market["asked"] and ("C", (110.0,)) in rr_market["asked"]
    assert len(rr_market["asked"]) == 2


def test_review_snapshot_records_every_number(rr_market, tmp_path, monkeypatch):
    _boom_other_strategies(monkeypatch)
    seed(tmp_path, rr_record())
    assert run(tmp_path) == 0
    doc = snap(tmp_path)
    (p,) = doc["positions"]
    assert p["strategy"] == "risk_reversal" and p["data_quality"] == "ok"
    assert p["spot"] == 100.0 and p["put_mid"] == pytest.approx(2.00)
    assert p["call_mid"] == pytest.approx(1.50) and p["net_credit"] == 0.50
    assert p["pnl"] == pytest.approx(0.0)  # exit_net 0.50 == net_credit 0.50
    put_delta = spread_core.put_delta(100.0, 90.0, 0.40, 35)
    assert p["short_put_delta"] == pytest.approx(put_delta, abs=1e-6)
    assert p["delta"] == p["short_put_delta"]
    assert p["long_call_delta"] == pytest.approx(
        spread_core.call_delta(100.0, 110.0, 0.40, 35), abs=1e-6)
    assert p["earnings"] == "no" and p["stop_flag"] is False and p["take_profit_flag"] is False
    assert p["advice"] == "hold" and p["rule"] == 5 and p["reason"] == "继续持有"
    assert doc["rules"]["ATTENTION_DELTA"] == 0.35
    for legacy in ("credit", "short_strike", "long_strike", "debit", "cost_to_close"):
        assert legacy not in p
    md = (tmp_path / "reviews" / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "| risk_reversal | ZZZQ | 90/110 |" in md


@pytest.mark.parametrize("kind", ["puts", "calls"])
def test_review_rr_missing_leg_is_rule0_not_crash(rr_market, tmp_path, kind):
    rr_market[kind] = []
    seed(tmp_path, rr_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert (p["advice"], p["rule"], p["data_quality"]) == ("attention", 0, "none")
    assert p["pnl"] is None and p["spot"] is None


def test_review_rr_put_in_the_money_attention(rr_market, tmp_path):
    rr_market["spot"] = 85.0
    rr_market["puts"] = [row(90.0, 6.00)]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert (p["advice"], p["rule"]) == ("attention", 2) and "被指派" in p["reason"]


def test_review_rr_take_profit_boundary_plus_200(rr_market, tmp_path):
    # pnl = (0.50 - (put - call)) x 100: +200 needs put - call = -1.50
    rr_market["puts"], rr_market["calls"] = [row(90.0, 1.00)], [row(110.0, 2.50)]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(200.0) and p["take_profit_flag"] is True
    assert (p["advice"], p["rule"]) == ("close", 3)


def test_review_rr_just_under_take_profit_holds(rr_market, tmp_path):
    rr_market["puts"], rr_market["calls"] = [row(90.0, 1.00)], [row(110.0, 2.49)]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(199.0) and p["take_profit_flag"] is False
    assert p["rule"] == 5


def test_review_rr_stop_boundary_minus_400(rr_market, tmp_path):
    # pnl -400 needs put - call = 4.50; spot stays above the put strike
    rr_market["puts"], rr_market["calls"] = [row(90.0, 5.00)], [row(110.0, 0.50)]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(-400.0) and p["stop_flag"] is True
    assert (p["advice"], p["rule"]) == ("attention", 2)
    rr_market["puts"] = [row(90.0, 4.99)]
    run(tmp_path)
    (q,) = snap(tmp_path)["positions"]
    assert q["pnl"] == pytest.approx(-399.0) and q["stop_flag"] is False and q["rule"] == 5


def test_review_rr_scales_flags_with_contracts(rr_market, tmp_path):
    # 2 contracts: the take-profit line is +$400, reached by the same quotes as +$200 for one
    rr_market["puts"], rr_market["calls"] = [row(90.0, 1.00)], [row(110.0, 2.50)]
    seed(tmp_path, rr_record(contracts=2))
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(400.0) and p["take_profit_flag"] is True


def test_review_rr_earnings_attention(rr_market, tmp_path):
    rr_market["earnings"] = "yes"
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert (p["advice"], p["rule"]) == ("attention", 4) and "财报" in p["reason"]


def test_review_rr_delta_at_threshold_is_inclusive(rr_market, tmp_path):
    rr_market["puts"] = [{**row(90.0, 2.00), "delta": -0.35}]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["short_put_delta"] == pytest.approx(0.35)
    assert (p["advice"], p["rule"]) == ("attention", 4)


def test_review_rr_stale_quote_ignores_delta(rr_market, tmp_path):
    rr_market["puts"] = [{**row(90.0, 2.00), "bid": 0, "ask": 0}]
    rr_market["calls"] = [{**row(110.0, 1.50), "bid": 0, "ask": 0}]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None and p["data_quality"] == "stale"
    assert p["short_put_delta"] is None and p["long_call_delta"] is None and p["delta"] is None
    assert p["reason"].endswith("数据陈旧，Delta 未使用")


def test_review_rr_long_call_without_iv_has_none_delta_but_trusted_data(rr_market, tmp_path):
    rr_market["calls"] = [row(110.0, 1.50, iv=None)]
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["data_quality"] == "ok" and p["long_call_delta"] is None
    assert p["short_put_delta"] is not None and p["rule"] == 5


def test_review_rr_unknown_earnings_suffix(rr_market, tmp_path):
    rr_market["earnings"] = "unknown"
    seed(tmp_path, rr_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["reason"] == "继续持有；财报日期未知"


def test_one_bad_rr_record_does_not_break_the_batch(rr_market, tmp_path, monkeypatch):
    real = spread_data.fetch_puts

    def flaky(t, e, strikes=()):
        if t == "ZZZBAD":
            raise RuntimeError("坏数据")
        return real(t, e, strikes)
    monkeypatch.setattr(spread_data, "fetch_puts", flaky)
    seed(tmp_path, rr_record(ticker="ZZZBAD"), rr_record(ticker="ZZZOK"))
    assert run(tmp_path) == 0
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    assert got["ZZZBAD"]["error"] == "坏数据" and got["ZZZBAD"]["rule"] == 0
    assert got["ZZZOK"]["error"] is None and got["ZZZOK"]["advice"] == "hold"


def test_rr_review_never_reaches_other_strategy_helpers(rr_market, tmp_path, monkeypatch):
    _boom_other_strategies(monkeypatch)
    rr_market["earnings"] = "yes"
    seed(tmp_path, rr_record(), rr_record(ticker="ZZZE", expiry="2026-10-16"))
    assert run(tmp_path) == 0
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    assert got["ZZZQ"]["error"] is None and got["ZZZE"]["rule"] == 1


def test_review_existing_rows_identical_with_a_risk_reversal_present(pin_market, tmp_path):
    seed(tmp_path, *_existing_ledger())
    assert run(tmp_path) == 0
    base = snap(tmp_path)["positions"]
    pin_market["calls"].append(row(120.0, 1.50))
    other = tmp_path / "with_rr"
    other.mkdir()
    seed(other, *_existing_ledger(),
         rr_record(ticker="ZZZK", short_put_strike=90.0, long_call_strike=120.0))
    assert run(other) == 0
    with_rr = snap(other)["positions"]
    assert with_rr[:-1] == base and with_rr[-1]["strategy"] == "risk_reversal"
