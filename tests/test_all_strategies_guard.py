"""Cross-cutting safety net: every strategy must survive every place that dispatches on strategy.

Self-contained (invented numbers, no network): the market layer is monkeypatched. If a new strategy
is added and one report/review/journal/settle path forgets it, this file fails first.
"""
from datetime import date
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import put_journal
import put_review
import spread_data
import spread_html
import spread_ledger as ledger_mod
import spread_report
import spread_sim

TODAY = date(2026, 10, 30)
EXPIRY = "2026-11-20"
PAST_EXPIRY = "2026-10-23"
STRATEGIES = ("bull_put_spread", "cash_secured_put", "covered_call", "collar", "protective_put",
              "iron_condor", "bull_call_spread")
NEW_STRATEGIES = ("collar", "protective_put")
CONDOR = "iron_condor"
BULLCALL = "bull_call_spread"
COLLAR_TICKER, PPUT_TICKER, CONDOR_TICKER, BULLCALL_TICKER = "ZQCL", "ZQPP", "ZQIC", "ZQBC"
# Distinctive invented values that must never reach the public page.
PRIVATE_TOKENS = (COLLAR_TICKER, PPUT_TICKER, "71.5", "93.25", "83.37", "777", "333", "1234")
CONDOR_TOKENS = (CONDOR_TICKER, "83.5", "88.5", "111.5", "116.5", "87.13", "112.87", "1.37",
                 "1,089", "iron_condor", "铁鹰")
BULLCALL_TOKENS = (BULLCALL_TICKER, "101.9", "bull_call_spread", "Call 借方价差", "100/105",
                   "bullcall", "0.3548")


def _base(strategy: str, ticker: str, expiry: str, status: str, pnl: float, result: str) -> dict:
    settled = status != "open"
    return {
        "strategy": strategy, "ticker": ticker, "contracts": 2, "snapshot_date": "2026-10-09",
        "expiry": expiry, "underlying_price": 100.0, "status": status,
        "exit_date": expiry if settled else None,
        "exit_underlying": 101.0 if settled else None,
        "pnl": pnl if settled else None, "result": result if settled else None,
        "monday_gap_pct": None,
    }


def _record(strategy: str, status: str, expiry: str) -> dict:
    """One invented record per strategy; the ticker is unique per (strategy, status)."""
    ticker = {"bull_put_spread": "ZQBP", "cash_secured_put": "ZQCS", "covered_call": "ZQCC",
              "collar": COLLAR_TICKER, "protective_put": PPUT_TICKER,
              "iron_condor": CONDOR_TICKER, "bull_call_spread": BULLCALL_TICKER}[strategy]
    ticker += "O" if status == "open" else "S"
    if strategy == CONDOR:
        return _condor_record(ticker, status, expiry)
    if strategy == BULLCALL:
        return _bullcall_record(ticker, status, expiry)
    if strategy == "bull_put_spread":
        rec = _base(strategy, ticker, expiry, status, 80.0, "win")
        rec.update(short_strike=95.0, long_strike=90.0, width=5.0, credit=0.8, delta_bucket=0.10,
                   short_delta=0.1)
        rec.pop("strategy")  # legacy records carry no strategy field
        return rec
    if strategy == "cash_secured_put":
        rec = _base(strategy, ticker, expiry, status, 150.0, "win")
        rec.update(short_strike=95.0, long_strike=None, width=None, credit=0.75,
                   short_delta=0.2, delta_target=0.2, tier="mid")
        return rec
    if strategy == "covered_call":
        rec = _base(strategy, ticker, expiry, status, 120.0, "win")
        rec.update(short_strike=110.0, long_strike=None, width=None, credit=0.6, cost_basis=90.0,
                   reference_price=90.0, short_delta=0.2, delta_target=0.2, tier="mid",
                   lot_ref={"source": "manual", "lot_id": 1, "ticker": ticker})
        return rec
    rec = _base(strategy, ticker, expiry, status, 60.0,
                "called_away" if strategy == "collar" else "expired_worthless")
    rec.update(put_strike=71.5, put_debit=1.2, call_strike=93.25 if strategy == "collar" else None,
               call_credit=1.8 if strategy == "collar" else 0, covered_shares=777,
               cost_basis=83.37, reference_price=83.37, dte_mode="monthly", tier=85,
               lot_ref={"source": "real", "lot_id": None, "ticker": ticker},
               net_credit=0.6 if strategy == "collar" else -1.2,
               earnings_in_window=False,
               stock_leg_pnl=1234.0 if status != "open" else None)
    return rec


def _condor_record(ticker: str, status: str, expiry: str) -> dict:
    """Invented condor: 83.5/88.5/111.5/116.5, width 5, net 1.37, 3 contracts -> $1,089 at risk."""
    settled = status != "open"
    rec = _base(CONDOR, ticker, expiry, status, 411.0, "win")
    rec.update(contracts=3, put_long_strike=83.5, put_short_strike=88.5, call_short_strike=111.5,
               call_long_strike=116.5, width=5.0, put_credit=0.71, call_credit=0.66,
               net_credit=1.37, breakeven_low=87.13, breakeven_high=112.87, delta_bucket=0.15,
               put_short_delta=0.15, call_short_delta=0.15, put_short_iv=0.3, call_short_iv=0.28,
               earnings_in_window=False, suggestion_file=f"2026-10-09-{ticker}-condor.json",
               exit_cost=0.0 if settled else None)
    if settled:
        rec["breached_side"] = "none"
    return rec


def _bullcall_record(ticker: str, status: str, expiry: str) -> dict:
    """Design section 10: buy 100 Call 3.20 / sell 105 Call 1.30, 2 contracts -> debit 1.90,
    breakeven 101.90, max loss $380; settled at C=103 -> value 3.00, +$220, captured 0.3548."""
    settled = status != "open"
    rec = _base(BULLCALL, ticker, expiry, status, 220.0, "win")
    rec.update(contracts=2, long_call_strike=100.0, short_call_strike=105.0, width=5.0,
               long_call_mid=3.2, short_call_mid=1.3, debit=1.9, breakeven=101.9,
               breakeven_move_pct=0.019, max_profit_move_pct=0.05, delta_bucket=0.5,
               long_call_delta=0.52, short_call_delta=0.31, long_call_iv=0.3,
               short_call_iv=0.28, earnings_in_window=False,
               suggestion_file=f"2026-10-09-{ticker}-bullcall.json",
               exit_value=3.0 if settled else None,
               fraction_of_max_captured=0.3548 if settled else None)
    return rec


def _ledger(records: list[dict]) -> dict:
    led = ledger_mod.empty_ledger()
    for r in records:
        led, _ = ledger_mod.add_spread(led, r)
    return led


def _open_records() -> list[dict]:
    return [_record(s, "open", EXPIRY) for s in STRATEGIES]


def _settled_records() -> list[dict]:
    return [_record(s, "expired", PAST_EXPIRY) for s in STRATEGIES]


@pytest.fixture
def market(monkeypatch):
    """A flat fake market: every put/call quote exists, spot 100, no earnings or dividends."""
    def quote(strike):
        return {"strike": strike, "bid": 1.0, "ask": 1.1, "last": 1.05, "iv": 0.4}

    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=(), **kw: [quote(k) for k in strikes])
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(), **kw: [quote(k) for k in strikes])
    monkeypatch.setattr(spread_data, "last_close", lambda t: (TODAY, 100.0))
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: 100.0)
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: None)


def test_every_strategy_survives_every_dispatch_site(market):
    open_, settled = _open_records(), _settled_records()
    everything = open_ + settled
    for i, r in enumerate(everything, 1):
        r["id"] = i
    marks = {r["id"]: spread_sim._mark(r) for r in open_}
    assert all("error" not in m for m in marks.values())

    for records in (open_, settled, everything):
        spread_report.render_report(records, marks, TODAY.isoformat(), broker_cols=True)
        spread_report.render_report(records, None, TODAY.isoformat())
        spread_report.render_public_report(records, marks, TODAY.isoformat())
        spread_html.render_report_html(records, marks, TODAY.isoformat())
        put_journal.build_report(records, [], {}, TODAY.isoformat())

    for r in open_:
        days_left = (date.fromisoformat(r["expiry"]) - TODAY).days
        neutral = put_review._neutral(r, days_left)
        measured = put_review._measure(r, neutral, TODAY)
        assert measured["error"] is None
        assert put_review.gather_metrics(r, TODAY)["error"] is None
        assert spread_sim._line(r)

    led = _ledger(open_)
    for r in led["spreads"]:
        led, kind, _ = spread_sim._settle(led, {**r, "expiry": PAST_EXPIRY}, TODAY)
        assert kind == "settled"
    assert {s["status"] for s in led["spreads"]} == {"expired"}
    assert all(s["pnl"] is not None for s in led["spreads"])


def test_new_strategies_never_in_win_rate_stats():
    records = _settled_records() + _open_records()
    assert not set(NEW_STRATEGIES) & set(spread_report.WIN_RATE_STRATEGIES)
    per_strategy = spread_report.compute_strategy_stats(records)
    assert not set(NEW_STRATEGIES) & set(per_strategy)
    only_new = [r for r in records if ledger_mod.strategy_of(r) in NEW_STRATEGIES]
    stats = spread_report.compute_stats(only_new)
    assert stats["n_settled"] == 0 and stats["win_rate"] is None and stats["total_pnl"] == 0
    # one settled record of each single-spread win-rate strategy is counted, the others are not
    assert spread_report.compute_stats(records)["n_settled"] == len(
        [r for r in records if r["status"] == "expired"
         and ledger_mod.strategy_of(r) in spread_report.SINGLE_SPREAD_STATS])


def test_public_text_contains_no_collar_or_protective_put_data():
    records = _settled_records() + _open_records()
    for i, r in enumerate(records, 1):
        r["id"] = i
    marks = {r["id"]: {"cost": 0.5, "pnl": 10.0, "underlying": 100.0, "dist_pct": 5.0,
                       "flags": {"stop": False, "take_profit": False}, "stale": False}
             for r in records if r["status"] == "open"}
    text = spread_report.render_public_report(records, marks, TODAY.isoformat())
    for token in PRIVATE_TOKENS:
        assert token not in text, token
    assert not spread_report.public_records([r for r in records
                                             if ledger_mod.strategy_of(r) in NEW_STRATEGIES])
    assert not spread_report.public_records(
        [r for r in records if ledger_mod.strategy_of(r) == "covered_call"])


def _numbered(records: list[dict]) -> list[dict]:
    for i, r in enumerate(records, 1):
        r["id"] = i
    return records


def _is_condor(r: dict) -> bool:
    return ledger_mod.strategy_of(r) == CONDOR


def test_condor_never_in_bull_put_stats_or_journal_ad():
    records = _numbered(_settled_records() + _open_records())
    without = [r for r in records if not _is_condor(r)]
    condors = [r for r in records if _is_condor(r)]
    assert condors and CONDOR not in spread_report.SINGLE_SPREAD_STATS
    # compute_stats: win rate, P&L and the Delta-bucket table ignore condors, mixed or alone
    assert spread_report.compute_stats(records) == spread_report.compute_stats(without)
    alone = spread_report.compute_stats(condors)
    assert alone["n_settled"] == 0 and alone["win_rate"] is None and alone["total_pnl"] == 0
    assert not alone.get("by_bucket")
    # the condor only ever gets its own row in the by-strategy table
    assert CONDOR in spread_report.compute_strategy_stats(records)
    # journal sections A-D and the sweeps are computed from single-leg cases only
    full = put_journal.build_report(records, [], {"version": 1, "notes": {}}, TODAY.isoformat())
    base = put_journal.build_report(without, [], {"version": 1, "notes": {}}, TODAY.isoformat())
    for key in ("close_advice", "attention", "hold", "streak", "sweep_close", "sweep_delta"):
        assert full[key] == base[key], key
    assert full["counts"]["settled"] == base["counts"]["settled"]
    assert "iron_condor" in full and "iron_condor" not in base
    assert not any(t.get("strategy") == CONDOR for t in full["timelines"])


def test_public_text_unchanged_by_condor_records():
    records = _numbered(_settled_records() + _open_records())
    without = [r for r in records if not _is_condor(r)]
    marks = {r["id"]: {"cost": 0.5, "pnl": 10.0, "underlying": 100.0, "dist_pct": 5.0,
                       "flags": {"stop": False, "take_profit": False}, "stale": False}
             for r in records if r["status"] == "open"}
    with_condor = spread_report.render_public_report(records, marks, TODAY.isoformat())
    assert with_condor.encode("utf-8") == spread_report.render_public_report(
        without, marks, TODAY.isoformat()).encode("utf-8")
    for token in CONDOR_TOKENS:
        assert token not in with_condor, token
    assert not spread_report.public_records([r for r in records if _is_condor(r)])


def _is_bullcall(r: dict) -> bool:
    return ledger_mod.strategy_of(r) == BULLCALL


def test_bullcall_never_in_bull_put_stats_or_journal_ad():
    records = _numbered(_settled_records() + _open_records())
    without = [r for r in records if not _is_bullcall(r)]
    bullcalls = [r for r in records if _is_bullcall(r)]
    assert bullcalls and BULLCALL not in spread_report.SINGLE_SPREAD_STATS
    assert spread_report.compute_stats(records) == spread_report.compute_stats(without)
    alone = spread_report.compute_stats(bullcalls)
    assert alone["n_settled"] == 0 and alone["win_rate"] is None and alone["total_pnl"] == 0
    assert not alone.get("by_bucket")
    assert BULLCALL in spread_report.compute_strategy_stats(records)
    full = put_journal.build_report(records, [], {"version": 1, "notes": {}}, TODAY.isoformat())
    base = put_journal.build_report(without, [], {"version": 1, "notes": {}}, TODAY.isoformat())
    for key in ("close_advice", "attention", "hold", "streak", "sweep_close", "sweep_delta"):
        assert full[key] == base[key], key
    assert full["counts"]["settled"] == base["counts"]["settled"]
    assert BULLCALL in full and BULLCALL not in base
    assert not any(t.get("strategy") == BULLCALL for t in full["timelines"])


def test_public_text_unchanged_by_bullcall_records():
    records = _numbered(_settled_records() + _open_records())
    without = [r for r in records if not _is_bullcall(r)]
    marks = {r["id"]: {"cost": 0.5, "pnl": 10.0, "underlying": 100.0, "dist_pct": 5.0,
                       "flags": {"stop": False, "take_profit": False}, "stale": False}
             for r in records if r["status"] == "open"}
    with_bullcall = spread_report.render_public_report(records, marks, TODAY.isoformat())
    assert with_bullcall.encode("utf-8") == spread_report.render_public_report(
        without, marks, TODAY.isoformat()).encode("utf-8")
    for token in BULLCALL_TOKENS:
        assert token not in with_bullcall, token
    assert not spread_report.public_records([r for r in records if _is_bullcall(r)])
