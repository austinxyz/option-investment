"""CLI tests for scripts/put_review.py. Market data is faked; the ledger lives in tmp_path."""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ibkr_session
import spread_core
import spread_data
import spread_ledger
import put_review
from spread_fixtures import make_chain

TODAY = date(2026, 10, 16)
EXPIRY = "2026-10-23"


def row(strike, mid, iv=0.40):
    return {"strike": strike, "bid": mid - 0.01, "ask": mid + 0.01, "last": mid, "iv": iv}


@pytest.fixture
def market(monkeypatch):
    m = {"spot": 100.0, "puts": make_chain(spot=100.0), "earnings": "no", "open": False,
         "gateway": None}

    def ensure_gateway():
        if m["gateway"] is not None:
            raise m["gateway"]

    monkeypatch.setattr(ibkr_session, "ensure_gateway", ensure_gateway)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=(): m["puts"])
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: m["open"])
    return m


def record(**kw):
    base = {
        "strategy": "cash_secured_put", "ticker": "ABC", "contracts": 1,
        "snapshot_date": "2026-10-09", "expiry": EXPIRY, "underlying_price": 100.0,
        "short_strike": 95.0, "long_strike": None, "width": None, "credit": 1.00,
        "status": "open", "exit_date": None, "exit_cost": None, "pnl": None, "result": None,
    }
    base.update(kw)
    return base


def seed(tmp_path, *records):
    led = spread_ledger.empty_ledger()
    for r in records:
        led, _ = spread_ledger.add_spread(led, r)
    spread_ledger.save(tmp_path / "spreads.json", led)


def run(tmp_path, today=TODAY):
    return put_review.main(["--ledger-dir", str(tmp_path)], today=today)


def snap(tmp_path, today=TODAY):
    return json.loads((tmp_path / "reviews" / f"{today.isoformat()}.json").read_text(encoding="utf-8"))


def test_review_cash_secured_put_close_when_mostly_captured(market, tmp_path):
    market["puts"] = [row(95.0, 0.15)]
    seed(tmp_path, record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["captured"] == pytest.approx(0.85)
    assert p["advice"] == "close" and p["rule"] == 3


def test_review_spread_uses_short_leg_delta(market, tmp_path):
    market["puts"] = make_chain(spot=100.0, iv=0.40, days=7)
    seed(tmp_path, record(strategy="bull_put_spread", short_strike=95.0, long_strike=90.0,
                          width=5.0, credit=1.50))
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    expected = spread_core.put_delta(100.0, 95.0, 0.40, 7)
    assert p["delta"] == pytest.approx(expected, abs=1e-6)
    assert p["data_quality"] == "ok" and p["long_strike"] == 90.0


def test_review_itm_is_attention(market, tmp_path):
    market["spot"] = 90.0
    market["puts"] = [row(95.0, 5.5)]
    seed(tmp_path, record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "attention" and p["rule"] == 2


def test_review_stale_quotes_do_not_use_delta(market, tmp_path, capsys):
    market["puts"] = make_chain(spot=100.0, stale=True)
    seed(tmp_path, record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["data_quality"] == "stale" and p["delta"] is None
    assert snap(tmp_path)["snapshot_kind"] == "陈旧"
    assert "数据陈旧，Delta 未使用" in capsys.readouterr().out
    assert "数据陈旧，Delta 未使用" in (tmp_path / "reviews" / f"{TODAY}.md").read_text(encoding="utf-8")


def test_review_missing_leg_quote_is_attention_not_crash(market, tmp_path, capsys):
    market["puts"] = [row(80.0, 0.05)]
    seed(tmp_path, record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "attention" and "取不到报价" in p["reason"]
    assert "取不到报价" in capsys.readouterr().out


def test_review_one_bad_position_does_not_abort(market, tmp_path, monkeypatch):
    def fetch(t, e, strikes=()):
        if t == "BAD":
            raise spread_data.DataError("网络挂了")
        return market["puts"]
    monkeypatch.setattr(spread_data, "fetch_puts", fetch)
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record(ticker="BAD"), record(ticker="OK"))
    run(tmp_path)
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    assert got["BAD"]["advice"] == "attention" and got["OK"]["error"] is None


def test_review_expired_position_makes_no_network_call(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("network call")
    for name in ("last_close", "fetch_puts", "earnings_status"):
        monkeypatch.setattr(spread_data, name, boom)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    seed(tmp_path, record(expiry="2026-10-16"))
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "await_settlement" and p["days_left"] == 0


def test_review_no_open_positions_writes_nothing(tmp_path, capsys):
    assert run(tmp_path) == 0
    assert "无未平仓" in capsys.readouterr().out
    seed(tmp_path, record(status="closed_early"))
    assert run(tmp_path) == 0
    assert "无未平仓" in capsys.readouterr().out
    assert not (tmp_path / "reviews").exists()


def test_review_snapshot_has_ids_rules_and_thresholds(market, tmp_path):
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    doc = snap(tmp_path)
    assert doc["version"] == 1 and doc["generated_on"] == "2026-10-16"
    assert doc["snapshot_kind"] == "收盘"
    assert doc["rules"]["CLOSE_CAPTURED"] == 0.80 and "ATTENTION_DELTA" in doc["rules"]
    (p,) = doc["positions"]
    for key in ("id", "strategy", "ticker", "expiry", "advice", "rule", "reason", "data_quality"):
        assert key in p
    assert p["id"] == 1 and p["strategy"] == "cash_secured_put"


def test_review_intraday_snapshot_kind(market, tmp_path):
    market["open"] = True
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    assert snap(tmp_path)["snapshot_kind"] == "盘中"


def test_review_same_day_rerun_overwrites_other_day_keeps_both(market, tmp_path):
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    run(tmp_path, today=date(2026, 10, 17))
    assert sorted(p.name for p in (tmp_path / "reviews").glob("*.json")) == [
        "2026-10-16.json", "2026-10-17.json"]
    market["puts"] = [row(95.0, 0.1)]
    run(tmp_path)
    assert snap(tmp_path)["positions"][0]["advice"] == "close"


def test_review_never_modifies_ledger(market, tmp_path):
    market["puts"] = [row(95.0, 0.15)]
    seed(tmp_path, record())
    before = (tmp_path / "spreads.json").read_bytes()
    run(tmp_path)
    assert (tmp_path / "spreads.json").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reviews", "spreads.json"]


def test_review_exactly_80_percent_float_noise_closes(market, tmp_path):
    market["puts"] = [row(95.0, 0.30)]
    seed(tmp_path, record(credit=1.50))
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "close"


def test_review_no_data_rows_are_quality_none(market, tmp_path, monkeypatch):
    def boom(t, e, strikes=()):
        raise spread_data.DataError("网络挂了")
    monkeypatch.setattr(spread_data, "fetch_puts", boom)
    seed(tmp_path, record())
    run(tmp_path)
    doc = snap(tmp_path)
    assert doc["positions"][0]["data_quality"] == "none"
    assert doc["snapshot_kind"] == "陈旧"


def test_review_nan_spot_is_error_row(market, tmp_path):
    market["spot"] = float("nan")
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    text = (tmp_path / "reviews" / f"{TODAY}.json").read_text(encoding="utf-8")
    assert "NaN" not in text
    (p,) = json.loads(text)["positions"]
    assert p["advice"] == "attention" and "现价无效" in p["reason"]


def test_review_unknown_earnings_noted_in_reason(market, tmp_path):
    market["earnings"] = "unknown"
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    assert "财报日期未知" in snap(tmp_path)["positions"][0]["reason"]


def test_review_itm_reason_names_strategy(market, tmp_path):
    market["spot"] = 90.0
    market["puts"] = [row(95.0, 5.5), row(90.0, 2.0)]
    seed(tmp_path, record(strategy="bull_put_spread", long_strike=90.0, width=5.0, credit=1.5))
    run(tmp_path)
    assert "最大亏损" in snap(tmp_path)["positions"][0]["reason"]


def _break_quotes(monkeypatch):
    def boom(t, e, strikes=()):
        raise spread_data.DataError("网络挂了")
    monkeypatch.setattr(spread_data, "fetch_puts", boom)


def test_review_worse_same_day_run_keeps_good_snapshot(market, tmp_path, monkeypatch, capsys):
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    good = (tmp_path / "reviews" / f"{TODAY}.json").read_bytes()
    good_md = (tmp_path / "reviews" / f"{TODAY}.md").read_bytes()
    _break_quotes(monkeypatch)
    capsys.readouterr()
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "保留原快照，没有覆盖" in out and "ABC" in out
    assert (tmp_path / "reviews" / f"{TODAY}.json").read_bytes() == good
    assert (tmp_path / "reviews" / f"{TODAY}.md").read_bytes() == good_md


def _stale_iv(market):
    market["puts"] = [row(95.0, 0.5, iv=None)]


def test_review_stale_same_day_run_keeps_ok_snapshot(market, tmp_path, capsys):
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    assert snap(tmp_path)["positions"][0]["data_quality"] == "ok"
    good = (tmp_path / "reviews" / f"{TODAY}.json").read_bytes()
    _stale_iv(market)
    capsys.readouterr()
    assert run(tmp_path) == 0
    assert "保留原快照，没有覆盖" in capsys.readouterr().out
    assert (tmp_path / "reviews" / f"{TODAY}.json").read_bytes() == good


def test_review_ok_same_day_run_overwrites_stale_snapshot(market, tmp_path):
    _stale_iv(market)
    seed(tmp_path, record())
    run(tmp_path)
    assert snap(tmp_path)["positions"][0]["data_quality"] == "stale"
    market["puts"] = [row(95.0, 0.5)]
    run(tmp_path)
    assert snap(tmp_path)["positions"][0]["data_quality"] == "ok"


def test_review_equal_quality_same_day_run_overwrites(market, tmp_path):
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record())
    run(tmp_path)
    market["puts"] = [row(95.0, 0.40)]
    run(tmp_path)
    assert snap(tmp_path)["positions"][0]["cost_to_close"] == pytest.approx(0.40)


def test_review_no_data_run_overwrites_when_existing_has_no_data(market, tmp_path, monkeypatch):
    _break_quotes(monkeypatch)
    seed(tmp_path, record())
    run(tmp_path)
    (tmp_path / "reviews" / f"{TODAY}.json").write_text(
        json.dumps({"positions": [{"data_quality": "none"}]}), encoding="utf-8")
    run(tmp_path)
    assert snap(tmp_path)["version"] == 1


def test_review_no_data_run_overwrites_corrupt_existing(market, tmp_path, monkeypatch):
    _break_quotes(monkeypatch)
    seed(tmp_path, record())
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / f"{TODAY}.json").write_text("{not json", encoding="utf-8")
    assert run(tmp_path) == 0
    assert snap(tmp_path)["version"] == 1


@pytest.fixture
def call_market(monkeypatch):
    m = {"spot": 100.0, "calls": [row(105.0, 0.5)], "earnings": "no"}
    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)  # never the real gateway
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(): m["calls"])
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


def cc_record(**kw):
    return record(**{"strategy": "covered_call", "short_strike": 105.0, "credit": 1.00, **kw})


def test_review_covered_call_otm_holds(call_market, tmp_path):
    seed(tmp_path, cc_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["itm"] is False and p["advice"] == "hold" and p["rule"] == 5
    assert p["captured"] == pytest.approx(0.5)


def test_review_covered_call_itm_needs_attention(call_market, tmp_path):
    call_market["spot"] = 105.0
    call_market["calls"] = [row(105.0, 3.0)]
    seed(tmp_path, cc_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["itm"] is True and p["advice"] == "attention" and p["rule"] == 2
    assert "被行权" in p["reason"]


def test_review_covered_call_uses_call_delta(call_market, tmp_path):
    call_market["spot"] = 100.0
    call_market["calls"] = [row(102.0, 0.9, iv=0.40)]
    seed(tmp_path, cc_record(short_strike=102.0))
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    expected = spread_core.call_delta(100.0, 102.0, 0.40, 7)
    assert p["delta"] == pytest.approx(expected, abs=1e-6) and expected >= 0.35
    assert p["advice"] == "attention" and p["rule"] == 4


def test_review_snapshot_records_covered_call_strategy(call_market, tmp_path):
    seed(tmp_path, cc_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["strategy"] == "covered_call" and p["long_strike"] is None


def test_review_trusts_frozen_quote_with_broker_delta(market, tmp_path, capsys):
    chain = make_chain(spot=100.0, iv=0.40, days=7, stale=True)
    market["puts"] = [{**r, "delta": 0.18, "from_close": True} for r in chain]
    seed(tmp_path, record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["data_quality"] == "ok" and p["delta"] == pytest.approx(0.18)
    assert "数据陈旧，Delta 未使用" not in capsys.readouterr().out


def test_review_stale_without_broker_delta_still_skips_delta(market, tmp_path):
    market["puts"] = make_chain(spot=100.0, iv=0.40, days=7, stale=True)
    seed(tmp_path, record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["data_quality"] == "stale" and p["delta"] is None


def test_review_frozen_quote_with_broker_delta_and_no_iv_is_trusted(market, tmp_path):
    chain = make_chain(spot=100.0, iv=0.40, days=7, stale=True)
    market["puts"] = [{**r, "iv": None, "delta": 0.18, "from_close": True} for r in chain]
    seed(tmp_path, record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["data_quality"] == "ok" and p["delta"] == pytest.approx(0.18)


DOWN = ibkr_session.GatewayUnavailable("连不上 gw.test:14999：连接被拒绝")


def test_review_aborts_before_writing_when_gateway_down(market, tmp_path, capsys):
    market["gateway"] = DOWN
    seed(tmp_path, record())
    assert run(tmp_path) == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert not (tmp_path / "reviews").exists()


def test_review_gateway_error_mid_run_is_not_a_per_position_error(market, tmp_path, monkeypatch, capsys):
    def fetch(t, e, strikes=()):
        if t == "BAD":
            raise DOWN
        return market["puts"]
    monkeypatch.setattr(spread_data, "fetch_puts", fetch)
    market["puts"] = [row(95.0, 0.5)]
    seed(tmp_path, record(ticker="OK"), record(ticker="BAD"))
    assert run(tmp_path) == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert not (tmp_path / "reviews").exists()


def test_review_all_expired_needs_no_gateway(market, tmp_path):
    market["gateway"] = DOWN
    seed(tmp_path, record(expiry="2026-10-16"))
    assert run(tmp_path) == 0


def test_measure_requests_leg_strikes(market, tmp_path, monkeypatch):
    seen = []

    def fetch(t, e, strikes=()):
        seen.append(tuple(strikes))
        return market["puts"]
    monkeypatch.setattr(spread_data, "fetch_puts", fetch)
    market["puts"] = make_chain(spot=100.0)
    seed(tmp_path, record(strategy="bull_put_spread", short_strike=95.0, long_strike=90.0,
                          width=5.0, credit=1.50),
         record(ticker="XYZ", short_strike=96.0))
    run(tmp_path)
    assert set(seen[0]) == {95.0, 90.0}
    assert set(seen[1]) == {96.0}


def test_review_greekless_short_leg_is_stale_not_error(market, tmp_path):
    market["puts"] = [{"strike": 95.0, "bid": 0.49, "ask": 0.51, "last": 0.5,
                       "iv": None, "delta": None}]
    seed(tmp_path, record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None and p["data_quality"] == "stale" and p["delta"] is None


# ---- collar / protective put (task 6) -----------------------------------------------

@pytest.fixture
def lot_market(monkeypatch):
    m = {"spot": 100.0, "puts": [row(90.0, 1.0)], "calls": [row(110.0, 1.5)], "ex_div": None,
         "asked": []}

    def fetch(kind):
        def _f(t, e, strikes=()):
            m["asked"].append((kind, tuple(strikes)))
            return m["puts" if kind == "P" else "calls"]
        return _f

    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("P"))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("C"))
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


def lot_record(strategy="collar", **kw):
    base = {
        "strategy": strategy, "ticker": "ZZZQ", "contracts": 2, "snapshot_date": "2026-10-09",
        "expiry": EXPIRY, "put_strike": 90.0, "put_debit": 1.2, "call_strike": 110.0,
        "call_credit": 1.8, "net_credit": 0.6, "covered_shares": 200, "cost_basis": 80.0,
        "reference_price": 80.0, "lot_ref": {"source": "real", "ticker": "ZZZQ"},
        "status": "open", "exit_date": None, "pnl": None, "result": None,
    }
    if strategy == "protective_put":
        base.update(call_strike=None, call_credit=0.0, net_credit=-1.2)
    base.update(kw)
    return base


def test_review_collar_fetches_both_legs_and_holds(lot_market, tmp_path):
    seed(tmp_path, lot_record(expiry="2026-11-20"))
    assert run(tmp_path) == 0
    assert ("P", (90.0,)) in lot_market["asked"] and ("C", (110.0,)) in lot_market["asked"]
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "hold" and p["rule"] == 6


def test_review_collar_call_itm_in_cli_needs_attention(lot_market, tmp_path):
    lot_market["spot"] = 111.0
    seed(tmp_path, lot_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "attention" and p["rule"] == 2


def test_review_collar_ex_div_in_cli(lot_market, tmp_path):
    lot_market["spot"] = 111.0
    lot_market["ex_div"] = date(2026, 10, 20)
    seed(tmp_path, lot_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["rule"] == 3 and "2026-10-20" in p["reason"]


def test_review_collar_missing_leg_quote_is_attention(lot_market, tmp_path):
    lot_market["calls"] = []
    seed(tmp_path, lot_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "attention" and p["rule"] == 0 and p["data_quality"] == "none"


def test_review_snapshot_records_new_strategies(lot_market, tmp_path):
    seed(tmp_path, lot_record(),
         lot_record("protective_put", ticker="ZZZP", lot_ref={"source": "real", "ticker": "ZZZP"}))
    assert run(tmp_path) == 0
    collar, pput = snap(tmp_path)["positions"]
    assert collar["strategy"] == "collar" and collar["put_strike"] == 90.0
    assert collar["call_strike"] == 110.0 and collar["data_quality"] == "ok"
    assert collar["reason"] and "advice" in collar and "rule" in collar
    assert pput["strategy"] == "protective_put" and pput["call_strike"] is None
    assert "credit" not in collar and "short_strike" not in collar
    md = (tmp_path / "reviews" / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "90/110" in md and "ZZZP" in md


def test_review_expired_collar_awaits_settlement_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: pytest.fail("no gateway"))
    seed(tmp_path, lot_record(expiry="2026-10-16"))
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "await_settlement" and p["data_quality"] == "none"


# ---- iron condor (task 6 of the condor plan) ----------------------------------------
# ZZZQ (invented): long put 85 / short put 90 / short call 110 / long call 115, width 5,
# net credit 1.50/share. Mids 0.50/0.20 and 0.40/0.10 give cost 0.30 + 0.30 = 0.60,
# so captured = (1.50 - 0.60) / 1.50 = 60%.

@pytest.fixture
def condor_market(monkeypatch):
    m = {"spot": 100.0, "puts": [row(90.0, 0.50), row(85.0, 0.20)],
         "calls": [row(110.0, 0.40), row(115.0, 0.10)], "earnings": "no", "ex_div": None,
         "asked": []}

    def fetch(kind):
        def _f(t, e, strikes=()):
            m["asked"].append((kind, tuple(strikes)))
            return m["puts" if kind == "P" else "calls"]
        return _f

    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("P"))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("C"))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


def condor_record(**kw):
    base = {
        "strategy": "iron_condor", "ticker": "ZZZQ", "contracts": 1,
        "snapshot_date": "2026-10-09", "expiry": EXPIRY, "underlying_price": 100.0,
        "suggestion_file": "2026-10-09-ZZZQ-condor.md",
        "put_short_strike": 90.0, "put_long_strike": 85.0,
        "call_short_strike": 110.0, "call_long_strike": 115.0,
        "width": 5.0, "put_credit": 0.80, "call_credit": 0.70, "net_credit": 1.50,
        "breakeven_low": 88.5, "breakeven_high": 111.5, "delta_bucket": 0.15,
        "put_short_delta": 0.14, "call_short_delta": 0.16,
        "put_short_iv": 0.40, "call_short_iv": 0.38, "earnings_in_window": False,
        "status": "open", "exit_date": None, "exit_cost": None,
        "exit_underlying": None, "pnl": None, "result": None,
    }
    base.update(kw)
    return base


def test_neutral_condor_does_not_crash_review_run(condor_market, tmp_path):
    condor_market["puts"] += [row(95.0, 0.50)]
    seed(tmp_path, condor_record(), record(ticker="ZZZR"),
         condor_record(ticker="ZZZS", expiry="2026-10-16"))
    assert run(tmp_path) == 0
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    assert got["ZZZQ"]["error"] is None and got["ZZZQ"]["advice"] == "hold"
    assert got["ZZZR"]["error"] is None
    assert got["ZZZS"]["advice"] == "await_settlement" and got["ZZZS"]["data_quality"] == "none"
    assert ("P", (90.0, 85.0)) in condor_market["asked"]
    assert ("C", (110.0, 115.0)) in condor_market["asked"]


def test_review_snapshot_records_condor_fields(condor_market, tmp_path):
    seed(tmp_path, condor_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["strategy"] == "iron_condor" and p["data_quality"] == "ok"
    assert p["put_short_strike"] == 90.0 and p["call_short_strike"] == 110.0
    assert p["cost_to_close"] == pytest.approx(0.60) and p["captured"] == pytest.approx(0.60)
    assert p["put_short_delta"] == pytest.approx(spread_core.put_delta(100.0, 90.0, 0.40, 7), abs=1e-6)
    assert p["call_short_delta"] == pytest.approx(spread_core.call_delta(100.0, 110.0, 0.40, 7), abs=1e-6)
    assert p["delta"] == pytest.approx(max(p["put_short_delta"], p["call_short_delta"]))
    assert p["spot"] == 100.0 and p["ex_div"] is None and p["itm"] is False
    assert p["advice"] == "hold" and p["rule"] == 5
    for legacy in ("credit", "short_strike", "long_strike"):
        assert legacy not in p
    md = (tmp_path / "reviews" / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "85/90/110/115" in md


@pytest.mark.parametrize("side", ["puts", "calls"])
def test_review_condor_missing_quote_attention(condor_market, tmp_path, side):
    condor_market[side] = condor_market[side][:1]  # drop one long wing
    seed(tmp_path, condor_record())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert p["advice"] == "attention" and p["rule"] == 0 and p["data_quality"] == "none"
    assert p["captured"] is None and p["cost_to_close"] is None


def test_review_condor_call_itm_in_cli(condor_market, tmp_path):
    condor_market["spot"] = 112.0
    condor_market["ex_div"] = date(2026, 10, 20)
    condor_market["calls"] = [row(110.0, 2.6), row(115.0, 0.6)]
    seed(tmp_path, condor_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["itm"] is True and p["advice"] == "attention" and p["rule"] == 2
    assert p["ex_div"] == "2026-10-20" and "2026-10-20" in p["reason"]


def test_review_condor_stale_quotes_skip_deltas(condor_market, tmp_path):
    condor_market["puts"] = [{**r, "bid": 0, "ask": 0} for r in condor_market["puts"]]
    seed(tmp_path, condor_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["data_quality"] == "stale" and p["put_short_delta"] is None
    assert p["call_short_delta"] is None and p["delta"] is None


def test_review_condor_never_calls_single_spread_helpers(condor_market, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("single-spread helper used for a condor")
    for name in ("cost_to_close", "unrealized_pnl", "csp_threshold_flags"):
        monkeypatch.setattr(spread_core, name, boom)
    seed(tmp_path, condor_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None


# ---- bull call debit spread (bull call plan task 6) ---------------------------------
# Pin first: the review output for the six older strategies must not move.

import hashlib  # noqa: E402

PIN_REVIEW_MD = "de46c4a7176f85143771f357d2314d3e41c4193198c228a76532596c7128d406"
PIN_REVIEW_JSON = "165f6e584ad9d8e51e6076a8e7ff0163b255dc5fe9055cf59a4ae03415e1a476"


def _old_strategy_ledger(tmp_path):
    seed(tmp_path,
         record(),                                                           # CSP ABC 95
         record(strategy="bull_put_spread", ticker="ZZZB", short_strike=95.0,
                long_strike=90.0, width=5.0, credit=1.50),
         cc_record(ticker="ZZZC"),
         lot_record(ticker="ZZZL", lot_ref={"source": "real", "ticker": "ZZZL"}),
         lot_record("protective_put", ticker="ZZZP", lot_ref={"source": "real", "ticker": "ZZZP"}),
         condor_record(),
         record(ticker="ZZZE", expiry="2026-10-16"))


def test_review_md_unchanged_for_old_strategies(condor_market, tmp_path):
    condor_market["puts"] += [row(95.0, 0.40)]
    condor_market["calls"] += [row(105.0, 0.50)]
    _old_strategy_ledger(tmp_path)
    assert run(tmp_path) == 0
    folder = tmp_path / "reviews"
    md = (folder / f"{TODAY.isoformat()}.md").read_bytes()
    js = (folder / f"{TODAY.isoformat()}.json").read_bytes()
    assert hashlib.sha256(md).hexdigest() == PIN_REVIEW_MD
    assert hashlib.sha256(js).hexdigest() == PIN_REVIEW_JSON
