"""Tests for the /strategy-pick regime classifier (design 6.5, 6.6, section 10).

Metrics are built by hand from the section-10 numbers (ZZZQ, invented); the history-length
cases go through compute_metrics so the real None-slope path is exercised.
"""
import dataclasses
import re

import pytest

from strategy_pick_fixtures import REPO_ROOT, base_closes, weekday_dates
import strategy_pick_rules as rules
from strategy_pick_metrics import Metrics, compute_metrics
from strategy_pick_regime import (Evidence, Regime, classify, direction_tag, recent_change_hint,
                                  regime_leaf, vol_tag)

PRICE = 100.0  # Pn of the worked example


def metrics(**over) -> Metrics:
    """Section 10 ZZZQ: R1y +25%, MA50 96 / MA200 90, slope +2.04%, MaxDD 15%, RV1y 30%."""
    base = dict(n_days=252, last_close=PRICE, r1y=0.25, ma50=96.0, ma200=90.0, ma200_slope=0.0204, max_dd=0.15,
                rv1y=0.30, rv21_series=(), rv21_current=0.26, range_width=0.283,
                touches_high=3, touches_low=3, r3m=0.087, rv63=0.27, crossed_ma200_63d=False,
                dollar_volume_median=386.4e6, missing_sessions=0, history_short=False,
                history_under_year=False)
    base.update(over)
    return Metrics(**base)


def th(cls="bluechip"):
    return rules.thresholds_for(cls)


def run(m, cls="bluechip", price=PRICE):
    return classify(dataclasses.replace(m, last_close=price), cls)


def test_base_example_is_steady_up():
    r = run(metrics())
    assert (r.vol_tag, r.direction_tag, r.leaf) == ("normal", "up", "steady_up")
    assert r.warnings == []
    assert not r.hint.changed  # R3m same sign, RV63/RV1y = 0.90, no cross


def test_base_fixture_series_gives_up_direction():
    closes = base_closes()
    m = compute_metrics(closes, [4_200_000.0] * 252, weekday_dates(252))
    assert direction_tag(m, th())[0] == "up"


def test_v3_rv_40_is_high_vol_with_up_sublabel():
    r = run(metrics(rv1y=0.40))
    assert (r.vol_tag, r.direction_tag, r.leaf) == ("high", "up", "high_vol")
    assert "高波动" in r.label and "上行" in r.label


def test_v4_hot_class_same_numbers_steady_up_at_equal_return():
    # hot: UP_RETURN 0.25, VOL_HIGH 0.60, DD_HIGH 0.40; R1y 0.25 reaches it exactly.
    r = run(metrics(r1y=0.25), cls="hot")
    assert (r.vol_tag, r.direction_tag, r.leaf) == ("normal", "up", "steady_up")
    below = run(metrics(r1y=0.2499), cls="hot")
    assert below.direction_tag != "up"


@pytest.mark.parametrize("field,value,thr", [("rv1y", 0.35, "VOL_HIGH"), ("max_dd", 0.25, "DD_HIGH")])
def test_vol_high_at_exact_threshold(field, value, thr):
    tag, ev = vol_tag(metrics(**{field: value}), th())
    assert tag == "high"
    assert any(e.constant == thr and e.result == "hit" for e in ev)
    just_under = vol_tag(metrics(**{field: value - 0.0001}), th())[0]
    assert just_under == "normal"


def test_down_before_range():
    # Flat year (|R1y| 4%, narrow, 3 touches each side) under a falling MA200: down wins.
    m = metrics(r1y=0.04, ma200=100.0, ma200_slope=-0.01, range_width=0.2)
    assert direction_tag(dataclasses.replace(m, last_close=95.0), th())[0] == "down"
    assert direction_tag(dataclasses.replace(m, last_close=105.0), th())[0] == "range"  # above MA200: no down rule


def test_down_on_return_alone_at_boundary():
    m = metrics(r1y=-0.10, ma200_slope=0.01)
    assert direction_tag(dataclasses.replace(m, last_close=PRICE), th())[0] == "down"  # -FLAT_RETURN reached


def test_range_needs_two_touches_each_side():
    flat = dict(r1y=0.02, ma200_slope=0.005, range_width=0.3)
    assert direction_tag(dataclasses.replace(metrics(touches_high=2, touches_low=2, **flat), last_close=PRICE), th())[0] == "range"
    for kw in ({"touches_high": 1}, {"touches_low": 1}):
        m = metrics(**{**flat, "touches_high": 2, "touches_low": 2, **kw})
        assert direction_tag(dataclasses.replace(m, last_close=PRICE), th())[0] == "unclear"


def test_range_width_boundary_inclusive():
    m = metrics(r1y=0.0, ma200_slope=0.005, range_width=0.35)
    assert direction_tag(dataclasses.replace(m, last_close=PRICE), th())[0] == "range"
    m2 = metrics(r1y=0.0, ma200_slope=0.005, range_width=0.3501)
    assert direction_tag(dataclasses.replace(m2, last_close=PRICE), th())[0] == "unclear"


def test_up_but_ma50_below_ma200_is_unclear_with_label():
    r = run(metrics(ma50=89.0))
    assert (r.vol_tag, r.direction_tag, r.leaf) == ("normal", "up", "unclear")
    assert "上行但均线未排好" in r.label
    assert run(metrics(ma50=90.0)).leaf == "unclear"  # MA50 == MA200 is not above


def test_regime_leaf_table():
    m = metrics()
    assert regime_leaf("normal", "range", m)[0] == "range"
    assert regime_leaf("normal", "down", m)[0] == "down"
    assert regime_leaf("normal", "unclear", m)[0] == "unclear"
    assert regime_leaf("high", "down", m)[0] == "high_vol"
    assert "下行" in regime_leaf("high", "down", m)[1]


def test_slope_unknown_forces_unclear_and_warns():
    closes = [100.0 - 0.1 * i for i in range(219)]  # falls ~21%: would be "down" if guessed
    m = compute_metrics(closes, [1e6] * 219, weekday_dates(219))
    assert m.ma200_slope is None and m.r1y < -0.15
    r = classify(m, "bluechip")
    assert r.direction_tag == "unclear"
    assert any("220" in w and "斜率" in w for w in r.warnings)
    assert any(e.result == "unknown" for e in r.evidence)


def test_recent_hint_each_rule():
    h = recent_change_hint(metrics(r3m=-0.05))               # opposite sign, |R3m| = 5% exactly
    assert h.changed and len(h.reasons) == 1
    assert not recent_change_hint(metrics(r3m=-0.0499)).changed
    assert recent_change_hint(metrics(rv63=0.45)).changed     # 0.45 / 0.30 = 1.5 exactly
    assert not recent_change_hint(metrics(rv63=0.449)).changed
    assert recent_change_hint(metrics(rv63=0.18)).changed     # 0.18 / 0.30 = 0.6 exactly
    assert not recent_change_hint(metrics(rv63=0.181)).changed
    assert recent_change_hint(metrics(crossed_ma200_63d=True)).changed


def test_recent_hint_base_consistent():
    h = recent_change_hint(metrics(rv63=0.27))  # 0.27 / 0.30 = 0.90
    assert not h.changed and h.reasons == []


def test_recent_hint_missing_recent_fields_are_skipped():
    assert not recent_change_hint(metrics(r3m=None, rv63=None)).changed


def _compare(name, value, threshold):
    op = re.search(r" (>=|<=|>|<) ", name).group(1)
    return {">=": value >= threshold, "<=": value <= threshold,
            ">": value > threshold, "<": value < threshold}[op]


@pytest.mark.parametrize("m,price", [
    (metrics(), PRICE),
    (metrics(rv1y=0.35), PRICE),
    (metrics(r1y=-0.2), 80.0),
    (metrics(r1y=0.0, range_width=0.3, ma200_slope=0.0), PRICE),
    (metrics(r3m=-0.06, rv63=0.5, crossed_ma200_63d=True), PRICE),
])
def test_evidence_values_match_thresholds(m, price):
    r = classify(dataclasses.replace(m, last_close=price), "bluechip")
    evs = list(r.evidence) + list(r.hint.evidence)
    assert evs
    for e in evs:
        assert isinstance(e, Evidence)
        if e.result == "unknown":
            continue
        assert e.result == ("hit" if _compare(e.name, e.value, e.threshold) else "miss"), e


def test_rv_30_vs_threshold_35_is_not_high_vol():
    tag, ev = vol_tag(metrics(rv1y=0.30), th())
    rv = next(e for e in ev if e.constant == "VOL_HIGH")
    assert tag == "normal" and rv.result == "miss"


def test_classify_reads_thresholds_from_rules(monkeypatch):
    assert run(metrics(rv1y=0.40)).vol_tag == "high"
    patched = {k: dict(v) for k, v in rules.CLASS_THRESHOLDS.items()}
    patched["bluechip"]["VOL_HIGH"] = 0.50
    monkeypatch.setattr(rules, "CLASS_THRESHOLDS", patched)
    assert run(metrics(rv1y=0.40)).vol_tag == "normal"
    patched["bluechip"]["UP_RETURN"] = 0.30
    assert run(metrics(rv1y=0.40)).direction_tag != "up"  # R1y 0.25 < 0.30


def test_regime_and_evidence_are_frozen():
    r = run(metrics())
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.leaf = "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.evidence[0].result = "x"
    assert isinstance(r, Regime)


def test_no_threshold_literals_in_regime_module():
    src = (REPO_ROOT / "scripts" / "strategy_pick_regime.py").read_text(encoding="utf-8")
    for lit in ("0.35", "0.25", "0.12", "0.10", "0.60", "0.40", "0.05", "1.5", "0.6", "220"):
        assert not re.search(rf"(?<![\w.]){re.escape(lit)}(?![\w.])", src), lit
