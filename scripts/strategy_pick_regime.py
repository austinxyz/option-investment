#!/usr/bin/env python3
"""Regime classifier for /strategy-pick (pure, no I/O).

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.6). Every threshold arrives
through the `th` dict (strategy_pick_rules.thresholds_for) or a named rules constant, and every
comparison is recorded as an Evidence so the report can show value / threshold / constant name /
result without re-deriving anything. All comparisons are inclusive where the spec says "reaching".
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field

from strategy_pick_metrics import Metrics
from strategy_pick_rules import (RECENT_RETURN_MIN, RECENT_RV_RATIO_HIGH, RECENT_RV_RATIO_LOW,
                                 thresholds_for)

_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
HIT, MISS, UNKNOWN = "hit", "miss", "unknown"
DIRECTION_LABELS = {"up": "上行", "down": "下行", "range": "盘整", "unclear": "不明"}


@dataclass(frozen=True)
class Evidence:
    name: str          # reads "<metric> <op> <CONSTANT>"; the operator is the comparison made
    value: float | None
    threshold: float | None
    constant: str
    result: str        # hit / miss / unknown (unknown = an input was missing, never guessed)


@dataclass(frozen=True)
class RecentHint:
    changed: bool
    reasons: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True)
class Regime:
    vol_tag: str
    direction_tag: str
    leaf: str
    label: str
    hint: RecentHint
    evidence: list[Evidence]
    warnings: list[str]


def _ev(metric: str, value, op: str, threshold, constant: str) -> Evidence:
    if value is None or threshold is None:
        result = UNKNOWN
    else:
        result = HIT if _OPS[op](value, threshold) else MISS
    return Evidence(f"{metric} {op} {constant}", value, threshold, constant, result)


def _hit(*evidence: Evidence) -> bool:
    return all(e.result == HIT for e in evidence)


def vol_tag(m: Metrics, th: dict) -> tuple[str, list[Evidence]]:
    rv = _ev("RV1y", m.rv1y, ">=", th["VOL_HIGH"], "VOL_HIGH")
    dd = _ev("MaxDD", m.max_dd, ">=", th["DD_HIGH"], "DD_HIGH")
    return ("high" if rv.result == HIT or dd.result == HIT else "normal"), [rv, dd]


def _up_evidence(m: Metrics, th: dict, price) -> list[Evidence]:
    return [_ev("R1y", m.r1y, ">=", th["UP_RETURN"], "UP_RETURN"),
            _ev("Pn", price, ">", m.ma200, "MA200"),
            _ev("MA200_slope", m.ma200_slope, ">", 0.0, "ZERO")]


def _down_evidence(m: Metrics, th: dict, price) -> tuple[Evidence, list[Evidence]]:
    by_return = _ev("R1y", m.r1y, "<=", -th["FLAT_RETURN"], "FLAT_RETURN")
    below = [_ev("Pn", price, "<", m.ma200, "MA200"),
             _ev("MA200_slope", m.ma200_slope, "<", 0.0, "ZERO")]
    return by_return, below


def _range_evidence(m: Metrics, th: dict) -> list[Evidence]:
    return [_ev("|R1y|", abs(m.r1y), "<=", th["FLAT_RETURN"], "FLAT_RETURN"),
            _ev("RangeWidth", m.range_width, "<=", th["RANGE_MAX"], "RANGE_MAX"),
            _ev("TouchesHigh", m.touches_high, ">=", th["MIN_TOUCHES"], "MIN_TOUCHES"),
            _ev("TouchesLow", m.touches_low, ">=", th["MIN_TOUCHES"], "MIN_TOUCHES")]


def _slope_unknown_warning(th: dict) -> str:
    return (f"历史不足 {int(th['MIN_SLOPE_DAYS'])} 天，MA200 斜率无法计算，方向记为不明")


def direction_tag(m: Metrics, th: dict) -> tuple[str, list[Evidence]]:
    """Order is fixed: up, down, range, unclear. Unknown slope or price never becomes a guess."""
    price = m.last_close
    up = _up_evidence(m, th, price)
    if m.ma200_slope is None:
        return "unclear", up
    by_return, below = _down_evidence(m, th, price)
    rng = _range_evidence(m, th)
    evidence = up + [by_return] + below + rng
    if _hit(*up):
        return "up", evidence
    if _hit(by_return) or _hit(*below):
        return "down", evidence
    if _hit(*rng):
        return "range", evidence
    return "unclear", evidence


def regime_leaf(vol: str, direction: str, m: Metrics) -> tuple[str, str]:
    dir_label = DIRECTION_LABELS[direction]
    if vol == "high":
        return "high_vol", f"高波动 + {dir_label}"
    if direction == "up":
        if _ma50_above_ma200(m):
            return "steady_up", "稳步上涨"
        return "unclear", "上行但均线未排好"
    return direction, dir_label


def _ma50_above_ma200(m: Metrics) -> bool:
    return _hit(_ev("MA50", m.ma50, ">", m.ma200, "MA200"))


def recent_change_hint(m: Metrics) -> RecentHint:
    evidence, reasons = [], []
    if m.r3m is not None:
        opposite = _ev("R3m*R1y", m.r3m * m.r1y, "<", 0.0, "ZERO")
        size = _ev("|R3m|", abs(m.r3m), ">=", RECENT_RETURN_MIN, "RECENT_RETURN_MIN")
        evidence += [opposite, size]
        if _hit(opposite, size):
            reasons.append("3 个月回报与全年回报方向相反且幅度达到下限")
    if m.rv63 is not None and m.rv1y:
        ratio = m.rv63 / m.rv1y
        high = _ev("RV63/RV1y", ratio, ">=", RECENT_RV_RATIO_HIGH, "RECENT_RV_RATIO_HIGH")
        low = _ev("RV63/RV1y", ratio, "<=", RECENT_RV_RATIO_LOW, "RECENT_RV_RATIO_LOW")
        evidence += [high, low]
        if _hit(high) or _hit(low):
            reasons.append("最近 63 日波动率与全年波动率差异显著")
    if m.crossed_ma200_63d:
        reasons.append("最近 63 个交易日收盘价穿过 MA200")
    return RecentHint(bool(reasons), reasons, evidence)


def _warnings(m: Metrics, th: dict) -> list[str]:
    return [_slope_unknown_warning(th)] if m.ma200_slope is None else []


def classify(m: Metrics, asset_class: str) -> Regime:
    th = thresholds_for(asset_class)
    vol, vol_ev = vol_tag(m, th)
    direction, dir_ev = direction_tag(m, th)
    leaf, label = regime_leaf(vol, direction, m)
    evidence = vol_ev + dir_ev
    if vol == "normal" and direction == "up":
        evidence.append(_ev("MA50", m.ma50, ">", m.ma200, "MA200"))
    hint = recent_change_hint(m)
    return Regime(vol, direction, leaf, label, hint, evidence, _warnings(m, th))
