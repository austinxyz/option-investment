#!/usr/bin/env python3
"""Carry a /strategy-pick Delta target into the next command (pure: no I/O, clock or network).

See docs/superpowers/specs/2026-10-26-delta-target-handoff-design.md (D1, D2, 6.3). Every number
comes from the constants the leaf table and the target tools already use (the target ranges
arrive in `delta_target`, the buckets from spread_core, the --delta range and walk floor from
put_screen_rules), so a changed constant moves the hand-off with it instead of drifting.
Only strategy_pick_render imports this module; rules / text must not import it.
"""
from __future__ import annotations

from typing import Callable, NamedTuple, Sequence

import put_screen_rules as psr
import spread_core
import strategy_pick_text as T

HANDOFF_KIND = {
    "cash_secured_put": "screen_put",
    "covered_call": "screen_call",
    "bull_put_spread": "bucket_spread",
    "iron_condor": "bucket_condor",
    "bull_call_spread": "bucket_bullcall",
    "collar": "no_delta_collar",
    "protective_put": "no_delta_collar",
}
# The leg whose range picks the bucket / --delta, and the buckets the tool's `open` accepts.
_LEG_BY_KIND = {"screen_put": "short_put", "screen_call": "short_call",
                "bucket_spread": "short_put", "bucket_condor": "short_put",
                "bucket_bullcall": "long_call"}
_BUCKETS_BY_KIND = {"bucket_spread": spread_core.DELTA_BUCKETS,
                    "bucket_condor": spread_core.DELTA_BUCKETS,
                    "bucket_bullcall": spread_core.BULLCALL_DELTA_BUCKETS}
_ALT_WORD = {"short": "更价外的", "long": "更便宜的"}
_TIE_PRECISION = 9  # round distances so float noise never decides a tie
_SECTION_TITLE = "### 把 Delta 目标带到下一步"
_CALL_ALL_HOLDINGS = "；它扫全部持股，`--delta` 对这次扫的每只持股都生效"
_COLLAR_TAIL = ("，它不按 Delta 选：按现价 90% / 85% / 80% 三档列出 Put{call}，"
                'Delta 目标带不过去；对照上表"参考价位"挑最接近的一档')
_COLLAR_CALL = "（Collar 的 Call 取零成本）"


class Handoff(NamedTuple):
    command: str                    # first command to run, extends NEXT_COMMANDS
    open_args: tuple[str, ...]      # `open T <bucket>` lines, main bucket first
    note: str                       # exact text that follows the command in the sentence


def _field(target, name: str):
    return target[name] if isinstance(target, dict) else getattr(target, name)


def _distance(bucket: float, low: float, high: float) -> float:
    """How far a bucket sits from [low, high]; 0 inside."""
    return round(max(low - bucket, bucket - high, 0), _TIE_PRECISION)


def nearest_bucket(low: float, high: float, buckets: Sequence[float],
                   leg: str) -> tuple[float, tuple[float, ...]]:
    """(main bucket, alternatives) for a target range (D1).

    Buckets inside the range: the upper one leads, the rest follow high to low. None inside:
    the nearest wins; a tie goes to the safer side (short legs lower Delta, long legs higher).
    """
    inside = sorted((b for b in buckets if low <= b <= high), reverse=True)
    if inside:
        return inside[0], tuple(inside[1:])
    prefer_low = leg.startswith("short")
    best = min(buckets, key=lambda b: (_distance(b, low, high), b if prefer_low else -b))
    return best, ()


def _range_of(delta_target: Sequence, leg: str) -> tuple[float, float]:
    for t in delta_target:
        if _field(t, "leg") == leg:
            return _field(t, "low"), _field(t, "high")
    raise ValueError(f"delta_target has no {leg} leg")


def _checked_range(strategy: str, kind: str, delta_target: Sequence) -> tuple[float, float]:
    low, high = _range_of(delta_target, _LEG_BY_KIND[kind])
    if kind == "bucket_condor" and _range_of(delta_target, "short_call") != (low, high):
        raise ValueError(f"{strategy}: both short legs must share one Delta target")
    return low, high


def _bucket_handoff(strategy: str, kind: str, delta_target: Sequence, ticker: str) -> Handoff:
    low, high = _checked_range(strategy, kind, delta_target)
    leg = _LEG_BY_KIND[kind]
    main, alts = nearest_bucket(low, high, _BUCKETS_BY_KIND[kind], leg)
    opens = tuple(f"open {ticker} {b:.2f}" for b in (main, *alts))
    note = f"，再 `{opens[0]}`"
    if alts:
        others = "、".join(f"`{o}`" for o in opens[1:])
        note += f"（或{_ALT_WORD[leg.split('_')[0]]} {others}）"
    return Handoff(T.NEXT_COMMANDS[strategy].format(ticker=ticker), opens, note)


def _screen_handoff(strategy: str, kind: str, delta_target: Sequence, ticker: str) -> Handoff:
    _, high = _checked_range(strategy, kind, delta_target)
    # The range's upper end is always a legal --delta; the walk-down then covers the rest.
    target = round(min(max(high, psr.DELTA_ARG_MIN), psr.DELTA_ARG_MAX), 2)
    command = T.NEXT_COMMANDS[strategy].format(ticker=ticker)
    if kind == "screen_put":
        command += f" --tickers {ticker}"
    if not psr.is_default_delta(target):
        command += f" --delta {target:.2f}"
    note = f"（目标 {target:.2f}，价差过宽时往外最多到 {psr.walk_floor(target):.2f}）"
    if kind == "screen_call":
        note += _CALL_ALL_HOLDINGS
    return Handoff(command, (), note)


def _collar_handoff(strategy: str, ticker: str) -> Handoff:
    call = _COLLAR_CALL if strategy == "collar" else ""
    return Handoff(T.NEXT_COMMANDS[strategy].format(ticker=ticker), (),
                   _COLLAR_TAIL.format(call=call))


def handoff(strategy: str, delta_target: Sequence, ticker: str) -> Handoff | None:
    """How to carry a pick's Delta target into its next command; None without a target."""
    if not delta_target:
        return None
    kind = HANDOFF_KIND[strategy]
    if kind == "no_delta_collar":
        return _collar_handoff(strategy, ticker)
    if kind in _BUCKETS_BY_KIND:
        return _bucket_handoff(strategy, kind, delta_target, ticker)
    return _screen_handoff(strategy, kind, delta_target, ticker)


def handoff_body(pick: dict, ticker: str) -> str | None:
    """Sentence without the `- label（delta）：` prefix (what the console prints)."""
    h = handoff(pick["strategy"], pick["delta_target"], ticker)
    if h is None:
        return None
    return ("先 " if h.open_args else "") + f"`{h.command}`" + h.note


def handoff_lines(result: dict, delta_text: Callable[[list], str]) -> list[str]:
    """Markdown section for every pick with a hand-off; [] when none has one.

    `delta_text` is the report's own "Delta target" formatter, passed in so the wording equals
    the conclusion table's column without importing the renderer.
    """
    lines = []
    for p in result["picks"]:
        body = handoff_body(p, result["ticker"])
        if body is not None:
            label = T.STRATEGY_LABELS[p["strategy"]]
            lines.append(f"- {label}（{delta_text(p['delta_target'])}）：{body}")
    return [_SECTION_TITLE, "", *lines, ""] if lines else []
