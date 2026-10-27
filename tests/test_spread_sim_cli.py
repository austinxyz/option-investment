"""CLI tests for scripts/spread_sim.py. All market data is faked; the ledger lives in tmp_path."""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ibkr_session
import spread_data
import spread_ledger
import spread_sim
from spread_fixtures import make_chain

SNAP = date(2026, 10, 16)
EXPIRY = "2026-10-23"


@pytest.fixture
def market(monkeypatch):
    """Fake yfinance. Mutate the returned dict to move the market between calls."""
    m = {
        "spot": 100.0,
        "puts": make_chain(spot=100.0),
        "closes": {},          # date -> close
        "gap": None,
        "earnings": "no",
        "fail": False,
        "open": False,         # is the US market open right now (drives the intraday flag)
    }

    def last_close(t):
        if m["fail"]:
            raise spread_data.DataError("网络挂了")
        return SNAP, m["spot"]

    monkeypatch.setattr(spread_data, "last_close", last_close)
    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-16", "2026-10-21", EXPIRY, "2026-10-30"])
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=(): m["puts"])
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: m["closes"].get(d))
    monkeypatch.setattr(spread_data, "monday_gap_pct", lambda t, s, c: m["gap"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: m["open"])
    return m


def run(tmp_path, *argv, today=SNAP):
    return spread_sim.main(["--ledger-dir", str(tmp_path), *argv], today=today)


def opened(tmp_path, ticker, bucket, *extra, today=SNAP):
    """The real workflow: suggest saves the candidates, open records the chosen one."""
    assert run(tmp_path, "suggest", ticker, "--width", "5", today=today) == 0
    return run(tmp_path, "open", ticker, bucket, *extra, today=today)


def ledger(tmp_path):
    return spread_ledger.load(tmp_path / "spreads.json")


def test_suggest_prints_three_buckets_and_writes_nothing(market, tmp_path, capsys):
    assert run(tmp_path, "suggest", "abc") == 0
    out = capsys.readouterr().out
    assert "ABC" in out and "2026-10-23" in out
    for b in ("0.10", "0.15", "0.20"):
        assert b in out
    assert not (tmp_path / "spreads.json").exists()


def test_open_records_the_chosen_bucket_and_writes_report(market, tmp_path, capsys):
    assert opened(tmp_path, "ABC", "0.15", "--contracts", "2") == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["id"] == 1 and s["ticker"] == "ABC" and s["contracts"] == 2
    assert s["status"] == "open" and s["delta_bucket"] == 0.15
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == "2026-10-16"
    assert s["short_strike"] - s["long_strike"] == pytest.approx(5.0)
    assert s["earnings_in_window"] is False and s["monday_gap_pct"] is None
    assert s["pnl"] is None and s["exit_date"] is None
    out = capsys.readouterr().out
    assert "已记录" in out and "快照 2026-10-16" in out
    assert "ABC" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_open_stores_unknown_earnings_as_null(market, tmp_path):
    market["earnings"] = "unknown"
    opened(tmp_path, "ABC", "0.10")
    assert ledger(tmp_path)["spreads"][0]["earnings_in_window"] is None


def test_open_same_ticker_same_week_rejected(market, tmp_path, capsys):
    assert opened(tmp_path, "ABC", "0.10") == 0
    assert opened(tmp_path, "ABC", "0.20") == 1
    assert "已经有记录 #1" in capsys.readouterr().err
    assert len(ledger(tmp_path)["spreads"]) == 1


def test_open_several_tickers_in_one_session(market, tmp_path):
    for t in ("AAA", "BBB", "CCC"):
        assert opened(tmp_path, t, "0.10") == 0
    assert [s["id"] for s in ledger(tmp_path)["spreads"]] == [1, 2, 3]


def test_open_unavailable_bucket_writes_nothing(market, tmp_path, capsys):
    market["puts"] = [r for r in market["puts"] if r["strike"] in (93.0, 95.0, 96.0)]
    assert opened(tmp_path, "ABC", "0.10") == 1
    assert "没有合适合约" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()


def test_data_error_returns_1_and_writes_nothing(market, tmp_path, capsys):
    market["fail"] = True
    assert run(tmp_path, "suggest", "ABC") == 1
    assert "网络挂了" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()
    assert not (tmp_path / "suggestions").exists()


def test_status_midweek_shows_unrealized_loss_without_changing_ledger(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.20")
    before = ledger(tmp_path)
    market["puts"] = make_chain(spot=90.0)  # stock fell: spread is under water
    market["spot"] = 90.0
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "浮盈亏 -$" in out
    after = ledger(tmp_path)
    assert after["spreads"][0]["status"] == "open"
    assert after["spreads"][0]["pnl"] is None
    assert after["spreads"][0]["short_strike"] == before["spreads"][0]["short_strike"]
    assert "-$" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_status_flags_stop_line(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.20")
    market["puts"] = make_chain(spot=85.0)
    market["spot"] = 85.0
    run(tmp_path, "status", today=date(2026, 10, 20))
    assert "已到止损线" in capsys.readouterr().out


def test_status_settles_after_expiry_with_expiry_day_close(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.20")
    s = ledger(tmp_path)["spreads"][0]
    market["closes"][date(2026, 10, 23)] = s["long_strike"] - 1.0  # below long strike: max loss
    assert run(tmp_path, "status", today=date(2026, 10, 24)) == 0
    done = ledger(tmp_path)["spreads"][0]
    assert done["status"] == "expired" and done["result"] == "loss"
    assert done["pnl"] == pytest.approx(-(s["width"] - s["credit"]) * 100, abs=0.01)
    assert done["exit_date"] == EXPIRY
    assert "已到期结算" in capsys.readouterr().out


def test_status_expired_in_the_money_keeps_full_credit(market, tmp_path):
    opened(tmp_path, "ABC", "0.10")
    s = ledger(tmp_path)["spreads"][0]
    market["closes"][date(2026, 10, 23)] = s["short_strike"] + 3.0
    run(tmp_path, "status", today=date(2026, 10, 24))
    done = ledger(tmp_path)["spreads"][0]
    assert done["result"] == "win" and done["pnl"] == pytest.approx(s["credit"] * 100, abs=0.01)


def test_status_leaves_trade_open_when_expiry_close_unavailable(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.10")
    assert run(tmp_path, "status", today=date(2026, 10, 24)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "稍后重试" in capsys.readouterr().out


def test_status_does_not_settle_on_expiry_day_itself(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.10")
    market["closes"][date(2026, 10, 23)] = 50.0  # intraday value must be ignored
    run(tmp_path, "status", today=date(2026, 10, 23))
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "次日或之后再运行" in capsys.readouterr().out


def test_status_backfills_monday_gap_once_available(market, tmp_path):
    opened(tmp_path, "ABC", "0.10")
    run(tmp_path, "status", today=date(2026, 10, 17))
    assert ledger(tmp_path)["spreads"][0]["monday_gap_pct"] is None
    market["gap"] = -1.8
    run(tmp_path, "status", today=date(2026, 10, 19))
    assert ledger(tmp_path)["spreads"][0]["monday_gap_pct"] == -1.8


def test_status_with_no_trades_is_fine(market, tmp_path):
    assert run(tmp_path, "status") == 0
    assert (tmp_path / "report.md").exists()


def test_close_early_records_realized_pnl(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.20")
    s = ledger(tmp_path)["spreads"][0]
    market["puts"] = make_chain(spot=104.0)  # rallied: spread is cheap to buy back
    market["spot"] = 104.0
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    done = ledger(tmp_path)["spreads"][0]
    assert done["status"] == "closed_early" and done["exit_date"] == "2026-10-20"
    assert done["pnl"] > 0 and done["result"] == "win"
    assert done["exit_cost"] < s["credit"]
    assert "已提前平仓" in capsys.readouterr().out


def test_close_twice_or_unknown_id_is_refused(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.20")
    assert run(tmp_path, "close", "1") == 0
    assert run(tmp_path, "close", "1") == 1
    assert run(tmp_path, "close", "9") == 1
    err = capsys.readouterr().err
    assert "不能再平仓" in err and "id=9" in err


def test_corrupt_ledger_blocks_every_command_and_is_not_overwritten(market, tmp_path, capsys):
    assert run(tmp_path, "suggest", "ABC") == 0  # so `open` gets past the saved-suggestion check
    capsys.readouterr()
    (tmp_path / "spreads.json").write_text("{oops", encoding="utf-8")
    for argv in (("open", "ABC", "0.10"), ("status",), ("close", "1")):
        assert run(tmp_path, *argv) == 1
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == "{oops"
    assert ".bak" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ("open", "ABC", "0.10", "--contracts", "0"),
    ("open", "ABC", "0.10", "--contracts", "-2"),
    ("open", "ABC", "0.10", "--width", "0"),
    ("suggest", "ABC", "--width", "-5"),
    ("open", "ABC", "0.12"),
])
def test_invalid_arguments_are_rejected_before_any_market_call(market, tmp_path, argv):
    market["fail"] = True  # would raise DataError if reached
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, *argv)
    assert exc.value.code == 2
    assert not (tmp_path / "spreads.json").exists()


# --- final fix wave -----------------------------------------------------------

def test_close_after_expiry_is_refused_and_ledger_untouched(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.20")
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=date(2026, 10, 24)) == 1
    assert "已过到期日" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_unsettled_past_expiry_trade_shows_retry_message_in_report(market, tmp_path):
    opened(tmp_path, "ABC", "0.10")
    run(tmp_path, "status", today=date(2026, 10, 24))
    assert "稍后重试" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_unchanged_status_does_not_rewrite_ledger(market, tmp_path):
    opened(tmp_path, "ABC", "0.10")
    assert not (tmp_path / "spreads.json.bak").exists()
    assert run(tmp_path, "status", today=date(2026, 10, 16)) == 0
    assert not (tmp_path / "spreads.json.bak").exists()
    assert (tmp_path / "report.md").exists()


def test_settling_status_saves_with_backup_of_presettlement_ledger(market, tmp_path):
    opened(tmp_path, "ABC", "0.20")
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    s = ledger(tmp_path)["spreads"][0]
    market["closes"][date(2026, 10, 23)] = s["long_strike"] - 1.0
    run(tmp_path, "status", today=date(2026, 10, 24))
    assert (tmp_path / "spreads.json.bak").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("broken", ["last_close", "fetch_puts"])
def test_status_data_error_keeps_trade_open_and_reports_it(market, tmp_path, capsys,
                                                           monkeypatch, broken):
    opened(tmp_path, "ABC", "0.10")
    capsys.readouterr()

    def boom(*a, **k):
        raise spread_data.DataError("网络挂了")

    monkeypatch.setattr(spread_data, broken, boom)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert "网络挂了" in capsys.readouterr().out
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "网络挂了" in (tmp_path / "report.md").read_text(encoding="utf-8")


# ---- cash-secured put records -------------------------------------------------

def _seed_csp(tmp_path, strike=100.0, credit=1.5, expiry=EXPIRY):
    rec = {
        "strategy": "cash_secured_put", "ticker": "ABC", "contracts": 1,
        "snapshot_date": "2026-10-16", "expiry": expiry, "underlying_price": 101.0,
        "short_strike": strike, "long_strike": None, "width": None, "credit": credit,
        "short_mid": credit, "long_mid": None, "short_spread_pct": None, "long_spread_pct": None,
        "monday_gap_pct": 0.0, "status": "open", "exit_date": None, "exit_cost": None,
        "exit_underlying": None, "pnl": None, "result": None,
    }
    led, _ = spread_ledger.add_spread(spread_ledger.empty_ledger(), rec)
    spread_ledger.save(tmp_path / "spreads.json", led)


def _csp_chain(mid):
    return [{"strike": 100.0, "bid": mid - 0.02, "ask": mid + 0.02, "last": mid, "iv": 0.4}]


@pytest.fixture
def no_report(monkeypatch):
    # Report rendering for long_strike=None is Task 4's job.
    monkeypatch.setattr(spread_sim, "_write_report", lambda *a, **k: None)


def test_status_settles_csp_otm_as_full_premium(market, tmp_path, no_report):
    _seed_csp(tmp_path)
    market["closes"][date.fromisoformat(EXPIRY)] = 101.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "expired" and s["pnl"] == 150.0 and s["result"] == "win"
    assert s["exit_cost"] == 0.0 and s["exit_underlying"] == 101.0


def test_status_settles_csp_assigned(market, tmp_path, capsys, no_report):
    _seed_csp(tmp_path)
    market["closes"][date.fromisoformat(EXPIRY)] = 92.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["pnl"] == -650.0 and s["result"] == "assigned" and s["exit_cost"] == 8.0
    assert "被指派" in capsys.readouterr().out


def test_status_marks_open_csp_with_take_profit_flag(market, tmp_path, capsys, no_report):
    _seed_csp(tmp_path)
    market["puts"] = _csp_chain(0.60)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "#1 ABC 100 Put 到期 2026-10-23" in out and "已到止盈线" in out
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_close_csp_books_buyback(market, tmp_path, no_report):
    _seed_csp(tmp_path)
    market["puts"] = _csp_chain(0.40)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(110.0)


def test_status_unchanged_for_legacy_spread_record(market, tmp_path):
    assert opened(tmp_path, "ABC", "0.20") == 0
    s0 = ledger(tmp_path)["spreads"][0]
    assert "strategy" not in s0
    market["closes"][date.fromisoformat(EXPIRY)] = 90.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    s = ledger(tmp_path)["spreads"][0]
    assert s["status"] == "expired" and s["result"] == "loss"
    assert s["pnl"] == pytest.approx(-(5 - s0["credit"]) * 100)


def test_status_with_csp_writes_full_report_without_patching(market, tmp_path):
    _seed_csp(tmp_path)
    market["puts"] = _csp_chain(0.60)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 按策略对比" in md and "ABC" in md and "100 Put" in md
    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "ABC" in page and "100 Put" in page


# ---- covered-call records -----------------------------------------------------

def _seed_cc(tmp_path, strike=110.0, credit=1.2, contracts=1, reference_price=100.0,
             lot_source="tmp"):
    rec = {
        "strategy": "covered_call", "ticker": "ABC", "contracts": contracts,
        "snapshot_date": "2026-10-16", "expiry": EXPIRY, "underlying_price": 101.0,
        "short_strike": strike, "long_strike": None, "width": None, "credit": credit,
        "short_mid": credit, "long_mid": None, "short_spread_pct": None, "long_spread_pct": None,
        "monday_gap_pct": 0.0, "status": "open", "exit_date": None, "exit_cost": None,
        "exit_underlying": None, "pnl": None, "result": None,
        "lot_ref": {"source": lot_source, "lot_id": "L1", "ticker": "ABC"},
        "cost_basis": 90.0, "reference_price": reference_price, "covered_shares": 100 * contracts,
    }
    led, _ = spread_ledger.add_spread(spread_ledger.empty_ledger(), rec)
    spread_ledger.save(tmp_path / "spreads.json", led)


def _cc_chain(mid):
    return [{"strike": 110.0, "bid": mid - 0.02, "ask": mid + 0.02, "last": mid, "iv": 0.4}]


@pytest.fixture
def cc_market(market, monkeypatch):
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(): market["calls"])
    market["calls"] = _cc_chain(0.80)
    return market


def test_status_marks_open_covered_call(cc_market, tmp_path, capsys, no_report):
    _seed_cc(tmp_path)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "#1 ABC 110 Call 到期 2026-10-23" in out
    assert "浮盈亏 +$40" in out and "距短腿 10.0%" in out
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_status_settles_covered_call_worthless(cc_market, tmp_path, no_report):
    _seed_cc(tmp_path, contracts=2)
    cc_market["closes"][date.fromisoformat(EXPIRY)] = 105.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "expired" and s["result"] == "win"
    assert s["pnl"] == pytest.approx(1.2 * 100 * 2) and s["exit_cost"] == 0.0
    assert "stock_leg_pnl" not in s


def test_status_settles_covered_call_called_away(cc_market, tmp_path, capsys, no_report):
    _seed_cc(tmp_path, contracts=2)
    cc_market["closes"][date.fromisoformat(EXPIRY)] = 115.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["result"] == "called_away" and s["pnl"] == pytest.approx(240.0)
    assert s["stock_leg_pnl"] == pytest.approx((110.0 - 100.0) * 100 * 2)
    assert s["exit_cost"] == 5.0
    assert "被行权（股票按行权价卖出）" in capsys.readouterr().out


def test_status_covered_call_stock_leg_falls_back_to_cost_basis(cc_market, tmp_path, no_report):
    _seed_cc(tmp_path)
    led = ledger(tmp_path)
    rec = {k: v for k, v in led["spreads"][0].items() if k != "reference_price"}
    led = {**led, "spreads": [rec]}
    spread_ledger.save(tmp_path / "spreads.json", led)
    cc_market["closes"][date.fromisoformat(EXPIRY)] = 115.0
    run(tmp_path, "status", today=date(2026, 10, 26))
    assert ledger(tmp_path)["spreads"][0]["stock_leg_pnl"] == pytest.approx(2000.0)


def test_status_waits_on_expiry_day_for_covered_call(cc_market, tmp_path, capsys, no_report):
    _seed_cc(tmp_path)
    cc_market["closes"][date.fromisoformat(EXPIRY)] = 115.0
    run(tmp_path, "status", today=date.fromisoformat(EXPIRY))
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "次日或之后再运行" in capsys.readouterr().out


def test_close_covered_call_books_buyback(cc_market, tmp_path, no_report):
    _seed_cc(tmp_path)
    cc_market["calls"] = _cc_chain(0.40)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(80.0)
    assert "stock_leg_pnl" not in s


def test_put_and_spread_marking_unchanged(market, tmp_path, no_report):
    _seed_csp(tmp_path)
    market["puts"] = _csp_chain(0.60)
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert m["pnl"] == pytest.approx(90.0) and m["dist_pct"] == pytest.approx(0.0)
    assert spread_sim._line(ledger(tmp_path)["spreads"][0]) == "#1 ABC 100 Put 到期 2026-10-23"


def test_status_with_corrupt_paper_shares_still_reports(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.15")
    (tmp_path / "paper_shares.json").write_text("{bad", encoding="utf-8")
    capsys.readouterr()
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    cap = capsys.readouterr()
    assert "警告：模拟持股同步失败" in cap.err
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "paper_shares.json").read_text(encoding="utf-8") == "{bad"


def test_close_with_corrupt_paper_shares_still_closes(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.15")
    (tmp_path / "paper_shares.json").write_text("{bad", encoding="utf-8")
    capsys.readouterr()
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    cap = capsys.readouterr()
    assert "已提前平仓" in cap.out and "警告：模拟持股同步失败" in cap.err
    assert ledger(tmp_path)["spreads"][0]["status"] == "closed_early"


def test_status_and_close_trigger_sync(cc_market, tmp_path, no_report):
    # status: an assigned put already settled in the ledger becomes a paper lot
    rec = {"strategy": "cash_secured_put", "ticker": "ABC", "contracts": 1,
           "snapshot_date": "2026-10-09", "expiry": "2026-10-16", "underlying_price": 101.0,
           "short_strike": 100.0, "long_strike": None, "width": None, "credit": 1.5,
           "monday_gap_pct": 0.0, "status": "expired", "exit_date": "2026-10-16",
           "exit_cost": 8.0, "exit_underlying": 92.0, "pnl": -650.0, "result": "assigned"}
    led, _ = spread_ledger.add_spread(spread_ledger.empty_ledger(), rec)
    spread_ledger.save(tmp_path / "spreads.json", led)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    doc = json.loads((tmp_path / "paper_shares.json").read_text(encoding="utf-8"))
    (lot,) = doc["lots"]
    assert lot["source"] == "assigned_put" and lot["shares"] == 100 and lot["cost_basis"] == 98.5
    # close: a covered call on that lot, closed early, lowers the cost basis
    cc_rec = {"strategy": "covered_call", "ticker": "ABC", "contracts": 1,
              "snapshot_date": "2026-10-16", "expiry": EXPIRY, "underlying_price": 101.0,
              "short_strike": 110.0, "long_strike": None, "width": None, "credit": 1.2,
              "monday_gap_pct": 0.0, "status": "open", "exit_date": None, "exit_cost": None,
              "exit_underlying": None, "pnl": None, "result": None,
              "lot_ref": {"source": "assigned_put", "lot_id": lot["id"], "ticker": "ABC"},
              "cost_basis": 98.5, "reference_price": 92.0, "covered_shares": 100}
    led, _ = spread_ledger.add_spread(ledger(tmp_path), cc_rec)
    spread_ledger.save(tmp_path / "spreads.json", led)
    cc_market["calls"] = _cc_chain(0.40)
    assert run(tmp_path, "close", "2", today=date(2026, 10, 20)) == 0
    doc = json.loads((tmp_path / "paper_shares.json").read_text(encoding="utf-8"))
    assert doc["lots"][0]["cost_basis"] == pytest.approx(98.5 - 0.8)
    assert ledger(tmp_path)["spreads"][1]["lot_applied"] is True


@pytest.mark.parametrize("break_doc", [
    lambda d: d["lots"][0].pop("source"),
    lambda d: d.update(lots="nope"),
    lambda d: d.pop("next_id"),
])
def test_status_survives_malformed_paper_shares(market, tmp_path, capsys, break_doc):
    opened(tmp_path, "ABC", "0.10")
    import paper_shares
    doc, _ = paper_shares.add_lot(paper_shares.empty_lots(), ticker="ABC", shares=100,
                                  cost=10.0, opened_on="2026-10-01")
    break_doc(doc)
    path = tmp_path / paper_shares.LOTS_FILE
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = path.read_bytes()
    (tmp_path / "report.md").unlink()
    assert run(tmp_path, "status", today=SNAP) == 0
    assert "模拟持股同步失败" in capsys.readouterr().err
    assert (tmp_path / "report.md").exists()
    assert path.read_bytes() == before


def test_print_sync_downgrades_unexpected_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(spread_sim.paper_shares, "sync",
                        lambda d: (_ for _ in ()).throw(KeyError("source")))
    spread_sim._print_sync(tmp_path)
    assert "模拟持股同步失败" in capsys.readouterr().err


def test_status_called_away_on_real_lot_notes_holding_unchanged(cc_market, tmp_path, capsys, no_report):
    _seed_cc(tmp_path, lot_source="real")
    cc_market["closes"][date.fromisoformat(EXPIRY)] = 115.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    assert "被行权（股票按行权价卖出）（真实持仓未变）" in capsys.readouterr().out


def test_status_called_away_on_paper_lot_has_no_unchanged_note(cc_market, tmp_path, capsys, no_report):
    _seed_cc(tmp_path, lot_source="manual")
    cc_market["closes"][date.fromisoformat(EXPIRY)] = 115.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    assert "真实持仓未变" not in capsys.readouterr().out


DOWN = ibkr_session.GatewayUnavailable("连不上 gw.test:14999：连接被拒绝")


def test_spread_sim_status_fails_clearly_when_gateway_down(market, tmp_path, capsys, monkeypatch):
    opened(tmp_path, "ABC", "0.10")
    capsys.readouterr()
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")

    def down(*a, **k):
        raise DOWN
    monkeypatch.setattr(spread_data, "fetch_puts", down)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 1
    captured = capsys.readouterr()
    assert "gw.test:14999" in captured.err and "gw.test:14999" not in captured.out
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_spread_sim_status_gateway_down_in_monday_gap_aborts(market, tmp_path, capsys, monkeypatch):
    opened(tmp_path, "ABC", "0.10")

    def down(*a, **k):
        raise DOWN
    monkeypatch.setattr(spread_data, "monday_gap_pct", down)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 1
    assert "gw.test:14999" in capsys.readouterr().err


def test_spread_sim_close_and_suggest_fail_clearly_when_gateway_down(market, tmp_path, capsys, monkeypatch):
    opened(tmp_path, "ABC", "0.10")

    def down(*a, **k):
        raise DOWN
    monkeypatch.setattr(spread_data, "fetch_puts", down)
    capsys.readouterr()
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    monkeypatch.setattr(spread_data, "last_close", down)
    assert run(tmp_path, "suggest", "ABC") == 1
    assert "gw.test:14999" in capsys.readouterr().err


def test_mark_requests_both_leg_strikes(market, tmp_path, monkeypatch):
    seen = []

    def fetch(t, e, strikes=()):
        seen.append(tuple(strikes))
        return market["puts"]
    monkeypatch.setattr(spread_data, "fetch_puts", fetch)
    opened(tmp_path, "ABC", "0.15")
    s = ledger(tmp_path)["spreads"][0]
    seen.clear()
    spread_sim._mark(s)
    assert set(seen[0]) == {s["short_strike"], s["long_strike"]}


def test_mark_csp_and_cc_request_short_strike(market, cc_market, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(spread_data, "fetch_puts",
                        lambda t, e, strikes=(): seen.append(tuple(strikes)) or _csp_chain(0.6))
    monkeypatch.setattr(spread_data, "fetch_calls",
                        lambda t, e, strikes=(): seen.append(tuple(strikes)) or _cc_chain(0.8))
    _seed_csp(tmp_path)
    spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert seen == [(100.0,)]
    seen.clear()
    _seed_cc(tmp_path)
    spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert seen == [(110.0,)]


def test_status_marks_spread_whose_long_leg_has_no_greeks(market, tmp_path, capsys):
    opened(tmp_path, "ABC", "0.15")
    s = ledger(tmp_path)["spreads"][0]
    market["puts"] = [
        {"strike": s["short_strike"], "bid": 0.9, "ask": 1.1, "last": 1.0, "iv": 0.4, "delta": 0.2},
        {"strike": s["long_strike"], "bid": 0.2, "ask": 0.3, "last": 0.25, "iv": None, "delta": None},
    ]
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "浮盈亏" in out and "找不到" not in out


def _interleave_send(monkeypatch, name, rid=1):
    """Simulate a concurrent `send`: after the CLI loaded its copy and changed it, a broker block
    lands on disk just before the save."""
    real = getattr(spread_ledger, name)
    done = []

    def wrapper(*a, **k):
        out = real(*a, **k)
        if not done:
            done.append(1)
            path = a_dir["path"]
            spread_ledger.save(path, spread_ledger.set_broker(
                spread_ledger.load(path), rid, {"status": "submitted", "order_ref": "rwh-1-LIVE0001"}))
        return out

    a_dir = {}
    monkeypatch.setattr(spread_ledger, name, wrapper)
    return a_dir


def _broker(tmp_path, rid=1):
    return spread_ledger.get_spread(ledger(tmp_path), rid).get("broker")


def test_status_save_keeps_broker_block_written_meanwhile(market, tmp_path, monkeypatch):
    opened(tmp_path, "ABC", "0.10")
    run(tmp_path, "status", today=date(2026, 10, 17))
    market["gap"] = -1.8
    _interleave_send(monkeypatch, "update_spread")["path"] = tmp_path / "spreads.json"
    run(tmp_path, "status", today=date(2026, 10, 19))
    s = ledger(tmp_path)["spreads"][0]
    assert s["monday_gap_pct"] == -1.8
    assert _broker(tmp_path) == {"status": "submitted", "order_ref": "rwh-1-LIVE0001"}


def test_close_save_keeps_broker_block_written_meanwhile(market, tmp_path, monkeypatch):
    opened(tmp_path, "ABC", "0.20")
    market["puts"] = make_chain(spot=104.0)
    market["spot"] = 104.0
    _interleave_send(monkeypatch, "update_spread")["path"] = tmp_path / "spreads.json"
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "closed_early"
    assert _broker(tmp_path) == {"status": "submitted", "order_ref": "rwh-1-LIVE0001"}


def test_open_save_keeps_broker_block_of_other_record_written_meanwhile(market, tmp_path, monkeypatch):
    opened(tmp_path, "ABC", "0.10")
    _interleave_send(monkeypatch, "add_spread")["path"] = tmp_path / "spreads.json"
    assert opened(tmp_path, "DEF", "0.10") == 0
    assert len(ledger(tmp_path)["spreads"]) == 2
    assert _broker(tmp_path) == {"status": "submitted", "order_ref": "rwh-1-LIVE0001"}


# ---- collar / protective-put records (ZZZQ, invented) ----------------------------

def _seed_collar(tmp_path, *, strategy="collar", put_strike=90.0, call_strike=110.0,
                 put_debit=1.0, call_credit=1.5, contracts=1, reference_price=100.0):
    if strategy == "protective_put":
        call_strike, call_credit = None, 0.0
    rec = {
        "strategy": strategy, "ticker": "ZZZQ", "contracts": contracts,
        "snapshot_date": "2026-10-16", "expiry": EXPIRY, "underlying_price": 101.0,
        "put_strike": put_strike, "put_debit": put_debit, "call_strike": call_strike,
        "call_credit": call_credit, "net_credit": round(call_credit - put_debit, 6),
        "covered_shares": 100 * contracts, "cost_basis": 95.0,
        "reference_price": reference_price,
        "lot_ref": {"source": "tmp", "lot_id": "L1", "ticker": "ZZZQ"},
        "status": "open", "exit_date": None, "exit_underlying": None, "pnl": None, "result": None,
    }
    led, _ = spread_ledger.add_spread(spread_ledger.empty_ledger(), rec)
    spread_ledger.save(tmp_path / "spreads.json", led)


def _leg(strike, mid):
    return [{"strike": strike, "bid": mid - 0.02, "ask": mid + 0.02, "last": mid, "iv": 0.4}]


@pytest.fixture
def collar_market(market, monkeypatch):
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(): market["calls"])
    market["puts"], market["calls"] = _leg(90.0, 0.60), _leg(110.0, 0.90)
    return market


def _settle_collar(market, tmp_path, close, **seed):
    _seed_collar(tmp_path, **seed)
    market["closes"][date.fromisoformat(EXPIRY)] = close
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    return ledger(tmp_path)["spreads"][0]


def test_status_marks_collar_open_with_both_legs(collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    # (1.5-0.9)+(0.6-1.0) = +0.20/share -> +$20
    assert "#1 ZZZQ 90/110 Collar 到期 2026-10-23" in out and "浮盈亏 +$20" in out
    assert "下限 90" in out and "上限 110" in out
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_status_marks_protective_put_open(collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path, strategy="protective_put", put_debit=0.4, contracts=2)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    # (0 - 0) + (0.6-0.4) = +0.20/share x 100 x 2 = +$40
    assert "#1 ZZZQ 90 保护性Put 到期 2026-10-23" in out and "浮盈亏 +$40" in out


def test_status_collar_missing_leg_reports_no_quote(collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path)
    collar_market["calls"] = _leg(111.0, 0.90)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert "找不到" in capsys.readouterr().out
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert "error" in m and "pnl" not in m


def test_mark_collar_requests_each_leg_strike_and_protective_put_skips_calls(
        market, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(spread_data, "fetch_puts",
                        lambda t, e, strikes=(): seen.append(("p", strikes)) or _leg(90.0, 0.6))
    monkeypatch.setattr(spread_data, "fetch_calls",
                        lambda t, e, strikes=(): seen.append(("c", strikes)) or _leg(110.0, 0.9))
    _seed_collar(tmp_path)
    spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert seen == [("p", (90.0,)), ("c", (110.0,))]
    seen.clear()
    _seed_collar(tmp_path, strategy="protective_put")
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert seen == [("p", (90.0,))] and m["call_mid"] == 0.0


def test_settle_collar_called_away(collar_market, tmp_path, capsys, no_report):
    s = _settle_collar(collar_market, tmp_path, 115.0)
    # net 0.50 x 100 = +$50 option leg; stock sold at 110 vs ref 100 = +$1000
    assert s["status"] == "expired" and s["result"] == "called_away"
    assert s["pnl"] == pytest.approx(50.0) and s["stock_leg_pnl"] == pytest.approx(1000.0)
    assert s["exit_underlying"] == 115.0 and s["exit_date"] == EXPIRY
    assert "被行权" in capsys.readouterr().out


def test_settle_collar_protected(collar_market, tmp_path, no_report):
    s = _settle_collar(collar_market, tmp_path, 85.0, contracts=2)
    # (0.50 + 5) x 100 x 2 = $1100; stock (85-100) x 100 x 2 = -$3000
    assert s["result"] == "protected" and s["pnl"] == pytest.approx(1100.0)
    assert s["stock_leg_pnl"] == pytest.approx(-3000.0)


def test_settle_collar_expired_worthless(collar_market, tmp_path, no_report):
    s = _settle_collar(collar_market, tmp_path, 100.0)
    assert s["result"] == "expired_worthless" and s["pnl"] == pytest.approx(50.0)
    assert s["stock_leg_pnl"] == pytest.approx(0.0) and s["status"] == "expired"


def test_settle_collar_boundaries_put_strictly_below_call_at_or_above(
        collar_market, tmp_path, no_report):
    assert _settle_collar(collar_market, tmp_path, 90.0)["result"] == "expired_worthless"
    assert _settle_collar(collar_market, tmp_path, 110.0)["result"] == "called_away"


def test_settle_protective_put_variants(collar_market, tmp_path, no_report):
    down = _settle_collar(collar_market, tmp_path, 80.0, strategy="protective_put", put_debit=2.0)
    # -2.00 + 10 = +8.00/share = +$800; stock (80-100) = -$2000
    assert down["result"] == "protected" and down["pnl"] == pytest.approx(800.0)
    assert down["stock_leg_pnl"] == pytest.approx(-2000.0)
    up = _settle_collar(collar_market, tmp_path, 105.0, strategy="protective_put", put_debit=2.0)
    assert up["result"] == "expired_worthless" and up["pnl"] == pytest.approx(-200.0)
    assert up["stock_leg_pnl"] == pytest.approx(500.0)  # no cap without a call


def test_close_collar_uses_signed_mids_and_records_exit_mids(collar_market, tmp_path, no_report):
    _seed_collar(tmp_path, put_debit=2.0, call_credit=1.0)  # net debit -1.00
    collar_market["puts"], collar_market["calls"] = _leg(90.0, 0.50), _leg(110.0, 0.20)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    # (1.0-0.2)+(0.5-2.0) = -0.70/share -> -$70 (not clamped)
    assert s["status"] == "closed_early" and s["result"] == "closed"
    assert s["pnl"] == pytest.approx(-70.0)
    assert s["exit_put_mid"] == pytest.approx(0.50) and s["exit_call_mid"] == pytest.approx(0.20)
    assert s["exit_underlying"] == 100.0 and s["exit_date"] == "2026-10-20"


def test_close_protective_put_books_put_gain(collar_market, tmp_path, no_report):
    _seed_collar(tmp_path, strategy="protective_put", put_debit=2.0, contracts=2)
    collar_market["puts"] = _leg(90.0, 3.5)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    s = ledger(tmp_path)["spreads"][0]
    assert s["pnl"] == pytest.approx(300.0) and s["exit_call_mid"] == 0.0


def test_close_collar_refuses_when_a_leg_has_no_quote(collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path)
    collar_market["calls"] = []
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 1
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "错误" in capsys.readouterr().err


def test_close_after_expiry_refused_for_collar(collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 24)) == 1
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "过到期日" in capsys.readouterr().err


def test_status_never_calls_unknown_branch_for_new_strategies(
        collar_market, tmp_path, monkeypatch, no_report):
    def boom(*a, **k):
        raise AssertionError("generic bull-put path used for a new strategy")
    for name in ("cost_to_close", "unrealized_pnl", "close_pnl", "expiry_pnl", "threshold_flags",
                 "expiry_cost", "csp_expiry_pnl", "cc_expiry_pnl"):
        monkeypatch.setattr(spread_sim.core, name, boom)
    for strategy in ("collar", "protective_put"):
        _seed_collar(tmp_path, strategy=strategy)
        assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
        collar_market["closes"][date.fromisoformat(EXPIRY)] = 95.0
        assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
        assert ledger(tmp_path)["spreads"][0]["status"] == "expired"
        assert run(tmp_path, "close", "1", today=date(2026, 10, 26)) == 1  # already expired


def test_existing_strategies_status_and_close_unchanged(market, tmp_path, no_report):
    assert opened(tmp_path, "ABC", "0.20") == 0
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    s = ledger(tmp_path)["spreads"][0]
    assert s["status"] == "closed_early" and "exit_put_mid" not in s
    assert spread_sim._line(s).startswith("#1 ABC ") and "/" in spread_sim._line(s)


def test_existing_cc_and_csp_close_unchanged(cc_market, tmp_path, no_report):
    _seed_cc(tmp_path)
    cc_market["calls"] = _cc_chain(0.40)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    s = ledger(tmp_path)["spreads"][0]
    assert s["pnl"] == pytest.approx(80.0) and s["exit_cost"] == pytest.approx(0.40)
    assert "exit_put_mid" not in s and "exit_call_mid" not in s
    other = tmp_path / "csp"
    other.mkdir()
    _seed_csp(other)
    cc_market["puts"] = _csp_chain(0.40)
    assert run(other, "close", "1", today=date(2026, 10, 20)) == 0
    s = ledger(other)["spreads"][0]
    assert s["pnl"] == pytest.approx(110.0) and s["exit_cost"] == pytest.approx(0.40)
    assert "exit_put_mid" not in s


@pytest.mark.parametrize("close,result,option,stock", [
    # net debit: call 1.0 - put 1.5 = -0.50/share; put 90, call 110, reference 100
    (85.0, "protected", 450.0, -1500.0),         # (-0.50 + 5) x 100
    (115.0, "called_away", -50.0, 1000.0),       # -0.50 x 100; stock sold at 110
    (100.0, "expired_worthless", -50.0, 0.0),
])
def test_settle_net_debit_collar_all_outcomes(collar_market, tmp_path, no_report,
                                              close, result, option, stock):
    s = _settle_collar(collar_market, tmp_path, close, put_debit=1.5, call_credit=1.0)
    assert s["result"] == result and s["status"] == "expired"
    assert s["pnl"] == pytest.approx(option) and s["stock_leg_pnl"] == pytest.approx(stock)


def test_settle_message_shows_option_and_stock_legs(collar_market, tmp_path, capsys, no_report):
    _settle_collar(collar_market, tmp_path, 115.0)
    out = capsys.readouterr().out
    assert "期权腿盈亏 +$50" in out and "股票腿 +$1,000" in out


def test_close_protective_put_message_has_no_call_leg(collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path, strategy="protective_put", put_debit=2.0)
    collar_market["puts"] = _leg(90.0, 3.5)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "Put $3.50" in out and "Call" not in out.split("盈亏")[0].replace("保护性Put", "")


def test_settle_without_reference_price_reports_record_and_continues(
        collar_market, tmp_path, capsys, no_report):
    _seed_collar(tmp_path)
    led = ledger(tmp_path)
    bad = {k: v for k, v in led["spreads"][0].items() if k != "reference_price"}
    good, _ = spread_ledger.add_spread(
        spread_ledger.empty_ledger(), {**led["spreads"][0], "ticker": "ZZZR"})
    mixed = {**good, "spreads": [{**bad, "id": 1}, {**good["spreads"][0], "id": 2}]}
    spread_ledger.save(tmp_path / "spreads.json", mixed)
    collar_market["closes"][date.fromisoformat(EXPIRY)] = 100.0
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    out = capsys.readouterr().out
    assert "#1" in out and "reference_price" in out
    s1, s2 = ledger(tmp_path)["spreads"]
    assert s1["status"] == "open" and s2["status"] == "expired"


# ---- iron condor records (ZZZQ, invented) ---------------------------------------
# Shared seed: long put 85 / short put 90 / short call 110 / long call 115, width 5,
# put credit 0.80 + call credit 0.70 = net credit 1.50/share. Max profit $150, max loss
# (5 - 1.50) x 100 = $350 per contract. Breakevens 88.50 / 111.50.

def _condor_rec(ticker="ZZZQ", contracts=1, expiry=EXPIRY, **extra):
    return {
        "strategy": "iron_condor", "ticker": ticker, "contracts": contracts,
        "snapshot_date": "2026-10-16", "expiry": expiry, "underlying_price": 100.0,
        "suggestion_file": "2026-10-16-ZZZQ-condor.md",
        "put_short_strike": 90.0, "put_long_strike": 85.0,
        "call_short_strike": 110.0, "call_long_strike": 115.0,
        "width": 5.0, "put_credit": 0.80, "call_credit": 0.70, "net_credit": 1.50,
        "breakeven_low": 88.5, "breakeven_high": 111.5, "delta_bucket": 0.15,
        "put_short_delta": 0.14, "call_short_delta": 0.16,
        "put_short_iv": 0.40, "call_short_iv": 0.38, "earnings_in_window": False,
        "monday_gap_pct": 0.0, "status": "open", "exit_date": None, "exit_cost": None,
        "exit_underlying": None, "pnl": None, "result": None, **extra,
    }


def _seed_condor(tmp_path, *recs):
    led = spread_ledger.empty_ledger()
    for rec in recs or (_condor_rec(),):
        led, _ = spread_ledger.add_spread(led, rec)
    spread_ledger.save(tmp_path / "spreads.json", led)


def _legs(*pairs):
    rows = []
    for strike, mid in pairs:
        rows += _leg(strike, mid)
    return rows


@pytest.fixture
def condor_market(market, monkeypatch):
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(): market["calls"])
    # put side 0.50 - 0.20 = 0.30; call side 0.40 - 0.10 = 0.30; cost 0.60/share
    market["puts"] = _legs((90.0, 0.50), (85.0, 0.20))
    market["calls"] = _legs((110.0, 0.40), (115.0, 0.10))
    return market


def _settle_condor(market, tmp_path, close, **rec):
    _seed_condor(tmp_path, _condor_rec(**rec))
    market["closes"][date.fromisoformat(EXPIRY)] = close
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    return ledger(tmp_path)["spreads"][0]


def test_status_marks_condor_open_with_four_legs(condor_market, tmp_path, capsys,
                                                 monkeypatch, no_report):
    seen = []
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=():
                        seen.append(("p", tuple(strikes))) or condor_market["puts"])
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=():
                        seen.append(("c", tuple(strikes))) or condor_market["calls"])
    _seed_condor(tmp_path)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    # cost 0.60 -> (1.50 - 0.60) x 100 = +$90; spot 100, nearer short = min(10, 10) -> 10.0%
    assert "#1 ZZZQ 85/90/110/115 铁鹰 到期 2026-10-23" in out
    assert "浮盈亏 +$90" in out and "距较近短腿 10.0%" in out
    assert {s[0]: set(s[1]) for s in seen} == {"p": {90.0, 85.0}, "c": {110.0, 115.0}}
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert m["cost"] == pytest.approx(0.60) and m["pnl"] == pytest.approx(90.0)
    assert m["underlying"] == 100.0 and m["dist_pct"] == pytest.approx(10.0)
    assert m["stale"] is False and set(m["flags"]) == {"stop", "take_profit"}
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_mark_condor_dist_pct_is_to_the_nearer_short(condor_market, tmp_path, no_report):
    _seed_condor(tmp_path)
    condor_market["spot"] = 106.0
    # 106 - 90 = 16 vs 110 - 106 = 4 -> 4 / 106 = 3.77%
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert m["dist_pct"] == pytest.approx(4 / 106 * 100)
    condor_market["spot"] = 112.0  # beyond the short call: negative distance
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert m["dist_pct"] == pytest.approx(-2 / 112 * 100)


@pytest.mark.parametrize("drop", ["puts", "calls"])
def test_status_condor_missing_leg_reports_no_quote(condor_market, tmp_path, capsys,
                                                    no_report, drop):
    _seed_condor(tmp_path)
    condor_market[drop] = condor_market[drop][:1]  # only the short leg on that side
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert "找不到" in capsys.readouterr().out
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert "error" in m and "pnl" not in m
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_status_condor_flags_stop_and_take_profit(condor_market, tmp_path, capsys, no_report):
    _seed_condor(tmp_path)
    # +$90 >= 50% x $150 = $75 -> take-profit line
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "已到止盈线" in out and "已到止损线" not in out
    # put side 3.80 - 0.40 = 3.40, call side 0.10 - 0.05 = 0.05 -> cost 3.45
    # (1.50 - 3.45) x 100 = -$195 <= -50% x $350 = -$175 -> stop line
    condor_market["puts"] = _legs((90.0, 3.80), (85.0, 0.40))
    condor_market["calls"] = _legs((110.0, 0.10), (115.0, 0.05))
    condor_market["spot"] = 91.0
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "浮盈亏 -$195" in out and "已到止损线" in out and "已到止盈线" not in out


def test_settle_condor_between_shorts_full_credit(condor_market, tmp_path, capsys, no_report):
    s = _settle_condor(condor_market, tmp_path, 100.0)
    # no side loses: +1.50 x 100 = +$150
    assert s["status"] == "expired" and s["result"] == "win"
    assert s["pnl"] == pytest.approx(150.0) and s["exit_cost"] == 0.0
    assert s["breached_side"] == "none" and s["exit_underlying"] == 100.0
    assert s["exit_date"] == EXPIRY
    assert "已到期结算" in capsys.readouterr().out
    assert "credit" not in s and "short_strike" not in s and "long_strike" not in s


@pytest.mark.parametrize("close,contracts,pnl,result,exit_cost", [
    (88.0, 1, -50.0, "loss", 2.0),     # put loss 2: (1.50 - 2) x 100 = -$50
    (89.0, 2, 100.0, "win", 1.0),      # put loss 1: (1.50 - 1) x 100 x 2 = +$100, still a win
    (80.0, 1, -350.0, "loss", 5.0),    # put loss clamped to width 5: -3.50 x 100 = -$350
])
def test_settle_condor_put_breach(condor_market, tmp_path, no_report,
                                  close, contracts, pnl, result, exit_cost):
    s = _settle_condor(condor_market, tmp_path, close, contracts=contracts)
    assert s["status"] == "expired" and s["breached_side"] == "put"
    assert s["pnl"] == pytest.approx(pnl) and s["result"] == result
    assert s["exit_cost"] == pytest.approx(exit_cost) and s["exit_underlying"] == close


@pytest.mark.parametrize("close,pnl,result,exit_cost", [
    (112.0, -50.0, "loss", 2.0),       # call loss 2: -0.50 x 100
    (111.0, 50.0, "win", 1.0),         # call loss 1: +0.50 x 100
    (130.0, -350.0, "loss", 5.0),      # call loss clamped to 5: -3.50 x 100
])
def test_settle_condor_call_breach(condor_market, tmp_path, capsys, no_report,
                                   close, pnl, result, exit_cost):
    s = _settle_condor(condor_market, tmp_path, close)
    assert s["status"] == "expired" and s["breached_side"] == "call"
    assert s["pnl"] == pytest.approx(pnl) and s["result"] == result
    assert s["exit_cost"] == pytest.approx(exit_cost)
    assert "Call 侧" in capsys.readouterr().out


@pytest.mark.parametrize("close", [90.0, 110.0, 88.5])
def test_settle_condor_boundary_equal_to_short_strike(condor_market, tmp_path, no_report, close):
    s = _settle_condor(condor_market, tmp_path, close)
    if close == 88.5:  # exactly the lower breakeven: 1.50 - 1.50 = 0 -> loss (pnl == 0)
        assert s["pnl"] == pytest.approx(0.0) and s["result"] == "loss"
        assert s["breached_side"] == "put"
        return
    # exactly at a short strike: that side loses 0 -> full credit
    assert s["pnl"] == pytest.approx(150.0) and s["result"] == "win"
    assert s["breached_side"] == "none" and s["exit_cost"] == 0.0


def test_condor_waits_on_expiry_day(condor_market, tmp_path, capsys, no_report):
    _seed_condor(tmp_path)
    condor_market["closes"][date.fromisoformat(EXPIRY)] = 80.0
    assert run(tmp_path, "status", today=date.fromisoformat(EXPIRY)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "次日或之后再运行" in capsys.readouterr().out


@pytest.mark.parametrize("puts,calls,cost,pnl,result", [
    # put side 0.30 - 0.50 = -0.20 -> clamped 0; call side 0.40 - 0.10 = 0.30
    # cost 0.30 -> (1.50 - 0.30) x 100 x 2 = +$240 (unclamped would be +$280)
    (((90.0, 0.30), (85.0, 0.50)), ((110.0, 0.40), (115.0, 0.10)), 0.30, 240.0, "win"),
    # call side 7.00 - 1.00 = 6.00 -> clamped 5; put side 0.05 - 0.03 = 0.02
    # cost 5.02 -> (1.50 - 5.02) x 100 x 2 = -$704 (unclamped 6.02 would be -$904)
    (((90.0, 0.05), (85.0, 0.03)), ((110.0, 7.00), (115.0, 1.00)), 5.02, -704.0, "loss"),
])
def test_close_condor_uses_four_mids_clamped(condor_market, tmp_path, capsys, no_report,
                                             puts, calls, cost, pnl, result):
    _seed_condor(tmp_path, _condor_rec(contracts=2))
    condor_market["puts"], condor_market["calls"] = _legs(*puts), _legs(*calls)
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["exit_date"] == "2026-10-20"
    assert s["exit_cost"] == pytest.approx(cost) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["exit_underlying"] == 100.0
    assert "已提前平仓" in capsys.readouterr().out


@pytest.mark.parametrize("drop", ["puts", "calls"])
def test_close_condor_refuses_when_a_leg_has_no_quote(condor_market, tmp_path, capsys,
                                                      no_report, drop):
    _seed_condor(tmp_path)
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    condor_market[drop] = condor_market[drop][1:]  # only the long leg on that side
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 1
    assert "错误" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_close_after_expiry_refused_for_condor(condor_market, tmp_path, capsys, no_report):
    _seed_condor(tmp_path)
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=date(2026, 10, 24)) == 1
    assert "过到期日" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_status_never_calls_single_spread_helpers_for_condor(
        condor_market, tmp_path, monkeypatch, no_report):
    def boom(*a, **k):
        raise AssertionError("single-spread helper used for an iron condor")
    for name in ("cost_to_close", "unrealized_pnl", "close_pnl", "expiry_pnl", "expiry_cost",
                 "csp_expiry_pnl", "cc_expiry_pnl", "csp_threshold_flags",
                 "collar_unrealized_pnl", "collar_close_pnl", "collar_expiry_option_pnl"):
        monkeypatch.setattr(spread_sim.core, name, boom)
    _seed_condor(tmp_path)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "closed_early"
    other = tmp_path / "settle"
    other.mkdir()
    _seed_condor(other)
    condor_market["closes"][date.fromisoformat(EXPIRY)] = 95.0
    assert run(other, "status", today=date(2026, 10, 26)) == 0
    assert ledger(other)["spreads"][0]["status"] == "expired"


@pytest.mark.parametrize("failure", ["no_quote", "data_error"])
def test_status_continues_past_a_condor_error(condor_market, tmp_path, capsys, monkeypatch,
                                              no_report, failure):
    def calls(t, e, strikes=()):
        if t != "ZZZQ":
            return condor_market["calls"]
        if failure == "data_error":
            raise spread_data.DataError("ZZZQ 期权链挂了")
        return []
    monkeypatch.setattr(spread_data, "fetch_calls", calls)
    _seed_condor(tmp_path, _condor_rec(), _condor_rec(ticker="ZZZR"),
                 _condor_rec(ticker="ZZZS", expiry="2026-10-16"))
    condor_market["closes"][date(2026, 10, 16)] = 100.0
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    lines = capsys.readouterr().out.splitlines()
    bad = next(line for line in lines if line.startswith("#1 ZZZQ"))
    assert "找不到" in bad or "挂了" in bad
    assert any(line.startswith("#2 ZZZR") and "浮盈亏 +$90" in line for line in lines)
    s1, s2, s3 = ledger(tmp_path)["spreads"]
    assert s1["status"] == "open" and s2["status"] == "open"
    assert s3["status"] == "expired" and s3["pnl"] == pytest.approx(150.0)


def test_paper_shares_untouched_by_condor(condor_market, tmp_path, capsys, no_report):
    import paper_shares
    doc, _ = paper_shares.add_lot(paper_shares.empty_lots(), ticker="ZZZQ", shares=100,
                                  cost=95.0, opened_on="2026-10-01")
    lots = tmp_path / paper_shares.LOTS_FILE
    paper_shares.save_lots(lots, doc)
    before = lots.read_bytes()
    _seed_condor(tmp_path, _condor_rec(), _condor_rec(ticker="ZZZR"))
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert run(tmp_path, "close", "2", today=date(2026, 10, 20)) == 0
    condor_market["closes"][date.fromisoformat(EXPIRY)] = 80.0  # put side breached at expiry
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    assert lots.read_bytes() == before
    assert "假设持仓" not in capsys.readouterr().out
    for s in ledger(tmp_path)["spreads"]:
        assert "lot_applied" not in s and "lot_ref" not in s
    assert [s["status"] for s in ledger(tmp_path)["spreads"]] == ["expired", "closed_early"]


def test_existing_strategies_status_and_close_unchanged_alongside_condor(
        condor_market, tmp_path, capsys, monkeypatch, no_report):
    """A bull put spread behaves the same whether or not a condor shares the ledger.

    (The plain `test_existing_strategies_status_and_close_unchanged` above already exists for
    the collar work; this is the condor variant.)"""
    condor_puts = condor_market["puts"]
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=():
                        condor_puts if t == "ZZZQ" else condor_market["chain"])
    alone, mixed = tmp_path / "alone", tmp_path / "mixed"
    outputs, records = [], []
    for folder in (alone, mixed):
        folder.mkdir()
        condor_market["chain"], condor_market["spot"] = make_chain(spot=100.0), 100.0
        assert opened(folder, "ABC", "0.20") == 0
        if folder is mixed:
            led, _ = spread_ledger.add_spread(ledger(folder), _condor_rec())
            spread_ledger.save(folder / "spreads.json", led)
        capsys.readouterr()
        condor_market["chain"], condor_market["spot"] = make_chain(spot=104.0), 104.0
        assert run(folder, "status", today=date(2026, 10, 20)) == 0
        abc = [line for line in capsys.readouterr().out.splitlines() if line.startswith("#1 ABC")]
        assert run(folder, "close", "1", today=date(2026, 10, 20)) == 0
        outputs.append((abc, capsys.readouterr().out))
        records.append(ledger(folder)["spreads"][0])
    assert outputs[0] == outputs[1] and records[0] == records[1]
    assert outputs[0][0] and "breached_side" not in records[0]
    assert ledger(mixed)["spreads"][1]["status"] == "open"
    csp = {"strategy": "cash_secured_put", "id": 3, "ticker": "ABC", "short_strike": 100.0,
           "expiry": EXPIRY}
    assert spread_sim._line(csp) == "#3 ABC 100 Put 到期 2026-10-23"


# ---- bull call debit spread records (ZZZQ, invented; design section 10) ----------
# Shared seed: buy 100 call @3.20, sell 105 call @1.30 -> debit 1.90/share, width 5,
# breakeven 101.90, max loss 1.90 x 100 = $190, max profit (5 - 1.90) x 100 = $310.
# Take-profit line +$155 (0.5 x 310), stop line -$95 (-0.5 x 190), both inclusive.

def _bullcall_rec(ticker="ZZZQ", contracts=1, expiry=EXPIRY, **extra):
    return {
        "strategy": "bull_call_spread", "ticker": ticker, "contracts": contracts,
        "snapshot_date": "2026-10-16", "expiry": expiry, "underlying_price": 100.0,
        "suggestion_file": "2026-10-16-ZZZQ-bullcall.md",
        "long_call_strike": 100.0, "short_call_strike": 105.0, "width": 5.0,
        "long_call_mid": 3.20, "short_call_mid": 1.30, "debit": 1.90, "breakeven": 101.9,
        "breakeven_move_pct": 0.019, "max_profit_move_pct": 0.05, "delta_bucket": 0.50,
        "long_call_delta": 0.52, "short_call_delta": 0.31,
        "long_call_iv": 0.40, "short_call_iv": 0.38, "earnings_in_window": False,
        "status": "open", "exit_date": None, "exit_value": None, "exit_underlying": None,
        "pnl": None, "result": None, "fraction_of_max_captured": None, **extra,
    }


def _seed_bullcall(tmp_path, *recs):
    led = spread_ledger.empty_ledger()
    for rec in recs or (_bullcall_rec(),):
        led, _ = spread_ledger.add_spread(led, rec)
    spread_ledger.save(tmp_path / "spreads.json", led)


@pytest.fixture
def bullcall_market(market, monkeypatch):
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(): market["calls"])
    # long 4.00 - short 1.60 = 2.40/share -> (2.40 - 1.90) x 100 = +$50
    market["calls"] = _legs((100.0, 4.00), (105.0, 1.60))
    market["spot"] = 103.0
    return market


def _settle_bullcall(market, tmp_path, close, **rec):
    _seed_bullcall(tmp_path, _bullcall_rec(**rec))
    market["closes"][date.fromisoformat(EXPIRY)] = close
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    return ledger(tmp_path)["spreads"][0]


def test_status_marks_bullcall_with_two_explicit_strikes(bullcall_market, tmp_path, capsys,
                                                         monkeypatch, no_report):
    seen = []
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=():
                        seen.append(tuple(strikes)) or bullcall_market["calls"])
    monkeypatch.setattr(spread_data, "fetch_puts", lambda *a, **k: pytest.fail("puts fetched"))
    _seed_bullcall(tmp_path)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    # value 2.40 -> +$50; distance to the long strike (103 - 100) / 103 = 2.9%
    assert "#1 ZZZQ 100/105 Call 借方价差 到期 2026-10-23" in out
    assert "浮盈亏 +$50" in out and "距长腿 2.9%" in out
    assert "止盈" not in out and "止损" not in out
    assert seen == [(100.0, 105.0)]
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert set(m) == {"value", "pnl", "underlying", "dist_pct", "flags", "stale",
                      "long_mid", "short_mid", "long_iv", "short_iv"}
    assert m["value"] == pytest.approx(2.40) and m["pnl"] == pytest.approx(50.0)
    assert m["dist_pct"] == pytest.approx(3 / 103 * 100) and m["stale"] is False
    assert m["long_mid"] == pytest.approx(4.00) and m["short_mid"] == pytest.approx(1.60)
    assert m["long_iv"] == 0.4 and m["short_iv"] == 0.4
    assert m["flags"] == {"stop": False, "take_profit": False}
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_win_loss_labels_shared_with_condor_alias():
    assert spread_sim.WIN_LOSS_LABELS == {"win": "盈利", "loss": "亏损"}
    assert spread_sim.CONDOR_RESULT_LABELS is spread_sim.WIN_LOSS_LABELS


@pytest.mark.parametrize("spot", [0.0, None])
def test_mark_bullcall_invalid_underlying_is_error_not_raise(bullcall_market, monkeypatch, spot):
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, spot))
    m = spread_sim._mark(_bullcall_rec())
    assert set(m) == {"error"} and "现价" in m["error"]


@pytest.mark.parametrize("keep", [100.0, 105.0])
def test_status_bullcall_missing_leg_reports_no_quote(bullcall_market, tmp_path, capsys,
                                                      no_report, keep):
    _seed_bullcall(tmp_path)
    bullcall_market["calls"] = [r for r in bullcall_market["calls"] if r["strike"] == keep]
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert "找不到" in capsys.readouterr().out
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert set(m) == {"error"}
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


def test_status_bullcall_flags_take_profit_and_stop(bullcall_market, tmp_path, capsys,
                                                    no_report):
    _seed_bullcall(tmp_path)
    # 4.85 - 1.40 = 3.45 -> (3.45 - 1.90) x 100 = +$155 = exactly 50% of $310 (inclusive)
    bullcall_market["calls"] = _legs((100.0, 4.85), (105.0, 1.40))
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "浮盈亏 +$155" in out and "已到止盈线（最大收益的50%）" in out
    assert "已到止损线" not in out
    # 1.45 - 0.50 = 0.95 -> (0.95 - 1.90) x 100 = -$95 = exactly -50% of $190 (inclusive)
    bullcall_market["calls"] = _legs((100.0, 1.45), (105.0, 0.50))
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    out = capsys.readouterr().out
    assert "浮盈亏 -$95" in out and "已到止损线（浮亏≥净支出的50%）" in out
    assert "已到止盈线" not in out


@pytest.mark.parametrize("close", [99.0, 100.0])
def test_settle_bullcall_below_long_full_loss(bullcall_market, tmp_path, capsys, no_report,
                                              close):
    s = _settle_bullcall(bullcall_market, tmp_path, close)
    # value 0 -> (0 - 1.90) x 100 = -$190; -190 / 310 = -0.6129
    assert s["status"] == "expired" and s["exit_date"] == EXPIRY
    assert s["exit_value"] == 0.0 and s["pnl"] == -190.0 and s["result"] == "loss"
    assert s["fraction_of_max_captured"] == -0.6129 and s["exit_underlying"] == close
    out = capsys.readouterr().out
    assert "已到期结算" in out and "亏损" in out and "-$190" in out


def test_settle_bullcall_at_breakeven_zero_loss(bullcall_market, tmp_path, capsys, no_report):
    s = _settle_bullcall(bullcall_market, tmp_path, 101.90)
    # value 1.90 -> pnl 0.0 (never -0.0) -> loss
    assert s["exit_value"] == pytest.approx(1.90) and s["result"] == "loss"
    assert s["pnl"] == 0.0 and str(s["pnl"]) == "0.0"
    assert s["fraction_of_max_captured"] == 0.0 and str(s["fraction_of_max_captured"]) == "0.0"
    assert "-$0" not in capsys.readouterr().out


@pytest.mark.parametrize("contracts,pnl", [(1, 110.0), (2, 220.0)])
def test_settle_bullcall_between_strikes_partial(bullcall_market, tmp_path, no_report,
                                                 contracts, pnl):
    s = _settle_bullcall(bullcall_market, tmp_path, 103.0, contracts=contracts)
    # value 3.00 -> (3.00 - 1.90) x 100 x n; 110 / 310 = 0.3548 (220 / 620 the same)
    assert s["exit_value"] == pytest.approx(3.0) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == "win" and s["fraction_of_max_captured"] == 0.3548


@pytest.mark.parametrize("close", [105.0, 120.0])
def test_settle_bullcall_above_short_clamped(bullcall_market, tmp_path, no_report, close):
    s = _settle_bullcall(bullcall_market, tmp_path, close)
    # value clamped to width 5 -> (5 - 1.90) x 100 = +$310, the full max profit
    assert s["exit_value"] == 5.0 and s["pnl"] == pytest.approx(310.0)
    assert s["result"] == "win" and s["fraction_of_max_captured"] == 1.0


def test_settle_bullcall_writes_no_exit_cost_or_breached_side(bullcall_market, tmp_path,
                                                              no_report):
    s = _settle_bullcall(bullcall_market, tmp_path, 103.0)
    for key in ("exit_cost", "breached_side", "credit", "short_strike", "long_strike",
                "net_credit", "lot_ref", "stock_leg_pnl"):
        assert key not in s


def test_bullcall_waits_on_expiry_day(bullcall_market, tmp_path, capsys, no_report):
    _seed_bullcall(tmp_path)
    bullcall_market["closes"][date.fromisoformat(EXPIRY)] = 120.0
    assert run(tmp_path, "status", today=date.fromisoformat(EXPIRY)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "次日或之后再运行" in capsys.readouterr().out


@pytest.mark.parametrize("long_mid,short_mid,value,pnl,result,fraction", [
    (4.00, 1.60, 2.40, 50.0, "win", 0.1613),      # 2.40 - 1.90 = +0.50 x 100; 50 / 310
    (6.00, 0.50, 5.00, 310.0, "win", 1.0),        # 5.50 clamped to width 5
    (0.90, 1.00, 0.00, -190.0, "loss", -0.6129),  # -0.10 clamped to 0
])
def test_close_bullcall_uses_two_mids_clamped(bullcall_market, tmp_path, capsys, no_report,
                                              long_mid, short_mid, value, pnl, result, fraction):
    _seed_bullcall(tmp_path)
    bullcall_market["calls"] = _legs((100.0, long_mid), (105.0, short_mid))
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["exit_date"] == "2026-10-20"
    assert s["exit_value"] == pytest.approx(value) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_max_captured"] == fraction
    assert s["exit_long_call_mid"] == pytest.approx(long_mid)
    assert s["exit_short_call_mid"] == pytest.approx(short_mid)
    assert s["exit_long_call_iv"] == 0.4 and s["exit_short_call_iv"] == 0.4
    assert s["exit_underlying"] == 103.0
    assert "exit_cost" not in s and "breached_side" not in s
    assert "已提前平仓 #1 ZZZQ 100/105 Call 借方价差" in capsys.readouterr().out


def test_close_bullcall_skips_missing_ivs(bullcall_market, tmp_path, no_report):
    _seed_bullcall(tmp_path)
    for row in bullcall_market["calls"]:
        row["iv"] = None
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(50.0)
    assert "exit_long_call_iv" not in s and "exit_short_call_iv" not in s


@pytest.mark.parametrize("keep", [100.0, 105.0])
def test_close_bullcall_refuses_without_quote(bullcall_market, tmp_path, capsys, no_report,
                                              keep):
    _seed_bullcall(tmp_path)
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    bullcall_market["calls"] = [r for r in bullcall_market["calls"] if r["strike"] == keep]
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 1
    assert "错误" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_close_after_expiry_refused_for_bullcall(bullcall_market, tmp_path, capsys, no_report):
    _seed_bullcall(tmp_path)
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=date(2026, 10, 24)) == 1
    assert "过到期日" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_bullcall_never_calls_seller_helpers(bullcall_market, tmp_path, monkeypatch, no_report):
    def boom(*a, **k):
        raise AssertionError("seller helper used for a bull call spread")
    for name in ("threshold_flags", "cost_to_close", "unrealized_pnl", "close_pnl", "expiry_pnl",
                 "breakeven_win_rate", "expiry_cost", "csp_expiry_pnl", "cc_expiry_pnl",
                 "csp_threshold_flags", "collar_unrealized_pnl", "collar_close_pnl",
                 "collar_expiry_option_pnl", "condor_cost_to_close", "condor_unrealized_pnl",
                 "condor_close_pnl", "condor_expiry_pnl"):
        monkeypatch.setattr(spread_sim.core, name, boom)
    _seed_bullcall(tmp_path)
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert run(tmp_path, "close", "1", today=date(2026, 10, 20)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "closed_early"
    other = tmp_path / "settle"
    other.mkdir()
    _seed_bullcall(other)
    bullcall_market["closes"][date.fromisoformat(EXPIRY)] = 103.0
    assert run(other, "status", today=date(2026, 10, 26)) == 0
    assert ledger(other)["spreads"][0]["status"] == "expired"


@pytest.mark.parametrize("failure", ["no_quote", "data_error"])
def test_status_continues_past_a_bullcall_error(bullcall_market, tmp_path, capsys, monkeypatch,
                                                no_report, failure):
    def calls(t, e, strikes=()):
        if t != "ZZZQ":
            return bullcall_market["calls"]
        if failure == "data_error":
            raise spread_data.DataError("ZZZQ 期权链挂了")
        return []
    monkeypatch.setattr(spread_data, "fetch_calls", calls)
    _seed_bullcall(tmp_path, _bullcall_rec(), _bullcall_rec(ticker="ZZZR"),
                   _bullcall_rec(ticker="ZZZS", expiry="2026-10-16"))
    bullcall_market["closes"][date(2026, 10, 16)] = 103.0
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    lines = capsys.readouterr().out.splitlines()
    bad = next(line for line in lines if line.startswith("#1 ZZZQ"))
    assert "找不到" in bad or "挂了" in bad
    assert any(line.startswith("#2 ZZZR") and "浮盈亏 +$50" in line for line in lines)
    s1, s2, s3 = ledger(tmp_path)["spreads"]
    assert s1["status"] == "open" and s2["status"] == "open"
    assert s3["status"] == "expired" and s3["pnl"] == pytest.approx(110.0)


def test_paper_shares_untouched_by_bullcall(bullcall_market, tmp_path, capsys, no_report):
    import paper_shares
    doc, _ = paper_shares.add_lot(paper_shares.empty_lots(), ticker="ZZZQ", shares=100,
                                  cost=95.0, opened_on="2026-10-01")
    lots = tmp_path / paper_shares.LOTS_FILE
    paper_shares.save_lots(lots, doc)
    before = lots.read_bytes()
    _seed_bullcall(tmp_path, _bullcall_rec(), _bullcall_rec(ticker="ZZZR"))
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    assert run(tmp_path, "close", "2", today=date(2026, 10, 20)) == 0
    bullcall_market["closes"][date.fromisoformat(EXPIRY)] = 120.0  # short call in the money
    assert run(tmp_path, "status", today=date(2026, 10, 26)) == 0
    assert lots.read_bytes() == before
    assert "假设持仓" not in capsys.readouterr().out
    for s in ledger(tmp_path)["spreads"]:
        assert "lot_applied" not in s and "lot_ref" not in s
    assert [s["status"] for s in ledger(tmp_path)["spreads"]] == ["expired", "closed_early"]


def test_existing_strategies_status_and_close_unchanged_alongside_bullcall(
        bullcall_market, tmp_path, capsys, no_report):
    """A bull put spread behaves the same whether or not a bull call spread shares the ledger.

    (`test_existing_strategies_status_and_close_unchanged` already exists above; this is the
    bull call variant, named apart so it does not shadow that one.)"""
    alone, mixed = tmp_path / "alone", tmp_path / "mixed"
    outputs, records = [], []
    for folder in (alone, mixed):
        folder.mkdir()
        bullcall_market["puts"], bullcall_market["spot"] = make_chain(spot=100.0), 100.0
        assert opened(folder, "ABC", "0.20") == 0
        if folder is mixed:
            led, _ = spread_ledger.add_spread(ledger(folder), _bullcall_rec())
            spread_ledger.save(folder / "spreads.json", led)
        capsys.readouterr()
        bullcall_market["puts"], bullcall_market["spot"] = make_chain(spot=104.0), 104.0
        assert run(folder, "status", today=date(2026, 10, 20)) == 0
        abc = [line for line in capsys.readouterr().out.splitlines() if line.startswith("#1 ABC")]
        assert run(folder, "close", "1", today=date(2026, 10, 20)) == 0
        outputs.append((abc, capsys.readouterr().out))
        records.append(ledger(folder)["spreads"][0])
    assert outputs[0] == outputs[1] and records[0] == records[1]
    assert outputs[0][0] and "exit_value" not in records[0]
    assert "fraction_of_max_captured" not in records[0]
    assert ledger(mixed)["spreads"][1]["status"] == "open"
    assert spread_sim._line(_condor_rec(id=4)) == "#4 ZZZQ 85/90/110/115 铁鹰 到期 2026-10-23"
    assert spread_sim._line(_bullcall_rec(id=5)) == "#5 ZZZQ 100/105 Call 借方价差 到期 2026-10-23"
