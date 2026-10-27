#!/usr/bin/env python3
"""Plain-language explanation of a saved `suggest` result (pure rendering, no I/O).

Everything is derived from the candidate numbers, so the text is reproducible.
See docs/superpowers/specs/2026-10-14-spread-sim-design.md.
"""
from __future__ import annotations
from datetime import date

import option_labels

EARNINGS_TEXT = {"yes": "到期前有财报 ⚠️", "no": "到期前无财报", "unknown": "财报日期未知 ⚠️"}
OPEN_CMD = "./.venv/Scripts/python.exe scripts/spread_sim.py open"


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


def snapshot_to_json(snap: dict, generated_on: str) -> dict:
    """Machine-readable twin of the markdown file; `open` reads this one."""
    return {
        "version": 1,
        "generated_on": generated_on,
        **option_labels.labelled_fields(),  # {} in IBKR
        "ticker": snap["ticker"],
        "snapshot_date": _iso(snap["snapshot_date"]),
        "spot": snap["spot"],
        "expiry": snap["expiry"],
        "days": snap["days"],
        "earnings": snap["earnings"],
        "width": snap["width"],
        "intraday": bool(snap.get("intraday", False)),
        "width_info": snap.get("width_info"),
        "candidates": snap["candidates"],
    }


def _info_section(snap: dict) -> list[str]:
    stale = any(c["available"] and c["stale"] for c in snap["candidates"])
    intraday = snap.get("intraday", False)
    quality = ("报价陈旧：休市时 bid/ask 为空，用了最后成交价，权利金只是估算"
               if stale else "用 bid/ask 中间价")
    if intraday:
        quality += "；**盘中快照**：价格和报价是此刻的实时数据，收盘后会变"
    cycle = "" if snap["days"] == 7 else "，非整 7 天：该标的没有恰好 7 天后的到期日，取之后最近的一个"
    return [
        "## 基本信息", "",
        "| 项目 | 内容 |", "|------|------|",
        f"| 快照 | {_iso(snap['snapshot_date'])} {'盘中' if intraday else '收盘'} ${snap['spot']:,.2f} |",
        f"| 到期日 | {snap['expiry']}（{snap['days']} 天{cycle}）|",
        f"| 价差宽度 | {_width_tag(snap)} |",
        f"| 财报 | {EARNINGS_TEXT[snap['earnings']]} |",
        f"| 数据质量 | {quality} |", "",
    ]


def _width_tag(snap: dict) -> str:
    info = snap.get("width_info") or {}
    src, rec = info.get("source"), info.get("recommended")
    w = snap["width"]
    if src == "auto":
        return f"${w:g}（自动推荐）"
    if src == "manual":
        return f"${w:g}（你指定，推荐 ${rec:g}）" if rec else f"${w:g}（你指定）"
    if src == "fallback":
        return f"${w:g}（默认，无法估算推荐）"
    return f"${w:g}"


def _width_section(snap: dict) -> list[str]:
    info = snap.get("width_info")
    if not info:
        return []
    w, days = snap["width"], snap["days"]
    src, rec = info.get("source"), info.get("recommended")
    working = ""
    if rec:
        working = (f"{days} 天预期波动 ±${info['expected_move']:.2f}（现价 × 平值隐含波动率 "
                   f"{info['atm_iv'] * 100:.0f}% × √({days}/365)）的 {info['fraction']:.0%} ≈ ${info['raw']:.2f}，"
                   f"取整到行权价间距 ${info['step']:g} 的整数倍 → ${rec:g}")
        if info.get("capped"):
            working += "（已按现价的 5% 上限收窄）"
    if src == "auto":
        head = f"- **自动推荐 ${w:g}**：{working}。"
    elif src == "manual" and rec:
        head = f"- **你指定的宽度 ${w:g}**；系统推荐 ${rec:g}（{working}）。"
    elif src == "manual":
        head = f"- **你指定的宽度 ${w:g}**；无法估算推荐宽度（行权价或隐含波动率数据不足）。"
    else:
        head = f"- 无法估算推荐宽度（行权价或隐含波动率数据不足），使用默认 ${w:g}。"
    return [
        "## 为什么是这个宽度", "", head,
        "- **宽度基本不改变赢面**：短腿位置由 delta 决定，到期被突破的概率不变；宽度越大，权利金占宽度的比例会略降（盈亏平衡胜率略升），主要改变的是每张的金额和摩擦成本占比。",
        "- 窄价差每张只赚几十美元，买卖价差和手续费（摩擦）占比大；宽价差每张最大亏损大，要少开几张让单笔风险不变。",
        "- 推荐宽度按预期波动的固定比例定，让不同价位、不同波动的标的结构可比。", "",
    ]


def _overview_section(snap: dict) -> list[str]:
    spot = snap["spot"]
    lines = ["## 候选一览", "",
             "| 档位 | 短腿/长腿 | 权利金 | 最大盈/亏 | 距现价 | 权利金/宽度 | 盈亏平衡胜率 |",
             "|------|-----------|--------|-----------|--------|-------------|--------------|"]
    for c in snap["candidates"]:
        if not c["available"]:
            lines.append(f"| {c['bucket']:.2f} | — | — | — | — | — | 无合适合约 |")
            continue
        below = (spot - c["short_strike"]) / spot * 100
        lines.append(
            f"| {c['bucket']:.2f} | {c['short_strike']:g}/{c['long_strike']:g} | ${c['credit']:.2f} "
            f"| +${c['max_profit']:,.0f}/-${c['max_loss']:,.0f} | -{below:.1f}% "
            f"| {c['credit_ratio'] * 100:.1f}% | {c['breakeven_win_rate'] * 100:.0f}% |")
    return lines + [""]


def _bucket_section(c: dict, spot: float, days: int) -> list[str]:
    if not c["available"]:
        return [f"### 档位 {c['bucket']:.2f} — 无合适合约", "", f"原因：{c['reason']}。", ""]
    short, long_ = c["short_strike"], c["long_strike"]
    below = (spot - short) / spot * 100
    em_pct = c["expected_move"] / spot * 100
    be = c["breakeven_win_rate"]
    lines = [
        f"### 档位 {c['bucket']:.2f}：卖 {short:g} Put / 买 {long_:g} Put", "",
        f"- **为什么选这个短腿**：目标 delta {c['bucket']:.2f}，期权链里最接近的是 {short:g}"
        f"（实际 delta {c['short_delta']:.2f}，隐含波动率 {c['short_iv'] * 100:.0f}%）。"
        f"delta 可以粗略理解为到期时跌到这个价位以下的概率，约 {c['short_delta'] * 100:.0f}%"
        "（风险中性估计，不是预测）。",
        f"- **离现价多远**：短腿比现价 ${spot:,.2f} 低 {below:.1f}%，约 {c['em_multiple']:.2f} 倍到期前预期波动"
        f"（{days} 天，±${c['expected_move']:.2f}，±{em_pct:.1f}%）。倍数越大越安全，权利金也越薄。",
        f"- **长腿的作用**：再买 {long_:g} Put 做保护，宽度 ${c['width']:g}，把最大亏损封顶。",
        f"- **收益与风险**（每张合约）：收到权利金 ${c['credit']:.2f}/股，最多赚 +${c['max_profit']:,.0f}，"
        f"最多亏 -${c['max_loss']:,.0f}；盈亏平衡价 ${c['breakeven']:.2f}；权利金占宽度 {c['credit_ratio'] * 100:.1f}%。",
        f"- **做一张需要资金**：约 ${c['max_loss']:,.0f}。券商冻结 ${c.get('margin', c['width'] * 100):,.0f}"
        f"（宽度 × 100），收到权利金 ${c['max_profit']:,.0f} 后净占用 ${c['max_loss']:,.0f}，即最坏情况下亏掉的钱（未含手续费，各券商略有差异）。",
        f"- **盈亏平衡胜率 {be:.0%}**：持有到期、不止损的话，每 100 笔里要赢约 {be * 100:.0f} 笔才不亏钱。"
        "你的真实胜率要高于这个数才有意义。",
        f"- **什么时候赢**：到期收盘价 ≥ {short:g}：保留全部权利金；"
        f"介于 {c['breakeven']:.2f} 和 {short:g} 之间：仍然盈利，但赚得比全额少；"
        f"介于 {long_:g} 和 {c['breakeven']:.2f} 之间：亏损；≤ {long_:g}：亏满最大亏损。",
    ]
    if c["warnings"]:
        lines.append("- **需要注意**：")
        lines += [f"  - {w}" for w in c["warnings"]]
    return lines + [""]


def _tradeoff_section() -> list[str]:
    return [
        "## 三档怎么权衡", "",
        "- **delta 越小**：短腿离现价越远，被打穿的概率越低，但权利金越薄，盈亏平衡胜率越高（越难覆盖）。",
        "- **delta 越大**：权利金越厚，但被打穿的概率也越高，一次亏损要吃掉好几次盈利。",
        "- 三档并排做，累积一段时间后看**哪一档的真实胜率更能超过自己的盈亏平衡胜率**，这是模拟的目的。",
        "- 同一个标的同一到期日只能开一档。", "",
    ]


def _open_section(snap: dict) -> list[str]:
    avail = [c for c in snap["candidates"] if c["available"]]
    lines = ["## 怎么开仓", "",
             "这些只是候选，**未写入账本**。确认后运行下面的命令之一，记录的就是你刚读过的这份候选，"
             "不会重新取价："]
    if not avail:
        return lines[:2] + ["没有可用档位，请换个标的，或周末数据完整后重新运行 suggest。", ""]
    lines += ["", "```bash"]
    lines += [f"{OPEN_CMD} {snap['ticker']} {c['bucket']:.2f}" for c in avail]
    lines += ["```", "", "要开多张合约，加 `--contracts N`。", ""]
    return lines


def render_suggestion_md(snap: dict, notes: tuple[str, ...] = ()) -> str:
    """The file the user reads before deciding which bucket (if any) to open.

    `notes` (closed-market hint, stale drops) follow the data-quality banner; spread-sim has no
    summary file, so this explanation is where they are written."""
    head = [
        f"# {snap['ticker']} 价差候选 · 快照 {_iso(snap['snapshot_date'])}", "",
        "*由 `/spread-sim suggest` 生成。纸上交易，未写入账本；确认后用 `open` 记录。非投资建议。*", "",
    ]
    detail = ["## 逐档讲解", ""]
    for c in snap["candidates"]:
        detail += _bucket_section(c, snap["spot"], snap["days"])
    return option_labels.with_banner("\n".join(
        head + _info_section(snap) + _width_section(snap) + _overview_section(snap) + detail
        + _tradeoff_section() + _open_section(snap)), notes)
