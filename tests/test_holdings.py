"""Tests for scripts/holdings.py. Fictional tickers and numbers only."""
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import holdings
import spread_ledger as ledger_mod


def row(ticker, shares, avg_cost, est=False):
    return SimpleNamespace(ticker=ticker, shares=shares, avg_cost=avg_cost, cost_estimated=est)


def test_real_lots_aggregate_across_accounts_weighted_cost():
    lots = holdings.real_lots(lambda: [row("AAA", 100, 10.0), row("AAA", 300, 20.0, True)], lambda: [])
    (lot,) = lots
    assert lot.shares == 400 and lot.cost_basis == pytest.approx(17.5)
    assert lot.original_cost == lot.reference_price == lot.cost_basis
    assert lot.cost_estimated is True and lot.accounts == 2
    assert lot.source == "real" and lot.lot_id is None and lot.label == "AAA（真实）"
    assert lot.key == ("real", None, "AAA")
    assert lot.ref == {"source": "real", "lot_id": None, "ticker": "AAA"}


def test_real_lots_skip_dash_and_blank_tickers():
    lots = holdings.real_lots(lambda: [row("", 10, 1.0)], lambda: [row("—", 5, 1.0), row("BBB", 5, 2.0)])
    assert [l.ticker for l in lots] == ["BBB"]


def test_stock_and_etf_rows_with_same_ticker_merge():
    (lot,) = holdings.real_lots(lambda: [row("CCC", 100, 10.0)], lambda: [row("CCC", 100, 30.0)])
    assert lot.shares == 200 and lot.cost_basis == pytest.approx(20.0) and lot.accounts == 2


def test_real_lots_only_calls_read_functions(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("write function touched")

    fake = SimpleNamespace(list_active=lambda: [row("DDD", 100, 5.0)],
                           list_etf=lambda: [], add_position=boom, save=boom, write=boom)
    monkeypatch.setitem(sys.modules, "positions", fake)
    assert [l.ticker for l in holdings.real_lots()] == ["DDD"]


def test_real_lots_loader_error_becomes_ledger_error():
    def bad():
        raise OSError("nope")

    with pytest.raises(ledger_mod.LedgerError, match="读取 positions.md 失败"):
        holdings.real_lots(bad, lambda: [])


def _lot(shares, key=("real", None, "AAA")):
    return holdings.HeldLot(source=key[0], lot_id=key[1], ticker=key[2], shares=shares,
                            cost_basis=1.0, original_cost=1.0, reference_price=1.0,
                            cost_estimated=False, accounts=1, label="x")


def test_contracts_available_floors_fractional_shares():
    assert holdings.contracts_available(_lot(36.68), ledger_mod.empty_ledger()) == 0
    assert holdings.contracts_available(_lot(250), ledger_mod.empty_ledger()) == 2


def test_contracts_available_subtracts_open_calls():
    lot = _lot(300)
    led = {"version": 1, "spreads": [
        {"id": 1, "strategy": "covered_call", "status": "open", "contracts": 2,
         "ticker": "AAA", "expiry": "x", "lot_ref": lot.ref},
        {"id": 2, "strategy": "covered_call", "status": "expired", "contracts": 1,
         "ticker": "AAA", "expiry": "y", "lot_ref": lot.ref}]}
    assert holdings.contracts_available(lot, led) == 1
    assert holdings.contracts_available(_lot(100), led) == 0


def test_paper_lots_open_only_with_labels():
    doc = {"version": 1, "next_id": 4, "lots": [
        {"id": 1, "ticker": "AAA", "shares": 100, "original_cost": 10.0, "cost_basis": 9.0,
         "reference_price": 10.0, "source": "manual", "source_ref": None, "status": "open"},
        {"id": 2, "ticker": "BBB", "shares": 200, "original_cost": 20.0, "cost_basis": 19.0,
         "reference_price": 18.0, "source": "assigned_put", "source_ref": 7, "status": "open"},
        {"id": 3, "ticker": "CCC", "shares": 0, "original_cost": 1.0, "cost_basis": 1.0,
         "reference_price": 1.0, "source": "manual", "source_ref": None, "status": "called_away"}]}
    a, b = holdings.paper_lots(doc)
    assert a.label == "AAA（假设 #1）" and a.key == ("manual", 1, "AAA") and a.cost_basis == 9.0
    assert b.label == "BBB（被指派 put #7，持仓 #2）" and b.reference_price == 18.0 and b.original_cost == 20.0


def test_none_cost_row_left_out_of_average_and_marks_estimated():
    (lot,) = holdings.real_lots(lambda: [row("AAA", 100, 10.0), row("AAA", 100, None)], lambda: [])
    assert lot.shares == 200 and lot.cost_basis == pytest.approx(10.0)
    assert lot.original_cost == lot.reference_price == pytest.approx(10.0)
    assert lot.cost_estimated is True


def test_etf_zero_cost_row_does_not_drag_average_to_zero():
    (lot,) = holdings.real_lots(lambda: [row("AAA", 100, 20.0)], lambda: [row("AAA", 100, 0.0)])
    assert lot.cost_basis == pytest.approx(20.0) and lot.cost_estimated is True


def test_all_costs_unknown_gives_zero_cost_estimated_lot():
    (lot,) = holdings.real_lots(lambda: [row("AAA", 100, None)], lambda: [row("BBB", 0, 0.0)])
    assert lot.cost_basis == lot.original_cost == lot.reference_price == 0.0
    assert lot.cost_estimated is True and lot.shares == 100


def test_dash_and_na_tickers_skipped():
    lots = holdings.real_lots(lambda: [row("-", 10, 1.0), row("n/a", 10, 1.0)], lambda: [])
    assert lots == []


def test_malformed_shares_raise_ledger_error():
    with pytest.raises(ledger_mod.LedgerError, match="读取 positions.md 失败"):
        holdings.real_lots(lambda: [row("AAA", "lots", 5.0)], lambda: [])


# --- taxable-lot facts for the collar tax warning ----------------------------

def arow(ticker, shares, account, entry_date="2026-01-05", avg_cost=10.0):
    return SimpleNamespace(ticker=ticker, shares=shares, avg_cost=avg_cost, cost_estimated=False,
                           account=account, entry_date=entry_date)


def test_real_lots_marks_taxable_vs_roth():
    lots = {l.ticker: l for l in holdings.real_lots(
        lambda: [arow("ZZZQ", 100, "Taxable-A"), arow("ZZZR", 100, "Roth-A"),
                 arow("ZZZS", 100, "my ira"), arow("ZZZT", 100, "Solo 401k")],
        lambda: [])}
    assert lots["ZZZQ"].has_taxable_account is True
    assert lots["ZZZR"].has_taxable_account is False
    assert lots["ZZZS"].has_taxable_account is False
    assert lots["ZZZT"].has_taxable_account is False


def test_real_lots_merges_accounts_taxable_if_any_taxable():
    (lot,) = holdings.real_lots(
        lambda: [arow("ZZZQ", 100, "Roth-A"), arow("ZZZQ", 100, "Taxable-A")], lambda: [])
    assert lot.has_taxable_account is True
    (lot,) = holdings.real_lots(
        lambda: [arow("ZZZQ", 100, "Roth-A"), arow("ZZZQ", 100, "Roth-B")], lambda: [])
    assert lot.has_taxable_account is False


def test_real_lots_earliest_entry_date_across_accounts():
    (lot,) = holdings.real_lots(
        lambda: [arow("ZZZQ", 100, "Taxable-A", "2026-03-01"),
                 arow("ZZZQ", 100, "Roth-A", "2025-11-20")], lambda: [])
    assert lot.earliest_entry_date == "2025-11-20"


def test_real_lots_missing_entry_date_is_none():
    # One contributing row without a date makes the whole merged lot's date unknown (None),
    # so the tax warning errs on the side of warning.
    (lot,) = holdings.real_lots(
        lambda: [arow("ZZZQ", 100, "Taxable-A", "2025-01-01"),
                 arow("ZZZQ", 100, "Taxable-B", "")], lambda: [])
    assert lot.earliest_entry_date is None
    (lot,) = holdings.real_lots(lambda: [row("ZZZQ", 100, 10.0)], lambda: [])
    assert lot.earliest_entry_date is None


def test_paper_lots_keep_tax_fields_none():
    doc = {"version": 1, "next_id": 2, "lots": [
        {"id": 1, "ticker": "ZZZQ", "shares": 100, "original_cost": 10.0, "cost_basis": 9.0,
         "reference_price": 10.0, "source": "manual", "source_ref": None, "status": "open"}]}
    (lot,) = holdings.paper_lots(doc)
    assert lot.has_taxable_account is None and lot.earliest_entry_date is None
    assert _lot(100).has_taxable_account is None and _lot(100).earliest_entry_date is None


def test_contracts_available_for_put_ignores_open_calls():
    lot = _lot(350)
    assert holdings.contracts_available_for_put(lot) == 3
    assert holdings.contracts_available_for_put(_lot(36.68)) == 0
    led = {"version": 1, "spreads": [
        {"id": 1, "strategy": "covered_call", "status": "open", "contracts": 3,
         "ticker": "AAA", "expiry": "x", "lot_ref": lot.ref}]}
    assert holdings.contracts_available(lot, led) == 0
    assert holdings.contracts_available_for_put(lot) == 3


def test_contracts_available_for_put_counts_open_collars_and_puts_only():
    lot = _lot(350)
    def rec(i, strategy, status="open", contracts=1):
        return {"id": i, "strategy": strategy, "status": status, "contracts": contracts,
                "ticker": "AAA", "expiry": "x", "lot_ref": lot.ref}
    led = {"version": 1, "spreads": [
        rec(1, "collar"), rec(2, "protective_put", contracts=1), rec(3, "covered_call", contracts=3),
        rec(4, "collar", status="expired"), rec(5, "protective_put", status="closed")]}
    assert holdings.contracts_available_for_put(lot, led) == 1
    other = {**rec(6, "collar"), "lot_ref": {"source": "real", "lot_id": 9, "ticker": "AAA"}}
    led["spreads"].append(other)
    assert holdings.contracts_available_for_put(lot, led) == 1   # other lots do not count
