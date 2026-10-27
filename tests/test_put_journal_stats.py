"""Tests for scripts/put_journal_stats.py (synthetic data only)."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import put_journal_stats as pjs  # noqa: E402


def rec(id="a", status="expired", exit_date="2026-10-23", pnl=50.0,
        credit=0.525, contracts=2, ticker="AAA", expiry="2026-10-23", **kw):
    d = {"id": id, "ticker": ticker, "contracts": contracts, "credit": credit,
         "expiry": expiry, "status": status, "exit_date": exit_date,
         "pnl": pnl, "result": "win" if (pnl or 0) > 0 else "loss"}
    d.update(kw)
    return d


def row(id="a", advice="hold", rule=0, cost_to_close=0.3, data_ok=True,
        data_quality="live", **kw):
    d = {"id": id, "strategy": "bull_put", "ticker": "AAA",
         "expiry": "2026-10-23", "credit": 0.525,
         "cost_to_close": cost_to_close, "advice": advice, "rule": rule,
         "data_ok": data_ok, "data_quality": data_quality}
    d.update(kw)
    return d


def snap(date, rows):
    return {"version": 1, "generated_on": date, "snapshot_kind": "weekly",
            "rules": {}, "positions": rows}


def case(id="a", status="expired", pnl=50.0, exit_date="2026-10-23", rows=None,
         credit=0.525, contracts=2):
    rows = rows or []
    return {"id": id, "strategy": "bull_put", "ticker": "AAA",
            "expiry": "2026-10-23", "contracts": contracts, "credit": credit,
            "status": status, "exit_date": exit_date, "pnl": pnl,
            "result": "x", "rows": rows, "comparable": bool(rows)}


def crow(advice, date="2026-10-13", **kw):
    r = row(advice=advice, **kw)
    r["date"] = date
    return r


def test_build_cases_only_settled():
    recs = [rec("a"), rec("b", status="open", exit_date=None, pnl=None),
            rec("c", status="closed_early")]
    cases = pjs.build_cases(recs, [])
    assert sorted(c["id"] for c in cases) == ["a", "c"]
    assert all(c["comparable"] is False and c["rows"] == [] for c in cases)


def test_build_cases_skips_missing_exit_date():
    assert pjs.build_cases([rec("a", exit_date=None)], []) == []


def test_build_cases_keeps_rows_up_to_and_including_exit_date():
    snaps = [snap("2026-10-20", [row("a")]), snap("2026-10-23", [row("a")]),
             snap("2026-10-24", [row("a")])]
    cases = pjs.build_cases(
        [rec("a", status="closed_early", exit_date="2026-10-23")], snaps)
    assert [r["date"] for r in cases[0]["rows"]] == ["2026-10-20", "2026-10-23"]
    assert cases[0]["comparable"] is True


def test_closed_early_same_day_close_advice_counts():
    snaps = [snap("2026-10-23", [row("a", advice="close", cost_to_close=0.105)])]
    cases = pjs.build_cases(
        [rec("a", status="closed_early", exit_date="2026-10-23", pnl=50.0)], snaps)
    c = cases[0]
    assert c["comparable"] is True
    s = pjs.close_advice_stats(cases)
    assert s["n_close"] == 1 and s["items"][0]["date"] == "2026-10-23"
    assert pjs.consistency(c) == "一致"


def test_expired_expiry_day_none_rows_still_excluded():
    snaps = [snap("2026-10-23", [row("a", data_quality="none")])]
    cases = pjs.build_cases(
        [rec("a", status="expired", exit_date="2026-10-23", expiry="2026-10-23")],
        snaps)
    assert cases[0]["rows"] == [] and cases[0]["comparable"] is False


def test_build_cases_drops_data_quality_none_rows():
    snaps = [snap("2026-10-13", [row("a", data_quality="none")])]
    cases = pjs.build_cases([rec("a")], snaps)
    assert cases[0]["rows"] == [] and cases[0]["comparable"] is False


def test_build_cases_row_gets_date_and_sorted_without_mutation():
    snaps = [snap("2026-10-20", [row("a", advice="close")]),
             snap("2026-10-13", [row("a", advice="hold"), row("zzz")])]
    before = copy.deepcopy(snaps)
    cases = pjs.build_cases([rec("a")], snaps)
    assert [(r["date"], r["advice"]) for r in cases[0]["rows"]] == [
        ("2026-10-13", "hold"), ("2026-10-20", "close")]
    assert snaps == before
    assert cases[0]["strategy"] == "bull_put_spread"


def test_pnl_if_closed_values():
    c = case(credit=0.525, contracts=2)
    assert pjs.pnl_if_closed(c, {"cost_to_close": 0.105}) == 84.0
    assert pjs.pnl_if_closed(c, {"cost_to_close": 0.725}) == -40.0


def test_close_advice_stats_uses_earliest_close_row():
    c = case("a", pnl=50.0, rows=[
        crow("hold", "2026-10-06"),
        crow("close", "2026-10-13", cost_to_close=0.105, data_ok=False),
        crow("close", "2026-10-20", cost_to_close=0.3)])
    s = pjs.close_advice_stats([c])
    assert s["n_comparable"] == 1 and s["n_close"] == 1
    item = s["items"][0]
    assert item["date"] == "2026-10-13"
    assert item["pnl_if"] == 84.0 and item["actual"] == 50.0
    assert item["diff"] == 34.0 and item["stale"] is True
    assert s["better"] == 1 and s["stale_rows"] == 1
    assert s["mean_diff"] == 34.0 and s["total_diff"] == 34.0


def test_close_advice_stats_empty():
    s = pjs.close_advice_stats([case("a", rows=[crow("hold")]), case("b")])
    assert s["n_comparable"] == 1 and s["n_close"] == 0
    assert s["mean_diff"] is None and s["items"] == []
    assert s["total_diff"] == 0 and s["better"] == 0


def test_attention_stats_precision_recall():
    cases = [
        case("A", pnl=-10, rows=[crow("attention", rule=2)]),
        case("B", pnl=10, rows=[crow("attention", rule=2)]),
        case("C", pnl=-10, rows=[crow("hold")]),
        case("D", pnl=10, rows=[crow("hold")]),
    ]
    s = pjs.attention_stats(cases)
    assert s["n_comparable"] == 4 and s["n_flagged"] == 2
    assert s["n_trouble"] == 2 and s["flagged_and_trouble"] == 1
    assert s["precision"] == 0.5 and s["recall"] == 0.5


def test_attention_stats_zero_denominators_are_none():
    s = pjs.attention_stats([case("a", pnl=10, rows=[crow("hold")])])
    assert s["precision"] is None and s["recall"] is None
    assert s["by_rule"] == {}


def test_attention_by_rule_uses_first_attention_row():
    c = case("a", pnl=-5, rows=[crow("hold", "2026-10-06"),
                                crow("attention", "2026-10-13", rule=2),
                                crow("attention", "2026-10-20", rule=4)])
    s = pjs.attention_stats([c])
    assert list(s["by_rule"]) == [2]
    assert s["by_rule"][2] == {"flagged": 1, "trouble": 1, "precision": 1.0}


def test_hold_stats_last_row():
    cases = [
        case("a", pnl=10, rows=[crow("close", "2026-10-06"), crow("hold", "2026-10-13")]),
        case("b", pnl=-10, rows=[crow("hold", "2026-10-06"), crow("close", "2026-10-13")]),
        case("c", pnl=-3, rows=[crow("hold")]),
        case("d", pnl=0, rows=[crow("hold")]),
    ]
    s = pjs.hold_stats(cases)
    assert s == {"n": 3, "wins": 1, "rate": 1 / 3}
    assert pjs.hold_stats([])["rate"] is None


def _series(outcomes):
    return [case(f"c{i}", pnl=(10 if w else -10), exit_date=f"2026-10-{i + 1:02d}")
            for i, w in enumerate(outcomes)]


def test_streak_stats_after_three_wins():
    # W W W L W W W W : i=3 (L) counts; i=7 (W, after W W W) counts; i=6 (W) after W W L? no
    s = pjs.streak_stats(_series([1, 1, 1, 0, 1, 1, 1, 1]))
    # eligible: i=3 (prev 1,1,1) -> loss; i=6 (prev 4,5? idx3..5 = 0,1,1) no;
    # i=7 (prev idx4..6 = 1,1,1) -> win
    assert s["n_settled"] == 8 and s["n"] == 2 and s["wins"] == 1
    assert s["rate"] == 0.5 and s["baseline_rate"] == 7 / 8


def test_streak_stats_short_series_has_n_zero_and_none_rate():
    s = pjs.streak_stats(_series([1, 1, 1]))
    assert s["n"] == 0 and s["wins"] == 0 and s["rate"] is None
    assert s["baseline_rate"] == 1.0
    e = pjs.streak_stats([])
    assert e["baseline_rate"] is None and e["n_settled"] == 0


def test_streak_ordering_ties_by_id():
    cs = [case("d", pnl=-1, exit_date="2026-10-23"),
          case("c", pnl=1, exit_date="2026-10-23"),
          case("b", pnl=1, exit_date="2026-10-23"),
          case("a", pnl=1, exit_date="2026-10-23")]
    s = pjs.streak_stats(cs)
    assert s["n"] == 1 and s["wins"] == 0


def test_zero_pnl_is_neither_win_nor_trouble():
    z = case("z", pnl=0, rows=[crow("attention", rule=2)])
    s = pjs.attention_stats([z])
    assert s["n_comparable"] == 1 and s["n_flagged"] == 1
    assert s["n_trouble"] == 0 and s["flagged_and_trouble"] == 0
    h = pjs.hold_stats([case("h", pnl=0, rows=[crow("hold")])])
    assert h["n"] == 1 and h["wins"] == 0


def test_consistency_variants():
    close = [crow("close")]
    hold = [crow("hold")]
    assert pjs.consistency(case(status="closed_early", rows=close)) == "一致"
    assert pjs.consistency(case(status="closed_early", rows=hold)) == "不一致"
    assert pjs.consistency(case(status="expired", rows=close)) == "不一致"
    assert pjs.consistency(case(status="expired", rows=hold)) == "一致"
    assert pjs.consistency(case(status="expired", rows=[])) == "无建议可比"


def test_sample_warning():
    assert pjs.sample_warning(30) is None
    assert pjs.sample_warning(31) is None
    assert pjs.sample_warning(5) == "样本不足（5 < 30），别下结论"
    assert pjs.MIN_SAMPLE == 30


def test_journal_build_cases_skips_new_strategies():
    base = {"ticker": "ZZZQ", "expiry": "2026-10-23", "contracts": 1, "status": "expired",
            "exit_date": "2026-10-23", "pnl": 10.0, "result": "protected",
            "put_strike": 90.0, "net_credit": 0.5}
    records = [{**base, "id": 1, "strategy": "collar"},
               {**base, "id": 2, "strategy": "protective_put"}]
    assert pjs.build_cases(records, []) == []


def test_journal_build_cases_skips_condor():
    # a condor has no credit / short_strike: build_cases must skip it, not KeyError
    condor = {"id": 3, "strategy": "iron_condor", "ticker": "ZZZQ", "expiry": "2026-10-23",
              "contracts": 1, "status": "expired", "exit_date": "2026-10-23", "pnl": 150.0,
              "result": "win", "net_credit": 1.5, "width": 5.0, "breached_side": "none"}
    rows = [row(id=3, advice="close", rule=3)]
    assert pjs.build_cases([condor], [snap("2026-10-20", rows)]) == []
    assert [c["id"] for c in pjs.build_cases([condor, rec(id="a")], [])] == ["a"]


# ---- bull call debit spread (bull call plan task 6) ---------------------------------

def _bullcall(id, pnl, captured, debit=1.9, width=5.0, status="expired"):
    return {"id": id, "strategy": "bull_call_spread", "ticker": "ZZZQ", "expiry": "2026-10-23",
            "contracts": 1, "status": status, "exit_date": "2026-10-23", "pnl": pnl,
            "result": "win" if pnl > 0 else "loss", "long_call_strike": 100.0,
            "short_call_strike": 100.0 + width, "width": width, "debit": debit,
            "fraction_of_max_captured": captured}


def test_journal_build_cases_skips_bullcall():
    # a bull call has no credit / short_strike: build_cases must skip it, not KeyError
    bc = _bullcall(4, 110.0, 0.3548)
    rows = [row(id=4, advice="close", rule=3)]
    assert pjs.build_cases([bc], [snap("2026-10-20", rows)]) == []
    assert [c["id"] for c in pjs.build_cases([bc, rec(id="a")], [])] == ["a"]
    assert "bull_call_spread" in pjs.NOT_CASE_STRATEGIES


def test_condor_and_bullcall_stats_do_not_mix():
    condor = {"id": 3, "strategy": "iron_condor", "ticker": "ZZZC", "expiry": "2026-10-23",
              "contracts": 1, "status": "expired", "exit_date": "2026-10-23", "pnl": 150.0,
              "result": "win", "net_credit": 1.5, "width": 5.0, "breached_side": "none"}
    # debit 2.00 / width 5 -> 0.40 required; the one winner captured 0.5
    bcs = [_bullcall(4, 150.0, 0.5, debit=2.0), _bullcall(5, -200.0, -0.6667, debit=2.0)]
    c = pjs.condor_stats([condor, *bcs])
    assert c["n"] == 1 and c["total_pnl"] == 150.0
    b = pjs.bullcall_stats([condor, *bcs])
    assert b["n"] == 2 and b["wins"] == 1 and b["win_rate"] == 0.5
    assert abs(b["required_avg"] - 0.40) < 1e-12
    assert abs(b["captured_avg_winners"] - 0.5) < 1e-12
    assert b["total_pnl"] == -50.0 and "breached" not in b
    assert pjs.bullcall_stats([condor]) is None
    assert pjs.condor_stats(bcs) is None
    assert pjs.bullcall_stats([_bullcall(6, 0.0, None, status="open")]) is None
