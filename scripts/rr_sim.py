#!/usr/bin/env python3
"""Risk reversal (sell OTM put + buy OTM call) paper-trading simulator — CLI.

    rr_sim.py suggest TICKER [--min-days N]
        # saves suggestions/<snapshot>-<TICKER>-rr.{md,json} and rr-sim/<snapshot>.md
        # (no ledger write)
    rr_sim.py open TICKER {0.30,0.20,0.15} [--contracts N]
        # records the saved candidate verbatim; reads only the saved json
    rr_sim.py status | close ID       # shared with spread_sim

`open` never re-fetches the market and needs no gateway. The file name pattern ends in
`-rr.json`, which none of the other loaders match (and this one matches none of theirs).
Paper only: never touches data/positions.md and has no order code. Stock-leg assignment is not
simulated; the ledger P&L is the option legs only.

Hand example (design section 8): spot 100, sell 90 Put 2.00 / buy 110 Call 1.50 -> net credit
0.50, capital (90 - 0.50) x 100 = $8,950 (conservative cash-secured basis), downside
breakeven 89.50, dead zone 90 - 110.
See docs/superpowers/specs/2026-10-27-risk-reversal-design.md (4, 5, 8, 9).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import ibkr_session
import option_labels
import rr_sim_render as render
import rr_sim_rules as rules
import spread_core as core
import spread_data
import spread_ledger as ledger_mod
import spread_sim

SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "rr-sim"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})-(?P<ticker>{TICKER_PATTERN})-rr\.json$")
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
TOL = 1e-6          # bucket comparisons; money uses core.RR_ROUNDING_TOL (via validate_candidate)
MIN_DAYS_FLOOR = rules.MIN_DAYS_FLOOR
# How far above spot (fraction), how many strikes per request and how many requests the explicit
# long-call fetch may use when the default window ends before the call mid falls to the put mid.
CALL_REACH = 0.30
CALL_BATCH = 8
CALL_ROUNDS = 4
CANDIDATE_NUMBERS = ("short_put_strike", "long_call_strike", "short_put_mid", "long_call_mid",
                     "net_credit", "breakeven_down", "put_move_pct", "call_move_pct",
                     "capital_basis", "short_put_delta", "underlying_price")
CANDIDATE_OPTIONAL = ("long_call_delta", "short_put_iv", "long_call_iv", "upside_breakeven")
# Copied verbatim from the saved candidate into the ledger record (design 5.5). Explicit on
# purpose: the candidate also carries display-only keys (capital_basis, upside_breakeven,
# dead_zone, days, warnings) that must never reach the ledger, and the record has none of the
# seller / bull call keys (credit, short_strike, long_strike, width, debit, lot_ref).
COPIED_FIELDS = (
    "short_put_strike", "long_call_strike", "short_put_mid", "long_call_mid", "net_credit",
    "breakeven_down", "put_move_pct", "call_move_pct", "delta_bucket", "short_put_delta",
    "long_call_delta", "short_put_iv", "long_call_iv", "underlying_price")
# Null until spread_sim settles or closes the record.
SETTLEMENT_FIELDS = ("exit_date", "exit_underlying", "pnl", "result", "outcome_zone")


# ---- suggest ---------------------------------------------------------------------

def _merge(rows: list[dict], extra: list[dict]) -> list[dict]:
    """Union of two chain fetches by strike (the explicit fetch wins on overlap)."""
    by_strike = {r["strike"]: r for r in rows}
    by_strike.update({r["strike"]: r for r in extra})
    return [by_strike[k] for k in sorted(by_strike)]


def _put_mids(puts: list[dict], spot: float, days: float) -> list[float]:
    """Mid of each bucket's short put (buckets with no usable put are skipped)."""
    picks = (rules.pick_short_put(puts, spot, days, b) for b in core.RR_PUT_DELTA_BUCKETS)
    return [round(p["quote"]["mid"], 4) for p in picks if isinstance(p, dict)]


def _call_edge(calls: list[dict], spot: float, put_mids: list[float]) -> float | None:
    """Highest OTM call strike when some bucket's best call sits on it and still costs more than
    its put (a cheaper, closer call may lie beyond the window); else None."""
    otm = {k: q for k, q in core.quote_map(calls).items() if k > spot}
    if not otm:
        return None
    top = max(otm)
    for mid in put_mids:
        pick = rules.pick_long_call(calls, spot, mid)
        if isinstance(pick, dict) and pick["quote"]["strike"] == top and pick["quote"]["mid"] > mid:
            return top
    return None


def _more_call_strikes(ticker: str, spot: float, edge: float, asked: set[float]) -> tuple:
    listed = spread_data.chain_strikes(ticker)
    above = [k for k in listed if edge < k <= spot * (1 + CALL_REACH) and k not in asked]
    return tuple(above[:CALL_BATCH])


def _wide_calls(ticker: str, expiry: str, spot: float, days: float, puts: list[dict],
                calls: list[dict]) -> list[dict]:
    """Extend the call chain upward by explicit strike requests until no bucket wants more
    (bounded; a strike the source does not list is asked for once and then left to the rules)."""
    asked: set[float] = set()
    mids = _put_mids(puts, spot, days)
    for _ in range(CALL_ROUNDS):
        edge = _call_edge(calls, spot, mids)
        need = _more_call_strikes(ticker, spot, edge, asked) if edge is not None else ()
        if not need:
            break
        asked.update(need)
        calls = _merge(calls, spread_data.fetch_calls(ticker, expiry, strikes=need))
    return calls


def _no_expiry(ticker: str, snap_date: date, spot: float, min_days: int, intraday: bool) -> dict:
    """All three buckets rejected: no expiry in the window, so no chain is read."""
    return {"ticker": ticker, "snapshot_date": snap_date.isoformat(), "spot": spot,
            "expiry": None, "days": None, "min_days": min_days, "earnings": "unknown",
            "ex_dividend": None, "intraday": intraday,
            "candidates": rules.unavailable_candidates(rules.no_expiry_reason(min_days))}


def _snapshot(ticker: str, min_days: int, today: date) -> dict:
    """Every network read for one suggest run; nothing is written until this returns."""
    snap_date, spot = spread_data.last_close(ticker)
    intraday = snap_date == today and spread_data.market_open_now()
    expiry = rules.pick_rr_expiry(spread_data.expirations(ticker), snap_date, min_days)
    if expiry is None:
        return _no_expiry(ticker, snap_date, spot, min_days, intraday)
    expiry_date = date.fromisoformat(expiry)
    days = (expiry_date - snap_date).days
    puts = spread_data.fetch_puts(ticker, expiry)
    calls = _wide_calls(ticker, expiry, spot, days, puts, spread_data.fetch_calls(ticker, expiry))
    earnings = spread_data.earnings_status(ticker, snap_date, expiry_date)
    ex_div = spread_data.ex_dividend_in_window(ticker, snap_date, expiry_date)
    candidates = rules.build_rr_candidates(spot, days, puts, calls, earnings, ex_div,
                                           expiry_date, snap_date)
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
    """Every readable risk reversal candidate of this snapshot day, plus names that would not load."""
    docs, unreadable = [], []
    for p in sorted(folder.glob(f"{stamp}-*-rr.json")):
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
    md = _write_pair(sug_dir / f"{stamp}-{doc['ticker']}-rr", md_text, json_text)
    docs, unreadable = _day_docs(sug_dir, stamp)
    summary = out_dir / f"{stamp}.md"
    summary.write_text(option_labels.add_notes(
        render.render_summary_md(docs, stamp, today.isoformat(), unreadable), notes),
        encoding="utf-8")
    return md, summary


def _print_suggested(doc: dict, md: Path, summary: Path) -> None:
    ok = sum(c["status"] == "ok" for c in doc["candidates"])
    when = f"到期 {doc['expiry']}（{doc['days']} 天）" if doc["expiry"] else "没有可用的到期日"
    print(f"候选已保存：{md}\n汇总已保存：{summary}")
    print(f"{doc['ticker']} {when}：可用 {ok} 档，剔除 {len(doc['candidates']) - ok} 档。"
          f"确认后运行 open {doc['ticker']} <档位>，记录的就是这份候选。")
    for warning in rules.FIXED_WARNINGS:
        print(f"提示：{warning}")


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    spread_data.ensure_source()  # nothing is read or written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    doc = render.candidate_to_json(_snapshot(args.ticker, args.min_days, today), today.isoformat())
    pairs = [(doc["ticker"], doc["expiry"])] if doc["expiry"] else []
    drops = option_labels.stale_drop_line(option_labels.stale_drop_total(pairs))
    md, summary = _write_outputs(ledger_dir, doc, today, option_labels.run_notes(hint, drops))
    option_labels.print_notes(option_labels.run_notes(drops))
    _print_suggested(doc, md, summary)
    return 0


# ---- load / validate -------------------------------------------------------------

def _damaged(name: str, ticker: str, why) -> ledger_mod.LedgerError:
    return ledger_mod.LedgerError(
        f"已保存的 {ledger_mod.RR_LABEL}候选 {name} 不可用（{why}），请重新运行 suggest {ticker}")


def _check_candidate(c: dict) -> None:
    """Raise ValueError/KeyError/TypeError when one saved bucket has the wrong shape."""
    if not any(abs(c["delta_bucket"] - b) < TOL for b in core.RR_PUT_DELTA_BUCKETS):
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
    if not isinstance(c["dead_zone"], list) or len(c["dead_zone"]) != 2:
        raise ValueError("dead_zone 不是两个行权价")


def _check_expiry(doc: dict) -> None:
    """A null expiry is legal only when every bucket was rejected (no expiry in the window)."""
    if doc["expiry"] is None:
        if any(c["status"] == "ok" for c in doc["candidates"]):
            raise ValueError("expiry 为空但有可用档位")
        return
    date.fromisoformat(doc["expiry"])


def _check_doc(doc: dict, ticker: str) -> None:
    """Raise ValueError/KeyError/TypeError when the saved file cannot be trusted by `open`."""
    if doc["strategy"] != render.STRATEGY or doc["version"] != render.VERSION:
        raise ValueError("strategy 或 version 不对")
    if doc["ticker"] != ticker:
        raise ValueError(f"文件里的 ticker 是 {doc['ticker']}，对不上")
    date.fromisoformat(doc["snapshot_date"])
    if doc["earnings"] not in EARNINGS_FLAG:
        raise ValueError("earnings 取值不对")
    spread_sim._number(doc["spot"], "spot")
    if not isinstance(doc["candidates"], list) or not doc["candidates"]:
        raise ValueError("candidates 为空")
    for c in doc["candidates"]:
        _check_candidate(c)
    _check_expiry(doc)


def _load_candidate(ledger_dir: Path, ticker: str) -> dict:
    """Latest saved risk reversal candidate for exactly this ticker; damaged or missing is an error."""
    folder = ledger_dir / SUGGESTIONS_DIR
    files = sorted(p for p in folder.glob("*-rr.json")
                   if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker
                   ) if folder.is_dir() else []
    if not files:
        raise ledger_mod.LedgerError(
            f"没有 {ticker} 的已保存{ledger_mod.RR_LABEL}候选，请先运行 suggest {ticker}")
    path = files[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_doc(doc, ticker)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(path.name, ticker, exc) from exc
    doc["file"] = path.name[:-len(".json")] + ".md"
    return doc


def _structure_error(c: dict) -> str | None:
    """Why a saved risk reversal is not a valid structure, or None (same tolerance as suggest)."""
    put_k, call_k = c["short_put_strike"], c["long_call_strike"]
    if not put_k < c["underlying_price"] < call_k:
        return "Put 行权价应低于现价、Call 行权价应高于现价"
    if not c["short_put_mid"] > 0 or not c["long_call_mid"] > 0:
        return "两腿中间价必须为正"
    return rules.validate_candidate(c)


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
    """Explicit-field record (design 5.5): no seller / bull call keys and none of the
    candidate's display-only keys."""
    return {
        "strategy": ledger_mod.STRATEGY_RR, "ticker": doc["ticker"],
        **{key: cand[key] for key in COPIED_FIELDS},
        "contracts": contracts, "expiry": doc["expiry"], "snapshot_date": doc["snapshot_date"],
        "status": "open", "suggestion_file": doc["file"],
        "earnings_in_window": EARNINGS_FLAG[doc["earnings"]],
        **{key: None for key in SETTLEMENT_FIELDS},
        **option_labels.record_fields(doc),
    }


def _check_open_args(doc: dict, contracts: int, today: date) -> None:
    ticker = doc["ticker"]
    if contracts < 1:
        raise ledger_mod.LedgerError(f"张数必须 ≥ 1，当前是 {contracts}")
    if doc["expiry"] is not None and today >= date.fromisoformat(doc["expiry"]):
        raise ledger_mod.LedgerError(
            f"{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest {ticker}")


def _print_opened(stored: dict, doc: dict, cand: dict) -> None:
    kind = "盘中" if doc.get("intraday") else "收盘"
    n, net = stored["contracts"], stored["net_credit"]
    capital = core.rr_capital(stored["short_put_strike"], net, n)
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），{render.premium_text(net)}"
          f"（卖 ${stored['short_put_mid']:.2f} − 买 ${stored['long_call_mid']:.2f}），"
          f"占用资金 ${capital:,.0f}（{render.CAPITAL_NOTE}），"
          f"下行盈亏平衡 {stored['breakeven_down']:.2f}，死区 {render.dead_zone_text(cand)}，"
          f"{n} 张，最大收益 {render.UNLIMITED}")
    for w in cand["warnings"]:
        print(f"  提示：{w}")


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    doc = _load_candidate(ledger_dir, args.ticker)
    _check_open_args(doc, args.contracts, today)
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
    value = int(text)
    if value < MIN_DAYS_FLOOR:
        raise argparse.ArgumentTypeError(f"必须是 ≥{MIN_DAYS_FLOOR} 的整数")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rr_sim",
                                description=f"{ledger_mod.RR_LABEL}（Risk Reversal）纸上交易模拟器")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="出 3 档候选（不写账本）")
    s.add_argument("ticker", type=spread_sim._ticker_arg)
    s.add_argument("--min-days", type=_min_days, default=core.RR_MIN_DAYS,
                   help=f"到期日距快照日的最少天数（默认 {core.RR_MIN_DAYS}，最小 {MIN_DAYS_FLOOR}，"
                        f"上限固定 {core.RR_MAX_DAYS} 天）")
    o = sub.add_parser("open", help="记录已保存候选里的一档")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("bucket", type=float, choices=core.RR_PUT_DELTA_BUCKETS)
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
