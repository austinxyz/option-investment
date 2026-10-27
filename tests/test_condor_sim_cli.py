"""CLI tests for scripts/condor_sim.py. The IBKR gateway and all market data are faked.

Invented ticker ZZZQ; paper-trading numbers only.

Hand-computed example used by the `open` tests (saved candidate written directly):
  long put 85 / short put 90 / short call 110 / long call 115, width 5.
  Put side credit 1.30 - 0.70 = 0.60; call side 1.20 - 0.30 = 0.90; net credit 1.50/share.
  Breakevens 90 - 1.50 = 88.50 and 110 + 1.50 = 111.50.
  Max loss (5 - 1.50) x 100 = $350 per contract; 2 contracts -> capital at risk $700.
  Required win rate (5 - 1.50) / 5 = 70%.
"""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import condor_sim
import condor_sim_rules
import ibkr_session
import spread_data
import spread_ledger
import spread_sim
from spread_fixtures import make_chain, make_call_chain

SNAP = date(2026, 10, 16)
EXPIRY = "2026-10-23"          # first expiry >= 7 days after SNAP
DOWN = ibkr_session.GatewayUnavailable("IBKR 网关不可达：gw.test:14999")
PUT_WINDOW = [float(k) for k in range(90, 100)]     # narrow default window on purpose
CALL_WINDOW = [float(k) for k in range(101, 111)]
LISTED = [float(k) for k in range(70, 131)]
RECORD_KEYS = {
    "strategy", "ticker", "put_short_strike", "put_long_strike", "call_short_strike",
    "call_long_strike", "width", "put_credit", "call_credit", "net_credit", "breakeven_low",
    "breakeven_high", "delta_bucket", "put_short_delta", "call_short_delta", "put_short_iv",
    "call_short_iv", "underlying_price", "contracts", "expiry", "snapshot_date", "status",
    "suggestion_file", "earnings_in_window",
    # settlement placeholders (filled by spread_sim status / close)
    "result", "pnl", "exit_cost", "exit_underlying", "exit_date",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"gateway": None, "spot": 100.0, "put_calls": [], "call_calls": [],
         "listed": list(LISTED), "drop_calls": set(), "fail_fetch": False,
         "put_window": list(PUT_WINDOW), "call_window": list(CALL_WINDOW)}
    monkeypatch.setattr(ibkr_session, "ensure_gateway",
                        lambda: (_ for _ in ()).throw(m["gateway"]) if m["gateway"] else None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, m["spot"]))
    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-21", EXPIRY, "2026-10-30"])
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: list(m["listed"]))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)

    def fetch_puts(t, e, strikes=(), window_pct=None, window_limit=None):
        if m["fail_fetch"]:
            raise AssertionError("open must not re-fetch the market")
        m["put_calls"].append(tuple(strikes))
        wanted = sorted(set(m["put_window"]) | (set(strikes) & set(m["listed"])))
        return make_chain(spot=m["spot"], days=7, strikes=wanted, quote_spread=0.01)

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        if m["fail_fetch"]:
            raise AssertionError("open must not re-fetch the market")
        m["call_calls"].append(tuple(strikes))
        wanted = sorted((set(m["call_window"]) | (set(strikes) & set(m["listed"]))) - m["drop_calls"])
        return make_call_chain(spot=m["spot"], days=7, strikes=wanted, quote_spread=0.01)

    monkeypatch.setattr(spread_data, "fetch_puts", fetch_puts)
    monkeypatch.setattr(spread_data, "fetch_calls", fetch_calls)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=SNAP):
    return condor_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug_dir(env):
    return env["ledger"] / "suggestions"


def cand_json(env, ticker="ZZZQ", stamp=SNAP):
    return json.loads((sug_dir(env) / f"{stamp}-{ticker}-condor.json").read_text(encoding="utf-8"))


def bucket_of(doc, b):
    return next(c for c in doc["candidates"] if c["delta_bucket"] == b)


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def hand_candidate(**over):
    c = {"delta_bucket": 0.15, "status": "ok",
         "warnings": [condor_sim_rules.WARN_UP_MOVE],
         "put_short_strike": 90.0, "put_long_strike": 85.0,
         "call_short_strike": 110.0, "call_long_strike": 115.0, "width": 5.0,
         "put_credit": 0.60, "call_credit": 0.90, "net_credit": 1.50,
         "breakeven_low": 88.5, "breakeven_high": 111.5,
         "put_short_delta": 0.15, "call_short_delta": 0.14,
         "put_short_iv": 0.40, "call_short_iv": 0.38, "underlying_price": 100.0,
         "max_loss": 350.0, "required_win_rate": 0.7}
    c.update(over)
    return c


def write_saved(env, candidates=None, stamp=SNAP, ticker="ZZZQ", **over):
    """A saved candidate file as suggest writes it (hand numbers, see module docstring)."""
    doc = {"version": 1, "strategy": "iron_condor", "generated_on": stamp.isoformat(),
           "data_source": "IBKR", "frozen": True, "intraday": False, "ticker": ticker,
           "snapshot_date": stamp.isoformat(), "spot": 100.0, "expiry": EXPIRY, "days": 7,
           "earnings": "no", "ex_dividend": None, "width": 5.0, "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               hand_candidate(delta_bucket=0.10),
               hand_candidate(),
               {"delta_bucket": 0.20, "status": "unavailable", "reason": "上方没有等宽的长腿"}]}
    doc.update(over)
    sug_dir(env).mkdir(parents=True, exist_ok=True)
    path = sug_dir(env) / f"{stamp}-{ticker}-condor.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (sug_dir(env) / f"{stamp}-{ticker}-condor.md").write_text("# saved\n", encoding="utf-8")
    return path


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest", "ZZZQ", "--width", "5") != 0
    assert "gw.test:14999" in capsys.readouterr().err
    assert not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def test_settings_error_is_clean_error_line(env, monkeypatch, capsys):
    def bad():
        raise ibkr_session.SettingsError("IBKR_PORT 必须是整数，当前是 'x'")
    monkeypatch.setattr(ibkr_session, "ensure_gateway", bad)
    assert run(env, "suggest", "ZZZQ", "--width", "5") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "IBKR_PORT" in err
    assert not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch):
    def boom(t, e, strikes=(), **kw):
        raise DOWN
    monkeypatch.setattr(spread_data, "fetch_calls", boom)
    assert run(env, "suggest", "ZZZQ", "--width", "5") != 0
    assert not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def test_suggest_writes_summary_and_candidate_files(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    for ext in ("md", "json"):
        assert (sug_dir(env) / f"{SNAP}-ZZZQ-condor.{ext}").is_file()
    summary = env["ledger"] / "condor-sim" / f"{SNAP}.md"
    assert summary.is_file()
    assert "ZZZQ" in summary.read_text(encoding="utf-8")
    doc = cand_json(env)
    assert doc["strategy"] == "iron_condor" and doc["data_source"] == "IBKR"
    assert doc["frozen"] is True and doc["snapshot_date"] == SNAP.isoformat()
    assert doc["expiry"] == EXPIRY and doc["width"] == 5.0
    md = (sug_dir(env) / f"{SNAP}-ZZZQ-condor.md").read_text(encoding="utf-8")
    assert "数据源 IBKR" in md and "冻结行情" in md and SNAP.isoformat() in md
    assert not (env["ledger"] / "spreads.json").exists()


def test_suggest_summary_keeps_other_tickers_of_the_same_day(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    assert run(env, "suggest", "ZZZR", "--width", "5") == 0
    text = (env["ledger"] / "condor-sim" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "ZZZQ" in text and "ZZZR" in text


def test_suggest_three_buckets_with_four_strikes_and_breakevens(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    assert [c["delta_bucket"] for c in doc["candidates"]] == [0.10, 0.15, 0.20]
    md = (sug_dir(env) / f"{SNAP}-ZZZQ-condor.md").read_text(encoding="utf-8")
    for c in doc["candidates"]:
        assert c["status"] == "ok", c
        assert c["put_long_strike"] < c["put_short_strike"] < c["call_short_strike"] < c["call_long_strike"]
        assert c["breakeven_low"] == pytest.approx(c["put_short_strike"] - c["net_credit"])
        assert c["breakeven_high"] == pytest.approx(c["call_short_strike"] + c["net_credit"])
        strikes = "/".join(f"{c[k]:g}" for k in ("put_long_strike", "put_short_strike",
                                                  "call_short_strike", "call_long_strike"))
        assert strikes in md
        assert f"{c['breakeven_low']:.2f}" in md and f"{c['breakeven_high']:.2f}" in md
        assert f"${c['max_loss']:,.0f}" in md
        assert f"{c['required_win_rate'] * 100:.0f}%" in md


def test_suggest_rejected_bucket_lists_reason(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    c20 = bucket_of(cand_json(env), 0.20)
    env["drop_calls"] = {c20["call_long_strike"]}       # its equal-width call wing vanishes
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    rejected = bucket_of(doc, 0.20)
    assert rejected["status"] == "unavailable" and "等宽" in rejected["reason"]
    assert bucket_of(doc, 0.10)["status"] == "ok" and bucket_of(doc, 0.15)["status"] == "ok"
    for path in (sug_dir(env) / f"{SNAP}-ZZZQ-condor.md", env["ledger"] / "condor-sim" / f"{SNAP}.md"):
        assert rejected["reason"] in path.read_text(encoding="utf-8")


def test_suggest_explicitly_requests_wings_outside_default_window(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    asked_puts = {k for call in env["put_calls"] for k in call}
    asked_calls = {k for call in env["call_calls"] for k in call}
    outside_puts = [c["put_long_strike"] for c in doc["candidates"]
                    if c["put_long_strike"] not in PUT_WINDOW]
    outside_calls = [c["call_long_strike"] for c in doc["candidates"]
                     if c["call_long_strike"] not in CALL_WINDOW]
    assert outside_puts and outside_calls          # the fixture really puts wings outside
    assert set(outside_puts) <= asked_puts
    assert set(outside_calls) <= asked_calls
    assert env["put_calls"][0] == () and env["call_calls"][0] == ()   # shorts: default window


def test_suggest_warns_that_large_up_move_is_a_full_loss(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    md = (sug_dir(env) / f"{SNAP}-ZZZQ-condor.md").read_text(encoding="utf-8")
    summary = (env["ledger"] / "condor-sim" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "大涨对铁鹰可能是满额亏损" in md and "大涨对铁鹰可能是满额亏损" in summary
    for c in cand_json(env)["candidates"]:
        assert condor_sim_rules.WARN_UP_MOVE in c["warnings"]


def test_wing_request_follows_final_short_strikes(env):
    """Narrow windows (puts 95-99, calls 101-105): the 0.10 call short (108) is only reached
    after the first wing request widens the call chain, so its wing 113 must be asked for too.
    Expected 0.10 condor: 88/93/108/113 (BS chain, iv 0.40, 7 days, spot 100)."""
    env["put_window"] = [float(k) for k in range(95, 100)]
    env["call_window"] = [float(k) for k in range(101, 106)]
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    c10 = bucket_of(cand_json(env), 0.10)
    assert c10["status"] == "ok", c10
    assert (c10["put_short_strike"], c10["call_short_strike"], c10["call_long_strike"]) == (93.0, 108.0, 113.0)
    assert c10["put_long_strike"] == 88.0


def test_suggest_and_open_dotted_ticker(env):
    assert run(env, "suggest", "ZZ.Q", "--width", "5") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZ.Q-condor.json", f"{SNAP}-ZZ.Q-condor.md"]
    assert run(env, "open", "ZZ.Q", "0.15") == 0


def test_suggest_failed_json_write_leaves_no_orphan_md(env, monkeypatch):
    def boom(doc):
        raise TypeError("not serialisable")
    monkeypatch.setattr(condor_sim, "_json_text", boom)
    with pytest.raises(TypeError):
        run(env, "suggest", "ZZZQ", "--width", "5")
    assert not sug_dir(env).exists() or not list(sug_dir(env).iterdir())


# ---- open ------------------------------------------------------------------------

def test_open_records_saved_candidate_without_refetch(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    saved = bucket_of(cand_json(env), 0.15)
    env["fail_fetch"] = True
    env["spot"] = 250.0                                 # the market moved; ignored by open
    assert run(env, "open", "ZZZQ", "0.15", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    for key in ("put_short_strike", "put_long_strike", "call_short_strike", "call_long_strike",
                "width", "put_credit", "call_credit", "net_credit", "breakeven_low",
                "breakeven_high", "put_short_delta", "call_short_delta", "put_short_iv",
                "call_short_iv", "underlying_price"):
        assert s[key] == saved[key], key
    assert s["contracts"] == 2 and s["delta_bucket"] == 0.15
    assert s["suggestion_file"] == f"{SNAP}-ZZZQ-condor.md"


def test_open_writes_explicit_fields_and_no_legacy_keys(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert set(s) == RECORD_KEYS | {"id"}
    for legacy in ("credit", "short_strike", "long_strike", "max_loss", "required_win_rate",
                   "warnings", "reason", "breached_side"):
        assert legacy not in s
    assert s["strategy"] == "iron_condor" and s["status"] == "open"
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == SNAP.isoformat()
    assert s["earnings_in_window"] is False
    out = capsys.readouterr().out
    assert "$700" in out and "88.50" in out and "111.50" in out and "70%" in out


def test_open_net_credit_matches_sum_of_sides(env):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15") == 0
    (s,) = ledger(env)["spreads"]
    assert s["net_credit"] == pytest.approx(s["put_credit"] + s["call_credit"])
    assert s["net_credit"] == pytest.approx(1.50) and s["net_credit"] < s["width"]


def test_open_accepts_candidate_with_sub_cent_rounding(env):
    """Rules round each credit to 4 places: 0.12345 + 0.12345 -> sides 0.1235 / 0.1235 but
    net round(0.2469) = 0.2469, 1e-4 off the rounded sum. open must still accept it."""
    write_saved(env, candidates=[hand_candidate(put_credit=0.1235, call_credit=0.1235,
                                                net_credit=0.2469, breakeven_low=89.7531,
                                                breakeven_high=110.2469)])
    assert run(env, "open", "ZZZQ", "0.15") == 0
    assert ledger(env)["spreads"][0]["net_credit"] == pytest.approx(0.2469)


@pytest.mark.parametrize("case", ["bad_json", "wrong_strategy", "ticker_mismatch", "missing_key",
                                  "bad_date", "expired"])
def test_open_rejects_damaged_expired_or_mismatched_candidate(env, case, capsys):
    path = write_saved(env)
    today = SNAP
    if case == "bad_json":
        path.write_text("{not json", encoding="utf-8")
    elif case == "wrong_strategy":
        write_saved(env, strategy="collar")
    elif case == "ticker_mismatch":
        write_saved(env, ticker="ZZZQ")
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**doc, "ticker": "ZZZR"}), encoding="utf-8")
    elif case == "missing_key":
        write_saved(env, candidates=[{k: v for k, v in hand_candidate().items() if k != "net_credit"}])
    elif case == "bad_date":
        write_saved(env, expiry="2026-13-40")
    else:
        today = date.fromisoformat(EXPIRY)
    assert run(env, "open", "ZZZQ", "0.15", today=today) == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_without_saved_candidate_refuses(env, capsys):
    assert run(env, "open", "ZZZQ", "0.15") == 1
    assert "suggest" in capsys.readouterr().err


def test_open_rejects_rejected_bucket(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.20") == 1
    assert "上方没有等宽的长腿" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_width_mismatch(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15", "--width", "3") == 1
    assert "--width" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    assert run(env, "open", "ZZZQ", "0.15", "--width", "5") == 0


def test_open_rejects_duplicate_ticker_expiry(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15") == 0
    assert run(env, "open", "ZZZQ", "0.10") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_rejects_duplicate_against_other_strategy(env, capsys):
    led = spread_ledger.empty_ledger()
    led, _ = spread_ledger.add_spread(led, {
        "ticker": "ZZZQ", "expiry": EXPIRY, "status": "open", "contracts": 1,
        "short_strike": 95.0, "long_strike": 90.0, "width": 5.0, "credit": 0.5,
        "snapshot_date": SNAP.isoformat(), "underlying_price": 100.0})
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json", led)
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


@pytest.mark.parametrize("over", [
    {"put_long_strike": 92.0},                                     # long put above short put
    {"call_short_strike": 89.0, "breakeven_high": 90.5},           # range inverted
    {"call_long_strike": 116.0},                                   # unequal call width
    {"width": 4.0},                                                # width disagrees with legs
    {"net_credit": 1.40, "breakeven_low": 88.6, "breakeven_high": 111.4},   # net != sum
    {"put_credit": 0.0, "net_credit": 0.90, "breakeven_low": 89.1, "breakeven_high": 110.9},
    {"call_credit": 5.0, "net_credit": 5.60, "breakeven_low": 84.4, "breakeven_high": 115.6},
    {"breakeven_low": 88.0},                                       # breakeven does not match
    {"call_short_strike": 90.0, "call_long_strike": 95.0, "breakeven_high": 91.5},  # reused strike
])
def test_open_rejects_invalid_structure(env, over, capsys):
    write_saved(env, candidates=[hand_candidate(**over)])
    assert run(env, "open", "ZZZQ", "0.15") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


# ---- isolation from spread_sim ---------------------------------------------------

def test_condor_candidate_files_not_picked_up_by_spread_sim_open(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    for p in sug_dir(env).iterdir():
        assert spread_sim.SUGGESTION_FILE.match(p.name) is None
    assert spread_sim._load_suggestion(env["ledger"], "ZZZQ") is None
    code = spread_sim.main(["--ledger-dir", str(env["ledger"]), "open", "ZZZQ", "0.15"], today=SNAP)
    assert code == 1
    assert "没有 ZZZQ 的已保存候选" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_spread_sim_files_not_picked_up_by_condor_open(env, capsys):
    sug_dir(env).mkdir(parents=True)
    (sug_dir(env) / f"{SNAP}-ZZZQ.json").write_text("{}", encoding="utf-8")
    (sug_dir(env) / f"{SNAP}-ZZZQ-collar-real.json").write_text("{}", encoding="utf-8")
    assert run(env, "open", "ZZZQ", "0.15") == 1
    assert "没有 ZZZQ 的已保存铁鹰候选" in capsys.readouterr().err


def test_open_uses_latest_saved_condor_file(env):
    write_saved(env, stamp=date(2026, 10, 9), expiry="2026-10-16",
                candidates=[hand_candidate(net_credit=1.40, call_credit=0.80,
                                           breakeven_low=88.6, breakeven_high=111.4)])
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15") == 0
    assert ledger(env)["spreads"][0]["net_credit"] == pytest.approx(1.50)


# ---- status / close delegate ----------------------------------------------------

def test_status_and_close_delegate_to_spread_sim(env, monkeypatch):
    seen = []
    monkeypatch.setattr(spread_sim, "cmd_status",
                        lambda args, d, t: seen.append(("status", Path(d), t)) or 0)
    monkeypatch.setattr(spread_sim, "cmd_close",
                        lambda args, d, t: seen.append(("close", args.id, Path(d), t)) or 0)
    assert run(env, "status") == 0
    assert run(env, "close", "7") == 0
    assert seen == [("status", env["ledger"], SNAP), ("close", 7, env["ledger"], SNAP)]


def test_condor_sim_never_writes_positions_md(env, monkeypatch):
    written = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    assert run(env, "open", "ZZZQ", "0.15") == 0
    assert written and "positions.md" not in written


def test_condor_sim_imports_no_broker_modules():
    source = (REPO_ROOT / "scripts" / "condor_sim.py").read_text(encoding="utf-8")
    source += (REPO_ROOT / "scripts" / "condor_sim_render.py").read_text(encoding="utf-8")
    for name in ("broker_", "placeOrder", "ib_async"):
        assert name not in source
