"""Tests for scripts/spread_ledger.py — uses tmp_path only, never the real data dir."""
from pathlib import Path
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_ledger as led


def _rec(ticker="ABC", expiry="2026-10-23", **extra):
    base = {"ticker": ticker, "expiry": expiry, "status": "open", "contracts": 1}
    base.update(extra)
    return base


def test_load_missing_file_returns_empty_ledger(tmp_path):
    assert led.load(tmp_path / "spreads.json") == {"version": 1, "spreads": []}


def test_save_then_load_roundtrip_with_chinese_text(tmp_path):
    p = tmp_path / "paper" / "spreads.json"
    ledger, _ = led.add_spread(led.empty_ledger(), _rec(notes="测试"))
    led.save(p, ledger)
    assert led.load(p) == ledger
    assert "测试" in p.read_text(encoding="utf-8")  # not \u-escaped


def test_save_backs_up_previous_version(tmp_path):
    p = tmp_path / "spreads.json"
    first, _ = led.add_spread(led.empty_ledger(), _rec("AAA"))
    led.save(p, first)
    second, _ = led.add_spread(first, _rec("BBB"))
    led.save(p, second)
    bak = tmp_path / "spreads.json.bak"
    assert json.loads(bak.read_text(encoding="utf-8")) == first
    assert led.load(p) == second
    assert not (tmp_path / "spreads.json.tmp").exists()


def test_load_corrupt_json_raises_and_points_to_backup(tmp_path):
    p = tmp_path / "spreads.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(led.LedgerError) as exc:
        led.load(p)
    assert ".bak" in str(exc.value)
    assert p.read_text(encoding="utf-8") == "{not json"  # untouched


def test_load_wrong_shape_raises(tmp_path):
    p = tmp_path / "spreads.json"
    p.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(led.LedgerError):
        led.load(p)


def test_add_assigns_incrementing_ids_and_does_not_mutate_input():
    empty = led.empty_ledger()
    one, r1 = led.add_spread(empty, _rec("AAA"))
    two, r2 = led.add_spread(one, _rec("BBB"))
    assert (r1["id"], r2["id"]) == (1, 2)
    assert empty["spreads"] == []
    assert len(one["spreads"]) == 1 and len(two["spreads"]) == 2


def test_duplicate_same_ticker_same_expiry_rejected_any_status_and_case():
    ledger, _ = led.add_spread(led.empty_ledger(), _rec("abc", status="closed_early"))
    with pytest.raises(led.LedgerError) as exc:
        led.add_spread(ledger, _rec("ABC"))
    assert "#1" in str(exc.value)


def test_same_ticker_different_expiry_allowed():
    ledger, _ = led.add_spread(led.empty_ledger(), _rec("ABC", "2026-10-23"))
    ledger, rec = led.add_spread(ledger, _rec("ABC", "2026-10-30"))
    assert rec["id"] == 2


def test_strategy_of_defaults_to_bull_put_for_old_records():
    assert led.strategy_of(_rec()) == "bull_put_spread"


def test_strategy_of_reads_explicit_value():
    assert led.strategy_of({"strategy": "cash_secured_put"}) == "cash_secured_put"


def test_find_duplicate_ignores_strategy():
    ledger, _ = led.add_spread(
        led.empty_ledger(), _rec("ABC", strategy="cash_secured_put"))
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _rec("ABC"))


def test_update_spread_returns_new_ledger_and_leaves_original():
    ledger, rec = led.add_spread(led.empty_ledger(), _rec())
    new = led.update_spread(ledger, rec["id"], status="expired", pnl=100.0)
    assert led.get_spread(new, 1)["status"] == "expired"
    assert led.get_spread(new, 1)["pnl"] == 100.0
    assert led.get_spread(ledger, 1)["status"] == "open"


def test_update_and_get_unknown_id_raise():
    with pytest.raises(led.LedgerError):
        led.update_spread(led.empty_ledger(), 9, status="x")
    with pytest.raises(led.LedgerError):
        led.get_spread(led.empty_ledger(), 9)


# --- covered-call ledger rules ----------------------------------------------

def _lot(lot_id="L1", ticker="ABC", source="acct1"):
    return {"source": source, "lot_id": lot_id, "ticker": ticker}


def _cc(lot_id="L1", **extra):
    return _rec(strategy=led.STRATEGY_COVERED_CALL, lot_ref=_lot(lot_id), **extra)


def test_duplicate_blocks_other_strategy_same_expiry_for_covered_call():
    ledger, _ = led.add_spread(led.empty_ledger(), _rec(strategy=led.STRATEGY_CSP))
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _cc())


def test_duplicate_allows_second_covered_call_on_different_lot():
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1"))
    ledger, stored = led.add_spread(ledger, _cc("L2"))
    assert stored["id"] == 2


def test_duplicate_blocks_same_lot_same_expiry():
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1"))
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _cc("L1"))


def test_existing_duplicate_rule_unchanged_without_lot_ref():
    ledger, _ = led.add_spread(led.empty_ledger(), _rec())
    assert led.find_duplicate(ledger, "abc", "2026-10-23") is not None
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _rec())


def test_covered_contracts_counts_only_open_matching_lot():
    ledger = led.empty_ledger()
    for rec in (_cc("L1", contracts=2), _cc("L1", expiry="2026-10-30", contracts=1,
                                             status="expired"),
                _cc("L2", expiry="2026-11-06", contracts=3),
                _rec(expiry="2026-11-13", contracts=5)):
        ledger, _ = led.add_spread(ledger, rec)
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 2
    assert led.covered_contracts(ledger, ("acct1", "L2", "ABC")) == 3
    assert led.covered_contracts(ledger, ("acct1", "L9", "ABC")) == 0


def test_set_broker_merges_only_broker_key():
    ledger = {"version": 1, "spreads": [
        {"id": 1, "ticker": "A", "status": "open", "credit": 1.0,
         "broker": {"status": "pending", "limit": 1.0}},
        {"id": 2, "ticker": "B", "status": "open"}]}
    new = led.set_broker(ledger, 1, {"status": "submitted", "order_id": 7})
    assert new["spreads"][0]["broker"] == {"status": "submitted", "limit": 1.0, "order_id": 7}
    assert {k: v for k, v in new["spreads"][0].items() if k != "broker"} == \
        {"id": 1, "ticker": "A", "status": "open", "credit": 1.0}
    assert new["spreads"][1] == ledger["spreads"][1]
    assert ledger["spreads"][0]["broker"]["status"] == "pending"  # input untouched
    with pytest.raises(led.LedgerError):
        led.set_broker(ledger, 9, {"status": "x"})
    with pytest.raises(led.LedgerError):
        led.set_broker(ledger, 1, {"credit": 5.0})  # only known broker fields
    with pytest.raises(led.LedgerError):
        led.set_broker(ledger, 1, {"status": "weird"})


def test_set_broker_rejects_long_account_tail():
    ledger = {"version": 1, "spreads": [{"id": 1, "ticker": "A", "status": "open"}]}
    with pytest.raises(ValueError):
        led.set_broker(ledger, 1, {"account_tail": "DU0000001"})
    assert led.set_broker(ledger, 1, {"account_tail": "0001"})["spreads"][0]["broker"] ==         {"account_tail": "0001"}


def test_set_broker_close_order_validated_and_merged():
    ledger, _ = led.add_spread(led.empty_ledger(), _rec(broker={"status": "filled", "order_id": 7}))
    new = led.set_broker(ledger, 1, {"close_order": {"status": "pending", "order_ref": "rwh-1-X",
                                                     "limit": 0.5}})
    assert new["spreads"][0]["broker"] == {"status": "filled", "order_id": 7, "close_order": {
        "status": "pending", "order_ref": "rwh-1-X", "limit": 0.5}}
    merged = led.set_broker(new, 1, {"close_order": {"status": "submitted", "order_id": 8}})
    assert merged["spreads"][0]["broker"]["close_order"] == {
        "status": "submitted", "order_ref": "rwh-1-X", "limit": 0.5, "order_id": 8}
    assert merged["spreads"][0]["broker"]["status"] == "filled"
    assert new["spreads"][0]["broker"]["close_order"]["status"] == "pending"  # input not mutated


@pytest.mark.parametrize("bad", [
    {"credit": 1}, {"status": "weird"}, {"account_tail": "DU0000001"}, "text", ["x"]])
def test_set_broker_close_order_rejects_bad_content(bad):
    ledger, _ = led.add_spread(led.empty_ledger(), _rec())
    with pytest.raises((led.LedgerError, ValueError)):
        led.set_broker(ledger, 1, {"close_order": bad})


def _on_disk_ledger(tmp_path):
    path = tmp_path / "spreads.json"
    ledger, _ = led.add_spread(led.empty_ledger(), _rec())
    ledger, _ = led.add_spread(ledger, _rec(ticker="XYZ"))
    led.save(path, ledger)
    return path, ledger


def test_save_keeping_broker_keeps_on_disk_broker_blocks(tmp_path):
    path, loaded = _on_disk_ledger(tmp_path)
    led.save(path, led.set_broker(led.load(path), 1, {"status": "submitted", "order_ref": "rwh-1-X"}))
    stale = led.update_spread(loaded, 2, status="closed_early")
    merged = led.save_keeping_broker(path, stale)
    on_disk = led.load(path)
    assert on_disk == merged
    assert on_disk["spreads"][0]["broker"] == {"status": "submitted", "order_ref": "rwh-1-X"}
    assert on_disk["spreads"][1]["status"] == "closed_early"
    assert "broker" not in stale["spreads"][0]  # input not mutated


def test_save_keeping_broker_overrides_stale_copy_but_keeps_unsent_records(tmp_path):
    path, loaded = _on_disk_ledger(tmp_path)
    stale = led.set_broker(loaded, 1, {"status": "pending", "order_ref": "rwh-1-OLD"})
    led.save(path, led.set_broker(loaded, 1, {"status": "filled", "order_ref": "rwh-1-NEW"}))
    led.save_keeping_broker(path, stale)
    spreads = led.load(path)["spreads"]
    assert spreads[0]["broker"]["status"] == "filled" and spreads[0]["broker"]["order_ref"] == "rwh-1-NEW"
    assert "broker" not in spreads[1]


def test_save_keeping_broker_refuses_when_disk_has_records_the_copy_lacks(tmp_path):
    path, loaded = _on_disk_ledger(tmp_path)
    stale = {**loaded, "spreads": loaded["spreads"][:1]}
    before = path.read_bytes()
    with pytest.raises(led.LedgerError):
        led.save_keeping_broker(path, stale)
    assert path.read_bytes() == before


def test_save_keeping_broker_creates_missing_file(tmp_path):
    path = tmp_path / "sub" / "spreads.json"
    ledger, _ = led.add_spread(led.empty_ledger(), _rec())
    assert led.save_keeping_broker(path, ledger) == ledger and led.load(path) == ledger


# --- collar / protective put ledger rules -----------------------------------

def _collar(lot_id="L1", **extra):
    return _rec(strategy=led.STRATEGY_COLLAR, lot_ref=_lot(lot_id), **extra)


def _pp(lot_id="L1", **extra):
    return _rec(strategy=led.STRATEGY_PROTECTIVE_PUT, lot_ref=_lot(lot_id), **extra)


def test_existing_covered_call_duplicate_rules_unchanged():
    # Pins the covered-call rules as they were before collar existed.
    ledger = led.empty_ledger()
    ledger, _ = led.add_spread(ledger, _cc("L1"))
    # same lot + same expiry -> duplicate; different lot -> fine
    assert led.find_duplicate(ledger, "ABC", "2026-10-23", _lot("L1")) is not None
    assert led.find_duplicate(ledger, "ABC", "2026-10-23", _lot("L2")) is None
    # no lot_ref -> plain ticker+expiry rule, any strategy
    assert led.find_duplicate(ledger, "abc", "2026-10-23") is not None
    assert led.find_duplicate(ledger, "ABC", "2026-10-30") is None
    # a non-lot record blocks a covered call on any lot
    other, _ = led.add_spread(led.empty_ledger(), _rec(strategy=led.STRATEGY_CSP))
    assert led.find_duplicate(other, "ABC", "2026-10-23", _lot("L9")) is not None
    # legacy record (no strategy key) also blocks
    legacy, _ = led.add_spread(led.empty_ledger(), _rec())
    assert led.find_duplicate(legacy, "ABC", "2026-10-23", _lot("L1")) is not None
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _cc("L1"))


def test_strategy_constants_and_groups():
    assert led.STRATEGY_COLLAR == "collar"
    assert led.STRATEGY_PROTECTIVE_PUT == "protective_put"
    assert led.LOT_STRATEGIES == (led.STRATEGY_COVERED_CALL, "collar", "protective_put")
    assert led.CALL_WRITING == (led.STRATEGY_COVERED_CALL, "collar")
    assert led.strategy_of({"strategy": "collar"}) == "collar"
    assert led.strategy_of({"strategy": "protective_put"}) == "protective_put"
    assert led.strategy_of({}) == led.STRATEGY_BULL_PUT


def test_collar_call_counts_toward_covered_contracts():
    ledger, _ = led.add_spread(led.empty_ledger(), _collar("L1", contracts=2))
    ledger, _ = led.add_spread(ledger, _cc("L1", expiry="2026-10-30", contracts=1))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 3


def test_protective_put_does_not_count_toward_covered_contracts():
    ledger, _ = led.add_spread(led.empty_ledger(), _pp("L1", contracts=4))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 0


def test_collar_and_covered_call_on_same_lot_same_expiry_are_duplicates():
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1"))
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _collar("L1"))
    ledger, _ = led.add_spread(led.empty_ledger(), _collar("L1"))
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _cc("L1"))


def test_collar_and_covered_call_on_different_lots_not_duplicates():
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1"))
    ledger, stored = led.add_spread(ledger, _collar("L2"))
    assert stored["id"] == 2


def test_protective_put_and_covered_call_on_same_lot_not_duplicates():
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1"))
    ledger, _ = led.add_spread(ledger, _pp("L1"))
    ledger, stored = led.add_spread(led.empty_ledger(), _pp("L1"))
    ledger, stored = led.add_spread(ledger, _cc("L1"))
    assert stored["id"] == 2


def test_two_protective_puts_same_lot_same_expiry_are_duplicates():
    ledger, _ = led.add_spread(led.empty_ledger(), _pp("L1"))
    with pytest.raises(led.LedgerError):
        led.add_spread(ledger, _pp("L1"))
    ledger, stored = led.add_spread(ledger, _pp("L2"))
    assert stored["id"] == 2


def test_non_lot_record_still_blocks_new_strategies():
    ledger, _ = led.add_spread(led.empty_ledger(), _rec(strategy=led.STRATEGY_CSP))
    for cand in (_collar("L1"), _pp("L1")):
        with pytest.raises(led.LedgerError):
            led.add_spread(ledger, cand)


def test_add_spread_accepts_lot_ref_for_collar_and_protective_put():
    ledger, a = led.add_spread(led.empty_ledger(), _collar("L1"))
    ledger, b = led.add_spread(ledger, _pp("L2", expiry="2026-10-30"))
    assert a["lot_ref"] == _lot("L1") and b["lot_ref"] == _lot("L2")
    # different lots on the same expiry are accepted only because lot_ref is honoured
    ledger, c = led.add_spread(led.empty_ledger(), _collar("L1"))
    ledger, d = led.add_spread(ledger, _collar("L2"))
    assert d["id"] == 2


# --- iron condor ledger rules -----------------------------------------------

def _condor(**extra):
    return _rec(strategy="iron_condor", **extra)


def _others():
    return [
        _rec(strategy=led.STRATEGY_BULL_PUT),
        _rec(strategy=led.STRATEGY_CSP),
        _cc("L1"),
        _collar("L1"),
        _pp("L1"),
    ]


def test_existing_duplicate_rules_unchanged():
    # Pins the pre-condor rules: lot-less candidate blocked by any same ticker+expiry record,
    # lot-based candidate only blocked by same-lot conflicts or lot-less records.
    for existing in _others():
        ledger, _ = led.add_spread(led.empty_ledger(), existing)
        assert led.find_duplicate(ledger, "ABC", "2026-10-23") is not None
        assert led.find_duplicate(ledger, "ABC", "2026-10-30") is None
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1"))
    assert led.find_duplicate(ledger, "ABC", "2026-10-23", _lot("L1"), led.STRATEGY_COLLAR)
    assert led.find_duplicate(ledger, "ABC", "2026-10-23", _lot("L1"), led.STRATEGY_PROTECTIVE_PUT) is None
    assert led.find_duplicate(ledger, "ABC", "2026-10-23", _lot("L2")) is None
    ledger, _ = led.add_spread(led.empty_ledger(), _rec(strategy=led.STRATEGY_BULL_PUT))
    assert led.find_duplicate(ledger, "ABC", "2026-10-23", _lot("L1"), led.STRATEGY_COLLAR)
    ledger, _ = led.add_spread(led.empty_ledger(), _cc("L1", contracts=2))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 2


def test_strategy_of_recognizes_iron_condor():
    assert led.STRATEGY_IRON_CONDOR == "iron_condor"
    assert led.strategy_of({"strategy": "iron_condor"}) == led.STRATEGY_IRON_CONDOR
    assert led.STRATEGY_IRON_CONDOR not in led.LOT_STRATEGIES
    assert led.STRATEGY_IRON_CONDOR not in led.CALL_WRITING


def test_strategy_of_missing_key_still_bull_put():
    assert led.strategy_of({}) == led.STRATEGY_BULL_PUT
    assert led.strategy_of({"strategy": None}) == led.STRATEGY_BULL_PUT


def test_iron_condor_blocks_same_ticker_same_expiry_any_strategy():
    for other in _others():
        ledger, _ = led.add_spread(led.empty_ledger(), other)
        with pytest.raises(led.LedgerError):
            led.add_spread(ledger, _condor())
        ledger, _ = led.add_spread(led.empty_ledger(), _condor())
        with pytest.raises(led.LedgerError):
            led.add_spread(ledger, other)
    ledger, _ = led.add_spread(led.empty_ledger(), _condor())
    assert led.find_duplicate(ledger, "abc", "2026-10-23") is not None


def test_iron_condor_does_not_block_other_expiry():
    ledger, _ = led.add_spread(led.empty_ledger(), _condor())
    ledger, stored = led.add_spread(ledger, _rec(expiry="2026-10-30"))
    assert stored["id"] == 2
    ledger, stored = led.add_spread(ledger, _condor(expiry="2026-11-06"))
    assert stored["id"] == 3
    ledger, stored = led.add_spread(ledger, _condor(ticker="XYZ"))
    assert stored["id"] == 4


def test_covered_contracts_ignores_iron_condor():
    ledger, _ = led.add_spread(led.empty_ledger(), _condor(contracts=5))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 0
    ledger, _ = led.add_spread(ledger, _cc("L1", expiry="2026-10-30", contracts=2))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 2


# --- bull call spread ledger rules ------------------------------------------

def _bullcall(**extra):
    return _rec(strategy="bull_call_spread", **extra)


def test_strategy_of_recognizes_bull_call():
    assert led.STRATEGY_BULL_CALL == "bull_call_spread"
    assert led.strategy_of({"strategy": "bull_call_spread"}) == led.STRATEGY_BULL_CALL
    assert led.STRATEGY_BULL_CALL not in led.LOT_STRATEGIES
    assert led.STRATEGY_BULL_CALL not in led.CALL_WRITING


def test_bullcall_strike_keys_order_long_then_short():
    assert led.BULLCALL_STRIKE_KEYS == ("long_call_strike", "short_call_strike")
    assert led.BULLCALL_LABEL == "Call 借方价差"


def test_bull_call_blocks_same_ticker_same_expiry_any_strategy():
    for other in _others() + [_condor()]:
        ledger, _ = led.add_spread(led.empty_ledger(), other)
        with pytest.raises(led.LedgerError):
            led.add_spread(ledger, _bullcall())
        ledger, _ = led.add_spread(led.empty_ledger(), _bullcall())
        with pytest.raises(led.LedgerError):
            led.add_spread(ledger, other)
    ledger, _ = led.add_spread(led.empty_ledger(), _bullcall())
    assert led.find_duplicate(ledger, "abc", "2026-10-23") is not None


def test_bull_call_does_not_block_other_expiry():
    ledger, _ = led.add_spread(led.empty_ledger(), _bullcall())
    ledger, stored = led.add_spread(ledger, _rec(expiry="2026-10-30"))
    assert stored["id"] == 2
    ledger, stored = led.add_spread(ledger, _bullcall(expiry="2026-11-06"))
    assert stored["id"] == 3
    ledger, stored = led.add_spread(ledger, _bullcall(ticker="XYZ"))
    assert stored["id"] == 4


def test_covered_contracts_ignores_bull_call():
    ledger, _ = led.add_spread(led.empty_ledger(), _bullcall(contracts=5))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 0
    ledger, _ = led.add_spread(ledger, _cc("L1", expiry="2026-10-30", contracts=2))
    assert led.covered_contracts(ledger, ("acct1", "L1", "ABC")) == 2
