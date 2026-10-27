#!/usr/bin/env python3
"""Seven-step table rows and number formatting for the /strategy-pick report (design 6.13).

Every row is (step, item, value, threshold, constant, result). Thresholds are read from the
result's own `thresholds_used`, so the table can never show a number the run did not use.
The regime comparisons are not stored in the JSON (6.13 fixes its fields), so they are
re-derived by running the same classify() on the stored metrics; in one run that is the same
code on the same numbers, hence the same Evidence. The table is therefore a run-time product:
re-rendering a JSON written earlier would use the rules constants as they are now.
"""
from __future__ import annotations

from dataclasses import fields

import strategy_pick_text as T
from strategy_pick_metrics import Metrics
from strategy_pick_regime import DIRECTION_LABELS, Regime, classify

STEP_HEADER = ("步", "项目", "值", "阈值", "常量", "结果")
VIEW_LABELS = {"bull": "看好", "neutral": "中性", "bear": "不看好"}
# Statuses STATUS_TO_VIEW does not map; the mapped ones stay as the wiki writes them.
STATUS_LABELS = {"under_review": "复审中", "unrecognized": "无法识别"}
CLASS_LABELS = {"bluechip": "大盘蓝筹", "hot": "热门赛道热门股", "etf": "ETF"}
CLASS_SOURCES = {"cli": "--class 指定", "etf_page": "wiki/etf 页存在", "quote_type": "yfinance quoteType",
                 "etf_table": "持仓 ETF 赛道表 / 类型列", "wiki_tier": "wiki 赛道承载表档位",
                 "stock_type": "持仓类型列", "fallback": "判不出（按热门）"}
REGIME_LABELS = {"steady_up": "稳步上涨", "high_vol": "高波动", "range": "盘整", "down": "下行",
                 "unclear": "不明"}
IV_LABELS = {"high": "高", "mid": "中", "low": "低"}
HELD_LABELS = {"held": "已持有", "not_held": "未持有"}
VOL_LABELS = {"high": "高波动", "normal": "正常"}
METHOD_LABELS = {"iv_vs_rv": "平值 IV 对 RV21 分布（代理）", "rv_vs_rv": "当前 RV21 自比（IV 缺失，再退一步的代理）"}
_EV_RESULTS = {"hit": "是", "miss": "否", "unknown": "未知"}
_GATE_RESULTS = {"pass": "过", "fail": "**不过**", "unchecked": "未检查"}
_OPS = {">=": "≥", "<=": "≤", ">": ">", "<": "<"}
_PSEUDO = {"ZERO": "（与 0 比较）", "MA200": "（与当天 MA200 比较）"}
_GATE_ITEMS = {
    "ticker": "杠杆 ETF", "MarketCap": "市值（只对个股）", "Price": "股价",
    "DollarVolume": "日均成交额（中位）", "Expiries90d": "90 天内到期日",
    "Strikes15pct": "±15% 内行权价（全链合并计数，代理）", "PutSpreadPct": "平值 Put 买卖价差",
    "CallSpreadPct": "平值 Call 买卖价差", "AtmOI": "平值 OI（yfinance）"}
_TREND_ITEMS = {"RV1y": "一年实现波动率", "MaxDD": "一年最大回撤", "R1y": "一年回报",
                "Pn": "最新收盘", "MA200_slope": "MA200 斜率", "|R1y|": "一年回报绝对值",
                "RangeWidth": "区间宽度", "TouchesHigh": "上沿触碰", "TouchesLow": "下沿触碰",
                "MA50": "MA50"}
_PCT_METRICS = {"RV1y", "MaxDD", "R1y", "MA200_slope", "|R1y|", "RangeWidth", "PutSpreadPct",
                "CallSpreadPct"}
_TWO_DECIMAL_PCT = {"PutSpreadPct", "CallSpreadPct", "MA200_slope"}
# Which rule of 6.6 an Evidence row belongs to, keyed by (metric, operator).
_RULE_LABELS = {("RV1y", ">="): "高波动条件", ("MaxDD", ">="): "高波动条件",
                ("R1y", ">="): "上行条件", ("Pn", ">"): "上行条件", ("MA200_slope", ">"): "上行条件",
                ("R1y", "<="): "下行条件", ("Pn", "<"): "下行条件", ("MA200_slope", "<"): "下行条件",
                ("|R1y|", "<="): "盘整条件", ("RangeWidth", "<="): "盘整条件",
                ("TouchesHigh", ">="): "盘整条件", ("TouchesLow", ">="): "盘整条件",
                ("MA50", ">"): "均线排列（稳步上涨需要）"}
_INT_METRICS = {"TouchesHigh", "TouchesLow", "Expiries90d", "Strikes15pct", "AtmOI"}


# ---- formatting ----

def pct(x, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def signed_pct(x, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f}%"


def money(x) -> str:
    if x is None:
        return "未取到"
    if abs(x) >= 1e9:
        return f"${x / 1e9:.1f}B"
    return f"${x / 1e6:.1f}M" if abs(x) >= 1e6 else f"${x:,.0f}"


def num(x, digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def cell(text) -> str:
    # An ASCII bar inside a cell would split the markdown table.
    return str(text).replace("|", "｜").replace("\n", " ")


def table(header, rows) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]


def _metric_value(metric: str, value) -> str:
    if value is None:
        return "未取到"
    if metric in _PCT_METRICS:
        return pct(value, 2 if metric in _TWO_DECIMAL_PCT else 1)
    if metric in ("MarketCap", "DollarVolume"):
        return money(value)
    return str(int(value)) if metric in _INT_METRICS else num(value)


# ---- step 1 ----

def _gate_row(item: dict, ticker: str, is_etf: bool) -> tuple:
    metric, op = item["name"].split()[:2]
    result = _GATE_RESULTS[item["result"]]
    if metric == "ticker":
        return ("①", _GATE_ITEMS[metric], ticker, "不在杠杆 ETF 名单", item["constant"], result)
    value = _metric_value(metric, item["value"])
    if metric == "MarketCap" and is_etf:  # the cap rule is for stocks only (6.7)
        value, result = "—", "不适用（ETF）"
    threshold = f"{_OPS[op]} {_metric_value(metric, item['threshold'])}"
    return ("①", _GATE_ITEMS.get(metric, metric), value, threshold, item["constant"], result)


def gate_rows(result: dict) -> list[tuple]:
    is_etf = result["asset_class"] == "etf"
    return [_gate_row(i, result["ticker"], is_etf) for i in result["gate"]["items"]]


# ---- steps 2-3 ----

def class_view_rows(result: dict) -> list[tuple]:
    used = result["thresholds_used"]
    cls = ("②", "标的类别", CLASS_SOURCES.get(result["class_source"], result["class_source"]),
           "--class → ETF → wiki 档位 → 类型列 → 未知按热门", "CLASS_BY_TIER_EMOJI",
           CLASS_LABELS[result["asset_class"]])
    raw = result["thesis_status"]
    status = STATUS_LABELS.get(raw, raw) if raw else "无"
    value = f"wiki 状态 {status}"
    if result["view_source"] == "user":
        value += f"；用户指定 {VIEW_LABELS[result['view']]}"
    source = "用户指定" if result["view_source"] == "user" else "wiki"
    view = ("③", "基本面看法", value, "active / use → 看好；watch → 中性；avoid → 不看好",
            "STATUS_TO_VIEW", f"{VIEW_LABELS[result['view']]}（来源：{source}）")
    age, stale = result["thesis_age_days"], used["THESIS_STALE_DAYS"]
    verdict = "未知" if age is None else ("**过期**" if age > stale else "未过期")
    age_row = ("③", "论点更新距今", "未知" if age is None else f"{age} 天", f"≤ {stale} 天",
               "THESIS_STALE_DAYS", verdict)
    return [cls, view, age_row]


# ---- step 4 ----

def regime_of(result: dict) -> Regime:
    names = {f.name for f in fields(Metrics)} - {"rv21_series"}
    m = Metrics(**{k: result["metrics"][k] for k in names}, rv21_series=())
    return classify(m, result["asset_class"])


def _evidence_row(ev) -> tuple:
    metric, op = ev.name.split()[:2]
    constant = _PSEUDO.get(ev.constant, ev.constant)
    if ev.constant == "ZERO":
        threshold = f"{_OPS[op]} 0"
    else:
        shown_as = "MA" if ev.constant == "MA200" else metric  # a price level, not a ratio
        threshold = f"{_OPS[op]} {_metric_value(shown_as, ev.threshold)}"
    item = f"{_RULE_LABELS.get((metric, op), '条件')}：{_TREND_ITEMS.get(metric, metric)}"
    return ("④", item, _metric_value(metric, ev.value), threshold, constant,
            _EV_RESULTS[ev.result])


def trend_rows(result: dict) -> list[tuple]:
    m, used = result["metrics"], result["thresholds_used"]
    short = m["history_short"]
    rows = [("④", "交易日数", str(m["n_days"]), f"≥ {used['MIN_HISTORY_DAYS']}",
             "MIN_HISTORY_DAYS", "**不足：不分类、不给策略**" if short else "够")]
    if short:
        return rows
    regime = regime_of(result)
    rows += [_evidence_row(ev) for ev in regime.evidence]
    vol, direction = VOL_LABELS[result["vol_tag"]], DIRECTION_LABELS[result["direction_tag"]]
    rows.append(("④", "走势叶子", f"波动 {vol}，方向 {direction}",
                 "高波动优先；正常 + 上行且 MA50 > MA200 → 稳步上涨", "VOL_HIGH / DD_HIGH / UP_RETURN",
                 f"**{regime.label}**"))
    hint = result["recent_change_hint"]
    rows.append(("④", "最近 3 个月（不参与分类）", "；".join(hint["reasons"]) or "无命中",
                 f"R3m 反向且 ≥ {pct(used['RECENT_RETURN_MIN'], 0)}；RV63/RV1y ≥ "
                 f"{used['RECENT_RV_RATIO_HIGH']} 或 ≤ {used['RECENT_RV_RATIO_LOW']}；穿 MA200",
                 "RECENT_RETURN_MIN / RECENT_RV_RATIO_HIGH / RECENT_RV_RATIO_LOW",
                 "最近 3 个月和全年不一样" if hint["changed"] else "与全年一致"))
    return rows


# ---- step 5 ----

def iv_rows(result: dict) -> list[tuple]:
    iv, used = result["iv"], result["thresholds_used"]
    if iv["percentile"] is None:
        return [("⑤", "IV 位置（代理）", "未计算", "—", "IV_HIGH_PCTL / IV_LOW_PCTL", "未计算")]
    p, label = iv["percentile"], iv["label"]
    rows = [("⑤", "IV 位置（代理）百分位", pct(p), f"≥ {pct(used['IV_HIGH_PCTL'], 0)} 记高",
             "IV_HIGH_PCTL", "是" if label == "high" else "否"),
            ("⑤", "IV 位置（代理）百分位", pct(p), f"≤ {pct(used['IV_LOW_PCTL'], 0)} 记低",
             "IV_LOW_PCTL", "是" if label == "low" else "否"),
            ("⑤", "IV 标签", METHOD_LABELS[iv["method"]], "中间记中", "IV_HIGH_PCTL / IV_LOW_PCTL",
             f"**{IV_LABELS[label]}**")]
    thin, atm = used["THIN_IV"], iv["atm_iv"]
    thin_result = "不适用（IV 缺失）" if atm is None else ("权利金薄" if atm < thin else "不薄")
    rows.append(("⑤", "平值 IV", num(atm), f"< {num(thin)} 提示权利金薄", "THIN_IV", thin_result))
    return rows


# ---- steps 6-7 ----

def holding_rows(result: dict) -> list[tuple]:
    h, lot = result["holding"], result["thresholds_used"]["MIN_LOT_SHARES"]
    if h["held_shares_total"] <= 0:
        verdict = "未持有"
    else:
        verdict = "按已持有" if h["treated_as_held"] else "持股不足一张，按未持有"
    value = f"{h['max_lot_shares']:g} 股（合计 {h['held_shares_total']:g} 股，{h['lot_count']} 行）"
    return [("⑥", "最大单账户持股", value, f"≥ {lot} 股", "MIN_LOT_SHARES", verdict)]


def _earnings_row(result: dict) -> tuple:
    e, used = result["earnings"], result["thresholds_used"]
    if result["asset_class"] == "etf":
        found = e["ex_div"] or "窗口内无"
        return ("⑦", "除息日（ETF 不查财报）", found, f"≤ {used['EARNINGS_WINDOW_DAYS']} 天",
                "EARNINGS_WINDOW_DAYS", "备兑 Call 留意提前指派" if e["ex_div"] else "无影响")
    if e["unknown"]:
        value, verdict = "未知", "未知（开仓前自己查）"
    else:
        value = f"{e['days']} 天（{e['date']}）"
        verdict = "**临近**" if e["imminent"] else ("窗口内" if e["in_window"] else "窗口外")
    return ("⑦", "下次财报距快照", value,
            f"≤ {used['EARNINGS_WINDOW_DAYS']} 天在窗口；≤ {used['EARNINGS_IMMINENT_DAYS']} 天临近",
            "EARNINGS_WINDOW_DAYS / EARNINGS_IMMINENT_DAYS", verdict)


def rank_note(result: dict) -> tuple[str | None, float | None]:
    """(pick ranked by the CSP-vs-Bull-Put rule, CSP share) or (None, None) when no ranking ran."""
    for p in result["picks"]:
        for flag in p["flags"]:
            if flag.startswith("portfolio_share="):
                return p["strategy"], float(flag.split("=", 1)[1])
            if flag == "rank_by_class_only":
                return p["strategy"], None
    return None, None


def _rank_row(result: dict) -> tuple | None:
    ranked, share = rank_note(result)
    if ranked is None:
        return None
    used = result["thresholds_used"]
    first = next(p["strategy"] for p in result["picks"]
                 if p["strategy"] in ("cash_secured_put", "bull_put_spread"))
    cls = result["asset_class"]
    member = "在" if cls in used["CSP_FIRST_CLASSES"] else "不在"
    share_text = "组合总值取不到，只按类别" if share is None else f"约 {pct(share, 0)}"
    value = f"{share_text}；类别 {CLASS_LABELS[cls]}（{member} CSP_FIRST_CLASSES）"
    return ("⑦", "现金担保 Put 资金占组合", value,
            f"≤ {pct(used['CSP_MAX_PORTFOLIO_SHARE'])} 且类别 ∈ "
            f"{' / '.join(used['CSP_FIRST_CLASSES'])}",
            "CSP_MAX_PORTFOLIO_SHARE / CSP_FIRST_CLASSES",
            f"{T.STRATEGY_LABELS[first]} 排前")


def output_rows(result: dict) -> list[tuple]:
    rows = [_earnings_row(result)]
    rank = _rank_row(result)
    if rank:
        rows.append(rank)
    labels = " + ".join(T.STRATEGY_LABELS[p["strategy"]] for p in result["picks"])
    rows.append(("⑦", "决策树叶子", result["leaf_id"], "设计 6.8 / 6.9 / 6.11 的叶子表",
                 "LEAF_TABLE", labels))
    return rows


def step_rows(result: dict) -> list[tuple]:
    return (gate_rows(result) + class_view_rows(result) + trend_rows(result) + iv_rows(result)
            + holding_rows(result) + output_rows(result))
