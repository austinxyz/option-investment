"""Tests for scripts/put_journal_whatif.py (synthetic data only)."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import put_journal_stats as pjs  # noqa: E402
import put_journal_whatif as wi  # noqa: E402


def mrow(captured=0.1, delta=0.1, date="2026-10-13", cost_to_close=0.3,
         earnings="no", itm=False, stop_flag=False, days_left=10,
         data_ok=True, error=None, **kw):
    d = {"id": "a", "strategy": "bull_put_spread", "date": date,
         "captured": captured, "delta": delta, "cost_to_close": cost_to_close,
         "earnings": earnings, "itm": itm, "stop_flag": stop_flag,
         "days_left": days_left, "data_ok": data_ok, "error": error,
         "data_quality": "live"}
    d.update(kw)
    return d


def mcase(id="a", pnl=50.0, rows=None, credit=0.5, contracts=1,
          comparable=None):
    rows = rows if rows is not None else []
    return {"id": id, "strategy": "bull_put_spread", "ticker": "AAA",
            "expiry": "2026-10-23", "contracts": contracts, "credit": credit,
            "status": "expired", "exit_date": "2026-10-23", "pnl": pnl,
            "result": "x", "rows": rows,
            "comparable": bool(rows) if comparable is None else comparable}


def by_value(rows, v):
    return next(r for r in rows if r["value"] == v)


def test_sweep_close_default_value_matches_stats():
    from put_review_rules import review_position
    rows_a = [mrow(captured=0.85)]
    rows_b = [mrow(captured=0.3)]
    for r in rows_a + rows_b:
        r["advice"] = review_position(r)["advice"]
    cases = [mcase("a", rows=rows_a), mcase("b", rows=rows_b)]
    out = wi.sweep_close(cases)
    assert by_value(out, 0.80)["n_triggered"] == pjs.close_advice_stats(cases)["n_close"] == 1


def test_sweep_close_lower_threshold_triggers_earlier_and_more():
    rows = [mrow(captured=0.55, date="2026-10-13", cost_to_close=0.2),
            mrow(captured=0.85, date="2026-10-20", cost_to_close=0.05)]
    c = mcase("a", pnl=10.0, rows=rows, credit=0.5, contracts=2)
    out = wi.sweep_close([c])
    # 0.5: first row -> (0.5-0.2)*100*2 = 60
    assert by_value(out, 0.5)["total_followed"] == 60.0
    # 0.8: second row -> (0.5-0.05)*100*2 = 90
    assert by_value(out, 0.8)["total_followed"] == 90.0
    assert by_value(out, 0.8)["n_triggered"] == 1
    # 0.9: never -> actual
    assert by_value(out, 0.9)["n_triggered"] == 0
    assert by_value(out, 0.9)["total_followed"] == 10.0


def test_sweep_close_followed_replaces_actual_only_when_triggered():
    hit = mcase("a", pnl=100.0, rows=[mrow(captured=0.9, cost_to_close=0.4)],
                credit=0.5)
    miss = mcase("b", pnl=-30.0, rows=[mrow(captured=0.1)])
    out = by_value(wi.sweep_close([hit, miss]), 0.8)
    assert out["n_triggered"] == 1
    assert out["total_actual"] == 70.0
    assert out["total_followed"] == round(10.0 - 30.0, 2)
    assert out["diff"] == round(-20.0 - 70.0, 2)


def test_sweep_close_ignores_non_comparable_cases():
    c = mcase("a", pnl=99.0, rows=[], comparable=False)
    out = wi.sweep_close([c])
    assert all(r["n_comparable"] == 0 and r["total_actual"] == 0 for r in out)


def test_sweep_close_diff_sign():
    c = mcase("a", pnl=-200.0, rows=[mrow(captured=0.9, cost_to_close=0.05)],
              credit=0.5)
    assert by_value(wi.sweep_close([c]), 0.8)["diff"] > 0


def test_sweep_close_does_not_mutate_inputs():
    c = mcase("a", rows=[mrow(captured=0.9)])
    before = copy.deepcopy([c])
    wi.sweep_close([c])
    wi.sweep_delta([c])
    assert [c] == before


def test_sweep_delta_precision_recall_changes_with_threshold():
    flagged_loss = mcase("a", pnl=-50.0, rows=[mrow(delta=0.30)])
    strong_loss = mcase("b", pnl=-50.0, rows=[mrow(delta=0.40)])
    safe_win = mcase("c", pnl=20.0, rows=[mrow(delta=0.30)])
    out = wi.sweep_delta([flagged_loss, strong_loss, safe_win])
    lo = by_value(out, 0.25)
    hi = by_value(out, 0.35)
    assert (lo["n_flagged"], lo["n_trouble"]) == (3, 2)
    assert lo["precision"] == 2 / 3 and lo["recall"] == 1.0
    assert hi["n_flagged"] == 1
    assert hi["precision"] == 1.0 and hi["recall"] == 0.5


def test_sweep_delta_earnings_flag_counts_for_every_threshold():
    c = mcase("a", pnl=-5.0, rows=[mrow(delta=0.01, earnings="yes")])
    out = wi.sweep_delta([c])
    assert [r["n_flagged"] for r in out] == [1, 1, 1]


def test_sweep_delta_zero_denominators_none():
    c = mcase("a", pnl=0.0, rows=[mrow(delta=0.01)])
    r = by_value(wi.sweep_delta([c]), 0.35)
    assert r["n_comparable"] == 1
    assert r["n_flagged"] == 0 and r["n_trouble"] == 0
    assert r["precision"] is None and r["recall"] is None


def test_sweep_empty_cases_returns_rows_with_zeroes():
    close = wi.sweep_close([])
    assert [r["value"] for r in close] == list(wi.CLOSE_GRID)
    assert all(r["n_comparable"] == 0 and r["n_triggered"] == 0 and r["diff"] == 0
               for r in close)
    delta = wi.sweep_delta([])
    assert [r["value"] for r in delta] == list(wi.DELTA_GRID)
    assert all(r["n_flagged"] == 0 and r["precision"] is None
               and r["recall"] is None for r in delta)
