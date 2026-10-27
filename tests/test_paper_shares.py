"""Tests for scripts/paper_shares.py (Wheel sync). Fictional numbers only."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import paper_shares as ps
import spread_ledger as lm


def csp(strike=50.0, credit=1.0, contracts=1, close=45.0, expiry="2026-10-23", ticker="ABC"):
    return {"strategy": "cash_secured_put", "ticker": ticker, "contracts": contracts,
            "expiry": expiry, "short_strike": strike, "credit": credit, "status": "expired",
            "result": "assigned", "exit_underlying": close, "exit_date": expiry,
            "snapshot_date": "2026-10-16"}


def cc(lot_id, *, status="expired", result="win", credit=1.0, contracts=1, strike=60.0,
       exit_cost=0.0, source="assigned_put", expiry="2026-10-30", ref=50.0, stock_leg=None):
    rec = {"strategy": "covered_call", "ticker": "ABC", "contracts": contracts, "expiry": expiry,
           "short_strike": strike, "credit": credit, "status": status, "result": result,
           "exit_cost": exit_cost, "exit_date": expiry, "snapshot_date": "2026-10-23",
           "lot_ref": {"source": source, "lot_id": lot_id, "ticker": "ABC"},
           "reference_price": ref, "cost_basis": ref, "covered_shares": 100 * contracts}
    if stock_leg is not None:
        rec["stock_leg_pnl"] = stock_leg
    return rec


def seed(tmp_path, *records):
    led = lm.empty_ledger()
    for r in records:
        led, _ = lm.add_spread(led, r)
    lm.save(tmp_path / "spreads.json", led)


def lots(tmp_path):
    return ps.load_lots(tmp_path / "paper_shares.json")["lots"]


def ledger(tmp_path):
    return lm.load(tmp_path / "spreads.json")


def test_add_remove_lot_roundtrip_and_bak(tmp_path):
    path = tmp_path / "paper_shares.json"
    doc, lot = ps.add_lot(ps.load_lots(path), ticker="ABC", shares=200, cost=12.5, opened_on="2026-10-21")
    assert lot["id"] == 1 and lot["original_cost"] == lot["cost_basis"] == lot["reference_price"] == 12.5
    assert lot["source"] == "manual" and lot["status"] == "open" and doc["next_id"] == 2
    ps.save_lots(path, doc)
    ps.save_lots(path, doc)
    assert path.with_name(path.name + ".bak").exists()
    again = ps.load_lots(path)
    assert again == doc
    removed = ps.remove_lot(again, lm.empty_ledger(), 1)
    assert removed["lots"] == [] and len(again["lots"]) == 1
    with pytest.raises(lm.LedgerError):
        ps.remove_lot(again, lm.empty_ledger(), 99)


def test_load_corrupt_raises_and_keeps_file(tmp_path):
    path = tmp_path / "paper_shares.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(lm.LedgerError, match=r"\.bak"):
        ps.load_lots(path)
    assert path.read_text(encoding="utf-8") == "{oops"


def test_remove_lot_refused_when_calls_reference_it():
    doc, lot = ps.add_lot(ps.load_lots(Path("/nonexistent/x.json")), ticker="ABC", shares=100,
                          cost=10.0, opened_on="2026-10-21")
    led, _ = lm.add_spread(lm.empty_ledger(), cc(lot["id"], source="manual", status="closed_early"))
    with pytest.raises(lm.LedgerError):
        ps.remove_lot(doc, led, lot["id"])


def test_sync_creates_lot_from_assigned_put_once(tmp_path):
    seed(tmp_path, csp(strike=50.0, credit=1.0, contracts=2, close=45.0))
    assert ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    assert lot["shares"] == 200 and lot["original_cost"] == lot["cost_basis"] == 49.0
    assert lot["reference_price"] == 45.0 and lot["opened_on"] == "2026-10-23"
    assert lot["source"] == "assigned_put" and lot["source_ref"] == 1 and lot["status"] == "open"
    assert ps.sync(tmp_path) == [] and len(lots(tmp_path)) == 1


def test_sync_noop_creates_no_files(tmp_path):
    assert ps.sync(tmp_path) == []
    assert not (tmp_path / "paper_shares.json").exists()
    seed(tmp_path, {**csp(close=60.0), "result": "win"})
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert ps.sync(tmp_path) == []
    assert not (tmp_path / "paper_shares.json").exists()
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_sync_applies_expired_call_premium_to_cost_basis(tmp_path):
    seed(tmp_path, csp(contracts=2), cc(1, credit=1.0, contracts=1))
    ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    assert lot["cost_basis"] == pytest.approx(49.0 - 100.0 / 200) and lot["shares"] == 200
    assert lot["applied_ids"] == [2]
    assert ledger(tmp_path)["spreads"][1]["lot_applied"] is True


def test_sync_closed_early_uses_net_premium(tmp_path):
    seed(tmp_path, csp(), cc(1, status="closed_early", result="win", credit=1.0, exit_cost=0.4))
    ps.sync(tmp_path)
    assert lots(tmp_path)[0]["cost_basis"] == pytest.approx(49.0 - 0.6)


def test_sync_called_away_partial_reduces_shares(tmp_path):
    seed(tmp_path, csp(contracts=3),
         cc(1, result="called_away", credit=1.0, contracts=1, stock_leg=1000.0))
    ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    assert lot["shares"] == 200 and lot["status"] == "open"
    assert lot["realized_stock_pnl"] == 1000.0
    assert lot["cost_basis"] == pytest.approx(49.0 - 100.0 / 300)


def test_sync_called_away_full_closes_lot(tmp_path):
    seed(tmp_path, csp(), cc(1, result="called_away", stock_leg=1000.0, expiry="2026-10-30"))
    ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    assert lot["shares"] == 0 and lot["status"] == "called_away" and lot["closed_on"] == "2026-10-30"


def test_sync_twice_does_not_double_apply(tmp_path):
    seed(tmp_path, csp(contracts=2), cc(1))
    ps.sync(tmp_path)
    first = lots(tmp_path)
    assert ps.sync(tmp_path) == []
    assert lots(tmp_path) == first


def test_sync_recovers_when_flag_save_was_lost(tmp_path):
    seed(tmp_path, csp(contracts=2), cc(1))
    ps.sync(tmp_path)
    first = lots(tmp_path)
    led = ledger(tmp_path)
    led["spreads"][1].pop("lot_applied")
    lm.save(tmp_path / "spreads.json", led)
    ps.sync(tmp_path)
    assert lots(tmp_path) == first
    assert ledger(tmp_path)["spreads"][1]["lot_applied"] is True


def test_sync_missing_lot_gives_message_and_sets_flag(tmp_path):
    seed(tmp_path, cc(42, source="manual"))
    msgs = ps.sync(tmp_path)
    assert msgs and "42" in msgs[0]
    assert ledger(tmp_path)["spreads"][0]["lot_applied"] is True
    assert not (tmp_path / "paper_shares.json").exists()


def test_sync_skips_real_source_calls(tmp_path):
    seed(tmp_path, cc(None, source="real"))
    assert ps.sync(tmp_path) == []
    assert "lot_applied" not in ledger(tmp_path)["spreads"][0]


def test_sync_corrupt_lots_raises_and_preserves(tmp_path):
    seed(tmp_path, csp())
    (tmp_path / "paper_shares.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(lm.LedgerError):
        ps.sync(tmp_path)
    assert (tmp_path / "paper_shares.json").read_text(encoding="utf-8") == "{bad"


def _good_doc():
    doc, _ = ps.add_lot(ps.empty_lots(), ticker="ABC", shares=100, cost=10.0, opened_on="2026-10-21")
    return doc


@pytest.mark.parametrize("mutate", [
    lambda d: d["lots"][0].pop("source"),
    lambda d: d.update(lots={"a": 1}),
    lambda d: d.pop("next_id"),
    lambda d: d.update(next_id="2"),
    lambda d: d["lots"][0].update(shares="many"),
    lambda d: d["lots"].append(5),
])
def test_load_lots_rejects_malformed_shape_and_keeps_file(tmp_path, mutate):
    import json
    doc = _good_doc()
    mutate(doc)
    path = tmp_path / "paper_shares.json"
    text = json.dumps(doc)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(lm.LedgerError, match=r"\.bak"):
        ps.load_lots(path)
    assert path.read_text(encoding="utf-8") == text


def test_load_lots_defaults_retired_refs_for_older_files(tmp_path):
    import json
    doc = _good_doc()
    doc.pop("retired_source_refs", None)
    path = tmp_path / "paper_shares.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert ps.load_lots(path)["retired_source_refs"] == []


def test_removed_assigned_lot_is_not_recreated(tmp_path):
    seed(tmp_path, csp(contracts=2))
    ps.sync(tmp_path)
    doc = ps.load_lots(tmp_path / "paper_shares.json")
    ps.save_lots(tmp_path / "paper_shares.json", ps.remove_lot(doc, ledger(tmp_path), 1))
    assert ps.sync(tmp_path) == [] and ps.sync(tmp_path) == []
    assert lots(tmp_path) == []


def test_removing_manual_lot_does_not_retire_anything():
    doc = ps.remove_lot(_good_doc(), lm.empty_ledger(), 1)
    assert doc["retired_source_refs"] == [] and doc["lots"] == []


def test_apply_call_ignores_lot_with_other_ticker(tmp_path):
    seed(tmp_path, csp(contracts=2), {**cc(1), "ticker": "ABC"})
    ps.sync(tmp_path)  # creates lot #1 (ABC) and applies call #2
    led2 = ledger(tmp_path)
    bad = {**led2["spreads"][1], "lot_applied": False,
           "lot_ref": {"source": "assigned_put", "lot_id": 1, "ticker": "ZZZ"}}
    led2 = {**led2, "spreads": [led2["spreads"][0], bad]}
    doc = ps.load_lots(tmp_path / "paper_shares.json")
    _, flagged, msgs = ps._apply_calls(doc, led2)
    assert flagged == [2] and "找不到假设持仓" in msgs[0]


# ---- collar / protective put write-back (ZZZQ, invented) --------------------------

def collar(lot_id, *, strategy="collar", status="expired", result="expired_worthless",
           net_credit=0.5, contracts=1, pnl=None, stock_leg=0.0, source="manual",
           expiry="2026-10-30"):
    is_collar = strategy == "collar"
    rec = {"strategy": strategy, "ticker": "ABC", "contracts": contracts, "expiry": expiry,
           "put_strike": 45.0, "put_debit": 1.0, "call_strike": 60.0 if is_collar else None,
           "call_credit": 1.0 + net_credit if is_collar else 0.0,
           "net_credit": net_credit, "status": status, "result": result, "exit_date": expiry,
           "snapshot_date": "2026-10-23", "reference_price": 50.0, "cost_basis": 50.0,
           "covered_shares": 100 * contracts, "stock_leg_pnl": stock_leg,
           "lot_ref": {"source": source, "lot_id": lot_id, "ticker": "ABC"}}
    if pnl == "omit":
        return rec
    # Settlement writes the option-leg pnl; default to the net premium (nothing in the money).
    rec["pnl"] = round(net_credit * 100 * contracts, 2) if pnl is None else pnl
    return rec


def seed_lot(tmp_path, shares=300, cost=50.0):
    doc, lot = ps.add_lot(ps.load_lots(tmp_path / "paper_shares.json"), ticker="ABC",
                          shares=shares, cost=cost, opened_on="2026-10-01")
    ps.save_lots(tmp_path / "paper_shares.json", doc)
    return lot["id"]


def test_paper_shares_collar_called_away_removes_shares(tmp_path):
    lid = seed_lot(tmp_path, shares=300)
    seed(tmp_path, collar(lid, result="called_away", stock_leg=1000.0))
    ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    # 300 - 100 sold; net credit $50 over 300 shares lowers cost by 0.1667
    assert lot["shares"] == 200 and lot["status"] == "open"
    assert lot["realized_stock_pnl"] == 1000.0
    assert lot["cost_basis"] == pytest.approx(50.0 - 50.0 / 300, abs=1e-5)
    assert ledger(tmp_path)["spreads"][0]["lot_applied"] is True


def test_paper_shares_collar_called_away_last_lot_closes(tmp_path):
    lid = seed_lot(tmp_path, shares=100)
    seed(tmp_path, collar(lid, result="called_away", stock_leg=1000.0))
    ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    assert lot["shares"] == 0 and lot["status"] == "called_away"


def test_paper_shares_collar_not_called_adjusts_cost_by_signed_net_credit(tmp_path):
    lid = seed_lot(tmp_path, shares=100, cost=50.0)
    seed(tmp_path, collar(lid, net_credit=-0.8))  # net debit $80 raises cost
    ps.sync(tmp_path)
    assert lots(tmp_path)[0]["cost_basis"] == pytest.approx(50.8)
    other = tmp_path / "b"
    other.mkdir()
    lid2 = seed_lot(other, shares=100, cost=50.0)
    seed(other, collar(lid2, net_credit=0.5))  # net credit $50 lowers it
    ps.sync(other)
    assert lots(other)[0]["cost_basis"] == pytest.approx(49.5)


def test_paper_shares_put_itm_keeps_shares(tmp_path):
    lid = seed_lot(tmp_path, shares=100)
    seed(tmp_path, collar(lid, result="protected", net_credit=0.5, stock_leg=-500.0))
    ps.sync(tmp_path)
    (lot,) = lots(tmp_path)
    assert lot["shares"] == 100 and lot["status"] == "open"
    assert lot["realized_stock_pnl"] == 0.0  # only called_away books the stock leg
    assert lot["cost_basis"] == pytest.approx(49.5)


def test_paper_shares_collar_closed_early_uses_realised_option_pnl(tmp_path):
    lid = seed_lot(tmp_path, shares=100)
    seed(tmp_path, collar(lid, status="closed_early", result="closed", pnl=-70.0))
    ps.sync(tmp_path)
    assert lots(tmp_path)[0]["cost_basis"] == pytest.approx(50.7)


def test_paper_shares_protective_put_never_touches_lots(tmp_path):
    lid = seed_lot(tmp_path, shares=100)
    seed(tmp_path, collar(lid, strategy="protective_put", result="protected", net_credit=-2.0))
    before = (tmp_path / "paper_shares.json").read_text(encoding="utf-8")
    assert ps.sync(tmp_path) == []
    assert (tmp_path / "paper_shares.json").read_text(encoding="utf-8") == before
    assert "lot_applied" not in ledger(tmp_path)["spreads"][0]


def test_paper_shares_collar_protected_put_gain_lowers_cost(tmp_path):
    lid = seed_lot(tmp_path, shares=100, cost=100.0)
    # net debit 0.50, close 85 vs put 90: (-0.50 + 5) x 100 = +$450 option leg
    seed(tmp_path, collar(lid, result="protected", net_credit=-0.5, pnl=450.0))
    ps.sync(tmp_path)
    assert lots(tmp_path)[0]["cost_basis"] == pytest.approx(95.5)


def test_paper_shares_collar_expired_worthless_net_debit_raises_cost(tmp_path):
    lid = seed_lot(tmp_path, shares=100, cost=100.0)
    seed(tmp_path, collar(lid, result="expired_worthless", net_credit=-0.5, pnl=-50.0))
    ps.sync(tmp_path)
    assert lots(tmp_path)[0]["cost_basis"] == pytest.approx(100.5)


@pytest.mark.parametrize("status", ["expired", "closed_early"])
def test_paper_shares_collar_without_pnl_raises_ledger_error(tmp_path, status):
    lid = seed_lot(tmp_path, shares=100)
    seed(tmp_path, collar(lid, status=status, pnl="omit"))
    with pytest.raises(lm.LedgerError):
        ps.sync(tmp_path)
    assert lots(tmp_path)[0]["cost_basis"] == 50.0


def test_sync_messages_say_collar_for_collars_and_covered_call_for_calls(tmp_path):
    lid = seed_lot(tmp_path, shares=300)
    seed(tmp_path, collar(lid), cc(lid, source="manual", expiry="2026-10-31"))
    out = ps.sync(tmp_path)
    assert any(m.startswith("领口 #1") for m in out)
    assert any(m.startswith("备兑看涨 #2") for m in out)
