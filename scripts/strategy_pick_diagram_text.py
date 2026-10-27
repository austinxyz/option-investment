"""Node text for the strategy-pick decision-path diagram (design D7, D10). Pure, no I/O.

Every number comes from the result dict: thresholds from `thresholds_used` or from the Evidence
that `strategy_pick_steps.regime_of(result)` re-derives (the seven-step table's own source),
values from `metrics`, `iv`, `picks` and `gate`. Comparison symbols follow the result, never a
rounded number: a hit shows the condition's operator, a miss the reversed one, unknown "未知".
Privacy boundary: holding is only ever "已持有 / 未持有 / 持股不足一张" (no share count), the
portfolio share only as the two-decimal `portfolio_share=` flag shown as a whole percent, and the
thesis text, account names and the portfolio value never enter. Kept apart from the layout module
(strategy_pick_diagram and strategy_pick_diagram_frame import this one, never the other
way round).
"""
from __future__ import annotations

from dataclasses import dataclass

import strategy_pick_rules as R
import strategy_pick_steps as S
import strategy_pick_text as T
from strategy_pick_regime import DIRECTION_LABELS, HIT, UNKNOWN

LAYOUT_TREE, LAYOUT_BEAR, LAYOUT_CHAIN = "T", "B", "C"
GATE_STEP, HISTORY_STEP = 1, 2
CMP = {">=": "≥", "<=": "≤", ">": ">", "<": "<", "≥": "≥", "≤": "≤"}
FLIP = {"≥": "<", ">": "≤", "≤": ">", "<": "≥"}
UNKNOWN_TEXT = "未知"
WHOLE_PCT_TAIL = ".0%"
CIRCLED = "①②"
PICK_SEP = "   "
PAIR_SEP = " / "
RANGE_SEP = "–"
ITEM_SEP = "；"
DELTA_DIGITS = 2
SHARE_FLAG = "portfolio_share="
FAR_OTM_FLAG = "far_otm"
SOURCE_TEXT = {"user": "用户指定"}
DEFAULT_SOURCE = "wiki"
NOT_HELD_TEXT = "未持有"
HELD_TEXT = "已持有"
NO_LOT_ROOT = "持股不足一张"
NO_LOT_TREE_TAIL = f"按{NOT_HELD_TEXT}"
NOT_HELD_OR_NO_LOT = f"{NOT_HELD_TEXT}{PAIR_SEP}{NO_LOT_ROOT}"
REDUCE_HINT_FLAG = "reduce_hint"
RV_SELF_LINE = "RV21 自比（再退一步的代理）"


class DiagramError(ValueError):
    """The leaf table or result holds something the diagram has no display name or layout for."""


def lookup(table: dict, key, kind: str):
    try:
        return table[key]
    except KeyError:
        raise DiagramError(f"no display name for {kind} {key!r}") from None


@dataclass(frozen=True)
class PathSpec:
    layout: str               # T (subtree) / B (bear) / C (pre-tree chain)
    view: str | None
    branch: str | None
    regime: str | None
    iv: str | None
    stop_step: int | None     # layout C: chain box the run stopped in (1-based)


def path_of(result: dict) -> PathSpec:
    leaf = result["leaf_id"]
    if leaf == R.LEAF_GATE_FAIL:
        return PathSpec(LAYOUT_CHAIN, None, None, None, None, GATE_STEP)
    if leaf == R.LEAF_HISTORY_SHORT:
        return PathSpec(LAYOUT_CHAIN, None, None, None, None, HISTORY_STEP)
    parts = leaf.split(".")
    if len(parts) == 2 and parts[0] in R.COLLAPSED_VIEWS:
        return PathSpec(LAYOUT_BEAR, parts[0], parts[1], None, None, None)
    if len(parts) == 4:
        return PathSpec(LAYOUT_TREE, *parts, None)
    raise DiagramError(f"leaf id {leaf!r} has no diagram layout")


def cmp_symbol(op: str, hit: bool | None) -> str:
    sym = lookup(CMP, op, "comparison")
    if hit is None:
        return UNKNOWN_TEXT
    return sym if hit else FLIP[sym]


def thr_pct(x: float) -> str:
    """Threshold as a percent; a whole percent drops the decimal ("35%", not "35.0%")."""
    text = S.pct(x)
    return text.replace(WHOLE_PCT_TAIL, "%") if text.endswith(WHOLE_PCT_TAIL) else text


# ---- regime node ----

def _find(reg, metric: str, op: str):
    return next((e for e in reg.evidence if e.name.split()[:2] == [metric, op]), None)


def _line(e, op: str, label: str, value: str, threshold: str) -> str:
    if e.result == UNKNOWN:
        return f"{label} {UNKNOWN_TEXT}"
    return f"{label} {value} {cmp_symbol(op, e.result == HIT)} {threshold}"


def _r1y(reg, op: str) -> str:
    e = _find(reg, "R1y", op)
    return _line(e, op, "一年", S.signed_pct(e.value), thr_pct(e.threshold))


def _close(reg, op: str) -> str:
    e = _find(reg, "Pn", op)
    return _line(e, op, "收盘", S.num(e.value), f"MA200 {S.num(e.threshold)}")


def _slope(reg, op: str, result: dict) -> str:
    e = _find(reg, "MA200_slope", op)
    if e.result == UNKNOWN:
        limit = result["thresholds_used"]["MIN_SLOPE_DAYS"]
        return f"MA200 斜率未知（历史 {result['metrics']['n_days']} < {limit} 日）"
    return _line(e, op, "MA200 斜率", S.signed_pct(e.value, 2), "0")


def _ma50(reg) -> str | None:
    e = _find(reg, "MA50", ">")
    return None if e is None else _line(e, ">", "MA50", S.num(e.value), "MA200")


def _pct_line(reg, metric: str, op: str, label: str) -> str:
    e = _find(reg, metric, op)
    return _line(e, op, label, S.pct(e.value), thr_pct(e.threshold))


def _vol_dd(reg) -> tuple[str, str]:
    return (_pct_line(reg, "RV1y", ">=", "波动率"), _pct_line(reg, "MaxDD", ">=", "回撤"))


def _touches(reg) -> str:
    hi, lo = _find(reg, "TouchesHigh", ">="), _find(reg, "TouchesLow", ">=")
    label = "上沿 / 下沿触碰"
    if UNKNOWN in (hi.result, lo.result):
        return f"{label} {UNKNOWN_TEXT}"
    hit = hi.result == HIT and lo.result == HIT
    return (f"{label} {int(hi.value)} / {int(lo.value)} {cmp_symbol('>=', hit)} "
            f"{int(hi.threshold)}")


def _steady(reg, result: dict) -> tuple:
    return (_r1y(reg, ">="), _close(reg, ">"), _slope(reg, ">", result), _ma50(reg),
            *_vol_dd(reg))


def _high_vol(reg, result: dict) -> tuple:
    return (*_vol_dd(reg), f"方向：{DIRECTION_LABELS[reg.direction_tag]}")


def _range(reg, result: dict) -> tuple:
    return (_pct_line(reg, "|R1y|", "<=", "一年回报绝对值"),
            _pct_line(reg, "RangeWidth", "<=", "区间宽度"), _touches(reg), *_vol_dd(reg))


def _down(reg, result: dict) -> tuple:
    return (_r1y(reg, "<="), _close(reg, "<"), _slope(reg, "<", result), *_vol_dd(reg))


def _unclear(reg, result: dict) -> tuple:
    ma50 = _ma50(reg) if reg.direction_tag == "up" else None
    return (_r1y(reg, ">="), _close(reg, ">"), _slope(reg, ">", result), ma50, *_vol_dd(reg))


_REGIME_WRITERS = {"steady_up": _steady, "high_vol": _high_vol, "range": _range,
                   "down": _down, "unclear": _unclear}


def regime_lines(result: dict) -> tuple[str, ...]:
    reg = S.regime_of(result)
    writer = lookup(_REGIME_WRITERS, reg.leaf, "regime")
    return (reg.label,) + tuple(ln for ln in writer(reg, result) if ln)


# ---- IV node ----

def iv_lines(result: dict) -> tuple[str, ...]:
    iv, used = result["iv"], result["thresholds_used"]
    name = lookup(S.IV_LABELS, iv["label"], "IV label")
    # Same text as the seven-step table's IV rows (whole percent, pct(x, 0)).
    p, hi, lo = S.pct(iv["percentile"]), S.pct(used["IV_HIGH_PCTL"], 0), S.pct(used["IV_LOW_PCTL"], 0)
    if iv["label"] == R.IV_HIGH:
        place = f"百分位 {p} {CMP['>=']} {hi}"
    elif iv["label"] == R.IV_LOW:
        place = f"百分位 {p} {CMP['<=']} {lo}"
    else:
        place = f"{lo} < {p} < {hi}"  # the narrow IV column cannot also fit "百分位"
    atm = "平值 IV 缺失" if iv["atm_iv"] is None else f"平值 IV {S.num(iv['atm_iv'])}"
    lines = (f"IV {name}", place, atm)
    return lines + ((RV_SELF_LINE,) if iv["method"] == R.IV_METHOD_FALLBACK else ())


# ---- root and branch nodes ----

def _holding_state(result: dict) -> str:
    h = result["holding"]
    if h["treated_as_held"]:
        return R.BRANCH_HELD
    return R.BRANCH_NO_LOT if h["held_shares_total"] > 0 else R.BRANCH_NOT_HELD


def held_title(result: dict) -> str:
    return f"已持有（单账户 ≥ {result['thresholds_used']['MIN_LOT_SHARES']:g} 股）"


def source_text(result: dict) -> str:
    return SOURCE_TEXT.get(result["view_source"], DEFAULT_SOURCE)


def no_lot_text(result: dict) -> str:
    """The 96-wide root column cannot hold the per-account rule; the subtitle states it."""
    return f"持股不足 {result['thresholds_used']['MIN_LOT_SHARES']:g} 股"


def root_lines(result: dict) -> tuple[str, ...]:
    view = lookup(S.VIEW_LABELS, result["view"], "view")
    source = f"来源：{source_text(result)}"
    state = _holding_state(result)
    if path_of(result).layout == LAYOUT_BEAR:
        if state == R.BRANCH_NO_LOT:
            return (view, no_lot_text(result), source)
        return (view, source)
    if state == R.BRANCH_NO_LOT:
        return (view, no_lot_text(result), NO_LOT_TREE_TAIL, source)
    return (view, HELD_TEXT if state == R.BRANCH_HELD else NOT_HELD_TEXT, source)


def bear_branch_lines(result: dict, branch: str, on: bool) -> tuple[str, ...]:
    if branch == R.BRANCH_HELD:
        return (held_title(result),)
    if on and _holding_state(result) == R.BRANCH_NO_LOT:
        return (NOT_HELD_OR_NO_LOT, f"提示：{lookup(T.FLAG_LABELS, REDUCE_HINT_FLAG, 'flag')}")
    return (NOT_HELD_OR_NO_LOT,)


# ---- leaf node ----

def _label(strategy: str) -> str:
    return lookup(T.STRATEGY_LABELS, strategy, "strategy")


def _table_label(strategy: str) -> str:
    if strategy == R.SELL_PAIR:
        return PAIR_SEP.join(_label(k) for k in (R.CSP_KEY, R.BULL_PUT_KEY))
    return _label(strategy)


def pick_title(strategies: list[str]) -> str:
    if len(strategies) > len(CIRCLED):
        raise DiagramError("more picks than circled digits")
    labels = [_label(s) for s in strategies]
    if len(labels) == 1:
        return labels[0]
    return PICK_SEP.join(f"{CIRCLED[i]} {lab}" for i, lab in enumerate(labels))


def rank_line(result: dict) -> str | None:
    ranked, share = S.rank_note(result)
    if ranked is None:
        return None
    used, cls = result["thresholds_used"], result["asset_class"]
    first = next(p["strategy"] for p in result["picks"]
                 if p["strategy"] in (R.CSP_KEY, R.BULL_PUT_KEY))
    tail = f" → {_label(first)} 排前"
    if share is None:
        return "组合总值取不到，只按类别" + tail
    if cls not in used["CSP_FIRST_CLASSES"]:
        return f"类别 {lookup(S.CLASS_LABELS, cls, 'class')}不在优先类" + tail
    sym = cmp_symbol("<=", first == R.CSP_KEY)
    return f"占组合约 {S.pct(share, 0)} {sym} {thr_pct(used['CSP_MAX_PORTFOLIO_SHARE'])}" + tail


def _span(t: dict, digits: int) -> str:
    lo, hi = f"{t['low']:.{digits}f}", f"{t['high']:.{digits}f}"
    return lo if lo == hi else f"{lo}{RANGE_SEP}{hi}"


def _ref_line(result: dict) -> str | None:
    pick = next((p for p in result["picks"] if p["delta_target"]), None)
    if pick is None:
        return None
    if not pick["reference_level"]:
        return lookup(T.WARN, "ref_missing_iv", "warning")
    digits = int(result["thresholds_used"]["REF_DECIMALS"])
    legs = ITEM_SEP.join(f"{lookup(T.LEG_LABELS, t['leg'], 'leg')} ≈ {_span(t, digits)}"
                         for t in pick["reference_level"])
    return f"参考{legs}（粗略）"


def _flag_text(flag: str, pick: dict) -> str:
    text = lookup(T.FLAG_LABELS, flag, "flag")
    if flag == FAR_OTM_FLAG and pick["delta_target"]:
        return f"{text}（Delta {_span(pick['delta_target'][0], DELTA_DIGITS)}）"
    return text


def _flag_lines(picks: list[dict]) -> list[str]:
    out = []
    for i, p in enumerate(picks):
        shown = [f for f in dict.fromkeys(p["flags"]) if not f.startswith(SHARE_FLAG)
                 and f != T.CLASS_ONLY_FLAG and f not in T.HIDDEN_FLAGS]
        if shown:
            prefix = f"{CIRCLED[i]} " if len(picks) > 1 else ""
            out.append(prefix + ITEM_SEP.join(_flag_text(f, p) for f in shown))
    return out


def _leaf_table(result: dict) -> tuple[list[str], list[str]]:
    entry = result["thresholds_used"]["LEAF_TABLE"][result["leaf_id"]]
    return [p["strategy"] for p in entry["picks"]], list(entry["warnings"])


def _expand(strategies: list[str]) -> list[str]:
    out: list[str] = []
    for s in strategies:
        out += [R.CSP_KEY, R.BULL_PUT_KEY] if s == R.SELL_PAIR else [s]
    return out


def _earnings_line(result: dict, picked: list[str]) -> str | None:
    if not picked or picked[0] != R.WAIT_EARNINGS:
        return None
    gone = [s for s in _expand(_leaf_table(result)[0]) if s not in picked]
    head = f"财报 {result['earnings']['days']} 天内，先等财报"
    return head + (f"（原第二 {_label(gone[-1])} 让位）" if gone else "")


def _table_line(result: dict, picked: list[str]) -> str | None:
    table = _leaf_table(result)[0]
    if set(_expand(table)) == set(picked):
        return None
    return "叶子表：" + " + ".join(_table_label(s) for s in table)


def _warn_lines(result: dict) -> list[str]:
    texts = [lookup(T.WARN, k, "warning") for k in _leaf_table(result)[1]]
    return [t for t in texts if "{" not in t]


def leaf_lines(result: dict) -> tuple[str, ...]:
    picked = [p["strategy"] for p in result["picks"]]
    lines = [pick_title(picked), rank_line(result), _ref_line(result),
             *_flag_lines(result["picks"]), _earnings_line(result, picked),
             *_warn_lines(result), _table_line(result, picked)]
    return tuple(ln for ln in lines if ln)
