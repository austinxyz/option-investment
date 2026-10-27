#!/usr/bin/env python3
"""Stats and markdown rendering for the paper-trading simulator (pure, no I/O).

Win = realized pnl > 0. See docs/superpowers/specs/2026-10-14-spread-sim-design.md.
"""
from __future__ import annotations

import option_settings
import spread_core as core
from spread_ledger import (BREACHED_SIDE_LABELS, BULLCALL_LABEL, BULLCALL_STRIKE_KEYS,
                           CONDOR_STRIKE_KEYS, STRATEGY_BULL_CALL, STRATEGY_BULL_PUT,
                           STRATEGY_COLLAR, STRATEGY_COVERED_CALL, STRATEGY_IRON_CONDOR,
                           STRATEGY_PROTECTIVE_PUT, strategy_of)

STRATEGY_CSP = "cash_secured_put"
# Put-protected share positions: their own block, never in any win-rate denominator.
LOT_PUT_STRATEGIES = (STRATEGY_COLLAR, STRATEGY_PROTECTIVE_PUT)
# Only these have a win rate; a strategy not listed here is excluded from every statistic.
WIN_RATE_STRATEGIES = (STRATEGY_BULL_PUT, STRATEGY_CSP, STRATEGY_COVERED_CALL, STRATEGY_IRON_CONDOR,
                       STRATEGY_BULL_CALL)
# compute_stats never mixes a condor or a bull call into these: each has its own block and row.
SINGLE_SPREAD_STATS = (STRATEGY_BULL_PUT, STRATEGY_CSP, STRATEGY_COVERED_CALL)
# Strategies rendered by _other_strategy_sections; an unknown strategy is rendered nowhere.
OTHER_SECTION_STRATEGIES = (STRATEGY_CSP, STRATEGY_COVERED_CALL, STRATEGY_IRON_CONDOR,
                            STRATEGY_BULL_CALL)
# Strategies the capital and summary maths understand; anything else counts as zero.
KNOWN_STRATEGIES = WIN_RATE_STRATEGIES + LOT_PUT_STRATEGIES
# Public page allowlist: only strategies that are paper-only. Anything else, including a
# strategy added later, stays private until it is deliberately added here.
PUBLIC_STRATEGIES = (STRATEGY_BULL_PUT, STRATEGY_CSP)

MIN_SAMPLE = 30
SETTLED = ("closed_early", "expired")


def _money(x: float) -> str:
    return f"{'+' if x > 0 else '-' if x < 0 else ''}${abs(x):,.0f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _spct(x: float | None) -> str:
    """Signed one-decimal percent of an already-computed ratio; '—' when undefined."""
    return "—" if x is None else f"{x * 100:+.1f}%"


def capital_at_risk(s: dict) -> float:
    """Capital tied up: spread max loss, or cash set aside for a cash-secured put."""
    mult = 100 * s["contracts"]
    if strategy_of(s) not in KNOWN_STRATEGIES:
        return 0.0  # never guess at fields of a strategy this code does not know
    if strategy_of(s) == STRATEGY_BULL_CALL:
        return core.bullcall_max_loss(s["debit"], s["contracts"])  # the debit paid is all it can lose
    if strategy_of(s) == STRATEGY_IRON_CONDOR:
        return (s["width"] - s["net_credit"]) * mult  # only one side can lose at expiry
    if strategy_of(s) in LOT_PUT_STRATEGIES:
        return max(0.0, -s["net_credit"]) * mult  # only a net debit is new cash out
    if strategy_of(s) == STRATEGY_COVERED_CALL:
        return 0.0  # the shares are already owned; no new capital is tied up
    if strategy_of(s) == STRATEGY_CSP:
        return s["short_strike"] * mult
    width = s.get("width") or (s["short_strike"] - s["long_strike"])
    return (width - s.get("credit", 0.0)) * mult


def pnl_base(s: dict) -> float:
    """Denominator for P&L %: share cost for a covered call, else capital at risk."""
    if strategy_of(s) == STRATEGY_COVERED_CALL:
        return s.get("cost_basis", 0.0) * 100 * s["contracts"]
    if strategy_of(s) in LOT_PUT_STRATEGIES:
        return s.get("cost_basis", 0.0) * s.get("covered_shares", 0)
    if strategy_of(s) in (STRATEGY_BULL_PUT, STRATEGY_CSP, STRATEGY_IRON_CONDOR, STRATEGY_BULL_CALL):
        return capital_at_risk(s)
    return 0.0  # an unknown strategy has no base


def _pnl_ratio(pnl: float | None, s: dict) -> float | None:
    cap = pnl_base(s)
    return None if pnl is None or cap <= 0 else pnl / cap


def _group(settled: list[dict], key: str) -> dict:
    groups: dict = {}
    for s in settled:
        if key in s:
            groups.setdefault(s[key], []).append(s)
    out = {}
    for k in sorted(groups, key=lambda v: (v is None, v)):
        rows = groups[k]
        wins = sum(1 for r in rows if r["pnl"] > 0)
        out[k] = {"n": len(rows), "wins": wins, "win_rate": wins / len(rows),
                  "total_pnl": round(sum(r["pnl"] for r in rows), 2)}
    return out


def compute_stats(spreads: list[dict]) -> dict:
    return _win_stats([s for s in spreads if strategy_of(s) in SINGLE_SPREAD_STATS])


def _win_stats(spreads: list[dict]) -> dict:
    """Win-rate statistics of records already filtered to one comparable group."""
    settled = [s for s in spreads if s["status"] in SETTLED]
    open_ = [s for s in spreads if s["status"] == "open"]
    wins = [s["pnl"] for s in settled if s["pnl"] > 0]
    losses = [-s["pnl"] for s in settled if s["pnl"] < 0]
    n = len(settled)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    win_rate = len(wins) / n if n else None
    breakeven = None
    if avg_win is not None and avg_loss is not None:
        breakeven = avg_loss / (avg_win + avg_loss)
    passes = None if (win_rate is None or breakeven is None) else win_rate > breakeven
    gaps = [abs(s["monday_gap_pct"]) for s in spreads if s.get("monday_gap_pct") is not None]
    return {
        "n_settled": n,
        "n_open": len(open_),
        "wins": len(wins),
        "win_rate": win_rate,
        "total_pnl": round(sum(s["pnl"] for s in settled), 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "breakeven_win_rate": breakeven,
        "passes": passes,
        "by_bucket": _group(settled, "delta_bucket"),
        "by_expiry": _group(settled, "expiry"),
        "avg_abs_gap": sum(gaps) / len(gaps) if gaps else None,
        "enough_sample": n >= MIN_SAMPLE,
    }


REAL_UNCHANGED_NOTE = "（真实持仓未变）"


def result_text(s: dict) -> str:
    """The settled result, annotated when a real holding was 'called away' on paper only."""
    result = str(s["result"])
    ref = s.get("lot_ref") or {}
    if (strategy_of(s) in (STRATEGY_COVERED_CALL, STRATEGY_COLLAR) and result == "called_away"
            and ref.get("source") == "real"):
        return result + REAL_UNCHANGED_NOTE
    return result


def legs_label(s: dict) -> str:
    """'150/145' for a spread, '150 Put' for a single-leg record, both legs for a collar,
    '85/90/110/115' (long put / short put / short call / long call) for an iron condor,
    '100/105 Call' (long / short) for a bull call debit spread."""
    if strategy_of(s) in LOT_PUT_STRATEGIES:
        return lot_put_legs(s)
    if strategy_of(s) == STRATEGY_BULL_CALL:
        return "/".join(f"{s[k]:g}" for k in BULLCALL_STRIKE_KEYS) + " Call"
    if strategy_of(s) == STRATEGY_IRON_CONDOR:
        return "/".join(f"{s[k]:g}" for k in CONDOR_STRIKE_KEYS)
    if strategy_of(s) == STRATEGY_COVERED_CALL:
        return f"{s['short_strike']:g} Call"
    if s.get("long_strike") is None:
        return f"{s['short_strike']:g} Put"
    return f"{s['short_strike']:g}/{s['long_strike']:g}"


def threshold_labels(csp: bool) -> tuple[str, str]:
    """(stop, take-profit) tip text; the basis differs between spreads and single-leg puts."""
    if csp:
        return (f"🔴 已到止损线（浮亏≥权利金的{core.CSP_STOP_PREMIUM_MULTIPLE:g}倍）",
                f"🟢 已到止盈线（权利金的{core.TAKE_PROFIT_FRACTION * 100:.0f}%）")
    return "🔴 已到止损线（最大亏损的50%）", "🟢 已到止盈线（权利金的50%）"


# (stop, take-profit) tip text for a bull call debit spread; one definition shared by
# status lines, md and html (the buyer's basis: the debit paid and the max profit).
BULLCALL_THRESHOLD_LABELS = (
    f"🔴 已到止损线（浮亏≥净支出的{core.BULLCALL_STOP_FRACTION * 100:.0f}%）",
    f"🟢 已到止盈线（最大收益的{core.BULLCALL_TAKE_PROFIT_FRACTION * 100:.0f}%）",
)


def _mark_cells(m: dict | None, csp: bool = False,
                labels: tuple[str, str] | None = None) -> tuple[str, str, str]:
    """(pnl, distance, note) table cells for one open record's mark; `labels` overrides the
    (stop, take-profit) tip text for a strategy with its own basis."""
    if m is None or "error" in m:
        return "—", "—", m["error"] if m else "运行 status 查看"
    stop_text, tp_text = labels or threshold_labels(csp)
    tips = []
    if m["flags"]["stop"]:
        tips.append(stop_text)
    if m["flags"]["take_profit"]:
        tips.append(tp_text)
    if m.get("stale"):
        tips.append("报价陈旧")
    return _money(m["pnl"]), f"{m['dist_pct']:.1f}%", "；".join(tips) or "—"


def _usable_mark(s: dict, marks: dict) -> dict | None:
    m = marks.get(s["id"])
    return None if m is None or "error" in m else m


def _open_pct(s: dict, marks: dict) -> str:
    m = _usable_mark(s, marks)
    return "—" if m is None else _spct(_pnl_ratio(m["pnl"], s))


def _open_summary(records: list[dict], marks: dict) -> list[str]:
    """One line: total capital tied up (with strategy split) and total floating P&L."""
    open_ = [s for s in records if s["status"] == "open" and strategy_of(s) in KNOWN_STRATEGIES]
    if not open_:
        return []
    total = sum(capital_at_risk(s) for s in open_)
    csp_cap = sum(capital_at_risk(s) for s in open_ if strategy_of(s) == STRATEGY_CSP)
    lot_cap = sum(capital_at_risk(s) for s in open_ if strategy_of(s) in LOT_PUT_STRATEGIES)
    condor_cap = sum(capital_at_risk(s) for s in open_ if strategy_of(s) == STRATEGY_IRON_CONDOR)
    spread_cap = sum(capital_at_risk(s) for s in open_ if strategy_of(s) == STRATEGY_BULL_PUT)
    bullcall_cap = sum(capital_at_risk(s) for s in open_ if strategy_of(s) == STRATEGY_BULL_CALL)
    labels = ("Put 贷方价差", "现金担保 Put", "保护性 Put / Collar 净支出", "铁鹰", BULLCALL_LABEL)
    amounts = (spread_cap, csp_cap, lot_cap, condor_cap, bullcall_cap)
    parts = [f"{label} {_usd(amount)}" for label, amount in zip(labels, amounts) if amount]
    split = f"（{' / '.join(parts)}）" if len(parts) > 1 else ""
    # Collar / protective-put pnl has a different base (cost x shares): shown in its own section.
    flow = [s for s in open_ if strategy_of(s) not in LOT_PUT_STRATEGIES]
    marked = [(s, m) for s in flow if (m := _usable_mark(s, marks))]
    line = f"未平仓占用资金合计 {_usd(total)}{split}"
    if marked:
        pnl = sum(m["pnl"] for _, m in marked)
        cap = sum(pnl_base(s) for s, _ in marked)
        line += f"；浮盈亏合计 {_money(pnl)}（{_spct(pnl / cap if cap > 0 else None)}）"
    missing = len(flow) - len(marked)
    if missing:
        line += f"（另有 {missing} 笔无报价，未计入浮盈亏）"
    return [line, ""]


def peak_capital(records: list[dict]) -> tuple[float, str] | None:
    """Largest capital tied up at once: (amount, first date it was reached).

    A trade holds its capital from `snapshot_date` (when it was opened) up to, but not
    including, `exit_date`; an open trade holds it to the end. Covered calls tie up none.
    """
    held = [s for s in records if capital_at_risk(s) > 0 and s.get("snapshot_date")]
    best: tuple[float, str] | None = None
    for day in sorted({s["snapshot_date"] for s in held}):
        total = sum(capital_at_risk(s) for s in held
                    if s["snapshot_date"] <= day and (s["status"] == "open"
                                                      or (s.get("exit_date") or "9999") > day))
        if best is None or total > best[0]:
            best = (total, day)
    return best


def _peak_line(records: list[dict]) -> list[str]:
    peak = peak_capital(records)
    if peak is None:
        return []
    return [f"历史最大同时占用资金 {_usd(peak[0])}（{peak[1]} 起）", ""]


def _open_section(open_: list[dict], marks: dict) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = ["| # | 标的 | 短/长腿 | 到期 | 张数 | 浮盈亏 | 占用资金 | 盈亏% | 距短腿 | 提示 |",
             "|---|------|---------|------|------|--------|----------|-------|--------|------|"]
    for s in open_:
        pnl, dist, note = _mark_cells(marks.get(s["id"]))
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} "
                     f"| {s['expiry']} | {s['contracts']} | {pnl} | {_usd(capital_at_risk(s))} "
                     f"| {_open_pct(s, marks)} | {dist} | {note} |")
    return lines + [""]


def _settled_section(spreads: list[dict]) -> list[str]:
    settled = [s for s in spreads if s["status"] in SETTLED]
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = []
    for expiry in sorted({s["expiry"] for s in settled}, reverse=True):
        lines += [f"**到期 {expiry}**", "",
                  "| # | 标的 | 短/长腿 | 档位 | 结果 | 方式 | 盈亏 | 占用资金 | 盈亏% | 次日开盘跳空 |",
                  "|---|------|---------|------|------|------|------|----------|-------|----------|"]
        for s in (r for r in settled if r["expiry"] == expiry):
            gap = "—" if s.get("monday_gap_pct") is None else f"{s['monday_gap_pct']:+.2f}%"
            how = "提前平仓" if s["status"] == "closed_early" else "到期结算"
            lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} "
                         f"| {s['delta_bucket']:.2f} | {s['result']} | {how} | {_money(s['pnl'])} "
                         f"| {_usd(capital_at_risk(s))} | {_spct(_pnl_ratio(s['pnl'], s))} | {gap} |")
        lines.append("")
    return lines


def _stats_section(st: dict) -> list[str]:
    if st["n_settled"] == 0:
        return ["（还没有已结算的，暂无统计）", ""]
    lines = [
        "| 项目 | 数值 |", "|------|------|",
        f"| 已结算笔数 | {st['n_settled']}（未平仓 {st['n_open']}）|",
        f"| 胜率（盈亏>0 算赢）| {_pct(st['win_rate'])} |",
        f"| 总盈亏 | {_money(st['total_pnl'])} |",
        f"| 平均盈利 | {'—' if st['avg_win'] is None else _money(st['avg_win'])} |",
        f"| 平均亏损 | {'—' if st['avg_loss'] is None else _money(-st['avg_loss'])} |",
        f"| 按实际盈亏比算的盈亏平衡胜率 | {_pct(st['breakeven_win_rate'])} |",
    ]
    if st["passes"] is None:
        verdict = "暂无亏损（或盈利）样本，无法比较"
    else:
        verdict = "✅ 实际胜率高于盈亏平衡胜率" if st["passes"] else "❌ 实际胜率低于盈亏平衡胜率"
    lines += [f"| 是否过线 | {verdict} |", ""]
    if not st["enough_sample"]:
        lines += [f"> ⚠️ 样本不足（{st['n_settled']} < {MIN_SAMPLE}），别下结论。", ""]
    return lines


def _bucket_section(st: dict) -> list[str]:
    if not st["by_bucket"]:
        return ["（暂无）", ""]
    lines = ["| 档位（delta）| 笔数 | 胜率 | 总盈亏 |", "|--------------|------|------|--------|"]
    for b, g in st["by_bucket"].items():
        lines.append(f"| {b:.2f} | {g['n']} | {_pct(g['win_rate'])} | {_money(g['total_pnl'])} |")
    return lines + [""]


def compute_strategy_stats(records: list[dict]) -> dict[str, dict]:
    """compute_stats per strategy, for the strategies present in `records`."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        if strategy_of(r) not in WIN_RATE_STRATEGIES:
            continue  # collars and protective puts have no win rate
        groups.setdefault(strategy_of(r), []).append(r)
    return {k: _win_stats(v) for k, v in groups.items()}


def _strategy_section(records: list[dict]) -> list[str]:
    stats = compute_strategy_stats(records)
    lines = ["| 策略 | 已结算 | 胜率 | 总盈亏 | 平均盈利 | 平均亏损 | 盈亏平衡胜率 | 是否过线 |",
             "|------|--------|------|--------|----------|----------|--------------|----------|"]
    for name, st in stats.items():
        verdict = "—" if st["passes"] is None else ("✅" if st["passes"] else "❌")
        lines.append(
            f"| {name} | {st['n_settled']} | {_pct(st['win_rate'])} | {_money(st['total_pnl'])} "
            f"| {'—' if st['avg_win'] is None else _money(st['avg_win'])} "
            f"| {'—' if st['avg_loss'] is None else _money(-st['avg_loss'])} "
            f"| {_pct(st['breakeven_win_rate'])} | {verdict} |")
    lines.append("")
    lines += condor_theory_note(records)
    lines += _bullcall_module().bullcall_required_note(records)
    for name, st in stats.items():
        if not st["enough_sample"]:
            lines += [f"> ⚠️ {name} 样本不足（{st['n_settled']} < {MIN_SAMPLE}），别下结论。", ""]
    return lines


BROKER_FILLED = ("filled", "partial")
BROKER_HEADERS = ["券商成交", "滑点"]


def _broker_fill(s: dict) -> float | None:
    """Per-share fill price of the OPEN order, or None. Only a filled/partial open order
    with a real number counts; None / strings / bools are never read as a price."""
    b = s.get("broker")
    if not isinstance(b, dict) or b.get("status") not in BROKER_FILLED:
        return None
    fill = b.get("fill_price")
    if isinstance(fill, bool) or not isinstance(fill, (int, float)):
        return None
    return float(fill)


def _broker_cells(s: dict) -> list[str]:
    """[券商成交, 滑点]. Slippage = (fill - paper credit) x 100 x contracts; for a SELL a
    negative number means the broker fill was worse than the paper fill."""
    fill = _broker_fill(s)
    credit = s.get("credit")
    if fill is None:
        return ["—", "—"]
    if isinstance(credit, bool) or not isinstance(credit, (int, float)):
        return [f"${fill:.2f}", "—"]
    return [f"${fill:.2f}", _money((fill - credit) * 100 * s.get("contracts", 1))]


def _table_head(headers: list[str], broker: bool) -> list[str]:
    cols = headers + (BROKER_HEADERS if broker else [])
    return ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]


def _tail(s: dict, broker: bool) -> str:
    """Extra cells appended after a row's closing '|' (already ends with one)."""
    return "".join(f" {c} |" for c in _broker_cells(s)) if broker else ""


def _csp_open_rows(open_: list[dict], marks: dict, broker: bool = False) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = _table_head(["策略", "#", "标的", "腿", "到期", "张数", "浮盈亏", "占用资金", "盈亏%", "距短腿", "提示"], broker)
    for s in open_:
        pnl, dist, note = _mark_cells(marks.get(s["id"]), csp=True)
        lines.append(f"| {strategy_of(s)} | {s['id']} | {s['ticker']} | {legs_label(s)} "
                     f"| {s['expiry']} | {s['contracts']} | {pnl} | {_usd(capital_at_risk(s))} "
                     f"| {_open_pct(s, marks)} | {dist} | {note} |" + _tail(s, broker))
    return lines + [""]


def _csp_settled_rows(settled: list[dict], broker: bool = False) -> list[str]:
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = _table_head(["策略", "#", "标的", "腿", "到期", "结果", "方式", "盈亏", "占用资金", "盈亏%"], broker)
    for s in sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        how = "提前平仓" if s["status"] == "closed_early" else "到期结算"
        lines.append(f"| {strategy_of(s)} | {s['id']} | {s['ticker']} | {legs_label(s)} "
                     f"| {s['expiry']} | {s['result']} | {how} | {_money(s['pnl'])} "
                     f"| {_usd(capital_at_risk(s))} | {_spct(_pnl_ratio(s['pnl'], s))} |" + _tail(s, broker))
    return lines + [""]


def _cc_open_rows(open_: list[dict], marks: dict, broker: bool = False) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = _table_head(["#", "标的", "腿", "到期", "张数", "浮盈亏", "盈亏%", "距短腿", "提示"], broker)
    for s in open_:
        pnl, dist, note = _mark_cells(marks.get(s["id"]), csp=True)
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} | {s['expiry']} "
                     f"| {s['contracts']} | {pnl} | {_open_pct(s, marks)} | {dist} | {note} |" + _tail(s, broker))
    return lines + [""]


def _cc_settled_rows(settled: list[dict], broker: bool = False) -> list[str]:
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = _table_head(["#", "标的", "腿", "到期", "结果", "方式", "盈亏（期权腿）", "含股票腿", "盈亏%"], broker)
    for s in sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        how = "提前平仓" if s["status"] == "closed_early" else "到期结算"
        stock = s.get("stock_leg_pnl")
        total = "—" if stock is None else _money(s["pnl"] + stock)
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} | {s['expiry']} "
                     f"| {result_text(s)} | {how} | {_money(s['pnl'])} | {total} "
                     f"| {_spct(_pnl_ratio(s['pnl'], s))} |" + _tail(s, broker))
    return lines + [""]


def _split_status(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    return ([s for s in rows if s["status"] == "open"],
            [s for s in rows if s["status"] in SETTLED])


LOT_PUT_NOTE = ("> 零成本只指现金流（Call 收入抵掉 Put 支出），不是机会成本：Call 行权价以上放弃的涨幅"
                "仍然是代价。本节不计胜率，也不与任何盈亏平衡线比较；股票腿盈亏仅供参考，头条盈亏只含期权腿。")


def lot_put_legs(s: dict) -> str:
    """'71.5 Put / 93.25 Call' for a collar, '71.5 Put' for a protective put."""
    put = f"{s['put_strike']:g} Put"
    return put if s.get("call_strike") is None else f"{put} / {s['call_strike']:g} Call"


def lot_put_range(s: dict) -> str:
    cap = "不封顶" if s.get("call_strike") is None else f"{s['call_strike']:g}"
    return f"锁定区间 {s['put_strike']:g} ~ {cap}"


def lot_put_net_cost(s: dict) -> str:
    """Signed net option cash flow: per share and in dollars (a debit is money out)."""
    net = s["net_credit"]
    if round(net, 2) == 0:
        return "净成本 $0（零成本）"
    dollars = _usd(abs(net) * 100 * s["contracts"])
    side = "净收入" if net > 0 else "净支出"
    return f"{side} ${abs(net):.2f}/股（{dollars}）"


def lot_put_cap_cost(s: dict) -> str:
    """What the sold call gives up; a protective put sells no call, so it gives up nothing."""
    call, ref = s.get("call_strike"), s.get("reference_price")
    if call is None:
        return "无上限代价（没卖 Call，涨幅不封顶）"
    tail = f"（相对参考价（真实持仓为平均成本）{(call / ref - 1) * 100:+.1f}%）" if ref else ""
    return f"上限代价：涨过 {call:g} 的部分放弃{tail}"


def _lot_float(s: dict, marks: dict) -> str:
    m = marks.get(s["id"])
    if m is None:
        return "运行 status 查看"
    if "error" in m:
        return m["error"]
    return _money(m["pnl"]) + ("（报价陈旧）" if m.get("stale") else "")


def _lot_open_rows(open_: list[dict], marks: dict) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = _table_head(["#", "标的", "策略", "股数", "到期", "张数", "Put 下限", "Call 上限",
                         "净成本", "区间", "上限代价", "浮盈亏"], False)
    for s in open_:
        call = "—" if s.get("call_strike") is None else f"{s['call_strike']:g}"
        lines.append(f"| {s['id']} | {s['ticker']} | {strategy_of(s)} | {s['covered_shares']} "
                     f"| {s['expiry']} | {s['contracts']} | {s['put_strike']:g} | {call} "
                     f"| {lot_put_net_cost(s)} | {lot_put_range(s)} | {lot_put_cap_cost(s)} "
                     f"| {_lot_float(s, marks)} |")
    return lines + [""]


def _lot_settled_rows(settled: list[dict]) -> list[str]:
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = _table_head(["#", "标的", "策略", "到期", "结果", "方式", "期权腿盈亏", "股票腿",
                         "合计（参考）", "Put 下限", "Call 上限", "净成本"], False)
    for s in sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        stock = s.get("stock_leg_pnl")
        how = "提前平仓" if s["status"] == "closed_early" else "到期结算"
        call = "—" if s.get("call_strike") is None else f"{s['call_strike']:g}"
        lines.append(f"| {s['id']} | {s['ticker']} | {strategy_of(s)} | {s['expiry']} "
                     f"| {result_text(s)} | {how} | {_money(s['pnl'])} "
                     f"| {'—' if stock is None else _money(stock)} "
                     f"| {'—' if stock is None else _money(s['pnl'] + stock)} "
                     f"| {s['put_strike']:g} | {call} | {lot_put_net_cost(s)} |")
    return lines + [""]


def _lot_summary(rows: list[dict]) -> list[str]:
    """Per strategy: counts by result label, option-leg pnl and net cost. No win rate."""
    out = []
    for name in LOT_PUT_STRATEGIES:
        settled = [s for s in rows if strategy_of(s) == name and s["status"] in SETTLED]
        if not settled:
            continue
        counts = " / ".join(f"{label} {sum(1 for s in settled if s['result'] == label)}"
                            for label in ("called_away", "protected", "expired_worthless", "closed"))
        cash = sum(s["net_credit"] * 100 * s["contracts"] for s in settled)
        cost = "净成本合计 $0" if round(cash) == 0 else (
            f"净{'收入' if cash > 0 else '支出'}合计 {_usd(abs(cash))}")
        total = sum(s["pnl"] for s in settled)
        out.append(f"- {name}：已结算 {len(settled)}（{counts}），期权腿总盈亏 {_money(total)}，{cost}")
    return out + [""] if out else []


def _lot_put_section(rows: list[dict], marks: dict) -> list[str]:
    open_, settled = _split_status(rows)
    return ["## 保护性 Put 与 Collar 记录", "", LOT_PUT_NOTE, "", *_lot_summary(rows),
            "**未平仓**", "", *_lot_open_rows(open_, marks),
            "**已结算**", "", *_lot_settled_rows(settled)]


def _other_strategy_sections(others: list[dict], records: list[dict], marks: dict,
                             broker: bool = False) -> list[str]:
    puts = [s for s in others if strategy_of(s) == STRATEGY_CSP]
    calls = [s for s in others if strategy_of(s) == STRATEGY_COVERED_CALL]
    condors = [s for s in others if strategy_of(s) == STRATEGY_IRON_CONDOR]
    out = ["## 按策略对比", "", *_strategy_section(records)]
    if puts:
        open_, settled = _split_status(puts)
        out += ["## 现金担保 Put 记录", "", "**未平仓**", "", *_csp_open_rows(open_, marks, broker),
                "**已结算**", "", *_csp_settled_rows(settled, broker)]
    if calls:
        open_, settled = _split_status(calls)
        out += ["## 备兑 Call（Covered Call）记录", "", "**未平仓**", "", *_cc_open_rows(open_, marks, broker),
                "**已结算**", "", *_cc_settled_rows(settled, broker)]
    if condors:
        out += _condor_section(condors, marks)
    bullcalls = [s for s in others if strategy_of(s) == STRATEGY_BULL_CALL]
    if bullcalls:
        out += _bullcall_module().bullcall_section(bullcalls, marks)
    return out


def _bullcall_module():
    # Imported on use: spread_report_bullcall builds on this module's helpers, so a top-level
    # import would be circular.
    import spread_report_bullcall
    return spread_report_bullcall


CONDOR_FOOTER = "铁鹰=最大亏损（单侧宽度−净权利金）×100×张数（到期最多只有一侧亏）；"
CONDOR_NOTE = ("> 所需胜率 = 理论盈亏平衡胜率 =（单侧宽度 − 净权利金）÷ 单侧宽度，只按开仓时的权利金和宽度算；"
               "它假设每笔结果只有满额盈利或满额最大亏损两种（不计部分亏损和提前平仓）；"
               "「按策略对比」表里的盈亏平衡胜率按实际盈亏比算，两者口径不同。"
               "大涨或大跌越过任一侧长翼都是满额亏损。")
BREACHED_LABELS = BREACHED_SIDE_LABELS


def condor_required_win_rate(s: dict) -> float:
    """Theoretical breakeven win rate of one condor: (width - net credit) / width."""
    return core.breakeven_win_rate(s["net_credit"], s["width"])


def condor_breakevens_label(s: dict) -> str:
    return f"{s['breakeven_low']:.2f} / {s['breakeven_high']:.2f}"


def breached_label(s: dict) -> str:
    """Breached side of a settled condor; an early close never records one, so it shows a dash."""
    return BREACHED_LABELS.get(s.get("breached_side"), "—")


def condor_theory_avg(records: list[dict]) -> float | None:
    """Per-trade average of the theoretical breakeven win rate over settled condors."""
    settled = [s for s in records
               if strategy_of(s) == STRATEGY_IRON_CONDOR and s["status"] in SETTLED]
    return sum(map(condor_required_win_rate, settled)) / len(settled) if settled else None


def condor_theory_note(records: list[dict]) -> list[str]:
    if not any(strategy_of(s) == STRATEGY_IRON_CONDOR for s in records):
        return []
    return [f"> iron_condor 理论盈亏平衡胜率（已结算逐笔平均，1 − 净权利金÷宽度）"
            f"{_pct(condor_theory_avg(records))}；上表的盈亏平衡胜率按实际盈亏比算，口径不同。", ""]


def _condor_terms(s: dict) -> str:
    """Breakevens, net credit, max loss (= capital) and required win rate cells."""
    return (f"{condor_breakevens_label(s)} | ${s['net_credit']:.2f} | {_usd(capital_at_risk(s))} "
            f"| {_pct(condor_required_win_rate(s))}")


CONDOR_LEGS_HEAD = "腿（长Put/短Put/短Call/长Call）"
CONDOR_TERMS_HEAD = ["盈亏平衡价", "净权利金", "最大亏损/占用资金", "所需胜率"]


def _condor_open_rows(open_: list[dict], marks: dict) -> list[str]:
    if not open_:
        return ["（无未平仓）", ""]
    lines = _table_head(["#", "标的", CONDOR_LEGS_HEAD, "到期", "张数", *CONDOR_TERMS_HEAD,
                         "浮盈亏", "盈亏%", "距较近短腿", "提示"], False)
    for s in open_:
        pnl, dist, note = _mark_cells(marks.get(s["id"]))
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} | {s['expiry']} "
                     f"| {s['contracts']} | {_condor_terms(s)} | {pnl} | {_open_pct(s, marks)} "
                     f"| {dist} | {note} |")
    return lines + [""]


def _condor_settled_rows(settled: list[dict]) -> list[str]:
    if not settled:
        return ["（还没有已结算的）", ""]
    lines = _table_head(["#", "标的", CONDOR_LEGS_HEAD, "到期", *CONDOR_TERMS_HEAD,
                         "结果", "方式", "盈亏", "盈亏%", "突破侧"], False)
    for s in sorted(settled, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        how = "提前平仓" if s["status"] == "closed_early" else "到期结算"
        lines.append(f"| {s['id']} | {s['ticker']} | {legs_label(s)} | {s['expiry']} "
                     f"| {_condor_terms(s)} | {s['result']} | {how} | {_money(s['pnl'])} "
                     f"| {_spct(_pnl_ratio(s['pnl'], s))} | {breached_label(s)} |")
    return lines + [""]


def _condor_summary(rows: list[dict]) -> list[str]:
    """Settled count, win rate vs theoretical breakeven, breached-side split, sample warning."""
    settled = [s for s in rows if s["status"] in SETTLED]
    n = len(settled)
    wins = sum(1 for s in settled if s["pnl"] > 0)
    sides = " / ".join(f"{label} {sum(1 for s in settled if s.get('breached_side') == key)}"
                       for key, label in BREACHED_LABELS.items())
    early = sum(1 for s in settled if s.get("breached_side") is None)
    tail = f"（另有 {early} 笔提前平仓，不记突破侧）" if early else ""
    line = (f"- 已结算 {n}，胜率 {_pct(wins / n if n else None)}，理论盈亏平衡胜率（逐笔平均）"
            f"{_pct(condor_theory_avg(rows))}，突破侧：{sides}{tail}，"
            f"总盈亏 {_money(sum(s['pnl'] for s in settled))}")
    warn = [f"> ⚠️ 样本不足（{n} < {MIN_SAMPLE}），别下结论。", ""] if n < MIN_SAMPLE else []
    return [line, "", *warn]


def _condor_section(rows: list[dict], marks: dict) -> list[str]:
    open_, settled = _split_status(rows)
    return ["## 铁鹰（Iron Condor）记录", "", CONDOR_NOTE, "", *_condor_summary(rows),
            "**未平仓**", "", *_condor_open_rows(open_, marks),
            "**已结算**", "", *_condor_settled_rows(settled)]


def render_report(records: list[dict], marks: dict | None, generated_on: str,
                  broker_cols: bool = False) -> str:
    marks = marks or {}
    spreads = [s for s in records if strategy_of(s) == STRATEGY_BULL_PUT]
    lot_puts = [s for s in records if strategy_of(s) in LOT_PUT_STRATEGIES]
    others = [s for s in records if strategy_of(s) in OTHER_SECTION_STRATEGIES]
    st = compute_stats(spreads)
    open_ = [s for s in spreads if s["status"] == "open"]
    gap = "—" if st["avg_abs_gap"] is None else f"{st['avg_abs_gap']:.2f}%"
    out = [
        "# 垂直 Put 贷方价差与现金担保 Put 纸上交易报告", "",
        f"*由 `/spread-sim` 自动生成于 {generated_on}，只读。纸上交易，不含真实持仓。*", "",
        "## 未平仓", "", *_open_summary(records, marks), *_peak_line(records),
        *_open_section(open_, marks),
        "## 已结算", "", *_settled_section(spreads),
        "## 累积统计", "", *_stats_section(st),
        "## 分档对比", "", *_bucket_section(st),
        "## 开仓价假设的偏差", "",
        f"下一个交易日开盘相对快照价的平均绝对跳空：{gap}。真实世界无法在快照价成交，这个数字越大，模拟结果越乐观。", "",
    ]
    if others:
        out += _other_strategy_sections(others, records, marks, broker_cols)
    if lot_puts:
        out += _lot_put_section(lot_puts, marks)
    cc_note = ("备兑 Call=不占用额外现金（股票已持有），盈亏%=期权腿盈亏÷(成本×股数)；"
               if any(strategy_of(s) == STRATEGY_COVERED_CALL for s in others) else "")
    lot_note = ("保护性 Put / Collar=占用资金为净支出（净收入记 0），盈亏%=期权腿盈亏÷(成本×股数)；"
                if lot_puts else "")
    condor_note = (CONDOR_FOOTER if any(strategy_of(s) == STRATEGY_IRON_CONDOR for s in others)
                   else "")
    if any(strategy_of(s) == STRATEGY_BULL_CALL for s in others):
        condor_note += _bullcall_module().BULLCALL_FOOTER
    out += ["---", "*口径：每张合约 ×100 的美元；中间价成交；不含手续费。"
            "占用资金：Put 贷方价差=最大亏损（宽度−权利金）×100×张数，"
            "现金担保 Put=行权价×100×张数（需备足的现金）；"
            f"{cc_note}{lot_note}{condor_note}盈亏%=盈亏÷占用资金。*", ""]
    return "\n".join(out)


def _width_tag(width_info: dict | None) -> str:
    if not width_info:
        return ""
    src, rec = width_info.get("source"), width_info.get("recommended")
    if src == "auto":
        return "（自动推荐）"
    if src == "manual":
        return f"（你指定，推荐 ${rec:g}）" if rec else "（你指定）"
    return "（默认，无法估算推荐）"


def format_suggestions(ticker: str, snapshot_date: str, spot: float, expiry: str,
                       days: int, earnings: str, width: float,
                       candidates: list[dict], intraday: bool = False,
                       width_info: dict | None = None) -> str:
    est = {"yes": "到期前有财报 ⚠️", "no": "到期前无财报", "unknown": "财报日期未知 ⚠️"}[earnings]
    lines = [
        f"{ticker} 快照 {snapshot_date} {'盘中' if intraday else '收盘'} ${spot:,.2f} · 到期 {expiry}（{days} 天）· 宽度 ${width:g}{_width_tag(width_info)} · {est}", "",
        "| 档位 | 短腿/长腿 | 权利金 | 最大盈/亏 | 占用资金/张 | 平衡价 | 权利金/宽度 | 盈亏平衡胜率 | 短腿delta | 距现价/预期波动 |",
        "|------|-----------|--------|-----------|-------------|--------|-------------|--------------|-----------|-----------------|",
    ]
    notes = []
    for c in candidates:
        if not c["available"]:
            lines.append(f"| {c['bucket']:.2f} | — | — | — | — | — | — | — | — | 无合适合约 |")
            notes.append(f"档位 {c['bucket']:.2f}：{c['reason']}")
            continue
        lines.append(
            f"| {c['bucket']:.2f} | {c['short_strike']:g}/{c['long_strike']:g} | ${c['credit']:.2f} "
            f"| +${c['max_profit']:,.0f}/-${c['max_loss']:,.0f} | ${c['max_loss']:,.0f} | ${c['breakeven']:.2f} "
            f"| {c['credit_ratio'] * 100:.1f}% | {c['breakeven_win_rate'] * 100:.0f}% "
            f"| {c['short_delta']:.2f} | {c['em_multiple']:.2f} 倍 |")
        for w in c["warnings"]:
            notes.append(f"档位 {c['bucket']:.2f}：{w}")
    if notes:
        lines += ["", "提示："] + [f"- {n}" for n in notes]
    return "\n".join(lines)


PUBLIC_TITLE = "# 模拟盘报告（垂直 Put 贷方价差与现金担保 Put 纸上交易）"


def _label_contracts(lines: list[str]) -> list[str]:
    """Write the 张数 column as "N 张" so a contract count never reads as a share count."""
    out: list[str] = []
    col: int | None = None
    for line in lines:
        if not line.startswith("|"):
            col = None
            out.append(line)
            continue
        cells = line.split("|")
        names = [c.strip() for c in cells]
        if "张数" in names:
            col = names.index("张数")
        elif col is not None and col < len(cells) and names[col].isdigit():
            cells[col] = f" {names[col]} 张 "
            line = "|".join(cells)
        out.append(line)
    return out


def public_records(records: list[dict]) -> list[dict]:
    """Only the paper-only strategies; everything else is withheld from the public page."""
    return [s for s in records if strategy_of(s) in PUBLIC_STRATEGIES]


def render_public_report(records: list[dict], marks: dict | None, generated_on: str) -> str:
    """Shareable markdown for the public KB (wiki/options/paper-trading.md).

    An allowlist, not a blacklist: covered calls, collars and protective puts are
    written against held lots (real share counts and cost), which must never reach the
    public tree, and a strategy added later must opt in. Front matter matches the
    other wiki pages.
    """
    shareable = public_records(records)
    body = _label_contracts(render_report(shareable, marks, generated_on, broker_cols=False).split("\n"))
    body[0] = PUBLIC_TITLE
    body[2] = (f"*数据快照 {generated_on}，非实时。纸上交易，不含真实持仓；"
               "涉及真实持股的策略（备兑 Call、保护性 Put、Collar）不在此公开。不构成投资建议。*")
    return f"---\nsource: {option_settings.public_source_label()}\n---\n\n" + "\n".join(body)
