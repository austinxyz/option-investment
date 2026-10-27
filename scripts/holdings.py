#!/usr/bin/env python3
"""Held share lots for covered-call paper trading (real positions + paper lots).

Read-only with respect to positions.md: only positions.list_active / list_etf are used.
See docs/superpowers/specs/2026-10-21-call-screen-design.md.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Callable

import spread_ledger as ledger_mod

SOURCE_REAL = "real"
SOURCE_MANUAL = "manual"
SOURCE_ASSIGNED = "assigned_put"
_BLANK_TICKERS = {"", "—", "-", "N/A"}
PUT_BUYING = (ledger_mod.STRATEGY_COLLAR, ledger_mod.STRATEGY_PROTECTIVE_PUT)
_TAX_ADVANTAGED_MARKERS = ("roth", "ira", "401")


@dataclass(frozen=True)
class HeldLot:
    source: str            # real | manual | assigned_put
    lot_id: int | None
    ticker: str
    shares: float
    cost_basis: float      # effective (premium-adjusted for paper lots)
    original_cost: float
    reference_price: float
    cost_estimated: bool
    accounts: int
    label: str
    # Tax facts, real lots only (None = unknown / paper lot). See the collar spec, 10.7.
    has_taxable_account: bool | None = None
    earliest_entry_date: str | None = None

    @property
    def key(self) -> tuple:
        return (self.source, self.lot_id, self.ticker)

    @property
    def ref(self) -> dict:
        return {"source": self.source, "lot_id": self.lot_id, "ticker": self.ticker}


def _default_loaders() -> tuple[Callable, Callable]:
    import positions  # lazy: only the two read functions are used
    return positions.list_active, positions.list_etf


def _is_taxable(account: str | None) -> bool:
    """Every real account is taxable unless its name says Roth / IRA / 401 (any case)."""
    name = (account or "").lower()
    return not any(m in name for m in _TAX_ADVANTAGED_MARKERS)


def _earliest(a: dict) -> str | None:
    """Earliest entry date across the merged rows; None if any contributing row has no date
    (an unknown date must not look like a long holding period)."""
    if a["undated"] or not a["dates"]:
        return None
    return min(a["dates"])


def _aggregate(rows: list) -> dict[str, dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        ticker = (r.ticker or "").strip().upper()
        if ticker in _BLANK_TICKERS:
            continue
        a = agg.setdefault(ticker, {"shares": 0.0, "cost": 0.0, "priced": 0.0,
                                    "est": False, "n": 0, "taxable": False, "dates": [],
                                    "undated": False})
        a["shares"] += r.shares
        if isinstance(r.avg_cost, (int, float)) and r.avg_cost > 0:
            a["cost"] += r.shares * r.avg_cost
            a["priced"] += r.shares
        else:  # blank / "—" cost: keep the shares but leave them out of the average
            a["est"] = True
        a["est"] = a["est"] or bool(getattr(r, "cost_estimated", False))
        a["n"] += 1
        a["taxable"] = a["taxable"] or _is_taxable(getattr(r, "account", ""))
        entry = (getattr(r, "entry_date", "") or "").strip()
        if entry:
            a["dates"].append(entry)
        else:
            a["undated"] = True
    return agg


def real_lots(stock_loader: Callable | None = None,
              etf_loader: Callable | None = None) -> list[HeldLot]:
    try:
        if stock_loader is None or etf_loader is None:
            d_stock, d_etf = _default_loaders()
            stock_loader = stock_loader or d_stock
            etf_loader = etf_loader or d_etf
        rows = list(stock_loader()) + list(etf_loader())
        aggregated = _aggregate(rows)
    except ledger_mod.LedgerError:
        raise
    except Exception as exc:  # noqa: BLE001 - any loader failure is a user-facing error
        raise ledger_mod.LedgerError(f"读取 positions.md 失败：{exc}") from exc
    lots = []
    for ticker, a in sorted(aggregated.items()):
        if a["shares"] <= 0:
            continue
        avg = a["cost"] / a["priced"] if a["priced"] > 0 else 0.0
        lots.append(HeldLot(
            source=SOURCE_REAL, lot_id=None, ticker=ticker, shares=a["shares"], cost_basis=avg,
            original_cost=avg, reference_price=avg, cost_estimated=a["est"], accounts=a["n"],
            label=f"{ticker}（真实）",
            has_taxable_account=a["taxable"], earliest_entry_date=_earliest(a)))
    return lots


def _paper_label(lot: dict) -> str:
    if lot["source"] == SOURCE_ASSIGNED:
        return f"{lot['ticker']}（被指派 put #{lot.get('source_ref')}，持仓 #{lot['id']}）"
    return f"{lot['ticker']}（假设 #{lot['id']}）"


def paper_lots(doc: dict) -> list[HeldLot]:
    return [
        HeldLot(source=l["source"], lot_id=l["id"], ticker=l["ticker"], shares=l["shares"],
                cost_basis=l["cost_basis"], original_cost=l["original_cost"],
                reference_price=l["reference_price"], cost_estimated=False, accounts=1,
                label=_paper_label(l))
        for l in doc["lots"] if l.get("status") == "open"
    ]


def contracts_available(lot: HeldLot, ledger: dict) -> int:
    covered = ledger_mod.covered_contracts(ledger, lot.key)
    return max(math.floor(lot.shares / 100) - covered, 0)


def contracts_available_for_put(lot: HeldLot, ledger: dict | None = None) -> int:
    """Long puts hedge shares: open calls on the lot do not reduce the count, but open
    protective puts and collars (each owns one put per contract) do."""
    held = 0
    if ledger is not None:
        held = sum(
            s.get("contracts", 0) for s in ledger["spreads"]
            if ledger_mod.strategy_of(s) in PUT_BUYING and s.get("status") == "open"
            and ledger_mod.lot_key(s.get("lot_ref") or {}) == lot.key)
    return max(math.floor(lot.shares / 100) - held, 0)
