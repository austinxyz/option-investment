#!/usr/bin/env python3
"""Iron condor paper-trading simulator — CLI.

    condor_sim.py suggest TICKER [--width W]      # saves suggestions/<snapshot>-<TICKER>-condor.{md,json}
                                                  # and condor-sim/<snapshot>.md (no ledger write)
    condor_sim.py open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]   # records the saved candidate
    condor_sim.py status | close ID               # shared with spread_sim

`open` never re-fetches the market: it records the candidate the user reviewed. The file name
pattern ends in `-condor.json`, which spread_sim's `<date>-<TICKER>.json` loader never matches.
Paper only: never touches data/positions.md and has no order code.
See docs/superpowers/specs/2026-10-25-iron-condor-design.md (5, 12).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import condor_sim_render as render
import condor_sim_rules as rules
import ibkr_session
import spread_core as core
import option_labels
import option_settings
import spread_data
import spread_ledger as ledger_mod
import spread_sim

SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "condor-sim"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})-(?P<ticker>{TICKER_PATTERN})-condor\.json$")
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
TOL = 1e-6
# Rules round each credit and the net to 4 places on their own, so net vs the rounded sum
# (and the breakevens) may differ by up to 1.5e-4; anything larger is a damaged file.
ROUNDING_TOL = 2e-4
WING_ROUNDS = 4   # re-pick shorts on the widened chain at most this many times
STRIKE_KEYS = ledger_mod.CONDOR_STRIKE_KEYS
CANDIDATE_NUMBERS = STRIKE_KEYS + (
    "width", "put_credit", "call_credit", "net_credit", "breakeven_low", "breakeven_high",
    "put_short_delta", "call_short_delta", "underlying_price", "max_loss", "required_win_rate")
CANDIDATE_OPTIONAL = ("put_short_iv", "call_short_iv")
# Fields copied verbatim from the saved candidate into the ledger record (Global Constraints).
COPIED_FIELDS = (
    "put_long_strike", "put_short_strike", "call_short_strike", "call_long_strike", "width",
    "put_credit", "call_credit", "net_credit", "breakeven_low", "breakeven_high",
    "put_short_delta", "call_short_delta", "put_short_iv", "call_short_iv",
    "underlying_price", "delta_bucket")


# ---- suggest ---------------------------------------------------------------------

def _merge(rows: list[dict], extra: list[dict]) -> list[dict]:
    """Union of two chain fetches by strike (the explicit fetch wins on overlap)."""
    by_strike = {r["strike"]: r for r in rows}
    by_strike.update({r["strike"]: r for r in extra})
    return [by_strike[k] for k in sorted(by_strike)]


def _wing_strikes(puts: list[dict], calls: list[dict], listed: list[float], spot: float,
                  days: float, width: float) -> tuple[tuple, tuple]:
    """Listed wing strikes each bucket may need that the default window did not return.

    The put wing may sit up to MAX_WIDTH_DEVIATION away from the target width; the call wing
    must equal the put side's real width, so the same band is requested above the short call."""
    reach = width * (1 + core.MAX_WIDTH_DEVIATION) + rules.EPS
    have_p, have_c = set(core.quote_map(puts)), set(core.quote_map(calls))
    want_p: set[float] = set()
    want_c: set[float] = set()
    for bucket in core.DELTA_BUCKETS:
        sp = rules.pick_short_put(puts, spot, days, bucket, **option_settings.spread_kwargs())
        if isinstance(sp, dict):
            ps = sp["quote"]["strike"]
            want_p.update(k for k in listed if ps - reach <= k < ps)
        sc = rules.pick_short_call(calls, spot, days, bucket, **option_settings.spread_kwargs())
        if isinstance(sc, dict):
            cs = sc["quote"]["strike"]
            want_c.update(k for k in listed if cs < k <= cs + reach)
    return tuple(sorted(want_p - have_p)), tuple(sorted(want_c - have_c))


def _chains(ticker: str, expiry: str, spot: float, days: float, width: float,
            puts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Default-window chains, then the wings outside that window requested by strike.

    A widened chain can move a bucket's short to a farther strike (the rules re-pick shorts on
    the merged chain), so the wing request repeats until it asks for nothing new."""
    calls = spread_data.fetch_calls(ticker, expiry)
    listed = spread_data.chain_strikes(ticker)
    for _ in range(WING_ROUNDS):
        extra_p, extra_c = _wing_strikes(puts, calls, listed, spot, days, width)
        if not extra_p and not extra_c:
            break
        if extra_p:
            puts = _merge(puts, spread_data.fetch_puts(ticker, expiry, strikes=extra_p))
        if extra_c:
            calls = _merge(calls, spread_data.fetch_calls(ticker, expiry, strikes=extra_c))
    return puts, calls


def _target_width(puts: list[dict], spot: float, days: float, width: float | None) -> tuple:
    if width is not None:
        return width, "manual"
    rec = core.recommend_width(puts, spot, days)
    return (rec["width"], "auto") if rec else (core.DEFAULT_WIDTH, "fallback")


def _snapshot(ticker: str, width: float | None, today: date) -> dict:
    """Every network read for one suggest run; nothing is written until this returns."""
    snap_date, spot = spread_data.last_close(ticker)
    intraday = snap_date == today and spread_data.market_open_now()
    expiry = spread_data.pick_expiry(spread_data.expirations(ticker), snap_date)
    if expiry is None:
        raise spread_data.DataError(
            f"{ticker} 没有距快照日至少 {spread_data.MIN_DAYS_TO_EXPIRY} 天的到期日")
    expiry_date = date.fromisoformat(expiry)
    days = (expiry_date - snap_date).days
    puts = spread_data.fetch_puts(ticker, expiry)
    width, source = _target_width(puts, spot, days, width)
    puts, calls = _chains(ticker, expiry, spot, days, width, puts)
    earnings = spread_data.earnings_status(ticker, snap_date, expiry_date)
    ex_div = spread_data.ex_dividend_in_window(ticker, snap_date, expiry_date)
    candidates = rules.build_condor_candidates(spot, days, puts, calls, width, earnings,
                                               ex_div, expiry_date, snap_date,
                                               **option_settings.spread_kwargs())
    return {"ticker": ticker, "snapshot_date": snap_date.isoformat(), "spot": spot,
            "expiry": expiry, "days": days, "earnings": earnings,
            "ex_dividend": ex_div.isoformat() if ex_div else None, "width": width,
            "width_source": source, "intraday": intraday, "candidates": candidates}


def _json_text(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, default=float) + "\n"


def _write_pair(base: Path, md_text: str, json_text: str) -> Path:
    """Write md + json via temp files, then move both into place (md first, json last), so a
    failed write leaves neither an orphan md nor a json without its md."""
    # name + suffix, not with_suffix: a ticker like ZZ.Q has a dot of its own
    md, js = (base.parent / f"{base.name}{ext}" for ext in (".md", ".json"))
    tmp_md, tmp_js = (p.parent / f"{p.name}.tmp" for p in (md, js))
    try:
        tmp_md.write_text(md_text, encoding="utf-8")
        tmp_js.write_text(json_text, encoding="utf-8")
    except OSError:
        tmp_md.unlink(missing_ok=True)
        tmp_js.unlink(missing_ok=True)
        raise
    tmp_md.replace(md)
    tmp_js.replace(js)
    return md


def _day_docs(folder: Path, stamp: str) -> tuple[list[dict], list[str]]:
    """Every readable condor candidate of this snapshot day, plus names that would not load."""
    docs, unreadable = [], []
    for p in sorted(folder.glob(f"{stamp}-*-condor.json")):
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
    md = _write_pair(sug_dir / f"{stamp}-{doc['ticker']}-condor", md_text, json_text)
    docs, unreadable = _day_docs(sug_dir, stamp)
    summary = out_dir / f"{stamp}.md"
    summary.write_text(option_labels.add_notes(
        render.render_summary_md(docs, stamp, today.isoformat(), unreadable), notes),
        encoding="utf-8")
    return md, summary


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    spread_data.ensure_source()  # nothing is written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    snap = _snapshot(args.ticker, args.width, today)
    doc = render.candidate_to_json(snap, today.isoformat())
    drops = option_labels.stale_drop_line(
        option_labels.stale_drop_total([(doc["ticker"], doc["expiry"])]))
    md, summary = _write_outputs(ledger_dir, doc, today, option_labels.run_notes(hint, drops))
    option_labels.print_notes(option_labels.run_notes(drops))
    ok = sum(c["status"] == "ok" for c in doc["candidates"])
    print(f"候选已保存：{md}\n汇总已保存：{summary}")
    print(f"{doc['ticker']} 到期 {doc['expiry']}：可用 {ok} 档，剔除 {len(doc['candidates']) - ok} 档。"
          f"确认后运行 open {doc['ticker']} <档位>，记录的就是这份候选。")
    print(f"提示：{rules.WARN_UP_MOVE}")
    return 0


# ---- load / validate -------------------------------------------------------------

def _damaged(name: str, ticker: str, why) -> ledger_mod.LedgerError:
    return ledger_mod.LedgerError(
        f"已保存的铁鹰候选 {name} 不可用（{why}），请重新运行 suggest {ticker}")


def _check_candidate(c: dict) -> None:
    """Raise ValueError/KeyError/TypeError when one saved bucket has the wrong shape."""
    if c["delta_bucket"] not in core.DELTA_BUCKETS:
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
    """Latest saved condor candidate for exactly this ticker; damaged or missing is an error."""
    folder = ledger_dir / SUGGESTIONS_DIR
    files = sorted(p for p in folder.glob("*-condor.json")
                   if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker
                   ) if folder.is_dir() else []
    if not files:
        raise ledger_mod.LedgerError(f"没有 {ticker} 的已保存铁鹰候选，请先运行 suggest {ticker}")
    path = files[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_doc(doc, ticker)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(path.name, ticker, exc) from exc
    doc["file"] = path.name[:-len(".json")] + ".md"
    return doc


def _structure_error(c: dict) -> str | None:
    """Why a saved condor is not a valid structure (Global Constraints), or None."""
    pl, ps, cs, cl = (c[k] for k in STRIKE_KEYS)
    w, put, call, net = c["width"], c["put_credit"], c["call_credit"], c["net_credit"]
    if not pl < ps < cs < cl:
        return "行权价顺序不是 长Put < 短Put < 短Call < 长Call（或两侧复用了行权价）"
    if abs((ps - pl) - w) > TOL or abs((cl - cs) - w) > TOL:
        return f"两侧宽度不等于 {w:g}"
    for name, credit in (("Put", put), ("Call", call)):
        if not 0 < credit < w:
            return f"{name} 侧权利金 {credit:.2f} 不在 (0, {w:g}) 之内"
    if abs(net - (put + call)) > ROUNDING_TOL:
        return f"净权利金 {net:.2f} 不等于两侧之和 {put + call:.2f}"
    if net >= w:
        return f"净权利金 {net:.2f} ≥ 宽度 {w:g}"
    if (abs(c["breakeven_low"] - (ps - net)) > ROUNDING_TOL
            or abs(c["breakeven_high"] - (cs + net)) > ROUNDING_TOL):
        return "盈亏平衡价与行权价、净权利金对不上"
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
    """Explicit-field record (Global Constraints): no credit / short_strike / long_strike keys,
    and none of the candidate's display-only keys (max_loss, required_win_rate, warnings)."""
    return {
        "strategy": ledger_mod.STRATEGY_IRON_CONDOR, "ticker": doc["ticker"],
        **{key: cand[key] for key in COPIED_FIELDS},
        "contracts": contracts, "expiry": doc["expiry"], "snapshot_date": doc["snapshot_date"],
        "status": "open", "suggestion_file": doc["file"],
        "earnings_in_window": EARNINGS_FLAG[doc["earnings"]],
        "exit_date": None, "exit_cost": None, "exit_underlying": None, "pnl": None, "result": None,
        **option_labels.record_fields(doc),
    }


def _check_open_args(doc: dict, args, today: date) -> None:
    ticker = doc["ticker"]
    if today >= date.fromisoformat(doc["expiry"]):
        raise ledger_mod.LedgerError(
            f"{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest {ticker}")
    if args.width is not None and abs(args.width - doc["width"]) > TOL:
        raise ledger_mod.LedgerError(
            f"已保存候选的目标宽度是 ${doc['width']:g}，与 --width {args.width:g} 不一致，"
            f"请重新运行 suggest {ticker} --width {args.width:g}")


def _print_opened(stored: dict, doc: dict, cand: dict) -> None:
    kind = "盘中" if doc.get("intraday") else "收盘"
    n = stored["contracts"]
    capital = (stored["width"] - stored["net_credit"]) * 100 * n
    win_rate = core.breakeven_win_rate(stored["net_credit"], stored["width"])
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），净权利金 ${stored['net_credit']:.2f}/股"
          f"（Put 侧 ${stored['put_credit']:.2f} + Call 侧 ${stored['call_credit']:.2f}），"
          f"盈亏平衡 {stored['breakeven_low']:.2f} / {stored['breakeven_high']:.2f}，"
          f"最大盈利 ${stored['net_credit'] * 100 * n:,.0f}，最大亏损 / 占用资金 ${capital:,.0f}，"
          f"所需胜率 {win_rate * 100:.0f}%")
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="condor_sim", description="铁鹰（Iron Condor）纸上交易模拟器")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="出 3 档铁鹰候选（不写账本）")
    s.add_argument("ticker", type=spread_sim._ticker_arg)
    s.add_argument("--width", type=spread_sim._positive_float, default=None,
                   help="单侧目标宽度；省略则按 Put 链自动推荐")
    o = sub.add_parser("open", help="记录已保存候选里的一档")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("bucket", type=float, choices=core.DELTA_BUCKETS)
    o.add_argument("--contracts", type=spread_sim._positive_int, default=1)
    o.add_argument("--width", type=spread_sim._positive_float, default=None,
                   help="只用于核对，必须与已保存候选的目标宽度一致")
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
