"""Chain boxes (layout C), title, subtitle, footer and alt text of the decision-path diagram.

Split from strategy_pick_diagram_text to keep each module under 400 lines; same rules: numbers
and thresholds only from the result dict, symbols by result, no share counts, no thesis text,
no portfolio value. Imports strategy_pick_diagram_text; neither text module imports the layout.
"""
from __future__ import annotations

import strategy_pick_rules as R
import strategy_pick_steps as S
from strategy_pick_diagram_text import (GATE_STEP, HISTORY_STEP, LAYOUT_CHAIN, LAYOUT_TREE,
                                        PathSpec, cmp_symbol, lookup, path_of, pick_title,
                                        source_text)

GATE_NAME = "流动性门槛"
CHAIN_TITLES = (f"① {GATE_NAME}", "② 历史长度", "③ 看法与持有", "④ 决策树")
NO_ADVICE = "不做期权建议"
ALL_PASSED = "全部通过"
MAX_GATE_LINES = 5
GATE_FAIL, GATE_UNCHECKED = "fail", "unchecked"
NO_OPTIONS_METRIC = "Expiries90d"
NO_OPTIONS_TEXT = "没有期权数据"
MISSING = "未取到"
LEGEND = "高亮 = 本次走过的路径；节点里的小字是报告里的实际数值，灰淡的是没走到的分支。"
DISCLAIMER = "不构成投资建议。"
GATE_LABELS = {"MarketCap": "市值", "Price": "股价", "DollarVolume": "日均成交额",
               "Expiries90d": "90 天内到期日", "Strikes15pct": "±15% 内行权价",
               "PutSpreadPct": "平值 Put 价差", "CallSpreadPct": "平值 Call 价差",
               "AtmOI": "平值 OI"}
GATE_MONEY = frozenset({"MarketCap", "DollarVolume"})
GATE_PCT = frozenset({"PutSpreadPct", "CallSpreadPct"})
GATE_INT = frozenset({"Expiries90d", "Strikes15pct", "AtmOI"})


# ---- layout C chain ----

def _gate_value(metric: str, value) -> str:
    if value is None:
        return MISSING
    if metric in GATE_MONEY:
        return S.money(value)
    if metric in GATE_PCT:
        return S.pct(value, 2)
    return str(int(value)) if metric in GATE_INT else S.num(value)


def _gate_line(item: dict, ticker: str) -> str:
    metric, op = item["name"].split()[:2]
    if metric == "ticker":
        return f"{ticker} 在杠杆 ETF 名单内"
    if metric == NO_OPTIONS_METRIC and item["value"] == 0:
        return NO_OPTIONS_TEXT
    label = lookup(GATE_LABELS, metric, "gate item")
    if item["value"] is None:
        return f"{label} {MISSING}"
    return (f"{label} {_gate_value(metric, item['value'])} {cmp_symbol(op, False)} "
            f"{_gate_value(metric, item['threshold'])}")


def gate_lines(result: dict) -> tuple[str, ...]:
    items = result["gate"]["items"]
    fails = [i for i in items if i["result"] == GATE_FAIL]
    lines = [_gate_line(i, result["ticker"]) for i in fails[:MAX_GATE_LINES]]
    if len(fails) > MAX_GATE_LINES:
        lines.append(f"另 {len(fails) - MAX_GATE_LINES} 项不过")
    unchecked = sum(1 for i in items if i["result"] == GATE_UNCHECKED)
    if unchecked:
        lines.append(f"未检查 {unchecked} 项")
    return tuple(lines)


def chain_lines(result: dict) -> tuple[tuple[str, ...], ...]:
    boxes = [(t,) for t in CHAIN_TITLES]
    stop = path_of(result).stop_step
    if stop == GATE_STEP:
        boxes[0] = (CHAIN_TITLES[0],) + gate_lines(result)
    elif stop == HISTORY_STEP:
        limit = result["thresholds_used"]["MIN_HISTORY_DAYS"]
        boxes[0] = (CHAIN_TITLES[0], ALL_PASSED)
        boxes[1] = (CHAIN_TITLES[1],
                    f"交易日 {result['metrics']['n_days']} < {limit}，不分类、不给策略")
    return tuple(boxes)


# ---- title, subtitle, footer, alt text ----

def _path_head(result: dict, path: PathSpec) -> str:
    if path.layout == LAYOUT_CHAIN:
        return "门槛出局" if path.stop_step == GATE_STEP else "历史不足"
    parts = [lookup(S.VIEW_LABELS, path.view, "view"),
             lookup(S.HELD_LABELS, path.branch, "branch")]
    if path.layout == LAYOUT_TREE:
        parts += [lookup(S.REGIME_LABELS, path.regime, "regime"),
                  f"IV {lookup(S.IV_LABELS, path.iv, 'IV label')}"]
    return " × ".join(parts)


def diagram_title(result: dict) -> str:
    path = path_of(result)
    head = _path_head(result, path)
    if path.layout == LAYOUT_TREE:
        head = " × ".join(head.split(" × ")[:2])
    return f"{result['ticker']} 的决策路径（{head}）"


def diagram_alt(result: dict) -> str:
    """The `<desc>` sentence, also the Markdown image alt text (so never a "]")."""
    path = path_of(result)
    picks = (NO_ADVICE if path.layout == LAYOUT_CHAIN
             else pick_title([p["strategy"] for p in result["picks"]]))
    text = f"{result['ticker']} 的决策路径：{_path_head(result, path)} → {picks}"
    return text.replace("[", "（").replace("]", "）")


def subtitle(result: dict) -> tuple[str, str]:
    stop = path_of(result).stop_step
    n, limit = result["metrics"]["n_days"], result["thresholds_used"]["MIN_HISTORY_DAYS"]
    if stop == GATE_STEP:
        return f"停在：{CHAIN_TITLES[0]}", LEGEND
    if stop == HISTORY_STEP:
        return f"已通过：{GATE_NAME} · 停在：{CHAIN_TITLES[1]}（历史 {n} 日 < {limit}）", LEGEND
    cls = lookup(S.CLASS_LABELS, result["asset_class"], "class")
    view = lookup(S.VIEW_LABELS, result["view"], "view")
    lot = result["thresholds_used"]["MIN_LOT_SHARES"]
    return (f"已通过：{GATE_NAME} · 历史 {n} 日 ≥ {limit} · 类别 {cls} · "
            f"看法 {view}（{source_text(result)}） · 持有 = 单账户 ≥ {lot:g} 股"), LEGEND


def _iv_footer(result: dict) -> str:
    n = result["metrics"]["rv21_window_count"]
    if result["iv"]["method"] == R.IV_METHOD_FALLBACK:
        return f"IV 位置是代理：IV 缺失，当前 RV21 在过去一年 {n} 个 RV21 窗口里的百分位"
    return f"IV 位置是代理：平值 IV 在过去一年 {n} 个 RV21 窗口里的百分位"


def footer(result: dict, source_label: str | None = None) -> tuple[str, ...]:
    """`source_label` is the human data-source name ("Yahoo" / "Demo") the caller passes in
    non-IBKR mode; None (IBKR) adds nothing. The result dict carries no such field."""
    lines = [_iv_footer(result)] if path_of(result).layout == LAYOUT_TREE else []
    stamp = f"行情快照 {result['snapshot_date']} · 运行日 {result['run_date']}"
    if source_label:
        stamp += f" · 数据源 {source_label}"
    lines += [stamp, f"警告 {len(result['warnings'])} 条，见报告「警告」一节", DISCLAIMER]
    return tuple(lines)
