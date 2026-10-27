"""CLI tests for scripts/put_screen.py. Market data is faked; ledger and wiki live in tmp_path."""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ibkr_session
import put_screen
import spread_data
import spread_ledger
from spread_fixtures import make_chain

SNAP = date(2026, 10, 16)
TODAY = date(2026, 10, 18)
EXPIRY = "2026-11-20"
EXPIRATIONS = ["2026-10-23", "2026-11-13", EXPIRY, "2026-12-18"]


def _add_ticker(wiki: Path, ticker: str, status: str = "Watch") -> None:
    d = wiki / "tickers" / ticker
    d.mkdir(parents=True)
    (d / "overview.md").write_text(f"**Status**: {status}\n", encoding="utf-8")


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"fail": set(), "no_monthly": False, "open": False}
    wiki = tmp_path / "wiki"
    (wiki / "sectors").mkdir(parents=True)
    _add_ticker(wiki, "AAA")
    monkeypatch.setattr(put_screen, "WIKI_DIR", wiki)

    def mas(t):
        if t in m["fail"]:
            raise spread_data.DataError("网络挂了")
        return {"snapshot_date": SNAP, "close": 100.0, "ma50": 95.0, "ma200": 90.0}

    monkeypatch.setattr(spread_data, "moving_averages", mas)
    m["spot"] = 100.0
    m["gateway"] = None  # an exception to raise from ensure_gateway
    m["last_close_error"] = {}  # ticker -> exception raised by last_close

    def last_close(t):
        if t in m["last_close_error"]:
            raise m["last_close_error"][t]
        return SNAP, m["spot"]

    def ensure_gateway():
        if m["gateway"] is not None:
            raise m["gateway"]

    monkeypatch.setattr(ibkr_session, "ensure_gateway", ensure_gateway)
    monkeypatch.setattr(spread_data, "last_close", last_close)
    monkeypatch.setattr(spread_data, "expirations",
                        lambda t: ["2026-10-23"] if m["no_monthly"] else EXPIRATIONS)
    monkeypatch.setattr(spread_data, "fetch_puts",
                        lambda t, e, strikes=(): make_chain(spot=100.0, days=35, iv=0.40,
                                                strikes=[float(k) for k in range(70, 100)]))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "market_open_now", lambda: m["open"])
    m["ledger"] = tmp_path / "ledger"
    m["wiki"] = wiki
    return m


def run(env, *argv):
    """Legacy tests assume monthly mode, so `suggest` defaults to --dte monthly here;
    the weekly/default-mode tests pass --dte explicitly or call main() directly."""
    if argv == ("suggest",):
        argv = ("suggest", "--dte", "monthly")
    return put_screen.main(["--ledger-dir", str(env["ledger"]), *argv], today=TODAY)


def run_raw(env, *argv):
    return put_screen.main(["--ledger-dir", str(env["ledger"]), *argv], today=TODAY)


def test_suggest_writes_per_ticker_and_summary_files(env):
    assert run(env, "suggest") == 0
    led = env["ledger"]
    for name in (f"suggestions/{SNAP}-AAA-put.md", f"suggestions/{SNAP}-AAA-put.json",
                 f"put-screen/{SNAP}.md", f"put-screen/{SNAP}.json"):
        assert (led / name).exists(), name
    assert not (led / "spreads.json").exists()


def test_suggest_continues_when_one_ticker_fails(env):
    _add_ticker(env["wiki"], "BBB")
    env["fail"].add("BBB")
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "BBB — 取行情失败" in md.split("## 被剔除")[1]
    assert (env["ledger"] / "suggestions" / f"{SNAP}-AAA-put.json").exists()


def test_suggest_no_monthly_writes_message(env):
    env["no_monthly"] = True
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "本日无合适月度到期" in md
    assert not list((env["ledger"] / "suggestions").glob("*.json"))


def _suggest(env):
    assert run(env, "suggest") == 0
    path = env["ledger"] / "suggestions" / f"{SNAP}-AAA-put.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def _boom(*a, **k):
    raise AssertionError("open must not touch the network")


def test_open_records_saved_candidate_verbatim(env, monkeypatch):
    _, doc = _suggest(env)
    monkeypatch.setattr(spread_data, "fetch_puts", _boom)
    monkeypatch.setattr(spread_data, "last_close", _boom)
    assert run(env, "open", "AAA", "--contracts", "2") == 0
    led = spread_ledger.load(env["ledger"] / "spreads.json")
    rec = led["spreads"][0]
    assert rec["strategy"] == "cash_secured_put"
    assert rec["short_strike"] == doc["strike"] and rec["credit"] == doc["mid"]
    assert rec["short_delta"] == doc["delta"] and rec["contracts"] == 2
    assert rec["long_strike"] is None and rec["width"] is None
    assert rec["earnings_in_window"] is False and rec["status"] == "open"
    assert rec["suggestion_file"] == f"{SNAP}-AAA-put.md"
    assert (env["ledger"] / "report.md").exists()


def test_open_refuses_without_saved_suggestion(env, capsys):
    assert run(env, "open", "AAA") == 1
    assert "没有 AAA 的已保存候选" in capsys.readouterr().err


def test_open_refuses_expired_suggestion(env, capsys):
    path, doc = _suggest(env)
    doc["expiry"] = "2026-10-18"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "AAA") == 1
    assert "已过期" in capsys.readouterr().err


def test_open_refuses_damaged_suggestion(env, capsys):
    path, doc = _suggest(env)
    doc["strike"] = "abc"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "AAA") == 1
    assert "suggest" in capsys.readouterr().err


def test_open_refuses_same_ticker_expiry_already_open_as_spread(env, capsys):
    _, doc = _suggest(env)
    legacy = {"id": 1, "ticker": "AAA", "expiry": doc["expiry"], "status": "open", "contracts": 1}
    env["ledger"].mkdir(exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json",
                       {**spread_ledger.empty_ledger(), "spreads": [legacy]})
    assert run(env, "open", "AAA") == 1
    assert "已经有记录" in capsys.readouterr().err


def test_open_picks_newest_suggestion_file(env):
    path, doc = _suggest(env)
    older = path.with_name("2026-10-09-AAA-put.json")
    older.write_text(json.dumps({**doc, "strike": 50.0, "snapshot_date": "2026-10-09"}),
                     encoding="utf-8")
    assert run(env, "open", "AAA") == 0
    rec = spread_ledger.load(env["ledger"] / "spreads.json")["spreads"][0]
    assert rec["short_strike"] == doc["strike"]


def test_status_and_close_delegate_to_spread_sim(env, monkeypatch, capsys):
    _suggest(env)
    assert run(env, "open", "AAA") == 0
    path = env["ledger"] / "spreads.json"
    led = spread_ledger.load(path)
    csp = led["spreads"][0]
    led["spreads"].append({
        "id": 2, "ticker": "OLD", "expiry": "2026-12-18", "status": "open", "contracts": 1,
        "snapshot_date": "2026-10-16", "underlying_price": 100.0, "short_strike": 90.0,
        "long_strike": 85.0, "width": 5.0, "credit": 1.0, "monday_gap_pct": 0.0,
        "delta_bucket": 0.15, "short_delta": 0.15, "short_iv": 0.4, "short_spread_pct": 0.02,
        "long_spread_pct": 0.02, "earnings_in_window": False})
    spread_ledger.save(path, led)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, 100.0))
    capsys.readouterr()
    assert run(env, "status") == 0
    out = capsys.readouterr().out
    assert "AAA" in out and "OLD" in out
    assert run(env, "close", str(csp["id"])) == 0
    assert "已提前平仓" in capsys.readouterr().out


def test_suggest_dotted_ticker_keeps_its_own_files_and_open_finds_them(env):
    _add_ticker(env["wiki"], "BRK.B")
    assert run(env, "suggest") == 0
    folder = env["ledger"] / "suggestions"
    names = {p.name for p in folder.glob(f"{SNAP}-BRK.*")}
    assert names == {f"{SNAP}-BRK.B-put.md", f"{SNAP}-BRK.B-put.json"}
    assert run(env, "open", "BRK.B") == 0
    rec = spread_ledger.load(env["ledger"] / "spreads.json")["spreads"][0]
    assert rec["ticker"] == "BRK.B" and rec["suggestion_file"] == f"{SNAP}-BRK.B-put.md"


def test_open_ignores_spread_suggestion_file_without_put_suffix(env, capsys):
    folder = env["ledger"] / "suggestions"
    folder.mkdir(parents=True)
    (folder / f"{SNAP}-AAA.json").write_text("{}", encoding="utf-8")
    assert run(env, "open", "AAA") == 1
    assert "没有 AAA 的已保存候选" in capsys.readouterr().err


def test_suggest_removes_stale_same_day_candidate_but_keeps_spread_files(env, monkeypatch):
    assert run(env, "suggest") == 0
    folder = env["ledger"] / "suggestions"
    spread_md, spread_json = folder / f"{SNAP}-AAA.md", folder / f"{SNAP}-AAA.json"
    spread_md.write_text("spread", encoding="utf-8")
    spread_json.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(spread_data, "moving_averages", lambda t: {
        "snapshot_date": SNAP, "close": 100.0, "ma50": 150.0, "ma200": 200.0})
    assert run(env, "suggest") == 0
    assert not (folder / f"{SNAP}-AAA-put.json").exists()
    assert not (folder / f"{SNAP}-AAA-put.md").exists()
    assert spread_md.exists() and spread_json.exists()


def test_open_refuses_when_newest_screen_rejected_ticker(env, capsys):
    _suggest(env)
    summary = env["ledger"] / "put-screen" / f"{SNAP}.json"
    doc = json.loads(summary.read_text(encoding="utf-8"))
    doc["candidates"] = []
    doc["rejects"] = [{"ticker": "AAA", "reason": "收盘价低于 MA200"}]
    summary.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "AAA") == 1
    err = capsys.readouterr().err
    assert str(SNAP) in err and "收盘价低于 MA200" in err and "suggest" in err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_allowed_when_screen_lists_ticker_or_no_summary(env):
    path, _ = _suggest(env)
    assert run(env, "open", "AAA") == 0
    (env["ledger"] / "spreads.json").unlink()
    (env["ledger"] / "put-screen" / f"{SNAP}.json").unlink()
    assert run(env, "open", "AAA") == 0


def test_suggest_prescreen_rejects_cost_no_network(env, monkeypatch):
    (env["wiki"] / "tickers" / "AAA" / "overview.md").write_text("**Status**: Avoid\n",
                                                                 encoding="utf-8")
    monkeypatch.setattr(spread_data, "moving_averages", _boom)
    monkeypatch.setattr(spread_data, "expirations", _boom)
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "put-screen" / f"{TODAY}.md").read_text(encoding="utf-8")
    assert "AAA — Status 为 Avoid" in md


def test_suggest_survives_unexpected_exception_type(env, monkeypatch):
    _add_ticker(env["wiki"], "BBB")

    def mas(t):
        if t == "BBB":
            raise KeyError("Close")
        return {"snapshot_date": SNAP, "close": 100.0, "ma50": 95.0, "ma200": 90.0}

    monkeypatch.setattr(spread_data, "moving_averages", mas)
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "BBB — 取行情失败：KeyError" in md
    assert (env["ledger"] / "suggestions" / f"{SNAP}-AAA-put.json").exists()


def test_status_prints_csp_threshold_basis(env, monkeypatch, capsys):
    _suggest(env)
    assert run(env, "open", "AAA") == 0
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, 100.0))
    cheap = make_chain(spot=100.0, days=35, iv=0.40, strikes=[float(k) for k in range(70, 100)])
    for q in cheap:
        q["bid"], q["ask"], q["last"] = 0.01, 0.03, 0.02
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=(): cheap)
    capsys.readouterr()
    assert run(env, "status") == 0
    out = capsys.readouterr().out
    assert "🟢 已到止盈线（权利金的50%）" in out


def _summary(env):
    return json.loads((env["ledger"] / "put-screen" / f"{SNAP}.json").read_text(encoding="utf-8"))


def test_suggest_default_is_weekly(env):
    assert run_raw(env, "suggest") == 0
    summary = _summary(env)
    assert summary["rules"]["dte_mode"] == "weekly" and summary["rules"]["window_days"] == [7, 14]
    cand = summary["candidates"][0]
    assert cand["dte_mode"] == "weekly" and cand["expiry"] == "2026-10-23" and cand["days"] == 7
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "（周度）" in md.splitlines()[0] and "7–14 天最近到期" in md
    one = (env["ledger"] / "suggestions" / f"{SNAP}-AAA-put.md").read_text(encoding="utf-8")
    assert "Gamma 高" in one


def test_suggest_dte_monthly_reproduces_monthly(env):
    assert run_raw(env, "suggest", "--dte", "monthly") == 0
    summary = _summary(env)
    assert summary["rules"]["dte_mode"] == "monthly" and summary["rules"]["window_days"] == [30, 45]
    assert summary["candidates"][0]["expiry"] == EXPIRY
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "（月度）" in md.splitlines()[0] and "Gamma 高" not in md


def test_suggest_weekly_no_weekly_expiry_writes_weekly_message(env, monkeypatch):
    monkeypatch.setattr(spread_data, "expirations", lambda t: [EXPIRY])
    assert run_raw(env, "suggest", "--dte", "weekly") == 0
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "本日无合适周度到期" in md
    assert not list((env["ledger"] / "suggestions").glob("*.json"))


def test_open_records_dte_mode(env):
    assert run_raw(env, "suggest") == 0
    assert run(env, "open", "AAA") == 0
    rec = spread_ledger.load(env["ledger"] / "spreads.json")["spreads"][0]
    assert rec["dte_mode"] == "weekly"


def test_open_old_suggestion_without_dte_mode_records_monthly(env):
    path, doc = _suggest(env)
    doc.pop("dte_mode", None)
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "AAA") == 0
    rec = spread_ledger.load(env["ledger"] / "spreads.json")["spreads"][0]
    assert rec["dte_mode"] == "monthly"


DOWN = ibkr_session.GatewayUnavailable("连不上 gw.test:14999：连接被拒绝")


def test_suggest_aborts_before_writing_when_gateway_down(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest") == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert not (env["ledger"] / "suggestions").exists()
    assert not (env["ledger"] / "put-screen").exists()


def test_gateway_error_mid_run_is_not_a_per_ticker_reject(env, capsys):
    _add_ticker(env["wiki"], "BBB")
    env["last_close_error"]["BBB"] = DOWN
    assert run(env, "suggest") == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert not (env["ledger"] / "put-screen").exists()


def test_gateway_error_in_chain_fetch_aborts_run(env, monkeypatch, capsys):
    def puts(t, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "fetch_puts", puts)
    assert run(env, "suggest") == 1
    assert not (env["ledger"] / "put-screen").exists()


def test_single_ticker_without_chain_is_a_reject_and_run_continues(env, monkeypatch):
    _add_ticker(env["wiki"], "BBB")

    def puts(t, e):
        if t == "BBB":
            raise spread_data.DataError("no chain")
        return make_chain(spot=100.0, days=35, iv=0.40,
                          strikes=[float(k) for k in range(70, 100)])
    monkeypatch.setattr(spread_data, "fetch_puts", puts)
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "BBB — 取不到期权链" in md
    assert (env["ledger"] / "suggestions" / f"{SNAP}-AAA-put.json").exists()


def test_put_screen_uses_ibkr_spot_not_yahoo_close(env):
    env["spot"] = 103.0  # Yahoo mas close stays 100.0
    _, doc = _suggest(env)
    assert doc["spot"] == 103.0


def test_snapshot_date_comes_from_ibkr_last_close(env, monkeypatch):
    other = date(2026, 10, 15)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (other, 100.0))
    assert run(env, "suggest") == 0
    assert (env["ledger"] / "put-screen" / f"{other}.json").exists()


def test_candidate_and_summary_state_source_and_snapshot_day(env):
    path, doc = _suggest(env)
    assert doc["data_source"] == "IBKR" and doc["frozen"] is True
    summary = json.loads((env["ledger"] / "put-screen" / f"{SNAP}.json").read_text(encoding="utf-8"))
    assert summary["data_source"] == "IBKR" and summary["frozen"] is True
    md = (env["ledger"] / "put-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert f"数据源 IBKR 模拟网关 · 基于 {SNAP} 收盘（冻结行情）" in md


def test_intraday_summary_says_delayed_not_frozen(env, monkeypatch):
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, 100.0))
    env["open"] = True
    assert run(env, "suggest") == 0
    summary = json.loads((env["ledger"] / "put-screen" / f"{TODAY}.json").read_text(encoding="utf-8"))
    assert summary["frozen"] is False
    md = (env["ledger"] / "put-screen" / f"{TODAY}.md").read_text(encoding="utf-8")
    assert f"基于 {TODAY} 收盘（盘中延迟行情）" in md


def test_open_still_never_fetches(env, monkeypatch):
    _suggest(env)
    import ibkr_data
    for mod in (spread_data, ibkr_data, ibkr_session):
        for name in dir(mod):
            if name in ("DataError", "GatewayUnavailable", "SettingsError"):
                continue
            fn = getattr(mod, name)
            if callable(fn) and not name.startswith("_") and not isinstance(fn, type)                     and getattr(fn, "__module__", None) == mod.__name__:
                monkeypatch.setattr(mod, name, _boom)
    assert run(env, "open", "AAA") == 0


def test_open_save_keeps_broker_block_written_meanwhile(env, monkeypatch):
    _suggest(env)
    assert run(env, "open", "AAA") == 0
    path = env["ledger"] / "spreads.json"
    led = spread_ledger.load(path)
    led["spreads"][0]["ticker"] = "ZZZ"  # free the AAA slot so a second open is allowed
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
