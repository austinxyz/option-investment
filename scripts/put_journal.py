#!/usr/bin/env python3
"""Put journal: did the weekly put-review advice turn out right? (read-only on the ledger)

    put_journal.py                      # writes journal.md / journal.json
    put_journal.py note ID "text" [--kind reason|mindset|exit|other]

Pairs settled ledger records with the weekly review snapshots in reviews/.
Only journal.md, journal.json and journal-notes.json are ever written.
See docs/superpowers/specs/2026-10-19-put-journal-design.md.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import option_labels
import option_settings
import put_journal_stats as stats
import put_journal_whatif as whatif
import spread_ledger as ledger_mod
import spread_sim
from put_review import ADVICE_LABEL, REVIEWS_DIR
from spread_report import _money, _pct, legs_label

NOTES_FILE = "journal-notes.json"
NOTE_KINDS = ("reason", "mindset", "exit", "other")
NOTE_KIND_LABEL = {"reason": "理由", "mindset": "心态", "exit": "退出", "other": "其他"}
NO_SETTLED = "还没有已结算的持仓"
NO_SETTLED_EXCEPT_CONDOR = "除铁鹰外暂无已结算持仓（铁鹰见下方铁鹰块）"
CONDOR_NOTE_HIDDEN = "提示：铁鹰的笔记只存进笔记文件，复盘报告里不显示"
NO_SETTLED_EXCEPT_BULLCALL = (f"除 {ledger_mod.BULLCALL_LABEL}外暂无已结算持仓"
                              f"（{ledger_mod.BULLCALL_LABEL}见下方其块）")
NO_SETTLED_EXCEPT_OWN_BLOCKS = (f"除铁鹰与 {ledger_mod.BULLCALL_LABEL}外暂无已结算持仓"
                                "（各自见下方的块）")
BULLCALL_NOTE_HIDDEN = f"提示：{ledger_mod.BULLCALL_LABEL}的笔记只存进笔记文件，复盘报告里不显示"
# Strategies whose notes never reach a timeline (they have their own block instead).
NOTE_HIDDEN = {ledger_mod.STRATEGY_IRON_CONDOR: CONDOR_NOTE_HIDDEN,
               ledger_mod.STRATEGY_BULL_CALL: BULLCALL_NOTE_HIDDEN}
# Allowlist, not "everything but covered calls": a new strategy must opt in to A-D explicitly.
PUT_STATS_STRATEGIES = (ledger_mod.STRATEGY_BULL_PUT, ledger_mod.STRATEGY_CSP)
CONDOR_BREACHED_LABEL = {**ledger_mod.BREACHED_SIDE_LONG_LABELS, "unrecorded": "未记录"}
DASH = "—"


class NotesError(Exception):
    """The notes file is damaged or a note is not acceptable."""


# --------------------------------------------------------------------------- I/O

def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dump_json(doc: dict) -> str:
    """Strict JSON: a non-finite float is a bug and raises ValueError."""
    return json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False, default=float) + "\n"


def _snapshot_problem(doc: object) -> str | None:
    if not isinstance(doc, dict):
        return "不是 JSON 对象"
    if not isinstance(doc.get("generated_on"), str):
        return "缺少 generated_on"
    if not isinstance(doc.get("positions"), list):
        return "缺少 positions 列表"
    return None


def load_snapshots(reviews_dir: Path) -> tuple[list[dict], list[str]]:
    """Read reviews/*.json by name; bad files are skipped with a Chinese warning."""
    snapshots: list[dict] = []
    warnings: list[str] = []
    if not reviews_dir.is_dir():
        return snapshots, warnings
    for path in sorted(reviews_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"快照 {path.name} 无法读取，已跳过（{exc}）")
            continue
        version = doc.get("version") if isinstance(doc, dict) else None
        if version is not None and version != 1:
            warnings.append(f"快照 {path.name} 的版本 {version} 不是 1，已跳过")
            continue
        problem = _snapshot_problem(doc)
        if problem:
            warnings.append(f"快照 {path.name} 结构不对（{problem}），已跳过")
            continue
        snapshots.append(doc)
    return snapshots, warnings


def load_notes(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "notes": {}}
    hint = f"请人工检查 {path} 后再试，本命令不会自动覆盖。"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NotesError(f"笔记文件 {path} 无法读取（{exc}）。{hint}") from exc
    notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(notes, dict) or not all(isinstance(v, list) for v in notes.values()):
        raise NotesError(f"笔记文件 {path} 结构不对。{hint}")
    return {"version": 1, "notes": notes}


def add_note(path: Path, position_id: int, text: str, kind: str, today: date,
             known_ids: set[int]) -> None:
    if kind not in NOTE_KINDS:
        raise NotesError(f"笔记类型必须是 {'/'.join(NOTE_KINDS)} 之一，收到 {kind!r}")
    if position_id not in known_ids:
        raise NotesError(f"账本里没有 id={position_id} 的持仓")
    text = text.strip()
    if not text:
        raise NotesError("笔记内容不能为空")
    data = load_notes(path)
    entry = {"date": today.isoformat(), "kind": kind, "text": text}
    notes = {k: list(v) for k, v in data["notes"].items()}
    notes.setdefault(str(position_id), []).append(entry)
    _write_atomic(path, dump_json({"version": 1, "notes": notes}))


# ------------------------------------------------------------------------ report

def _row_summary(row: dict) -> dict:
    return {k: row.get(k) for k in
            ("date", "advice", "rule", "captured", "delta", "reason", "data_quality")}


def _timeline(case: dict, record: dict, notes: dict) -> dict:
    return {
        "id": case["id"], "strategy": case["strategy"], "ticker": case["ticker"],
        "strikes": legs_label(record), "expiry": case["expiry"],
        "status": case["status"], "pnl": case["pnl"], "result": case["result"],
        "exit_date": case["exit_date"], "consistency": stats.consistency(case),
        "rows": [_row_summary(r) for r in case["rows"]],
        "notes": list(notes.get("notes", {}).get(str(case["id"]), [])),
    }


def _open_summary(record: dict, snapshots: list[dict]) -> dict:
    rows = []
    for snap in snapshots:
        for pos in snap["positions"]:
            if pos.get("id") == record["id"] and pos.get("data_quality") != "none":
                rows.append({**pos, "date": snap["generated_on"]})
    rows.sort(key=lambda r: r["date"])
    last = rows[-1] if rows else None
    return {
        "id": record["id"], "ticker": record["ticker"], "strikes": legs_label(record),
        "expiry": record["expiry"], "advice_rows": len(rows),
        "last_advice": last["advice"] if last else None,
        "last_advice_date": last["date"] if last else None,
    }


def covered_call_stats(cases: list[dict]) -> dict:
    """Covered calls are judged by option-leg pnl only, apart from the put numbers."""
    pnls = [c["pnl"] or 0.0 for c in cases]
    wins = sum(1 for p in pnls if p > 0)
    return {"n": len(cases), "wins": wins,
            "win_rate": wins / len(cases) if cases else None,
            "total_pnl": round(sum(pnls), 2)}


def build_report(records: list[dict], snapshots: list[dict], notes: dict,
                 generated_on: str, warnings: list[str] | None = None) -> dict:
    all_cases = stats.build_cases(records, snapshots)
    cases = [c for c in all_cases if c["strategy"] in PUT_STATS_STRATEGIES]
    calls = [c for c in all_cases if c["strategy"] == ledger_mod.STRATEGY_COVERED_CALL]
    by_id = {r["id"]: r for r in records}
    comparable = [c for c in all_cases if c["comparable"]]
    dates = sorted(s["generated_on"] for s in snapshots)
    report = {
        "generated_on": generated_on,
        "counts": {
            "settled": len(all_cases), "comparable": len(comparable),
            "open": sum(1 for r in records if r.get("status") == "open"),
            "snapshots": len(snapshots),
            "snapshot_dates": [dates[0], dates[-1]] if dates else [None, None],
        },
        "close_advice": stats.close_advice_stats(cases),
        "attention": stats.attention_stats(cases),
        "hold": stats.hold_stats(cases),
        "streak": stats.streak_stats(cases),
        "sweep_close": whatif.sweep_close(cases),
        "sweep_delta": whatif.sweep_delta(cases),
        "timelines": [_timeline(c, by_id[c["id"]], notes) for c in all_cases],
        "open_positions": [_open_summary(r, snapshots) for r in records
                           if r.get("status") == "open"],
        "warnings": list(warnings or []),
    }
    if calls:
        report["covered_call"] = covered_call_stats(calls)
    lot_put = stats.lot_put_stats(records)
    if lot_put:
        report["lot_put"] = lot_put
    condor = stats.condor_stats(records)
    if condor:
        report["iron_condor"] = condor
    bullcall = stats.bullcall_stats(records)
    if bullcall:
        report[ledger_mod.STRATEGY_BULL_CALL] = bullcall
    return report


# --------------------------------------------------------------------- rendering

def _num(v: float | None, fmt: str) -> str:
    return DASH if v is None else format(v, fmt)


def _money_or_dash(v: float | None) -> str:
    return DASH if v is None else _money(v)


def _sample(n: int, label: str) -> list[str]:
    lines = [f"样本：{label} {n} 笔"]
    warning = stats.sample_warning(n)
    if warning:
        lines.append(f"警告：{warning}")
    return lines


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    cell = lambda c: str(c).replace("|", "｜")  # noqa: E731
    return (["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            + ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows])


def _block_settled(report: dict, key: str) -> int:
    return (report.get(key) or {}).get("n", 0)


def _own_block_count_lines(report: dict) -> list[str]:
    """Own-block strategies sit outside counts.settled; one line each instead of hiding them."""
    lines = []
    condor = _block_settled(report, ledger_mod.STRATEGY_IRON_CONDOR)
    if condor:
        lines.append(f"另有铁鹰已结算 {condor} 笔，见铁鹰块")
    bullcall = _block_settled(report, ledger_mod.STRATEGY_BULL_CALL)
    if bullcall:
        lines.append(f"另有 {ledger_mod.BULLCALL_LABEL}已结算 {bullcall} 笔，见其块")
    return lines


def _no_settled(report: dict) -> str:
    condor = _block_settled(report, ledger_mod.STRATEGY_IRON_CONDOR)
    bullcall = _block_settled(report, ledger_mod.STRATEGY_BULL_CALL)
    if condor and bullcall:
        return NO_SETTLED_EXCEPT_OWN_BLOCKS
    if bullcall:
        return NO_SETTLED_EXCEPT_BULLCALL
    return NO_SETTLED_EXCEPT_CONDOR if condor else NO_SETTLED


def _render_header(report: dict) -> list[str]:
    c = report["counts"]
    first, last = c["snapshot_dates"]
    span = f"{first} ～ {last}" if first else DASH
    lines = [f"# 期权建议复盘 {report['generated_on']}", "",
             f"- 已结算 {c['settled']} 笔，其中有建议可比 {c['comparable']} 笔，"
             "其余没有建议可比",
             *(f"- {line}" for line in _own_block_count_lines(report)),
             f"- 进行中 {c['open']} 笔",
             f"- 快照 {c['snapshots']} 份，日期范围 {span}"]
    if report["warnings"]:
        lines += ["", "## 提示"] + [f"- {w}" for w in report["warnings"]]
    return lines


def _render_close_advice(a: dict, m: int) -> list[str]:
    lines = ["### A. 平仓建议值不值得听", ""]
    lines += _sample(m, "可比持仓") + [f"其中出现过“平仓”建议 {a['n_close']} 笔"]
    lines += [f"平均差额：{_money_or_dash(a['mean_diff'])}，总差额：{_money(a['total_diff'])}，"
              f"听建议更好 {a['better']} 笔（差额为正 = 听建议更好）"]
    if a["stale_rows"]:
        lines.append(f"其中 {a['stale_rows']} 行基于陈旧报价")
    if a["items"]:
        rows = [[f"#{i['id']}", i["ticker"], i["date"], _money(i["pnl_if"]),
                 _money(i["actual"]), _money(i["diff"]),
                 "陈旧报价" if i["stale"] else ""] for i in a["items"]]
        lines += [""] + _table(["id", "标的", "建议日", "当时平仓", "实际", "差额", "备注"], rows)
    return lines


def _render_attention(b: dict, m: int) -> list[str]:
    lines = ["### B. “需要关注”准不准", ""]
    lines += _sample(m, "可比持仓")
    lines += [f"被标过关注 {b['n_flagged']} 笔，出事（亏损）{b['n_trouble']} 笔，"
              f"两者重合 {b['flagged_and_trouble']} 笔",
              f"精确率：{_pct(b['precision'])}", f"召回率：{_pct(b['recall'])}"]
    if b["by_rule"]:
        rows = [[f"规则 {rule}", str(v["flagged"]), str(v["trouble"]), _pct(v["precision"])]
                for rule, v in b["by_rule"].items()]
        lines += [""] + _table(["首次命中规则", "被标关注", "出事", "精确率"], rows)
    return lines


def _render_hold(h: dict, m: int) -> list[str]:
    lines = ["### C. “继续持有”后来有没有事", ""] + _sample(m, "可比持仓")
    lines.append(f"最后一条建议是“继续持有”的 {h['n']} 笔，其中赢 {h['wins']} 笔，"
                 f"赢率：{_pct(h['rate'])}")
    return lines


def _render_streak(d: dict) -> list[str]:
    lines = ["### D. 连赢之后", ""] + _sample(d["n_settled"], "已结算持仓")
    lines.append(f"前面连赢 3 笔之后的下一笔：{d['n']} 笔，赢 {d['wins']} 笔，"
                 f"胜率：{_pct(d['rate'])}；全体胜率（对照）：{_pct(d['baseline_rate'])}")
    lines.append("同日结算按 id 排序，仅供参考")
    return lines


def _render_stats(report: dict) -> list[str]:
    lines = ["## 建议准不准", ""]
    if report["counts"]["settled"] == 0:
        return lines + [_no_settled(report)]
    m = report["close_advice"]["n_comparable"]
    blocks = [_render_close_advice(report["close_advice"], m),
              _render_attention(report["attention"], m),
              _render_hold(report["hold"], m), _render_streak(report["streak"])]
    for block in blocks:
        lines += block + [""]
    return lines[:-1]


def _render_sweeps(report: dict) -> list[str]:
    lines = ["## 阈值重算", ""]
    if report["counts"]["settled"] == 0:
        return lines + [_no_settled(report)]
    m = report["close_advice"]["n_comparable"]
    lines += ["### 平仓阈值 CLOSE_CAPTURED（0.80 为当前）", ""] + _sample(m, "可比持仓")
    rows = [[f"{s['value']:.2f}", str(s["n_triggered"]), _money(s["total_followed"]),
             _money(s["total_actual"]), _money(s["diff"])] for s in report["sweep_close"]]
    lines += [""] + _table(["阈值", "触发平仓笔数", "按规则平仓总盈亏", "按实际总盈亏", "差额"], rows)
    lines += ["", "### 关注阈值 ATTENTION_DELTA（0.35 为当前）", ""] + _sample(m, "可比持仓")
    rows = [[f"{s['value']:.2f}", str(s["n_flagged"]), str(s["n_trouble"]),
             _pct(s["precision"]), _pct(s["recall"])] for s in report["sweep_delta"]]
    return lines + [""] + _table(["阈值", "被标关注", "出事", "精确率", "召回率"], rows)


def _render_covered_call(report: dict) -> list[str]:
    c = report.get("covered_call")
    if not c:
        return []
    lines = ["## 备兑看涨（单独统计，不计入上面的 put 数字）", ""] + _sample(c["n"], "已结算备兑看涨")
    lines.append(f"赢 {c['wins']} 笔（按期权腿盈亏），胜率：{_pct(c['win_rate'])}，"
                 f"期权腿总盈亏：{_money(c['total_pnl'])}")
    return lines


def _render_lot_put(report: dict) -> list[str]:
    b = report.get("lot_put")
    if not b:
        return []
    lines = ["## 保护 / 领口（单独统计，不计入上面任何数字，也没有胜率）", ""]
    lines += _sample(b["n"], "已结算领口 / 保护性 Put")
    lines.append("结果：" + "，".join(f"{k} {n} 笔" for k, n in b["by_result"].items()))
    lines.append(f"期权腿总盈亏：{_money(b['total_pnl'])}（不含股票腿）")
    cost = b["total_net_cost"]
    lines.append(f"总净成本：{_money(cost)}（正数 = 净付出的现金，负数 = 净收到的权利金；"
                 "按 -净权利金 × 100 × 张数 汇总）")
    return lines


def _render_condor(report: dict) -> list[str]:
    b = report.get("iron_condor")
    if not b:
        return []
    lines = ["## 铁鹰（单独统计，不计入上面的 A–D 与阈值重算）", ""]
    lines += _sample(b["n"], "已结算铁鹰")
    lines.append(f"赢 {b['wins']} 笔，胜率：{_pct(b['win_rate'])}，总盈亏：{_money(b['total_pnl'])}")
    lines.append(f"理论盈亏平衡胜率（逐笔平均，1 − 净权利金 ÷ 宽度）：{_pct(b['theory_breakeven_avg'])}"
                 "；与按实际盈亏比算的盈亏平衡胜率口径不同")
    lines.append("突破情况：" + "，".join(f"{CONDOR_BREACHED_LABEL[k]} {n} 笔"
                                      for k, n in b["breached"].items()))
    return lines


def _pct1(x: float | None) -> str:
    """Unsigned one-decimal percent; rounding first so it never prints '-0.0%'."""
    return DASH if x is None else f"{round(x * 100, 1) + 0.0:.1f}%"


def _render_bullcall(report: dict) -> list[str]:
    b = report.get(ledger_mod.STRATEGY_BULL_CALL)
    if not b:
        return []
    label = ledger_mod.BULLCALL_LABEL
    lines = [f"## {label}（单独统计，不计入上面的 A–D 与阈值重算）", ""]
    lines += _sample(b["n"], f"已结算 {label}")
    lines.append(f"赢 {b['wins']} 笔，胜率：{_pct(b['win_rate'])}，总盈亏：{_money(b['total_pnl'])}")
    lines.append(f"所需胜率（逐笔平均，净支出 ÷ 宽度）：{_pct(b['required_avg'])}"
                 "；与按实际盈亏比算的盈亏平衡胜率口径不同")
    lines.append(f"赢的笔平均捕获最大收益的 {_pct1(b['captured_avg_winners'])}")
    return lines


def _render_timeline(t: dict) -> list[str]:
    result = t["result"] or DASH
    lines = [f"### #{t['id']} {t['strategy']} {t['ticker']} {t['strikes']} "
             f"到期 {t['expiry']} {t['status']} 盈亏 {_money_or_dash(t['pnl'])} 结果 {result}",
             f"与建议：{t['consistency']}"]
    if t["rows"]:
        rows = [[r["date"], ADVICE_LABEL.get(r["advice"], str(r["advice"])), str(r["rule"]),
                 _num(r["captured"], ".0%"), _num(r["delta"], ".2f"), r["reason"] or DASH,
                 r["data_quality"] or DASH] for r in t["rows"]]
        lines += [""] + _table(["日期", "建议", "规则", "已赚比例", "Delta", "理由", "数据"], rows)
    if t["notes"]:
        lines += ["", "笔记："]
        lines += [f"- {n['date']}（{NOTE_KIND_LABEL.get(n['kind'], n['kind'])}）{n['text']}"
                  for n in t["notes"]]
    return lines


def _render_timelines(report: dict) -> list[str]:
    lines = ["## 每笔已结算持仓时间线", ""]
    if not report["timelines"]:
        return lines + [_no_settled(report)]
    for t in report["timelines"]:
        lines += _render_timeline(t) + [""]
    return lines[:-1]


def _render_open(report: dict) -> list[str]:
    lines = ["## 进行中的持仓", ""]
    if not report["open_positions"]:
        return lines + ["当前没有进行中的持仓"]
    for p in report["open_positions"]:
        last = p["last_advice"]
        tail = (f"最近建议：{ADVICE_LABEL.get(last, last)}（{p['last_advice_date']}）"
                if last else "最近建议：—")
        lines.append(f"- #{p['id']} {p['ticker']} {p['strikes']}：已有 {p['advice_rows']} 条"
                     f"可用建议，{tail}")
    return lines


FOOTER = [
    "- 口径：当时平仓用快照中间价，真实有滑点会让“听建议”略显乐观；",
    "- 同日结算按 id 排序仅供参考；",
    "- pnl 为 0 的持仓不算赢也不算出事；",
    "- 模拟/研究用途，非投资建议",
]


def render_journal_md(report: dict) -> str:
    sections = [_render_header(report), _render_stats(report), _render_sweeps(report),
                _render_covered_call(report), _render_lot_put(report), _render_condor(report),
                _render_bullcall(report), _render_timelines(report),
                _render_open(report), ["---"] + FOOTER]
    sections = [s for s in sections if s]
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


# --------------------------------------------------------------------------- CLI

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="put_journal", description="期权建议复盘")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")
    note = sub.add_parser("note", help="给持仓记一条笔记")
    note.add_argument("id", type=int)
    note.add_argument("text")
    note.add_argument("--kind", choices=NOTE_KINDS, default="other")
    return p


def _run_note(args: argparse.Namespace, ledger: dict, folder: Path, today: date) -> int:
    known = {s["id"] for s in ledger["spreads"]}
    try:
        add_note(folder / NOTES_FILE, args.id, args.text, args.kind, today, known)
    except NotesError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"已记录 #{args.id} 的笔记（{args.kind}）：{folder / NOTES_FILE}")
    hidden = {NOTE_HIDDEN.get(ledger_mod.strategy_of(s)) for s in ledger["spreads"]
              if s["id"] == args.id}
    for notice in sorted(n for n in hidden if n):
        print(notice)
    return 0


def _run_report(ledger: dict, folder: Path, today: date) -> int:
    snapshots, warnings = load_snapshots(folder / REVIEWS_DIR)
    try:
        notes = load_notes(folder / NOTES_FILE)
    except NotesError as exc:
        warnings.append(f"{exc}（本次报告不含笔记）")
        notes = {"version": 1, "notes": {}}
    report = build_report(ledger["spreads"], snapshots, notes, today.isoformat(), warnings)
    # Sources are per record (the ibkr and yahoo ledgers share one folder): a mixed sample is
    # said even in IBKR mode; an all-IBKR journal in IBKR mode is unchanged.
    mix = option_labels.run_notes(option_labels.journal_mix_line(ledger["spreads"]))
    md = option_labels.with_banner(render_journal_md(report), mix)
    # raises before anything is written if a float is not finite
    text = dump_json({**report, **option_labels.labelled_fields()})
    _write_atomic(folder / "journal.md", md)
    _write_atomic(folder / "journal.json", text)
    c = report["counts"]
    print(f"已结算 {c['settled']} 笔（有建议可比 {c['comparable']} 笔），"
          f"进行中 {c['open']} 笔，快照 {c['snapshots']} 份")
    for line in _own_block_count_lines(report):
        print(line)
    if c["settled"] == 0:
        print(_no_settled(report))
    for w in report["warnings"]:
        print(f"提示：{w}")
    print(f"已保存：{folder / 'journal.md'}\n已保存：{folder / 'journal.json'}")
    return 0


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = _build_parser().parse_args(argv)
    today = today or date.today()
    try:
        option_labels.print_console_header()
        folder = spread_sim.resolve_ledger_dir(args.ledger_dir)
        ledger = ledger_mod.load(folder / "spreads.json")
    except (ledger_mod.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    if args.command == "note":
        return _run_note(args, ledger, folder, today)
    return _run_report(ledger, folder, today)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
