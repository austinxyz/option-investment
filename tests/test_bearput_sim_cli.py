"""CLI tests for scripts/bearput_sim.py. The IBKR gateway and all market data are faked.

Invented ticker ZQBP; paper-trading numbers only.

Hand-computed example (bearish design section 8), used by the chain fixture and the `open` tests:
  spot 100, buy 100 Put mid 4.80, sell 90 Put mid 1.80, width 10, 1 contract.
  Debit 4.80 - 1.80 = 3.00/share; breakeven 100 - 3.00 = 97.00.
  Max loss 3.00 x 100 = $300; max profit (10 - 3.00) x 100 = $700 (2 contracts: $600 / $1,400).
  Required win rate 3.00 / 10 = 30%; breakeven move -3.00%, max-profit move -10.00%.

Chain fixture: broker delta = -(0.50 + (K - 100) / 50) (so 105 -> 0.60, 100 -> 0.50, 95 -> 0.40,
90 -> 0.30), iv None, mid 4.80 at 100 and 1.80 at 90, else 4.80 e^(0.10 (K-100)),
bid/ask = mid -/+ 0.01. The default window is out of the money only (88-99, strictly below spot,
like ibkr_chain.strike_window), so the at/in-the-money long legs (100, 105) are asked for by
strike from chain_strikes, and the 0.40 bucket's short leg 85 lies outside too.
Expiry: first listed >= 30 days after the snapshot (2026-11-20, 35 days).
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
import bearput_sim
import bearput_sim_rules
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
EXPIRY = "2026-11-20"          # first expiry >= 30 days after SNAP (35 days)
EXPIRIES = ["2026-10-23", "2026-11-13", EXPIRY, "2026-12-18"]
DOWN = ibkr_session.GatewayUnavailable("IBKR 网关不可达：gw.test:14999")
WINDOW = [float(k) for k in range(88, 100)]
LISTED = [float(k) for k in range(80, 121)]
ITM = tuple(float(k) for k in range(100, 112))   # spot..spot x 1.15, nearest 12
T = "ZQBP"
RECORD_KEYS = {
    "strategy", "ticker", "long_put_strike", "short_put_strike", "width", "long_put_mid",
    "short_put_mid", "debit", "breakeven", "breakeven_move_pct", "max_profit_move_pct",
    "delta_bucket", "long_put_delta", "short_put_delta", "long_put_iv", "short_put_iv",
    "underlying_price", "contracts", "expiry", "snapshot_date", "status", "suggestion_file",
    "earnings_in_window",
    # settlement placeholders (filled by spread_sim status / close)
    "exit_date", "exit_value", "exit_underlying", "pnl", "result", "fraction_of_max_captured",
}
SELLER_KEYS = ("credit", "short_strike", "long_strike", "net_credit", "exit_cost",
               "breached_side", "lot_ref", "short_mid", "long_mid", "option_type",
               "long_call_strike", "short_call_strike", "put_strike")
DISPLAY_KEYS = ("max_profit", "max_loss", "required_win_rate", "warnings", "reason")
SELLER_HELPERS = ("threshold_flags", "cost_to_close", "unrealized_pnl", "close_pnl",
                  "expiry_pnl", "breakeven_win_rate", "expiry_cost", "csp_expiry_pnl",
                  "csp_threshold_flags", "bearcall_pnl", "bearcall_close_cost",
                  "bearcall_expiry_cost")


def _mid(k):
    return {100.0: 4.80, 90.0: 1.80}.get(k, round(4.80 * math.exp(0.10 * (k - 100)), 4))


def _row(k, mid=None):
    mid = _mid(k) if mid is None else mid
    return {"strike": k, "bid": round(mid - 0.01, 4), "ask": round(mid + 0.01, 4), "last": mid,
            "iv": None, "delta": -round(min(max(0.50 + (k - 100) / 50, 0.01), 0.99), 4)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"gateway": None, "spot": 100.0, "puts": [], "listed": list(LISTED),
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

    def chain_strikes(t):
        if m["fail_fetch"]:
            raise AssertionError("open must not re-fetch the market")
        return list(m["listed"])

    def fetch_puts(t, e, strikes=(), window_pct=None, window_limit=None):
        if m["fail_fetch"]:
            raise AssertionError("open must not re-fetch the market")
        m["puts"].append(tuple(strikes))
        wanted = sorted(set(m["window"]) | (set(strikes) & set(m["listed"])))
        return [_row(k, m["mids"].get(k)) for k in wanted]

    def no_calls(*a, **k):
        raise AssertionError("bearput_sim never needs the call chain")

    monkeypatch.setattr(spread_data, "chain_strikes", chain_strikes)
    monkeypatch.setattr(spread_data, "fetch_puts", fetch_puts)
    monkeypatch.setattr(spread_data, "fetch_calls", no_calls)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=SNAP):
    return bearput_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def run_spread(env, *argv, today=SNAP):
    return spread_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug_dir(env):
    return env["ledger"] / "suggestions"


def cand_json(env, ticker=T, stamp=SNAP):
    return json.loads((sug_dir(env) / f"{stamp}-{ticker}-bearput.json").read_text(encoding="utf-8"))


def cand_md(env, ticker=T, stamp=SNAP):
    return (sug_dir(env) / f"{stamp}-{ticker}-bearput.md").read_text(encoding="utf-8")


def summary_md(env, stamp=SNAP):
    return (env["ledger"] / "bearput-sim" / f"{stamp}.md").read_text(encoding="utf-8")


def bucket_of(doc, b):
    return next(c for c in doc["candidates"] if c["delta_bucket"] == b)


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def nothing_written(env):
    return not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def hand_candidate(**over):
    c = {"delta_bucket": 0.50, "status": "ok",
         "long_put_strike": 100.0, "short_put_strike": 90.0, "width": 10.0,
         "long_put_mid": 4.80, "short_put_mid": 1.80, "debit": 3.00, "breakeven": 97.0,
         "breakeven_move_pct": -0.03, "max_profit_move_pct": -0.10,
         "long_put_delta": 0.50, "short_put_delta": 0.30,
         "long_put_iv": 0.40, "short_put_iv": None, "underlying_price": 100.0,
         "max_profit": 700.0, "max_loss": 300.0, "required_win_rate": 0.30,
         "warnings": [bearput_sim_rules.WARN_FULL_DEBIT_BEARPUT, bearcall_sim_rules.WARN_DRIFT]}
    c.update(over)
    return c


def write_saved(env, candidates=None, stamp=SNAP, ticker=T, **over):
    """A saved candidate file as suggest writes it (hand numbers, see module docstring)."""
    doc = {"version": 1, "strategy": "bear_put_spread", "generated_on": stamp.isoformat(),
           "data_source": "IBKR", "frozen": True, "intraday": False, "ticker": ticker,
           "snapshot_date": stamp.isoformat(), "spot": 100.0, "expiry": EXPIRY, "days": 35,
           "min_days": 30, "earnings": "no", "ex_dividend": None, "width": 10.0,
           "width_source": "manual",
           "candidates": candidates if candidates is not None else [
               hand_candidate(delta_bucket=0.60, long_put_strike=105.0, short_put_strike=95.0,
                              long_put_mid=7.90, short_put_mid=2.90, debit=5.00,
                              breakeven=100.0, breakeven_move_pct=0.0,
                              max_profit_move_pct=-0.05, long_put_delta=0.60,
                              short_put_delta=0.40, max_profit=500.0, max_loss=500.0,
                              required_win_rate=0.50),
               hand_candidate(),
               {"delta_bucket": 0.40, "status": "unavailable",
                "reason": "下方没有等宽的短腿（需要 Put 85 的有效报价）"}]}
    doc.update(over)
    sug_dir(env).mkdir(parents=True, exist_ok=True)
    path = sug_dir(env) / f"{stamp}-{ticker}-bearput.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (sug_dir(env) / f"{stamp}-{ticker}-bearput.md").write_text("# saved\n", encoding="utf-8")
    return path


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest", T, "--width", "10") != 0
    assert "gw.test:14999" in capsys.readouterr().err
    assert nothing_written(env)
    assert env["puts"] == []                        # no market read after the gate fails


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch):
    def boom(t, s, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", boom)   # the last network read
    assert run(env, "suggest", T, "--width", "10") != 0
    assert nothing_written(env)


def test_suggest_writes_summary_and_candidate_files(env, capsys):
    assert run(env, "suggest", T, "--width", "10") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-{T}-bearput.json", f"{SNAP}-{T}-bearput.md"]
    assert T in summary_md(env)
    doc = cand_json(env)
    assert doc["strategy"] == "bear_put_spread" and doc["data_source"] == "IBKR"
    assert doc["frozen"] is True and doc["snapshot_date"] == SNAP.isoformat()
    assert doc["width"] == 10.0 and doc["width_source"] == "manual"
    md = cand_md(env)
    assert "数据源 IBKR" in md and "冻结行情" in md and SNAP.isoformat() in md
    assert not (env["ledger"] / "spreads.json").exists()
    out = capsys.readouterr().out
    assert "bearput-sim" in out and f"open {T}" in out


def test_suggest_summary_keeps_other_tickers_of_the_same_day(env):
    assert run(env, "suggest", T, "--width", "10") == 0
    assert run(env, "suggest", "ZQBS", "--width", "10") == 0
    text = summary_md(env)
    assert T in text and "ZQBS" in text


def test_suggest_default_expiry_at_least_30_days(env):
    assert run(env, "suggest", T, "--width", "10") == 0
    doc = cand_json(env)
    assert doc["expiry"] == EXPIRY and doc["days"] == 35 and doc["min_days"] == 30
    assert (date.fromisoformat(doc["expiry"]) - SNAP).days >= spread_core.BEARPUT_MIN_DAYS
    for c in doc["candidates"]:
        assert not any("时间不在你这边" in w for w in c.get("warnings", []))


def test_suggest_min_days_override_warns_short_dte(env, capsys):
    assert run(env, "suggest", T, "--width", "10", "--min-days", "7") == 0
    doc = cand_json(env)
    assert doc["expiry"] == "2026-10-23" and doc["days"] == 7 and doc["min_days"] == 7
    assert all(c["status"] == "ok" for c in doc["candidates"])
    for c in doc["candidates"]:
        assert any("时间不在你这边" in w for w in c["warnings"])
    assert "时间不在你这边" in cand_md(env) and "时间不在你这边" in summary_md(env)
    assert "时间不在你这边" in capsys.readouterr().out


def test_suggest_min_days_30_or_more_has_no_short_dte_warning(env, capsys):
    assert run(env, "suggest", T, "--width", "10", "--min-days", "30") == 0
    assert "时间不在你这边" not in cand_md(env)
    assert "时间不在你这边" not in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["6", "0", "-3", "x"])
def test_suggest_min_days_below_7_refused(env, bad):
    with pytest.raises(SystemExit):
        run(env, "suggest", T, "--min-days", bad)
    assert nothing_written(env)


def test_suggest_no_expiry_far_enough_writes_nothing(env, capsys):
    assert run(env, "suggest", T, "--width", "10", "--min-days", "90") == 1
    assert "90" in capsys.readouterr().err
    assert nothing_written(env)


def test_suggest_three_buckets_with_hand_numbers(env):
    assert run(env, "suggest", T, "--width", "10") == 0
    doc = cand_json(env)
    assert [c["delta_bucket"] for c in doc["candidates"]] == [0.60, 0.50, 0.40]
    assert [c["status"] for c in doc["candidates"]] == ["ok", "ok", "ok"]
    assert [(c["long_put_strike"], c["short_put_strike"]) for c in doc["candidates"]] == [
        (105.0, 95.0), (100.0, 90.0), (95.0, 85.0)]
    c = bucket_of(doc, 0.50)
    assert c["long_put_mid"] == pytest.approx(4.80) and c["short_put_mid"] == pytest.approx(1.80)
    assert c["debit"] == pytest.approx(3.00) and c["breakeven"] == pytest.approx(97.00)
    assert c["max_loss"] == pytest.approx(300) and c["max_profit"] == pytest.approx(700)
    assert c["required_win_rate"] == pytest.approx(0.30)
    assert c["breakeven_move_pct"] == pytest.approx(-0.03)
    assert c["max_profit_move_pct"] == pytest.approx(-0.10)
    assert c["long_put_delta"] == pytest.approx(0.50) and c["short_put_delta"] == pytest.approx(0.30)
    md, summary = cand_md(env), summary_md(env)
    for text in (md, summary):
        for value in ("$3.00", "97.00", "$700", "$300", "30%", "-3.00%", "-10.00%"):
            assert value in text, value


def test_suggest_asks_for_at_and_in_the_money_long_legs(env):
    """The default put window is strictly below spot, so the 0.60 (105, ITM) and 0.50 (100, at
    the money) long legs exist only through an explicit request. Asked in every data mode."""
    assert run(env, "suggest", T, "--width", "10") == 0
    assert env["puts"][0] == ()                     # the default window first
    assert env["puts"][1] == ITM                    # then spot .. spot x 1.15, nearest 12
    longs = [c["long_put_strike"] for c in cand_json(env)["candidates"]]
    assert [k for k in longs if k not in WINDOW] == [105.0, 100.0]


def test_suggest_auto_width_uses_put_chain_else_default(env, monkeypatch):
    seen = []

    def fake(puts, spot, days, *a, **k):
        seen.append(sorted(r["strike"] for r in puts))
        return None
    monkeypatch.setattr(spread_core, "recommend_width", fake)
    assert run(env, "suggest", T) == 0
    assert seen == [WINDOW]                         # the default put window, before ITM merge
    doc = cand_json(env)
    assert doc["width"] == spread_core.DEFAULT_WIDTH and doc["width_source"] == "fallback"
    monkeypatch.setattr(spread_core, "recommend_width", lambda c, s, d, *a, **k: {"width": 5.0})
    assert run(env, "suggest", T) == 0
    doc = cand_json(env)
    assert doc["width"] == 5.0 and doc["width_source"] == "auto"
    assert bucket_of(doc, 0.50)["short_put_strike"] == 95.0


def test_suggest_rejected_bucket_lists_reason(env):
    env["listed"] = [k for k in LISTED if k != 85.0]      # the 0.40 short leg is not listed
    assert run(env, "suggest", T, "--width", "10") == 0
    doc = cand_json(env)
    rejected = bucket_of(doc, 0.40)
    assert rejected["status"] == "unavailable" and "下方没有等宽的短腿" in rejected["reason"]
    assert bucket_of(doc, 0.60)["status"] == "ok" and bucket_of(doc, 0.50)["status"] == "ok"
    for text in (cand_md(env), summary_md(env)):
        assert rejected["reason"] in text
    assert len(env["puts"]) <= 2 + bearput_sim.SHORT_LEG_ROUNDS   # bounded re-request


def test_suggest_all_buckets_rejected_still_writes_files(env):
    env["window"] = [float(k) for k in range(70, 75)]    # every delta far from the targets
    env["listed"] = list(env["window"])                  # and nothing at or above spot
    assert run(env, "suggest", T, "--width", "10") == 0
    assert all(c["status"] == "unavailable" for c in cand_json(env)["candidates"])
    assert "三档都被剔除" in cand_md(env)


def test_suggest_requests_short_leg_outside_default_window(env):
    assert run(env, "suggest", T, "--width", "10") == 0
    asked = {k for call in env["puts"] for k in call}
    outside = [c["short_put_strike"] for c in cand_json(env)["candidates"]
               if c["short_put_strike"] not in WINDOW]
    assert outside == [85.0]                        # the fixture really puts one outside
    assert set(outside) <= asked
    assert (85.0,) in env["puts"]
    assert env["puts"] == [(), ITM, (85.0,)]        # nothing asked twice


def test_suggest_short_leg_request_passes_spread_limit(env, monkeypatch):
    seen = []
    real = bearput_sim_rules.short_strike_needed

    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(bearput_sim_rules, "short_strike_needed", spy)
    assert run(env, "suggest", T, "--width", "10") == 0
    assert seen and all(k == {"spread_limit": bearput_sim._spread_limit()} for k in seen)


def test_suggest_every_candidate_shows_fixed_risk_warnings(env, capsys):
    assert run(env, "suggest", T, "--width", "10") == 0
    doc = cand_json(env)
    fixed = (bearput_sim_rules.WARN_FULL_DEBIT_BEARPUT, bearcall_sim_rules.WARN_DRIFT)
    for c in doc["candidates"]:
        for w in fixed:
            assert w in c["warnings"]
    md, summary, out = cand_md(env), summary_md(env), capsys.readouterr().out
    for w in fixed:
        assert md.count(w) == 1                     # banner, not per bucket
        assert w in summary and w in out
    assert "亏掉全部净支出" in out and "所需胜率低于一半是正常的" in out
    assert "向上漂移" in out


def test_suggest_earnings_flow_into_candidates(env):
    env["earnings"] = "yes"
    assert run(env, "suggest", T, "--width", "10") == 0
    doc = cand_json(env)
    assert doc["earnings"] == "yes"
    assert any("财报" in w for w in bucket_of(doc, 0.50)["warnings"])


def test_suggest_dotted_ticker_file_names(env):
    assert run(env, "suggest", "zz.q", "--width", "10") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZ.Q-bearput.json", f"{SNAP}-ZZ.Q-bearput.md"]
    assert run(env, "open", "ZZ.Q", "0.50") == 0
    assert ledger(env)["spreads"][0]["suggestion_file"] == f"{SNAP}-ZZ.Q-bearput.md"


@pytest.mark.parametrize("cmd", [("suggest", T), ("open", T, "0.50")])
def test_width_with_more_than_four_decimals_refused(env, capsys, cmd):
    if cmd[0] == "open":
        write_saved(env)
    before = sorted(p.name for p in env["ledger"].rglob("*")) if env["ledger"].exists() else []
    assert run(env, *cmd, "--width", "10.00001") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "4 位小数" in err and "Traceback" not in err
    after = sorted(p.name for p in env["ledger"].rglob("*")) if env["ledger"].exists() else []
    assert after == before and env["puts"] == []


def test_settings_error_is_clean_error_line(env, monkeypatch, capsys):
    def bad():
        raise ibkr_session.SettingsError("IBKR_PORT 必须是整数，当前是 'x'")
    monkeypatch.setattr(ibkr_session, "ensure_gateway", bad)
    assert run(env, "suggest", T, "--width", "10") != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "IBKR_PORT" in err and "Traceback" not in err
    assert nothing_written(env)


def test_suggest_bad_ticker_refused(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", "../ZQBP")
    assert nothing_written(env)


def test_write_pair_failure_leaves_no_orphan(env, monkeypatch, tmp_path):
    def boom(doc):
        raise TypeError("not serialisable")
    monkeypatch.setattr(bearput_sim, "_json_text", boom)
    with pytest.raises(TypeError):
        run(env, "suggest", T, "--width", "10")
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
        bearput_sim._write_pair(folder / "2026-10-16-ZZ.Q-bearput", "# md\n", "{}\n")
    assert list(folder.iterdir()) == []


# ---- open ------------------------------------------------------------------------

def test_open_records_saved_candidate_without_refetch(env, capsys):
    assert run(env, "suggest", T, "--width", "10") == 0
    saved = bucket_of(cand_json(env), 0.50)
    env["fail_fetch"] = True
    env["gateway"] = DOWN                           # open needs no gateway at all
    env["spot"] = 250.0                             # the market moved; ignored by open
    assert run(env, "open", T, "0.50", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    for key in bearput_sim.COPIED_FIELDS:
        assert s[key] == saved[key], key
    assert s["contracts"] == 2 and s["delta_bucket"] == 0.50 and s["underlying_price"] == 100.0
    assert s["suggestion_file"] == f"{SNAP}-{T}-bearput.md"


def test_open_writes_explicit_fields_and_no_seller_keys(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.50", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert set(s) == RECORD_KEYS | {"id"}
    for key in SELLER_KEYS + DISPLAY_KEYS:
        assert key not in s, key
    assert s["strategy"] == spread_ledger.STRATEGY_BEAR_PUT and s["status"] == "open"
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == SNAP.isoformat()
    assert s["earnings_in_window"] is False and s["short_put_iv"] is None
    for key in ("exit_date", "exit_value", "exit_underlying", "pnl", "result",
                "fraction_of_max_captured"):
        assert s[key] is None, key
    assert spread_ledger.strategy_of(s) == "bear_put_spread"
    out = capsys.readouterr().out
    for value in ("$3.00", "97.00", "$1,400", "$600", "30%", "Put 借方价差", "100/90"):
        assert value in out, value
    assert bearput_sim_rules.WARN_FULL_DEBIT_BEARPUT in out


def test_copied_fields_are_an_explicit_tuple():
    assert isinstance(bearput_sim.COPIED_FIELDS, tuple)
    assert set(bearput_sim.COPIED_FIELDS) | {
        "strategy", "ticker", "contracts", "expiry", "snapshot_date", "status",
        "suggestion_file", "earnings_in_window", "exit_date", "exit_value", "exit_underlying",
        "pnl", "result", "fraction_of_max_captured"} == RECORD_KEYS


@pytest.mark.parametrize("field", ["debit", "breakeven"])
def test_open_debit_tolerance_shared_constant(env, field, monkeypatch, capsys):
    """Rules round mids, debit and breakeven to 4 places each; a 0.5e-4 drift is rounding,
    1.5e-4 is a damaged file. The bound is spread_core.BEARPUT_ROUNDING_TOL (1e-4)."""
    assert spread_core.BEARPUT_ROUNDING_TOL == 1e-4
    base = {"debit": 3.00, "breakeven": 97.00}

    def saved(drift):
        over = dict(base)
        over[field] = base[field] + drift
        if field == "debit":                          # keep breakeven consistent with debit
            over["breakeven"] = 100.0 - over["debit"]
        write_saved(env, candidates=[hand_candidate(**over)])

    saved(0.5e-4)
    assert run(env, "open", T, "0.50") == 0
    (env["ledger"] / "spreads.json").unlink()
    saved(1.5e-4)
    assert run(env, "open", T, "0.50") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    monkeypatch.setattr(spread_core, "BEARPUT_ROUNDING_TOL", 2e-4)   # read live, not copied
    assert run(env, "open", T, "0.50") == 0


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
        write_saved(env, candidates=[{k: v for k, v in hand_candidate().items() if k != "debit"}])
    elif case == "bad_number":
        write_saved(env, candidates=[hand_candidate(long_put_mid="4.80")])
    elif case == "bad_date":
        write_saved(env, expiry="2026-13-40")
    elif case == "bad_bucket":
        write_saved(env, candidates=[hand_candidate(delta_bucket=0.15)])
    elif case == "empty":
        write_saved(env, candidates=[])
    else:
        today = date.fromisoformat(EXPIRY)
    assert run(env, "open", T, "0.50", today=today) == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("over", [
    {"short_put_strike": 100.0, "breakeven": 97.0},                    # long not above short
    {"short_put_strike": 89.0},                                       # legs not width apart
    {"width": 9.0},                                                   # width disagrees with legs
    {"long_put_mid": 1.80, "short_put_mid": 1.80, "debit": 0.0, "breakeven": 100.0},
    {"long_put_mid": 11.80, "short_put_mid": 1.80, "debit": 10.0, "breakeven": 90.0},
    {"long_put_mid": 4.90},                                           # debit != long - short
    {"breakeven": 97.5},                                              # breakeven != long - debit
    {"breakeven": 103.0},                                             # long + debit (call side)
])
def test_open_rejects_invalid_structure(env, over, capsys):
    write_saved(env, candidates=[hand_candidate(**over)])
    assert run(env, "open", T, "0.50") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_without_saved_candidate_refuses(env, capsys):
    assert run(env, "open", T, "0.50") == 1
    assert f"没有 {T} 的已保存 Put 借方价差候选" in capsys.readouterr().err


def test_open_rejects_unavailable_bucket(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.40") == 1
    assert "下方没有等宽的短腿" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_bucket_missing_from_file(env, capsys):
    write_saved(env, candidates=[hand_candidate()])
    assert run(env, "open", T, "0.60") == 1
    assert "0.60" in capsys.readouterr().err


@pytest.mark.parametrize("bucket", ["0.15", "0.30", "0.55"])
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
    assert run(env, "open", T, "0.50", "--width", "5") == 1
    assert "--width" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    assert run(env, "open", T, "0.50", "--width", "10") == 0


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
    assert run(env, "open", T, "0.50") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_second_bucket_same_expiry_blocked(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.50") == 0
    assert run(env, "open", T, "0.60") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_uses_latest_saved_bearput_file(env):
    write_saved(env, stamp=date(2026, 10, 9), candidates=[hand_candidate(
        long_put_mid=4.90, debit=3.10, breakeven=96.9)])
    write_saved(env)
    assert run(env, "open", T, "0.50") == 0
    assert ledger(env)["spreads"][0]["debit"] == pytest.approx(3.00)


# ---- isolation from the other loaders --------------------------------------------

OTHER_PATTERNS = (spread_sim.SUGGESTION_FILE, put_screen.SUGGESTION_FILE,
                  put_screen.SUMMARY_FILE, call_screen.SUGGESTION_FILE,
                  call_screen.SUMMARY_FILE, collar_screen.SUGGESTION_FILE,
                  condor_sim.SUGGESTION_FILE, bullcall_sim.SUGGESTION_FILE,
                  rr_sim.SUGGESTION_FILE, bearcall_sim.SUGGESTION_FILE)


def test_bearput_files_not_picked_up_by_other_loaders(env, capsys):
    assert run(env, "suggest", T, "--width", "10") == 0
    for p in sug_dir(env).iterdir():
        for pattern in OTHER_PATTERNS:
            assert pattern.match(p.name) is None, (pattern.pattern, p.name)
    assert spread_sim._load_suggestion(env["ledger"], T) is None
    with pytest.raises(spread_ledger.LedgerError, match="没有 ZQBP 的已保存铁鹰候选"):
        condor_sim._load_candidate(env["ledger"], T)
    with pytest.raises(spread_ledger.LedgerError, match="Call 借方价差候选"):
        bullcall_sim._load_candidate(env["ledger"], T)
    with pytest.raises(spread_ledger.LedgerError, match="Call 贷方价差候选"):
        bearcall_sim._load_candidate(env["ledger"], T)
    code = spread_sim.main(["--ledger-dir", str(env["ledger"]), "open", T, "0.15"], today=SNAP)
    assert code == 1 and not (env["ledger"] / "spreads.json").exists()

    # and the other way round: none of their files is a bear put candidate
    for p in list(sug_dir(env).iterdir()):
        p.unlink()
    others = [f"{SNAP}-{T}.json", f"{SNAP}-{T}-put.json", f"{SNAP}-{T}-call-real.json",
              f"{SNAP}-{T}-collar-lot1.json", f"{SNAP}-{T}-condor.json",
              f"{SNAP}-{T}-bullcall.json", f"{SNAP}-{T}-rr.json",
              f"{SNAP}-{T}-bearcall.json", f"{SNAP}-{T}-longput.json",
              f"{SNAP}-{T}-bearput.json.tmp", f"{SNAP}-{T}-BEARPUT.json"]
    for name in others:
        (sug_dir(env) / name).write_text("{}", encoding="utf-8")
        assert bearput_sim.SUGGESTION_FILE.match(name) is None, name
    capsys.readouterr()
    assert run(env, "open", T, "0.50") == 1
    assert f"没有 {T} 的已保存 Put 借方价差候选" in capsys.readouterr().err
    assert bearput_sim._day_docs(sug_dir(env), SNAP.isoformat()) == ([], [])


def test_summary_lists_damaged_same_day_file(env):
    sug_dir(env).mkdir(parents=True)
    (sug_dir(env) / f"{SNAP}-ZQBS-bearput.json").write_text("{bad", encoding="utf-8")
    assert run(env, "suggest", T, "--width", "10") == 0
    text = summary_md(env)
    assert f"{SNAP}-ZQBS-bearput.json" in text and T in text


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


def test_bearput_sim_never_writes_positions_md(env, monkeypatch):
    written = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    assert run(env, "suggest", T, "--width", "10") == 0
    assert run(env, "open", T, "0.50") == 0
    assert written and "positions.md" not in written
    assert not any(p.name == "positions.md" for p in env["ledger"].rglob("*"))


def test_bearput_sim_imports_no_broker_modules():
    source = (REPO_ROOT / "scripts" / "bearput_sim.py").read_text(encoding="utf-8")
    source += (REPO_ROOT / "scripts" / "bearput_sim_render.py").read_text(encoding="utf-8")
    for name in ("broker_", "placeOrder", "ib_async", "with_suffix", "bullcall_sim",
                 "BULLCALL_", "bearcall_sim "):
        assert name not in source, name
    assert "import bearcall_sim\n" not in source


# ---- end to end: the opened record is consumed by spread_sim / report / review ---

MID_LIFE = date(2026, 11, 2)
AFTER = date(2026, 11, 23)


def _open_hand_record(env):
    assert run(env, "suggest", T, "--width", "10") == 0
    assert run(env, "open", T, "0.50") == 0
    env["puts"].clear()


def test_open_then_spread_sim_status_close_and_report(env, capsys):
    _open_hand_record(env)
    env["mids"] = {100.0: 5.50, 90.0: 1.50}         # value 4.00 -> +$100
    assert run_spread(env, "status", today=MID_LIFE) == 0
    out = capsys.readouterr().out
    assert "100/90 Put 借方价差" in out and "+$100" in out
    assert env["puts"] == [(100.0, 90.0)]           # explicit strikes=(long, short)
    report = (env["ledger"] / "report.md").read_text(encoding="utf-8")
    assert "100/90 Put" in report and "Put 借方价差" in report
    assert "100/90 Put" in (env["ledger"] / "report.html").read_text(encoding="utf-8")
    assert run(env, "close", "1", today=MID_LIFE) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(100.0)
    assert s["exit_value"] == pytest.approx(4.00) and s["result"] == "win"
    assert s["fraction_of_max_captured"] == pytest.approx(0.1429)
    for key in ("exit_cost", "credit", "short_strike", "long_strike", "breached_side"):
        assert key not in s, key


@pytest.mark.parametrize("close,value,pnl,result,fraction", [
    (105.0, 0.0, -300.0, "loss", -0.4286),
    (100.0, 0.0, -300.0, "loss", -0.4286),        # C = long strike
    (97.0, 3.0, 0.0, "loss", 0.0),                # C = breakeven: 0.0, never -0.0
    (95.0, 5.0, 200.0, "win", 0.2857),
    (90.0, 10.0, 700.0, "win", 1.0),              # C = short strike
    (80.0, 10.0, 700.0, "win", 1.0)])             # clamped to width
def test_open_then_spread_sim_settles_hand_table(env, close, value, pnl, result, fraction):
    _open_hand_record(env)
    env["close"] = close
    assert run_spread(env, "status", today=AFTER) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "expired" and s["exit_underlying"] == close
    assert s["exit_value"] == pytest.approx(value) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_max_captured"] == fraction
    assert str(s["pnl"]) != "-0.0"


def test_open_then_option_review_measures_the_record(env):
    _open_hand_record(env)
    env["mids"] = {100.0: 5.50, 90.0: 1.50}
    (s,) = ledger(env)["spreads"]
    m = put_review.gather_metrics(s, MID_LIFE)
    assert m["error"] is None and m["strategy"] == "bear_put_spread"
    assert m["value"] == pytest.approx(4.00) and m["pnl"] == pytest.approx(100.0)
    assert (m["long_put_strike"], m["short_put_strike"]) == (100.0, 90.0)
    verdict = put_review._review(m)
    assert verdict["rule"] == 5 and verdict["advice"] == "hold"


def test_bearput_sim_never_calls_seller_helpers(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("seller helper used for a bear put spread")
    for name in SELLER_HELPERS:
        monkeypatch.setattr(spread_core, name, boom)
    _open_hand_record(env)
    env["mids"] = {100.0: 5.50, 90.0: 1.50}
    assert run_spread(env, "status", today=MID_LIFE) == 0
    assert run(env, "close", "1", today=MID_LIFE) == 0
    assert ledger(env)["spreads"][0]["pnl"] == pytest.approx(100.0)
    env["ledger"], env["mids"] = env["ledger"].parent / "settle", {}
    _open_hand_record(env)
    env["close"] = 95.0
    assert run(env, "status", today=AFTER) == 0
    assert ledger(env)["spreads"][0]["pnl"] == pytest.approx(200.0)


# ---- open --from-pick (today's /strategy-pick result) ----------------------------

from datetime import timedelta

from frompick_fixtures import bear_held_picks, use_folder, write_pick, write_picks

PICK_LINE = f"按 /strategy-pick {SNAP.isoformat()} 的结果：Delta 目标 0.50–0.60 → 档位 0.60"


@pytest.fixture
def picks(monkeypatch, tmp_path):
    return use_folder(monkeypatch, tmp_path / "picks")


def _files(env):
    return sorted(p.relative_to(env["ledger"]) for p in env["ledger"].rglob("*"))


def test_from_pick_strategy_is_bear_put_spread():
    assert bearput_sim.FROM_PICK_STRATEGY == "bear_put_spread"


def test_open_from_pick_uses_todays_pick_bucket(env, picks):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_put_spread", 0.50, 0.60)
    env["fail_fetch"], env["gateway"] = True, DOWN    # still no market, no gateway
    assert run(env, "open", T.lower(), "--from-pick", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert s["delta_bucket"] == 0.60 and s["contracts"] == 2 and s["long_put_strike"] == 105.0


def test_open_from_pick_works_on_bear_held_second_pick(env, picks, capsys):
    """bear.held = [reduce_position, bear_put_spread]: Bear Put is rank 2 and still usable."""
    write_saved(env)
    write_picks(picks, T, SNAP, bear_held_picks())
    capsys.readouterr()
    assert run(env, "open", T, "--from-pick") == 0
    (s,) = ledger(env)["spreads"]
    assert s["delta_bucket"] == 0.60
    assert PICK_LINE in capsys.readouterr().out.splitlines()


def test_open_from_pick_prints_pick_line(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_put_spread", 0.50, 0.60)
    capsys.readouterr()
    assert run(env, "open", T, "--from-pick") == 0
    assert PICK_LINE in capsys.readouterr().out.splitlines()


def test_open_bucket_and_from_pick_are_mutually_exclusive(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_put_spread", 0.50, 0.60)
    with pytest.raises(SystemExit) as exc:
        run(env, "open", T, "0.60", "--from-pick")
    assert exc.value.code == 2
    assert "档位和 --from-pick 只能二选一" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("case", ["no_pick_today", "older_file", "stale_run_date",
                                  "other_strategy"])
def test_open_from_pick_refusals_exit_2_write_nothing(env, picks, capsys, case):
    write_saved(env)
    yesterday = SNAP - timedelta(days=1)
    if case == "older_file":
        write_pick(picks, T, yesterday, "bear_put_spread", 0.50, 0.60)
    elif case == "stale_run_date":
        write_pick(picks, T, yesterday, "bear_put_spread", 0.50, 0.60, file_date=SNAP)
    elif case == "other_strategy":
        write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
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
    write_pick(picks, T, SNAP - timedelta(days=1), "bear_put_spread", 0.50, 0.60, file_date=SNAP)
    assert run(env, "open", T, "--from-pick") == 2
    assert "不是今天" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_from_pick_refuses_when_strategy_not_in_picks(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_call_spread", 0.10, 0.15)
    assert run(env, "open", T, "--from-pick") == 2
    assert "今天的推荐里没有 Bear Put 价差" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_from_pick_keeps_width_check(env, picks, capsys):
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_put_spread", 0.50, 0.60)
    assert run(env, "open", T, "--from-pick", "--width", "5") == 1
    assert not (env["ledger"] / "spreads.json").exists()


def test_explicit_bucket_still_works_unchanged(env, picks, capsys):
    write_saved(env)
    capsys.readouterr()
    assert run(env, "open", T, "0.50") == 0          # no pick file needed at all
    (s,) = ledger(env)["spreads"]
    assert s["delta_bucket"] == 0.50 and s["long_put_strike"] == 100.0
    assert "/strategy-pick" not in capsys.readouterr().out


def test_from_pick_and_explicit_bucket_record_identical_trades(env, picks):
    base = env["ledger"]
    env["ledger"] = base.parent / "explicit"
    write_saved(env)
    assert run(env, "open", T, "0.60", "--contracts", "3") == 0
    explicit = ledger(env)["spreads"]
    env["ledger"] = base.parent / "frompick"
    write_saved(env)
    write_pick(picks, T, SNAP, "bear_put_spread", 0.50, 0.60)
    assert run(env, "open", T, "--from-pick", "--contracts", "3") == 0
    assert ledger(env)["spreads"] == explicit
