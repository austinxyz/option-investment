#!/usr/bin/env python3
"""Bear call credit spread paper-trading simulator — CLI.

    bearcall_sim.py suggest TICKER [--width W]
        # saves suggestions/<snapshot>-<TICKER>-bearcall.{md,json} and bearcall-sim/<snapshot>.md
        # (no ledger write)
    bearcall_sim.py open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]
    bearcall_sim.py open TICKER --from-pick [...]   # bucket from today's /strategy-pick
        # records the saved candidate verbatim
    bearcall_sim.py status | close ID       # shared with spread_sim

A bearish seller spread: sell an out-of-the-money call, buy the call one width above it.
`open` never re-fetches the market: it records the candidate the user reviewed. The file name
pattern ends in `-bearcall.json`, which none of the other simulators' loaders match (and this
one matches none of theirs). Paper only: never touches data/positions.md and has no order code.

Hand example (bearish design section 8): sell 105 Call 2.10 / buy 110 Call 0.90 -> credit 1.20,
breakeven 106.20, max profit $120, max loss $380, required win rate 76%.
See docs/superpowers/specs/2026-10-27-bearish-and-new-strategies-design.md (5.1-5.5, 5.11-5.13).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import bearcall_sim_render as render
import bearcall_sim_rules as rules
import ibkr_session
import spread_core as core
import option_labels
import option_settings
import spread_data
import spread_ledger as ledger_mod
import spread_sim
import strategy_pick_frompick as frompick

SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "bearcall-sim"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})-(?P<ticker>{TICKER_PATTERN})-bearcall\.json$")
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
TOL = 1e-6          # strike / width / bucket comparisons; money uses core.BEARCALL_ROUNDING_TOL
WIDTH_DECIMALS = 4  # strikes and widths never need finer than 0.0001
LONG_LEG_ROUNDS = 4   # re-pick short legs on the widened chain at most this many times
OPTION_TYPE = "C"
CANDIDATE_NUMBERS = (
    "short_strike", "long_strike", "width", "short_mid", "long_mid", "credit", "breakeven",
    "breakeven_move_pct", "max_loss_move_pct", "short_delta", "underlying_price",
    "max_profit", "max_loss", "required_win_rate")
CANDIDATE_OPTIONAL = ("short_iv", "short_spread_pct", "long_spread_pct")
# Fields copied verbatim from the saved candidate into the ledger record (Global Constraints:
# the seller spread fields plus option_type "C"). Explicit on purpose: the candidate also
# carries display-only keys (max_profit, max_loss, required_win_rate, warnings).
COPIED_FIELDS = (
    "option_type", "short_strike", "long_strike", "width", "short_mid", "long_mid", "credit",
    "breakeven", "breakeven_move_pct", "max_loss_move_pct", "delta_bucket", "short_delta",
    "short_iv", "short_spread_pct", "long_spread_pct", "underlying_price")

# Null until spread_sim settles or closes the record (the same shape spread_sim writes back).
SETTLEMENT_FIELDS = ("exit_date", "exit_cost", "exit_underlying", "pnl", "result")


# ---- suggest ---------------------------------------------------------------------

def _merge(rows: list[dict], extra: list[dict]) -> list[dict]:
    """Union of two chain fetches by strike (the explicit fetch wins on overlap)."""
    by_strike = {r["strike"]: r for r in rows}
    by_strike.update({r["strike"]: r for r in extra})
    return [by_strike[k] for k in sorted(by_strike)]


def _spread_limit() -> float:
    """The hard spread line of the active data source (the IBKR line unless overridden)."""
    return option_settings.spread_kwargs().get("spread_limit", rules.MAX_SPREAD_PCT)


def _calls(ticker: str, expiry: str, spot: float, days: float, width: float,
           calls: list[dict]) -> list[dict]:
    """Add each bucket's long leg (short + width) that the default window did not return.

    A fetched long strike can become a better short leg for another bucket, which then needs a
    new long, so the request repeats until it asks for nothing new (bounded; a strike the
    gateway does not list is asked for once and then left to the rules to reject)."""
    asked: set[float] = set()
    for _ in range(LONG_LEG_ROUNDS):
        need = tuple(k for k in rules.long_strike_needed(calls, spot, days, width,
                                                         spread_limit=_spread_limit())
                     if k not in asked)
        if not need:
            break
        asked.update(need)
        calls = _merge(calls, spread_data.fetch_calls(ticker, expiry, strikes=need))
    return calls


def _target_width(calls: list[dict], spot: float, days: float, width: float | None) -> tuple:
    if width is not None:
        return width, "manual"
    rec = core.recommend_width(calls, spot, days)
    return (rec["width"], "auto") if rec else (core.DEFAULT_WIDTH, "fallback")


def _expiry(ticker: str, snap_date: date) -> tuple[str, int]:
    """First expiry at least MIN_DAYS_TO_EXPIRY (7) days out, the same rule as /spread-sim."""
    expiry = spread_data.pick_expiry(spread_data.expirations(ticker), snap_date)
    if expiry is None:
        raise spread_data.DataError(
            f"{ticker} 没有距快照日至少 {spread_data.MIN_DAYS_TO_EXPIRY} 天的到期日")
    return expiry, (date.fromisoformat(expiry) - snap_date).days


def _snapshot(ticker: str, width: float | None, today: date) -> dict:
    """Every network read for one suggest run; nothing is written until this returns."""
    snap_date, spot = spread_data.last_close(ticker)
    intraday = snap_date == today and spread_data.market_open_now()
    expiry, days = _expiry(ticker, snap_date)
    expiry_date = date.fromisoformat(expiry)
    calls = spread_data.fetch_calls(ticker, expiry)
    width, source = _target_width(calls, spot, days, width)
    calls = _calls(ticker, expiry, spot, days, width, calls)
    earnings = spread_data.earnings_status(ticker, snap_date, expiry_date)
    ex_div = spread_data.ex_dividend_in_window(ticker, snap_date, expiry_date)
    candidates = rules.build_bearcall_candidates(spot, days, calls, width, earnings, ex_div,
                                                 expiry_date, snap_date,
                                                 **option_settings.spread_kwargs())
    return {"ticker": ticker, "snapshot_date": snap_date.isoformat(), "spot": spot,
            "expiry": expiry, "days": days, "earnings": earnings,
            "ex_dividend": ex_div.isoformat() if ex_div else None, "width": width,
            "width_source": source, "intraday": intraday, "candidates": candidates}


def _json_text(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, default=float) + "\n"


def _pair_paths(base: Path) -> tuple[Path, Path]:
    # name + suffix, not a suffix swap: a ticker like ZZ.Q has a dot of its own
    return base.parent / f"{base.name}.md", base.parent / f"{base.name}.json"


def _write_pair(base: Path, md_text: str, json_text: str) -> Path:
    """Write md + json via temp files, then move both into place (md first, json last), so a
    failed write leaves neither an orphan md nor a json without its md."""
    md, js = _pair_paths(base)
    tmp_md, tmp_js = (p.parent / f"{p.name}.tmp" for p in (md, js))
    try:
        tmp_md.write_text(md_text, encoding="utf-8")
        tmp_js.write_text(json_text, encoding="utf-8")
        tmp_md.replace(md)
        tmp_js.replace(js)
    finally:
        tmp_md.unlink(missing_ok=True)
        tmp_js.unlink(missing_ok=True)
    return md


def _day_docs(folder: Path, stamp: str) -> tuple[list[dict], list[str]]:
    """Every readable bear call candidate of this snapshot day, plus names that would not load."""
    docs, unreadable = [], []
    for p in sorted(folder.glob(f"{stamp}-*-bearcall.json")):
        m = SUGGESTION_FILE.match(p.name)
        if not m:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            _check_doc(doc, m.group("ticker"))
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            unreadable.append(p.name)
            continue
        docs.append(doc)
    return docs, unreadable


def _write_outputs(ledger_dir: Path, doc: dict, today: date,
                   notes: tuple[str, ...] = ()) -> tuple[Path, Path]:
    """Candidate md, then its json (a json never lacks its md), then the day's summary.

    `notes` are this run's facts (closed market, stale drops): candidate md and summary."""
    stamp = doc["snapshot_date"]
    md_text = option_labels.add_notes(render.render_candidate_md(doc), notes)
    json_text = _json_text(doc)  # both rendered before anything is written
    sug_dir, out_dir = ledger_dir / SUGGESTIONS_DIR, ledger_dir / SUMMARY_DIR
    sug_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = _write_pair(sug_dir / f"{stamp}-{doc['ticker']}-bearcall", md_text, json_text)
    docs, unreadable = _day_docs(sug_dir, stamp)
    summary = out_dir / f"{stamp}.md"
    summary.write_text(option_labels.add_notes(
        render.render_summary_md(docs, stamp, today.isoformat(), unreadable), notes),
        encoding="utf-8")
    return md, summary


def _print_suggested(doc: dict, md: Path, summary: Path) -> None:
    ok = sum(c["status"] == "ok" for c in doc["candidates"])
    print(f"候选已保存：{md}\n汇总已保存：{summary}")
    print(f"{doc['ticker']} 到期 {doc['expiry']}（{doc['days']} 天）：可用 {ok} 档，"
          f"剔除 {len(doc['candidates']) - ok} 档。"
          f"确认后运行 open {doc['ticker']} <档位>，记录的就是这份候选。")
    for note in render.shared_short_notes(doc["candidates"]):
        print(f"提示：{note}")
    for warning in render.FIXED_WARNINGS:
        print(f"提示：{warning}")


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    spread_data.ensure_source()  # nothing is read or written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    snap = _snapshot(args.ticker, args.width, today)
    doc = render.candidate_to_json(snap, today.isoformat())
    drops = option_labels.stale_drop_line(
        option_labels.stale_drop_total([(doc["ticker"], doc["expiry"])]))
    md, summary = _write_outputs(ledger_dir, doc, today, option_labels.run_notes(hint, drops))
    option_labels.print_notes(option_labels.run_notes(drops))
    _print_suggested(doc, md, summary)
    return 0


# ---- load / validate -------------------------------------------------------------

def _damaged(name: str, ticker: str, why) -> ledger_mod.LedgerError:
    return ledger_mod.LedgerError(
        f"已保存的 {ledger_mod.BEARCALL_LABEL}候选 {name} 不可用（{why}），请重新运行 suggest {ticker}")


def _check_candidate(c: dict) -> None:
    """Raise ValueError/KeyError/TypeError when one saved bucket has the wrong shape."""
    if not any(abs(c["delta_bucket"] - b) < TOL for b in core.DELTA_BUCKETS):
        raise ValueError("delta_bucket 取值不对")
    if c["status"] == "unavailable":
        if not isinstance(c["reason"], str):
            raise ValueError("被剔除的档位缺少原因")
        return
    if c["status"] != "ok":
        raise ValueError("status 取值不对")
    for key in CANDIDATE_NUMBERS:
        spread_sim._number(c[key], key)
    for key in CANDIDATE_OPTIONAL:
        spread_sim._number(c[key], key, allow_none=True)
    if not isinstance(c["warnings"], list):
        raise ValueError("warnings 不是列表")


def _check_doc(doc: dict, ticker: str) -> None:
    """Raise ValueError/KeyError/TypeError when the saved file cannot be trusted by `open`."""
    if doc["strategy"] != render.STRATEGY or doc["version"] != render.VERSION:
        raise ValueError("strategy 或 version 不对")
    if doc["ticker"] != ticker:
        raise ValueError(f"文件里的 ticker 是 {doc['ticker']}，对不上")
    date.fromisoformat(doc["expiry"])
    date.fromisoformat(doc["snapshot_date"])
    if doc["earnings"] not in EARNINGS_FLAG:
        raise ValueError("earnings 取值不对")
    spread_sim._number(doc["spot"], "spot")
    spread_sim._number(doc["width"], "width")
    if not isinstance(doc["candidates"], list) or not doc["candidates"]:
        raise ValueError("candidates 为空")
    for c in doc["candidates"]:
        _check_candidate(c)


def _load_candidate(ledger_dir: Path, ticker: str) -> dict:
    """Latest saved bear call candidate for exactly this ticker; damaged or missing is an error."""
    folder = ledger_dir / SUGGESTIONS_DIR
    files = sorted(p for p in folder.glob("*-bearcall.json")
                   if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker
                   ) if folder.is_dir() else []
    if not files:
        raise ledger_mod.LedgerError(
            f"没有 {ticker} 的已保存 {ledger_mod.BEARCALL_LABEL}候选，请先运行 suggest {ticker}")
    path = files[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_doc(doc, ticker)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(path.name, ticker, exc) from exc
    doc["file"] = path.name[:-len(".json")] + ".md"
    return doc


def _structure_error(c: dict, doc_width: float) -> str | None:
    """Why a saved bear call is not a valid structure (Global Constraints), or None."""
    short, long_ = c["short_strike"], c["long_strike"]
    w, credit, tol = c["width"], c["credit"], core.BEARCALL_ROUNDING_TOL   # read live: one constant
    if c.get("option_type") != OPTION_TYPE:
        return f"option_type 是 {c.get('option_type')!r}，不是 Call"
    if not short < long_:
        return "长腿行权价不高于短腿"
    if abs((long_ - short) - w) > TOL or abs(w - doc_width) > TOL:
        return f"两腿间距 {long_ - short:g} 与宽度 {w:g} / 文件宽度 {doc_width:g} 对不上"
    if not 0 < credit < w:
        return f"净权利金 {credit:.4f} 不在 (0, {w:g}) 之内"
    if abs(credit - (c["short_mid"] - c["long_mid"])) > tol:
        return f"净权利金 {credit:.4f} 不等于两腿中间价之差"
    if abs(c["breakeven"] - (short + credit)) > tol:
        return f"盈亏平衡价 {c['breakeven']:.4f} 不等于 短腿 + 净权利金"
    return None


def _offered(doc: dict, bucket: float) -> dict:
    """The saved bucket, refused when missing, rejected or structurally invalid."""
    ticker = doc["ticker"]
    cand = next((c for c in doc["candidates"] if abs(c["delta_bucket"] - bucket) < TOL), None)
    if cand is None:
        raise _damaged(doc["file"], ticker, f"里面没有档位 {bucket:.2f}")
    if cand["status"] != "ok":
        raise ledger_mod.LedgerError(f"档位 {bucket:.2f} 没有合适合约（{cand['reason']}）")
    error = _structure_error(cand, doc["width"])
    if error:
        raise _damaged(doc["file"], ticker, f"档位 {bucket:.2f} 结构无效：{error}")
    return cand


# ---- open ------------------------------------------------------------------------

def _record(doc: dict, cand: dict, contracts: int) -> dict:
    """Explicit-field record (Global Constraints): the seller spread fields spread_sim,
    spread_report and put_review read, plus option_type "C"; no buyer keys (debit /
    long_call_strike / ...) and none of the candidate's display-only keys."""
    return {
        "strategy": ledger_mod.STRATEGY_BEAR_CALL, "ticker": doc["ticker"],
        **{key: cand[key] for key in COPIED_FIELDS},
        "contracts": contracts, "expiry": doc["expiry"], "snapshot_date": doc["snapshot_date"],
        "status": "open", "suggestion_file": doc["file"],
        "earnings_in_window": EARNINGS_FLAG[doc["earnings"]],
        **{key: None for key in SETTLEMENT_FIELDS},
        **option_labels.record_fields(doc),
    }


def _check_open_args(doc: dict, args, today: date) -> None:
    ticker = doc["ticker"]
    if today >= date.fromisoformat(doc["expiry"]):
        raise ledger_mod.LedgerError(
            f"{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest {ticker}")
    if args.width is not None and abs(args.width - doc["width"]) > TOL:
        raise ledger_mod.LedgerError(
            f"已保存候选的宽度是 ${doc['width']:g}，与 --width {args.width:g} 不一致，"
            f"请重新运行 suggest {ticker} --width {args.width:g}")


def _print_opened(stored: dict, doc: dict, cand: dict) -> None:
    kind = "盘中" if doc.get("intraday") else "收盘"
    n, credit, width = stored["contracts"], stored["credit"], stored["width"]
    max_profit = round(credit * 100 * n, 2) + 0.0
    max_loss = round((width - credit) * 100 * n, 2) + 0.0
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），净权利金 ${credit:.2f}/股"
          f"（卖 ${stored['short_mid']:.2f} − 买 ${stored['long_mid']:.2f}），"
          f"盈亏平衡 {stored['breakeven']:.2f}，"
          f"最大收益 ${max_profit:,.0f}，最大亏损 / 占用资金 ${max_loss:,.0f}，"
          f"所需胜率 {core.breakeven_win_rate(credit, width) * 100:.0f}%")
    for w in cand["warnings"]:
        print(f"  提示：{w}")


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    doc = _load_candidate(ledger_dir, args.ticker)
    _check_open_args(doc, args, today)
    cand = _offered(doc, args.bucket)
    path = ledger_dir / "spreads.json"
    led, stored = ledger_mod.add_spread(ledger_mod.load(path), _record(doc, cand, args.contracts))
    led = ledger_mod.save_keeping_broker(path, led)
    spread_sim._write_report(ledger_dir, led, {}, today)
    _print_opened(stored, doc, cand)
    return 0


def cmd_status(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_status(args, ledger_dir, today)


def cmd_close(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_close(args, ledger_dir, today)


def _width_precision_error(width: float | None) -> str | None:
    """--width with more than WIDTH_DECIMALS decimals is refused before anything runs."""
    if width is None or abs(round(width, WIDTH_DECIMALS) - width) <= 1e-12:
        return None
    return f"--width 最多 {WIDTH_DECIMALS} 位小数，当前是 {width!r}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bearcall_sim",
                                description=f"{ledger_mod.BEARCALL_LABEL}（Bear Call Spread）纸上交易模拟器")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="出 3 档候选（不写账本）")
    s.add_argument("ticker", type=spread_sim._ticker_arg)
    s.add_argument("--width", type=spread_sim._positive_float, default=None,
                   help="两腿宽度；省略则按 Call 链自动推荐")
    o = sub.add_parser("open", help="记录已保存候选里的一档")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("bucket", type=float, choices=core.DELTA_BUCKETS, nargs="?",
                   default=None)
    o.add_argument("--from-pick", action="store_true",
                   help="档位用今天 /strategy-pick TICKER 的 Bear Call 价差推荐（与档位二选一）")
    o.add_argument("--contracts", type=spread_sim._positive_int, default=1)
    o.add_argument("--width", type=spread_sim._positive_float, default=None,
                   help="只用于核对，必须与已保存候选的宽度一致")
    sub.add_parser("status", help="查看未平仓浮盈亏，到期自动结算（与 spread_sim 共用）")
    c = sub.add_parser("close", help="提前平仓（与 spread_sim 共用）")
    c.add_argument("id", type=int)
    return p


COMMANDS = {"suggest": cmd_suggest, "open": cmd_open, "status": cmd_status, "close": cmd_close}
FROM_PICK_STRATEGY = "bear_call_spread"


def _from_pick(args, today: date) -> str | None:
    """Fill the bucket from today's pick; the console line, or None."""
    if not getattr(args, "from_pick", False):
        return None
    args.bucket, line = frompick.open_from_pick(args.ticker, FROM_PICK_STRATEGY, today)
    return line


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    frompick.check_open_args(parser, args)
    if (error := _width_precision_error(getattr(args, "width", None))):
        print(f"错误：{error}", file=sys.stderr)
        return 2
    try:
        pick_line = _from_pick(args, today or date.today())
    except frompick.FromPickError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return frompick.EXIT_FROM_PICK
    except ibkr_session.SettingsError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    try:
        option_labels.print_console_header()
        if pick_line:
            print(pick_line)
        ledger_dir = spread_sim.resolve_ledger_dir(args.ledger_dir)
        return COMMANDS[args.cmd](args, ledger_dir, today or date.today())
    except (spread_data.DataError, ledger_mod.LedgerError, ibkr_session.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
