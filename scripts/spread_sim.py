#!/usr/bin/env python3
"""Bull put credit spread paper-trading simulator — CLI.

    spread_sim.py suggest TICKER [--width 5]     # prints + saves suggestions/<date>-<TICKER>.{md,json}
    spread_sim.py open TICKER {0.10,0.15,0.20} [--contracts 1] [--width W]   # records the saved suggestion
    spread_sim.py status
    spread_sim.py close ID

Opens at the last close's mid price, settles at the expiry-day close.
`open` never re-fetches the market: it records the candidate the user reviewed.
See docs/superpowers/specs/2026-10-14-spread-sim-design.md.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import option_labels
import option_settings
import spread_core as core
import paper_shares
import spread_data
import spread_explain
import spread_html
import spread_ledger as ledger_mod
import spread_report

REPO = Path(__file__).resolve().parent.parent
# The overlay (IBKR) ledger, fixed: broker orders never belong in the demo ledger.
IBKR_LEDGER_DIR = REPO / option_settings.PAPER_DIR
SUGGESTIONS_DIR = "suggestions"
TICKER_PATTERN = r"[A-Z0-9][A-Z0-9.\-]{0,9}"
SUGGESTION_FILE = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}-(?P<ticker>{TICKER_PATTERN})\.json$")
CANDIDATE_NUMBERS = ("short_strike", "long_strike", "width", "credit", "short_mid", "long_mid",
                     "short_delta", "short_iv", "max_profit", "max_loss", "breakeven")
EARNINGS_FLAG = {"yes": True, "no": False, "unknown": None}


def _snapshot(ticker: str, width: float | None, today: date) -> dict:
    snap_date, spot = spread_data.last_close(ticker)
    intraday = snap_date == today and spread_data.market_open_now()
    expiry = spread_data.pick_expiry(spread_data.expirations(ticker), snap_date)
    if expiry is None:
        raise spread_data.DataError(
            f"{ticker} 没有距快照日至少 {spread_data.MIN_DAYS_TO_EXPIRY} 天的到期日")
    expiry_date = date.fromisoformat(expiry)
    days = (expiry_date - snap_date).days
    puts = spread_data.fetch_puts(ticker, expiry)
    earnings = spread_data.earnings_status(ticker, snap_date, expiry_date)
    rec = core.recommend_width(puts, spot, days)
    if width is None:
        width, source = (rec["width"], "auto") if rec else (core.DEFAULT_WIDTH, "fallback")
    else:
        source = "manual"
    width_info = {"source": source, "recommended": rec["width"] if rec else None}
    if rec:
        width_info.update({k: rec[k] for k in ("expected_move", "raw", "step", "atm_iv", "fraction", "capped")})
    return {
        "ticker": ticker.strip().upper(), "snapshot_date": snap_date, "spot": spot,
        "expiry": expiry, "days": days, "earnings": earnings, "width": width,
        "intraday": intraday, "width_info": width_info,
        "candidates": core.build_candidates(spot, days, puts, width, earnings),
    }


def _write_report(ledger_dir: Path, ledger: dict, marks: dict, today: date) -> None:
    # Row marks and the mix line follow each record's own source (ibkr and yahoo share this
    # ledger); the banner follows the current setting. All-IBKR + IBKR setting: unchanged.
    records = option_labels.tag_records(ledger["spreads"])
    notes = option_labels.run_notes(option_labels.ledger_mix_line(ledger["spreads"]))
    text = option_labels.with_banner(spread_report.render_report(
        records, marks, today.isoformat(), broker_cols=True), notes)
    (ledger_dir / "report.md").write_text(text, encoding="utf-8")
    page = option_labels.with_html_banner(
        spread_html.render_report_html(records, marks, today.isoformat()), notes)
    (ledger_dir / "report.html").write_text(page, encoding="utf-8")
    _write_public_report(ledger_dir, ledger, marks, today)


def _write_public_report(ledger_dir: Path, ledger: dict, marks: dict, today: date) -> None:
    """Refresh the shareable page in the overlay wiki (picked up by kb-sync --profile public).

    Only when the repo layout is the real one (data/paper-trading next to wiki/options),
    and only with live marks or nothing open, so a bare open/close never publishes blanks.
    """
    target_dir = ledger_dir.parent.parent / "wiki" / "options"
    if ledger_dir.name != "paper-trading" or not target_dir.is_dir():
        return
    shareable = spread_report.public_records(ledger["spreads"])
    if not marks and any(s["status"] == "open" for s in shareable):
        return
    notes = option_labels.run_notes(option_labels.ledger_mix_line(shareable))
    text = option_labels.with_banner(spread_report.render_public_report(
        option_labels.tag_records(shareable), marks, today.isoformat()), notes)
    (target_dir / "paper-trading.md").write_text(text, encoding="utf-8")


def _is_csp(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_CSP


def _mark_csp(s: dict) -> dict:
    """Current mid-price mark for an open cash-secured put (single leg)."""
    quotes = core.quote_map(spread_data.fetch_puts(
        s["ticker"], s["expiry"], strikes=(s["short_strike"],)))
    short = quotes.get(s["short_strike"])
    if short is None:
        return {"error": "期权链里找不到这条腿的有效报价"}
    cost = short["mid"]
    pnl = core.unrealized_pnl(s["credit"], cost, s["contracts"])
    underlying = spread_data.last_close(s["ticker"])[1]
    return {
        "cost": cost, "pnl": pnl, "underlying": underlying,
        "dist_pct": (underlying - s["short_strike"]) / underlying * 100,
        "flags": core.csp_threshold_flags(s["credit"], pnl, s["contracts"]),
        "stale": short["stale"],
    }


def _is_cc(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_COVERED_CALL


def _mark_cc(s: dict) -> dict:
    """Current mid-price mark for an open covered call (option leg only)."""
    quotes = core.quote_map(spread_data.fetch_calls(
        s["ticker"], s["expiry"], strikes=(s["short_strike"],)))
    short = quotes.get(s["short_strike"])
    if short is None:
        return {"error": "期权链里找不到这条腿的有效报价"}
    cost = short["mid"]
    pnl = core.unrealized_pnl(s["credit"], cost, s["contracts"])
    underlying = spread_data.last_close(s["ticker"])[1]
    return {
        "cost": cost, "pnl": pnl, "underlying": underlying,
        "dist_pct": (s["short_strike"] - underlying) / underlying * 100,
        "flags": core.csp_threshold_flags(s["credit"], pnl, s["contracts"]),
        "stale": short["stale"],
    }


def _is_collar(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_COLLAR


def _is_protective_put(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_PROTECTIVE_PUT


def _is_lot_put(s: dict) -> bool:
    """Collar or protective put: the two strategies that carry put_strike/put_debit fields."""
    return _is_collar(s) or _is_protective_put(s)


def _mark_collar(s: dict) -> dict:
    """Signed mid-price mark for a collar or protective put (option legs only, dollars).

    Every needed leg must have a valid quote; a missing leg is an error, never a guess."""
    put = core.quote_map(spread_data.fetch_puts(
        s["ticker"], s["expiry"], strikes=(s["put_strike"],))).get(s["put_strike"])
    call = None
    if _is_collar(s):
        call = core.quote_map(spread_data.fetch_calls(
            s["ticker"], s["expiry"], strikes=(s["call_strike"],))).get(s["call_strike"])
    if put is None or (_is_collar(s) and call is None):
        return {"error": "期权链里找不到这条腿的有效报价"}
    call_mid = call["mid"] if call else 0.0
    pnl = core.collar_unrealized_pnl(s["call_credit"], s["put_debit"], call_mid,
                                     put["mid"], s["contracts"])
    return {
        "pnl": pnl, "underlying": spread_data.last_close(s["ticker"])[1],
        "put_mid": put["mid"], "call_mid": call_mid,
        "stale": put["stale"] or bool(call and call["stale"]),
    }


def _is_condor(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_IRON_CONDOR


def _condor_legs(s: dict) -> dict | None:
    """The four condor legs' quotes (explicit strikes), or None when any leg lacks a valid one."""
    puts = core.quote_map(spread_data.fetch_puts(
        s["ticker"], s["expiry"], strikes=(s["put_short_strike"], s["put_long_strike"])))
    calls = core.quote_map(spread_data.fetch_calls(
        s["ticker"], s["expiry"], strikes=(s["call_short_strike"], s["call_long_strike"])))
    legs = {"put_short": puts.get(s["put_short_strike"]), "put_long": puts.get(s["put_long_strike"]),
            "call_short": calls.get(s["call_short_strike"]),
            "call_long": calls.get(s["call_long_strike"])}
    return None if any(q is None for q in legs.values()) else legs


def _mark_condor(s: dict) -> dict:
    """Mid-price mark for an open iron condor: each side's cost clamped to [0, width].

    `dist_pct` is the distance to the nearer short strike (negative once outside it)."""
    legs = _condor_legs(s)
    if legs is None:
        return {"error": "期权链里找不到这四条腿的有效报价"}
    cost = core.condor_cost_to_close(legs["put_short"]["mid"], legs["put_long"]["mid"],
                                     legs["call_short"]["mid"], legs["call_long"]["mid"], s["width"])
    pnl = core.condor_unrealized_pnl(s["net_credit"], cost, s["contracts"])
    underlying = spread_data.last_close(s["ticker"])[1]
    nearer = min(underlying - s["put_short_strike"], s["call_short_strike"] - underlying)
    return {
        "cost": cost, "pnl": pnl, "underlying": underlying,
        "dist_pct": nearer / underlying * 100,
        "flags": core.threshold_flags(s["net_credit"], s["width"], pnl, s["contracts"]),
        "stale": any(q["stale"] for q in legs.values()),
    }


def _is_bullcall(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_BULL_CALL


def _bullcall_legs(s: dict) -> dict | None:
    """Both call legs' quotes (explicit strikes=(long, short)), or None when either lacks one."""
    long_k, short_k = (s[k] for k in ledger_mod.BULLCALL_STRIKE_KEYS)
    calls = core.quote_map(spread_data.fetch_calls(
        s["ticker"], s["expiry"], strikes=(long_k, short_k)))
    legs = {"long": calls.get(long_k), "short": calls.get(short_k)}
    return None if any(q is None for q in legs.values()) else legs


def _mark_bullcall(s: dict) -> dict:
    """Mid-price mark for an open bull call debit spread: value clamped to [0, width].

    `dist_pct` is the distance above the long strike (negative once below it)."""
    legs = _bullcall_legs(s)
    if legs is None:
        return {"error": "期权链里找不到这两腿的有效报价"}
    value = core.bullcall_close_value(legs["long"]["mid"], legs["short"]["mid"], s["width"])
    pnl, _ = core.bullcall_pnl(s["debit"], value, s["contracts"])
    underlying = spread_data.last_close(s["ticker"])[1]
    if not core._usable(underlying):
        return {"error": "现价无效，算不出距长腿"}
    return {
        "value": value, "pnl": pnl, "underlying": underlying,
        "dist_pct": (underlying - s["long_call_strike"]) / underlying * 100,
        "flags": core.bullcall_threshold_flags(s["debit"], s["width"], pnl, s["contracts"]),
        "stale": any(q["stale"] for q in legs.values()),
        "long_mid": legs["long"]["mid"], "short_mid": legs["short"]["mid"],
        "long_iv": legs["long"]["iv"], "short_iv": legs["short"]["iv"],
    }


def _mark(s: dict) -> dict:
    """Current mid-price mark for an open spread, put, covered call, collar, protective put,
    iron condor or bull call spread."""
    if _is_bullcall(s):
        return _mark_bullcall(s)
    if _is_condor(s):
        return _mark_condor(s)
    if _is_csp(s):
        return _mark_csp(s)
    if _is_cc(s):
        return _mark_cc(s)
    if _is_lot_put(s):
        return _mark_collar(s)
    quotes = core.quote_map(spread_data.fetch_puts(
        s["ticker"], s["expiry"], strikes=(s["short_strike"], s["long_strike"])))
    short, long_ = quotes.get(s["short_strike"]), quotes.get(s["long_strike"])
    if short is None or long_ is None:
        return {"error": "期权链里找不到这两腿的有效报价"}
    cost = core.cost_to_close(short["mid"], long_["mid"])
    pnl = core.unrealized_pnl(s["credit"], cost, s["contracts"])
    underlying = spread_data.last_close(s["ticker"])[1]
    return {
        "cost": cost, "pnl": pnl, "underlying": underlying,
        "dist_pct": (underlying - s["short_strike"]) / underlying * 100,
        "flags": core.threshold_flags(s["credit"], s["width"], pnl, s["contracts"]),
        "stale": short["stale"] or long_["stale"],
    }


def _line(s: dict) -> str:
    if _is_bullcall(s):
        strikes = "/".join(f"{s[k]:g}" for k in ledger_mod.BULLCALL_STRIKE_KEYS)
        return f"#{s['id']} {s['ticker']} {strikes} {ledger_mod.BULLCALL_LABEL} 到期 {s['expiry']}"
    if _is_condor(s):
        strikes = "/".join(f"{s[k]:g}" for k in ledger_mod.CONDOR_STRIKE_KEYS)
        return f"#{s['id']} {s['ticker']} {strikes} 铁鹰 到期 {s['expiry']}"
    if _is_collar(s):
        return (f"#{s['id']} {s['ticker']} {s['put_strike']:g}/{s['call_strike']:g} Collar "
                f"到期 {s['expiry']}")
    if _is_protective_put(s):
        return f"#{s['id']} {s['ticker']} {s['put_strike']:g} 保护性Put 到期 {s['expiry']}"
    if _is_cc(s):
        return f"#{s['id']} {s['ticker']} {s['short_strike']:g} Call 到期 {s['expiry']}"
    if _is_csp(s):
        return f"#{s['id']} {s['ticker']} {s['short_strike']:g} Put 到期 {s['expiry']}"
    return f"#{s['id']} {s['ticker']} {s['short_strike']:g}/{s['long_strike']:g} 到期 {s['expiry']}"


def _save_suggestion(ledger_dir: Path, snap: dict, today: date,
                     notes: tuple[str, ...] = ()) -> Path:
    """Write <snapshot>-<TICKER>.md (to read) then .json (for `open`); a rerun overwrites.

    The json goes last so a json never exists without the md its record points to.
    """
    folder = ledger_dir / SUGGESTIONS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{snap['snapshot_date'].isoformat()}-{snap['ticker']}"
    md = folder / f"{stem}.md"
    md.write_text(spread_explain.render_suggestion_md(snap, notes), encoding="utf-8")
    doc = spread_explain.snapshot_to_json(snap, today.isoformat())
    (folder / f"{stem}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=float) + "\n", encoding="utf-8")
    return md


def _damaged(name: str, ticker: str, why) -> ledger_mod.LedgerError:
    return ledger_mod.LedgerError(f"已保存的候选 {name} 不可用（{why}），请重新运行 suggest {ticker}")


def _number(value, label: str, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 不是数字")


def _check_candidate(c: dict) -> None:
    _number(c["bucket"], "bucket")
    if not isinstance(c["available"], bool):
        raise ValueError("available 不是布尔值")
    if not c["available"]:
        if not isinstance(c["reason"], str):
            raise ValueError("reason 不是文字")
        return
    for key in CANDIDATE_NUMBERS:
        _number(c[key], key)
    for key in ("short_spread_pct", "long_spread_pct"):
        _number(c[key], key, allow_none=True)
    if not isinstance(c["warnings"], list):
        raise ValueError("warnings 不是列表")


def _check_suggestion(doc: dict, ticker: str) -> None:
    """Raise ValueError/KeyError/TypeError when the saved file cannot be trusted by `open`."""
    if doc["ticker"] != ticker:
        raise ValueError(f"文件里的 ticker 是 {doc['ticker']}，对不上")
    date.fromisoformat(doc["expiry"])
    date.fromisoformat(doc["snapshot_date"])
    if doc["earnings"] not in EARNINGS_FLAG:
        raise ValueError("earnings 取值不对")
    _number(doc["spot"], "spot")
    _number(doc["width"], "width")
    if not isinstance(doc["candidates"], list) or not doc["candidates"]:
        raise ValueError("candidates 为空")
    for c in doc["candidates"]:
        _check_candidate(c)


def _load_suggestion(ledger_dir: Path, ticker: str) -> dict | None:
    """Latest saved suggestion for exactly this ticker, or None. A damaged file is an error."""
    ticker = ticker.strip().upper()
    folder = ledger_dir / SUGGESTIONS_DIR
    files = sorted(p for p in folder.glob("*.json")
                   if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker)
    if not files:
        return None
    try:
        doc = json.loads(files[-1].read_text(encoding="utf-8"))
        _check_suggestion(doc, ticker)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise _damaged(files[-1].name, ticker, exc) from exc
    doc["file"] = files[-1].with_suffix(".md").name
    return doc


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    snap = _snapshot(args.ticker, args.width, today)
    print(spread_report.format_suggestions(
        snap["ticker"], snap["snapshot_date"].isoformat(), snap["spot"], snap["expiry"],
        snap["days"], snap["earnings"], snap["width"], snap["candidates"],
        intraday=snap["intraday"], width_info=snap["width_info"]))
    drops = option_labels.stale_drop_line(
        option_labels.stale_drop_total([(snap["ticker"], snap["expiry"])]))
    option_labels.print_notes(option_labels.run_notes(drops))
    md = _save_suggestion(ledger_dir, snap, today, option_labels.run_notes(hint, drops))
    print(f"\n讲解和候选已保存：{md}\n确认后运行 open {snap['ticker']} <档位>，记录的就是这份候选。")
    return 0


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    snap = _load_suggestion(ledger_dir, args.ticker)
    ticker = args.ticker.strip().upper()
    if snap is None:
        print(f"错误：没有 {ticker} 的已保存候选，请先运行 suggest {ticker}", file=sys.stderr)
        return 1
    if today >= date.fromisoformat(snap["expiry"]):
        print(f"错误：{snap['file']} 的到期日 {snap['expiry']} 已过期，请重新运行 suggest {ticker}",
              file=sys.stderr)
        return 1
    if args.width is not None and abs(args.width - snap["width"]) > 1e-9:
        print(f"错误：已保存候选的宽度是 ${snap['width']:g}，与 --width {args.width:g} 不一致，"
              f"请重新运行 suggest {ticker} --width {args.width:g}", file=sys.stderr)
        return 1
    cand = next((c for c in snap["candidates"] if c["bucket"] == args.bucket), None)
    if cand is None:
        raise _damaged(snap["file"], ticker, f"里面没有档位 {args.bucket:.2f}")
    if not cand["available"]:
        print(f"错误：档位 {args.bucket:.2f} 没有合适合约（{cand['reason']}）", file=sys.stderr)
        return 1
    record = {
        "ticker": ticker, "contracts": args.contracts,
        "snapshot_date": snap["snapshot_date"], "expiry": snap["expiry"],
        "underlying_price": snap["spot"], "suggestion_file": snap["file"],
        "short_strike": cand["short_strike"], "long_strike": cand["long_strike"],
        "width": cand["width"], "credit": cand["credit"],
        "short_mid": cand["short_mid"], "long_mid": cand["long_mid"],
        "short_spread_pct": cand["short_spread_pct"], "long_spread_pct": cand["long_spread_pct"],
        "delta_bucket": cand["bucket"], "short_delta": cand["short_delta"],
        "short_iv": cand["short_iv"],
        "earnings_in_window": EARNINGS_FLAG[snap["earnings"]],
        "monday_gap_pct": None, "status": "open",
        "exit_date": None, "exit_cost": None, "exit_underlying": None,
        "pnl": None, "result": None, **option_labels.record_fields(snap),
    }
    path = ledger_dir / "spreads.json"
    led, stored = ledger_mod.add_spread(ledger_mod.load(path), record)
    led = ledger_mod.save_keeping_broker(path, led)
    _write_report(ledger_dir, led, {}, today)
    kind = "盘中" if snap.get("intraday") else "收盘"
    print(f"已记录 {_line(stored)}（快照 {stored['snapshot_date']} {kind}，候选 {stored['suggestion_file']}），权利金 ${stored['credit']:.2f}/股，"
          f"最大盈利 ${stored['credit'] * 100 * stored['contracts']:,.0f}，"
          f"最大亏损 ${(stored['width'] - stored['credit']) * 100 * stored['contracts']:,.0f}")
    for w in cand["warnings"]:
        print(f"  提示：{w}")
    return 0


RESULT_LABELS = {"assigned": "被指派", "called_away": "被行权（股票按行权价卖出）",
                 "protected": "Put 价内（下限生效）", "expired_worthless": "两腿均作废"}


def _reference_price(s: dict) -> float:
    ref = s.get("reference_price")
    if ref is None:
        raise ledger_mod.LedgerError(f"#{s.get('id', '?')} 缺少 reference_price，无法算股票腿")
    return ref


def _cc_reference_price(s: dict) -> float:
    return s.get("reference_price", s.get("cost_basis"))


def _collar_outcome(s: dict, close: float) -> tuple[float, str, None, dict]:
    """Option-leg P&L, label and the always-written stock-leg P&L for collar / protective put."""
    call_strike = s["call_strike"] if _is_collar(s) else None
    result = core.collar_expiry_result(s["put_strike"], call_strike, close)
    pnl = core.collar_expiry_option_pnl(s["net_credit"], s["put_strike"], close, s["contracts"])
    stock = core.collar_stock_leg_pnl(_reference_price(s), close, call_strike, s["contracts"])
    return pnl, result, None, {"stock_leg_pnl": stock}


WIN_LOSS_LABELS = {"win": "盈利", "loss": "亏损"}   # condor and bull call settlements
CONDOR_RESULT_LABELS = WIN_LOSS_LABELS                  # old name, kept as an alias
BREACHED_LABELS = ledger_mod.BREACHED_SIDE_LONG_LABELS


def _condor_outcome(s: dict, close: float) -> tuple[float, str, float, dict]:
    """Iron condor settlement: pnl, win/loss, intrinsic exit cost and the breached side."""
    pnl, result, breached, exit_cost = core.condor_expiry_pnl(
        s["net_credit"], s["put_short_strike"], s["call_short_strike"], s["width"],
        close, s["contracts"])
    return pnl, result, round(exit_cost, 4), {"breached_side": breached}


def _bullcall_captured(s: dict, pnl: float) -> float:
    """Signed fraction of max profit captured, round 4 (`+ 0.0` so it never stores -0.0)."""
    return round(core.bullcall_fraction_captured(pnl, s["debit"], s["width"], s["contracts"]),
                 4) + 0.0


def _bullcall_outcome(s: dict, close: float) -> tuple[float, str, None, dict]:
    """Bull call settlement: no exit_cost; extra = per-share exit_value and fraction captured."""
    value = core.bullcall_expiry_value(s["long_call_strike"], s["width"], close)
    pnl, result = core.bullcall_pnl(s["debit"], value, s["contracts"])
    return pnl, result, None, {"exit_value": round(value, 4) + 0.0,
                               "fraction_of_max_captured": _bullcall_captured(s, pnl)}


def _expiry_outcome(s: dict, close: float) -> tuple[float, str, float | None, dict]:
    """(option pnl, result, exit_cost or None, extra ledger fields) at the expiry-day close."""
    if _is_bullcall(s):
        return _bullcall_outcome(s, close)
    if _is_condor(s):
        return _condor_outcome(s, close)
    if _is_lot_put(s):
        return _collar_outcome(s, close)
    if _is_cc(s):
        pnl, result = core.cc_expiry_pnl(s["short_strike"], s["credit"], close, s["contracts"])
        extra = {}
        if result == "called_away":
            extra["stock_leg_pnl"] = core.cc_stock_leg_pnl(
                s["short_strike"], _cc_reference_price(s), s["contracts"])
        return pnl, result, round(max(close - s["short_strike"], 0.0), 4), extra
    if _is_csp(s):
        pnl, result = core.csp_expiry_pnl(s["short_strike"], s["credit"], close, s["contracts"])
        return pnl, result, round(max(s["short_strike"] - close, 0.0), 4), {}
    pnl, result = core.expiry_pnl(s["short_strike"], s["long_strike"], s["credit"],
                                  close, s["contracts"])
    return pnl, result, round(core.expiry_cost(s["short_strike"], s["long_strike"], close), 4), {}


def _settle_label(s: dict, result: str, extra: dict) -> str:
    """Human label for a settlement result; the condor also names the breached side."""
    if _is_bullcall(s):
        return (f"{WIN_LOSS_LABELS[result]}（每股价值 ${extra['exit_value']:.2f}，"
                f"捕获最大收益的 {extra['fraction_of_max_captured'] * 100:.1f}%）")
    if _is_condor(s):
        return f"{WIN_LOSS_LABELS[result]}（{BREACHED_LABELS[extra['breached_side']]}）"
    label = RESULT_LABELS.get(result, result)
    if result == "called_away" and (s.get("lot_ref") or {}).get("source") == "real":
        label += spread_report.REAL_UNCHANGED_NOTE
    return label


def _settle(led: dict, s: dict, today: date) -> tuple[dict, str, str]:
    """Return (ledger, kind, message); kind is pending | expiry_day | retry | settled."""
    expiry = date.fromisoformat(s["expiry"])
    if today < expiry:
        return led, "pending", ""
    if today == expiry:
        return led, "expiry_day", "到期日当天，次日或之后再运行 status 结算"
    close = spread_data.close_on(s["ticker"], expiry)
    if close is None:
        return led, "retry", "到期日收盘价取不到，保持未结算，稍后重试"
    pnl, result, exit_cost, extra = _expiry_outcome(s, close)
    if exit_cost is not None:
        extra["exit_cost"] = exit_cost
    led = ledger_mod.update_spread(
        led, s["id"], status="expired", exit_date=s["expiry"], exit_underlying=close,
        pnl=pnl, result=result, **extra)
    label = _settle_label(s, result, extra)
    pnl_text = f"盈亏 {spread_report._money(pnl)}"
    if _is_lot_put(s):
        pnl_text = (f"期权腿盈亏 {spread_report._money(pnl)}，"
                    f"股票腿 {spread_report._money(extra['stock_leg_pnl'])}")
    return led, "settled", f"已到期结算：收盘 ${close:,.2f}，{label}，{pnl_text}"


def _print_sync(ledger_dir: Path) -> None:
    """Wheel sync after the ledger is saved: assigned puts -> paper lots, settled calls -> lots."""
    try:
        messages = paper_shares.sync(ledger_dir)
    except (ledger_mod.LedgerError, KeyError, TypeError, ValueError) as exc:
        print(f"警告：模拟持股同步失败：{exc}", file=sys.stderr)
        return
    for msg in messages:
        print(msg)


def _lot_put_status_line(s: dict, m: dict) -> str:
    """Status text for collar / protective put: signed P&L plus the floor and cap they set."""
    band = f"下限 {s['put_strike']:g}" + (f"，上限 {s['call_strike']:g}" if _is_collar(s) else "")
    return (f"{_line(s)}：浮盈亏 {spread_report._money(m['pnl'])}，{band}，现价 {m['underlying']:g}"
            + ("，报价陈旧" if m["stale"] else ""))


def _status_texts(s: dict) -> tuple[str, str, str]:
    """(stop tip, take-profit tip, distance label) for a flagged status line."""
    if _is_bullcall(s):
        return (*spread_report.BULLCALL_THRESHOLD_LABELS, "距长腿")
    if _is_condor(s):
        return "🔴 已到止损线", "🟢 已到止盈线", "距较近短腿"
    if _is_csp(s) or _is_cc(s):
        return (*spread_report.threshold_labels(True), "距短腿")
    return "🔴 已到止损线", "🟢 已到止盈线", "距短腿"


def _mark_status_line(s: dict, m: dict) -> str:
    """One status line for an open record's mark (or its error)."""
    if "error" in m:
        return f"{_line(s)}：{m['error']}"
    if _is_lot_put(s):
        return _lot_put_status_line(s, m)
    stop_text, tp_text, dist_label = _status_texts(s)
    tips = [t for t, hit in ((stop_text, m["flags"]["stop"]),
                             (tp_text, m["flags"]["take_profit"]),
                             ("报价陈旧", m["stale"])) if hit]
    return (f"{_line(s)}：浮盈亏 {spread_report._money(m['pnl'])}，{dist_label} {m['dist_pct']:.1f}%"
            + (f"，{'；'.join(tips)}" if tips else ""))


def cmd_status(args, ledger_dir: Path, today: date) -> int:
    path = ledger_dir / "spreads.json"
    led = loaded = ledger_mod.load(path)
    marks: dict = {}
    for s in [x for x in led["spreads"] if x["status"] == "open"]:
        try:
            led, kind, msg = _settle(led, s, today)
            if kind in ("settled", "retry"):
                print(f"{_line(s)}：{msg}")
                if kind == "retry":
                    marks[s["id"]] = {"error": msg}
                continue
            if kind == "expiry_day":
                print(f"{_line(s)}：{msg}")
            marks[s["id"]] = _mark(s)
        except spread_data.SourceUnavailable:
            raise  # link lost: abort instead of pretending each position had its own error
        except (spread_data.DataError, ledger_mod.LedgerError) as exc:
            marks[s["id"]] = {"error": str(exc)}  # one bad record must not abort the rest
        print(_mark_status_line(s, marks[s["id"]]))
    for s in [x for x in led["spreads"] if x.get("monday_gap_pct") is None]:
        if today <= date.fromisoformat(s["snapshot_date"]):
            continue
        try:
            gap = spread_data.monday_gap_pct(s["ticker"], date.fromisoformat(s["snapshot_date"]),
                                             s["underlying_price"])
        except spread_data.SourceUnavailable:
            raise
        except spread_data.DataError:
            continue
        if gap is not None:
            led = ledger_mod.update_spread(led, s["id"], monday_gap_pct=gap)
    if led != loaded:
        led = ledger_mod.save_keeping_broker(path, led)
    _print_sync(ledger_dir)
    _write_report(ledger_dir, led, marks, today)
    print(f"报告：{ledger_dir / 'report.md'}")
    print(f"网页版：{ledger_dir / 'report.html'}")
    return 0


def _bullcall_close_values(s: dict, m: dict) -> tuple[float, str, dict, str]:
    """Sell the debit spread at the two mids: exit value, both mids, IVs when known."""
    pnl, result = core.bullcall_pnl(s["debit"], m["value"], s["contracts"])
    fields = {"exit_value": round(m["value"], 4) + 0.0,
              "exit_long_call_mid": round(m["long_mid"], 4),
              "exit_short_call_mid": round(m["short_mid"], 4)}
    for key, iv in (("exit_long_call_iv", m["long_iv"]), ("exit_short_call_iv", m["short_iv"])):
        if iv is not None:
            fields[key] = iv
    fields["fraction_of_max_captured"] = _bullcall_captured(s, pnl)
    return pnl, result, fields, (f"平仓价值 ${m['value']:.2f}/股（长腿 ${m['long_mid']:.2f}、"
                                 f"短腿 ${m['short_mid']:.2f}）")


def _close_values(s: dict, m: dict) -> tuple[float, str, dict, str]:
    """(pnl, result, extra ledger fields, cost text) for an early close at the current mark."""
    if _is_bullcall(s):
        return _bullcall_close_values(s, m)
    if _is_condor(s):
        pnl, result = core.condor_close_pnl(s["net_credit"], m["cost"], s["contracts"])
        return pnl, result, {"exit_cost": round(m["cost"], 4)}, f"四腿平仓成本 ${m['cost']:.2f}/股"
    if _is_lot_put(s):
        pnl = core.collar_close_pnl(s["call_credit"], s["put_debit"], m["call_mid"],
                                    m["put_mid"], s["contracts"])
        fields = {"exit_put_mid": round(m["put_mid"], 4), "exit_call_mid": round(m["call_mid"], 4)}
        call_text = f"、Call ${m['call_mid']:.2f}" if _is_collar(s) else ""
        return pnl, "closed", fields, f"平仓时 Put ${m['put_mid']:.2f}{call_text}/股"
    pnl, result = core.close_pnl(s["credit"], m["cost"], s["contracts"])
    return pnl, result, {"exit_cost": round(m["cost"], 4)}, f"平仓成本 ${m['cost']:.2f}/股"


def cmd_close(args, ledger_dir: Path, today: date) -> int:
    path = ledger_dir / "spreads.json"
    led = ledger_mod.load(path)
    s = ledger_mod.get_spread(led, args.id)
    if s["status"] != "open":
        print(f"错误：{_line(s)} 已经是 {s['status']}，不能再平仓", file=sys.stderr)
        return 1
    if today > date.fromisoformat(s["expiry"]):
        print(f"错误：{_line(s)} 已过到期日，请运行 status 结算，不能再用报价平仓", file=sys.stderr)
        return 1
    m = _mark(s)
    if "error" in m:
        print(f"错误：{m['error']}", file=sys.stderr)
        return 1
    pnl, result, fields, cost_text = _close_values(s, m)
    led = ledger_mod.update_spread(
        led, s["id"], status="closed_early", exit_date=today.isoformat(),
        exit_underlying=m["underlying"], pnl=pnl, result=result, **fields)
    led = ledger_mod.save_keeping_broker(path, led)
    _write_report(ledger_dir, led, {}, today)
    print(f"已提前平仓 {_line(s)}：{cost_text}，盈亏 {spread_report._money(pnl)}（{result}）")
    _print_sync(ledger_dir)
    return 0


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("必须是 ≥1 的整数")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return value


def _ticker_arg(text: str) -> str:
    value = text.strip().upper()
    if not re.fullmatch(TICKER_PATTERN, value):
        raise argparse.ArgumentTypeError("ticker 只能是字母、数字、点和连字符，最长 10 位")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spread_sim", description="Bull put spread 纸上交易模拟器")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="周末出 3 档候选（不写账本）")
    s.add_argument("ticker", type=_ticker_arg)
    s.add_argument("--width", type=_positive_float, default=None,
                   help="省略则自动推荐（约一周预期波动的 15%%，取整到行权价间距）")
    o = sub.add_parser("open", help="记录所选档位")
    o.add_argument("ticker", type=_ticker_arg)
    o.add_argument("bucket", type=float, choices=core.DELTA_BUCKETS)
    o.add_argument("--contracts", type=_positive_int, default=1)
    o.add_argument("--width", type=_positive_float, default=None,
                   help="只用于核对，必须与已保存候选的宽度一致；省略则沿用已保存的")
    sub.add_parser("status", help="查看未平仓浮盈亏，到期自动结算")
    c = sub.add_parser("close", help="提前平仓")
    c.add_argument("id", type=int)
    return p


COMMANDS = {"suggest": cmd_suggest, "open": cmd_open, "status": cmd_status, "close": cmd_close}


def default_ledger_dir() -> Path:
    """Ledger folder for the current data source; read per run so a bad setting is a clean error."""
    return option_settings.paper_dir(REPO)


def resolve_ledger_dir(arg: str | None) -> Path:
    return Path(arg) if arg else default_ledger_dir()


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        option_labels.print_console_header()
        ledger_dir = resolve_ledger_dir(args.ledger_dir)
        return COMMANDS[args.cmd](args, ledger_dir, today or date.today())
    except (spread_data.DataError, ledger_mod.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
