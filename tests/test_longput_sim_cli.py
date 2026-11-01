"""CLI tests for scripts/longput_sim.py. The IBKR gateway and all market data are faked.

Invented ticker ZQLP; paper-trading numbers only.

Hand-computed example (long put design section 3), used by the chain fixture and the `open` tests:
  spot 100, buy 95 Put mid 2.40, 1 contract.
  Debit 2.40/share; breakeven 95 - 2.40 = 92.60; breakeven move -7.40%.
  Max loss 2.40 x 100 = $240 (2 contracts: $480); display max profit (95 - 2.40) x 100 = $9,260
  (2 contracts: $18,520). No required win rate. Take-profit +$240, stop -$120.
  Expiry settlement: C = 100 / 95 / 92.60 / 90 / 80 -> pnl -240 / -240 / 0.0 / +260 / +1260,
  fraction_of_debit -1.0 / -1.0 / 0.0 / 1.0833 / 5.25.

Chain fixture: broker delta = -(0.50 + (K - 100) / 50) (so 100 -> 0.50, 95 -> 0.40, 90 -> 0.30),
iv None, mid 2.40 at 95, else 2.40 e^(0.12 (K - 95)), bid/ask = mid -/+ 0.01. The default
window is out of the money only (88-99, strictly below spot, like ibkr_chain.strike_window), so
the at-the-money 0.50 leg (strike 100 = spot) exists only through an explicit request by strike
(spot .. spot x 1.10, nearest 8 -> 100-107), made window-free (window_pct=0, window_limit=0).
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
import bearput_sim
import bullcall_sim
import call_screen
import collar_screen
import condor_sim
import ibkr_session
import longput_sim
import longput_sim_rules
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
ATM = tuple(float(k) for k in range(100, 108))   # spot..spot x 1.10, nearest 8
T = "ZQLP"
RECORD_KEYS = {
    "strategy", "ticker", "put_strike", "put_mid", "debit", "breakeven", "breakeven_move_pct",
    "delta_bucket", "put_delta", "put_iv", "underlying_price", "contracts", "expiry",
    "snapshot_date", "status", "suggestion_file", "earnings_in_window",
    # settlement placeholders (filled by spread_sim status / close)
    "exit_date", "exit_value", "exit_underlying", "pnl", "result", "fraction_of_debit",
}
SELLER_KEYS = ("credit", "short_strike", "long_strike", "width", "net_credit", "exit_cost",
               "breached_side", "lot_ref", "short_mid", "long_mid", "option_type",
               "long_put_strike", "short_put_strike", "long_call_strike", "short_call_strike",
               "call_strike", "put_debit", "fraction_of_max_captured")
DISPLAY_KEYS = ("max_profit", "max_loss", "required_win_rate", "warnings", "reason")
FOREIGN_HELPERS = ("threshold_flags", "cost_to_close", "unrealized_pnl", "close_pnl",
                   "expiry_pnl", "breakeven_win_rate", "expiry_cost", "csp_expiry_pnl",
                   "csp_threshold_flags", "bearcall_pnl", "bearcall_close_cost",
                   "bearcall_expiry_cost", "bearput_pnl", "bearput_close_value",
                   "bearput_expiry_value", "bearput_threshold_flags",
                   "bearput_required_win_rate", "bearput_fraction_captured")


def _mid(k):
    return 2.40 if k == 95.0 else round(2.40 * math.exp(0.12 * (k - 95)), 4)


def _row(k, mid=None):
    mid = _mid(k) if mid is None else mid
    return {"strike": k, "bid": round(mid - 0.01, 4), "ask": round(mid + 0.01, 4), "last": mid,
            "iv": None, "delta": -round(min(max(0.50 + (k - 100) / 50, 0.01), 0.99), 4)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"gateway": None, "spot": 100.0, "puts": [], "listed": list(LISTED),
         "window": list(WINDOW), "fail_fetch": False, "earnings": "no", "ex_div": None,
         "mids": {}, "rows": {}, "close": None, "itm_error": None}
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
        m["puts"].append((tuple(strikes), window_pct, window_limit))
        if strikes and m["itm_error"] is not None:
            raise m["itm_error"]
        window = [] if (window_pct, window_limit) == (0, 0) else m["window"]
        wanted = sorted(set(window) | (set(strikes) & set(m["listed"])))
        return [m["rows"].get(k) or _row(k, m["mids"].get(k)) for k in wanted]

    def no_calls(*a, **k):
        raise AssertionError("longput_sim never needs the call chain")

    monkeypatch.setattr(spread_data, "chain_strikes", chain_strikes)
    monkeypatch.setattr(spread_data, "fetch_puts", fetch_puts)
    monkeypatch.setattr(spread_data, "fetch_calls", no_calls)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=SNAP):
    return longput_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def run_spread(env, *argv, today=SNAP):
    return spread_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug_dir(env):
    return env["ledger"] / "suggestions"


def cand_json(env, ticker=T, stamp=SNAP):
    return json.loads((sug_dir(env) / f"{stamp}-{ticker}-longput.json").read_text(encoding="utf-8"))


def cand_md(env, ticker=T, stamp=SNAP):
    return (sug_dir(env) / f"{stamp}-{ticker}-longput.md").read_text(encoding="utf-8")


def summary_md(env, stamp=SNAP):
    return (env["ledger"] / "longput-sim" / f"{stamp}.md").read_text(encoding="utf-8")


def bucket_of(doc, b):
    return next(c for c in doc["candidates"] if c["delta_bucket"] == b)


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def nothing_written(env):
    return not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def hand_candidate(**over):
    c = {"delta_bucket": 0.40, "status": "ok",
         "put_strike": 95.0, "put_mid": 2.40, "debit": 2.40, "breakeven": 92.6,
         "breakeven_move_pct": -0.074, "put_delta": 0.40, "put_iv": None,
         "underlying_price": 100.0, "max_profit": 9260.0, "max_loss": 240.0,
         "warnings": [longput_sim_rules.WARN_THETA_DECAY]}
    c.update(over)
    return c


def write_saved(env, candidates=None, stamp=SNAP, ticker=T, **over):
    """A saved candidate file as suggest writes it (hand numbers, see module docstring)."""
    doc = {"version": 1, "strategy": "long_put", "generated_on": stamp.isoformat(),
           "data_source": "IBKR", "frozen": True, "intraday": False, "ticker": ticker,
           "snapshot_date": stamp.isoformat(), "spot": 100.0, "expiry": EXPIRY, "days": 35,
           "min_days": 30, "earnings": "no", "ex_dividend": None,
           "candidates": candidates if candidates is not None else [
               hand_candidate(delta_bucket=0.50, put_strike=100.0, put_mid=4.37, debit=4.37,
                              breakeven=95.63, breakeven_move_pct=-0.0437, put_delta=0.50,
                              max_profit=9563.0, max_loss=437.0),
               hand_candidate(),
               {"delta_bucket": 0.30, "status": "unavailable",
                "reason": "Put 买卖价差 8.0% 超过 5%"}]}
    doc.update(over)
    sug_dir(env).mkdir(parents=True, exist_ok=True)
    path = sug_dir(env) / f"{stamp}-{ticker}-longput.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (sug_dir(env) / f"{stamp}-{ticker}-longput.md").write_text("# saved\n", encoding="utf-8")
    return path


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest", T) != 0
    assert "gw.test:14999" in capsys.readouterr().err
    assert nothing_written(env)
    assert env["puts"] == []                        # no market read after the gate fails


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch):
    def boom(t, s, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", boom)   # the last network read
    assert run(env, "suggest", T) != 0
    assert nothing_written(env)


@pytest.mark.parametrize("error", [
    spread_data.SourceUnavailable("IBKR 网关断开"),
    spread_data.DataError("取 ZQLP 期权链失败"),           # not an outage: still never swallowed
])
def test_suggest_error_on_atm_request_aborts_and_writes_nothing(env, capsys, error):
    """Any DataError on the explicit at-the-money request aborts the run (as bearput_sim does):
    silently dropping it would move the 0.50 bucket to a worse strike with no note."""
    env["itm_error"] = error
    assert run(env, "suggest", T) != 0
    assert str(error) in capsys.readouterr().err
    assert nothing_written(env)


def test_suggest_rerun_same_day_overwrites_the_pair(env):
    assert run(env, "suggest", T) == 0
    env["mids"] = {95.0: 2.50}
    assert run(env, "suggest", T) == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-{T}-longput.json", f"{SNAP}-{T}-longput.md"]
    assert bucket_of(cand_json(env), 0.40)["debit"] == pytest.approx(2.50)
    assert "$2.50" in cand_md(env) and summary_md(env).count(f"| {T} | 0.40 |") == 1


def test_suggest_writes_summary_and_candidate_files(env, capsys):
    assert run(env, "suggest", T) == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-{T}-longput.json", f"{SNAP}-{T}-longput.md"]
    assert T in summary_md(env)
    doc = cand_json(env)
    assert doc["strategy"] == "long_put" and doc["data_source"] == "IBKR"
    assert doc["frozen"] is True and doc["snapshot_date"] == SNAP.isoformat()
    assert "width" not in doc and "width_source" not in doc
    md = cand_md(env)
    assert "数据源 IBKR" in md and "冻结行情" in md and SNAP.isoformat() in md
    assert not (env["ledger"] / "spreads.json").exists()
    out = capsys.readouterr().out
    assert "longput-sim" in out and f"open {T}" in out


def test_suggest_has_no_width_option(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", T, "--width", "5")
    assert nothing_written(env)


def test_suggest_summary_keeps_other_tickers_of_the_same_day(env):
    assert run(env, "suggest", T) == 0
    assert run(env, "suggest", "ZQLS") == 0
    text = summary_md(env)
    assert T in text and "ZQLS" in text


def test_suggest_default_expiry_at_least_30_days(env):
    assert run(env, "suggest", T) == 0
    doc = cand_json(env)
    assert doc["expiry"] == EXPIRY and doc["days"] == 35 and doc["min_days"] == 30
    assert (date.fromisoformat(doc["expiry"]) - SNAP).days >= spread_core.LONGPUT_MIN_DAYS
    for c in doc["candidates"]:
        assert not any("时间不在你这边" in w for w in c.get("warnings", []))


def test_suggest_min_days_override_warns_short_dte(env, capsys):
    assert run(env, "suggest", T, "--min-days", "7") == 0
    doc = cand_json(env)
    assert doc["expiry"] == "2026-10-23" and doc["days"] == 7 and doc["min_days"] == 7
    assert all(c["status"] == "ok" for c in doc["candidates"])
    for c in doc["candidates"]:
        assert any("时间不在你这边" in w for w in c["warnings"])
    assert "时间不在你这边" in cand_md(env) and "时间不在你这边" in summary_md(env)
    assert "时间不在你这边" in capsys.readouterr().out


def test_suggest_min_days_30_or_more_has_no_short_dte_warning(env, capsys):
    assert run(env, "suggest", T, "--min-days", "30") == 0
    assert "时间不在你这边" not in cand_md(env)
    assert "时间不在你这边" not in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["6", "0", "-3", "x"])
def test_suggest_min_days_below_7_refused(env, bad):
    with pytest.raises(SystemExit):
        run(env, "suggest", T, "--min-days", bad)
    assert nothing_written(env)


def test_suggest_no_expiry_far_enough_writes_nothing(env, capsys):
    assert run(env, "suggest", T, "--min-days", "90") == 1
    assert "90" in capsys.readouterr().err
    assert nothing_written(env)


def test_suggest_three_buckets_with_hand_numbers(env):
    assert run(env, "suggest", T) == 0
    doc = cand_json(env)
    assert [c["delta_bucket"] for c in doc["candidates"]] == [0.50, 0.40, 0.30]
    assert [c["status"] for c in doc["candidates"]] == ["ok", "ok", "ok"]
    assert [c["put_strike"] for c in doc["candidates"]] == [100.0, 95.0, 90.0]
    c = bucket_of(doc, 0.40)
    assert c["put_mid"] == pytest.approx(2.40) and c["debit"] == pytest.approx(2.40)
    assert c["breakeven"] == pytest.approx(92.60)
    assert c["breakeven_move_pct"] == pytest.approx(-0.074)
    assert c["max_loss"] == pytest.approx(240) and c["max_profit"] == pytest.approx(9260)
    assert c["put_delta"] == pytest.approx(0.40)
    for c in doc["candidates"]:
        for key in ("width", "short_strike", "long_strike", "credit", "required_win_rate",
                    "long_put_strike", "short_put_strike"):
            assert key not in c, key
    md, summary = cand_md(env), summary_md(env)
    for text in (md, summary):
        for value in ("$2.40", "92.60", "$240", "$9,260", "-7.40%"):
            assert value in text, value


def test_suggest_strike_equal_to_spot_is_a_normal_candidate(env):
    """The 0.50 bucket lands on strike 100 = spot: breakeven move = -debit / spot."""
    assert run(env, "suggest", T) == 0
    c = bucket_of(cand_json(env), 0.50)
    assert c["status"] == "ok" and c["put_strike"] == 100.0 == c["underlying_price"]
    assert c["breakeven_move_pct"] == pytest.approx(-c["debit"] / 100.0, abs=1e-4)


def test_suggest_asks_for_at_the_money_strikes_window_free(env):
    """The default put window is strictly below spot, so the 0.50 leg (100, at the money) exists
    only through an explicit request. That request is explicit-only (window 0) and comes FIRST,
    so the default-window fetch is last (yahoo_data's stale-drop count is per last fetch)."""
    assert run(env, "suggest", T) == 0
    assert env["puts"] == [(ATM, 0, 0), ((), None, None)]
    strikes = [c["put_strike"] for c in cand_json(env)["candidates"]]
    assert [k for k in strikes if k not in WINDOW] == [100.0]


def test_suggest_no_strike_at_or_above_spot_skips_explicit_request(env):
    env["listed"] = [k for k in LISTED if k < 100.0]
    assert run(env, "suggest", T) == 0
    assert env["puts"] == [((), None, None)]
    assert bucket_of(cand_json(env), 0.40)["status"] == "ok"


def test_suggest_single_leg_spread_exactly_five_percent_passes(env):
    env["rows"] = {95.0: {"strike": 95.0, "bid": 2.4375, "ask": 2.5625, "last": 2.50,
                          "iv": None, "delta": -0.40}}     # (2.5625 - 2.4375) / 2.50 = 5%
    assert run(env, "suggest", T) == 0
    c = bucket_of(cand_json(env), 0.40)
    assert c["status"] == "ok" and c["put_strike"] == 95.0 and c["debit"] == pytest.approx(2.50)


def test_suggest_single_leg_spread_over_five_percent_rejected(env):
    env["rows"] = {95.0: {"strike": 95.0, "bid": 2.30, "ask": 2.50, "last": 2.40,
                          "iv": None, "delta": -0.40}}     # 0.20 / 2.40 = 8.3%
    assert run(env, "suggest", T) == 0
    c = bucket_of(cand_json(env), 0.40)
    assert c["status"] == "unavailable" and "8.3% 超过 5%" in c["reason"]


def test_suggest_all_buckets_rejected_still_writes_files(env):
    env["window"] = [float(k) for k in range(70, 75)]    # every delta far from the targets
    env["listed"] = list(env["window"])                  # and nothing at or above spot
    assert run(env, "suggest", T) == 0
    assert all(c["status"] == "unavailable" for c in cand_json(env)["candidates"])
    md = cand_md(env)
    assert "三档都被剔除" in md
    for c in cand_json(env)["candidates"]:
        assert c["reason"] in md and c["reason"] in summary_md(env)


def test_suggest_every_candidate_carries_theta_warning(env, capsys):
    assert run(env, "suggest", T) == 0
    doc = cand_json(env)
    for c in doc["candidates"]:
        assert longput_sim_rules.WARN_THETA_DECAY in c["warnings"]
    md, summary, out = cand_md(env), summary_md(env), capsys.readouterr().out
    w = longput_sim_rules.WARN_THETA_DECAY
    assert md.count(w) == 1 and summary.count(w) == 1 and w in out   # banner, not per bucket
    assert "Theta" in out and "向上漂移" in out


def test_suggest_earnings_flow_into_candidates(env):
    env["earnings"] = "yes"
    assert run(env, "suggest", T) == 0
    doc = cand_json(env)
    assert doc["earnings"] == "yes"
    assert any("财报" in w for w in bucket_of(doc, 0.40)["warnings"])


def test_suggest_passes_spread_kwargs_to_builder(env, monkeypatch):
    seen = []
    real = longput_sim_rules.build_longput_candidates

    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(longput_sim_rules, "build_longput_candidates", spy)
    monkeypatch.setattr(longput_sim.option_settings, "spread_kwargs",
                        lambda: {"spread_limit": 0.10, "spread_warn": 0.05})
    assert run(env, "suggest", T) == 0
    assert seen == [{"spread_limit": 0.10, "spread_warn": 0.05}]


def test_suggest_dotted_ticker_file_names(env):
    assert run(env, "suggest", "zz.q") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZ.Q-longput.json", f"{SNAP}-ZZ.Q-longput.md"]
    assert run(env, "open", "ZZ.Q", "0.40") == 0
    assert ledger(env)["spreads"][0]["suggestion_file"] == f"{SNAP}-ZZ.Q-longput.md"


def test_settings_error_is_clean_error_line(env, monkeypatch, capsys):
    def bad():
        raise ibkr_session.SettingsError("IBKR_PORT 必须是整数，当前是 'x'")
    monkeypatch.setattr(ibkr_session, "ensure_gateway", bad)
    assert run(env, "suggest", T) != 0
    err = capsys.readouterr().err
    assert err.startswith("错误：") and "IBKR_PORT" in err and "Traceback" not in err
    assert nothing_written(env)


def test_suggest_bad_ticker_refused(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", "../ZQLP")
    assert nothing_written(env)


def test_write_pair_failure_leaves_no_orphan(env, monkeypatch, tmp_path):
    def boom(doc):
        raise TypeError("not serialisable")
    monkeypatch.setattr(longput_sim, "_json_text", boom)
    with pytest.raises(TypeError):
        run(env, "suggest", T)
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
        longput_sim._write_pair(folder / "2026-10-16-ZZ.Q-longput", "# md\n", "{}\n")
    assert list(folder.iterdir()) == []


# ---- open ------------------------------------------------------------------------

def test_open_records_saved_candidate_without_refetch(env, capsys):
    assert run(env, "suggest", T) == 0
    saved = bucket_of(cand_json(env), 0.40)
    env["fail_fetch"] = True
    env["gateway"] = DOWN                           # open needs no gateway at all
    env["spot"] = 250.0                             # the market moved; ignored by open
    assert run(env, "open", T, "0.40", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    for key in longput_sim.COPIED_FIELDS:
        assert s[key] == saved[key], key
    assert s["contracts"] == 2 and s["delta_bucket"] == 0.40 and s["underlying_price"] == 100.0
    assert s["suggestion_file"] == f"{SNAP}-{T}-longput.md"


def test_open_writes_explicit_fields_and_no_spread_keys(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.40", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert set(s) == RECORD_KEYS | {"id"}
    for key in SELLER_KEYS + DISPLAY_KEYS:
        assert key not in s, key
    assert s["strategy"] == spread_ledger.STRATEGY_LONG_PUT and s["status"] == "open"
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == SNAP.isoformat()
    assert s["earnings_in_window"] is False and s["put_iv"] is None
    assert (s["put_strike"], s["put_mid"], s["debit"], s["breakeven"]) == (95.0, 2.40, 2.40, 92.6)
    assert s["breakeven_move_pct"] == -0.074 and s["put_delta"] == 0.40
    for key in ("exit_date", "exit_value", "exit_underlying", "pnl", "result",
                "fraction_of_debit"):
        assert s[key] is None, key
    assert spread_ledger.strategy_of(s) == "long_put"
    out = capsys.readouterr().out
    for value in ("$2.40", "92.60", "-7.40%", "$480", "$18,520", "买入 Put", "ZQLP 95",
                  "无所需胜率"):
        assert value in out, value
    assert longput_sim_rules.WARN_THETA_DECAY in out


def test_copied_fields_are_an_explicit_tuple():
    assert isinstance(longput_sim.COPIED_FIELDS, tuple)
    assert set(longput_sim.COPIED_FIELDS) | {
        "strategy", "ticker", "contracts", "expiry", "snapshot_date", "status",
        "suggestion_file", "earnings_in_window", "exit_date", "exit_value", "exit_underlying",
        "pnl", "result", "fraction_of_debit"} == RECORD_KEYS


@pytest.mark.parametrize("field", ["debit", "breakeven"])
def test_open_debit_tolerance_shared_constant(env, field, monkeypatch, capsys):
    """Rules round the mid, debit and breakeven to 4 places each; a 0.5e-4 drift is rounding,
    1.5e-4 is a damaged file. The bound is spread_core.LONGPUT_ROUNDING_TOL (1e-4)."""
    assert spread_core.LONGPUT_ROUNDING_TOL == 1e-4
    base = {"debit": 2.40, "breakeven": 92.60}

    def saved(drift):
        over = dict(base)
        over[field] = base[field] + drift
        if field == "debit":                          # keep breakeven consistent with debit
            over["breakeven"] = 95.0 - over["debit"]
        write_saved(env, candidates=[hand_candidate(**over)])

    saved(0.5e-4)
    assert run(env, "open", T, "0.40") == 0
    (env["ledger"] / "spreads.json").unlink()
    saved(1.5e-4)
    assert run(env, "open", T, "0.40") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    monkeypatch.setattr(spread_core, "LONGPUT_ROUNDING_TOL", 2e-4)   # read live, not copied
    assert run(env, "open", T, "0.40") == 0


@pytest.mark.parametrize("case", ["bad_json", "wrong_strategy", "wrong_version",
                                  "ticker_mismatch", "missing_key", "bad_number", "bad_date",
                                  "bad_bucket", "empty", "expired", "bad_iv"])
def test_open_rejects_damaged_expired_or_mismatched_candidate(env, case, capsys):
    path = write_saved(env)
    today = SNAP
    if case == "bad_json":
        path.write_text("{not json", encoding="utf-8")
    elif case == "wrong_strategy":
        write_saved(env, strategy="bear_put_spread")
    elif case == "wrong_version":
        write_saved(env, version=2)
    elif case == "ticker_mismatch":
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**doc, "ticker": "ZQLS"}), encoding="utf-8")
    elif case == "missing_key":
        write_saved(env, candidates=[{k: v for k, v in hand_candidate().items() if k != "debit"}])
    elif case == "bad_number":
        write_saved(env, candidates=[hand_candidate(put_mid="2.40")])
    elif case == "bad_date":
        write_saved(env, expiry="2026-13-40")
    elif case == "bad_bucket":
        write_saved(env, candidates=[hand_candidate(delta_bucket=0.60)])
    elif case == "empty":
        write_saved(env, candidates=[])
    elif case == "bad_iv":
        write_saved(env, candidates=[hand_candidate(put_iv="high")])
    else:
        today = date.fromisoformat(EXPIRY)
    assert run(env, "open", T, "0.40", today=today) == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("over", [
    {"put_mid": 0.0, "debit": 0.0, "breakeven": 95.0},                # debit <= 0
    {"put_mid": 95.0, "debit": 95.0, "breakeven": 0.0},               # debit >= strike
    {"put_mid": 2.50},                                                # debit != put mid
    {"breakeven": 92.0},                                              # breakeven != K - debit
    {"breakeven": 97.4},                                              # K + debit (call side)
    {"put_strike": -5.0},                                             # nonsense strike
])
def test_open_rejects_invalid_structure(env, over, capsys):
    write_saved(env, candidates=[hand_candidate(**over)])
    assert run(env, "open", T, "0.40") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_without_saved_candidate_refuses(env, capsys):
    assert run(env, "open", T, "0.40") == 1
    assert f"没有 {T} 的已保存 买入 Put 候选" in capsys.readouterr().err


def test_open_rejects_unavailable_bucket(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.30") == 1
    assert "8.0% 超过 5%" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_bucket_missing_from_file(env, capsys):
    write_saved(env, candidates=[hand_candidate()])
    assert run(env, "open", T, "0.50") == 1
    assert "0.50" in capsys.readouterr().err


@pytest.mark.parametrize("bucket", ["0.60", "0.15", "0.35"])
def test_open_rejects_bucket_outside_choices(env, bucket):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", T, bucket)


def test_open_requires_bucket(env):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", T)


@pytest.mark.parametrize("flag", [("--width", "5"), ("--from-pick",)])
def test_open_has_no_width_or_from_pick(env, flag):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", T, "0.40", *flag)
    assert not (env["ledger"] / "spreads.json").exists()


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
    assert run(env, "open", T, "0.40") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_second_bucket_same_expiry_blocked(env, capsys):
    write_saved(env)
    assert run(env, "open", T, "0.40") == 0
    assert run(env, "open", T, "0.50") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_uses_latest_saved_longput_file(env):
    write_saved(env, stamp=date(2026, 10, 9), candidates=[hand_candidate(
        put_mid=2.50, debit=2.50, breakeven=92.5)])
    write_saved(env)
    assert run(env, "open", T, "0.40") == 0
    assert ledger(env)["spreads"][0]["debit"] == pytest.approx(2.40)


# ---- isolation from the other loaders --------------------------------------------

OTHER_PATTERNS = (spread_sim.SUGGESTION_FILE, put_screen.SUGGESTION_FILE,
                  put_screen.SUMMARY_FILE, call_screen.SUGGESTION_FILE,
                  call_screen.SUMMARY_FILE, collar_screen.SUGGESTION_FILE,
                  condor_sim.SUGGESTION_FILE, bullcall_sim.SUGGESTION_FILE,
                  rr_sim.SUGGESTION_FILE, bearcall_sim.SUGGESTION_FILE,
                  bearput_sim.SUGGESTION_FILE)


def test_longput_files_not_picked_up_by_other_loaders(env, capsys):
    assert run(env, "suggest", T) == 0
    for p in sug_dir(env).iterdir():
        for pattern in OTHER_PATTERNS:
            assert pattern.match(p.name) is None, (pattern.pattern, p.name)
    assert spread_sim._load_suggestion(env["ledger"], T) is None
    with pytest.raises(spread_ledger.LedgerError, match="没有 ZQLP 的已保存铁鹰候选"):
        condor_sim._load_candidate(env["ledger"], T)
    with pytest.raises(spread_ledger.LedgerError, match="Call 借方价差候选"):
        bullcall_sim._load_candidate(env["ledger"], T)
    with pytest.raises(spread_ledger.LedgerError, match="Call 贷方价差候选"):
        bearcall_sim._load_candidate(env["ledger"], T)
    with pytest.raises(spread_ledger.LedgerError, match="Put 借方价差候选"):
        bearput_sim._load_candidate(env["ledger"], T)
    code = spread_sim.main(["--ledger-dir", str(env["ledger"]), "open", T, "0.15"], today=SNAP)
    assert code == 1 and not (env["ledger"] / "spreads.json").exists()

    # and the other way round: none of their files is a long put candidate
    for p in list(sug_dir(env).iterdir()):
        p.unlink()
    others = [f"{SNAP}-{T}.json", f"{SNAP}-{T}-put.json", f"{SNAP}-{T}-call-real.json",
              f"{SNAP}-{T}-collar-lot1.json", f"{SNAP}-{T}-condor.json",
              f"{SNAP}-{T}-bullcall.json", f"{SNAP}-{T}-rr.json",
              f"{SNAP}-{T}-bearcall.json", f"{SNAP}-{T}-bearput.json",
              f"{SNAP}-{T}-longput.json.tmp", f"{SNAP}-{T}-LONGPUT.json"]
    for name in others:
        (sug_dir(env) / name).write_text("{}", encoding="utf-8")
        assert longput_sim.SUGGESTION_FILE.match(name) is None, name
    capsys.readouterr()
    assert run(env, "open", T, "0.40") == 1
    assert f"没有 {T} 的已保存 买入 Put 候选" in capsys.readouterr().err
    assert longput_sim._day_docs(sug_dir(env), SNAP.isoformat()) == ([], [])


def test_summary_lists_damaged_same_day_file(env):
    sug_dir(env).mkdir(parents=True)
    (sug_dir(env) / f"{SNAP}-ZQLS-longput.json").write_text("{bad", encoding="utf-8")
    assert run(env, "suggest", T) == 0
    text = summary_md(env)
    assert f"{SNAP}-ZQLS-longput.json" in text and T in text


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


def test_longput_sim_never_writes_positions_md(env, monkeypatch):
    written = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    assert run(env, "suggest", T) == 0
    assert run(env, "open", T, "0.40") == 0
    assert written and "positions.md" not in written
    assert not any(p.name == "positions.md" for p in env["ledger"].rglob("*"))


def test_longput_sim_imports_no_broker_or_sibling_modules():
    source = (REPO_ROOT / "scripts" / "longput_sim.py").read_text(encoding="utf-8")
    source += (REPO_ROOT / "scripts" / "longput_sim_render.py").read_text(encoding="utf-8")
    for name in ("broker_", "placeOrder", "ib_async", "with_suffix", "bullcall_sim",
                 "BULLCALL_", "bearput_sim", "BEARPUT_", "bearcall_sim "):
        assert name not in source, name
    assert "import bearcall_sim\n" not in source


# ---- end to end: the opened record is consumed by spread_sim / report / review ---

MID_LIFE = date(2026, 11, 2)
AFTER = date(2026, 11, 23)


def _open_hand_record(env):
    assert run(env, "suggest", T) == 0
    assert run(env, "open", T, "0.40") == 0
    env["puts"].clear()


def test_open_then_spread_sim_status_close_and_report(env, capsys):
    _open_hand_record(env)
    env["mids"] = {95.0: 3.00}                      # value 3.00 -> +$60
    assert run_spread(env, "status", today=MID_LIFE) == 0
    out = capsys.readouterr().out
    assert "95 买入 Put" in out and "+$60" in out
    assert [call[0] for call in env["puts"]] == [(95.0,)]   # explicit strikes=(put_strike,)
    report = (env["ledger"] / "report.md").read_text(encoding="utf-8")
    assert "买入 Put" in report and "95" in report
    assert "买入 Put" in (env["ledger"] / "report.html").read_text(encoding="utf-8")
    assert run(env, "close", "1", today=MID_LIFE) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(60.0)
    assert s["exit_value"] == pytest.approx(3.00) and s["result"] == "win"
    assert s["fraction_of_debit"] == pytest.approx(0.25)
    for key in ("exit_cost", "credit", "short_strike", "long_strike", "breached_side",
                "width", "fraction_of_max_captured"):
        assert key not in s, key


def test_close_without_quote_is_refused(env, capsys):
    _open_hand_record(env)
    env["listed"] = [k for k in LISTED if k != 95.0]          # no quote for the put
    env["window"] = [k for k in WINDOW if k != 95.0]
    assert run(env, "close", "1", today=MID_LIFE) != 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "open" and s["pnl"] is None


@pytest.mark.parametrize("close,value,pnl,result,fraction", [
    (100.0, 0.0, -240.0, "loss", -1.0),
    (95.0, 0.0, -240.0, "loss", -1.0),            # C = strike
    (92.6, 2.4, 0.0, "loss", 0.0),                # C = breakeven: 0.0, never -0.0
    (90.0, 5.0, 260.0, "win", 1.0833),
    (80.0, 15.0, 1260.0, "win", 5.25)])           # no upper clamp
def test_open_then_spread_sim_settles_hand_table(env, close, value, pnl, result, fraction):
    _open_hand_record(env)
    env["close"] = close
    assert run_spread(env, "status", today=AFTER) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "expired" and s["exit_underlying"] == close
    assert s["exit_value"] == pytest.approx(value) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_debit"] == fraction
    assert str(s["pnl"]) != "-0.0" and str(s["fraction_of_debit"]) != "-0.0"


def test_open_then_option_review_measures_the_record(env):
    _open_hand_record(env)
    env["mids"] = {95.0: 3.00}
    (s,) = ledger(env)["spreads"]
    m = put_review.gather_metrics(s, MID_LIFE)
    assert m["error"] is None and m["strategy"] == "long_put"
    assert m["value"] == pytest.approx(3.00) and m["pnl"] == pytest.approx(60.0)
    assert m["put_strike"] == 95.0 and m["captured"] == pytest.approx(0.25)
    assert [call[0] for call in env["puts"]] == [(95.0,)]
    verdict = put_review._review(m)
    assert verdict["rule"] == 5 and verdict["advice"] == "hold"


def test_longput_sim_never_calls_foreign_helpers(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("seller / bear put helper used for a long put")
    for name in FOREIGN_HELPERS:
        monkeypatch.setattr(spread_core, name, boom)
    _open_hand_record(env)
    env["mids"] = {95.0: 3.00}
    assert run_spread(env, "status", today=MID_LIFE) == 0
    assert run(env, "close", "1", today=MID_LIFE) == 0
    assert ledger(env)["spreads"][0]["pnl"] == pytest.approx(60.0)
    env["ledger"], env["mids"] = env["ledger"].parent / "settle", {}
    _open_hand_record(env)
    env["close"] = 90.0
    assert run(env, "status", today=AFTER) == 0
    assert ledger(env)["spreads"][0]["pnl"] == pytest.approx(260.0)
