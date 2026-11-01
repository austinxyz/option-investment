"""/option-review for bear call spreads, bear put spreads and long puts (bearish plan task 6).

Market data is faked; the ledger lives in tmp_path. Invented tickers and the hand-computed
numbers of the two designs (bearish-and-new-strategies 5.8 / section 8, long-put 2-3), spot 100:
  Bear Call: sell 105 C @2.10, buy 110 C @0.90 -> credit 1.20, width 5; +$120 / -$380.
  Bear Put:  buy 100 P @4.80, sell 90 P @1.80 -> debit 3.00, width 10; +$700 / -$300.
  Long Put:  buy 95 P @2.40 -> debit 2.40; max loss $240.
"""
import hashlib
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ibkr_session  # noqa: E402
import put_review_rules as rules  # noqa: E402
import spread_core  # noqa: E402
import spread_data  # noqa: E402
from test_put_review_cli import TODAY, row, run, seed, snap  # noqa: E402
from test_put_review_cli_bullcall import LONG_EXPIRY  # noqa: E402
from test_put_review_cli_rr import _existing_ledger, rr_record  # noqa: E402

# Eight older strategies (bull call and risk reversal included) plus an expiring one.
PIN_REVIEW_MD = "44e570130751ae3d4d7ca2f8e8e96d0513c2fd33b7c66343417646c90d52f1aa"
PIN_REVIEW_JSON = "5c6a5c0423697c63bf802588d99cc9f9bebf6ce73d33cb26a523f24ed43574d0"


@pytest.fixture
def pin_market(monkeypatch):
    """Strike-filtering fake chain with a row for every leg of every older strategy."""
    m = {"spot": 100.0, "asked": [], "earnings": "no", "ex_div": None,
         "puts": [row(95.0, 0.40), row(90.0, 0.50), row(85.0, 0.20)],
         "calls": [row(100.0, 3.20), row(105.0, 1.30), row(110.0, 0.40), row(115.0, 0.10),
                   row(120.0, 0.05)]}
    _install(monkeypatch, m)
    return m


def _install(monkeypatch, m):
    def fetch(kind):
        def _f(t, e, strikes=()):
            m["asked"].append((kind, tuple(strikes)))
            return [r for r in m["puts" if kind == "P" else "calls"] if r["strike"] in strikes]
        return _f

    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, m["spot"]))
    monkeypatch.setattr(spread_data, "fetch_puts", fetch("P"))
    monkeypatch.setattr(spread_data, "fetch_calls", fetch("C"))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: m["earnings"])
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: m["ex_div"])
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)


def _old_ledger():
    return [*_existing_ledger(),
            rr_record(ticker="ZZZK", short_put_strike=90.0, long_call_strike=120.0)]


def _digests(tmp_path):
    folder = tmp_path / "reviews"
    return [hashlib.sha256((folder / f"{TODAY.isoformat()}.{s}").read_bytes()).hexdigest()
            for s in ("md", "json")]


def test_review_md_unchanged_for_old_strategies(pin_market, tmp_path):
    seed(tmp_path, *_old_ledger())
    assert run(tmp_path) == 0
    assert _digests(tmp_path) == [PIN_REVIEW_MD, PIN_REVIEW_JSON]


# ---- records -------------------------------------------------------------------------------

def _common(strategy, ticker, expiry, suffix):
    return {"strategy": strategy, "ticker": ticker, "contracts": 1,
            "snapshot_date": "2026-10-09", "expiry": expiry, "underlying_price": 100.0,
            "suggestion_file": f"2026-10-09-{ticker}-{suffix}.md", "earnings_in_window": False,
            "status": "open", "exit_date": None, "exit_underlying": None, "pnl": None,
            "result": None}


def bearcall_record(ticker="ZQBR", expiry="2026-10-23", **kw):
    return {**_common("bear_call_spread", ticker, expiry, "bearcall"),
            "option_type": "C", "short_strike": 105.0, "long_strike": 110.0, "width": 5.0,
            "credit": 1.20, "short_mid": 2.10, "long_mid": 0.90, "delta_bucket": 0.15,
            "short_delta": 0.16, "short_iv": 0.40, "exit_cost": None, **kw}


def bearput_record(ticker="ZQBP", expiry=LONG_EXPIRY, **kw):
    return {**_common("bear_put_spread", ticker, expiry, "bearput"),
            "long_put_strike": 100.0, "short_put_strike": 90.0, "width": 10.0,
            "long_put_mid": 4.80, "short_put_mid": 1.80, "debit": 3.00, "breakeven": 97.0,
            "breakeven_move_pct": -0.03, "max_profit_move_pct": -0.10, "delta_bucket": 0.50,
            "long_put_delta": -0.51, "short_put_delta": -0.22, "long_put_iv": 0.40,
            "short_put_iv": 0.42, **kw}


def longput_record(ticker="ZQLP", expiry=LONG_EXPIRY, **kw):
    return {**_common("long_put", ticker, expiry, "longput"),
            "put_strike": 95.0, "put_mid": 2.40, "debit": 2.40, "breakeven": 92.6,
            "breakeven_move_pct": -0.074, "delta_bucket": 0.30, "put_delta": -0.31,
            "put_iv": 0.40, **kw}


@pytest.fixture
def bear_market(monkeypatch):
    """Design-section-8 mids: every position marks at exactly its opening price (pnl $0)."""
    m = {"spot": 100.0, "asked": [], "earnings": "no", "ex_div": None,
         "calls": [row(105.0, 2.10), row(110.0, 0.90)],
         "puts": [row(100.0, 4.80), row(90.0, 1.80), row(95.0, 2.40)]}
    _install(monkeypatch, m)
    return m


SELLER_NAMES = ("threshold_flags", "cost_to_close", "unrealized_pnl", "csp_threshold_flags",
                "close_pnl", "expiry_pnl", "breakeven_win_rate", "bullcall_close_value",
                "bullcall_pnl", "bullcall_threshold_flags", "bullcall_fraction_captured")


def _boom(monkeypatch, names):
    def boom(*a, **k):
        raise AssertionError("helper of another strategy used")
    for name in names:
        if hasattr(spread_core, name):
            monkeypatch.setattr(spread_core, name, boom)


# ---- pure rules: Bear Call (parent 5.8, seller order 0-5) -----------------------------------

def bc_m(**kw):
    base = {"strategy": "bear_call_spread", "error": None, "days_left": 20, "spot": 100.0,
            "short_strike": 105.0, "long_strike": 110.0, "credit": 1.20, "width": 5.0,
            "captured": 0.30, "itm": False, "stop_flag": False, "delta": 0.16, "data_ok": True,
            "earnings": "no", "ex_div": None}
    base.update(kw)
    return base


def test_bearcall_rule0_no_quote():
    v = rules.review_bearcall(bc_m(error="期权链里找不到这两腿的有效报价"))
    assert (v["advice"], v["rule"]) == ("attention", 0)
    assert v["reason"].startswith("取不到报价：")


def test_bearcall_rule1_expiry_day():
    assert rules.review_bearcall(bc_m(days_left=0))["advice"] == "await_settlement"


def test_bearcall_rule2_in_the_money_is_strict():
    at = rules.review_bearcall(bc_m(spot=105.0))
    assert at["rule"] != 2 and at["advice"] == "hold"
    above = rules.review_bearcall(bc_m(spot=105.01, itm=True))
    assert (above["advice"], above["rule"]) == ("attention", 2)
    assert "105" in above["reason"] and "提前指派" not in above["reason"]


def test_bearcall_rule2_ex_div_adds_early_assignment():
    v = rules.review_bearcall(bc_m(spot=106.0, itm=True, ex_div="2026-10-20"))
    assert (v["advice"], v["rule"]) == ("attention", 2)
    assert "2026-10-20" in v["reason"] and "提前指派" in v["reason"]


def test_bearcall_rule2_stop_flag():
    v = rules.review_bearcall(bc_m(stop_flag=True, captured=-1.6))
    assert (v["advice"], v["rule"]) == ("attention", 2) and "止损" in v["reason"]


def test_bearcall_rule3_close_at_80_percent():
    v = rules.review_bearcall(bc_m(captured=0.8 - 1e-12))
    assert (v["advice"], v["rule"]) == ("close", 3) and "80%" in v["reason"]
    assert rules.review_bearcall(bc_m(captured=0.79))["advice"] == "hold"


def test_bearcall_rule4_earnings():
    v = rules.review_bearcall(bc_m(earnings="yes"))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "财报" in v["reason"]


def test_bearcall_rule4_delta_at_threshold_only_when_trusted():
    v = rules.review_bearcall(bc_m(delta=0.35))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "0.35" in v["reason"]
    stale = rules.review_bearcall(bc_m(delta=0.35, data_ok=False))
    assert stale["advice"] == "hold" and stale["reason"].endswith(rules.STALE_NOTE)


def test_bearcall_rule5_quiet_expiry():
    v = rules.review_bearcall(bc_m(days_left=5, delta=0.10))
    assert (v["advice"], v["rule"]) == ("hold", 5) and "等它自然到期归零" in v["reason"]
    plain = rules.review_bearcall(bc_m(days_left=6, delta=0.10))
    assert plain["reason"] == "继续持有"


# ---- pure rules: Bear Put (parent 5.8, buyer, no Delta, no ex-div exception) -----------------

def bp_m(**kw):
    base = {"strategy": "bear_put_spread", "error": None, "days_left": 20, "spot": 100.0,
            "long_put_strike": 100.0, "short_put_strike": 90.0, "debit": 3.0, "width": 10.0,
            "value": 3.0, "pnl": 0.0, "captured": 0.0, "stop_flag": False,
            "take_profit_flag": False, "long_put_delta": 0.5, "delta": None, "data_ok": True,
            "earnings": "no", "ex_div": None}
    base.update(kw)
    return base


def test_bearput_rule0_and_rule1():
    v = rules.review_bearput(bp_m(error="期权链里找不到这两腿的有效报价"))
    assert (v["advice"], v["rule"]) == ("attention", 0) and v["reason"].startswith("取不到报价：")
    assert rules.review_bearput(bp_m(days_left=0))["rule"] == 1


def test_bearput_rule2_stop_at_half_debit():
    v = rules.review_bearput(bp_m(pnl=-150.0, stop_flag=True))
    assert (v["advice"], v["rule"]) == ("attention", 2) and "50%" in v["reason"]


def test_bearput_rule3_take_profit_or_at_short_strike():
    tp = rules.review_bearput(bp_m(pnl=350.0, captured=0.5, take_profit_flag=True, spot=95.0))
    assert (tp["advice"], tp["rule"]) == ("close", 3) and "50%" in tp["reason"]
    at_short = rules.review_bearput(bp_m(spot=90.0, pnl=300.0, captured=0.4286))
    assert (at_short["advice"], at_short["rule"]) == ("close", 3) and "90" in at_short["reason"]
    assert rules.review_bearput(bp_m(spot=89.0, pnl=300.0))["rule"] == 3
    assert rules.review_bearput(bp_m(spot=90.01, pnl=300.0))["advice"] == "hold"


def test_bearput_rule4_earnings():
    v = rules.review_bearput(bp_m(earnings="yes"))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "财报" in v["reason"]


def test_bearput_rule4_time_stop_ten_days_without_profit():
    v = rules.review_bearput(bp_m(days_left=10, pnl=0.0))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "10 天" in v["reason"]
    assert rules.review_bearput(bp_m(days_left=10, pnl=10.0))["advice"] == "hold"
    assert rules.review_bearput(bp_m(days_left=11, pnl=-10.0))["advice"] == "hold"


def test_bearput_rule4_five_days_strictly_between_legs():
    v = rules.review_bearput(bp_m(days_left=5, spot=95.0, pnl=50.0))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "两腿之间" in v["reason"]
    assert rules.review_bearput(bp_m(days_left=5, spot=100.0, pnl=50.0))["advice"] == "hold"
    assert rules.review_bearput(bp_m(days_left=6, spot=95.0, pnl=50.0))["advice"] == "hold"


def test_bearput_rule5_hold_and_ex_div_ignored():
    v = rules.review_bearput(bp_m(spot=99.0, ex_div="2026-10-20", pnl=20.0))
    assert (v["advice"], v["rule"], v["reason"]) == ("hold", 5, "继续持有")
    unknown = rules.review_bearput(bp_m(earnings="unknown"))
    assert unknown["reason"] == "继续持有" + rules.UNKNOWN_EARNINGS_NOTE


# ---- pure rules: Long Put (long-put design section 2) --------------------------------------

def lp_m(**kw):
    base = {"strategy": "long_put", "error": None, "days_left": 20, "spot": 100.0,
            "put_strike": 95.0, "debit": 2.4, "value": 2.4, "pnl": 0.0, "captured": 0.0,
            "stop_flag": False, "take_profit_flag": False, "put_delta": 0.3, "delta": None,
            "data_ok": True, "earnings": "no", "ex_div": None}
    base.update(kw)
    return base


def test_longput_rule0_and_rule1():
    v = rules.review_longput(lp_m(error="期权链里找不到这条腿的有效报价"))
    assert (v["advice"], v["rule"]) == ("attention", 0) and v["reason"].startswith("取不到报价：")
    assert rules.review_longput(lp_m(days_left=-1))["rule"] == 1


def test_longput_rule2_stop_at_half_debit():
    v = rules.review_longput(lp_m(pnl=-120.0, stop_flag=True))
    assert (v["advice"], v["rule"]) == ("attention", 2) and "50%" in v["reason"]


def test_longput_rule3_take_profit_at_full_debit():
    v = rules.review_longput(lp_m(pnl=240.0, captured=1.0, take_profit_flag=True, spot=90.0))
    assert (v["advice"], v["rule"]) == ("close", 3) and "100%" in v["reason"]


def test_longput_rule4_earnings():
    v = rules.review_longput(lp_m(earnings="yes"))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "财报" in v["reason"]


def test_longput_rule4_time_stop_ten_days():
    v = rules.review_longput(lp_m(days_left=10, pnl=-10.0))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "10 天" in v["reason"]
    assert rules.review_longput(lp_m(days_left=10, pnl=5.0, spot=94.0))["advice"] == "hold"


def test_longput_rule4_five_days_out_of_the_money():
    v = rules.review_longput(lp_m(days_left=5, spot=95.0, pnl=10.0))
    assert (v["advice"], v["rule"]) == ("attention", 4) and "价外" in v["reason"]
    assert rules.review_longput(lp_m(days_left=5, spot=94.99, pnl=10.0))["advice"] == "hold"


def test_longput_rule5_hold():
    assert rules.review_longput(lp_m())["reason"] == "继续持有"


def test_review_never_uses_delta_rule_for_buyers(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("another strategy's rules used")
    for name in ("review_bullcall", "review_condor", "review_rr", "review_lot_put", "_itm_reason"):
        monkeypatch.setattr(rules, name, boom)
    high = rules.review_position(bp_m(delta=0.9, long_put_delta=0.9, pnl=10.0, spot=101.0))
    assert (high["advice"], high["rule"], high["reason"]) == ("hold", 5, "继续持有")
    quiet = rules.review_position(bp_m(delta=0.05, long_put_delta=0.05, days_left=3,
                                       pnl=10.0, spot=101.0))
    assert quiet["reason"] == "继续持有"
    lp_high = rules.review_position(lp_m(delta=0.9, put_delta=0.9, pnl=10.0))
    assert (lp_high["advice"], lp_high["rule"]) == ("hold", 5)
    lp_quiet = rules.review_position(lp_m(delta=0.05, put_delta=0.05, days_left=3, pnl=10.0,
                                          spot=94.0))
    assert lp_quiet["reason"] == "继续持有"
    assert rules.review_position(bc_m(delta=0.9))["rule"] == 4  # the seller does use it


# ---- CLI ------------------------------------------------------------------------------------

def test_review_requests_explicit_strikes(bear_market, tmp_path):
    seed(tmp_path, bearcall_record(), bearput_record(), longput_record())
    assert run(tmp_path) == 0
    assert bear_market["asked"] == [("C", (105.0, 110.0)), ("P", (100.0, 90.0)), ("P", (95.0,))]
    assert all(p["data_quality"] == "ok" for p in snap(tmp_path)["positions"])


def test_review_snapshot_records_new_fields(bear_market, tmp_path, monkeypatch):
    _boom(monkeypatch, ("expiry_pnl", "cost_to_close", "unrealized_pnl", "csp_threshold_flags",
                        "bullcall_close_value", "bullcall_pnl"))
    seed(tmp_path, bearcall_record())
    assert run(tmp_path) == 0
    (bc,) = snap(tmp_path)["positions"]
    # mids 2.10 - 0.90 = 1.20 = the credit -> cost 1.20, captured 0, pnl $0
    assert bc["strategy"] == "bear_call_spread" and bc["data_quality"] == "ok"
    assert bc["short_strike"] == 105.0 and bc["long_strike"] == 110.0 and bc["width"] == 5.0
    assert bc["credit"] == 1.20 and bc["cost_to_close"] == pytest.approx(1.20)
    assert bc["captured"] == pytest.approx(0.0) and bc["pnl"] == 0.0 and bc["itm"] is False
    assert bc["delta"] == pytest.approx(spread_core.call_delta(100.0, 105.0, 0.40, 7), abs=1e-6)
    assert bc["ex_div"] is None and bc["earnings"] == "no"
    assert (bc["advice"], bc["rule"]) == ("hold", 5)
    md = (tmp_path / "reviews" / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "| bear_call_spread | ZQBR | 105/110 |" in md

    _boom(monkeypatch, SELLER_NAMES)
    buyers = tmp_path / "buyers"
    buyers.mkdir()
    seed(buyers, bearput_record(), longput_record())
    assert run(buyers) == 0
    bp, lp = snap(buyers)["positions"]
    assert bp["long_put_strike"] == 100.0 and bp["short_put_strike"] == 90.0
    assert bp["debit"] == 3.0 and bp["width"] == 10.0 and bp["value"] == pytest.approx(3.0)
    assert bp["pnl"] == 0.0 and bp["captured"] == pytest.approx(0.0)
    assert bp["stop_flag"] is False and bp["take_profit_flag"] is False
    assert bp["long_put_delta"] == pytest.approx(
        spread_core.put_delta(100.0, 100.0, 0.40, 35), abs=1e-6)
    assert bp["delta"] is None and bp["ex_div"] is None
    assert lp["put_strike"] == 95.0 and lp["debit"] == 2.4 and lp["value"] == pytest.approx(2.4)
    assert lp["pnl"] == 0.0 and lp["captured"] == pytest.approx(0.0)
    assert lp["put_delta"] == pytest.approx(spread_core.put_delta(100.0, 95.0, 0.40, 35), abs=1e-6)
    assert lp["delta"] is None
    for p in (bp, lp):
        for legacy in ("credit", "short_strike", "long_strike", "net_credit", "cost_to_close"):
            assert legacy not in p
    md = (buyers / "reviews" / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "| bear_put_spread | ZQBP | 100/90 |" in md and "| long_put | ZQLP | 95 |" in md


def test_neutral_does_not_crash_review_run(bear_market, tmp_path):
    expired = "2026-10-16"
    seed(tmp_path, bearcall_record(), bearput_record(), longput_record(),
         bearcall_record(ticker="ZQBX", expiry=expired),
         bearput_record(ticker="ZQPX", expiry=expired),
         longput_record(ticker="ZQLX", expiry=expired))
    assert run(tmp_path) == 0
    got = {p["ticker"]: p for p in snap(tmp_path)["positions"]}
    for t in ("ZQBR", "ZQBP", "ZQLP"):
        assert got[t]["error"] is None and got[t]["advice"] == "hold", t
    for t in ("ZQBX", "ZQPX", "ZQLX"):
        assert got[t]["advice"] == "await_settlement" and got[t]["data_quality"] == "none", t
        assert got[t]["delta"] is None
    assert got["ZQBX"]["short_strike"] == 105.0 and got["ZQPX"]["long_put_strike"] == 100.0
    assert got["ZQLX"]["put_strike"] == 95.0


@pytest.mark.parametrize("rec, kind, strike", [
    (bearcall_record, "calls", 110.0), (bearput_record, "puts", 90.0),
    (longput_record, "puts", 95.0)])
def test_review_missing_quote_is_rule0(bear_market, tmp_path, rec, kind, strike):
    bear_market[kind] = [r for r in bear_market[kind] if r["strike"] != strike]
    seed(tmp_path, rec())
    assert run(tmp_path) == 0
    (p,) = snap(tmp_path)["positions"]
    assert (p["advice"], p["rule"], p["data_quality"]) == ("attention", 0, "none")
    assert p["reason"].startswith("取不到报价：期权链里找不到") and p["pnl"] is None


def test_review_zero_mid_counts_as_no_quote(bear_market, tmp_path):
    bear_market["puts"] = [{"strike": 95.0, "bid": 0.0, "ask": 0.0, "last": 0.0, "iv": 0.4}]
    seed(tmp_path, longput_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert (p["advice"], p["rule"]) == ("attention", 0)


def test_review_bearcall_itm_ex_div_in_cli(bear_market, tmp_path):
    bear_market["spot"] = 106.0
    bear_market["ex_div"] = date(2026, 10, 20)
    bear_market["calls"] = [row(105.0, 2.30), row(110.0, 0.60)]
    seed(tmp_path, bearcall_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["itm"] is True and p["ex_div"] == "2026-10-20"
    assert (p["advice"], p["rule"]) == ("attention", 2) and "提前指派" in p["reason"]


@pytest.mark.parametrize("spot", [100.0, 105.0])
def test_review_bearcall_ex_div_only_when_strictly_itm(bear_market, tmp_path, monkeypatch, spot):
    def boom(*a, **k):
        raise AssertionError("ex-dividend looked up with the short call not in the money")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", boom)
    bear_market["spot"] = spot
    seed(tmp_path, bearcall_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["error"] is None and p["ex_div"] is None and p["itm"] is False


def test_review_buyers_never_look_up_ex_div(bear_market, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ex-dividend looked up for a put buyer")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", boom)
    seed(tmp_path, bearput_record(), longput_record())
    run(tmp_path)
    assert all(p["error"] is None for p in snap(tmp_path)["positions"])


def test_review_bearput_take_profit_in_cli(bear_market, tmp_path):
    # long 8.00 / short 1.50 -> value 6.50 -> +$350 = 50% of $700
    bear_market["spot"] = 93.0
    bear_market["puts"] = [row(100.0, 8.00), row(90.0, 1.50)]
    seed(tmp_path, bearput_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(350.0) and p["captured"] == pytest.approx(0.5)
    assert p["take_profit_flag"] is True and (p["advice"], p["rule"]) == ("close", 3)


def test_review_longput_stop_in_cli(bear_market, tmp_path):
    # mid 1.20 -> -$120 = -50% of $240
    bear_market["puts"] = [row(95.0, 1.20)]
    seed(tmp_path, longput_record())
    run(tmp_path)
    (p,) = snap(tmp_path)["positions"]
    assert p["pnl"] == pytest.approx(-120.0) and p["captured"] == pytest.approx(-0.5)
    assert p["stop_flag"] is True and (p["advice"], p["rule"]) == ("attention", 2)


def test_review_buyer_stale_quote_is_stale_not_error(bear_market, tmp_path):
    bear_market["puts"] = [{**r, "bid": 0, "ask": 0} for r in bear_market["puts"]]
    seed(tmp_path, bearput_record(), longput_record())
    run(tmp_path)
    for p in snap(tmp_path)["positions"]:
        assert p["error"] is None and p["data_quality"] == "stale" and p["delta"] is None


def test_existing_rows_identical_with_bearish_present(pin_market, tmp_path):
    seed(tmp_path, *_old_ledger())
    assert run(tmp_path) == 0
    base = snap(tmp_path)["positions"]
    pin_market["puts"].append(row(100.0, 4.80))
    other = tmp_path / "with_bearish"
    other.mkdir()
    seed(other, *_old_ledger(), bearcall_record(), bearput_record(), longput_record())
    assert run(other) == 0
    rows = snap(other)["positions"]
    assert rows[:len(base)] == base
    assert [r["strategy"] for r in rows[len(base):]] == ["bear_call_spread", "bear_put_spread",
                                                         "long_put"]
    assert all(r["error"] is None for r in rows[len(base):])
