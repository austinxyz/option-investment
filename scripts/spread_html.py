#!/usr/bin/env python3
"""Friendly single-file HTML view of the paper-trading ledger (pure rendering, no I/O).

Same data as report.md, laid out as cards with a payoff chart and a P&L bar per open trade.
See docs/superpowers/specs/2026-10-14-spread-sim-design.md.
"""
from __future__ import annotations
import html
from datetime import date

from spread_ledger import (BULLCALL_LABEL, STRATEGY_BULL_CALL, STRATEGY_BULL_PUT, STRATEGY_COVERED_CALL,
                           STRATEGY_IRON_CONDOR, strategy_of)
from spread_report import (BULLCALL_THRESHOLD_LABELS, CONDOR_FOOTER, CONDOR_LEGS_HEAD, KNOWN_STRATEGIES, LOT_PUT_NOTE,
                           LOT_PUT_STRATEGIES, MIN_SAMPLE, SETTLED, SINGLE_SPREAD_STATS, STRATEGY_CSP,
                           breached_label, capital_at_risk, compute_stats, compute_strategy_stats,
                           condor_breakevens_label, condor_required_win_rate, condor_theory_avg,
                           legs_label, lot_put_cap_cost, lot_put_net_cost, lot_put_range, result_text,
                           threshold_labels)
from spread_report import _open_pct, _pnl_ratio, _spct
from spread_report_bullcall import (BULLCALL_FOOTER, BULLCALL_LEGS_HEAD, BULLCALL_TERMS_HEAD,
                                    captured_label, how_label, pnl_pct, required_avg_label,
                                    signed_pct, term_cells)

e = html.escape

CSS = """
:root{--bg:#fbfaf7;--card:#fff;--ink:#1d2330;--mut:#5d6678;--line:#e3e0d8;--acc:#1f5fbf;--good:#18794e;--bad:#b3261e;--warn:#a15c00;--soft:#f1efe8}
@media (prefers-color-scheme: dark){:root{--bg:#14171d;--card:#1b1f27;--ink:#e8eaf0;--mut:#9aa3b5;--line:#2b313c;--acc:#6ea8ff;--good:#52c28a;--bad:#ff7b72;--warn:#e5a54b;--soft:#232834}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:26px 16px 70px}h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:34px 0 10px;padding-top:6px;border-top:1px solid var(--line)}
.sub{color:var(--mut);margin:0 0 16px}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}.mut{color:var(--mut)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:10px 12px}.kpi span{display:block;font-size:12.5px;color:var(--mut)}.kpi b{font-size:21px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}
.card h3{font-size:17px;margin:0 0 8px}.tag{font-size:12.5px;font-weight:600;padding:2px 9px;border-radius:99px;background:var(--soft);border:1px solid var(--line);margin-left:6px}
.tag.warn{color:var(--warn)}.mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:8px 0 12px}
.mini div{background:var(--soft);border-radius:9px;padding:7px 10px}.mini span{display:block;font-size:12px;color:var(--mut)}.mini b{font-size:17px}
.alert{border-left:4px solid var(--bad);background:var(--soft);border-radius:8px;padding:8px 14px;margin:10px 0;font-size:14.5px}.alert.tp{border-color:var(--good)}
.bar{position:relative;height:12px;border-radius:99px;background:linear-gradient(90deg,rgba(179,38,30,.28),rgba(120,120,120,.12) 50%,rgba(24,121,78,.28));margin:26px 4px 24px}
.bar i{position:absolute;top:-5px;width:3px;height:22px;background:var(--mut);border-radius:2px}.bar i.now{background:var(--acc);width:5px;top:-7px;height:26px}
.bar em{position:absolute;top:18px;font-size:11.5px;font-style:normal;color:var(--mut);transform:translateX(-50%);white-space:nowrap}
.bar em.up{top:-26px}svg{width:100%;height:auto;display:block}.pl{fill:none;stroke:var(--acc);stroke-width:3;stroke-linejoin:round}
.axis{stroke:currentColor;stroke-opacity:.35}.winbg{fill:var(--good);fill-opacity:.07}.lossbg{fill:var(--bad);fill-opacity:.06}
.vl{stroke-dasharray:4 4;stroke-width:1.2}.vl.mut{stroke:var(--mut)}.vl.warn{stroke:var(--warn)}.vl.acc{stroke:var(--acc)}
.lab{font-size:12px;fill:currentColor}.lab.good{fill:var(--good)}.lab.bad{fill:var(--bad)}.lab.warn{fill:var(--warn)}.lab.acc{fill:var(--acc)}.lab.mut{fill:var(--mut)}
table{border-collapse:collapse;width:100%;font-size:14.5px}th,td{border:1px solid var(--line);padding:8px 10px;text-align:left}th{background:var(--soft)}.tw{overflow-x:auto}
.note{border:1px dashed var(--warn);border-radius:9px;padding:8px 14px;font-size:14px;margin:10px 0}
.foot{margin-top:36px;padding:12px 14px;border:1px dashed var(--line);border-radius:10px;color:var(--mut);font-size:13.5px}
"""


def money(x: float, sign: bool = True) -> str:
    s = f"${abs(x):,.0f}"
    if not sign:
        return s
    return ("+" if x > 0 else "-" if x < 0 else "") + s


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _days_left(expiry: str, today: str) -> int:
    return (date.fromisoformat(expiry) - date.fromisoformat(today)).days


def _chart(s: dict, now_price: float | None) -> str:
    n = s["contracts"]
    short, long_, credit = s["short_strike"], s["long_strike"], s["credit"]
    win, loss = credit * 100 * n, (short - long_ - credit) * 100 * n
    be = short - credit
    ref = now_price or s["underlying_price"]
    W, H, L, R, T, B = 540, 262, 50, 14, 14, 74
    xmin, xmax = min(long_, ref) * 0.985, max(s["underlying_price"], ref) * 1.01
    ymax, ymin = win * 1.7, -loss * 1.12
    sx = lambda x: L + (x - xmin) / (xmax - xmin) * (W - L - R)
    sy = lambda y: T + (ymax - y) / (ymax - ymin) * (H - T - B)
    path = " ".join(("M" if i == 0 else "L") + f"{sx(x):.1f} {sy(y):.1f}"
                    for i, (x, y) in enumerate([(xmin, -loss), (long_, -loss), (short, win), (xmax, win)]))
    parts = [f'<rect x="{L}" y="{sy(0):.1f}" width="{W-L-R}" height="{H-B-sy(0):.1f}" class="lossbg"/>',
             f'<rect x="{L}" y="{T}" width="{W-L-R}" height="{sy(0)-T:.1f}" class="winbg"/>',
             f'<line x1="{L}" x2="{W-R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="axis"/>',
             f'<path d="{path}" class="pl"/>']

    def vline(x: float, label: str, cls: str, dy: int = 0) -> None:
        parts.append(f'<line x1="{sx(x):.1f}" x2="{sx(x):.1f}" y1="{T}" y2="{H-B}" class="vl {cls}"/>')
        parts.append(f'<text x="{sx(x):.1f}" y="{H-B+15+dy}" class="lab {cls}" text-anchor="middle">{label}</text>')

    # four label rows so strikes only a few dollars apart never overprint each other
    vline(long_, f"长腿 {long_:g}", "mut", 0)
    vline(be, f"平衡 {be:.1f}", "warn", 14)
    vline(short, f"短腿 {short:g}", "mut", 28)
    vline(ref, f"现价 {ref:.0f}", "acc", 42)
    parts.append(f'<text x="{L+6}" y="{sy(win)-6:.1f}" class="lab good">最大盈利 {money(win)}</text>')
    parts.append(f'<text x="{L+6}" y="{sy(-loss)-6:.1f}" class="lab bad">最大亏损 {money(-loss)}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(s["ticker"])} 到期盈亏图">{"".join(parts)}</svg>'


def _pnl_bar(s: dict, pnl: float) -> str:
    n = s["contracts"]
    win, loss = s["credit"] * 100 * n, (s["width"] - s["credit"]) * 100 * n
    pos = lambda v: max(0.0, min(100.0, (v + loss) / (win + loss) * 100))
    return ('<div class="bar">'
            f'<i style="left:{pos(-0.5 * loss):.1f}%"></i><em style="left:{pos(-0.5 * loss):.1f}%">止损线 {money(-0.5 * loss)}</em>'
            f'<i style="left:{pos(0):.1f}%"></i><em class="up" style="left:{pos(0):.1f}%">0</em>'
            f'<i style="left:{pos(0.5 * win):.1f}%"></i><em style="left:{pos(0.5 * win):.1f}%">止盈线 {money(0.5 * win)}</em>'
            f'<i class="now" style="left:{pos(pnl):.1f}%"></i><em class="up" style="left:{pos(pnl):.1f}%">现在 {money(pnl)}</em>'
            '</div>')


def _open_card(s: dict, mark: dict | None, today: str) -> str:
    n = s["contracts"]
    capital = (s["width"] - s["credit"]) * 100 * n
    left = _days_left(s["expiry"], today)
    tags = f'<span class="tag">到期 {e(s["expiry"])}（还剩 {left} 天）</span>'
    if s.get("earnings_in_window"):
        tags += '<span class="tag warn">带财报</span>'
    if s.get("earnings_in_window") is None:
        tags += '<span class="tag warn">财报未知</span>'
    head = (f'<h3>#{s["id"]} {e(s["ticker"])} · 卖 {s["short_strike"]:g} / 买 {s["long_strike"]:g} Put'
            f'{"" if n == 1 else f" × {n} 张"} {tags}</h3>')
    base = (f'<div><span>权利金</span><b>${s["credit"]:.2f}</b></div>'
            f'<div><span>占用资金</span><b>{money(capital, False)}</b></div>'
            f'<div><span>档位</span><b>{s["delta_bucket"]:.2f}</b></div>')
    if mark is None:
        body = f'<div class="mini">{base}<div><span>浮盈亏</span><b class="mut">运行 status 查看</b></div></div>' + _chart(s, None)
    elif "error" in mark:
        body = f'<div class="mini">{base}</div><div class="note">{e(mark["error"])}</div>' + _chart(s, None)
    else:
        cls = "good" if mark["pnl"] > 0 else "bad" if mark["pnl"] < 0 else "mut"
        banners = ""
        if mark["flags"]["stop"]:
            banners += '<div class="alert">🔴 <b>已到止损线</b>（亏损达最大亏损的 50%）。只是提示，要不要平仓由你决定。</div>'
        if mark["flags"]["take_profit"]:
            banners += '<div class="alert tp">🟢 <b>已到止盈线</b>（盈利达权利金的 50%）。只是提示，要不要平仓由你决定。</div>'
        stale = '<div class="note">报价陈旧（休市或买卖价为空，用了最后成交价），浮盈亏只是估算。</div>' if mark.get("stale") else ""
        body = (f'<div class="mini">{base}<div><span>浮盈亏</span><b class="{cls}">{money(mark["pnl"])}</b></div>'
                f'<div><span>现价</span><b>{mark["underlying"]:.2f}</b></div>'
                f'<div><span>距短腿</span><b>{mark["dist_pct"]:.1f}%</b></div></div>'
                f'{banners}{stale}{_pnl_bar(s, mark["pnl"])}{_chart(s, mark["underlying"])}')
    return f'<section class="card">{head}{body}</section>'


def _kpi_pair(label: str, rows: list[dict]) -> str:
    return (f'<div class="kpi"><span>{label}已结算</span><b>{len(rows)} 笔</b></div>'
            f'<div class="kpi"><span>{label}已实现盈亏</span><b>{money(sum(s["pnl"] for s in rows))}</b></div>')


def _settled_kpis(st: dict, condors: list[dict], bullcalls: list[dict]) -> str:
    """Settled KPIs; with settled condors or bull calls the spread figures are labelled as such
    and each of those gets its own pair, so a '0 笔' never sits above a table of their rows."""
    tag = "（Put 贷方价差）" if condors or bullcalls else ""
    out = (f'<div class="kpi"><span>已结算{tag}</span><b>{st["n_settled"]} 笔</b></div>'
           f'<div class="kpi"><span>已实现盈亏{tag}</span><b>{money(st["total_pnl"])}</b></div>')
    if condors:
        out += _kpi_pair("铁鹰", condors)
    if bullcalls:
        out += _kpi_pair(BULLCALL_LABEL, bullcalls)
    return out


def _summary(open_: list[dict], marks: dict, st: dict, condors: list[dict],
             bullcalls: list[dict]) -> str:
    capital = sum(capital_at_risk(s) for s in open_)  # same basis as the markdown report
    # Collar / protective-put pnl has a different base (cost x shares): shown in its own table.
    valid = [marks[s["id"]]["pnl"] for s in open_ if s["id"] in marks and "pnl" in marks[s["id"]]
             and strategy_of(s) not in LOT_PUT_STRATEGIES]
    unreal = money(sum(valid)) if valid else "—"
    ucls = "mut" if not valid else ("good" if sum(valid) > 0 else "bad" if sum(valid) < 0 else "mut")
    return ('<div class="kpis">'
            f'<div class="kpi"><span>未平仓</span><b>{len(open_)} 笔</b></div>'
            f'<div class="kpi"><span>占用资金合计</span><b>{money(capital, False)}</b></div>'
            f'<div class="kpi"><span>浮盈亏合计</span><b class="{ucls}">{unreal}</b></div>'
            f'{_settled_kpis(st, condors, bullcalls)}</div>')


def _bucket_cell(s: dict) -> str:
    return f'{s["delta_bucket"]:.2f}' if s.get("delta_bucket") is not None else "—"


def _single_mark_cells(m: dict | None, csp: bool = True,
                       labels: tuple[str, str] | None = None) -> tuple[str, str, str]:
    """(pnl, distance, note) html cells for an open record's mark (csp=False: spread basis;
    `labels` overrides the tip text for a strategy with its own basis)."""
    if m is None or "error" in m:
        return "—", "—", e(m["error"]) if m else "运行 status 查看"
    cls = "good" if m["pnl"] > 0 else "bad" if m["pnl"] < 0 else "mut"
    stop_text, tp_text = labels or threshold_labels(csp)
    tips = []
    if m["flags"]["stop"]:
        tips.append(stop_text)
    if m["flags"]["take_profit"]:
        tips.append(tp_text)
    if m.get("stale"):
        tips.append("报价陈旧")
    return f'<b class="{cls}">{money(m["pnl"])}</b>', f'{m["dist_pct"]:.1f}%', "；".join(tips) or "—"


def _single_open_table(title: str, rows: list[dict], marks: dict) -> str:
    """Compact table for single-leg records (no payoff chart)."""
    if not rows:
        return ""
    body = ""
    for s in rows:
        pnl, dist, note = _single_mark_cells(marks.get(s["id"]))
        body += (f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(legs_label(s))}</td>'
                 f'<td>{e(s["expiry"])}</td><td>{s["contracts"]}</td>'
                 f'<td>{money(s["credit"] * 100 * s["contracts"], False)}</td>'
                 f'<td>{pnl}</td><td>{dist}</td><td>{note}</td></tr>')
    return (f'<h3>{title}</h3><div class="tw"><table><thead><tr><th>#</th><th>标的</th><th>腿</th>'
            '<th>到期</th><th>张数</th><th>权利金</th><th>浮盈亏</th><th>距短腿</th><th>提示</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _csp_open_table(rows: list[dict], marks: dict) -> str:
    return _single_open_table("现金担保 Put（Cash-Secured Put）", rows, marks)


def _cc_open_table(rows: list[dict], marks: dict) -> str:
    """Open covered calls (stock already held, no extra cash tied up)."""
    return _single_open_table("备兑 Call（Covered Call）", rows, marks)


def _condor_term_cells(s: dict) -> str:
    return (f'<td>{e(condor_breakevens_label(s))}</td><td>${s["net_credit"]:.2f}</td>'
            f'<td>{money(capital_at_risk(s), False)}</td><td>{pct(condor_required_win_rate(s))}</td>')


CONDOR_TERMS_TH = "<th>盈亏平衡价</th><th>净权利金</th><th>最大亏损/占用资金</th><th>所需胜率</th>"


def _condor_open_table(rows: list[dict], marks: dict) -> str:
    """Open iron condors: four strikes and their own terms, no single-spread payoff chart."""
    if not rows:
        return ""
    body = ""
    for s in rows:
        pnl, dist, note = _single_mark_cells(marks.get(s["id"]), csp=False)
        body += (f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(legs_label(s))}</td>'
                 f'<td>{e(s["expiry"])}</td><td>{s["contracts"]}</td>{_condor_term_cells(s)}'
                 f'<td>{pnl}</td><td>{_open_pct(s, marks)}</td><td>{dist}</td><td>{note}</td></tr>')
    return (f'<h3>铁鹰（Iron Condor）</h3><div class="tw"><table><thead><tr><th>#</th><th>标的</th>'
            f'<th>{CONDOR_LEGS_HEAD}</th><th>到期</th><th>张数</th>{CONDOR_TERMS_TH}'
            '<th>浮盈亏</th><th>盈亏%</th><th>距较近短腿</th><th>提示</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _condor_settled_row(s: dict) -> str:
    cls = "good" if s["pnl"] > 0 else "bad" if s["pnl"] < 0 else "mut"
    how = "提前平仓" if s["status"] == "closed_early" else "到期结算"
    return (f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(legs_label(s))}</td>'
            f'<td>{e(s["expiry"])}</td>{_condor_term_cells(s)}<td>{how}</td>'
            f'<td>{e(str(s["result"]))}</td><td class="{cls}">{money(s["pnl"])}</td>'
            f'<td>{_spct(_pnl_ratio(s["pnl"], s))}</td><td>{e(breached_label(s))}</td></tr>')


def _condor_settled_table(records: list[dict]) -> str:
    rows = [s for s in records if s["status"] in SETTLED and strategy_of(s) == STRATEGY_IRON_CONDOR]
    if not rows:
        return ""
    body = "".join(_condor_settled_row(s)
                   for s in sorted(rows, key=lambda r: (r["expiry"], r["id"]), reverse=True))
    return (f'<h3>铁鹰（Iron Condor，已结算）</h3><div class="tw"><table><thead><tr><th>#</th><th>标的</th>'
            f'<th>{CONDOR_LEGS_HEAD}</th><th>到期</th>{CONDOR_TERMS_TH}<th>方式</th><th>结果</th>'
            f'<th>盈亏</th><th>盈亏%</th><th>突破侧</th></tr></thead><tbody>{body}</tbody></table></div>')


def _condor_theory_html(records: list[dict]) -> str:
    if not any(strategy_of(s) == STRATEGY_IRON_CONDOR for s in records):
        return ""
    return (f'<div class="note">iron_condor 理论盈亏平衡胜率（已结算逐笔平均，1 − 净权利金÷宽度）'
            f'{pct(condor_theory_avg(records))}；上表的盈亏平衡胜率按实际盈亏比算，口径不同。</div>')


BULLCALL_TERMS_TH = "".join(f"<th>{h}</th>" for h in BULLCALL_TERMS_HEAD)


def _bullcall_term_cells(s: dict) -> str:
    return "".join(f"<td>{e(c)}</td>" for c in term_cells(s))


def _bullcall_open_row(s: dict, marks: dict) -> str:
    m = marks.get(s["id"])
    pnl, dist, note = _single_mark_cells(m, labels=BULLCALL_THRESHOLD_LABELS)
    usable = None if m is None or "error" in m else m["pnl"]
    return (f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(legs_label(s))}</td>'
            f'<td>{e(s["expiry"])}</td><td>{s["contracts"]}</td>{_bullcall_term_cells(s)}'
            f'<td>{signed_pct(s.get("breakeven_move_pct"))}</td><td>{pnl}</td>'
            f'<td>{pnl_pct(s, usable)}</td><td>{dist}</td><td>{note}</td></tr>')


def _bullcall_open_table(rows: list[dict], marks: dict) -> str:
    """Open bull call debit spreads: own buyer-side terms, no credit-spread payoff card."""
    if not rows:
        return ""
    body = "".join(_bullcall_open_row(s, marks) for s in rows)
    return (f'<h3>{BULLCALL_LABEL}（Bull Call Debit Spread）</h3><div class="tw"><table><thead><tr>'
            f'<th>#</th><th>标的</th><th>{BULLCALL_LEGS_HEAD}</th><th>到期</th><th>张数</th>'
            f'{BULLCALL_TERMS_TH}<th>回本幅度</th><th>浮盈亏</th><th>盈亏%</th><th>距长腿</th>'
            f'<th>提示</th></tr></thead><tbody>{body}</tbody></table></div>')


def _bullcall_settled_row(s: dict) -> str:
    cls = "good" if s["pnl"] > 0 else "bad" if s["pnl"] < 0 else "mut"
    return (f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(legs_label(s))}</td>'
            f'<td>{e(s["expiry"])}</td>{_bullcall_term_cells(s)}<td>{how_label(s)}</td>'
            f'<td>{e(str(s["result"]))}</td><td class="{cls}">{money(s["pnl"])}</td>'
            f'<td>{pnl_pct(s, s["pnl"])}</td><td>{captured_label(s)}</td></tr>')


def _bullcall_settled_table(records: list[dict]) -> str:
    rows = _settled_of(records, STRATEGY_BULL_CALL)
    if not rows:
        return ""
    body = "".join(_bullcall_settled_row(s)
                   for s in sorted(rows, key=lambda r: (r["expiry"], r["id"]), reverse=True))
    return (f'<h3>{BULLCALL_LABEL}（Bull Call Debit Spread，已结算）</h3><div class="tw"><table><thead><tr>'
            f'<th>#</th><th>标的</th><th>{BULLCALL_LEGS_HEAD}</th><th>到期</th>{BULLCALL_TERMS_TH}'
            '<th>方式</th><th>结果</th><th>盈亏</th><th>盈亏%</th><th>捕获比例</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _bullcall_required_html(records: list[dict]) -> str:
    if not any(strategy_of(s) == STRATEGY_BULL_CALL for s in records):
        return ""
    return (f'<div class="note">bull_call_spread 所需胜率（已结算逐笔平均，净支出÷宽度）'
            f'{required_avg_label(records)}，是理论的逐笔平均；'
            '上表的盈亏平衡胜率按实际盈亏比算，口径不同。</div>')


def _lot_float_cell(s: dict, marks: dict) -> str:
    m = marks.get(s["id"])
    if m is None:
        return "运行 status 查看"
    if "error" in m:
        return e(m["error"])
    cls = "good" if m["pnl"] > 0 else "bad" if m["pnl"] < 0 else "mut"
    return f'<b class="{cls}">{money(m["pnl"])}</b>' + ("（报价陈旧）" if m.get("stale") else "")


def _lot_open_table(rows: list[dict], marks: dict) -> str:
    """Open collars / protective puts: own columns, no payoff chart (no credit/short_strike)."""
    body = "".join(
        f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(s["strategy"])}</td>'
        f'<td>{e(legs_label(s))}</td><td>{e(s["expiry"])}</td><td>{s["contracts"]}</td>'
        f'<td>{e(lot_put_net_cost(s))}</td><td>{e(lot_put_range(s))}</td>'
        f'<td>{e(lot_put_cap_cost(s))}</td><td>{_lot_float_cell(s, marks)}</td></tr>'
        for s in rows)
    return ('<h3>保护性 Put 与 Collar</h3><div class="tw"><table><thead><tr><th>#</th><th>标的</th>'
            '<th>策略</th><th>腿</th><th>到期</th><th>张数</th><th>净成本</th><th>区间</th>'
            '<th>上限代价</th><th>浮盈亏</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div><div class="note">{e(LOT_PUT_NOTE.lstrip("> "))}</div>')


def _lot_settled_table(spreads: list[dict]) -> str:
    rows = [s for s in spreads if s["status"] in SETTLED and strategy_of(s) in LOT_PUT_STRATEGIES]
    if not rows:
        return ""
    body = ""
    for s in sorted(rows, key=lambda r: (r["expiry"], r["id"]), reverse=True):
        stock = s.get("stock_leg_pnl")
        body += (f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(s["strategy"])}</td>'
                 f'<td>{e(legs_label(s))}</td><td>{e(s["expiry"])}</td><td>{e(result_text(s))}</td>'
                 f'<td>{money(s["pnl"])}</td><td>{"—" if stock is None else money(stock)}</td>'
                 f'<td>{"—" if stock is None else money(s["pnl"] + stock)}</td>'
                 f'<td>{e(lot_put_net_cost(s))}</td></tr>')
    return ('<h3>保护性 Put 与 Collar（已结算）</h3><div class="tw"><table><thead><tr><th>#</th><th>标的</th>'
            '<th>策略</th><th>腿</th><th>到期</th><th>结果</th><th>期权腿盈亏</th><th>股票腿</th>'
            '<th>合计（参考）</th><th>净成本</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _strategy_table(records: list[dict]) -> str:
    stats = compute_strategy_stats(records)
    if all(k == STRATEGY_BULL_PUT for k in stats):
        return ""
    body = "".join(
        f'<tr><td>{e(k)}</td><td>{st["n_settled"]}</td><td>{pct(st["win_rate"])}</td>'
        f'<td>{money(st["total_pnl"])}</td><td>{pct(st["breakeven_win_rate"])}</td>'
        f'<td>{"—" if st["passes"] is None else ("✅" if st["passes"] else "❌")}</td></tr>'
        for k, st in stats.items())
    warn = "".join(f'<div class="note">{e(k)} 样本不足（{st["n_settled"]} &lt; {MIN_SAMPLE}），别下结论。</div>'
                   for k, st in stats.items() if not st["enough_sample"])
    return ('<h3>按策略对比</h3><div class="tw"><table><thead><tr><th>策略</th><th>已结算</th><th>胜率</th>'
            '<th>总盈亏</th><th>盈亏平衡胜率</th><th>是否过线</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>{_condor_theory_html(records)}'
            f'{_bullcall_required_html(records)}{warn}')


def _settled(spreads: list[dict], quiet: bool = False) -> str:
    """quiet: another settled table follows, so an empty-state line would contradict it."""
    rows = [s for s in spreads if s["status"] in SETTLED and strategy_of(s) in SINGLE_SPREAD_STATS]
    if not rows:
        return "" if quiet else '<p class="mut">还没有已结算的。</p>'
    body = "".join(
        f'<tr><td>{s["id"]}</td><td>{e(s["ticker"])}</td><td>{e(legs_label(s).replace("/", " / "))}</td>'
        f'<td>{e(s["expiry"])}</td><td>{_bucket_cell(s)}</td>'
        f'<td>{"提前平仓" if s["status"] == "closed_early" else "到期结算"}</td><td>{e(result_text(s))}</td>'
        f'<td class="{"good" if s["pnl"] > 0 else "bad" if s["pnl"] < 0 else "mut"}">{money(s["pnl"])}</td></tr>'
        for s in sorted(rows, key=lambda r: (r["expiry"], r["id"]), reverse=True))
    return ('<div class="tw"><table><thead><tr><th>#</th><th>标的</th><th>卖 / 买</th><th>到期</th><th>档位</th>'
            f'<th>方式</th><th>结果</th><th>盈亏</th></tr></thead><tbody>{body}</tbody></table></div>')


def _stats(st: dict) -> str:
    if st["n_settled"] == 0:
        return '<p class="mut">暂无已结算的交易，统计从第一笔结算后开始。</p>'
    if st["passes"] is None:
        verdict = "暂无亏损（或盈利）样本，无法和盈亏平衡胜率比较"
    else:
        verdict = ('<b class="good">✅ 实际胜率高于盈亏平衡胜率</b>' if st["passes"]
                   else '<b class="bad">❌ 实际胜率低于盈亏平衡胜率</b>')
    buckets = "".join(f'<tr><td>{b:.2f}</td><td>{g["n"]}</td><td>{pct(g["win_rate"])}</td><td>{money(g["total_pnl"])}</td></tr>'
                      for b, g in st["by_bucket"].items())
    warn = (f'<div class="note">样本不足（{st["n_settled"]} &lt; {MIN_SAMPLE}），别下结论。</div>'
            if not st["enough_sample"] else "")
    return ('<div class="kpis">'
            f'<div class="kpi"><span>胜率（盈亏&gt;0 算赢）</span><b>{pct(st["win_rate"])}</b></div>'
            f'<div class="kpi"><span>盈亏平衡胜率（按实际盈亏比）</span><b>{pct(st["breakeven_win_rate"])}</b></div>'
            f'<div class="kpi"><span>平均盈利 / 亏损</span><b>{"—" if st["avg_win"] is None else money(st["avg_win"])} / '
            f'{"—" if st["avg_loss"] is None else money(-st["avg_loss"])}</b></div></div>'
            f'<p>{verdict}</p>{warn}'
            '<h3>分档对比</h3><div class="tw"><table><thead><tr><th>档位（delta）</th><th>笔数</th><th>胜率</th>'
            f'<th>总盈亏</th></tr></thead><tbody>{buckets}</tbody></table></div>')


def _settled_of(records: list[dict], strategy: str) -> list[dict]:
    return [s for s in records if s["status"] in SETTLED and strategy_of(s) == strategy]


def _open_cards(open_: list[dict], marks: dict, today: str) -> str:
    """Every open record's card or table row, routed per strategy; only a bull put reaches the
    payoff card, which reads credit / short_strike."""
    def of(*names: str) -> list[dict]:
        return [s for s in open_ if strategy_of(s) in names]
    lots = of(*LOT_PUT_STRATEGIES)
    return ("".join(_open_card(s, marks.get(s["id"]), today) for s in of(STRATEGY_BULL_PUT))
            + _csp_open_table(of(STRATEGY_CSP), marks) + _cc_open_table(of(STRATEGY_COVERED_CALL), marks)
            + (_lot_open_table(lots, marks) if lots else "")
            + _condor_open_table(of(STRATEGY_IRON_CONDOR), marks)
            + _bullcall_open_table(of(STRATEGY_BULL_CALL), marks))


def render_report_html(spreads: list[dict], marks: dict | None, generated_on: str) -> str:
    marks = marks or {}
    spread_only = [s for s in spreads if strategy_of(s) == STRATEGY_BULL_PUT]
    st = compute_stats(spread_only)
    # The open KPI counts only what the list below can render, so the two always agree.
    open_ = [s for s in spreads if s["status"] == "open" and strategy_of(s) in KNOWN_STRATEGIES]
    cards = _open_cards(open_, marks, generated_on) or '<p class="mut">还没有未平仓。</p>'
    lot_note = ("保护性 Put / Collar：占用资金 = 净支出（净收入记 0），盈亏% = 期权腿盈亏 ÷（成本 × 股数）。"
                if any(strategy_of(s) in LOT_PUT_STRATEGIES for s in spreads) else "")
    settled_condors = _settled_of(spreads, STRATEGY_IRON_CONDOR)
    settled_bullcalls = _settled_of(spreads, STRATEGY_BULL_CALL)
    condor_note = (CONDOR_FOOTER if any(strategy_of(s) == STRATEGY_IRON_CONDOR for s in spreads)
                   else "")
    if any(strategy_of(s) == STRATEGY_BULL_CALL for s in spreads):
        condor_note += BULLCALL_FOOTER
    gap = "—" if st["avg_abs_gap"] is None else f'{st["avg_abs_gap"]:.2f}%'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>垂直 Put 贷方价差与现金担保 Put 纸上交易 {e(generated_on)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>垂直 Put 贷方价差与现金担保 Put 纸上交易</h1>
<p class="sub">Bull Put Credit Spread · 生成于 {e(generated_on)} · 由 /spread-sim status 自动生成，只读</p>
{_summary(open_, marks, st, settled_condors, settled_bullcalls)}
<h2>未平仓</h2>
{cards}
<h2>已结算</h2>
{_settled(spreads, bool(settled_condors or settled_bullcalls))}{_lot_settled_table(spreads)}{_condor_settled_table(spreads)}{_bullcall_settled_table(spreads)}
<h2>累积统计</h2>
{_stats(st)}{_strategy_table(spreads)}
<h2>开仓价假设的偏差</h2>
<p>下一个交易日开盘相对快照价的平均绝对跳空：<b>{gap}</b>。真实世界无法在快照价成交，这个数字越大，模拟结果越乐观。</p>
<div class="foot">纸上交易，不含真实持仓。{lot_note}{condor_note}每张合约 ×100 美元，中间价成交，不含手续费；占用资金 = 最大亏损（券商冻结宽度×100，扣掉收到的权利金）。休市时的浮盈亏用最后成交价，只是估算。非投资建议。</div>
</div></body></html>"""
