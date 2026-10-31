#!/usr/bin/env python3
"""Covered-call paper screen — CLI.

    call_screen.py suggest [--dte {weekly,monthly}] [--delta X]  # screen held lots, save candidates (no ledger write)
    call_screen.py open TICKER [--lot real|ID] [--contracts N]  # records the saved candidate
    call_screen.py status | close ID                 # shared with spread_sim
    call_screen.py shares add TICKER --shares N --cost C | list | remove ID

Writes suggestions/<snapshot>-<TICKER>-call-{real|lot<ID>}.{md,json} and call-screen/<snapshot>.{md,json}.
Paper only: positions.md is read through holdings.real_lots and never written.
See docs/superpowers/specs/2026-10-21-call-screen-design.md.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import call_screen_render as render
import call_screen_rules as rules
import holdings
import option_labels
import option_settings
import paper_shares
import put_screen_rules
import spread_data
import spread_ledger as ledger_mod
import spread_sim

SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "call-screen"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})-(?P<ticker>{TICKER_PATTERN})-call-"
    r"(?P<lot>real|lot\d+)\.json$")
SUMMARY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
DEFAULT_DTE = "weekly"
WINDOWS = {"weekly": put_screen_rules.WEEKLY_DAYS, "monthly": put_screen_rules.WINDOW_DAYS}
NO_EXPIRY_REASON = {"weekly": rules.REASON_NO_WEEKLY, "monthly": rules.REASON_NO_MONTHLY}
NO_EXPIRY_MESSAGE = {"weekly": "本日无合适周度到期", "monthly": "本日无合适月度到期"}
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
REQUIRED_NUMBERS = ("spot", "strike", "mid", "delta", "iv", "shares", "cost_basis",
                    "original_cost", "reference_price")


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=float) + "\n",
                    encoding="utf-8")


def _rules_doc(dte_mode: str, target: float = rules.TARGET_DELTA) -> dict:
    doc = {"dte_mode": dte_mode, "window_days": list(WINDOWS[dte_mode]),
           "target_delta": target, "max_spread_pct": rules.MAX_SPREAD_PCT}
    spread = option_settings.spread_kwargs()  # empty in IBKR: the document keeps its old keys
    if spread:
        doc["max_spread_pct"] = spread["spread_limit"]
        doc["spread_warn_pct"] = spread["spread_warn"]
    return doc


def _memo(fn):
    cache: dict = {}

    def call(*key):
        if key not in cache:
            cache[key] = fn(*key)
        return cache[key]
    return call


def _fetch_market(ticker: str) -> dict:
    """One ticker's market data; moving averages are optional (short history is fine)."""
    snap, close = spread_data.last_close(ticker)
    try:
        mas = spread_data.moving_averages(ticker)
    except spread_data.DataError:
        mas = {"ma50": None, "ma200": None}
    return {"snapshot": snap, "close": close, "ma50": mas["ma50"], "ma200": mas["ma200"],
            "month": spread_data.month_return_pct(ticker),
            "expirations": spread_data.expirations(ticker)}


def _fetch_all(lots: list, avail: dict) -> tuple[dict, dict]:
    """ticker -> market data, and ticker -> failure text; one fetch per ticker."""
    got: dict = {}
    failed: dict = {}
    for ticker in dict.fromkeys(l.ticker for l in lots if avail[l.key] >= 1):
        try:
            got[ticker] = _fetch_market(ticker)
        except spread_data.SourceUnavailable:
            raise  # the link is gone: abort the whole run
        except Exception as exc:  # an odd frame must not stop the screen
            failed[ticker] = spread_data.fetch_failure_reason(exc)
    return got, failed


def _evaluate(lot, market: dict, snapshot: date, available: int, dte_mode: str, fns: dict,
              target: float = rules.TARGET_DELTA):
    ticker = lot.ticker
    try:
        return rules.evaluate_lot(
            lot, snapshot=snapshot, spot=market["close"], ma50=market["ma50"],
            ma200=market["ma200"], month_return=market["month"], contracts_available=available,
            expirations=market["expirations"],
            earnings_for=lambda e: fns["earnings"](ticker, e),
            ex_div_for=lambda e: fns["ex_div"](ticker, e),
            calls_for=lambda e: fns["calls"](ticker, e), dte_mode=dte_mode,
            target_delta=target,
            **option_settings.spread_kwargs())
    except spread_data.SourceUnavailable:
        raise
    except Exception as exc:  # any other trouble becomes a reject, never an abort
        return rules.Reject(lot.label, spread_data.fetch_failure_reason(exc))


def _market_fns(snapshot: date) -> dict:
    return {
        "earnings": _memo(lambda t, e: spread_data.earnings_status(
            t, snapshot, date.fromisoformat(e))),
        "ex_div": _memo(lambda t, e: spread_data.ex_dividend_in_window(
            t, snapshot, date.fromisoformat(e))),
        "calls": _memo(spread_data.fetch_calls),
    }


def _screen(lots: list, led: dict, today: date, dte_mode: str,
            target: float = rules.TARGET_DELTA) -> tuple[list, list, date]:
    avail = {l.key: holdings.contracts_available(l, led) for l in lots}
    got, failed = _fetch_all(lots, avail)
    snapshot = (Counter(m["snapshot"] for m in got.values()).most_common(1)[0][0]
                if got else today)
    fns = _market_fns(snapshot)
    candidates: list[dict] = []
    rejects: list = []
    for lot in lots:
        if avail[lot.key] < 1:
            res = rules.Reject(lot.label, rules._precheck(lot, avail[lot.key]))
        elif lot.ticker in failed:
            res = rules.Reject(lot.label, failed[lot.ticker])
        else:
            res = _evaluate(lot, got[lot.ticker], snapshot, avail[lot.key], dte_mode, fns, target)
        (rejects if isinstance(res, rules.Reject) else candidates).append(res)
    return candidates, rejects, snapshot


def _lot_slug(c: dict) -> str:
    return "real" if c["lot_id"] is None else f"lot{c['lot_id']}"


def _remove_stale_candidates(folder: Path, stamp: str) -> None:
    """Delete this snapshot's earlier call candidate files (put/spread files are untouched)."""
    pattern = re.compile(
        rf"^{re.escape(stamp)}-{TICKER_PATTERN}-call-(real|lot\d+)\.(md|json)$")
    for p in folder.iterdir():
        if pattern.match(p.name):
            p.unlink()


def _write_outputs(ledger_dir: Path, candidates: list, rejects: list, stamp: str,
                   today: date, intraday: bool, dte_mode: str,
                   notes: tuple[str, ...] = (), target: float = rules.TARGET_DELTA) -> Path:
    generated = today.isoformat()
    sug_dir, out_dir = ledger_dir / SUGGESTIONS_DIR, ledger_dir / SUMMARY_DIR
    sug_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_candidates(sug_dir, stamp)
    for c in candidates:  # md first, json last so a json never lacks its md
        base = f"{stamp}-{c['ticker']}-call-{_lot_slug(c)}"
        (sug_dir / f"{base}.md").write_text(render.render_lot_md(c, intraday), encoding="utf-8")
        _write_json(sug_dir / f"{base}.json", render.candidate_to_json(c, generated, intraday))
    all_no_expiry = bool(rejects) and not candidates and all(
        r.reason == NO_EXPIRY_REASON[dte_mode] for r in rejects)
    message = NO_EXPIRY_MESSAGE[dte_mode] if all_no_expiry else None
    summary = out_dir / f"{stamp}.md"
    summary.write_text(option_labels.add_notes(render.render_summary_md(
        candidates, rejects, stamp, generated, intraday, message=message,
        dte_mode=dte_mode, target=target), notes), encoding="utf-8")
    _write_json(out_dir / f"{stamp}.json", render.summary_to_json(
        candidates, rejects, stamp, generated, intraday, _rules_doc(dte_mode, target)))
    return summary


def _all_lots(ledger_dir: Path) -> list:
    doc = paper_shares.load_lots(ledger_dir / paper_shares.LOTS_FILE)
    return holdings.real_lots() + holdings.paper_lots(doc)


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    spread_data.ensure_source()  # nothing is written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    for msg in paper_shares.sync(ledger_dir):
        print(msg)
    led = ledger_mod.load(ledger_dir / "spreads.json")
    lots = _all_lots(ledger_dir)
    seed = option_labels.demo_seed_hint(lots)
    if seed:
        print(seed)
    candidates, rejects, snapshot = _screen(lots, led, today, args.dte, args.delta)
    intraday = snapshot == today and spread_data.market_open_now()
    drops = option_labels.stale_drop_line(
        option_labels.stale_drop_total((c["ticker"], c["expiry"]) for c in candidates))
    summary = _write_outputs(ledger_dir, candidates, rejects, snapshot.isoformat(),
                             today, intraday, args.dte, option_labels.run_notes(hint, drops),
                             args.delta)
    option_labels.print_notes(option_labels.run_notes(drops))
    print(f"汇总已保存：{summary}")
    print(f"候选 {len(candidates)} 个，剔除 {len(rejects)} 个。"
          "确认后运行 open <TICKER> --lot real|<ID>，记录的就是已保存的候选。")
    return 0


def _damaged(name: str, why) -> ledger_mod.LedgerError:
    return ledger_mod.LedgerError(f"已保存的候选 {name} 不可用（{why}），请重新运行 suggest")


def _check_candidate(doc: dict, ticker: str, slug: str) -> None:
    """Raise ValueError/KeyError/TypeError when the saved file cannot be trusted by `open`."""
    if doc["ticker"] != ticker:
        raise ValueError(f"文件里的 ticker 是 {doc['ticker']}，对不上")
    if _lot_slug(doc) != slug:
        raise ValueError("文件名里的持仓和内容对不上")
    date.fromisoformat(doc["expiry"])
    date.fromisoformat(doc["snapshot_date"])
    if doc["earnings"] not in EARNINGS_FLAG:
        raise ValueError("earnings 取值不对")
    if not isinstance(doc["source"], str):
        raise ValueError("source 不是字符串")
    for key in REQUIRED_NUMBERS:
        spread_sim._number(doc[key], key)
    spread_sim._number(doc["spread_pct"], "spread_pct", allow_none=True)
    if not isinstance(doc["warnings"], list):
        raise ValueError("warnings 不是列表")
    if "target_delta" in doc:  # absent in files saved before --delta existed
        _check_target_delta(doc["target_delta"])


def _check_target_delta(value) -> None:
    """A hand-edited target_delta must still be a real number inside the --delta range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
            rules.DELTA_ARG_MIN <= value <= rules.DELTA_ARG_MAX):
        raise ValueError("target_delta 取值不对")


def _saved_files(ledger_dir: Path, ticker: str) -> list[tuple[str, str, Path]]:
    """(stamp, lot slug, path) for the ticker's saved candidates, oldest first."""
    folder = ledger_dir / SUGGESTIONS_DIR
    if not folder.is_dir():
        return []
    found = [(m.group("stamp"), m.group("lot"), p) for p in folder.glob("*.json")
             if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker]
    return sorted(found, key=lambda f: (f[0], f[2].name))


def _pick_file(files: list, ticker: str, lot_arg: str | None) -> tuple[str, str, Path]:
    if lot_arg is not None:
        slug = "real" if lot_arg == "real" else f"lot{lot_arg}"
        mine = [f for f in files if f[1] == slug]
        if not mine:
            raise ledger_mod.LedgerError(f"没有 {ticker} 持仓 {lot_arg} 的已保存候选，请先运行 suggest")
        return mine[-1]
    newest = [f for f in files if f[0] == files[-1][0]]
    if len({f[1] for f in newest}) > 1:
        names = "、".join(f[1].removeprefix("lot") for f in newest)
        raise ledger_mod.LedgerError(f"{ticker} 有多个已保存候选，请用 --lot 指定：{names}")
    return newest[-1]


def _summary_stamps(ledger_dir: Path) -> list[Path]:
    folder = ledger_dir / SUMMARY_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.json") if SUMMARY_FILE.match(p.name))


def _check_not_rejected(ledger_dir: Path, stamp: str, doc: dict) -> None:
    """Refuse when the newest screen (not older than the candidate) no longer offers the lot."""
    files = _summary_stamps(ledger_dir)
    if not files or files[-1].name[:-len(".json")] < stamp:
        return
    newest = files[-1]
    try:
        summary = json.loads(newest.read_text(encoding="utf-8"))
        key = (doc["ticker"], doc["source"], doc["lot_id"])
        chosen = {(c["ticker"], c["source"], c["lot_id"]) for c in summary["candidates"]}
        reasons = {r["ticker"]: r["reason"] for r in summary["rejects"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _damaged(newest.name, exc) from exc
    if key in chosen:
        return
    label = doc.get("label", doc["ticker"])
    why = reasons.get(label, "本次筛选没有选出它")
    raise ledger_mod.LedgerError(
        f"{newest.name[:-len('.json')]} 的筛选里 {label} 被剔除：{why}，请重新运行 suggest")


def _load_candidate(ledger_dir: Path, ticker: str, lot_arg: str | None) -> dict:
    files = _saved_files(ledger_dir, ticker)
    if not files:
        raise ledger_mod.LedgerError(f"没有 {ticker} 的已保存候选，请先运行 suggest")
    stamp, slug, path = _pick_file(files, ticker, lot_arg)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_candidate(doc, ticker, slug)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(path.name, exc) from exc
    _check_not_rejected(ledger_dir, stamp, doc)
    doc["file"] = path.name[:-len(".json")] + ".md"
    return doc


def _live_lot(ledger_dir: Path, doc: dict):
    key = (doc["source"], doc["lot_id"], doc["ticker"])
    lot = next((l for l in _all_lots(ledger_dir) if l.key == key), None)
    if lot is None:
        raise ledger_mod.LedgerError(f"{doc['ticker']} 的持仓 {_lot_slug(doc)} 已不存在，请重新运行 suggest")
    return lot


def _record(doc: dict, contracts: int, lot) -> dict:
    return {
        "ticker": doc["ticker"], "strategy": ledger_mod.STRATEGY_COVERED_CALL,
        "contracts": contracts, "snapshot_date": doc["snapshot_date"], "expiry": doc["expiry"],
        "underlying_price": doc["spot"], "suggestion_file": doc["file"],
        "short_strike": doc["strike"], "long_strike": None, "width": None,
        "credit": doc["mid"], "short_mid": doc["mid"], "long_mid": None,
        "short_spread_pct": doc["spread_pct"], "long_spread_pct": None,
        "short_delta": doc["delta"], "short_iv": doc["iv"],
        "delta_target": doc.get("target_delta", rules.TARGET_DELTA),
        "dte_mode": doc.get("dte_mode", DEFAULT_DTE),
        "tier": None, "earnings_in_window": EARNINGS_FLAG[doc["earnings"]],
        "monday_gap_pct": None, "status": "open",
        "exit_date": None, "exit_cost": None, "exit_underlying": None,
        "pnl": None, "result": None, **option_labels.record_fields(doc),
        "lot_ref": {"source": doc["source"], "lot_id": doc["lot_id"], "ticker": doc["ticker"]},
        "cost_basis": lot.cost_basis, "reference_price": lot.reference_price,
        "covered_shares": contracts * 100,
    }


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    ticker = args.ticker.strip().upper()
    doc = _load_candidate(ledger_dir, ticker, args.lot)
    if today >= date.fromisoformat(doc["expiry"]):
        raise ledger_mod.LedgerError(
            f"{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest")
    path = ledger_dir / "spreads.json"
    led = ledger_mod.load(path)
    lot = _live_lot(ledger_dir, doc)
    available = holdings.contracts_available(lot, led)
    if args.contracts > available:
        raise ledger_mod.LedgerError(
            f"{ticker} 这份持仓现在最多可再卖 {available} 张，不能记录 {args.contracts} 张")
    led, stored = ledger_mod.add_spread(led, _record(doc, args.contracts, lot))
    led = ledger_mod.save_keeping_broker(path, led)
    spread_sim._write_report(ledger_dir, led, {}, today)
    kind = "盘中" if doc.get("intraday") else "收盘"
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），权利金 ${stored['credit']:.2f}/股，"
          f"覆盖 {stored['covered_shares']:g} 股")
    for w in doc["warnings"]:
        print(f"  提示：{w}")
    return 0


def cmd_status(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_status(args, ledger_dir, today)


def cmd_close(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_close(args, ledger_dir, today)


def _shares_add(args, ledger_dir: Path, today: date) -> int:
    path = ledger_dir / paper_shares.LOTS_FILE
    ledger_dir.mkdir(parents=True, exist_ok=True)
    doc, lot = paper_shares.add_lot(
        paper_shares.load_lots(path), ticker=args.ticker, shares=args.shares,
        cost=args.cost, opened_on=today.isoformat())
    paper_shares.save_lots(path, doc)
    print(f"已新增假设持仓 #{lot['id']}：{lot['ticker']} {lot['shares']:g} 股，成本 ${lot['original_cost']:.2f}")
    return 0


def _shares_list(args, ledger_dir: Path, today: date) -> int:
    doc = paper_shares.load_lots(ledger_dir / paper_shares.LOTS_FILE)
    if not doc["lots"]:
        print("没有假设持仓。")
    for l in doc["lots"]:
        print(f"#{l['id']} {l['ticker']} {l['shares']:g} 股，有效成本 ${l['cost_basis']:.2f}"
              f"（原始 ${l['original_cost']:.2f}），来源 {l['source']}，状态 {l['status']}")
    return 0


def _shares_remove(args, ledger_dir: Path, today: date) -> int:
    path = ledger_dir / paper_shares.LOTS_FILE
    led = ledger_mod.load(ledger_dir / "spreads.json")
    paper_shares.save_lots(path, paper_shares.remove_lot(
        paper_shares.load_lots(path), led, args.id))
    print(f"已删除假设持仓 #{args.id}")
    return 0


SHARES_COMMANDS = {"add": _shares_add, "list": _shares_list, "remove": _shares_remove}


def cmd_shares(args, ledger_dir: Path, today: date) -> int:
    return SHARES_COMMANDS[args.shares_cmd](args, ledger_dir, today)


def _lot_arg(text: str) -> str:
    value = text.strip().lower()
    if value == "real":
        return value
    if not (value.isdigit() and int(value) >= 1):
        raise argparse.ArgumentTypeError("--lot 只能是 real 或假设持仓编号")
    return str(int(value))


def _add_shares_parser(sub) -> None:
    sh = sub.add_parser("shares", help="管理假设持仓（纸上股票）")
    ssub = sh.add_subparsers(dest="shares_cmd", required=True)
    a = ssub.add_parser("add", help="新增假设持仓")
    a.add_argument("ticker", type=spread_sim._ticker_arg)
    a.add_argument("--shares", type=spread_sim._positive_float, required=True)
    a.add_argument("--cost", type=spread_sim._positive_float, required=True)
    ssub.add_parser("list", help="列出假设持仓")
    r = ssub.add_parser("remove", help="删除假设持仓")
    r.add_argument("id", type=int)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="call_screen", description="备兑 Call 筛选与纸上交易")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="筛选持仓，保存候选（不写账本）")
    s.add_argument("--dte", choices=("weekly", "monthly"), default=DEFAULT_DTE,
                   help="到期类型：weekly = 7–14 天最近到期（默认），monthly = 30–45 天月度")
    s.add_argument("--delta", type=rules.delta_arg, default=rules.TARGET_DELTA, metavar="0.15",
                   help="短 Call 的目标 Delta（0.05–0.40，最多两位小数；默认 0.20）；"
                        "价差过宽时往外最多走到 0.10 或目标（取小者）")
    o = sub.add_parser("open", help="记录已保存的候选")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("--lot", type=_lot_arg, default=None, help="real 或假设持仓编号")
    o.add_argument("--contracts", type=spread_sim._positive_int, default=1)
    sub.add_parser("status", help="查看未平仓浮盈亏，到期自动结算")
    c = sub.add_parser("close", help="提前平仓")
    c.add_argument("id", type=int)
    _add_shares_parser(sub)
    return p


COMMANDS = {"suggest": cmd_suggest, "open": cmd_open, "status": cmd_status,
            "close": cmd_close, "shares": cmd_shares}


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        option_labels.print_console_header()
        ledger_dir = spread_sim.resolve_ledger_dir(args.ledger_dir)
        return COMMANDS[args.cmd](args, ledger_dir, today or date.today())
    except (spread_data.DataError, ledger_mod.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
