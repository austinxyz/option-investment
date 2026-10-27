#!/usr/bin/env python3
"""Paper share lots (hypothetical or assigned-put shares) and the Wheel sync.

Lots live in <ledger_dir>/paper_shares.json. `sync` turns assigned cash-secured puts into
lots and applies settled covered-call results to them. It never touches positions.md and
never rewrites a file when nothing changed.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path

import spread_ledger as ledger_mod
from spread_ledger import LedgerError

LOTS_VERSION = 1
LOTS_FILE = "paper_shares.json"


def empty_lots() -> dict:
    return {"version": LOTS_VERSION, "next_id": 1, "lots": [], "retired_source_refs": []}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


LOT_CHECKS = (
    ("id", _is_int), ("ticker", lambda v: isinstance(v, str)), ("shares", _is_num),
    ("original_cost", _is_num), ("cost_basis", _is_num), ("reference_price", _is_num),
    ("source", lambda v: isinstance(v, str)), ("status", lambda v: isinstance(v, str)),
    ("applied_ids", lambda v: isinstance(v, list)), ("realized_stock_pnl", _is_num),
)


def _lot_problem(lot) -> str | None:
    if not isinstance(lot, dict):
        return "有持仓不是对象"
    for key, ok in LOT_CHECKS:
        if key not in lot or not ok(lot[key]):
            return f"持仓 {lot.get('id', '?')} 的 {key} 缺失或类型不对"
    return None


def _shape_problem(data) -> str | None:
    if not isinstance(data, dict):
        return "不是 JSON 对象"
    if not _is_int(data.get("version")):
        return "缺少 version"
    if not _is_int(data.get("next_id")):
        return "缺少 next_id"
    if not isinstance(data.get("lots"), list):
        return "缺少 lots 列表"
    retired = data.get("retired_source_refs", [])
    if not isinstance(retired, list) or not all(_is_int(r) for r in retired):
        return "retired_source_refs 不是整数列表"
    return next((p for p in map(_lot_problem, data["lots"]) if p), None)


def load_lots(path: Path) -> dict:
    if not path.exists():
        return empty_lots()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerError(
            f"假设持仓文件 {path} 不是有效的 JSON（{exc}）。上一次的备份在 {path}.bak，"
            "请人工确认后再恢复，本命令不会自动覆盖。"
        ) from exc
    problem = _shape_problem(data)
    if problem:
        raise LedgerError(
            f"假设持仓文件 {path} 结构不对：{problem}。上一次的备份在 {path}.bak，"
            "请人工确认后再恢复，本命令不会自动覆盖。")
    return {**data, "retired_source_refs": list(data.get("retired_source_refs", []))}


def save_lots(path: Path, doc: dict) -> None:
    ledger_mod.save(path, doc)  # same .bak + atomic replace


def _new_lot(lot_id: int, ticker: str, shares: float, cost: float, source: str,
             source_ref, opened_on: str, reference_price: float | None) -> dict:
    return {
        "id": lot_id, "ticker": ticker.strip().upper(), "shares": shares,
        "original_cost": cost, "cost_basis": cost,
        "reference_price": cost if reference_price is None else reference_price,
        "source": source, "source_ref": source_ref, "opened_on": opened_on,
        "status": "open", "closed_on": None, "realized_stock_pnl": 0.0, "applied_ids": [],
    }


def add_lot(doc: dict, *, ticker: str, shares: float, cost: float, source: str = "manual",
            source_ref=None, opened_on: str, reference_price: float | None = None
            ) -> tuple[dict, dict]:
    lot = _new_lot(doc["next_id"], ticker, shares, cost, source, source_ref,
                   opened_on, reference_price)
    new = copy.deepcopy(doc)
    new["lots"].append(lot)
    new["next_id"] = lot["id"] + 1
    return new, copy.deepcopy(lot)


def remove_lot(doc: dict, ledger: dict, lot_id: int) -> dict:
    if not any(l["id"] == lot_id for l in doc["lots"]):
        raise LedgerError(f"没有 id={lot_id} 的假设持仓")
    for s in ledger["spreads"]:
        ref = s.get("lot_ref") or {}
        if ref.get("lot_id") == lot_id and ref.get("source") != "real":
            raise LedgerError(f"假设持仓 #{lot_id} 已被备兑看涨 #{s['id']} 引用，不能删除")
    new = copy.deepcopy(doc)
    gone = next(l for l in new["lots"] if l["id"] == lot_id)
    new["lots"] = [l for l in new["lots"] if l["id"] != lot_id]
    if gone["source"] == "assigned_put" and gone.get("source_ref") is not None:
        new["retired_source_refs"] = [*new.get("retired_source_refs", []), gone["source_ref"]]
    return new


def _is_assigned_put(s: dict) -> bool:
    return (ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_CSP
            and s.get("status") == "expired" and s.get("result") == "assigned")


def _create_assigned_lots(doc: dict, led: dict) -> tuple[dict, list[str]]:
    msgs: list[str] = []
    for s in led["spreads"]:
        if not _is_assigned_put(s) or s["id"] in doc.get("retired_source_refs", []) or any(
                l["source"] == "assigned_put" and l.get("source_ref") == s["id"]
                for l in doc["lots"]):
            continue
        cost = round(s["short_strike"] - s["credit"], 4)
        doc, lot = add_lot(
            doc, ticker=s["ticker"], shares=s["contracts"] * 100, cost=cost,
            source="assigned_put", source_ref=s["id"], opened_on=s.get("exit_date") or s["expiry"],
            reference_price=s.get("exit_underlying"))
        msgs.append(f"被指派 #{s['id']} {s['ticker']}：新增假设持仓 #{lot['id']}"
                    f"（{lot['shares']:g} 股，成本 ${cost:.2f}）")
    return doc, msgs


def _call_premium(s: dict) -> float:
    """Dollars of option premium to fold into the lot's cost; signed (a net debit is negative)."""
    strategy = ledger_mod.strategy_of(s)
    if strategy == ledger_mod.STRATEGY_COLLAR:
        # Realised option-leg pnl at expiry and at early close alike, so both paths agree.
        if s.get("pnl") is None:
            raise LedgerError(f"领口 #{s['id']} 已结束但没有 pnl，无法更新假设持仓的有效成本")
        return s["pnl"]
    if strategy == ledger_mod.STRATEGY_COVERED_CALL:
        per_share = s["credit"] - (s.get("exit_cost") or 0.0) if s["status"] == "closed_early" \
            else s["credit"]
        return per_share * 100 * s["contracts"]
    raise ValueError(f"策略 {strategy} 不回写假设持仓")


# protective_put is deliberately absent: spec 10.6 writes back covered calls and collars only,
# so a bought put never changes a paper lot's shares or cost.
WRITEBACK_STRATEGIES = (ledger_mod.STRATEGY_COVERED_CALL, ledger_mod.STRATEGY_COLLAR)


def _pending_calls(led: dict) -> list[dict]:
    return [s for s in led["spreads"]
            if ledger_mod.strategy_of(s) in WRITEBACK_STRATEGIES
            and s.get("status") in ("expired", "closed_early") and not s.get("lot_applied")
            and (s.get("lot_ref") or {}).get("source") != "real"]


def _apply_call(lot: dict, s: dict) -> dict:
    new = copy.deepcopy(lot)
    before = new["shares"]
    if before > 0:
        new["cost_basis"] = round(new["cost_basis"] - _call_premium(s) / before, 6)
    if s.get("result") == "called_away":
        new["shares"] = max(before - 100 * s["contracts"], 0)
        new["realized_stock_pnl"] = round(
            new.get("realized_stock_pnl", 0.0) + (s.get("stock_leg_pnl") or 0.0), 2)
        if new["shares"] <= 0:
            new["status"], new["closed_on"] = "called_away", s.get("exit_date")
    new["applied_ids"] = [*new.get("applied_ids", []), s["id"]]
    return new


def _label(s: dict) -> str:
    return "领口" if ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_COLLAR else "备兑看涨"


def _apply_calls(doc: dict, led: dict) -> tuple[dict, list[int], list[str]]:
    msgs: list[str] = []
    flagged: list[int] = []
    for s in _pending_calls(led):
        ref = s.get("lot_ref") or {}
        lot_id = ref.get("lot_id")
        idx = next((i for i, l in enumerate(doc["lots"])
                    if l["id"] == lot_id and l["ticker"] == ref.get("ticker")
                    and l["source"] == ref.get("source")), None)
        flagged.append(s["id"])
        label = _label(s)
        if idx is None:
            msgs.append(f"{label} #{s['id']}：找不到假设持仓 #{lot_id}，未能回写成本")
            continue
        if s["id"] in doc["lots"][idx].get("applied_ids", []):
            continue
        lots = list(doc["lots"])
        lots[idx] = _apply_call(lots[idx], s)
        doc = {**doc, "lots": lots}
        msgs.append(f"{label} #{s['id']} 已回写假设持仓 #{lot_id}："
                    f"成本 ${lots[idx]['cost_basis']:.2f}，剩余 {lots[idx]['shares']:g} 股")
    return doc, flagged, msgs


def sync(ledger_dir: Path) -> list[str]:
    ledger_path, lots_path = ledger_dir / "spreads.json", ledger_dir / LOTS_FILE
    led = ledger_mod.load(ledger_path)
    original = load_lots(lots_path)
    doc, msgs = _create_assigned_lots(original, led)
    doc, flagged, call_msgs = _apply_calls(doc, led)
    if doc != original:
        save_lots(lots_path, doc)  # lots first, ledger flags second
    if flagged:
        new_led = copy.deepcopy(led)
        for s in new_led["spreads"]:
            if s["id"] in flagged:
                s["lot_applied"] = True
        ledger_mod.save(ledger_path, new_led)
    return msgs + call_msgs
