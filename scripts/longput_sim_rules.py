#!/usr/bin/env python3
"""Pure candidate rules for the long put simulator (no I/O, no network).

See docs/superpowers/specs/2026-10-30-long-put-design.md (sections 2-4). Single buyer leg:
put_* fields only, no width, no spread fields, no required win rate.

Hand example (design section 3): spot 100, buy 95 Put mid 2.40 -> debit 2.40, max loss $240,
breakeven 92.60, breakeven move -7.40% (-0.074), display max profit $9,260 (stock to zero).
"""
from __future__ import annotations
from datetime import date

import spread_core as core
from bearcall_sim_rules import WARN_DRIFT
from bearput_sim_rules import earnings_warning, pick_long_put, short_dte_warning
from put_screen_rules import MAX_SPREAD_PCT

WARN_THETA_DECAY = ("买方单腿：每天 Theta 抽水，到期前不跌过盈亏平衡价就亏光全部权利金；"
                    + WARN_DRIFT)


def _unavailable(bucket: float, reason: str) -> dict:
    return {"delta_bucket": bucket, "status": "unavailable", "reason": reason}


def _candidate(bucket: float, spot: float, days: float, puts: list[dict], earnings: str,
               spread: dict) -> dict:
    """`spread` holds the spread_limit / spread_warn keywords for the leg."""
    pick = pick_long_put(puts, spot, days, bucket, name="Put", **spread)
    if isinstance(pick, str):
        return _unavailable(bucket, pick)
    q = pick["quote"]
    put_mid = round(q["mid"], 4)
    debit = put_mid
    if debit <= 0 or debit >= q["strike"]:
        return _unavailable(bucket, f"净支出 {debit:.4f} 无效（≤0 或 ≥行权价 {q['strike']:g}）")
    breakeven = round(core.longput_breakeven(q["strike"], debit), 4)
    return {
        "delta_bucket": bucket, "status": "ok",
        "put_strike": q["strike"], "put_mid": put_mid, "debit": debit,
        "breakeven": breakeven,
        "breakeven_move_pct": round(breakeven / spot - 1, 4),
        "put_delta": round(pick["delta"], 4), "put_iv": q["iv"], "underlying_price": spot,
        "max_profit": core.longput_max_profit_display(q["strike"], debit),
        "max_loss": core.longput_max_loss(debit),
        "warnings": (pick["warnings"] + [WARN_THETA_DECAY] + earnings_warning(earnings)
                     + short_dte_warning(days, core.LONGPUT_MIN_DAYS)),
    }


def build_longput_candidates(spot: float, days: float, puts: list[dict], earnings: str,
                             ex_div: date | None, expiry: date, snapshot: date, *,
                             spread_limit: float = MAX_SPREAD_PCT,
                             spread_warn: float | None = None) -> list[dict]:
    """One candidate per LONGPUT_DELTA_BUCKETS entry (0.50 / 0.40 / 0.30), each judged on its own.

    `earnings` is "yes" | "no" | "unknown". `ex_div`, `expiry` and `snapshot` are accepted for a
    uniform signature: a put buyer is never assigned, so they add no warning. `max_profit` is
    display-only (stock to zero). Money fields are for one contract."""
    # IBKR defaults forward nothing at all, so the leg picker sees exactly the default call
    spread = ({} if (spread_limit, spread_warn) == (MAX_SPREAD_PCT, None)
              else {"spread_limit": spread_limit, "spread_warn": spread_warn})
    return [_candidate(b, spot, days, puts, earnings, spread) for b in core.LONGPUT_DELTA_BUCKETS]
