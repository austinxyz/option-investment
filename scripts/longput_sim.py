#!/usr/bin/env python3
"""Long put paper-trading simulator — CLI.

    longput_sim.py suggest TICKER [--min-days N]
        # saves suggestions/<snapshot>-<TICKER>-longput.{md,json} and longput-sim/<snapshot>.md
        # (no ledger write)
    longput_sim.py open TICKER {0.50,0.40,0.30} [--contracts N]
        # records the saved candidate verbatim
    longput_sim.py status | close ID       # shared with spread_sim

A bearish single-leg buyer: buy one put, max loss = the premium paid. `open` never re-fetches the
market: it records the candidate the user reviewed. The file name pattern ends in
`-longput.json`, which none of the other simulators' loaders match (and this one matches none of
theirs). Paper only: never touches data/positions.md and has no order code.

Hand example (long put design section 3): spot 100, buy 95 Put mid 2.40 -> debit 2.40, max loss
$240, breakeven 92.60 (-7.40%), display max profit $9,260; no required win rate.
See docs/superpowers/specs/2026-10-30-long-put-design.md.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import ibkr_session
import longput_sim_render as render
import longput_sim_rules as rules
import option_labels
import option_settings
import spread_core as core
import spread_data
import spread_ledger as ledger_mod
import spread_sim

SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "longput-sim"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})-(?P<ticker>{TICKER_PATTERN})-longput\.json$")
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
TOL = 1e-6          # bucket comparisons; money uses core.LONGPUT_ROUNDING_TOL
MIN_DAYS_FLOOR = 7
CANDIDATE_NUMBERS = ("put_strike", "put_mid", "debit", "breakeven", "breakeven_move_pct",
                     "put_delta", "underlying_price", "max_profit", "max_loss")
CANDIDATE_OPTIONAL = ("put_iv",)
# Fields copied verbatim from the saved candidate into the ledger record (Global Constraints:
# the Long Put fields). Explicit on purpose: the candidate also carries display-only keys
# (max_profit, max_loss, warnings) that must never reach the ledger.
COPIED_FIELDS = ("put_strike", "put_mid", "debit", "breakeven", "breakeven_move_pct",
                 "delta_bucket", "put_delta", "put_iv", "underlying_price")
# How far above spot (fraction) and how many strikes the explicit at-the-money request covers.
# The 0.50 |delta| put sits at the money (at most a little in it: MAX_DELTA_GAP is 0.05).
ATM_REACH = 0.10
ATM_STRIKES = 8

# Null until spread_sim settles or closes the record (the same shape spread_sim writes back).
SETTLEMENT_FIELDS = ("exit_date", "exit_value", "exit_underlying", "pnl", "result",
                     "fraction_of_debit")


# ---- suggest ---------------------------------------------------------------------

def _merge(rows: list[dict], extra: list[dict]) -> list[dict]:
    """Union of two chain fetches by strike (the later fetch wins on overlap)."""
    by_strike = {r["strike"]: r for r in rows}
    by_strike.update({r["strike"]: r for r in extra})
    return [by_strike[k] for k in sorted(by_strike)]


def _atm_puts(ticker: str, expiry: str, spot: float) -> list[dict]:
    """At- and just-in-the-money puts, asked for by strike only (window 0).

    Every source's default put window is strictly below spot (ibkr_chain.strike_window), so the
    0.50 put exists only when asked for; this is asked in every data mode. Any DataError aborts
    the run (as in the Bear Put CLI): swallowing it would silently move the 0.50 bucket. No listed
    strike at or above spot is not an error: nothing is asked."""
    listed = spread_data.chain_strikes(ticker)
    atm = [k for k in sorted(listed) if spot <= k <= spot * (1 + ATM_REACH)][:ATM_STRIKES]
    if not atm:
        return []
    return spread_data.fetch_puts(ticker, expiry, strikes=tuple(atm),
                                  window_pct=0, window_limit=0)


def _expiry(ticker: str, snap_date: date, min_days: int) -> tuple[str, int]:
    expiry = spread_data.pick_expiry(spread_data.expirations(ticker), snap_date,
                                     min_days=min_days)
    if expiry is None:
        raise spread_data.DataError(f"{ticker} 没有距快照日至少 {min_days} 天的到期日")
    return expiry, (date.fromisoformat(expiry) - snap_date).days


def _snapshot(ticker: str, min_days: int, today: date) -> dict:
    """Every network read for one suggest run; nothing is written until this returns."""
    snap_date, spot = spread_data.last_close(ticker)
    intraday = snap_date == today and spread_data.market_open_now()
    expiry, days = _expiry(ticker, snap_date, min_days)
    expiry_date = date.fromisoformat(expiry)
    atm = _atm_puts(ticker, expiry, spot)
    # The default window is fetched LAST: yahoo_data keeps the stale-drop count of the latest
    # fetch per side, and a window-free fetch would reset it to zero.
    puts = _merge(atm, spread_data.fetch_puts(ticker, expiry))
    earnings = spread_data.earnings_status(ticker, snap_date, expiry_date)
    ex_div = spread_data.ex_dividend_in_window(ticker, snap_date, expiry_date)
    candidates = rules.build_longput_candidates(spot, days, puts, earnings, ex_div,
                                                expiry_date, snap_date,
                                                **option_settings.spread_kwargs())
    return {"ticker": ticker, "snapshot_date": snap_date.isoformat(), "spot": spot,
            "expiry": expiry, "days": days, "min_days": min_days, "earnings": earnings,
            "ex_dividend": ex_div.isoformat() if ex_div else None, "intraday": intraday,
            "candidates": candidates}


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
    """Every readable long put candidate of this snapshot day, plus names that would not load."""
    docs, unreadable = [], []
    for p in sorted(folder.glob(f"{stamp}-*-longput.json")):
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
    md = _write_pair(sug_dir / f"{stamp}-{doc['ticker']}-longput", md_text, json_text)
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
    if doc["days"] < core.LONGPUT_MIN_DAYS:
        print(f"提示：距到期仅 {doc['days']} 天 < {core.LONGPUT_MIN_DAYS} 天：净买方，时间不在你这边")
    for note in render.shared_strike_notes(doc["candidates"]):
        print(f"提示：{note}")
    for warning in render.FIXED_WARNINGS:
        print(f"提示：{warning}")


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    spread_data.ensure_source()  # nothing is read or written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    snap = _snapshot(args.ticker, args.min_days, today)
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
        f"已保存的 {ledger_mod.LONGPUT_LABEL} 候选 {name} 不可用（{why}），请重新运行 suggest {ticker}")


def _check_candidate(c: dict) -> None:
    """Raise ValueError/KeyError/TypeError when one saved bucket has the wrong shape."""
    if not any(abs(c["delta_bucket"] - b) < TOL for b in core.LONGPUT_DELTA_BUCKETS):
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
    if not isinstance(doc["candidates"], list) or not doc["candidates"]:
        raise ValueError("candidates 为空")
    for c in doc["candidates"]:
        _check_candidate(c)


def _load_candidate(ledger_dir: Path, ticker: str) -> dict:
    """Latest saved long put candidate for exactly this ticker; damaged or missing is an error."""
    folder = ledger_dir / SUGGESTIONS_DIR
    files = sorted(p for p in folder.glob("*-longput.json")
                   if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker
                   ) if folder.is_dir() else []
    if not files:
        raise ledger_mod.LedgerError(
            f"没有 {ticker} 的已保存 {ledger_mod.LONGPUT_LABEL} 候选，请先运行 suggest {ticker}")
    path = files[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_doc(doc, ticker)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(path.name, ticker, exc) from exc
    doc["file"] = path.name[:-len(".json")] + ".md"
    return doc


def _structure_error(c: dict) -> str | None:
    """Why a saved long put is not a valid single leg (design section 2), or None."""
    strike, debit, tol = c["put_strike"], c["debit"], core.LONGPUT_ROUNDING_TOL  # read live
    if not strike > 0:
        return f"行权价 {strike:g} 无效"
    if not 0 < debit < strike:
        return f"净支出 {debit:.4f} 不在 (0, 行权价 {strike:g}) 之内"
    if abs(debit - c["put_mid"]) > tol:
        return f"净支出 {debit:.4f} 不等于 Put 中间价 {c['put_mid']:.4f}"
    if abs(c["breakeven"] - core.longput_breakeven(strike, debit)) > tol:
        return f"盈亏平衡价 {c['breakeven']:.4f} 不等于 行权价 − 净支出"
    return None


def _offered(doc: dict, bucket: float) -> dict:
    """The saved bucket, refused when missing, rejected or structurally invalid."""
    ticker = doc["ticker"]
    cand = next((c for c in doc["candidates"] if abs(c["delta_bucket"] - bucket) < TOL), None)
    if cand is None:
        raise _damaged(doc["file"], ticker, f"里面没有档位 {bucket:.2f}")
    if cand["status"] != "ok":
        raise ledger_mod.LedgerError(f"档位 {bucket:.2f} 没有合适合约（{cand['reason']}）")
    error = _structure_error(cand)
    if error:
        raise _damaged(doc["file"], ticker, f"档位 {bucket:.2f} 结构无效：{error}")
    return cand


# ---- open ------------------------------------------------------------------------

def _record(doc: dict, cand: dict, contracts: int) -> dict:
    """Explicit-field record (Global Constraints): the Long Put fields spread_sim,
    spread_report and put_review read; no seller or spread keys (credit / short_strike /
    long_strike / width / net_credit / exit_cost / ...) and none of the display-only keys."""
    return {
        "strategy": ledger_mod.STRATEGY_LONG_PUT, "ticker": doc["ticker"],
        **{key: cand[key] for key in COPIED_FIELDS},
        "contracts": contracts, "expiry": doc["expiry"], "snapshot_date": doc["snapshot_date"],
        "status": "open", "suggestion_file": doc["file"],
        "earnings_in_window": EARNINGS_FLAG[doc["earnings"]],
        **{key: None for key in SETTLEMENT_FIELDS},
        **option_labels.record_fields(doc),
    }


def _check_open_args(doc: dict, today: date) -> None:
    if today >= date.fromisoformat(doc["expiry"]):
        raise ledger_mod.LedgerError(
            f"{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest {doc['ticker']}")


def _print_opened(stored: dict, doc: dict, cand: dict) -> None:
    kind = "盘中" if doc.get("intraday") else "收盘"
    n, debit, strike = stored["contracts"], stored["debit"], stored["put_strike"]
    move = round(stored["breakeven_move_pct"] * 100, 2) + 0.0
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），净支出 ${debit:.2f}/股，"
          f"盈亏平衡 {stored['breakeven']:.2f}（回本幅度 {move:+.2f}%），"
          f"最大亏损 / 占用资金 ${core.longput_max_loss(debit, n):,.0f}，"
          f"最大收益（跌到 0，仅展示）${core.longput_max_profit_display(strike, debit, n):,.0f}；"
          f"{render.NO_REQUIRED}")
    for w in cand["warnings"]:
        print(f"  提示：{w}")


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    doc = _load_candidate(ledger_dir, args.ticker)
    _check_open_args(doc, today)
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


def _min_days(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是 ≥{MIN_DAYS_FLOOR} 的整数") from None
    if value < MIN_DAYS_FLOOR:
        raise argparse.ArgumentTypeError(f"必须是 ≥{MIN_DAYS_FLOOR} 的整数")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="longput_sim",
                                description=f"{ledger_mod.LONGPUT_LABEL}（Long Put）纸上交易模拟器")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="出 3 档候选（不写账本）")
    s.add_argument("ticker", type=spread_sim._ticker_arg)
    s.add_argument("--min-days", type=_min_days, default=core.LONGPUT_MIN_DAYS,
                   help=f"到期日距快照日的最少天数（默认 {core.LONGPUT_MIN_DAYS}，最小 {MIN_DAYS_FLOOR}）")
    o = sub.add_parser("open", help="记录已保存候选里的一档")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("bucket", type=float, choices=core.LONGPUT_DELTA_BUCKETS)
    o.add_argument("--contracts", type=spread_sim._positive_int, default=1)
    sub.add_parser("status", help="查看未平仓浮盈亏，到期自动结算（与 spread_sim 共用）")
    c = sub.add_parser("close", help="提前平仓（与 spread_sim 共用）")
    c.add_argument("id", type=int)
    return p


COMMANDS = {"suggest": cmd_suggest, "open": cmd_open, "status": cmd_status, "close": cmd_close}


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        option_labels.print_console_header()
        ledger_dir = spread_sim.resolve_ledger_dir(args.ledger_dir)
        return COMMANDS[args.cmd](args, ledger_dir, today or date.today())
    except (spread_data.DataError, ledger_mod.LedgerError, ibkr_session.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
