#!/usr/bin/env python3
"""Report for /strategy-pick: the JSON document, the markdown report and the console page.

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.13, 6.14). The JSON uses exactly
the 6.13 field names; the markdown and the console page are rendered from that document alone,
so the file on disk and what the user reads cannot disagree. No account names, costs, P&L or
portfolio value ever enter the document (only the CSP share of the portfolio, rounded to two
decimals, as a flag on the CSP pick). The seven-step table is computed when rendering (see strategy_pick_steps), not stored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path

import option_labels
import strategy_pick_rules as R
import strategy_pick_steps as S
import strategy_pick_text as T
from strategy_pick_text import (CLASS_ONLY_FLAG, FLAG_LABELS, HIDDEN_FLAGS,  # noqa: F401
                                LEG_LABELS)  # re-exported
from spread_core import expected_move
from strategy_pick_diagram_frame import diagram_alt
from strategy_pick_inputs import Earnings, Gate, Holding, IvPosition, ViewDecision
from strategy_pick_metrics import Metrics
from strategy_pick_regime import Regime
from strategy_pick_tree import Decision

DISCLAIMER = "不构成投资建议。本报告只供研究；最终用哪个策略、开不开仓由你决定。"
LIMITS = (
    "一年只是一个行情阶段：去年稳涨不代表明年稳涨。",
    "“高 / 低 IV”是和这只票自己过去一年比；过去一年整体偏高或偏低时，百分位会误导。",
    "IV 位置是代理：用当前平值 IV 对比过去一年滚动 21 日实现波动率（不是真 IV 历史）；"
    "隐含波动率平时就比实现波动率高一截，所以“高”会比真 IV 百分位来得多。",
    "±15% 内行权价个数按全链（所有到期日合并）的挂牌行权价计数，是代理，可能多算。",
    "价格用复权收盘价（拆股、分红都调整）；/put-screen 用不复权价，MA200 可能差一点。",
    "工具不预测方向，只把你的看法和走势、价格组合起来；参考价位是线性近似，标“粗略”。",
    "最终用哪个策略、开不开仓由你决定。",
)
PAGE_PREFIX = "wiki/options/strategies/"
LINK_UP = "../../"  # report lives in data/strategy-picks/
PSEUDO_CONSTANTS = frozenset({"ZERO", "MA200"})
SHARE_FLAG = "portfolio_share="
# Only a rounded share is written (whole percent), so the file cannot be used to back out the
# portfolio value from the reference strike.
SHARE_DECIMALS = 2
_PAIR = frozenset({R.CSP_KEY, R.BULL_PUT_KEY})
# Constants every run relies on even when no Evidence row names them.
BASE_CONSTANTS = ("LOOKBACK_DAYS", "MIN_HISTORY_DAYS", "MIN_SLOPE_DAYS", "MA_SHORT", "MA_LONG",
                  "MA_SLOPE_LAG", "RV_WINDOW", "RECENT_DAYS", "ANNUALIZE", "MISSING_SESSIONS_WARN",
                  "TOUCH_BAND_PCT", "TOUCH_GAP_DAYS", "GATE_EXPIRY_HORIZON_DAYS",
                  "GATE_STRIKE_RANGE_PCT", "IV_EXPIRY_MIN_DAYS", "IV_EXPIRY_MAX_DAYS",
                  "IV_EXPIRY_TARGET_DAYS", "IV_HIGH_PCTL", "IV_LOW_PCTL", "STATUS_TO_VIEW",
                  "THESIS_STALE_DAYS", "MIN_LOT_SHARES", "UNKNOWN_CLASS_FALLBACK", "CLASS_BY_TIER_EMOJI",
                  "EARNINGS_WINDOW_DAYS", "EARNINGS_IMMINENT_DAYS", "RECENT_RETURN_MIN",
                  "RECENT_RV_RATIO_HIGH", "RECENT_RV_RATIO_LOW")
TARGET_CONSTANTS = ("NORMAL_DELTA", "FAR_OTM_DELTA", "BULLCALL_LONG_DELTA", "CONDOR_SHORT_DELTA",
                    "COLLAR_PUT_DELTA", "COLLAR_CALL_DELTA", "Z_BY_DELTA", "REF_DECIMALS")
RANK_CONSTANTS = ("CSP_MAX_PORTFOLIO_SHARE", "CSP_FIRST_CLASSES", "CONTRACT_MULTIPLIER")


@dataclass(frozen=True)
class RunFacts:
    """Everything one run decided, before it becomes the JSON document."""
    ticker: str
    run_date: date
    snapshot_date: date
    asset_class: str
    class_source: str
    gate: Gate
    view: ViewDecision
    thesis_status: str | None
    thesis_verdict_text: str | None
    thesis_age_days: int | None
    metrics: Metrics
    regime: Regime | None
    iv: IvPosition | None
    expiry_used: date | None
    days: int | None
    holding: Holding
    earnings: Earnings
    decision: Decision
    warnings: tuple[str, ...]


# ---- JSON document ----

def _jsonable(value):
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value.isoformat() if isinstance(value, date) else value


def _evidence_constants(f: RunFacts) -> list[str]:
    evidence = list(f.gate.items)
    if f.regime is not None:
        evidence += list(f.regime.evidence) + list(f.regime.hint.evidence)
    if f.iv is not None:
        evidence += list(f.iv.evidence)
    return [e.constant for e in evidence if e.constant not in PSEUDO_CONSTANTS]


def thresholds_used(f: RunFacts) -> dict:
    """Name -> value of every rules constant this run used, read from the rules module now."""
    tier = R.CLASS_THRESHOLDS[f.asset_class]
    names = list(tier) + _evidence_constants(f) + list(BASE_CONSTANTS)
    if any(p.delta_target for p in f.decision.picks):
        names += TARGET_CONSTANTS
    if f.decision.rank_basis is not None:
        names += RANK_CONSTANTS
    used = {n: _jsonable(tier[n] if n in tier else getattr(R, n)) for n in dict.fromkeys(names)}
    used["LEAF_TABLE"] = _leaf_entry(f.decision.leaf_id)
    return used


def _leaf_entry(lid: str) -> dict:
    """The one LEAF_TABLE row this run used (the full table is ~100 leaves)."""
    picks, warns = R.LEAF_TABLE[lid]
    return {lid: {"picks": [{"strategy": p.strategy, "plan": p.plan, "flags": list(p.flags)}
                            for p in picks],
                  "warnings": list(warns)}}


def _targets(targets) -> list[dict] | None:
    if targets is None:
        return None
    return [{"leg": t.leg, "low": t.low, "high": t.high} for t in targets]


def _pick_flags(pick, d: Decision, carrier: str | None) -> list[str]:
    """The ranking note goes on the CSP pick only."""
    flags = list(pick.flags)
    if pick.strategy != carrier:
        return flags
    if d.csp_share is not None:
        flags.append(f"{SHARE_FLAG}{d.csp_share:.{SHARE_DECIMALS}f}")
    elif d.rank_basis == "class_only":
        flags.append(CLASS_ONLY_FLAG)
    return flags


def _rank_carrier(d: Decision) -> str | None:
    """The CSP pick; when it was dropped (earnings imminent) the share note has no owner."""
    listed = {p.strategy for p in d.picks}
    return R.CSP_KEY if d.rank_basis is not None and R.CSP_KEY in listed else None


def _picks(d: Decision) -> list[dict]:
    carrier = _rank_carrier(d)
    return [{"strategy": p.strategy, "rank": p.rank, "delta_target": _targets(p.delta_target),
             "reference_level": _targets(p.reference_level), "next_command": p.next_command,
             "flags": _pick_flags(p, d, carrier)} for p in d.picks]


def _not_chosen(d: Decision) -> list[dict]:
    return [{"strategy": n.strategy, "reason_code": n.reason_code, "reason_text": n.reason_text,
             "page": PAGE_PREFIX + n.page} for n in d.not_chosen]


def _gate(g: Gate) -> dict:
    return {"passed": g.passed,
            "items": [{"name": i.name, "constant": i.constant, "value": i.value,
                       "threshold": _jsonable(i.threshold), "result": i.result} for i in g.items]}


def _iv(f: RunFacts) -> dict:
    iv = f.iv
    return {"atm_iv": iv.atm_iv if iv else None, "expiry_used": _jsonable(f.expiry_used),
            "days": f.days, "percentile": iv.percentile if iv else None,
            "label": iv.label if iv else None, "method": iv.method if iv else None,
            "iv_rv_ratio": iv.iv_rv_ratio if iv else None}


def _metrics(m: Metrics) -> dict:
    out = {fl.name: getattr(m, fl.name) for fl in fields(Metrics) if fl.name != "rv21_series"}
    out["rv21_window_count"] = len(m.rv21_series)
    return out


def _holding(h: Holding) -> dict:
    return {"held_shares_total": h.held_shares_total, "max_lot_shares": h.max_lot_shares,
            "lot_count": h.lot_count, "ltcg_reached": h.ltcg_reached,
            "treated_as_held": h.treated_as_held}


def _earnings(e: Earnings) -> dict:
    return {"date": _jsonable(e.date), "days": e.days, "in_window": e.in_window,
            "imminent": e.imminent, "unknown": e.unknown, "ex_div": _jsonable(e.ex_div)}


def build_result(f: RunFacts) -> dict:
    """The JSON document, field for field the design 6.13 list."""
    r = f.regime
    hint = {"changed": r.hint.changed, "reasons": list(r.hint.reasons)} if r else None
    return {
        "ticker": f.ticker, **option_labels.labelled_fields(), "run_date": f.run_date.isoformat(),
        "snapshot_date": f.snapshot_date.isoformat(), "asset_class": f.asset_class,
        "class_source": f.class_source, "gate": _gate(f.gate), "view": f.view.view,
        "view_source": f.view.source, "thesis_status": f.thesis_status,
        "thesis_verdict_text": f.thesis_verdict_text, "thesis_age_days": f.thesis_age_days,
        "metrics": _metrics(f.metrics), "vol_tag": r.vol_tag if r else None,
        "direction_tag": r.direction_tag if r else None, "regime_leaf": r.leaf if r else None,
        "recent_change_hint": hint, "iv": _iv(f), "holding": _holding(f.holding),
        "earnings": _earnings(f.earnings), "leaf_id": f.decision.leaf_id,
        "picks": _picks(f.decision), "not_chosen": _not_chosen(f.decision),
        "warnings": list(f.warnings), "thresholds_used": thresholds_used(f),
    }


def render_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


# ---- markdown ----

def leaf_text(leaf: str) -> str:
    parts = leaf.split(".")
    if leaf == R.LEAF_GATE_FAIL:
        return "门槛出局"
    if leaf == R.LEAF_HISTORY_SHORT:
        return "历史不足"
    labels = [S.VIEW_LABELS.get(parts[0], parts[0]), S.HELD_LABELS.get(parts[1], parts[1])]
    if len(parts) == 4:
        labels += [S.REGIME_LABELS[parts[2]], f"IV {S.IV_LABELS[parts[3]]}"]
    return " × ".join(labels)


def _range_text(targets, digits: int) -> str:
    out = []
    for t in targets:
        lo, hi = f"{t['low']:.{digits}f}", f"{t['high']:.{digits}f}"
        out.append(f"{LEG_LABELS.get(t['leg'], t['leg'])} {lo if lo == hi else lo + '–' + hi}")
    return "；".join(out)


def _flag_text(flag: str) -> str:
    if flag.startswith(SHARE_FLAG):
        return f"现金担保 Put 资金占组合约 {S.pct(float(flag[len(SHARE_FLAG):]), 0)}"
    return FLAG_LABELS.get(flag, flag)


def _pick_row(p: dict) -> tuple:
    delta = _range_text(p["delta_target"], 2) if p["delta_target"] else "—"
    if p["reference_level"]:
        ref = "≈ " + _range_text(p["reference_level"], 2)
    else:
        ref = "IV 缺失，参考价位不给" if p["delta_target"] else "—"
    notes = "；".join(_flag_text(f) for f in p["flags"] if f not in HIDDEN_FLAGS) or "—"
    return (str(p["rank"]), T.STRATEGY_LABELS[p["strategy"]], delta, ref,
            f"`{p['next_command']}`", notes)


def _conclusion(result: dict) -> list[str]:
    lines = ["## 结论与下一步", "",
             f"决策树叶子：`{result['leaf_id']}`（{leaf_text(result['leaf_id'])}）", ""]
    if result["leaf_id"] == R.LEAF_GATE_FAIL:
        lines += ["**不做期权建议**：第 ① 步流动性门槛没过（见下表标“不过”的一行）。", ""]
    elif result["leaf_id"] == R.LEAF_HISTORY_SHORT:
        lines += ["**不做期权建议**：历史不足 200 个交易日，算不出 MA200，不分类、不给策略。", ""]
    lines += S.table(("排名", "策略", "Delta 目标", "参考价位（粗略）", "下一步", "说明"),
                     [_pick_row(p) for p in result["picks"]])
    caveats = [f"- {T.STRATEGY_LABELS[p['strategy']]}：`{p['next_command']}` 的限制："
               f"{T.NEXT_COMMAND_CAVEATS[p['strategy']]}"
               for p in result["picks"] if p["strategy"] in T.NEXT_COMMAND_CAVEATS]
    if caveats:
        lines += ["", "下一步命令有自己的规则，可能把这只票剔掉：", *caveats]
    return lines


def _metric_rows(m: dict) -> list[tuple]:
    return [("交易日数", m["n_days"]), ("最新收盘 Pn", S.num(m["last_close"])),
            ("一年回报 R1y", S.signed_pct(m["r1y"])), ("MA50", S.num(m["ma50"])),
            ("MA200", S.num(m["ma200"])), ("MA200 斜率（20 日）", S.signed_pct(m["ma200_slope"], 2)),
            ("一年最大回撤", S.pct(m["max_dd"])), ("一年实现波动率 RV1y", S.pct(m["rv1y"])),
            ("当前 RV21", S.pct(m["rv21_current"])),
            ("RV21 窗口数（收盘数 − 21）", m["rv21_window_count"]),
            ("区间宽度", S.pct(m["range_width"])),
            ("上沿 / 下沿触碰", f"{m['touches_high']} / {m['touches_low']}"),
            ("3 个月回报 R3m", S.signed_pct(m["r3m"])), ("RV63", S.pct(m["rv63"])),
            ("63 日内穿过 MA200", "是" if m["crossed_ma200_63d"] else "否"),
            ("日均成交额（中位）", S.money(m["dollar_volume_median"])),
            ("缺失交易日", m["missing_sessions"])]


def _trend(result: dict) -> list[str]:
    lines = ["## 走势指标", "", "口径：yfinance 复权收盘价（拆股、分红都调整）。", ""]
    lines += S.table(("指标", "值"), _metric_rows(result["metrics"]))
    hint = result["recent_change_hint"]
    if hint is not None:
        text = "；".join(hint["reasons"]) if hint["changed"] else "最近 3 个月与全年一致"
        lines += ["", f"最近 3 个月变了吗：{text}（只是提示，不参与分类）"]
    return lines


def _spot(result: dict) -> float:
    price = next((i["value"] for i in result["gate"]["items"] if i["constant"] == "MIN_PRICE"), None)
    return price if price is not None else result["metrics"]["last_close"]


def _iv_section(result: dict) -> list[str]:
    iv = result["iv"]
    lines = ["## IV 位置（代理）", ""]
    if result["metrics"]["history_short"]:
        return lines + [f"- 历史不足，不计算 IV 位置（{result['metrics']['n_days']} 个交易日）"]
    if iv["method"] == R.IV_METHOD_FALLBACK:
        lines.append("- 方法：IV 缺失，改用实现波动率自比（当前 RV21 在同一分布里的百分位，"
                     "再退一步的代理）")
    else:
        lines.append("- 方法：当前平值 IV 在过去一年滚动 21 日实现波动率里的百分位（代理，"
                     "不是真 IV 百分位）")
    expiry = (f"{iv['expiry_used']}（距快照 {iv['days']} 天）" if iv["expiry_used"]
              else "20–60 天内没有到期日，依赖到期日的检查跳过")
    lines += [f"- 所选到期日：{expiry}", f"- 平值 IV：{S.num(iv['atm_iv'])}"]
    if iv["percentile"] is not None:
        lines.append(f"- 百分位：{S.pct(iv['percentile'])}（共 "
                     f"{result['metrics']['rv21_window_count']} 个 RV21 窗口）→ "
                     f"**{S.IV_LABELS[iv['label']]}**")
    if iv["iv_rv_ratio"] is not None:
        lines.append(f"- IV ÷ 当前 RV21 = {iv['iv_rv_ratio']:.2f}（只作参考，不参与分类）")
    if iv["atm_iv"] is not None and iv["days"]:
        spot = _spot(result)
        sigma = expected_move(spot, iv["atm_iv"], iv["days"])
        lines.append(f"- 1σ 预期波动 = {spot:.2f} × {iv['atm_iv']:.2f} × √({iv['days']} ÷ 365) "
                     f"= {sigma:.2f}（参考价位用不四舍五入的值代入）")
    else:
        lines.append("- IV 缺失，参考价位不给")
    return lines


def _why_not(result: dict) -> list[str]:
    lines = ["## 为什么不选", ""]
    for n in result["not_chosen"]:
        page = n["page"].rsplit("/", 1)[-1]
        lines.append(f"- **{T.STRATEGY_LABELS[n['strategy']]}**：{n['reason_text']}"
                     f"（[{page}]({LINK_UP}{n['page']})）")
    return lines


def render_markdown(result: dict, diagram: str | None = None) -> str:
    """`diagram` is the svg file name; its image paragraph sits under the title (after the
    Yahoo / demo banner, which with_banner puts right below the title). None: no image line."""
    head = [f"# {result['ticker']} 策略推荐（运行日 {result['run_date']}，"
            f"行情快照 {result['snapshot_date']}）", ""]
    if diagram is not None:
        head += [f"![{diagram_alt(result)}]({diagram})", ""]
    steps = ["## 七步逐步表", "", *S.table(S.STEP_HEADER, S.step_rows(result))]
    warnings = ["## 警告", "", *([f"- {w}" for w in result["warnings"]] or ["无"])]
    limits = ["## 局限", "", *[f"- {line}" for line in LIMITS]]
    blocks = [head + _conclusion(result), steps, _trend(result), _iv_section(result),
              _why_not(result), warnings, limits, [DISCLAIMER]]
    return option_labels.with_banner(
        "\n\n".join("\n".join(b).strip("\n") for b in blocks) + "\n")


# ---- console ----

def render_console(result: dict, md_path: Path, json_path: Path,
                   svg_path: Path | None = None) -> str:
    m, iv = result["metrics"], result["iv"]
    lines = [f"{result['ticker']} 策略推荐 {result['run_date']}（行情快照 {result['snapshot_date']}）",
             f"叶子：{result['leaf_id']}（{leaf_text(result['leaf_id'])}）",
             f"门槛：{'通过' if result['gate']['passed'] else '没过，不做期权建议'}"]
    for p in result["picks"]:
        row = _pick_row(p)
        lines.append(f"  {row[0]}. {row[1]}｜Delta {row[2]}｜参考 {row[3]}｜下一步 {row[4]}")
    iv_text = (f"{S.pct(iv['percentile'])}（{S.IV_LABELS[iv['label']]}，代理）"
               if iv["percentile"] is not None else "未计算")
    lines += [f"关键数字：R1y {S.signed_pct(m['r1y'])}｜RV1y {S.pct(m['rv1y'])}｜"
              f"MaxDD {S.pct(m['max_dd'])}｜IV 位置 {iv_text}",
              f"警告 {len(result['warnings'])} 条（报告里逐条列出）"]
    lines += [f"路径图：{svg_path}"] if svg_path is not None else []
    lines += [f"报告：{md_path}", f"数据：{json_path}", DISCLAIMER]
    return "\n".join(lines)
