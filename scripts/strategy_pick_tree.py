#!/usr/bin/env python3
"""Decision tree for /strategy-pick: leaf lookup, ranking, reference levels, why-not, warnings.

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.8-6.12). Pure functions on plain
data, no I/O. Which strategies a leaf gets lives in rules.LEAF_TABLE / rules.REASON_RULES, so
decide() only looks a leaf up and then applies the earnings, ranking and reference-level
modifiers. The input stages (gate, view, class, IV, holding, earnings) live in
strategy_pick_inputs and are re-exported here so callers have one import.
"""
from __future__ import annotations

from dataclasses import dataclass

import strategy_pick_rules as R
import strategy_pick_text as T
from spread_core import expected_move
from strategy_pick_inputs import (FAIL, PASS, UNCHECKED, Earnings, Gate, GateInputs,  # noqa: F401
                                  Holding, IvPosition, NeedsView, ViewDecision, earnings_state,
                                  holding_state, iv_position, judge_gate, normalize_view,
                                  resolve_class, resolve_view, select_iv_expiry,
                                  select_oi_expiry, spread_pct)

# ---- decide (6.8-6.12) ----

@dataclass(frozen=True)
class Target:
    leg: str
    low: float
    high: float


@dataclass(frozen=True)
class Pick:
    strategy: str
    rank: int
    delta_target: tuple[Target, ...]
    reference_level: tuple[Target, ...] | None
    next_command: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class NotChosen:
    strategy: str
    reason_code: str
    reason_text: str
    page: str


@dataclass(frozen=True)
class DecisionContext:
    ticker: str
    view: str | None
    holding: Holding | None
    regime: str | None        # Regime.leaf
    iv: IvPosition | None
    asset_class: str
    earnings: Earnings | None
    spot: float
    days: int | None          # days to the expiry used for IV
    portfolio_value: float | None
    gate_passed: bool = True
    history_short: bool = False


@dataclass(frozen=True)
class Decision:
    leaf_id: str
    picks: tuple[Pick, ...]
    not_chosen: tuple[NotChosen, ...]
    warnings: tuple[str, ...]
    csp_share: float | None = None    # reference strike x 100 / portfolio value
    rank_basis: str | None = None     # share_and_class / class_only


def sigma_for(spot: float, atm_iv: float | None, days: int | None) -> float | None:
    """Unrounded one-sigma move; None without an ATM IV (no reference levels then)."""
    if atm_iv is None or not days:
        return None
    return expected_move(spot, atm_iv, days)


def _level(leg: str, side: int, deltas, spot: float, sigma: float) -> Target:
    vals = [round(spot + side * R.Z_BY_DELTA[d] * sigma if side else spot, R.REF_DECIMALS)
            for d in deltas]
    return Target(leg, min(vals), max(vals))


def plan_targets(plan: str | None, spot: float, sigma: float | None):
    """(delta targets, reference levels or None) for a leg plan in rules.LEG_PLANS."""
    if plan is None:
        return (), None
    legs = R.LEG_PLANS[plan]
    deltas = tuple(Target(leg, lo, hi) for leg, _, (lo, hi) in legs)
    if sigma is None:
        return deltas, None
    return deltas, tuple(_level(leg, side, d, spot, sigma) for leg, side, d in legs)


def rank_csp_vs_bull_put(ref_strike: float | None, portfolio_value: float | None,
                         asset_class: str) -> tuple[str, str, float | None]:
    """(first strategy, basis, share of portfolio); without a share only the class decides."""
    class_ok = asset_class in R.CSP_FIRST_CLASSES
    if ref_strike is None or not portfolio_value:
        return (R.CSP_KEY if class_ok else R.BULL_PUT_KEY), "class_only", None
    share = ref_strike * R.CONTRACT_MULTIPLIER / portfolio_value
    first = R.CSP_KEY if class_ok and share <= R.CSP_MAX_PORTFOLIO_SHARE else R.BULL_PUT_KEY
    return first, "share_and_class", share


def leaf_id(ctx: DecisionContext) -> str:
    if not ctx.gate_passed:
        return R.LEAF_GATE_FAIL
    if ctx.history_short:
        return R.LEAF_HISTORY_SHORT
    branch = R.BRANCH_HELD if ctx.holding.treated_as_held else R.BRANCH_NOT_HELD
    if ctx.view in R.COLLAPSED_VIEWS:
        return f"{ctx.view}.{branch}"
    return f"{ctx.view}.{branch}.{ctx.regime}.{ctx.iv.label}"


def _expand_pair(spec, ctx: DecisionContext, sigma):
    """Replace the SELL_PAIR token by CSP and Bull Put in ranked order."""
    out, share, basis = [], None, None
    for lp in spec:
        if lp.strategy != R.SELL_PAIR:
            out.append(lp)
            continue
        ref = None
        if sigma is not None:
            ref = round(ctx.spot - R.Z_BY_DELTA[R.RANK_DELTA_BY_PLAN[lp.plan]] * sigma,
                        R.REF_DECIMALS)
        first, basis, share = rank_csp_vs_bull_put(ref, ctx.portfolio_value, ctx.asset_class)
        second = R.BULL_PUT_KEY if first == R.CSP_KEY else R.CSP_KEY
        out += [lp._replace(strategy=first), lp._replace(strategy=second)]
    return out, share, basis


def _thin_iv(ctx: DecisionContext) -> bool:
    if ctx.iv is None or ctx.iv.atm_iv is None:
        return False
    return ctx.iv.atm_iv < R.CLASS_THRESHOLDS[ctx.asset_class]["THIN_IV"]


def _extra_flags(lp, idx: int, ctx: DecisionContext) -> tuple[str, ...]:
    e, h, s = ctx.earnings, ctx.holding, lp.strategy
    rules_hit = (
        (R.PLAN_FLAGS.get(lp.plan, ()), True),
        (("thin_premium",), s in R.SELLER_PICKS and _thin_iv(ctx)),
        ((R.EARNINGS_BEFORE_EXPIRY_FLAG,), s in R.EARNINGS_SENSITIVE and e is not None
         and e.in_window and ctx.asset_class in R.EARNINGS_AVOID_CLASSES),
        (("ex_dividend",), s in R.EX_DIV_PICKS and e is not None and e.ex_div is not None),
        (("ltcg_reset",), s in R.LTCG_PICKS and h is not None and not h.ltcg_reached),
        (("reduce_hint",), idx == 0 and h is not None and h.branch == R.BRANCH_NO_LOT
         and ctx.view in R.REDUCE_HINT_VIEWS),
    )
    return tuple(f for flags, on in rules_hit if on for f in flags)


def _with_flags(entries, ctx: DecisionContext):
    out = []
    for idx, lp in enumerate(entries):
        merged = tuple(dict.fromkeys(lp.flags + _extra_flags(lp, idx, ctx)))
        out.append(lp._replace(flags=merged))
    return out


def _earnings_imminent(entries, ctx: DecisionContext) -> bool:
    e = ctx.earnings
    return (e is not None and e.imminent and ctx.asset_class in R.EARNINGS_AVOID_CLASSES
            and any(lp.strategy in R.EARNINGS_SENSITIVE for lp in entries))


def _apply_imminent(entries, ctx: DecisionContext):
    """Hot stock, earnings within 7 days: wait first, old first second, old second dropped."""
    if not _earnings_imminent(entries, ctx):
        return entries, ()
    keep = R.MAX_PICKS - 1
    wait = R.LeafPick(R.WAIT_EARNINGS)
    return [wait] + entries[:keep], tuple(lp.strategy for lp in entries[keep:])


def _to_pick(rank: int, lp, ctx: DecisionContext, sigma) -> Pick:
    deltas, refs = plan_targets(lp.plan, ctx.spot, sigma)
    command = T.NEXT_COMMANDS[lp.strategy].format(ticker=ctx.ticker)
    return Pick(lp.strategy, rank, deltas, refs, command, lp.flags)


def _labels(ctx: DecisionContext) -> dict:
    branch = ctx.holding.branch if ctx.holding is not None else None
    return {"view": ctx.view, "held": branch, "regime": ctx.regime,
            "iv": ctx.iv.label if ctx.iv is not None else None}


def _reason_code(key: str, dropped, labels: dict, lid: str) -> str:
    if key in dropped:
        return R.EARNINGS_IMMINENT_CODE
    if lid in R.SPECIAL_LEAF_CODES and key not in R.NOT_CHOSEN_ONLY:
        return R.SPECIAL_LEAF_CODES[lid]
    for cond, code in R.REASON_RULES[key]:
        if all(labels.get(k) in allowed for k, allowed in cond.items()):
            return code
    raise KeyError(f"no why-not rule matched {key!r} at {lid!r}")


def _not_chosen(picked: set, dropped, ctx: DecisionContext, lid: str) -> tuple[NotChosen, ...]:
    labels = _labels(ctx)
    out = []
    for key in R.ALL_STRATEGY_KEYS:
        if key in picked:
            continue
        code = _reason_code(key, dropped, labels, lid)
        out.append(NotChosen(key, code, T.WHY_NOT[(key, code)], T.STRATEGY_PAGES[key]))
    return tuple(out)


def _earnings_warnings(ctx: DecisionContext, picks) -> list[str]:
    e, cls, out = ctx.earnings, ctx.asset_class, []
    if e is None or not picks:
        return out
    if e.ex_div is not None:
        out.append(T.WARN["ex_dividend"].format(date=e.ex_div.isoformat()))
    if e.unknown:
        out.append(T.WARN["earnings_unknown"])
    if not e.in_window:
        return out
    if cls in R.EARNINGS_WARN_CLASSES:
        out.append(T.WARN["earnings_warn_only"].format(date=e.date.isoformat(),
                                                       window=R.EARNINGS_WINDOW_DAYS))
    if cls not in R.EARNINGS_AVOID_CLASSES:
        return out
    out.append(T.WARN["earnings_avoid"].format(date=e.date.isoformat()))
    if ctx.days is not None and e.days <= ctx.days:
        out.append(T.WARN["earnings_cross_expiry"].format(days=ctx.days, earn_days=e.days))
    if e.imminent:
        out.append(T.WARN["earnings_imminent"].format(earn_days=e.days,
                                                      limit=R.EARNINGS_IMMINENT_DAYS))
    return out


def _holding_warnings(ctx: DecisionContext, picks) -> list[str]:
    h, out = ctx.holding, []
    if h is not None and h.branch == R.BRANCH_NO_LOT:
        out.append(T.WARN["no_lot"].format(max_lot=h.max_lot_shares, lot=R.MIN_LOT_SHARES))
        if ctx.view in R.REDUCE_HINT_VIEWS:
            out.append(T.WARN["reduce_hint"].format(lot=R.MIN_LOT_SHARES))
    if any("ltcg_reset" in p.flags for p in picks):
        out.append(T.WARN["ltcg_reset"])
    return out


def _warnings(ctx: DecisionContext, leaf_warns, picks, sigma) -> tuple[str, ...]:
    out = [T.WARN[k] for k in leaf_warns] + _holding_warnings(ctx, picks)
    out += _earnings_warnings(ctx, picks)
    if any(p.strategy in R.SELLER_PICKS for p in picks) and _thin_iv(ctx):
        out.append(T.WARN["thin_iv"].format(
            atm_iv=ctx.iv.atm_iv, thin=R.CLASS_THRESHOLDS[ctx.asset_class]["THIN_IV"]))
    if sigma is None and any(p.delta_target for p in picks):
        out.append(T.WARN["ref_missing_iv"])
    return tuple(dict.fromkeys(out))


def decide(ctx: DecisionContext) -> Decision:
    lid = leaf_id(ctx)
    spec, leaf_warns = R.LEAF_TABLE[lid]
    sigma = sigma_for(ctx.spot, ctx.iv.atm_iv, ctx.days) if ctx.iv is not None else None
    entries, share, basis = _expand_pair(spec, ctx, sigma)
    entries, dropped = _apply_imminent(_with_flags(entries, ctx), ctx)
    picks = tuple(_to_pick(rank, lp, ctx, sigma)
                  for rank, lp in enumerate(entries[:R.MAX_PICKS], start=1))
    not_chosen = _not_chosen({p.strategy for p in picks}, dropped, ctx, lid)
    return Decision(lid, picks, not_chosen, _warnings(ctx, leaf_warns, picks, sigma),
                    share, basis)
