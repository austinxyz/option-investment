"""CLI tests for scripts/call_screen.py. Market data and positions are faked; ledger in tmp_path."""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import call_screen
import holdings
import ibkr_session
import paper_shares
import spread_data
import spread_ledger
from spread_fixtures import make_call_chain

SNAP = date(2026, 10, 16)
TODAY = date(2026, 10, 17)
EXPIRY = "2026-10-23"
AFTER = date(2026, 10, 26)


def real_lot(ticker="AAA", shares=100.0, cost=90.0):
    return holdings.HeldLot(
        source="real", lot_id=None, ticker=ticker, shares=shares, cost_basis=cost,
        original_cost=cost, reference_price=cost, cost_estimated=False, accounts=1,
        label=f"{ticker}（真实）")


def _raise_if(exc):
    if exc is not None:
        raise exc


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"fail": set(), "real": [real_lot()], "close_on": 90.0, "gateway": None, "spot": 100.0}
    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: _raise_if(m["gateway"]))
    monkeypatch.setattr(holdings, "real_lots", lambda *a, **k: list(m["real"]))

    def last_close(t):
        if t in m["fail"]:
            raise spread_data.DataError("网络挂了")
        return SNAP, m["spot"]

    monkeypatch.setattr(spread_data, "last_close", last_close)
    monkeypatch.setattr(spread_data, "moving_averages",
                        lambda t: {"snapshot_date": SNAP, "close": 100.0, "ma50": 95.0, "ma200": 90.0})
    monkeypatch.setattr(spread_data, "month_return_pct", lambda t: 3.0)
    monkeypatch.setattr(spread_data, "expirations", lambda t: [EXPIRY])
    monkeypatch.setattr(spread_data, "fetch_calls",
                        lambda t, e: make_call_chain(spot=100.0, days=7, iv=0.40, quote_spread=0.01))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: m["close_on"])
    monkeypatch.setattr(spread_data, "monday_gap_pct", lambda *a, **k: None)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=TODAY):
    return call_screen.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug(env, name):
    return env["ledger"] / "suggestions" / name


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_ledger(env, spreads):
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json",
                       {**spread_ledger.empty_ledger(), "spreads": spreads})


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def test_suggest_writes_real_and_paper_lot_candidates(env, capsys):
    assert run(env, "shares", "add", "BBB", "--shares", "200", "--cost", "50") == 0
    assert run(env, "suggest") == 0
    for name in (f"{SNAP}-AAA-call-real.md", f"{SNAP}-AAA-call-real.json",
                 f"{SNAP}-BBB-call-lot1.md", f"{SNAP}-BBB-call-lot1.json"):
        assert sug(env, name).exists(), name
    for name in (f"call-screen/{SNAP}.md", f"call-screen/{SNAP}.json"):
        assert (env["ledger"] / name).exists(), name
    assert read_json(sug(env, f"{SNAP}-BBB-call-lot1.json"))["contracts_available"] == 2


def test_suggest_rejects_fractional_real_holding(env):
    env["real"] = [real_lot(shares=50.0)]
    assert run(env, "suggest") == 0
    summary = read_json(env["ledger"] / "call-screen" / f"{TODAY}.json")
    assert {"ticker": "AAA（真实）", "reason": "股数不足 100 股"} in summary["rejects"]
    assert not list((env["ledger"] / "suggestions").glob("*call*"))


def test_suggest_skips_already_covered_lot_with_reason(env):
    save_ledger(env, [{
        "id": 1, "ticker": "AAA", "strategy": "covered_call", "status": "open", "contracts": 1,
        "expiry": "2026-10-30", "lot_ref": {"source": "real", "lot_id": None, "ticker": "AAA"}}])
    assert run(env, "suggest") == 0
    summary = read_json(env["ledger"] / "call-screen" / f"{TODAY}.json")
    assert {"ticker": "AAA（真实）", "reason": "已被未平仓 call 覆盖"} in summary["rejects"]
    assert summary["candidates"] == []


def test_suggest_creates_lot_from_assigned_put_and_offers_call(env, capsys):
    env["real"] = []
    save_ledger(env, [{
        "id": 1, "ticker": "CCC", "strategy": "cash_secured_put", "status": "expired",
        "result": "assigned", "short_strike": 95.0, "credit": 2.0, "contracts": 1,
        "expiry": "2026-10-09", "exit_date": "2026-10-09", "exit_underlying": 90.0, "pnl": -100.0,
        "snapshot_date": "2026-10-02"}])
    assert run(env, "suggest") == 0
    assert "被指派 #1" in capsys.readouterr().out
    lots = paper_shares.load_lots(env["ledger"] / "paper_shares.json")["lots"]
    assert lots[0]["ticker"] == "CCC" and lots[0]["cost_basis"] == 93.0
    assert lots[0]["reference_price"] == 90.0
    doc = read_json(sug(env, f"{SNAP}-CCC-call-lot1.json"))
    assert doc["source"] == "assigned_put" and doc["original_cost"] == 93.0
    assert doc["cost_basis"] == 93.0 and doc["reference_price"] == 90.0


def test_suggest_never_writes_ledger_or_positions(env):
    assert run(env, "suggest") == 0
    assert not (env["ledger"] / "spreads.json").exists()
    save_ledger(env, [{"id": 1, "ticker": "ZZZ", "strategy": "cash_secured_put",
                       "status": "open", "contracts": 1, "expiry": "2026-11-20"}])
    before = (env["ledger"] / "spreads.json").read_bytes()
    assert run(env, "suggest") == 0
    assert (env["ledger"] / "spreads.json").read_bytes() == before
    assert not (env["ledger"] / "paper_shares.json").exists()


def test_suggest_rerun_replaces_same_day_call_files_only(env):
    assert run(env, "suggest") == 0
    folder = env["ledger"] / "suggestions"
    keep = [f"{SNAP}-AAA-put.md", f"{SNAP}-AAA-put.json", f"{SNAP}-AAA.json", f"{SNAP}-AAA.md"]
    for name in keep:
        (folder / name).write_text("x", encoding="utf-8")
    stale = folder / f"{SNAP}-ZZZ-call-lot9.json"
    stale.write_text("{}", encoding="utf-8")
    assert run(env, "suggest") == 0
    assert not stale.exists()
    assert all((folder / n).read_text(encoding="utf-8") == "x" for n in keep)
    assert (folder / f"{SNAP}-AAA-call-real.json").exists()


def test_suggest_continues_when_one_ticker_fails(env):
    env["real"] = [real_lot("AAA"), real_lot("BBB")]
    env["fail"].add("BBB")
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "call-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "BBB（真实） — 取行情失败" in md.split("## 被剔除")[1]
    assert sug(env, f"{SNAP}-AAA-call-real.json").exists()


def _suggest_open(env, *extra, today=TODAY):
    assert run(env, "suggest") == 0
    return run(env, "open", "AAA", *extra, today=today)


def test_open_records_covered_call_with_lot_ref_and_costs(env, monkeypatch):
    env["real"] = [real_lot(shares=300.0, cost=88.0)]
    assert run(env, "suggest") == 0
    doc = read_json(sug(env, f"{SNAP}-AAA-call-real.json"))
    monkeypatch.setattr(spread_data, "fetch_calls", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert run(env, "open", "AAA", "--contracts", "2") == 0
    rec = ledger(env)["spreads"][0]
    assert rec["strategy"] == "covered_call" and rec["contracts"] == 2
    assert rec["short_strike"] == doc["strike"] and rec["credit"] == doc["mid"]
    assert rec["long_strike"] is None and rec["width"] is None
    assert rec["lot_ref"] == {"source": "real", "lot_id": None, "ticker": "AAA"}
    assert rec["cost_basis"] == 88.0 and rec["reference_price"] == 88.0
    assert rec["covered_shares"] == 200 and rec["status"] == "open"
    assert rec["suggestion_file"] == f"{SNAP}-AAA-call-real.md"
    assert (env["ledger"] / "report.md").exists()


def test_open_requires_lot_when_ambiguous(env, capsys):
    assert run(env, "shares", "add", "AAA", "--shares", "100", "--cost", "80") == 0
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA") == 1
    err = capsys.readouterr().err
    assert "--lot" in err and "real" in err and "1" in err
    assert run(env, "open", "AAA", "--lot", "real") == 0


def test_open_refuses_more_contracts_than_available(env, capsys):
    env["real"] = [real_lot(shares=300.0)]
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA", "--contracts", "4") == 1
    assert "最多可再卖 3 张" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_refuses_expired_candidate(env, capsys):
    assert _suggest_open(env, today=date(2026, 10, 23)) == 1
    assert "已过期" in capsys.readouterr().err


def test_open_refuses_damaged_candidate(env, capsys):
    assert run(env, "suggest") == 0
    path = sug(env, f"{SNAP}-AAA-call-real.json")
    doc = read_json(path)
    doc["strike"] = "abc"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "AAA") == 1
    assert "suggest" in capsys.readouterr().err


def test_open_duplicate_other_strategy_same_expiry_refused(env, capsys):
    assert run(env, "suggest") == 0
    save_ledger(env, [{"id": 1, "ticker": "AAA", "strategy": "cash_secured_put",
                       "status": "open", "contracts": 1, "expiry": EXPIRY}])
    assert run(env, "open", "AAA") == 1
    assert "已经有记录" in capsys.readouterr().err


def test_open_same_ticker_other_lot_same_expiry_allowed(env):
    assert run(env, "shares", "add", "AAA", "--shares", "100", "--cost", "80") == 0
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA", "--lot", "real") == 0
    assert run(env, "open", "AAA", "--lot", "1") == 0
    assert len(ledger(env)["spreads"]) == 2
    assert run(env, "open", "AAA", "--lot", "1") == 1  # now fully covered


def test_shares_add_list_remove(env, capsys):
    assert run(env, "shares", "add", "bbb", "--shares", "150", "--cost", "42.5") == 0
    capsys.readouterr()
    assert run(env, "shares", "list") == 0
    out = capsys.readouterr().out
    assert "#1 BBB 150 股" in out and "42.50" in out
    assert run(env, "shares", "remove", "1") == 0
    assert paper_shares.load_lots(env["ledger"] / "paper_shares.json")["lots"] == []
    with pytest.raises(SystemExit):
        run(env, "shares", "add", "BBB", "--shares", "-5", "--cost", "10")
    assert run(env, "shares", "remove", "9") == 1


def test_shares_remove_refused_with_history(env, capsys):
    env["real"] = []
    assert run(env, "shares", "add", "AAA", "--shares", "100", "--cost", "80") == 0
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA") == 0
    capsys.readouterr()
    assert run(env, "shares", "remove", "1") == 1
    assert "不能删除" in capsys.readouterr().err
    assert len(paper_shares.load_lots(env["ledger"] / "paper_shares.json")["lots"]) == 1


def test_status_settles_and_updates_lot(env, capsys):
    env["real"] = []
    save_ledger(env, [{
        "id": 1, "ticker": "AAA", "strategy": "cash_secured_put", "status": "expired",
        "result": "assigned", "short_strike": 95.0, "credit": 2.0, "contracts": 1,
        "expiry": "2026-10-09", "exit_date": "2026-10-09", "exit_underlying": 93.0, "pnl": -100.0,
        "snapshot_date": "2026-10-02", "underlying_price": 96.0}])
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA") == 0
    mid = ledger(env)["spreads"][1]["credit"]
    env["close_on"] = 90.0  # expires worthless
    assert run(env, "status", today=AFTER) == 0
    call = ledger(env)["spreads"][1]
    assert call["status"] == "expired" and call["lot_applied"] is True
    lot = paper_shares.load_lots(env["ledger"] / "paper_shares.json")["lots"][0]
    assert lot["cost_basis"] == pytest.approx(93.0 - mid, abs=1e-4)
    assert lot["shares"] == 100 and lot["applied_ids"] == [call["id"]]


def _paper_lot(env, *, cost_basis, reference_price, shares=200.0):
    env["real"] = []
    env["ledger"].mkdir(parents=True, exist_ok=True)
    path = env["ledger"] / "paper_shares.json"
    doc, _ = paper_shares.add_lot(
        paper_shares.empty_lots(),
        ticker="AAA", shares=shares, cost=80.0, opened_on="2026-10-01")
    doc["lots"][0]["cost_basis"] = cost_basis
    doc["lots"][0]["reference_price"] = reference_price
    paper_shares.save_lots(path, doc)
    return path


def test_open_uses_live_availability_not_saved_value(env, capsys):
    env["real"] = [real_lot(shares=300.0)]
    assert run(env, "suggest") == 0
    assert read_json(sug(env, f"{SNAP}-AAA-call-real.json"))["contracts_available"] == 3
    save_ledger(env, [{"id": 1, "ticker": "AAA", "strategy": "covered_call", "status": "open",
                       "contracts": 2, "expiry": "2026-10-30",
                       "lot_ref": {"source": "real", "lot_id": None, "ticker": "AAA"}}])
    assert run(env, "open", "AAA", "--contracts", "2") == 1
    assert "最多可再卖 1 张" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_refuses_when_lot_no_longer_held(env, capsys):
    assert run(env, "suggest") == 0
    env["real"] = []
    assert run(env, "open", "AAA") == 1
    assert "已不存在" in capsys.readouterr().err


def test_suggest_fetches_once_per_ticker_and_skips_unavailable_lots(env, monkeypatch):
    calls = {"last_close": [], "fetch_calls": []}
    real_close, real_chain = spread_data.last_close, spread_data.fetch_calls

    def counting_close(t):
        calls["last_close"].append(t)
        return real_close(t)

    def counting_chain(t, e):
        calls["fetch_calls"].append(t)
        return real_chain(t, e)

    monkeypatch.setattr(spread_data, "last_close", counting_close)
    monkeypatch.setattr(spread_data, "fetch_calls", counting_chain)
    env["real"] = [real_lot("AAA"), real_lot("BBB", shares=50.0), real_lot("CCC")]
    save_ledger(env, [{"id": 1, "ticker": "CCC", "strategy": "covered_call", "status": "open",
                       "contracts": 1, "expiry": "2026-10-30",
                       "lot_ref": {"source": "real", "lot_id": None, "ticker": "CCC"}}])
    assert run(env, "shares", "add", "AAA", "--shares", "100", "--cost", "80") == 0
    assert run(env, "suggest") == 0
    assert calls["last_close"] == ["AAA"]
    assert calls["fetch_calls"] == ["AAA"]  # real + paper AAA lots share one chain
    assert sug(env, f"{SNAP}-AAA-call-lot1.json").exists()


def test_open_record_has_all_option_fields_and_live_basis(env):
    _paper_lot(env, cost_basis=70.0, reference_price=85.0)
    assert run(env, "suggest") == 0
    doc = read_json(sug(env, f"{SNAP}-AAA-call-lot1.json"))
    assert run(env, "open", "AAA") == 0
    rec = ledger(env)["spreads"][0]
    assert (rec["short_mid"], rec["short_delta"], rec["short_iv"]) == (
        doc["mid"], doc["delta"], doc["iv"])
    assert rec["short_spread_pct"] == doc["spread_pct"] and rec["long_mid"] is None
    assert rec["delta_target"] == call_screen.rules.TARGET_DELTA and rec["dte_mode"] == "weekly"
    assert rec["earnings_in_window"] is False
    assert rec["monday_gap_pct"] is None and rec["tier"] is None
    assert rec["status"] == "open"
    for key in ("exit_date", "exit_cost", "exit_underlying", "pnl", "result"):
        assert rec[key] is None, key
    assert rec["lot_ref"] == {"source": "manual", "lot_id": 1, "ticker": "AAA"}
    assert rec["cost_basis"] == 70.0 and rec["reference_price"] == 85.0
    assert rec["covered_shares"] == 100 and rec["underlying_price"] == doc["spot"]


def test_open_takes_basis_from_live_lot_when_it_changed(env):
    path = _paper_lot(env, cost_basis=70.0, reference_price=85.0)
    assert run(env, "suggest") == 0
    lots = paper_shares.load_lots(path)
    lots["lots"][0]["cost_basis"] = 65.5
    lots["lots"][0]["reference_price"] = 90.0
    paper_shares.save_lots(path, lots)
    assert run(env, "open", "AAA") == 0
    rec = ledger(env)["spreads"][0]
    assert rec["cost_basis"] == 65.5 and rec["reference_price"] == 90.0


def _write_summary(env, stamp, candidates, rejects):
    folder = env["ledger"] / "call-screen"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "snapshot": stamp, "candidates": candidates, "rejects": rejects}
    (folder / f"{stamp}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_open_refuses_candidate_rejected_by_newer_screen(env, capsys):
    assert run(env, "suggest") == 0  # Monday-style offer, stamp SNAP
    _write_summary(env, "2026-10-17", [], [{"ticker": "AAA（真实）", "reason": "到期前有财报"}])
    assert run(env, "open", "AAA", "--lot", "real", today=date(2026, 10, 17)) == 1
    err = capsys.readouterr().err
    assert "2026-10-17 的筛选里 AAA（真实） 被剔除：到期前有财报，请重新运行 suggest" in err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_refuses_when_newer_screen_did_not_select_it(env, capsys):
    assert run(env, "suggest") == 0
    _write_summary(env, "2026-10-17", [], [])
    assert run(env, "open", "AAA") == 1
    assert "本次筛选没有选出它" in capsys.readouterr().err


def test_open_ok_when_newer_screen_still_offers_it(env):
    assert run(env, "suggest") == 0
    cand = {"ticker": "AAA", "source": "real", "lot_id": None, "label": "AAA（真实）"}
    _write_summary(env, "2026-10-17", [cand], [])
    assert run(env, "open", "AAA") == 0


def test_open_ambiguity_ignores_older_stamp_of_another_lot(env):
    assert run(env, "shares", "add", "AAA", "--shares", "100", "--cost", "80") == 0
    assert run(env, "suggest") == 0
    older = sug(env, f"{SNAP}-AAA-call-lot1.json")
    older.rename(sug(env, "2026-10-15-AAA-call-lot1.json"))
    sug(env, f"{SNAP}-AAA-call-lot1.md").unlink()
    assert run(env, "open", "AAA") == 0
    assert ledger(env)["spreads"][0]["lot_ref"]["lot_id"] is None


def test_open_lot_with_leading_zeros_matches(env):
    assert run(env, "shares", "add", "AAA", "--shares", "100", "--cost", "80") == 0
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA", "--lot", "001") == 0
    assert ledger(env)["spreads"][0]["lot_ref"]["lot_id"] == 1


def test_malformed_paper_shares_gives_chinese_error_not_traceback(env, capsys):
    env["ledger"].mkdir(parents=True, exist_ok=True)
    (env["ledger"] / "paper_shares.json").write_text(
        json.dumps({"version": 1, "next_id": 2, "lots": [{"id": 1, "ticker": "AAA"}]}),
        encoding="utf-8")
    for argv in (("shares", "list"), ("suggest",)):
        assert run(env, *argv) == 1
        assert "错误：" in capsys.readouterr().err


def test_assigned_lot_label_shows_put_id_and_lot_id(env):
    env["real"] = []
    save_ledger(env, [{
        "id": 5, "ticker": "CCC", "strategy": "cash_secured_put", "status": "expired",
        "result": "assigned", "short_strike": 95.0, "credit": 2.0, "contracts": 1,
        "expiry": "2026-10-09", "exit_date": "2026-10-09", "exit_underlying": 90.0, "pnl": -100.0,
        "snapshot_date": "2026-10-02"}])
    assert run(env, "suggest") == 0
    doc = read_json(sug(env, f"{SNAP}-CCC-call-lot1.json"))
    assert doc["label"] == "CCC（被指派 put #5，持仓 #1）"
    assert doc["cost_basis"] == 93.0 and doc["reference_price"] == 90.0


def test_wheel_called_away_end_to_end(env, capsys):
    env["real"] = []
    save_ledger(env, [{
        "id": 1, "ticker": "AAA", "strategy": "cash_secured_put", "status": "expired",
        "result": "assigned", "short_strike": 95.0, "credit": 2.0, "contracts": 1,
        "expiry": "2026-10-09", "exit_date": "2026-10-09", "exit_underlying": 90.0, "pnl": -100.0,
        "snapshot_date": "2026-10-02", "underlying_price": 96.0}])
    assert run(env, "suggest") == 0
    assert run(env, "open", "AAA") == 0
    call = ledger(env)["spreads"][1]
    env["close_on"] = call["short_strike"] + 5.0  # finishes above the strike
    assert run(env, "status", today=AFTER) == 0
    call = ledger(env)["spreads"][1]
    assert call["result"] == "called_away" and call["lot_applied"] is True
    lot = paper_shares.load_lots(env["ledger"] / "paper_shares.json")["lots"][0]
    assert lot["shares"] == 0 and lot["status"] == "called_away"
    assert lot["realized_stock_pnl"] == pytest.approx((call["short_strike"] - 90.0) * 100)
    assert lot["applied_ids"] == [call["id"]]


DOWN = ibkr_session.GatewayUnavailable("连不上 gw.test:14999：连接被拒绝")


def test_suggest_aborts_before_writing_when_gateway_down(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest") == 1
    assert "gw.test:14999" in capsys.readouterr().err
    for name in ("suggestions", "call-screen", "paper_shares.json"):
        assert not (env["ledger"] / name).exists(), name
    assert not env["ledger"].exists() or not list(env["ledger"].iterdir())


def test_gateway_error_mid_run_is_not_a_per_ticker_reject(env, monkeypatch, capsys):
    env["real"] = [real_lot("AAA"), real_lot("BBB")]

    def last_close(t):
        if t == "BBB":
            raise DOWN
        return SNAP, 100.0
    monkeypatch.setattr(spread_data, "last_close", last_close)
    assert run(env, "suggest") == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert not (env["ledger"] / "call-screen").exists()


def test_gateway_error_in_chain_fetch_aborts_run(env, monkeypatch, capsys):
    def calls(t, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "fetch_calls", calls)
    assert run(env, "suggest") == 1
    assert not (env["ledger"] / "call-screen").exists()


def test_single_ticker_without_chain_is_a_reject_and_run_continues(env, monkeypatch):
    env["real"] = [real_lot("AAA"), real_lot("BBB")]

    def calls(t, e):
        if t == "BBB":
            raise spread_data.DataError("no chain")
        return make_call_chain(spot=100.0, days=7, iv=0.40, quote_spread=0.01)
    monkeypatch.setattr(spread_data, "fetch_calls", calls)
    assert run(env, "suggest") == 0
    summary = read_json(env["ledger"] / "call-screen" / f"{SNAP}.json")
    assert {"ticker": "BBB（真实）", "reason": "取不到期权链"} in summary["rejects"]
    assert sug(env, f"{SNAP}-AAA-call-real.json").exists()


def test_call_screen_uses_ibkr_spot_and_snapshot(env):
    env["spot"] = 101.0
    assert run(env, "suggest") == 0
    doc = read_json(sug(env, f"{SNAP}-AAA-call-real.json"))
    assert doc["spot"] == 101.0 and doc["snapshot_date"] == SNAP.isoformat()


def test_candidate_and_summary_state_source_and_snapshot_day(env):
    assert run(env, "suggest") == 0
    doc = read_json(sug(env, f"{SNAP}-AAA-call-real.json"))
    assert doc["data_source"] == "IBKR" and doc["frozen"] is True
    summary = read_json(env["ledger"] / "call-screen" / f"{SNAP}.json")
    assert summary["data_source"] == "IBKR" and summary["frozen"] is True
    md = (env["ledger"] / "call-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert f"数据源 IBKR 模拟网关 · 基于 {SNAP} 收盘（冻结行情）" in md


def test_open_still_never_fetches(env, monkeypatch):
    assert run(env, "suggest") == 0
    import ibkr_data

    def boom(*a, **k):
        raise AssertionError("open must not fetch")
    for mod in (spread_data, ibkr_data, ibkr_session):
        for name in dir(mod):
            fn = getattr(mod, name)
            if (callable(fn) and not name.startswith("_") and not isinstance(fn, type)
                    and getattr(fn, "__module__", None) == mod.__name__):
                monkeypatch.setattr(mod, name, boom)
    assert run(env, "open", "AAA", "--lot", "real") == 0


def test_open_save_keeps_broker_block_written_meanwhile(env, monkeypatch):
    env["real"] = [real_lot(shares=300.0, cost=88.0)]
    assert _suggest_open(env) == 0
    path = env["ledger"] / "spreads.json"
    led = spread_ledger.load(path)
    led["spreads"][0]["ticker"] = "ZZZ"  # free the AAA slot; the lot is still only 1/3 covered
    led["spreads"][0]["lot_ref"] = {"source": "real", "lot_id": None, "ticker": "ZZZ"}
    spread_ledger.save(path, led)
    real, done = spread_ledger.add_spread, []

    def racing_add(ledger, record):
        out = real(ledger, record)
        if not done:  # a concurrent `send` lands after our load, before our save
            done.append(1)
            spread_ledger.save(path, spread_ledger.set_broker(
                spread_ledger.load(path), 1, {"status": "submitted", "order_ref": "rwh-1-LIVE0001"}))
        return out

    monkeypatch.setattr(spread_ledger, "add_spread", racing_add)
    assert run(env, "open", "AAA") == 0
    spreads = spread_ledger.load(path)["spreads"]
    assert len(spreads) == 2
    assert spreads[0]["broker"] == {"status": "submitted", "order_ref": "rwh-1-LIVE0001"}
