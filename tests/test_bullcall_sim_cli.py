"""CLI tests for scripts/bullcall_sim.py. The IBKR gateway and all market data are faked.

Invented ticker ZZZQ; paper-trading numbers only.

Hand-computed example (design section 10), used by the chain fixture and the `open` tests:
  spot 100, buy 100 Call mid 3.20, sell 105 Call mid 1.30, width 5, 1 contract.
  Debit 3.20 - 1.30 = 1.90/share; breakeven 100 + 1.90 = 101.90.
  Max loss 1.90 x 100 = $190; max profit (5 - 1.90) x 100 = $310 (2 contracts: $380 / $620).
  Required win rate 1.90 / 5 = 38%; breakeven move +1.90%, max-profit move +5.00%.

Chain fixture: broker delta = 0.50 - (K - 100) / 30 (so 97 -> 0.60, 100 -> 0.50, 103 -> 0.40),
iv None (no Black-Scholes cross-check), mid 3.20 at 100 and 1.30 at 105, else 3.20 e^(-0.18 (K-100)),
bid/ask = mid -/+ 0.01. Default window 95-106, so the 0.40 bucket's short leg 108 lies outside.
"""
from datetime import date
from pathlib import Path
import json
import math
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bullcall_sim
import bullcall_sim_rules
import call_screen
import collar_screen
import condor_sim
import ibkr_session
import put_screen
import spread_core
import spread_data
import spread_ledger
import spread_sim

SNAP = date(2026, 10, 16)
EXPIRY = "2026-11-20"          # first expiry >= 30 days after SNAP (35 days)
EXPIRIES = ["2026-10-23", "2026-11-13", EXPIRY, "2026-12-18"]
DOWN = ibkr_session.GatewayUnavailable("IBKR 网关不可达：gw.test:14999")
WINDOW = [float(k) for k in range(95, 107)]
LISTED = [float(k) for k in range(90, 116)]
RECORD_KEYS = {
    "strategy", "ticker", "long_call_strike", "short_call_strike", "width", "long_call_mid",
    "short_call_mid", "debit", "breakeven", "breakeven_move_pct", "max_profit_move_pct",
    "delta_bucket", "long_call_delta", "short_call_delta", "long_call_iv", "short_call_iv",
    "underlying_price", "contracts", "expiry", "snapshot_date", "status", "suggestion_file",
    "earnings_in_window",
    # settlement placeholders (filled by spread_sim status / close)
    "exit_date", "exit_value", "exit_underlying", "pnl", "result", "fraction_of_max_captured",
}
SELLER_KEYS = ("credit", "short_strike", "long_strike", "net_credit", "exit_cost",
               "breached_side", "lot_ref", "short_put_strike", "short_mid", "long_mid")
DISPLAY_KEYS = ("max_profit", "max_loss", "required_win_rate", "warnings", "reason")


def _mid(k):
    return {100.0: 3.20, 105.0: 1.30}.get(k, round(3.20 * math.exp(-0.18 * (k - 100)), 4))


def _row(k):
    mid = _mid(k)
    return {"strike": k, "bid": round(mid - 0.01, 4), "ask": round(mid + 0.01, 4), "last": mid,
            "iv": None, "delta": round(0.50 - (k - 100) / 30, 4)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"gateway": None, "spot": 100.0, "calls": [], "listed": list(LISTED),
         "window": list(WINDOW), "fail_fetch": False, "earnings": "no", "ex_div": None}
    monkeypatch.setattr(ibkr_session, "ensure_gateway",
                        lambda: (_ for _ in ()).throw(m["gateway"]) if m["gateway"] else None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, m["spot"]))
    monkeypatch.setattr(spread_data, "expirations", lambda t: list(EXPIRIES))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        if m["fail_fetch"]:
            raise AssertionError("open must not re-fetch the market")
        m["calls"].append(tuple(strikes))
        wanted = sorted(set(m["window"]) | (set(strikes) & set(m["listed"])))
        return [_row(k) for k in wanted]

    def no_puts(*a, **k):
        raise AssertionError("bullcall_sim never needs the put chain")

    monkeypatch.setattr(spread_data, "fetch_calls", fetch_calls)
    monkeypatch.setattr(spread_data, "fetch_puts", no_puts)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=SNAP):
    return bullcall_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug_dir(env):
    return env["ledger"] / "suggestions"


def cand_json(env, ticker="ZZZQ", stamp=SNAP):
    return json.loads((sug_dir(env) / f"{stamp}-{ticker}-bullcall.json").read_text(encoding="utf-8"))


def cand_md(env, ticker="ZZZQ", stamp=SNAP):
    return (sug_dir(env) / f"{stamp}-{ticker}-bullcall.md").read_text(encoding="utf-8")


def summary_md(env, stamp=SNAP):
    return (env["ledger"] / "bullcall-sim" / f"{stamp}.md").read_text(encoding="utf-8")


def bucket_of(doc, b):
    return next(c for c in doc["candidates"] if c["delta_bucket"] == b)


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def nothing_written(env):
    return not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def hand_candidate(**over):
    c = {"delta_bucket": 0.50, "status": "ok",
         "long_call_strike": 100.0, "short_call_strike": 105.0, "width": 5.0,
         "long_call_mid": 3.20, "short_call_mid": 1.30, "debit": 1.90, "breakeven": 101.9,
         "breakeven_move_pct": 0.019, "max_profit_move_pct": 0.05,
         "long_call_delta": 0.50, "short_call_delta": 0.33,
         "long_call_iv": 0.40, "short_call_iv": None, "underlying_price": 100.0,
         "max_profit": 310.0, "max_loss": 190.0, "required_win_rate": 0.38,
         "warnings": [bullcall_sim_rules.WARN_FULL_DEBIT]}
    c.update(over)
    return c


def write_saved(env, candidates=None, stamp=SNAP, ticker="ZZZQ", **over):
    """A saved candidate file as suggest writes it (hand numbers, see module docstring)."""
    doc = {"version": 1, "strategy": "bull_call_spread", "generated_on": stamp.isoformat(),
           "data_source": "IBKR", "frozen": True, "intraday": False, "ticker": ticker,
           "snapshot_date": stamp.isoformat(), "spot": 100.0, "expiry": EXPIRY, "days": 35,
           "min_days": 30, "earnings": "no", "ex_dividend": None, "width": 5.0,
           "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               hand_candidate(delta_bucket=0.60, long_call_strike=97.0, short_call_strike=102.0,
                              long_call_mid=4.80, short_call_mid=2.20, debit=2.60,
                              breakeven=99.6, breakeven_move_pct=-0.004,
                              max_profit_move_pct=0.02, max_profit=240.0, max_loss=260.0,
                              required_win_rate=0.52),
               hand_candidate(),
               {"delta_bucket": 0.40, "status": "unavailable",
                "reason": "上方没有等宽的短腿（需要 Call 108 的有效报价）"}]}
    doc.update(over)
    sug_dir(env).mkdir(parents=True, exist_ok=True)
    path = sug_dir(env) / f"{stamp}-{ticker}-bullcall.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (sug_dir(env) / f"{stamp}-{ticker}-bullcall.md").write_text("# saved\n", encoding="utf-8")
    return path


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest", "ZZZQ", "--width", "5") != 0
    assert "gw.test:14999" in capsys.readouterr().err
    assert nothing_written(env)
    assert env["calls"] == []                       # no market read after the gate fails


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch):
    def boom(t, s, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "earnings_status", boom)   # the last network read
    assert run(env, "suggest", "ZZZQ", "--width", "5") != 0
    assert nothing_written(env)


def test_suggest_writes_summary_and_candidate_files(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZZQ-bullcall.json", f"{SNAP}-ZZZQ-bullcall.md"]
    assert "ZZZQ" in summary_md(env)
    doc = cand_json(env)
    assert doc["strategy"] == "bull_call_spread" and doc["data_source"] == "IBKR"
    assert doc["frozen"] is True and doc["snapshot_date"] == SNAP.isoformat()
    assert doc["expiry"] == EXPIRY and doc["width"] == 5.0 and doc["width_source"] == "manual"
    md = cand_md(env)
    assert "数据源 IBKR" in md and "冻结行情" in md and SNAP.isoformat() in md
    assert not (env["ledger"] / "spreads.json").exists()
    out = capsys.readouterr().out
    assert "bullcall-sim" in out and "open ZZZQ" in out


def test_suggest_summary_keeps_other_tickers_of_the_same_day(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    assert run(env, "suggest", "ZZZR", "--width", "5") == 0
    text = summary_md(env)
    assert "ZZZQ" in text and "ZZZR" in text


def test_suggest_default_expiry_at_least_30_days(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    assert doc["expiry"] == EXPIRY and doc["days"] == 35 and doc["min_days"] == 30
    assert (date.fromisoformat(doc["expiry"]) - SNAP).days >= spread_core.BULLCALL_MIN_DAYS
    for c in doc["candidates"]:
        assert not any("时间不在你这边" in w for w in c.get("warnings", []))


def test_suggest_min_days_override_warns_short_dte(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5", "--min-days", "7") == 0
    doc = cand_json(env)
    assert doc["expiry"] == "2026-10-23" and doc["days"] == 7 and doc["min_days"] == 7
    for c in doc["candidates"]:
        if c["status"] == "ok":
            assert any("时间不在你这边" in w for w in c["warnings"])
    assert "时间不在你这边" in cand_md(env)
    assert "时间不在你这边" in capsys.readouterr().out


def test_suggest_min_days_below_7_refused(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", "ZZZQ", "--min-days", "6")
    assert nothing_written(env)


def test_suggest_no_expiry_far_enough_writes_nothing(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5", "--min-days", "90") == 1
    assert "90" in capsys.readouterr().err
    assert nothing_written(env)


def test_suggest_three_buckets_with_hand_numbers(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    assert [c["delta_bucket"] for c in doc["candidates"]] == [0.60, 0.50, 0.40]
    assert [c["status"] for c in doc["candidates"]] == ["ok", "ok", "ok"]
    assert [(c["long_call_strike"], c["short_call_strike"]) for c in doc["candidates"]] == [
        (97.0, 102.0), (100.0, 105.0), (103.0, 108.0)]
    c = bucket_of(doc, 0.50)
    assert c["debit"] == pytest.approx(1.90) and c["breakeven"] == pytest.approx(101.90)
    assert c["max_loss"] == pytest.approx(190) and c["max_profit"] == pytest.approx(310)
    assert c["required_win_rate"] == pytest.approx(0.38)
    assert c["breakeven_move_pct"] == pytest.approx(0.019)
    assert c["max_profit_move_pct"] == pytest.approx(0.05)
    md, summary = cand_md(env), summary_md(env)
    for text in (md, summary):
        for value in ("$1.90", "101.90", "$310", "$190", "38%", "+1.90%", "+5.00%"):
            assert value in text, value


def test_suggest_auto_width_uses_call_chain_else_default(env, monkeypatch):
    seen = []

    def fake(calls, spot, days, *a, **k):
        seen.append(sorted(r["strike"] for r in calls))
        return None
    monkeypatch.setattr(spread_core, "recommend_width", fake)
    assert run(env, "suggest", "ZZZQ") == 0
    assert seen == [WINDOW]
    doc = cand_json(env)
    assert doc["width"] == spread_core.DEFAULT_WIDTH and doc["width_source"] == "fallback"
    monkeypatch.setattr(spread_core, "recommend_width", lambda c, s, d, *a, **k: {"width": 3.0})
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    assert doc["width"] == 3.0 and doc["width_source"] == "auto"
    assert bucket_of(doc, 0.50)["short_call_strike"] == 103.0


def test_suggest_rejected_bucket_lists_reason(env):
    env["listed"] = [k for k in LISTED if k != 108.0]     # the 0.40 short leg is not listed
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    rejected = bucket_of(doc, 0.40)
    assert rejected["status"] == "unavailable" and "上方没有等宽的短腿" in rejected["reason"]
    assert bucket_of(doc, 0.60)["status"] == "ok" and bucket_of(doc, 0.50)["status"] == "ok"
    for text in (cand_md(env), summary_md(env)):
        assert rejected["reason"] in text
    assert len(env["calls"]) <= 1 + bullcall_sim.SHORT_LEG_ROUNDS   # bounded re-request


def test_suggest_all_buckets_rejected_still_writes_files(env):
    env["window"] = [float(k) for k in range(110, 113)]   # every delta far from the targets
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    assert all(c["status"] == "unavailable" for c in cand_json(env)["candidates"])
    assert "三档都被剔除" in cand_md(env)


def test_suggest_requests_short_leg_outside_default_window(env):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    assert env["calls"][0] == ()                    # long legs: the default window
    asked = {k for call in env["calls"] for k in call}
    outside = [c["short_call_strike"] for c in cand_json(env)["candidates"]
               if c["short_call_strike"] not in WINDOW]
    assert outside == [108.0]                       # the fixture really puts one outside
    assert set(outside) <= asked
    assert (108.0,) in env["calls"]


def test_short_leg_request_follows_final_long_strikes(env):
    """Window 95-102, width 1: the 0.40 long is first 102 (delta 0.433) and needs short 103;
    fetching 103 (delta 0.40 exactly) moves the long to 103, which needs short 104 - so the
    request must repeat until it asks for nothing new. Expected 0.40 spread: 103/104."""
    env["window"] = [float(k) for k in range(95, 103)]
    assert run(env, "suggest", "ZZZQ", "--width", "1") == 0
    c40 = bucket_of(cand_json(env), 0.40)
    assert c40["status"] == "ok", c40
    assert (c40["long_call_strike"], c40["short_call_strike"]) == (103.0, 104.0)
    assert env["calls"][1:] == [(103.0,), (104.0,)]


def test_suggest_every_candidate_shows_full_debit_warning(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    for c in doc["candidates"]:
        assert bullcall_sim_rules.WARN_FULL_DEBIT in c["warnings"]
    md = cand_md(env)
    assert md.count(bullcall_sim_rules.WARN_FULL_DEBIT) == 1     # banner, not per bucket
    assert "或以下" in bullcall_sim_rules.WARN_FULL_DEBIT
    assert bullcall_sim_rules.WARN_FULL_DEBIT in summary_md(env)
    assert bullcall_sim_rules.WARN_FULL_DEBIT in capsys.readouterr().out


def test_suggest_earnings_and_ex_dividend_flow_into_candidates(env):
    env["earnings"], env["ex_div"] = "yes", date(2026, 11, 2)
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    doc = cand_json(env)
    assert doc["earnings"] == "yes" and doc["ex_dividend"] == "2026-11-02"
    c60 = bucket_of(doc, 0.60)                       # short 102 <= 100 x 1.03
    assert any("财报" in w for w in c60["warnings"])
    assert any("除息日" in w for w in c60["warnings"])


def test_suggest_dotted_ticker_file_names(env):
    assert run(env, "suggest", "zz.q", "--width", "5") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZ.Q-bullcall.json", f"{SNAP}-ZZ.Q-bullcall.md"]
    assert run(env, "open", "ZZ.Q", "0.50") == 0
    assert ledger(env)["spreads"][0]["suggestion_file"] == f"{SNAP}-ZZ.Q-bullcall.md"


@pytest.mark.parametrize("cmd", [("suggest", "ZZZQ"), ("open", "ZZZQ", "0.50")])
def test_width_with_more_than_four_decimals_refused(env, capsys, cmd):
    if cmd[0] == "open":
        write_saved(env)
    before = sorted(p.name for p in env["ledger"].rglob("*")) if env["ledger"].exists() else []
    assert run(env, *cmd, "--width", "5.00001") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "4 位小数" in err and "Traceback" not in err
    after = sorted(p.name for p in env["ledger"].rglob("*")) if env["ledger"].exists() else []
    assert after == before and env["calls"] == []
    assert run(env, "suggest", "ZZZQ", "--width", "5.0000") == 0     # 4 decimals still fine


def test_settings_error_is_clean_error_line(env, monkeypatch, capsys):
    def bad():
        raise ibkr_session.SettingsError("IBKR_PORT 必须是整数，当前是 'x'")
    monkeypatch.setattr(ibkr_session, "ensure_gateway", bad)
    assert run(env, "suggest", "ZZZQ", "--width", "5") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "IBKR_PORT" in err and "Traceback" not in err
    assert nothing_written(env)


def test_suggest_bad_ticker_refused(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", "../ZZZQ")
    assert nothing_written(env)


def test_write_pair_failure_leaves_no_orphan(env, monkeypatch, tmp_path):
    def boom(doc):
        raise TypeError("not serialisable")
    monkeypatch.setattr(bullcall_sim, "_json_text", boom)
    with pytest.raises(TypeError):
        run(env, "suggest", "ZZZQ", "--width", "5")
    assert not sug_dir(env).exists() or not list(sug_dir(env).iterdir())

    folder = tmp_path / "pair"
    folder.mkdir()
    real = Path.write_text

    def flaky(self, *a, **k):
        if self.name.endswith(".json.tmp"):
            raise OSError("disk full")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(OSError):
        bullcall_sim._write_pair(folder / "2026-10-16-ZZ.Q-bullcall", "# md\n", "{}\n")
    assert list(folder.iterdir()) == []


# ---- open ------------------------------------------------------------------------

def test_open_records_saved_candidate_without_refetch(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    saved = bucket_of(cand_json(env), 0.50)
    env["fail_fetch"] = True
    env["gateway"] = DOWN                           # open needs no gateway at all
    env["spot"] = 250.0                             # the market moved; ignored by open
    assert run(env, "open", "ZZZQ", "0.50", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    for key in bullcall_sim.COPIED_FIELDS:
        assert s[key] == saved[key], key
    assert s["contracts"] == 2 and s["delta_bucket"] == 0.50 and s["underlying_price"] == 100.0
    assert s["suggestion_file"] == f"{SNAP}-ZZZQ-bullcall.md"


def test_open_writes_explicit_fields_and_no_seller_keys(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.50", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert set(s) == RECORD_KEYS | {"id"}
    for key in SELLER_KEYS + DISPLAY_KEYS:
        assert key not in s, key
    assert s["strategy"] == spread_ledger.STRATEGY_BULL_CALL and s["status"] == "open"
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == SNAP.isoformat()
    assert s["earnings_in_window"] is False and s["short_call_iv"] is None
    for key in ("exit_date", "exit_value", "exit_underlying", "pnl", "result",
                "fraction_of_max_captured"):
        assert s[key] is None, key
    assert spread_ledger.strategy_of(s) == "bull_call_spread"
    out = capsys.readouterr().out
    for value in ("$1.90", "101.90", "$620", "$380", "38%", "Call 借方价差"):
        assert value in out, value
    assert bullcall_sim_rules.WARN_FULL_DEBIT in out


def test_copied_fields_are_an_explicit_tuple():
    assert isinstance(bullcall_sim.COPIED_FIELDS, tuple)
    assert set(bullcall_sim.COPIED_FIELDS) | {
        "strategy", "ticker", "contracts", "expiry", "snapshot_date", "status",
        "suggestion_file", "earnings_in_window", "exit_date", "exit_value", "exit_underlying",
        "pnl", "result", "fraction_of_max_captured"} == RECORD_KEYS


@pytest.mark.parametrize("field", ["debit", "breakeven"])
def test_open_debit_tolerance_shared_constant(env, field, monkeypatch, capsys):
    """Rules round mids, debit and breakeven to 4 places each; a 0.5e-4 drift is rounding,
    1.5e-4 is a damaged file. The bound is spread_core.BULLCALL_ROUNDING_TOL (1e-4)."""
    assert spread_core.BULLCALL_ROUNDING_TOL == 1e-4
    base = {"debit": 1.90, "breakeven": 101.90}

    def saved(drift):
        over = dict(base)
        over[field] = base[field] + drift
        if field == "debit":                          # keep breakeven consistent with debit
            over["breakeven"] = 100.0 + over["debit"]
        write_saved(env, candidates=[hand_candidate(**over)])

    saved(0.5e-4)
    assert run(env, "open", "ZZZQ", "0.50") == 0
    (env["ledger"] / "spreads.json").unlink()
    saved(1.5e-4)
    assert run(env, "open", "ZZZQ", "0.50") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    monkeypatch.setattr(spread_core, "BULLCALL_ROUNDING_TOL", 2e-4)   # read live, not copied
    assert run(env, "open", "ZZZQ", "0.50") == 0


@pytest.mark.parametrize("case", ["bad_json", "wrong_strategy", "wrong_version",
                                  "ticker_mismatch", "missing_key", "bad_number", "bad_date",
                                  "bad_bucket", "empty", "expired"])
def test_open_rejects_damaged_expired_or_mismatched_candidate(env, case, capsys):
    path = write_saved(env)
    today = SNAP
    if case == "bad_json":
        path.write_text("{not json", encoding="utf-8")
    elif case == "wrong_strategy":
        write_saved(env, strategy="iron_condor")
    elif case == "wrong_version":
        write_saved(env, version=2)
    elif case == "ticker_mismatch":
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**doc, "ticker": "ZZZR"}), encoding="utf-8")
    elif case == "missing_key":
        write_saved(env, candidates=[{k: v for k, v in hand_candidate().items() if k != "debit"}])
    elif case == "bad_number":
        write_saved(env, candidates=[hand_candidate(long_call_mid="3.20")])
    elif case == "bad_date":
        write_saved(env, expiry="2026-13-40")
    elif case == "bad_bucket":
        write_saved(env, candidates=[hand_candidate(delta_bucket=0.15)])
    elif case == "empty":
        write_saved(env, candidates=[])
    else:
        today = date.fromisoformat(EXPIRY)
    assert run(env, "open", "ZZZQ", "0.50", today=today) == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("over", [
    {"long_call_strike": 105.0, "breakeven": 106.9},                  # long not below short
    {"short_call_strike": 106.0},                                     # legs not width apart
    {"width": 4.0},                                                   # width disagrees with legs
    {"long_call_mid": 1.30, "short_call_mid": 1.30, "debit": 0.0, "breakeven": 100.0},
    {"long_call_mid": 6.30, "short_call_mid": 1.30, "debit": 5.0, "breakeven": 105.0},
    {"long_call_mid": 3.30},                                          # debit != long - short
    {"breakeven": 102.0},                                             # breakeven != long + debit
])
def test_open_rejects_invalid_structure(env, over, capsys):
    write_saved(env, candidates=[hand_candidate(**over)])
    assert run(env, "open", "ZZZQ", "0.50") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_without_saved_candidate_refuses(env, capsys):
    assert run(env, "open", "ZZZQ", "0.50") == 1
    assert "没有 ZZZQ 的已保存 Call 借方价差候选" in capsys.readouterr().err


def test_open_rejects_unavailable_bucket(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.40") == 1
    assert "上方没有等宽的短腿" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_bucket_missing_from_file(env, capsys):
    write_saved(env, candidates=[hand_candidate()])
    assert run(env, "open", "ZZZQ", "0.60") == 1
    assert "0.60" in capsys.readouterr().err


def test_open_rejects_bucket_outside_choices(env):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", "ZZZQ", "0.15")


def test_open_rejects_width_mismatch(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.50", "--width", "3") == 1
    assert "--width" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    assert run(env, "open", "ZZZQ", "0.50", "--width", "5") == 0


@pytest.mark.parametrize("other", [
    {},                                                               # bull put (no strategy key)
    {"strategy": "iron_condor"},
    {"strategy": "cash_secured_put"},
    {"strategy": "covered_call", "lot_ref": {"source": "paper", "lot_id": 1, "ticker": "ZZZQ"}},
    {"strategy": "bull_call_spread"},
])
def test_open_rejects_duplicate_ticker_expiry_any_strategy(env, other, capsys):
    led = spread_ledger.empty_ledger()
    led, _ = spread_ledger.add_spread(led, {"ticker": "ZZZQ", "expiry": EXPIRY,
                                            "status": "open", "contracts": 1, **other})
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json", led)
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.50") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_second_bucket_same_expiry_blocked(env, capsys):
    """Two buckets may share a long strike; either way only one per ticker + expiry opens."""
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.50") == 0
    assert run(env, "open", "ZZZQ", "0.60") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_other_expiry_not_blocked(env):
    led = spread_ledger.empty_ledger()
    led, _ = spread_ledger.add_spread(led, {
        "ticker": "ZZZQ", "expiry": "2026-12-18", "status": "open", "contracts": 1,
        "short_strike": 95.0, "long_strike": 90.0, "width": 5.0, "credit": 0.5,
        "snapshot_date": SNAP.isoformat(), "underlying_price": 100.0, "delta_bucket": 0.15,
        "short_delta": 0.15, "short_iv": 0.4, "short_mid": 0.8, "long_mid": 0.3,
        "earnings_in_window": False, "suggestion_file": f"{SNAP}-ZZZQ.md"})
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json", led)
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.50") == 0
    assert len(ledger(env)["spreads"]) == 2


def test_open_uses_latest_saved_bullcall_file(env):
    write_saved(env, stamp=date(2026, 10, 9), candidates=[hand_candidate(
        long_call_mid=3.30, debit=2.00, breakeven=102.0)])
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.50") == 0
    assert ledger(env)["spreads"][0]["debit"] == pytest.approx(1.90)


# ---- isolation from the other loaders --------------------------------------------

OTHER_PATTERNS = (spread_sim.SUGGESTION_FILE, put_screen.SUGGESTION_FILE,
                  put_screen.SUMMARY_FILE, call_screen.SUGGESTION_FILE,
                  call_screen.SUMMARY_FILE, collar_screen.SUGGESTION_FILE,
                  condor_sim.SUGGESTION_FILE)


def test_bullcall_files_not_picked_up_by_other_loaders(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    for p in sug_dir(env).iterdir():
        for pattern in OTHER_PATTERNS:
            assert pattern.match(p.name) is None, (pattern.pattern, p.name)
    assert spread_sim._load_suggestion(env["ledger"], "ZZZQ") is None
    with pytest.raises(spread_ledger.LedgerError, match="没有 ZZZQ 的已保存铁鹰候选"):
        condor_sim._load_candidate(env["ledger"], "ZZZQ")
    code = spread_sim.main(["--ledger-dir", str(env["ledger"]), "open", "ZZZQ", "0.15"], today=SNAP)
    assert code == 1 and not (env["ledger"] / "spreads.json").exists()

    # and the other way round: none of their files is a bull call candidate
    for p in list(sug_dir(env).iterdir()):
        p.unlink()
    others = [f"{SNAP}-ZZZQ.json", f"{SNAP}-ZZZQ-put.json", f"{SNAP}-ZZZQ-call-real.json",
              f"{SNAP}-ZZZQ-collar-lot1.json", f"{SNAP}-ZZZQ-condor.json",
              f"{SNAP}-ZZZQ-bullcall.json.tmp", f"{SNAP}-ZZZQ-BULLCALL.json"]
    for name in others:
        (sug_dir(env) / name).write_text("{}", encoding="utf-8")
        assert bullcall_sim.SUGGESTION_FILE.match(name) is None, name
    capsys.readouterr()
    assert run(env, "open", "ZZZQ", "0.50") == 1
    assert "没有 ZZZQ 的已保存 Call 借方价差候选" in capsys.readouterr().err
    assert bullcall_sim._day_docs(sug_dir(env), SNAP.isoformat()) == ([], [])


def test_summary_lists_damaged_same_day_file(env):
    sug_dir(env).mkdir(parents=True)
    (sug_dir(env) / f"{SNAP}-ZZZR-bullcall.json").write_text("{bad", encoding="utf-8")
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    text = summary_md(env)
    assert f"{SNAP}-ZZZR-bullcall.json" in text and "ZZZQ" in text


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


def test_bullcall_sim_never_writes_positions_md(env, monkeypatch):
    written = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    assert run(env, "suggest", "ZZZQ", "--width", "5") == 0
    assert run(env, "open", "ZZZQ", "0.50") == 0
    assert written and "positions.md" not in written
    assert not any(p.name == "positions.md" for p in env["ledger"].rglob("*"))


def test_bullcall_sim_imports_no_broker_modules():
    source = (REPO_ROOT / "scripts" / "bullcall_sim.py").read_text(encoding="utf-8")
    source += (REPO_ROOT / "scripts" / "bullcall_sim_render.py").read_text(encoding="utf-8")
    for name in ("broker_", "placeOrder", "ib_async", "with_suffix"):
        assert name not in source, name
