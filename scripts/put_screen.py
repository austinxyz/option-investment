#!/usr/bin/env python3
"""Cash-secured put screen — CLI.

    put_screen.py suggest [--dte {weekly,monthly}] [--tickers A,B]  # default weekly; screen the universe, save candidates (no ledger write)
    put_screen.py open TICKER [--contracts N]  # records the saved candidate, never re-fetches
    put_screen.py status                     # shared with spread_sim (spreads and puts together)
    put_screen.py close ID

Writes suggestions/<snapshot>-<TICKER>-put.{md,json} and put-screen/<snapshot>.{md,json}.
See docs/superpowers/specs/2026-10-18-put-screen-design.md.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import option_labels
import option_settings
import put_screen_render as render
import put_screen_rules as rules
import put_screen_wiki
import spread_data
import spread_ledger as ledger_mod
import spread_sim

REPO = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO / "wiki"
WATCHLIST = REPO / "data" / "watchlist.txt"
NOTHING_TO_SCREEN = "没有可筛的标的：用 --tickers，或照 examples/watchlist.txt 建 data/watchlist.txt"
EXIT_NOTHING_TO_SCREEN = 2
SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "put-screen"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^\d{{4}}-\d{{2}}-\d{{2}}-(?P<ticker>{TICKER_PATTERN})-put\.json$")
SUMMARY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
NO_EXPIRY_REASON = {"weekly": rules.REASON_NO_WEEKLY, "monthly": rules.REASON_NO_MONTHLY}
NO_EXPIRY_MESSAGE = {"weekly": "本日无合适周度到期", "monthly": "本日无合适月度到期"}
DEFAULT_DTE = "weekly"
WINDOWS = {"weekly": rules.WEEKLY_DAYS, "monthly": rules.WINDOW_DAYS}
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
REQUIRED_NUMBERS = ("spot", "strike", "mid", "delta", "iv")


class NothingToScreen(Exception):
    """No ticker source produced a ticker: the run stops before any file or network access."""


def _has_wiki_pages(wiki_dir: Path) -> bool:
    return any((Path(wiki_dir) / "tickers").glob("*/overview.md"))


def _demo_tickers() -> list[str]:
    import demo_market  # lazy: only the demo source needs the fictional market
    return list(demo_market.DEMO_TICKERS)


def _list_universe(path: Path) -> list:
    try:
        return put_screen_wiki.universe_from_list(path) if Path(path).is_file() else []
    except ValueError as exc:  # a hand-written file with a bad line: say which one
        raise option_settings.SettingsError(f"{Path(path).name}：{exc}") from exc


def resolve_universe(tickers: str | None, wiki_dir: Path, watchlist: Path, source: str) -> list:
    """First non-empty source wins: --tickers > wiki overview pages > watchlist > demo > none.

    A wiki folder holding only .gitkeep (the share edition) counts as absent, otherwise the
    screen would write an empty summary instead of falling through to the watchlist.
    """
    if tickers:
        return put_screen_wiki.universe_from_tickers(tickers)
    if _has_wiki_pages(wiki_dir):
        return put_screen_wiki.build_universe(wiki_dir)  # unchanged: byte-identical output
    rows = _list_universe(watchlist)
    if rows or source != "demo":
        return rows
    return put_screen_wiki.universe_from_tickers(",".join(_demo_tickers()))


def _rules_doc(dte_mode: str) -> dict:
    doc = {"dte_mode": dte_mode, "window_days": list(WINDOWS[dte_mode]),
           "target_delta": rules.TARGET_DELTA, "max_spread_pct": rules.MAX_SPREAD_PCT}
    spread = option_settings.spread_kwargs()  # empty in IBKR: the document keeps its old keys
    if spread:
        doc["max_spread_pct"] = spread["spread_limit"]
        doc["spread_warn_pct"] = spread["spread_warn"]
    return doc


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=float) + "\n",
                    encoding="utf-8")


def _failed(ticker: str, exc: Exception) -> rules.Reject:
    return rules.Reject(ticker, spread_data.fetch_failure_reason(exc))


def _evaluate(row, mas: dict, snapshot: date, dte_mode: str) -> dict | rules.Reject:
    """One ticker's evaluation; a lost gateway aborts, any other trouble becomes a reject."""
    ticker = row.ticker
    try:
        return rules.evaluate_ticker(
            row, snapshot=snapshot, spot=mas["spot"], ma50=mas["ma50"], ma200=mas["ma200"],
            expirations=spread_data.expirations(ticker),
            earnings_for=lambda expiry: spread_data.earnings_status(
                ticker, snapshot, date.fromisoformat(expiry)),
            puts_for=lambda expiry: spread_data.fetch_puts(ticker, expiry),
            dte_mode=dte_mode, **option_settings.spread_kwargs())
    except spread_data.SourceUnavailable:
        raise  # the link is gone: the whole run is invalid, not one ticker
    except Exception as exc:  # an odd frame must not stop the screen
        return _failed(ticker, exc)


def _market(ticker: str) -> dict:
    """IBKR spot and snapshot day, plus the Yahoo moving averages."""
    snapshot_date, spot = spread_data.last_close(ticker)
    mas = spread_data.moving_averages(ticker)
    return {"snapshot_date": snapshot_date, "spot": spot,
            "ma50": mas["ma50"], "ma200": mas["ma200"]}


def _fetch_all(universe) -> tuple[dict, dict]:
    """ticker -> spot and moving averages, and ticker -> Reject for those rejected or failed.

    Rejects that need no market data (leveraged ETF, Avoid, unrecognized) cost no network call.
    """
    got: dict = {}
    failed: dict = {}
    for row in universe:
        early = rules.prescreen_reject(row)
        if early:
            failed[row.ticker] = rules.Reject(row.ticker, early)
            continue
        try:
            got[row.ticker] = _market(row.ticker)
        except spread_data.SourceUnavailable:
            raise
        except Exception as exc:
            failed[row.ticker] = _failed(row.ticker, exc)
    return got, failed


def _remove_stale_candidates(folder: Path, stamp: str) -> None:
    """Delete this snapshot's earlier put candidate files (spread suggestions are untouched)."""
    pattern = re.compile(rf"^{re.escape(stamp)}-{TICKER_PATTERN}-put\.(md|json)$")
    for p in folder.iterdir():
        if pattern.match(p.name):
            p.unlink()


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    dte_mode = args.dte
    # Resolved first: "nothing to screen" must not need a gateway or a network.
    universe = resolve_universe(args.tickers, WIKI_DIR, WATCHLIST, option_settings.data_source())
    if not universe:
        raise NothingToScreen(NOTHING_TO_SCREEN)
    spread_data.ensure_source()  # nothing is written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    got, failed = _fetch_all(universe)
    snapshot = (Counter(m["snapshot_date"] for m in got.values()).most_common(1)[0][0]
                if got else today)
    intraday = snapshot == today and spread_data.market_open_now()
    candidates: list[dict] = []
    rejects: list = []
    for row in universe:
        res = failed.get(row.ticker) or _evaluate(row, got[row.ticker], snapshot, dte_mode)
        (rejects if isinstance(res, rules.Reject) else candidates).append(res)
    all_no_expiry = bool(universe) and not candidates and all(
        r.reason == NO_EXPIRY_REASON[dte_mode] for r in rejects)
    message = NO_EXPIRY_MESSAGE[dte_mode] if all_no_expiry else None
    notes = option_labels.run_notes(hint, option_labels.stale_drop_line(
        option_labels.stale_drop_total((c["ticker"], c["expiry"]) for c in candidates)))

    stamp, generated = snapshot.isoformat(), today.isoformat()
    sug_dir = ledger_dir / SUGGESTIONS_DIR
    out_dir = ledger_dir / SUMMARY_DIR
    sug_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_candidates(sug_dir, stamp)
    for c in candidates:  # md first, json last so a json never lacks its md
        base = f"{stamp}-{c['ticker']}-put"  # never with_suffix: tickers may contain dots
        (sug_dir / f"{base}.md").write_text(render.render_ticker_md(c, intraday), encoding="utf-8")
        _write_json(sug_dir / f"{base}.json", render.candidate_to_json(c, generated, intraday))
    summary = out_dir / f"{stamp}.md"
    summary_json = out_dir / f"{stamp}.json"
    summary.write_text(option_labels.add_notes(render.render_summary_md(
        candidates, rejects, stamp, generated, intraday, message=message,
        dte_mode=dte_mode), notes), encoding="utf-8")
    _write_json(summary_json, render.summary_to_json(
        candidates, rejects, stamp, generated, intraday, _rules_doc(dte_mode)))
    option_labels.print_notes(notes[1:] if hint else notes)  # the hint was printed first
    print(f"汇总已保存：{summary}")
    print(f"候选 {len(candidates)} 个，剔除 {len(rejects)} 个。"
          "确认后运行 open <TICKER>，记录的就是已保存的候选。")
    return 0


def _damaged(name: str, ticker: str, why) -> ledger_mod.LedgerError:
    return ledger_mod.LedgerError(f"已保存的候选 {name} 不可用（{why}），请重新运行 suggest")


def _check_candidate(doc: dict, ticker: str) -> None:
    """Raise ValueError/KeyError/TypeError when the saved file cannot be trusted by `open`."""
    if doc["ticker"] != ticker:
        raise ValueError(f"文件里的 ticker 是 {doc['ticker']}，对不上")
    date.fromisoformat(doc["expiry"])
    date.fromisoformat(doc["snapshot_date"])
    if doc["earnings"] not in EARNINGS_FLAG:
        raise ValueError("earnings 取值不对")
    for key in REQUIRED_NUMBERS:
        spread_sim._number(doc[key], key)
    spread_sim._number(doc["spread_pct"], "spread_pct", allow_none=True)
    if not isinstance(doc["warnings"], list):
        raise ValueError("warnings 不是列表")


def _load_candidate(ledger_dir: Path, ticker: str) -> dict | None:
    folder = ledger_dir / SUGGESTIONS_DIR
    files = sorted(p for p in folder.glob("*.json")
                   if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker)
    if not files:
        return None
    try:
        doc = json.loads(files[-1].read_text(encoding="utf-8"))
        _check_candidate(doc, ticker)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(files[-1].name, ticker, exc) from exc
    doc["file"] = files[-1].name[:-len(".json")] + ".md"
    return doc


def _check_not_rejected(ledger_dir: Path, ticker: str, doc: dict) -> str | None:
    """Error text when the newest screen (not older than the candidate) did not select the ticker."""
    folder = ledger_dir / SUMMARY_DIR
    files = sorted(p for p in folder.glob("*.json") if SUMMARY_FILE.match(p.name))         if folder.is_dir() else []
    if not files:
        return None
    newest = files[-1]
    screen_date = newest.name[:-len(".json")]
    if screen_date < doc["snapshot_date"]:
        return None
    try:
        summary = json.loads(newest.read_text(encoding="utf-8"))
        chosen = {c["ticker"] for c in summary["candidates"]}
        reasons = {r["ticker"]: r["reason"] for r in summary["rejects"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _damaged(newest.name, ticker, exc) from exc
    if ticker in chosen:
        return None
    why = f"被剔除：{reasons[ticker]}" if ticker in reasons else "本次筛选没有选出它"
    return f"{screen_date} 的筛选里 {ticker} {why}，请重新运行 suggest"


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    ticker = args.ticker.strip().upper()
    doc = _load_candidate(ledger_dir, ticker)
    if doc is None:
        print(f"错误：没有 {ticker} 的已保存候选，请先运行 suggest", file=sys.stderr)
        return 1
    if today >= date.fromisoformat(doc["expiry"]):
        print(f"错误：{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest",
              file=sys.stderr)
        return 1
    problem = _check_not_rejected(ledger_dir, ticker, doc)
    if problem:
        print(f"错误：{problem}", file=sys.stderr)
        return 1
    record = {
        "ticker": ticker, "strategy": ledger_mod.STRATEGY_CSP, "contracts": args.contracts,
        "snapshot_date": doc["snapshot_date"], "expiry": doc["expiry"],
        "underlying_price": doc["spot"], "suggestion_file": doc["file"],
        "short_strike": doc["strike"], "long_strike": None, "width": None,
        "credit": doc["mid"], "short_mid": doc["mid"], "long_mid": None,
        "short_spread_pct": doc["spread_pct"], "long_spread_pct": None,
        "short_delta": doc["delta"], "short_iv": doc["iv"],
        "delta_target": rules.TARGET_DELTA, "dte_mode": doc.get("dte_mode", "monthly"),
        "tier": doc.get("tier"),
        "earnings_in_window": EARNINGS_FLAG[doc["earnings"]],
        "monday_gap_pct": None, "status": "open",
        "exit_date": None, "exit_cost": None, "exit_underlying": None,
        "pnl": None, "result": None, **option_labels.record_fields(doc),
    }
    path = ledger_dir / "spreads.json"
    led, stored = ledger_mod.add_spread(ledger_mod.load(path), record)  # LedgerError on duplicate
    led = ledger_mod.save_keeping_broker(path, led)
    spread_sim._write_report(ledger_dir, led, {}, today)
    kind = "盘中" if doc.get("intraday") else "收盘"
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），权利金 ${stored['credit']:.2f}/股，"
          f"占用现金 ${stored['short_strike'] * 100 * stored['contracts']:,.0f}")
    for w in doc["warnings"]:
        print(f"  提示：{w}")
    return 0


def cmd_status(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_status(args, ledger_dir, today)


def cmd_close(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_close(args, ledger_dir, today)


def _tickers_arg(text: str) -> str:
    try:
        put_screen_wiki.universe_from_tickers(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"ticker {exc}") from exc
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="put_screen", description="现金担保 Put 筛选与纸上交易")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="筛选 wiki 标的，保存候选（不写账本）")
    s.add_argument("--dte", choices=("weekly", "monthly"), default=DEFAULT_DTE,
                   help="到期类型：weekly = 7–14 天最近到期（默认），monthly = 30–45 天月度")
    s.add_argument("--tickers", type=_tickers_arg, default=None, metavar="AAA,BBB",
                   help="只筛这些代码（优先于 wiki 和 data/watchlist.txt）")
    o = sub.add_parser("open", help="记录已保存的候选")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("--contracts", type=spread_sim._positive_int, default=1)
    sub.add_parser("status", help="查看未平仓浮盈亏，到期自动结算")
    c = sub.add_parser("close", help="提前平仓")
    c.add_argument("id", type=int)
    return p


COMMANDS = {"suggest": cmd_suggest, "open": cmd_open, "status": cmd_status, "close": cmd_close}


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        option_labels.print_console_header()
        ledger_dir = spread_sim.resolve_ledger_dir(args.ledger_dir)
        return COMMANDS[args.cmd](args, ledger_dir, today or date.today())
    except NothingToScreen as exc:
        print(exc, file=sys.stderr)
        return EXIT_NOTHING_TO_SCREEN
    except (spread_data.DataError, ledger_mod.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
