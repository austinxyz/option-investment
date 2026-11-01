"""CLI tests for scripts/bearcall_sim.py. The IBKR gateway and all market data are faked.

Invented ticker ZQBR; paper-trading numbers only.

Hand-computed example (bearish design section 8), used by the chain fixture and the `open` tests:
  spot 100, sell 105 Call mid 2.10, buy 110 Call mid 0.90, width 5, 1 contract.
  Credit 2.10 - 0.90 = 1.20/share; breakeven 105 + 1.20 = 106.20.
  Max profit 1.20 x 100 = $120; max loss (5 - 1.20) x 100 = $380 (2 contracts: $240 / $760).
  Required win rate 1 - 1.20 / 5 = 76%; breakeven move +6.20%, max-loss move +10.00%.

Chain fixture: broker delta = max(0.40 - (K - 100) / 20, 0.01) (so 104 -> 0.20, 105 -> 0.15,
106 -> 0.10), iv None, mid 2.10 at 105 and 0.90 at 110, else 2.10 e^(-0.17 (K-105)),
bid/ask = mid -/+ 0.01. Default window 95-110, so the 0.10 bucket's long leg 111 lies outside.
Expiry: first listed >= 7 days after the snapshot (2026-10-23, 7 days).
"""
from datetime import date
from pathlib import Path
import json
import math
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bearcall_sim
import bearcall_sim_rules
import bullcall_sim
import call_screen
import collar_screen
import condor_sim
import ibkr_session
import put_review
import put_screen
import rr_sim
import spread_core
import spread_data
import spread_ledger
import spread_sim

SNAP = date(2026, 10, 16)
EXPIRY = "2026-10-23"          # first expiry >= 7 days after SNAP
EXPIRIES = ["2026-10-20", EXPIRY, "2026-11-20"]
DOWN = ibkr_session.GatewayUnavailable("IBKR 网关不可达：gw.test:14999")
WINDOW = [float(k) for k in range(95, 111)]
LISTED = [float(k) for k in range(90, 121)]
T = "ZQBR"
RECORD_KEYS = {
    "strategy", "ticker", "option_type", "short_strike", "long_strike", "width", "short_mid",
    "long_mid", "credit", "breakeven", "breakeven_move_pct", "max_loss_move_pct",
    "delta_bucket", "short_delta", "short_iv", "short_spread_pct", "long_spread_pct",
    "underlying_price", "contracts", "expiry", "snapshot_date", "status", "suggestion_file",
    "earnings_in_window",
    # settlement placeholders (filled by spread_sim status / close)
    "exit_date", "exit_cost", "exit_underlying", "pnl", "result",
}
BUYER_KEYS = ("debit", "long_call_strike", "short_call_strike", "long_put_strike",
              "short_put_strike", "put_strike", "net_credit", "breached_side", "lot_ref",
              "exit_value", "fraction_of_max_captured")
DISPLAY_KEYS = ("max_profit", "max_loss", "required_win_rate", "warnings", "reason")


def _mid(k):
    return {105.0: 2.10, 110.0: 0.90}.get(k, round(2.10 * math.exp(-0.17 * (k - 105)), 4))


def _row(k, mid=None):
    mid = _mid(k) if mid is None else mid
    return {"strike": k, "bid": round(mid - 0.01, 4), "ask": round(mid + 0.01, 4), "last": mid,
            "iv": None, "delta": round(max(0.40 - (k - 100) / 20, 0.01), 4)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"gateway": None, "spot": 100.0, "calls": [], "listed": list(LISTED),
         "window": list(WINDOW), "fail_fetch": False, "earnings": "no", "ex_div": None,
         "mids": {}, "close": None}
    monkeypatch.setattr(ibkr_session, "ensure_gateway",
                        lambda: (_ for _ in ()).throw(m["gateway"]) if m["gateway"] else None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, m["spot"]))
    monkeypatch.setattr(spread_data, "expirations", lambda t: list(EXPIRIES))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    monkeypatch.setattr(spread_data, "monday_gap_pct", lambda t, s, p: None)
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: m["close"])

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        if m["fail_fetch"]:
            raise AssertionError("open must not re-fetch the market")
        m["calls"].append(tuple(strikes))
        wanted = sorted(set(m["window"]) | (set(strikes) & set(m["listed"])))
        return [_row(k, m["mids"].get(k)) for k in wanted]

    def no_puts(*a, **k):
        raise AssertionError("bearcall_sim never needs the put chain")

    monkeypatch.setattr(spread_data, "fetch_calls", fetch_calls)
    monkeypatch.setattr(spread_data, "fetch_puts", no_puts)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=SNAP):
    return bearcall_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def run_spread(env, *argv, today=SNAP):
    return spread_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug_dir(env):
    return env["ledger"] / "suggestions"


def cand_json(env, ticker=T, stamp=SNAP):
    return json.loads((sug_dir(env) / f"{stamp}-{ticker}-bearcall.json").read_text(encoding="utf-8"))


def cand_md(env, ticker=T, stamp=SNAP):
    return (sug_dir(env) / f"{stamp}-{ticker}-bearcall.md").read_text(encoding="utf-8")


def summary_md(env, stamp=SNAP):
    return (env["ledger"] / "bearcall-sim" / f"{stamp}.md").read_text(encoding="utf-8")


def bucket_of(doc, b):
    return next(c for c in doc["candidates"] if c["delta_bucket"] == b)


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def nothing_written(env):
    return not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def hand_candidate(**over):
    c = {"delta_bucket": 0.15, "status": "ok", "option_type": "C",
         "short_strike": 105.0, "long_strike": 110.0, "width": 5.0,
         "short_mid": 2.10, "long_mid": 0.90, "credit": 1.20, "breakeven": 106.2,
         "breakeven_move_pct": 0.062, "max_loss_move_pct": 0.10,
         "short_delta": 0.15, "short_iv": None, "short_spread_pct": 0.0095,
         "long_spread_pct": 0.0222, "underlying_price": 100.0,
         "max_profit": 120.0, "max_loss": 380.0, "required_win_rate": 0.76,
         "warnings": [bearcall_sim_rules.WARN_BEAR_CALL_UPSIDE, bearcall_sim_rules.WARN_DRIFT]}
    c.update(over)
    return c


def write_saved(env, candidates=None, stamp=SNAP, ticker=T, **over):
    """A saved candidate file as suggest writes it (hand numbers, see module docstring)."""
    doc = {"version": 1, "strategy": "bear_call_spread", "generated_on": stamp.isoformat(),
           "data_source": "IBKR", "frozen": True, "intraday": False, "ticker": ticker,
           "snapshot_date": stamp.isoformat(), "spot": 100.0, "expiry": EXPIRY, "days": 7,
           "earnings": "no", "ex_dividend": None, "width": 5.0, "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               {"delta_bucket": 0.10, "status": "unavailable",
                "reason": "上方没有等宽的长腿（需要 Call 111 的有效报价）"},
               hand_candidate(),
               hand_candidate(delta_bucket=0.20, short_strike=104.0, long_strike=109.0,
                              short_mid=2.50, long_mid=1.06, credit=1.44, breakeven=105.44,
                              breakeven_move_pct=0.0544, max_loss_move_pct=0.09,
                              short_delta=0.20, max_profit=144.0, max_loss=356.0,
                              required_win_rate=0.712)]}
    doc.update(over)
    sug_dir(env).mkdir(parents=True, exist_ok=True)
    path = sug_dir(env) / f"{stamp}-{ticker}-bearcall.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (sug_dir(env) / f"{stamp}-{ticker}-bearcall.md").write_text("# saved\n", encoding="utf-8")
    return path


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest", T, "--width", "5") != 0
    assert "gw.test:14999" in capsys.readouterr().err
    assert nothing_written(env)
    assert env["calls"] == []                       # no market read after the gate fails


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch):
    def boom(t, s, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", boom)   # the last network read
    assert run(env, "suggest", T, "--width", "5") != 0
    assert nothing_written(env)


def test_suggest_writes_summary_and_candidate_files(env, capsys):
    assert run(env, "suggest", T, "--width", "5") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-{T}-bearcall.json", f"{SNAP}-{T}-bearcall.md"]
    assert T in summary_md(env)
    doc = cand_json(env)
    assert doc["strategy"] == "bear_call_spread" and doc["data_source"] == "IBKR"
    assert doc["frozen"] is True and doc["snapshot_date"] == SNAP.isoformat()
    assert doc["expiry"] == EXPIRY and doc["days"] == 7
    assert doc["width"] == 5.0 and doc["width_source"] == "manual"
    md = cand_md(env)
    assert "数据源 IBKR" in md and "冻结行情" in md and SNAP.isoformat() in md
    assert not (env["ledger"] / "spreads.json").exists()
    out = capsys.readouterr().out
    assert "bearcall-sim" in out and f"open {T}" in out


def test_suggest_summary_keeps_other_tickers_of_the_same_day(env):
    assert run(env, "suggest", T, "--width", "5") == 0
    assert run(env, "suggest", "ZQBS", "--width", "5") == 0
    text = summary_md(env)
    assert T in text and "ZQBS" in text


def test_suggest_has_no_min_days_option(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", T, "--min-days", "30")
    assert nothing_written(env)


def test_suggest_no_expiry_far_enough_writes_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-20"])
    assert run(env, "suggest", T, "--width", "5") == 1
    assert "7" in capsys.readouterr().err
    assert nothing_written(env)


def test_suggest_three_buckets_with_hand_numbers(env):
    assert run(env, "suggest", T, "--width", "5") == 0
    doc = cand_json(env)
    assert [c["delta_bucket"] for c in doc["candidates"]] == [0.10, 0.15, 0.20]
    assert [c["status"] for c in doc["candidates"]] == ["ok", "ok", "ok"]
    assert [(c["short_strike"], c["long_strike"]) for c in doc["candidates"]] == [
        (106.0, 111.0), (105.0, 110.0), (104.0, 109.0)]
    c = bucket_of(doc, 0.15)
    assert c["option_type"] == "C"
    assert c["short_mid"] == pytest.approx(2.10) and c["long_mid"] == pytest.approx(0.90)
    assert c["credit"] == pytest.approx(1.20) and c["breakeven"] == pytest.approx(106.20)
    assert c["max_profit"] == pytest.approx(120) and c["max_loss"] == pytest.approx(380)
    assert c["required_win_rate"] == pytest.approx(0.76)
    assert c["breakeven_move_pct"] == pytest.approx(0.062)
    assert c["max_loss_move_pct"] == pytest.approx(0.10)
    md, summary = cand_md(env), summary_md(env)
    for text in (md, summary):
        for value in ("$1.20", "106.20", "$120", "$380", "76%", "+6.20%", "+10.00%"):
            assert value in text, value


def test_suggest_auto_width_uses_call_chain_else_default(env, monkeypatch):
    seen = []

    def fake(calls, spot, days, *a, **k):
        seen.append(sorted(r["strike"] for r in calls))
        return None
    monkeypatch.setattr(spread_core, "recommend_width", fake)
    assert run(env, "suggest", T) == 0
    assert seen == [WINDOW]
    doc = cand_json(env)
    assert doc["width"] == spread_core.DEFAULT_WIDTH and doc["width_source"] == "fallback"
    monkeypatch.setattr(spread_core, "recommend_width", lambda c, s, d, *a, **k: {"width": 3.0})
    assert run(env, "suggest", T) == 0
    doc = cand_json(env)
    assert doc["width"] == 3.0 and doc["width_source"] == "auto"
    assert bucket_of(doc, 0.15)["long_strike"] == 108.0


def test_suggest_rejected_bucket_lists_reason(env):
    env["listed"] = [k for k in LISTED if k != 111.0]     # the 0.10 long leg is not listed
    assert run(env, "suggest", T, "--width", "5") == 0
    doc = cand_json(env)
    rejected = bucket_of(doc, 0.10)
    assert rejected["status"] == "unavailable" and "上方没有等宽的长腿" in rejected["reason"]
    assert bucket_of(doc, 0.15)["status"] == "ok" and bucket_of(doc, 0.20)["status"] == "ok"
    for text in (cand_md(env), summary_md(env)):
        assert rejected["reason"] in text
    assert len(env["calls"]) <= 1 + bearcall_sim.LONG_LEG_ROUNDS   # bounded re-request


def test_suggest_all_buckets_rejected_still_writes_files(env):
    env["window"] = [float(k) for k in range(95, 100)]   # no out-of-the-money call at all
    env["listed"] = list(env["window"])
    assert run(env, "suggest", T, "--width", "5") == 0
    assert all(c["status"] == "unavailable" for c in cand_json(env)["candidates"])
    assert "三档都被剔除" in cand_md(env)


def test_suggest_requests_long_leg_outside_default_window(env):
    assert run(env, "suggest", T, "--width", "5") == 0
    assert env["calls"][0] == ()                    # short legs: the default window
    asked = {k for call in env["calls"] for k in call}
    outside = [c["long_strike"] for c in cand_json(env)["candidates"]
               if c["long_strike"] not in WINDOW]
    assert outside == [111.0]                       # the fixture really puts one outside
    assert set(outside) <= asked
    assert (111.0,) in env["calls"]


def test_suggest_long_leg_request_passes_spread_limit(env, monkeypatch):
    seen = []
    real = bearcall_sim_rules.long_strike_needed

    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(bearcall_sim_rules, "long_strike_needed", spy)
    assert run(env, "suggest", T, "--width", "5") == 0
    assert seen and all(k == {"spread_limit": bearcall_sim._spread_limit()} for k in seen)


def test_suggest_every_candidate_shows_fixed_risk_warnings(env, capsys):
    assert run(env, "suggest", T, "--width", "5") == 0
    doc = cand_json(env)
    fixed = (bearcall_sim_rules.WARN_BEAR_CALL_UPSIDE, bearcall_sim_rules.WARN_DRIFT)
    for c in doc["candidates"]:
        for w in fixed:
            assert w in c["warnings"]
    md, summary, out = cand_md(env), summary_md(env), capsys.readouterr().out
    for w in fixed:
        assert md.count(w) == 1                     # banner, not per bucket
        assert w in summary and w in out
    assert "上涨亏钱" in out and "宽度减权利金" in out


def test_suggest_earnings_flow_into_candidates(env):
    env["earnings"] = "yes"
    assert run(env, "suggest", T, "--width", "5") == 0
    doc = cand_json(env)
    assert doc["earnings"] == "yes"
    assert any("财报" in w for w in bucket_of(doc, 0.15)["warnings"])


def test_suggest_dotted_ticker_file_names(env):
    assert run(env, "suggest", "zz.q", "--width", "5") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZ.Q-bearcall.json", f"{SNAP}-ZZ.Q-bearcall.md"]
    assert run(env, "open", "ZZ.Q", "0.15") == 0
    assert ledger(env)["spreads"][0]["suggestion_file"] == f"{SNAP}-ZZ.Q-bearcall.md"


@pytest.mark.parametrize("cmd", [("suggest", T), ("open", T, "0.15")])
def test_width_with_more_than_four_decimals_refused(env, capsys, cmd):
    if cmd[0] == "open":
        write_saved(env)
    before = sorted(p.name for p in env["ledger"].rglob("*")) if env["ledger"].exists() else []
    assert run(env, *cmd, "--width", "5.00001") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "4 位小数" in err and "Traceback" not in err
    after = sorted(p.name for p in env["ledger"].rglob("*")) if env["ledger"].exists() else []
    assert after == before and env["calls"] == []


def test_settings_error_is_clean_error_line(env, monkeypatch, capsys):
    def bad():
        raise ibkr_session.SettingsError("IBKR_PORT 必须是整数，当前是 'x'")
    monkeypatch.setattr(ibkr_session, "ensure_gateway", bad)
    assert run(env, "suggest", T, "--width", "5") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "IBKR_PORT" in err and "Traceback" not in err
    assert nothing_written(env)


def test_suggest_bad_ticker_refused(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", "../ZQBR")
    assert nothing_written(env)


def test_write_pair_failure_leaves_no_orphan(env, monkeypatch, tmp_path):
    def boom(doc):
        raise TypeError("not serialisable")
    monkeypatch.setattr(bearcall_sim, "_json_text", boom)
    with pytest.raises(TypeError):
        run(env, "suggest", T, "--width", "5")
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
        bearcall_sim._write_pair(folder / "2026-10-16-ZZ.Q-bearcall", "# md\n", "{}\n")
    assert list(folder.iterdir()) == []


# ---- open ------------------------------------------------------------------------

def test_open_records_saved_candidate_without_refetch(env, capsys):
    assert run(env, "suggest", T, "--width", "5") == 0
    saved = bucket_of(cand_json(env), 0.15)
    env["fail_fetch"] = True
    env["gateway"] = DOWN                           # open needs no gateway at all
    env["spot"] = 250.0                             # the market moved; ignored by open
    assert run(env, "open", T, "0.15", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    for key in bearcall_sim.COPIED_FIELDS:
        assert s[key] == saved[key], key
    assert s["contracts"] == 2 and s["delta_bucket"] == 0.15 and s["underlying_price"] == 100.0
    assert s["option_type"] == "C"
    assert s["suggestion_file"] == f"{SNAP}-{T}-bearcall.md"


def test_open_writes_explicit_fields_and_no_buyer_keys(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.15", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert set(s) == RECORD_KEYS | {"id"}
    for key in BUYER_KEYS + DISPLAY_KEYS:
        assert key not in s, key
    assert s["strategy"] == spread_ledger.STRATEGY_BEAR_CALL and s["status"] == "open"
    assert s["option_type"] == "C"
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == SNAP.isoformat()
    assert s["earnings_in_window"] is False and s["short_iv"] is None
    for key in ("exit_date", "exit_cost", "exit_underlying", "pnl", "result"):
        assert s[key] is None, key
    assert spread_ledger.strategy_of(s) == "bear_call_spread"
    out = capsys.readouterr().out
    for value in ("$1.20", "106.20", "$240", "$760", "76%", "Call 贷方价差"):
        assert value in out, value
    assert bearcall_sim_rules.WARN_BEAR_CALL_UPSIDE in out


def test_copied_fields_are_an_explicit_tuple():
    assert isinstance(bearcall_sim.COPIED_FIELDS, tuple)
    assert set(bearcall_sim.COPIED_FIELDS) | {
        "strategy", "ticker", "contracts", "expiry", "snapshot_date", "status",
        "suggestion_file", "earnings_in_window", "exit_date", "exit_cost", "exit_underlying",
        "pnl", "result"} == RECORD_KEYS


@pytest.mark.parametrize("field", ["credit", "breakeven"])
def test_open_credit_tolerance_shared_constant(env, field, monkeypatch, capsys):
    """Rules round mids, credit and breakeven to 4 places each; a 0.5e-4 drift is rounding,
    1.5e-4 is a damaged file. The bound is spread_core.BEARCALL_ROUNDING_TOL (1e-4)."""
    assert spread_core.BEARCALL_ROUNDING_TOL == 1e-4
    base = {"credit": 1.20, "breakeven": 106.20}

    def saved(drift):
        over = dict(base)
        over[field] = base[field] + drift
        if field == "credit":                         # keep breakeven consistent with credit
            over["breakeven"] = 105.0 + over["credit"]
        write_saved(env, candidates=[hand_candidate(**over)])

    saved(0.5e-4)
    assert run(env, "open", T, "0.15") == 0
    (env["ledger"] / "spreads.json").unlink()
    saved(1.5e-4)
    assert run(env, "open", T, "0.15") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    monkeypatch.setattr(spread_core, "BEARCALL_ROUNDING_TOL", 2e-4)   # read live, not copied
    assert run(env, "open", T, "0.15") == 0


@pytest.mark.parametrize("case", ["bad_json", "wrong_strategy", "wrong_version",
                                  "ticker_mismatch", "missing_key", "bad_number", "bad_date",
                                  "bad_bucket", "empty", "expired"])
def test_open_rejects_damaged_expired_or_mismatched_candidate(env, case, capsys):
    path = write_saved(env)
    today = SNAP
    if case == "bad_json":
        path.write_text("{not json", encoding="utf-8")
    elif case == "wrong_strategy":
        write_saved(env, strategy="bull_call_spread")
    elif case == "wrong_version":
        write_saved(env, version=2)
    elif case == "ticker_mismatch":
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**doc, "ticker": "ZQBS"}), encoding="utf-8")
    elif case == "missing_key":
        write_saved(env, candidates=[{k: v for k, v in hand_candidate().items() if k != "credit"}])
    elif case == "bad_number":
        write_saved(env, candidates=[hand_candidate(short_mid="2.10")])
    elif case == "bad_date":
        write_saved(env, expiry="2026-13-40")
    elif case == "bad_bucket":
        write_saved(env, candidates=[hand_candidate(delta_bucket=0.50)])
    elif case == "empty":
        write_saved(env, candidates=[])
    else:
        today = date.fromisoformat(EXPIRY)
    assert run(env, "open", T, "0.15", today=today) == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("over", [
    {"long_strike": 105.0},                                           # long not above short
    {"long_strike": 111.0},                                           # legs not width apart
    {"width": 4.0},                                                   # width disagrees with legs
    {"short_mid": 0.90, "long_mid": 0.90, "credit": 0.0, "breakeven": 105.0},
    {"short_mid": 5.90, "long_mid": 0.90, "credit": 5.0, "breakeven": 110.0},
    {"short_mid": 2.20},                                              # credit != short - long
    {"breakeven": 106.5},                                             # breakeven != short + credit
    {"option_type": "P"},                                             # not a call spread
])
def test_open_rejects_invalid_structure(env, over, capsys):
    write_saved(env, candidates=[hand_candidate(**over)])
    assert run(env, "open", T, "0.15") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_without_saved_candidate_refuses(env, capsys):
    assert run(env, "open", T, "0.15") == 1
    assert f"没有 {T} 的已保存 Call 贷方价差候选" in capsys.readouterr().err


def test_open_rejects_unavailable_bucket(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.10") == 1
    assert "上方没有等宽的长腿" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_bucket_missing_from_file(env, capsys):
    write_saved(env, candidates=[hand_candidate()])
    assert run(env, "open", T, "0.20") == 1
    assert "0.20" in capsys.readouterr().err


@pytest.mark.parametrize("bucket", ["0.50", "0.60", "0.25"])
def test_open_rejects_bucket_outside_choices(env, bucket):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", T, bucket)


def test_open_requires_bucket_or_from_pick(env, capsys):
    write_saved(env)
    with pytest.raises(SystemExit) as exc:
        run(env, "open", T)
    assert exc.value.code == 2
    assert "open 需要档位" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_width_mismatch(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.15", "--width", "3") == 1
    assert "--width" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    assert run(env, "open", T, "0.15", "--width", "5") == 0


@pytest.mark.parametrize("other", [
    {},                                                               # bull put (no strategy key)
    {"strategy": "iron_condor"},
    {"strategy": "cash_secured_put"},
    {"strategy": "covered_call", "lot_ref": {"source": "paper", "lot_id": 1, "ticker": T}},
    {"strategy": "bull_call_spread"},
    {"strategy": "bear_call_spread"},
    {"strategy": "bear_put_spread"},
    {"strategy": "long_put"},
])
def test_open_rejects_duplicate_ticker_expiry_any_strategy(env, other, capsys):
    led = spread_ledger.empty_ledger()
    led, _ = spread_ledger.add_spread(led, {"ticker": T, "expiry": EXPIRY,
                                            "status": "open", "contracts": 1, **other})
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json", led)
    write_saved(env)
    assert run(env, "open", T, "0.15") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_second_bucket_same_expiry_blocked(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.15") == 0
    assert run(env, "open", T, "0.20") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_uses_latest_saved_bearcall_file(env):
    write_saved(env, stamp=date(2026, 10, 9), candidates=[hand_candidate(
        short_mid=2.20, credit=1.30, breakeven=106.3)])
    write_saved(env)
    assert run(env, "open", T, "0.15") == 0
    assert ledger(env)["spreads"][0]["credit"] == pytest.approx(1.20)


# ---- isolation from the other loaders --------------------------------------------

OTHER_PATTERNS = (spread_sim.SUGGESTION_FILE, put_screen.SUGGESTION_FILE,
                  put_screen.SUMMARY_FILE, call_screen.SUGGESTION_FILE,
                  call_screen.SUMMARY_FILE, collar_screen.SUGGESTION_FILE,
                  condor_sim.SUGGESTION_FILE, bullcall_sim.SUGGESTION_FILE,
                  rr_sim.SUGGESTION_FILE)


def test_bearcall_files_not_picked_up_by_other_loaders(env, capsys):
    assert run(env, "suggest", T, "--width", "5") == 0
    for p in sug_dir(env).iterdir():
        for pattern in OTHER_PATTERNS:
            assert pattern.match(p.name) is None, (pattern.pattern, p.name)
    assert spread_sim._load_suggestion(env["ledger"], T) is None
    with pytest.raises(spread_ledger.LedgerError, match="没有 ZQBR 的已保存铁鹰候选"):
        condor_sim._load_candidate(env["ledger"], T)
    with pytest.raises(spread_ledger.LedgerError, match="Call 借方价差候选"):
        bullcall_sim._load_candidate(env["ledger"], T)
    code = spread_sim.main(["--ledger-dir", str(env["ledger"]), "open", T, "0.15"], today=SNAP)
    assert code == 1 and not (env["ledger"] / "spreads.json").exists()

    # and the other way round: none of their files is a bear call candidate
    for p in list(sug_dir(env).iterdir()):
        p.unlink()
    others = [f"{SNAP}-{T}.json", f"{SNAP}-{T}-put.json", f"{SNAP}-{T}-call-real.json",
              f"{SNAP}-{T}-collar-lot1.json", f"{SNAP}-{T}-condor.json",
              f"{SNAP}-{T}-bullcall.json", f"{SNAP}-{T}-rr.json",
              f"{SNAP}-{T}-bearput.json", f"{SNAP}-{T}-longput.json",
              f"{SNAP}-{T}-bearcall.json.tmp", f"{SNAP}-{T}-BEARCALL.json"]
    for name in others:
        (sug_dir(env) / name).write_text("{}", encoding="utf-8")
        assert bearcall_sim.SUGGESTION_FILE.match(name) is None, name
    capsys.readouterr()
    assert run(env, "open", T, "0.15") == 1
    assert f"没有 {T} 的已保存 Call 贷方价差候选" in capsys.readouterr().err
    assert bearcall_sim._day_docs(sug_dir(env), SNAP.isoformat()) == ([], [])


def test_summary_lists_damaged_same_day_file(env):
    sug_dir(env).mkdir(parents=True)
    (sug_dir(env) / f"{SNAP}-ZQBS-bearcall.json").write_text("{bad", encoding="utf-8")
    assert run(env, "suggest", T, "--width", "5") == 0
    text = summary_md(env)
    assert f"{SNAP}-ZQBS-bearcall.json" in text and T in text


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


def test_bearcall_sim_never_writes_positions_md(env, monkeypatch):
    written = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    assert run(env, "suggest", T, "--width", "5") == 0
    assert run(env, "open", T, "0.15") == 0
    assert written and "positions.md" not in written
    assert not any(p.name == "positions.md" for p in env["ledger"].rglob("*"))


def test_bearcall_sim_imports_no_broker_modules():
    source = (REPO_ROOT / "scripts" / "bearcall_sim.py").read_text(encoding="utf-8")
    source += (REPO_ROOT / "scripts" / "bearcall_sim_render.py").read_text(encoding="utf-8")
    for name in ("broker_", "placeOrder", "ib_async", "with_suffix", "bullcall_sim",
                 "BULLCALL_"):
        assert name not in source, name


# ---- end to end: the opened record is consumed by spread_sim / report / review ---

MID_LIFE = date(2026, 10, 19)
AFTER = date(2026, 10, 24)


def _open_hand_record(env):
    assert run(env, "suggest", T, "--width", "5") == 0
    assert run(env, "open", T, "0.15") == 0
    env["calls"].clear()


def test_open_then_spread_sim_status_close_and_report(env, capsys):
    _open_hand_record(env)
    env["mids"] = {105.0: 1.40, 110.0: 0.50}       # design 8: cost 0.90 -> +$30
    assert run_spread(env, "status", today=MID_LIFE) == 0
    out = capsys.readouterr().out
    assert "105/110 Call 贷方价差" in out and "+$30" in out
    assert env["calls"] == [(105.0, 110.0)]        # explicit strikes=(short, long)
    report = (env["ledger"] / "report.md").read_text(encoding="utf-8")
    assert "105/110 Call" in report and "Call 贷方价差" in report
    assert "105/110 Call" in (env["ledger"] / "report.html").read_text(encoding="utf-8")
    assert run(env, "close", "1", today=MID_LIFE) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(30.0)
    assert s["exit_cost"] == pytest.approx(0.90) and s["result"] == "win"


@pytest.mark.parametrize("close,cost,pnl,result", [
    (100.0, 0.0, 120.0, "win"), (105.0, 0.0, 120.0, "win"), (106.2, 1.2, 0.0, "loss"),
    (108.0, 3.0, -180.0, "loss"), (110.0, 5.0, -380.0, "loss"),   # 110 = long strike
    (120.0, 5.0, -380.0, "loss")])
def test_open_then_spread_sim_settles_hand_table(env, close, cost, pnl, result):
    _open_hand_record(env)
    env["close"] = close
    assert run_spread(env, "status", today=AFTER) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "expired" and s["exit_underlying"] == close
    assert s["exit_cost"] == pytest.approx(cost) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result
    assert str(s["pnl"]) != "-0.0"


def test_open_then_option_review_measures_the_record(env):
    _open_hand_record(env)
    env["mids"] = {105.0: 1.40, 110.0: 0.50}
    (s,) = ledger(env)["spreads"]
    m = put_review.gather_metrics(s, MID_LIFE)
    assert m["error"] is None and m["strategy"] == "bear_call_spread"
    assert m["cost_to_close"] == pytest.approx(0.90) and m["pnl"] == pytest.approx(30.0)
    assert m["itm"] is False
    verdict = put_review._review(m)
    assert verdict["rule"] == 5 and verdict["advice"] == "hold"


def test_bearcall_never_calls_put_direction_expiry_pnl(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("put-direction helper used for a bear call spread")
    for name in ("expiry_pnl", "expiry_cost", "csp_expiry_pnl", "cost_to_close",
                 "unrealized_pnl", "close_pnl"):
        monkeypatch.setattr(spread_core, name, boom)
    _open_hand_record(env)
    env["mids"] = {105.0: 1.40, 110.0: 0.50}
    assert run_spread(env, "status", today=MID_LIFE) == 0
    assert run(env, "close", "1", today=MID_LIFE) == 0
    env["ledger"], env["mids"] = env["ledger"].parent / "settle", {}
    _open_hand_record(env)
    env["close"] = 108.0
    assert run(env, "status", today=AFTER) == 0
    assert ledger(env)["spreads"][0]["pnl"] == pytest.approx(-180.0)


# ---- open --from-pick (today's /strategy-pick result) ----------------------------

from datetime import timedelta

from frompick_fixtures import bear_held_picks, use_folder, write_pick, write_picks

PICK_LINE = f"按 /strategy-pick {SNAP.isoformat()} 的结果：Delta 目标 0.10–0.15 → 档位 0.15"


@pytest.fixture
def picks(monkeypatch, tmp_path):
    return use_folder(monkeypatch, tmp_path / "picks")


def _files(env):
    return sorted(p.relative_to(env["ledger"]) for p in env["ledger"].rglob("*"))


def test_from_pick_strategy_is_bear_call_spread():
    assert bearcall_sim.FROM_PICK_STRATEGY == "bear_call_spread"


def test_open_from_pick_uses_todays_pick_bucket(env, picks):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
    env["fail_fetch"], env["gateway"] = True, DOWN    # still no market, no gateway
    assert run(env, "open", T.lower(), "--from-pick", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert s["delta_bucket"] == 0.15 and s["contracts"] == 2 and s["short_strike"] == 105.0


def test_open_from_pick_prints_pick_line(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
    capsys.readouterr()
    assert run(env, "open", T, "--from-pick") == 0
    assert PICK_LINE in capsys.readouterr().out.splitlines()


def test_open_bucket_and_from_pick_are_mutually_exclusive(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
    with pytest.raises(SystemExit) as exc:
        run(env, "open", T, "0.15", "--from-pick")
    assert exc.value.code == 2
    assert "档位和 --from-pick 只能二选一" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("case", ["no_pick_today", "older_file", "stale_run_date",
                                  "other_strategy", "bear_held"])
def test_open_from_pick_refusals_exit_2_write_nothing(env, picks, capsys, case):
    write_saved(env)
    yesterday = SNAP - timedelta(days=1)
    if case == "older_file":
        write_pick(picks, T, yesterday, "bear_call_spread", 0.10, 0.15)
    elif case == "stale_run_date":
        write_pick(picks, T, yesterday, "bear_call_spread", 0.10, 0.15, file_date=SNAP)
    elif case == "other_strategy":
        write_pick(picks, T, SNAP, "bear_put_spread", 0.50, 0.60)
    elif case == "bear_held":
        write_picks(picks, T, SNAP, bear_held_picks())
    before = _files(env)
    capsys.readouterr()
    assert run(env, "open", T, "--from-pick") == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("错误：")
    assert _files(env) == before


def test_open_from_pick_refuses_when_no_pick_today(env, picks, capsys):
    write_saved(env)
    assert run(env, "open", T, "--from-pick") == 2
    assert f"今天还没有 {T} 的 /strategy-pick 结果" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_from_pick_refuses_stale_pick_file(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP - timedelta(days=1), "bear_call_spread", 0.10, 0.15, file_date=SNAP)
    assert run(env, "open", T, "--from-pick") == 2
    assert "不是今天" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_from_pick_refuses_when_strategy_not_in_picks(env, picks, capsys):
    """bear.held recommends reduce_position and Bear Put only: Bear Call must refuse."""
    write_saved(env)
    write_picks(picks, T, SNAP, bear_held_picks())
    assert run(env, "open", T, "--from-pick") == 2
    err = capsys.readouterr().err
    assert "今天的推荐里没有 Bear Call 价差" in err and "Bear Put 价差" in err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_from_pick_keeps_width_check(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
    assert run(env, "open", T, "--from-pick", "--width", "3") == 1
    assert not (env["ledger"] / "spreads.json").exists()


def test_explicit_bucket_still_works_unchanged(env, picks, capsys):
    write_saved(env)
    capsys.readouterr()
    assert run(env, "open", T, "0.20") == 0          # no pick file needed at all
    (s,) = ledger(env)["spreads"]
    assert s["delta_bucket"] == 0.20 and s["short_strike"] == 104.0
    assert "/strategy-pick" not in capsys.readouterr().out


def test_from_pick_and_explicit_bucket_record_identical_trades(env, picks):
    base = env["ledger"]
    env["ledger"] = base.parent / "explicit"
    write_saved(env)
    assert run(env, "open", T, "0.15", "--contracts", "3") == 0
    explicit = ledger(env)["spreads"]
    env["ledger"] = base.parent / "frompick"
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
    assert run(env, "open", T, "--from-pick", "--contracts", "3") == 0
    assert ledger(env)["spreads"] == explicit


def test_strategy_pick_modules_do_not_import_bearcall_or_bearput_sim():
    """Dependency runs command -> strategy_pick_frompick only, never back."""
    for name in ("bearcall_sim", "bearput_sim"):
        source = (REPO_ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        assert "import strategy_pick_frompick as frompick\n" in source, name
    for path in sorted((REPO_ROOT / "scripts").glob("strategy_pick*.py")):
        source = path.read_text(encoding="utf-8")
        for word in ("bearcall_sim", "bearput_sim"):
            assert word not in source, (path.name, word)
