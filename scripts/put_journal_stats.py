"""Put journal statistics: pair settled ledger records with weekly review snapshots.

Pure module: no I/O, no network, no clock. Inputs are plain dicts shaped like
`data/paper-trading/spreads.json` records and `put_review.snapshot_to_json`
snapshots.
"""
from __future__ import annotations

from spread_core import breakeven_win_rate, bullcall_required_win_rate
from spread_ledger import (STRATEGY_BULL_CALL, STRATEGY_COLLAR, STRATEGY_IRON_CONDOR,
                           STRATEGY_PROTECTIVE_PUT, strategy_of)

MIN_SAMPLE = 30
SETTLED_STATUSES = ("expired", "closed_early")
# They carry no credit / short_strike and are not premium-selling trades, so they never
# enter build_cases (and with it the A-D statistics and the threshold what-ifs).
LOT_PUT_STRATEGIES = (STRATEGY_COLLAR, STRATEGY_PROTECTIVE_PUT)
LOT_PUT_RESULTS = ("called_away", "protected", "expired_worthless", "closed")
# A condor has no credit / short_strike either; its own block keeps it out of A-D.
# A bull call debit spread pays a debit (no credit) and also gets its own block.
NOT_CASE_STRATEGIES = LOT_PUT_STRATEGIES + (STRATEGY_IRON_CONDOR, STRATEGY_BULL_CALL)
# An early close never records a breached side, so it is counted apart rather than guessed.
CONDOR_BREACHED_KEYS = ("none", "put", "call", "unrecorded")


def sample_warning(n: int) -> str | None:
    """Warning text when the sample is too small to conclude anything."""
    if n >= MIN_SAMPLE:
        return None
    return f"样本不足（{n} < {MIN_SAMPLE}），别下结论"


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def build_cases(records: list[dict], snapshots: list[dict]) -> list[dict]:
    """One case per settled record, carrying its usable snapshot rows.

    A row is usable when its snapshot date is not after the record's
    exit_date (inclusive: put-review only checks open positions, so a snapshot
    dated on the close day was taken before the close) and its data_quality is not "none".
    """
    rows_by_id: dict[str, list[dict]] = {}
    for snapshot in snapshots:
        date = snapshot["generated_on"]
        for position in snapshot.get("positions", []):
            if position.get("data_quality") == "none":
                continue
            rows_by_id.setdefault(position["id"], []).append(
                {**position, "date": date}
            )

    cases: list[dict] = []
    for record in records:
        exit_date = record.get("exit_date")
        if strategy_of(record) in NOT_CASE_STRATEGIES:
            continue
        if record.get("status") not in SETTLED_STATUSES or exit_date is None:
            continue
        rows = sorted(
            (r for r in rows_by_id.get(record["id"], []) if r["date"] <= exit_date),
            key=lambda r: r["date"],
        )
        cases.append({
            "id": record["id"],
            "strategy": strategy_of(record),
            "ticker": record.get("ticker"),
            "expiry": record.get("expiry"),
            "contracts": record["contracts"],
            "credit": record["credit"],
            "status": record["status"],
            "exit_date": exit_date,
            "pnl": record.get("pnl"),
            "result": record.get("result"),
            "rows": rows,
            "comparable": bool(rows),
        })
    return cases


def lot_put_stats(records: list[dict]) -> dict | None:
    """Settled collars / protective puts: counts per result label, option-leg pnl, net cost.

    `total_net_cost` sums -net_credit * 100 * contracts: positive means cash paid out on net,
    negative means a net credit was collected. Returns None when there are none.
    """
    settled = [r for r in records if strategy_of(r) in LOT_PUT_STRATEGIES
               and r.get("status") in SETTLED_STATUSES]
    if not settled:
        return None
    return {
        "n": len(settled),
        "by_result": {k: sum(1 for r in settled if r.get("result") == k) for k in LOT_PUT_RESULTS},
        "total_pnl": round(sum(r.get("pnl") or 0.0 for r in settled), 2),
        "total_net_cost": round(sum(-r["net_credit"] * 100 * r["contracts"] for r in settled), 2),
    }


def condor_stats(records: list[dict]) -> dict | None:
    """Settled iron condors: wins, per-trade average theoretical breakeven win rate
    (1 - net credit / width), breached-side counts and total pnl. None when there are none."""
    settled = [r for r in records if strategy_of(r) == STRATEGY_IRON_CONDOR
               and r.get("status") in SETTLED_STATUSES]
    if not settled:
        return None
    wins = sum(1 for r in settled if (r.get("pnl") or 0.0) > 0)
    sides = [r.get("breached_side") if r.get("breached_side") in CONDOR_BREACHED_KEYS[:3]
             else "unrecorded" for r in settled]
    theory = [breakeven_win_rate(r["net_credit"], r["width"]) for r in settled]
    return {
        "n": len(settled), "wins": wins, "win_rate": wins / len(settled),
        "theory_breakeven_avg": sum(theory) / len(theory),
        "breached": {k: sides.count(k) for k in CONDOR_BREACHED_KEYS},
        "total_pnl": round(sum(r.get("pnl") or 0.0 for r in settled), 2),
    }


def bullcall_stats(records: list[dict]) -> dict | None:
    """Settled bull call debit spreads: wins, win rate, per-trade average required win rate
    (debit / width), winners' average fraction of max profit captured, total pnl.
    None when there are none. There is no breached-side counterpart."""
    settled = [r for r in records if strategy_of(r) == STRATEGY_BULL_CALL
               and r.get("status") in SETTLED_STATUSES]
    if not settled:
        return None
    wins = [r for r in settled if (r.get("pnl") or 0.0) > 0]
    required = [bullcall_required_win_rate(r["debit"], r["width"]) for r in settled]
    captured = [r["fraction_of_max_captured"] for r in wins
                if r.get("fraction_of_max_captured") is not None]
    return {
        "n": len(settled), "wins": len(wins), "win_rate": len(wins) / len(settled),
        "required_avg": sum(required) / len(required),
        "captured_avg_winners": sum(captured) / len(captured) if captured else None,
        "total_pnl": round(sum(r.get("pnl") or 0.0 for r in settled), 2) + 0.0,
    }


def pnl_if_closed(case: dict, row: dict) -> float:
    """P&L had the position been closed at the row's cost_to_close."""
    return round((case["credit"] - row["cost_to_close"]) * 100 * case["contracts"], 2)


def _comparable(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c["comparable"]]


def close_advice_stats(cases: list[dict]) -> dict:
    """A: would following the earliest "close" advice have beaten the actual result?"""
    comparable = _comparable(cases)
    items: list[dict] = []
    for c in comparable:
        close_rows = [r for r in c["rows"] if r.get("advice") == "close"]
        if not close_rows:
            continue
        row = close_rows[0]
        pnl_if = pnl_if_closed(c, row)
        items.append({
            "id": c["id"],
            "ticker": c["ticker"],
            "date": row["date"],
            "pnl_if": pnl_if,
            "actual": c["pnl"],
            "diff": round(pnl_if - c["pnl"], 2),
            "stale": not row.get("data_ok"),
        })
    total = round(sum(i["diff"] for i in items), 2)
    return {
        "n_comparable": len(comparable),
        "n_close": len(items),
        "items": items,
        "mean_diff": round(total / len(items), 2) if items else None,
        "total_diff": total,
        "better": sum(1 for i in items if i["diff"] > 0),
        "stale_rows": sum(1 for i in items if i["stale"]),
    }


def attention_stats(cases: list[dict]) -> dict:
    """B: how well does an "attention" flag predict a losing position?"""
    comparable = _comparable(cases)
    flagged = 0
    trouble = 0
    both = 0
    rules: dict[int, dict[str, int]] = {}
    for c in comparable:
        first = next((r for r in c["rows"] if r.get("advice") == "attention"), None)
        is_trouble = c["pnl"] < 0
        flagged += first is not None
        trouble += is_trouble
        if first is None:
            continue
        both += is_trouble
        bucket = rules.setdefault(int(first["rule"]), {"flagged": 0, "trouble": 0})
        bucket["flagged"] += 1
        bucket["trouble"] += is_trouble
    by_rule = {
        rule: {**b, "precision": _rate(b["trouble"], b["flagged"])}
        for rule, b in sorted(rules.items())
    }
    return {
        "n_comparable": len(comparable),
        "n_flagged": flagged,
        "n_trouble": trouble,
        "flagged_and_trouble": both,
        "precision": _rate(both, flagged),
        "recall": _rate(both, trouble),
        "by_rule": by_rule,
    }


def hold_stats(cases: list[dict]) -> dict:
    """C: win rate of cases whose last usable advice was "hold"."""
    held = [c for c in _comparable(cases) if c["rows"][-1].get("advice") == "hold"]
    wins = sum(1 for c in held if c["pnl"] > 0)
    return {"n": len(held), "wins": wins, "rate": _rate(wins, len(held))}


def streak_stats(cases: list[dict], streak: int = 3) -> dict:
    """D: win rate of the case following a run of `streak` consecutive wins."""
    ordered = sorted(cases, key=lambda c: (c["exit_date"], c["id"]))
    n = 0
    wins = 0
    for i in range(streak, len(ordered)):
        if all(c["pnl"] > 0 for c in ordered[i - streak:i]):
            n += 1
            wins += ordered[i]["pnl"] > 0
    baseline_wins = sum(1 for c in ordered if c["pnl"] > 0)
    return {
        "n_settled": len(ordered),
        "n": n,
        "wins": wins,
        "rate": _rate(wins, n),
        "baseline_rate": _rate(baseline_wins, len(ordered)),
    }


def consistency(case: dict) -> str:
    """Did the user's actual action agree with the last usable advice?"""
    if not case["rows"]:
        return "无建议可比"
    last_is_close = case["rows"][-1].get("advice") == "close"
    if case["status"] == "closed_early":
        return "一致" if last_is_close else "不一致"
    return "不一致" if last_is_close else "一致"
