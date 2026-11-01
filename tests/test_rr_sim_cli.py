"""CLI tests for scripts/rr_sim.py. The IBKR gateway and all market data are faked.

Invented ticker ZZZQ; paper-trading numbers only.

Hand-computed example (design section 8), used by the chain fixture and the `open` tests:
  spot 100, sell 90 Put mid 2.00, buy 110 Call mid 1.50, 1 contract.
  net credit 0.50/share (净收入); capital (90 - 0.50) x 100 = $8,950 (conservative cash basis);
  downside breakeven 89.50; dead zone 90 - 110; put move -10.00%, call move +10.00%.
  Net debit example: Put 1.20 / Call 1.80 -> net credit -0.60, capital $9,060, upside breakeven
  110.60, "Call 太贵" warning (0.60 > 1.20 x 0.25).
  Settlement at 120: (10 + 0.50) x 100 = +$1,050, win, zone up.

Chain fixture: spot 100. Puts 78-99; delta -0.30 at 90 (mid 2.00), -0.20 at 85 (mid 1.00),
-0.15 at 80 (mid 0.50), -0.45 above 90, -0.05 elsewhere. Calls priced 105 3.00, 110 1.50,
112 1.00, 115 0.50, 120 0.20; the default call window is 105-110, so the 0.20 / 0.15 buckets
need the explicit strike request above 110 (112 and 115). bid/ask = mid -/+ 0.01.
"""
from datetime import date
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bullcall_sim
import call_screen
import collar_screen
import condor_sim
import ibkr_session
import put_screen
import rr_sim
import rr_sim_rules
import spread_core
import spread_data
import spread_ledger
import spread_sim

SNAP = date(2026, 10, 16)
EXPIRY = "2026-11-20"          # 35 days after SNAP, inside the 30-60 day window
EXPIRIES = ["2026-10-23", "2026-11-13", EXPIRY, "2026-12-18"]
DOWN = ibkr_session.GatewayUnavailable("IBKR 网关不可达：gw.test:14999")
PUT_STRIKES = [float(k) for k in range(78, 100)]
CALL_WINDOW = [105.0, 110.0]
LISTED = [float(k) for k in range(75, 126)]
CALL_MIDS = {105.0: 3.00, 110.0: 1.50, 112.0: 1.00, 115.0: 0.50, 120.0: 0.20}
PUT_MIDS = {90.0: 2.00, 85.0: 1.00, 80.0: 0.50}
PUT_DELTAS = {90.0: -0.30, 85.0: -0.20, 80.0: -0.15}
RECORD_KEYS = {
    "strategy", "ticker", "short_put_strike", "long_call_strike", "short_put_mid",
    "long_call_mid", "net_credit", "breakeven_down", "put_move_pct", "call_move_pct",
    "delta_bucket", "short_put_delta", "long_call_delta", "short_put_iv", "long_call_iv",
    "underlying_price", "contracts", "expiry", "snapshot_date", "status", "suggestion_file",
    "earnings_in_window",
    # settlement placeholders (filled by spread_sim status / close)
    "exit_date", "exit_underlying", "pnl", "result", "outcome_zone",
}
FOREIGN_KEYS = ("credit", "short_strike", "long_strike", "width", "debit", "lot_ref", "breakeven",
                "short_mid", "long_mid", "exit_cost", "exit_value", "fraction_of_max_captured",
                "long_call_strike_x", "short_call_strike")
DISPLAY_KEYS = ("capital_basis", "upside_breakeven", "dead_zone", "days", "warnings", "reason")
FIXED = rr_sim_rules.FIXED_WARNINGS


def _row(k, mid, delta):
    return {"strike": k, "bid": round(mid - 0.01, 4), "ask": round(mid + 0.01, 4), "last": mid,
            "iv": None, "delta": delta}


def _put_delta(k):
    return PUT_DELTAS.get(k, -0.45 if k > 90 else -0.05)


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"gateway": None, "spot": 100.0, "calls": [], "puts": [], "listed": list(LISTED),
         "put_window": list(PUT_STRIKES), "call_window": list(CALL_WINDOW),
         "call_mids": dict(CALL_MIDS), "put_mids": {}, "earnings": "no", "ex_div": None,
         "expirations": list(EXPIRIES)}
    monkeypatch.setattr(ibkr_session, "ensure_gateway",
                        lambda: (_ for _ in ()).throw(m["gateway"]) if m["gateway"] else None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, m["spot"]))
    monkeypatch.setattr(spread_data, "expirations", lambda t: list(m["expirations"]))
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: list(m["listed"]))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    monkeypatch.setattr(spread_data, "monday_gap_pct", lambda *a, **k: None)

    def put_mid(k):
        return m["put_mids"].get(k, PUT_MIDS.get(k, 3.00))

    def fetch_puts(t, e, strikes=(), window_pct=None, window_limit=None):
        m["puts"].append(tuple(strikes))
        wanted = sorted((set(m["put_window"]) | set(strikes)) & set(PUT_STRIKES))
        return [_row(k, put_mid(k), _put_delta(k)) for k in wanted]

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        m["calls"].append(tuple(strikes))
        wanted = sorted((set(m["call_window"]) | set(strikes)) & set(m["call_mids"]))
        return [_row(k, m["call_mids"][k], 0.25) for k in wanted]

    monkeypatch.setattr(spread_data, "fetch_puts", fetch_puts)
    monkeypatch.setattr(spread_data, "fetch_calls", fetch_calls)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=SNAP):
    return rr_sim.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug_dir(env):
    return env["ledger"] / "suggestions"


def cand_json(env, ticker="ZZZQ", stamp=SNAP):
    return json.loads((sug_dir(env) / f"{stamp}-{ticker}-rr.json").read_text(encoding="utf-8"))


def cand_md(env, ticker="ZZZQ", stamp=SNAP):
    return (sug_dir(env) / f"{stamp}-{ticker}-rr.md").read_text(encoding="utf-8")


def summary_md(env, stamp=SNAP):
    return (env["ledger"] / "rr-sim" / f"{stamp}.md").read_text(encoding="utf-8")


def bucket_of(doc, b):
    return next(c for c in doc["candidates"] if c["delta_bucket"] == b)


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def nothing_written(env):
    return not env["ledger"].exists() or not list(env["ledger"].rglob("*"))


def hand_candidate(**over):
    c = {"delta_bucket": 0.30, "status": "ok",
         "short_put_strike": 90.0, "long_call_strike": 110.0, "short_put_mid": 2.0,
         "long_call_mid": 1.5, "net_credit": 0.5, "breakeven_down": 89.5,
         "put_move_pct": -0.1, "call_move_pct": 0.1, "capital_basis": 8950.0,
         "upside_breakeven": None, "dead_zone": [90.0, 110.0], "short_put_delta": 0.30,
         "long_call_delta": 0.25, "short_put_iv": 0.40, "long_call_iv": None,
         "underlying_price": 100.0, "expiry": EXPIRY, "days": 35, "earnings_in_window": False,
         "warnings": list(FIXED)}
    c.update(over)
    return c


def write_saved(env, candidates=None, stamp=SNAP, ticker="ZZZQ", **over):
    """A saved candidate file as suggest writes it (hand numbers, see module docstring)."""
    doc = {"version": 1, "strategy": "risk_reversal", "generated_on": stamp.isoformat(),
           "data_source": "IBKR", "frozen": True, "intraday": False, "ticker": ticker,
           "snapshot_date": stamp.isoformat(), "spot": 100.0, "expiry": EXPIRY, "days": 35,
           "min_days": 30, "earnings": "no", "ex_dividend": None,
           "candidates": candidates if candidates is not None else [
               hand_candidate(),
               hand_candidate(delta_bucket=0.20, short_put_strike=85.0, short_put_mid=1.0,
                              long_call_strike=112.0, long_call_mid=1.0, net_credit=0.0,
                              breakeven_down=85.0, put_move_pct=-0.15, call_move_pct=0.12,
                              capital_basis=8500.0, dead_zone=[85.0, 112.0]),
               {"delta_bucket": 0.15, "status": "unavailable",
                "reason": "短 Put 最近的 Delta 0.05 离目标 0.15 超过 0.05（行权价 80）"}]}
    doc.update(over)
    sug_dir(env).mkdir(parents=True, exist_ok=True)
    path = sug_dir(env) / f"{stamp}-{ticker}-rr.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (sug_dir(env) / f"{stamp}-{ticker}-rr.md").write_text("# saved\n", encoding="utf-8")
    return path


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest", "ZZZQ") != 0
    assert "gw.test:14999" in capsys.readouterr().err
    assert nothing_written(env)
    assert env["calls"] == [] and env["puts"] == []     # no market read after the gate fails


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch):
    def boom(t, s, e):
        raise DOWN
    monkeypatch.setattr(spread_data, "earnings_status", boom)   # the last network read
    assert run(env, "suggest", "ZZZQ") != 0
    assert nothing_written(env)


def test_suggest_writes_summary_and_candidate_files(env, capsys):
    assert run(env, "suggest", "ZZZQ") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZZQ-rr.json", f"{SNAP}-ZZZQ-rr.md"]
    assert "ZZZQ" in summary_md(env)
    doc = cand_json(env)
    assert doc["strategy"] == "risk_reversal" and doc["data_source"] == "IBKR"
    assert doc["frozen"] is True and doc["snapshot_date"] == SNAP.isoformat()
    assert doc["expiry"] == EXPIRY and doc["days"] == 35 and doc["min_days"] == 30
    md = cand_md(env)
    assert "数据源 IBKR" in md and "冻结行情" in md and SNAP.isoformat() in md
    assert not (env["ledger"] / "spreads.json").exists()
    out = capsys.readouterr().out
    assert "open ZZZQ" in out
    for warning in FIXED:
        assert warning in out


def test_suggest_summary_keeps_other_tickers_of_the_same_day(env):
    assert run(env, "suggest", "ZZZQ") == 0
    assert run(env, "suggest", "ZZZR") == 0
    text = summary_md(env)
    assert "ZZZQ" in text and "ZZZR" in text


def test_suggest_hand_numbers_in_json_md_and_summary(env):
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    assert [c["delta_bucket"] for c in doc["candidates"]] == [0.30, 0.20, 0.15]
    assert [c["status"] for c in doc["candidates"]] == ["ok", "ok", "ok"]
    c = bucket_of(doc, 0.30)
    assert (c["short_put_strike"], c["long_call_strike"]) == (90.0, 110.0)
    assert (c["short_put_mid"], c["long_call_mid"]) == (2.0, 1.5)
    assert c["net_credit"] == pytest.approx(0.50) and c["breakeven_down"] == pytest.approx(89.50)
    assert c["capital_basis"] == pytest.approx(8950) and c["dead_zone"] == [90.0, 110.0]
    assert c["put_move_pct"] == pytest.approx(-0.10) and c["call_move_pct"] == pytest.approx(0.10)
    assert c["upside_breakeven"] is None
    for text in (cand_md(env), summary_md(env)):
        for value in ("净收入 $0.50/股", "$8,950", "现金担保口径，券商实际保证金因家而异",
                      "89.50", "90 ～ 110", "-10.00%", "+10.00%", "Call 行权价以下已不亏"):
            assert value in text, value
        for warning in FIXED:
            assert warning in text
    md = cand_md(env)
    assert "最大收益 无上限" in md and "所需胜率" not in md
    assert "卖 Put 90（中间价 $2.00" in md and "买 Call 110（中间价 $1.50" in md


def test_suggest_long_call_beyond_default_window_requested_by_strike(env):
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    assert [(c["short_put_strike"], c["long_call_strike"]) for c in doc["candidates"]] == [
        (90.0, 110.0), (85.0, 112.0), (80.0, 115.0)]
    assert [c["net_credit"] for c in doc["candidates"]] == [0.5, 0.0, 0.0]
    assert env["calls"][0] == ()                         # first read: the default window
    asked = {k for call in env["calls"] for k in call}
    assert {112.0, 115.0} <= asked
    assert len(env["calls"]) <= 1 + rr_sim.CALL_ROUNDS   # bounded re-request
    assert all(k <= 100.0 * (1 + rr_sim.CALL_REACH) for k in asked)


def test_suggest_net_debit_example(env):
    env["put_mids"] = {90.0: 1.20}
    env["call_mids"] = {105.0: 4.00, 110.0: 1.80}
    assert run(env, "suggest", "ZZZQ") == 0
    c = bucket_of(cand_json(env), 0.30)
    assert c["net_credit"] == pytest.approx(-0.60) and c["capital_basis"] == pytest.approx(9060)
    assert c["upside_breakeven"] == pytest.approx(110.60)
    assert any("Call 太贵" in w for w in c["warnings"])
    for text in (cand_md(env), summary_md(env)):
        assert "净支出 $0.60/股" in text and "$9,060" in text and "110.60" in text
        assert "Call 太贵，净支出偏大" in text


def test_suggest_min_days_default_and_override(env):
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    assert doc["expiry"] == EXPIRY and doc["days"] == 35 and doc["min_days"] == 30
    assert run(env, "suggest", "ZZZQ", "--min-days", "7") == 0
    doc = cand_json(env)
    assert doc["expiry"] == "2026-10-23" and doc["days"] == 7 and doc["min_days"] == 7


def test_suggest_min_days_below_7_refused(env):
    with pytest.raises(SystemExit):
        run(env, "suggest", "ZZZQ", "--min-days", "6")
    assert nothing_written(env)


def test_suggest_no_expiry_in_window_rejects_all_but_writes_files(env, capsys):
    assert run(env, "suggest", "ZZZQ", "--min-days", "90") == 0
    doc = cand_json(env)
    assert doc["expiry"] is None and doc["days"] is None
    reason = rr_sim_rules.no_expiry_reason(90)
    assert [c["status"] for c in doc["candidates"]] == ["unavailable"] * 3
    assert all(c["reason"] == reason for c in doc["candidates"])
    for text in (cand_md(env), summary_md(env)):
        assert "没有 90–60 天内的到期日" in text
    assert "三档都被剔除" in cand_md(env)
    assert env["puts"] == [] and env["calls"] == []     # no chain read without an expiry
    capsys.readouterr()
    assert run(env, "open", "ZZZQ", "0.30") == 1
    assert "没有 90–60 天内的到期日" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_suggest_all_buckets_rejected_still_writes_files(env):
    env["put_window"] = []                              # no put quotes at all
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    assert all(c["status"] == "unavailable" for c in doc["candidates"])
    assert "三档都被剔除" in cand_md(env)
    assert "无可用的虚值 Put" in summary_md(env)


def test_suggest_rejected_bucket_lists_reason(env):
    env["put_window"] = [k for k in PUT_STRIKES if k not in (80.0, 85.0)]
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    rejected = bucket_of(doc, 0.15)
    assert rejected["status"] == "unavailable" and "超过" in rejected["reason"]
    assert bucket_of(doc, 0.20)["status"] == "unavailable"
    assert bucket_of(doc, 0.30)["status"] == "ok"
    for text in (cand_md(env), summary_md(env)):
        assert rejected["reason"] in text


def test_suggest_wide_leg_rejects_bucket_with_reason(env):
    env["put_mids"] = {90.0: 2.00}
    env["call_mids"] = {105.0: 3.00, 110.0: 1.50}
    env["call_window"] = []                              # no OTM call quotes
    assert run(env, "suggest", "ZZZQ") == 0
    c = bucket_of(cand_json(env), 0.30)
    assert c["status"] == "unavailable" and "没有可用的虚值 Call" in c["reason"]


def test_suggest_earnings_and_ex_dividend_flow_into_candidates(env):
    env["earnings"], env["ex_div"] = "yes", date(2026, 11, 2)
    assert run(env, "suggest", "ZZZQ") == 0
    doc = cand_json(env)
    assert doc["earnings"] == "yes" and doc["ex_dividend"] == "2026-11-02"
    c = bucket_of(doc, 0.30)
    assert any("财报" in w for w in c["warnings"])
    assert any("除息日" in w for w in c["warnings"])
    assert c["earnings_in_window"] is True


def test_suggest_dotted_ticker_file_names(env):
    assert run(env, "suggest", "zz.q") == 0
    names = sorted(p.name for p in sug_dir(env).iterdir())
    assert names == [f"{SNAP}-ZZ.Q-rr.json", f"{SNAP}-ZZ.Q-rr.md"]
    assert run(env, "open", "ZZ.Q", "0.30") == 0
    assert ledger(env)["spreads"][0]["suggestion_file"] == f"{SNAP}-ZZ.Q-rr.md"


def test_settings_error_is_clean_error_line(env, monkeypatch, capsys):
    def bad():
        raise ibkr_session.SettingsError("IBKR_PORT 必须是整数，当前是 'x'")
    monkeypatch.setattr(ibkr_session, "ensure_gateway", bad)
    assert run(env, "suggest", "ZZZQ") != 0
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
    monkeypatch.setattr(rr_sim, "_json_text", boom)
    with pytest.raises(TypeError):
        run(env, "suggest", "ZZZQ")
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
        rr_sim._write_pair(folder / "2026-10-16-ZZ.Q-rr", "# md\n", "{}\n")
    assert list(folder.iterdir()) == []


# ---- open ------------------------------------------------------------------------

def _forbid_market(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("open must not touch the market")
    for name in ("last_close", "expirations", "chain_strikes", "fetch_puts", "fetch_calls",
                 "earnings_status", "ex_dividend_in_window", "ensure_source", "close_on"):
        monkeypatch.setattr(spread_data, name, boom)


def test_open_records_saved_candidate_without_touching_the_market(env, monkeypatch, capsys):
    assert run(env, "suggest", "ZZZQ") == 0
    saved = bucket_of(cand_json(env), 0.30)
    env["gateway"] = DOWN                               # open needs no gateway at all
    _forbid_market(monkeypatch)
    assert run(env, "open", "ZZZQ", "0.30", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    for key in rr_sim.COPIED_FIELDS:
        assert s[key] == saved[key], key
    assert s["contracts"] == 2 and s["delta_bucket"] == 0.30 and s["underlying_price"] == 100.0
    assert s["suggestion_file"] == f"{SNAP}-ZZZQ-rr.md"


def test_open_writes_explicit_fields_and_prints_readback(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.30", "--contracts", "2") == 0
    (s,) = ledger(env)["spreads"]
    assert set(s) == RECORD_KEYS | {"id"}
    for key in FOREIGN_KEYS + DISPLAY_KEYS:
        assert key not in s, key
    assert s["strategy"] == spread_ledger.STRATEGY_RR == "risk_reversal" and s["status"] == "open"
    assert s["expiry"] == EXPIRY and s["snapshot_date"] == SNAP.isoformat()
    assert s["earnings_in_window"] is False and s["long_call_iv"] is None
    for key in ("exit_date", "exit_underlying", "pnl", "result", "outcome_zone"):
        assert s[key] is None, key
    assert spread_ledger.strategy_of(s) == "risk_reversal"
    out = capsys.readouterr().out
    for value in ("90 Put 卖 / 110 Call 买", "净收入 $0.50/股", "$17,900",
                  "现金担保口径，券商实际保证金因家而异", "89.50", "90 ～ 110", "2 张", "无上限"):
        assert value in out, value
    for warning in FIXED:
        assert warning in out


def test_open_net_debit_readback_says_net_debit(env, capsys):
    write_saved(env, candidates=[hand_candidate(
        short_put_mid=1.2, long_call_mid=1.8, net_credit=-0.6, breakeven_down=90.6,
        capital_basis=9060.0, upside_breakeven=110.6)])
    assert run(env, "open", "ZZZQ", "0.30") == 0
    out = capsys.readouterr().out
    assert "净支出 $0.60/股" in out and "$9,060" in out and "90.60" in out


def test_copied_fields_are_an_explicit_tuple():
    assert isinstance(rr_sim.COPIED_FIELDS, tuple)
    assert set(rr_sim.COPIED_FIELDS) | {
        "strategy", "ticker", "contracts", "expiry", "snapshot_date", "status",
        "suggestion_file", "earnings_in_window", *rr_sim.SETTLEMENT_FIELDS} == RECORD_KEYS


@pytest.mark.parametrize("field", ["net_credit", "breakeven_down"])
def test_open_rounding_tolerance_shared_constant(env, field, monkeypatch, capsys):
    """Rules round mids, net and breakeven to 4 places each; a 0.5e-4 drift is rounding,
    1.5e-4 is a damaged file. The bound is spread_core.RR_ROUNDING_TOL (1e-4), read live."""
    assert spread_core.RR_ROUNDING_TOL == 1e-4

    def saved(drift):
        base = {"net_credit": 0.5, "breakeven_down": 89.5}
        base[field] += drift
        write_saved(env, candidates=[hand_candidate(**base)])

    saved(0.5e-4)
    assert run(env, "open", "ZZZQ", "0.30") == 0
    (env["ledger"] / "spreads.json").unlink()
    saved(1.5e-4)
    assert run(env, "open", "ZZZQ", "0.30") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()
    monkeypatch.setattr(spread_core, "RR_ROUNDING_TOL", 2e-4)       # read live, not copied
    assert run(env, "open", "ZZZQ", "0.30") == 0


def test_suggest_output_passes_open_validation(env):
    """suggest and open share RR_ROUNDING_TOL: every candidate suggest wrote must open."""
    assert run(env, "suggest", "ZZZQ") == 0
    for c in cand_json(env)["candidates"]:
        assert rr_sim_rules.validate_candidate(c) is None


@pytest.mark.parametrize("case", ["bad_json", "wrong_strategy", "wrong_version",
                                  "ticker_mismatch", "missing_key", "bad_number", "bad_date",
                                  "bad_bucket", "empty", "expired", "null_expiry_with_ok"])
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
        write_saved(env, candidates=[{k: v for k, v in hand_candidate().items()
                                      if k != "net_credit"}])
    elif case == "bad_number":
        write_saved(env, candidates=[hand_candidate(long_call_mid="1.50")])
    elif case == "bad_date":
        write_saved(env, expiry="2026-13-40")
    elif case == "bad_bucket":
        write_saved(env, candidates=[hand_candidate(delta_bucket=0.60)])
    elif case == "empty":
        write_saved(env, candidates=[])
    elif case == "null_expiry_with_ok":
        write_saved(env, expiry=None)
    else:
        today = date.fromisoformat(EXPIRY)
    assert run(env, "open", "ZZZQ", "0.30", today=today) == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("over", [
    {"short_put_strike": 101.0, "breakeven_down": 100.5},            # put not below spot
    {"long_call_strike": 99.0},                                      # call not above spot
    {"short_put_mid": 0.0, "net_credit": -1.5, "breakeven_down": 91.5},
    {"long_call_mid": 1.2},                                          # net != put - call
    {"breakeven_down": 90.0},                                        # breakeven != put - net
])
def test_open_rejects_invalid_structure(env, over, capsys):
    write_saved(env, candidates=[hand_candidate(**over)])
    assert run(env, "open", "ZZZQ", "0.30") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_without_saved_candidate_refuses(env, capsys):
    assert run(env, "open", "ZZZQ", "0.30") == 1
    assert "没有 ZZZQ 的已保存风险反转候选" in capsys.readouterr().err


def test_open_rejects_unavailable_bucket(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.15") == 1
    assert "离目标 0.15 超过" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_rejects_bucket_missing_from_file(env, capsys):
    write_saved(env, candidates=[hand_candidate()])
    assert run(env, "open", "ZZZQ", "0.20") == 1
    assert "0.20" in capsys.readouterr().err


def test_open_rejects_bucket_outside_choices(env):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", "ZZZQ", "0.40")


def test_open_rejects_fewer_than_one_contract(env, capsys):
    write_saved(env)
    with pytest.raises(SystemExit):
        run(env, "open", "ZZZQ", "0.30", "--contracts", "0")
    doc = rr_sim._load_candidate(env["ledger"], "ZZZQ")
    with pytest.raises(spread_ledger.LedgerError, match="张数"):
        rr_sim._check_open_args(doc, 0, SNAP)
    assert not (env["ledger"] / "spreads.json").exists()


@pytest.mark.parametrize("other", [
    {},                                                               # bull put (no strategy key)
    {"strategy": "iron_condor"},
    {"strategy": "cash_secured_put"},
    {"strategy": "covered_call", "lot_ref": {"source": "paper", "lot_id": 1, "ticker": "ZZZQ"}},
    {"strategy": "bull_call_spread"},
    {"strategy": "risk_reversal"},
])
def test_open_rejects_duplicate_ticker_expiry_any_strategy(env, other, capsys):
    led = spread_ledger.empty_ledger()
    led, _ = spread_ledger.add_spread(led, {"ticker": "ZZZQ", "expiry": EXPIRY,
                                            "status": "open", "contracts": 1, **other})
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json", led)
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.30") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_second_bucket_same_expiry_blocked(env, capsys):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.30") == 0
    assert run(env, "open", "ZZZQ", "0.20") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_uses_latest_saved_rr_file(env):
    write_saved(env, stamp=date(2026, 10, 9), candidates=[hand_candidate(
        short_put_mid=2.5, net_credit=1.0, breakeven_down=89.0, capital_basis=8900.0)])
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.30") == 0
    assert ledger(env)["spreads"][0]["net_credit"] == pytest.approx(0.5)


# ---- isolation from the other loaders --------------------------------------------

OTHER_PATTERNS = (spread_sim.SUGGESTION_FILE, put_screen.SUGGESTION_FILE,
                  put_screen.SUMMARY_FILE, call_screen.SUGGESTION_FILE,
                  call_screen.SUMMARY_FILE, collar_screen.SUGGESTION_FILE,
                  condor_sim.SUGGESTION_FILE, bullcall_sim.SUGGESTION_FILE)


def test_rr_files_not_picked_up_by_other_loaders(env, capsys):
    assert run(env, "suggest", "ZZZQ") == 0
    for p in sug_dir(env).iterdir():
        for pattern in OTHER_PATTERNS:
            assert pattern.match(p.name) is None, (pattern.pattern, p.name)
    assert spread_sim._load_suggestion(env["ledger"], "ZZZQ") is None
    with pytest.raises(spread_ledger.LedgerError, match="没有 ZZZQ 的已保存铁鹰候选"):
        condor_sim._load_candidate(env["ledger"], "ZZZQ")
    code = spread_sim.main(["--ledger-dir", str(env["ledger"]), "open", "ZZZQ", "0.15"], today=SNAP)
    assert code == 1 and not (env["ledger"] / "spreads.json").exists()

    # and the other way round: none of their files is a risk reversal candidate
    for p in list(sug_dir(env).iterdir()):
        p.unlink()
    others = [f"{SNAP}-ZZZQ.json", f"{SNAP}-ZZZQ-put.json", f"{SNAP}-ZZZQ-call-real.json",
              f"{SNAP}-ZZZQ-collar-lot1.json", f"{SNAP}-ZZZQ-condor.json",
              f"{SNAP}-ZZZQ-bullcall.json", f"{SNAP}-ZZZQ-rr.json.tmp", f"{SNAP}-ZZZQ-RR.json"]
    for name in others:
        (sug_dir(env) / name).write_text("{}", encoding="utf-8")
        assert rr_sim.SUGGESTION_FILE.match(name) is None, name
    capsys.readouterr()
    assert run(env, "open", "ZZZQ", "0.30") == 1
    assert "没有 ZZZQ 的已保存风险反转候选" in capsys.readouterr().err
    assert rr_sim._day_docs(sug_dir(env), SNAP.isoformat()) == ([], [])


def test_summary_lists_damaged_same_day_file(env):
    sug_dir(env).mkdir(parents=True)
    (sug_dir(env) / f"{SNAP}-ZZZR-rr.json").write_text("{bad", encoding="utf-8")
    assert run(env, "suggest", "ZZZQ") == 0
    text = summary_md(env)
    assert f"{SNAP}-ZZZR-rr.json" in text and "ZZZQ" in text


# ---- status / close --------------------------------------------------------------

def test_status_and_close_delegate_to_spread_sim(env, monkeypatch):
    seen = []
    monkeypatch.setattr(spread_sim, "cmd_status",
                        lambda args, d, t: seen.append(("status", Path(d), t)) or 0)
    monkeypatch.setattr(spread_sim, "cmd_close",
                        lambda args, d, t: seen.append(("close", args.id, Path(d), t)) or 0)
    assert run(env, "status") == 0
    assert run(env, "close", "7") == 0
    assert seen == [("status", env["ledger"], SNAP), ("close", 7, env["ledger"], SNAP)]


SELLER_STUBS = ("threshold_flags", "cost_to_close", "unrealized_pnl", "close_pnl", "expiry_pnl",
                "breakeven_win_rate")


@pytest.fixture
def no_seller_maths(monkeypatch):
    """Seller / bull call money functions raise on any call: the risk reversal path never uses them."""
    def boom(*a, **k):
        raise AssertionError("risk reversal must not reach a seller or bull call function")
    names = list(SELLER_STUBS) + [n for n in dir(spread_core) if n.startswith("bullcall_")
                                  and callable(getattr(spread_core, n))]
    for name in names:
        if hasattr(spread_core, name):
            monkeypatch.setattr(spread_core, name, boom)


def _open_hand(env):
    write_saved(env)
    assert run(env, "open", "ZZZQ", "0.30") == 0
    return ledger(env)["spreads"][0]["id"]


def test_status_settles_at_expiry_with_section_8_numbers(env, monkeypatch, capsys, no_seller_maths):
    _open_hand(env)
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: 120.0)
    assert run(env, "status", today=date(2026, 11, 23)) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "expired" and s["pnl"] == pytest.approx(1050.0)
    assert s["result"] == "win" and s["outcome_zone"] == "up" and s["exit_underlying"] == 120.0
    assert "+$1,050" in capsys.readouterr().out


def test_status_marks_open_record_and_close_early_gain(env, capsys, no_seller_maths):
    sid = _open_hand(env)
    env["put_mids"] = {90.0: 1.20}
    env["call_mids"] = {110.0: 3.00}
    env["call_window"] = [110.0]
    assert run(env, "status") == 0
    out = capsys.readouterr().out
    assert "浮盈亏 +$230" in out and "净收入 $0.50/股" in out
    assert run(env, "close", str(sid)) == 0
    (s,) = ledger(env)["spreads"]
    assert s["status"] == "closed_early" and s["pnl"] == pytest.approx(230.0)
    assert s["result"] == "win" and s["outcome_zone"] is None
    assert s["exit_short_put_mid"] == 1.2 and s["exit_long_call_mid"] == 3.0


def test_close_early_loss_with_section_8_numbers(env, no_seller_maths):
    sid = _open_hand(env)
    env["put_mids"] = {90.0: 6.00}
    env["call_mids"] = {110.0: 0.40}
    env["call_window"] = [110.0]
    assert run(env, "close", str(sid)) == 0
    (s,) = ledger(env)["spreads"]
    assert s["pnl"] == pytest.approx(-510.0) and s["result"] == "loss"


def test_close_refused_when_a_leg_has_no_quote(env, capsys, no_seller_maths):
    sid = _open_hand(env)
    env["call_mids"] = {105.0: 3.0}                      # the 110 call is gone
    env["call_window"] = [105.0]
    assert run(env, "close", str(sid)) == 1
    assert ledger(env)["spreads"][0]["status"] == "open"
    assert "有效报价" in capsys.readouterr().err


def test_full_flow_never_reaches_seller_functions(env, monkeypatch, no_seller_maths):
    assert run(env, "suggest", "ZZZQ") == 0
    assert run(env, "open", "ZZZQ", "0.30") == 0
    assert run(env, "status") == 0
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: 80.0)
    assert run(env, "status", today=date(2026, 11, 23)) == 0
    (s,) = ledger(env)["spreads"]
    assert s["pnl"] == pytest.approx(-950.0) and s["outcome_zone"] == "down"


def test_rr_sim_never_writes_positions_md(env, monkeypatch):
    written = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    assert run(env, "suggest", "ZZZQ") == 0
    assert run(env, "open", "ZZZQ", "0.30") == 0
    assert written and "positions.md" not in written
    assert not any(p.name == "positions.md" for p in env["ledger"].rglob("*"))


def test_rr_sim_imports_no_broker_modules():
    source = (REPO_ROOT / "scripts" / "rr_sim.py").read_text(encoding="utf-8")
    source += (REPO_ROOT / "scripts" / "rr_sim_render.py").read_text(encoding="utf-8")
    for name in ("broker_", "placeOrder", "ib_async", "with_suffix"):
        assert name not in source, name
