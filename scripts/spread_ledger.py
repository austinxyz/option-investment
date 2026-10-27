#!/usr/bin/env python3
"""JSON ledger for the paper-trading simulator.

Pure functions return new ledgers (never mutate the argument). The only side
effects are `load` (read) and `save` (backup + atomic write).
See docs/superpowers/specs/2026-10-14-spread-sim-design.md.
"""
from __future__ import annotations
import copy
import json
import os
import shutil
from pathlib import Path

LEDGER_VERSION = 1

STRATEGY_BULL_PUT = "bull_put_spread"
STRATEGY_CSP = "cash_secured_put"
STRATEGY_COVERED_CALL = "covered_call"
STRATEGY_COLLAR = "collar"
STRATEGY_PROTECTIVE_PUT = "protective_put"
# Four-leg, lot-less strategy: no lot_ref, so it clashes with any same ticker+expiry record.
STRATEGY_IRON_CONDOR = "iron_condor"
# Two-leg, lot-less debit strategy: like the condor it clashes with any same ticker+expiry record.
STRATEGY_BULL_CALL = "bull_call_spread"
# Strategies written against a held lot (they carry a lot_ref).
LOT_STRATEGIES = (STRATEGY_COVERED_CALL, STRATEGY_COLLAR, STRATEGY_PROTECTIVE_PUT)
# Strategies that sell a call against the lot (they consume covered contracts).
CALL_WRITING = (STRATEGY_COVERED_CALL, STRATEGY_COLLAR)
# Iron condor strikes: long put / short put / short call / long call, the order labels print them in.
CONDOR_STRIKE_KEYS = ("put_long_strike", "put_short_strike", "call_short_strike", "call_long_strike")
# Bull call spread strikes: long call first (the lower one), then the short call.
BULLCALL_STRIKE_KEYS = ("long_call_strike", "short_call_strike")
BULLCALL_LABEL = "Call 借方价差"
# Breached side of a settled condor: short labels for table cells, long ones for sentences.
BREACHED_SIDE_LABELS = {"none": "未突破", "put": "Put 侧", "call": "Call 侧"}
BREACHED_SIDE_LONG_LABELS = {k: v if k == "none" else f"{v}被突破"
                             for k, v in BREACHED_SIDE_LABELS.items()}


def strategy_of(record: dict) -> str:
    """Strategy of a record; legacy records without the key are bull put spreads."""
    return record.get("strategy") or STRATEGY_BULL_PUT


class LedgerError(Exception):
    """Ledger is unreadable, malformed, or the requested change is not allowed."""


def empty_ledger() -> dict:
    return {"version": LEDGER_VERSION, "spreads": []}


def load(path: Path) -> dict:
    if not path.exists():
        return empty_ledger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerError(
            f"账本 {path} 不是有效的 JSON（{exc}）。上一次的备份在 {path}.bak，"
            "请人工确认后再恢复，本命令不会自动覆盖。"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("spreads"), list):
        raise LedgerError(f"账本 {path} 结构不对：缺少 spreads 列表。备份在 {path}.bak。")
    return data


def save(path: Path, ledger: dict) -> None:
    """Back up the current file to <name>.bak, then replace it atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_keeping_broker(path: Path, ledger: dict) -> dict:
    """Save a ledger loaded earlier without wiping `broker` blocks written meanwhile.

    `broker_orders send` writes broker blocks from another process; a CLI that loaded the ledger,
    fetched quotes and then saved would otherwise overwrite them with its stale copy. Right before
    saving, reload the file and copy each record's on-disk `broker` value (matched by id) into the
    copy; a record without a broker block on disk ends up without one. Records only in the copy
    (a new open) keep theirs. A record that is on disk but missing from the copy means the file
    changed in a way this copy cannot represent, so nothing is written (LedgerError): losing a
    record is worse than asking the user to rerun. Returns the ledger that was saved."""
    on_disk = {s["id"]: s for s in load(path)["spreads"]}
    missing = sorted(set(on_disk) - {s["id"] for s in ledger["spreads"]})
    if missing:
        raise LedgerError(
            f"账本在读取之后被别的命令改过（磁盘上多了记录 {missing}），为避免覆盖，本次没有写入；请重新运行")
    merged = copy.deepcopy(ledger)
    for s in merged["spreads"]:
        disk = on_disk.get(s["id"])
        if disk is None:
            continue
        if "broker" in disk:
            s["broker"] = copy.deepcopy(disk["broker"])
        else:
            s.pop("broker", None)
    save(path, merged)
    return merged


def next_id(ledger: dict) -> int:
    return max((s["id"] for s in ledger["spreads"]), default=0) + 1


def lot_key(lot_ref: dict) -> tuple:
    return (lot_ref.get("source"), lot_ref.get("lot_id"), lot_ref.get("ticker"))


def _lot_conflict(existing: str, candidate: str) -> bool:
    """Two lot-based strategies on the same lot+expiry clash if both write a call, or both
    are protective puts. A protective put and a call-writing strategy coexist."""
    if existing in CALL_WRITING and candidate in CALL_WRITING:
        return True
    return existing == candidate == STRATEGY_PROTECTIVE_PUT


def find_duplicate(ledger: dict, ticker: str, expiry: str,
                   lot_ref: dict | None = None, strategy: str | None = None) -> dict | None:
    """Same ticker + same expiry week, any status. With a lot_ref (a lot-based strategy;
    `strategy` defaults to covered_call), an existing lot-based record only counts when it is
    on the same lot and the pair conflicts (see _lot_conflict); other lots never clash."""
    ticker = ticker.strip().upper()
    candidate = strategy or STRATEGY_COVERED_CALL
    for s in ledger["spreads"]:
        if s["ticker"].upper() != ticker or s["expiry"] != expiry:
            continue
        if lot_ref is not None and strategy_of(s) in LOT_STRATEGIES:
            if lot_key(s.get("lot_ref") or {}) != lot_key(lot_ref):
                continue
            if not _lot_conflict(strategy_of(s), candidate):
                continue
        return s
    return None


def covered_contracts(ledger: dict, lot_key_: tuple) -> int:
    """Contracts of open short calls (covered call or collar) written against the lot
    (source, lot_id, ticker). Protective puts do not cover shares."""
    return sum(
        s.get("contracts", 0) for s in ledger["spreads"]
        if strategy_of(s) in CALL_WRITING and s.get("status") == "open"
        and lot_key(s.get("lot_ref") or {}) == tuple(lot_key_)
    )


def get_spread(ledger: dict, spread_id: int) -> dict:
    for s in ledger["spreads"]:
        if s["id"] == spread_id:
            return s
    raise LedgerError(f"账本里没有 id={spread_id} 的记录")


def add_spread(ledger: dict, record: dict) -> tuple[dict, dict]:
    """Return (new_ledger, stored_record). Rejects a second spread on the same ticker+expiry."""
    strategy = strategy_of(record)
    lot_ref = record.get("lot_ref") if strategy in LOT_STRATEGIES else None
    dup = find_duplicate(ledger, record["ticker"], record["expiry"], lot_ref, strategy)
    if dup is not None:
        raise LedgerError(
            f"{record['ticker']} 在到期日 {record['expiry']} 已经有记录 #{dup['id']}（{dup['status']}），"
            "同一周同一 ticker 不重复开。"
        )
    stored = {**copy.deepcopy(record), "id": next_id(ledger)}
    new = copy.deepcopy(ledger)
    new["spreads"].append(stored)
    return new, stored


def update_spread(ledger: dict, spread_id: int, **fields) -> dict:
    get_spread(ledger, spread_id)  # raises LedgerError if absent
    new = copy.deepcopy(ledger)
    for s in new["spreads"]:
        if s["id"] == spread_id:
            s.update(fields)
    return new


BROKER_FIELDS = frozenset({
    "status", "order_ref", "order_id", "perm_id", "client_id", "account_tail", "limit",
    "fill_price", "filled_on", "sent_on", "commission", "intent", "last_ref", "close_order",
})
CLOSE_ORDER_FIELDS = frozenset({
    "status", "order_ref", "order_id", "perm_id", "client_id", "account_tail", "limit",
    "fill_price", "filled_on", "sent_on", "commission", "last_ref",
})
BROKER_STATUSES = frozenset({"pending", "submitted", "partial", "filled", "cancelled", "rejected",
                             "not_sent"})


def _check_block(block: dict, allowed: frozenset, where: str) -> None:
    unknown = set(block) - allowed
    if unknown:
        raise LedgerError(f"{where}不支持字段 {sorted(unknown)}")
    if "status" in block and block["status"] not in BROKER_STATUSES:
        raise LedgerError(f"{where}.status 不合法：{block['status']!r}")
    tail = block.get("account_tail")
    if tail is not None and (not isinstance(tail, str) or len(tail) > 4):
        raise ValueError(f"{where}.account_tail 只能存账号后四位")


def set_broker(ledger: dict, spread_id: int, block: dict) -> dict:
    """Return a new ledger with `block` merged into the record's `broker` key only.

    `close_order` is a sub-dict (the buy-to-close order); it is merged field by field so the
    open order's own fields are never touched by a close update and vice versa."""
    get_spread(ledger, spread_id)  # raises LedgerError if absent
    _check_block(block, BROKER_FIELDS, "broker 块")
    close = block.get("close_order")
    if "close_order" in block:
        if not isinstance(close, dict):
            raise LedgerError("broker.close_order 必须是对象")
        _check_block(close, CLOSE_ORDER_FIELDS, "broker.close_order")
    new = copy.deepcopy(ledger)
    for s in new["spreads"]:
        if s["id"] == spread_id:
            current = s.get("broker") or {}
            merged = {**current, **copy.deepcopy(block)}
            if close is not None:
                merged["close_order"] = {**(current.get("close_order") or {}), **copy.deepcopy(close)}
            s["broker"] = merged
    return new
