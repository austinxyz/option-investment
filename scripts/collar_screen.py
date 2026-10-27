#!/usr/bin/env python3
"""Collar / protective-put paper screen — CLI.

    collar_screen.py suggest [--dte {monthly,long}]   # screen held lots, save candidates (no ledger write)
    collar_screen.py open TICKER {90,85,80} [--lot real|ID] [--contracts N] [--put-only]
    collar_screen.py status | close ID                # shared with spread_sim

Writes suggestions/<snapshot>-<TICKER>-collar-{real|lot<ID>}.{md,json} and collar-screen/<snapshot>.{md,json}.
Paper only: the real holdings are read through holdings.real_lots and never written.
See docs/superpowers/specs/2026-10-25-collar-design.md (sections 5, 6, 10).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import call_screen as cs
import collar_screen_render as render
import collar_screen_rules as rules
import holdings
import option_labels
import option_settings
import paper_shares
import put_screen_rules
import spread_data
import spread_ledger as ledger_mod
import spread_sim

SUGGESTIONS_DIR = "suggestions"
SUMMARY_DIR = "collar-screen"
TICKER_PATTERN = spread_sim.TICKER_PATTERN
SUGGESTION_FILE = re.compile(
    rf"^(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})-(?P<ticker>{TICKER_PATTERN})-collar-"
    r"(?P<lot>real|lot\d+)\.json$")
SUMMARY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
DEFAULT_DTE = "monthly"
DTE_WINDOWS = {"monthly": (30, 60), "long": (90, 180)}
NO_EXPIRY_REASON = {"monthly": "没有 30–60 天内的到期日", "long": "没有 90–180 天内的到期日"}
PUT_WINDOW_PCT = 0.25   # fallback only: the tier strikes are requested explicitly
PUT_WINDOW_LIMIT = 60
TIER_PERCENTS = tuple(round(t * 100) for t in rules.TIERS)
EARNINGS_FLAG = spread_sim.EARNINGS_FLAG
TOP_NUMBERS = ("spot", "shares", "cost_basis", "original_cost", "reference_price")
VARIANT_NUMBERS = ("put_strike", "put_debit", "call_credit", "net_credit", "floor")
VARIANT_KEYS = ("collar", "put_only")
REJECT_KEYS = {"collar": "collar_reject", "put_only": "put_only_reject"}


def _rules_doc(dte_mode: str) -> dict:
    doc = {"dte_mode": dte_mode, "window_days": list(DTE_WINDOWS[dte_mode]),
           "tiers": list(TIER_PERCENTS), "max_spread_pct": put_screen_rules.MAX_SPREAD_PCT,
           "put_window_pct": PUT_WINDOW_PCT, "put_window_limit": PUT_WINDOW_LIMIT}
    spread = option_settings.spread_kwargs()  # empty in IBKR: the document keeps its old keys
    if spread:
        doc["max_spread_pct"] = spread["spread_limit"]
        doc["spread_warn_pct"] = spread["spread_warn"]
    return doc


def pick_collar_expiry(expirations: list[str], snapshot: date, dte_mode: str) -> str | None:
    """Nearest expiry whose days-to-expiry fall inside the mode's window."""
    lo, hi = DTE_WINDOWS[dte_mode]
    inside = [((date.fromisoformat(e) - snapshot).days, e) for e in expirations]
    inside = [de for de in inside if lo <= de[0] <= hi]
    return min(inside)[1] if inside else None


def tier_strikes(chain: list[float], spot: float) -> tuple[float, ...]:
    """Every chain strike inside each tier's acceptable band [spot*(tier-5pts), spot*tier].

    The chain list is the union over expiries, so one expiry may lack the tier's exact strike;
    asking for the whole band lets pick_put take the highest strike that really came back."""
    wanted: set[float] = set()
    for tier in rules.TIERS:
        lo, hi = spot * (tier - rules.MAX_TIER_MISS), spot * tier
        wanted.update(float(k) for k in chain if lo - 1e-9 <= k <= hi + 1e-9)
    return tuple(sorted(wanted, reverse=True))


def _fetch_market(ticker: str) -> dict:
    snap, close = spread_data.last_close(ticker)
    return {"snapshot": snap, "close": close, "expirations": spread_data.expirations(ticker)}


def _fetch_all(lots: list, avail_put: dict) -> tuple[dict, dict]:
    """ticker -> market data, and ticker -> failure text; one fetch per ticker."""
    got: dict = {}
    failed: dict = {}
    for ticker in dict.fromkeys(l.ticker for l in lots if avail_put[l.key] >= 1):
        try:
            got[ticker] = _fetch_market(ticker)
        except spread_data.SourceUnavailable:
            raise  # the link is gone: abort the whole run
        except Exception as exc:  # an odd frame must not stop the screen
            failed[ticker] = spread_data.fetch_failure_reason(exc)
    return got, failed


def _fetch_puts(ticker: str, expiry: str, spot: float) -> list[dict]:
    """Request the tier strikes by name: a nearest-N window cannot reach 20% OTM on dense chains."""
    targets = tier_strikes(spread_data.chain_strikes(ticker), spot)
    return spread_data.fetch_puts(ticker, expiry, strikes=targets,
                                  window_pct=PUT_WINDOW_PCT, window_limit=PUT_WINDOW_LIMIT)


def _market_fns(snapshot: date) -> dict:
    return {
        "earnings": cs._memo(lambda t, e: spread_data.earnings_status(
            t, snapshot, date.fromisoformat(e))),
        "ex_div": cs._memo(lambda t, e: spread_data.ex_dividend_in_window(
            t, snapshot, date.fromisoformat(e))),
        "puts": cs._memo(_fetch_puts),
        "calls": cs._memo(lambda t, e, spot: spread_data.fetch_calls(t, e)),
    }


def _variant(c: dict, spot: float) -> dict:
    put, call = c["put"], c["call"]
    return {
        "put_strike": put["strike"], "put_debit": put["mid"], "put_spread_pct": put["spread_pct"],
        "put_stale": put["stale"], "put_delta": put["delta"],
        "put_pct_below_spot": (spot - put["strike"]) / spot * 100,
        "call_strike": call["strike"] if call else None,
        "call_credit": call["mid"] if call else 0.0,
        "call_spread_pct": call["spread_pct"] if call else None,
        "call_delta": call["delta"] if call else None,
        "net_credit": c["net_credit"], "floor": c["floor"], "cap": c["cap"],
        "cap_cost": c["cap_cost"], "contracts_max": c["contracts"],
        "warnings": list(c["warnings"]),
    }


def _put_in(entry: dict, key: str, item, spot: float) -> None:
    if isinstance(item, rules.Reject):
        entry[REJECT_KEYS[key]] = item.reason
    else:
        entry[key] = _variant(item, spot)


def _tier_entries(res: dict, spot: float) -> list[dict]:
    """One entry per tier; a covered lot has no collar variants, so each says why."""
    out = []
    for i, percent in enumerate(TIER_PERCENTS):
        entry = {"tier": percent, "collar": None, "collar_reject": None,
                 "put_only": None, "put_only_reject": None}
        _put_in(entry, "put_only", res["put_only"][i], spot)
        if i < len(res["tiers"]):
            _put_in(entry, "collar", res["tiers"][i], spot)
        else:  # covered lot (no collar variants) or a missing entry: not offered
            entry["collar_reject"] = res["note"] or "未提供"
        out.append(entry)
    return out


def _candidate(lot, res: dict, market: dict, snapshot: date, expiry: str, earnings: str,
               ex_div: date | None, avail: tuple[int, int], dte_mode: str) -> dict:
    return {
        "ticker": lot.ticker, "source": lot.source, "lot_id": lot.lot_id, "label": lot.label,
        "shares": lot.shares, "contracts_available": avail[0],
        "contracts_available_for_put": avail[1], "cost_basis": lot.cost_basis,
        "original_cost": lot.original_cost, "reference_price": lot.reference_price,
        "cost_estimated": lot.cost_estimated, "accounts": lot.accounts,
        "snapshot_date": snapshot.isoformat(), "spot": market["close"], "expiry": expiry,
        "days": (date.fromisoformat(expiry) - snapshot).days, "dte_mode": dte_mode,
        "earnings": earnings, "ex_dividend": ex_div.isoformat() if ex_div else None,
        "note": res["note"], "tiers": _tier_entries(res, market["close"]),
    }


def _evaluate(lot, market: dict, snapshot: date, avail: tuple[int, int], dte_mode: str, fns: dict):
    expiry = pick_collar_expiry(market["expirations"], snapshot, dte_mode)
    if expiry is None:
        return rules.Reject(lot.label, NO_EXPIRY_REASON[dte_mode])
    t, spot = lot.ticker, market["close"]
    try:
        earnings, ex_div = fns["earnings"](t, expiry), fns["ex_div"](t, expiry)
        puts, calls = fns["puts"](t, expiry, spot), fns["calls"](t, expiry, spot)
        res = rules.evaluate_lot(
            lot, snapshot=snapshot, spot=spot, puts=puts, calls=calls, expiry=expiry,
            earnings=earnings, ex_div=ex_div, available_for_put=avail[1], available_for_call=avail[0],
            **option_settings.spread_kwargs())
    except spread_data.SourceUnavailable:
        raise
    except spread_data.DataError as exc:
        return rules.Reject(lot.label, f"取不到期权链：{exc}")
    except Exception as exc:  # any other trouble becomes a reject, never an abort
        return rules.Reject(lot.label, spread_data.fetch_failure_reason(exc))
    if isinstance(res, rules.Reject):
        return res
    return _candidate(lot, res, market, snapshot, expiry, earnings, ex_div, avail, dte_mode)


def _screen(lots: list, led: dict, today: date, dte_mode: str) -> tuple[list, list, date]:
    avail_call = {l.key: holdings.contracts_available(l, led) for l in lots}
    avail_put = {l.key: holdings.contracts_available_for_put(l, led) for l in lots}
    got, failed = _fetch_all(lots, avail_put)
    snapshot = (Counter(m["snapshot"] for m in got.values()).most_common(1)[0][0]
                if got else today)
    fns = _market_fns(snapshot)
    candidates: list[dict] = []
    rejects: list = []
    for lot in lots:
        if avail_put[lot.key] < 1:
            res = rules.Reject(lot.label, rules.REASON_SHORT_SHARES)
        elif lot.ticker in failed:
            res = rules.Reject(lot.label, failed[lot.ticker])
        else:
            res = _evaluate(lot, got[lot.ticker], snapshot,
                            (avail_call[lot.key], avail_put[lot.key]), dte_mode, fns)
        (rejects if isinstance(res, rules.Reject) else candidates).append(res)
    return candidates, rejects, snapshot


def _remove_stale_candidates(folder: Path, stamp: str) -> None:
    """Delete this snapshot's earlier collar candidate files (call/put files are untouched)."""
    pattern = re.compile(
        rf"^{re.escape(stamp)}-{TICKER_PATTERN}-collar-(real|lot\d+)\.(md|json)$")
    for p in folder.iterdir():
        if pattern.match(p.name):
            p.unlink()


def _write_outputs(ledger_dir: Path, candidates: list, rejects: list, stamp: str,
                   today: date, intraday: bool, dte_mode: str,
                   notes: tuple[str, ...] = ()) -> Path:
    generated = today.isoformat()
    sug_dir, out_dir = ledger_dir / SUGGESTIONS_DIR, ledger_dir / SUMMARY_DIR
    sug_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_candidates(sug_dir, stamp)
    for c in candidates:  # md first, json last so a json never lacks its md
        base = f"{stamp}-{c['ticker']}-collar-{cs._lot_slug(c)}"
        (sug_dir / f"{base}.md").write_text(render.render_lot_md(c, intraday), encoding="utf-8")
        cs._write_json(sug_dir / f"{base}.json", render.candidate_to_json(c, generated, intraday))
    summary = out_dir / f"{stamp}.md"
    summary.write_text(option_labels.add_notes(render.render_summary_md(
        candidates, rejects, stamp, generated, intraday, dte_mode=dte_mode), notes),
        encoding="utf-8")
    cs._write_json(out_dir / f"{stamp}.json", render.summary_to_json(
        candidates, rejects, stamp, generated, intraday, _rules_doc(dte_mode)))
    return summary


def cmd_suggest(args, ledger_dir: Path, today: date) -> int:
    spread_data.ensure_source()  # nothing is written if the link is down
    hint = option_labels.market_closed_hint(spread_data.market_open_now)
    option_labels.print_notes(option_labels.run_notes(hint))
    # sync is idempotent bookkeeping (assigned puts, settled calls); it only runs after the gateway check
    for msg in paper_shares.sync(ledger_dir):
        print(msg)
    led = ledger_mod.load(ledger_dir / "spreads.json")
    lots = cs._all_lots(ledger_dir)
    seed = option_labels.demo_seed_hint(lots)
    if seed:
        print(seed)
    candidates, rejects, snapshot = _screen(lots, led, today, args.dte)
    intraday = snapshot == today and spread_data.market_open_now()
    drops = option_labels.stale_drop_line(
        option_labels.stale_drop_total((c["ticker"], c["expiry"]) for c in candidates))
    summary = _write_outputs(ledger_dir, candidates, rejects, snapshot.isoformat(),
                             today, intraday, args.dte, option_labels.run_notes(hint, drops))
    option_labels.print_notes(option_labels.run_notes(drops))
    print(f"汇总已保存：{summary}")
    print(f"候选 {len(candidates)} 个，剔除 {len(rejects)} 个。"
          "确认后运行 open <TICKER> {90,85,80} [--lot real|<ID>] [--put-only]，"
          "记录的就是已保存的候选。")
    return 0


def _check_variant(v: dict, where: str) -> None:
    """Raise ValueError/KeyError/TypeError when a saved variant cannot be trusted."""
    for key in VARIANT_NUMBERS:
        spread_sim._number(v[key], f"{where}.{key}")
    spread_sim._number(v["call_strike"], f"{where}.call_strike", allow_none=True)
    if abs(v["net_credit"] - (v["call_credit"] - v["put_debit"])) > 1e-6:
        raise ValueError(f"{where} 的 net_credit 对不上两条腿")
    if (v["call_strike"] is None) != (v["call_credit"] == 0 and v["cap"] is None):
        raise ValueError(f"{where} 的 Call 腿前后矛盾")
    if not isinstance(v["warnings"], list):
        raise ValueError(f"{where}.warnings 不是列表")


def _check_tier(entry: dict) -> None:
    if entry["tier"] not in TIER_PERCENTS:
        raise ValueError("档位取值不对")
    for key in VARIANT_KEYS:
        if entry[key] is not None:
            _check_variant(entry[key], f"{entry['tier']}.{key}")
        elif not isinstance(entry[REJECT_KEYS[key]], str):
            raise ValueError("被剔除的档位缺少原因")


def _check_candidate(doc: dict, ticker: str, slug: str) -> None:
    """Raise ValueError/KeyError/TypeError when the saved file cannot be trusted by `open`."""
    if doc["strategy"] != "collar" or doc["version"] != 1:
        raise ValueError("strategy 或 version 不对")
    if doc["ticker"] != ticker:
        raise ValueError(f"文件里的 ticker 是 {doc['ticker']}，对不上")
    if cs._lot_slug(doc) != slug:
        raise ValueError("文件名里的持仓和内容对不上")
    date.fromisoformat(doc["expiry"])
    date.fromisoformat(doc["snapshot_date"])
    if doc["earnings"] not in EARNINGS_FLAG or not isinstance(doc["source"], str):
        raise ValueError("earnings 或 source 取值不对")
    for key in TOP_NUMBERS:
        spread_sim._number(doc[key], key)
    if not isinstance(doc["tiers"], list):
        raise ValueError("tiers 不是列表")
    for entry in doc["tiers"]:
        _check_tier(entry)


def _saved_files(ledger_dir: Path, ticker: str) -> list[tuple[str, str, Path]]:
    """(stamp, lot slug, path) for the ticker's saved candidates, oldest first."""
    folder = ledger_dir / SUGGESTIONS_DIR
    if not folder.is_dir():
        return []
    found = [(m.group("stamp"), m.group("lot"), p) for p in folder.glob("*.json")
             if (m := SUGGESTION_FILE.match(p.name)) and m.group("ticker") == ticker]
    return sorted(found, key=lambda f: (f[0], f[2].name))


def _summary_files(ledger_dir: Path) -> list[Path]:
    folder = ledger_dir / SUMMARY_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.json") if SUMMARY_FILE.match(p.name))


def _variant_of(entry: dict, put_only: bool) -> tuple[dict | None, str | None]:
    key = "put_only" if put_only else "collar"
    return entry[key], entry[REJECT_KEYS[key]]


def _check_not_rejected(ledger_dir: Path, stamp: str, doc: dict, tier: int, put_only: bool) -> None:
    """Refuse when the newest screen (not older than the candidate) no longer offers this tier."""
    files = _summary_files(ledger_dir)
    if not files or files[-1].name[:-len(".json")] < stamp:
        return
    newest = files[-1]
    try:
        summary = json.loads(newest.read_text(encoding="utf-8"))
        key = (doc["ticker"], doc["source"], doc["lot_id"])
        mine = next((c for c in summary["candidates"]
                     if (c["ticker"], c["source"], c["lot_id"]) == key), None)
        reasons = {r["ticker"]: r["reason"] for r in summary["rejects"]}
        offered = None if mine is None else _variant_of(
            next(t for t in mine["tiers"] if t["tier"] == tier), put_only)
    except (json.JSONDecodeError, KeyError, TypeError, StopIteration) as exc:
        raise cs._damaged(newest.name, exc) from exc
    if offered is not None and offered[0] is not None:
        return
    label = doc.get("label", doc["ticker"])
    why = offered[1] if offered else reasons.get(label, "本次筛选没有选出它")
    raise ledger_mod.LedgerError(
        f"{newest.name[:-len('.json')]} 的筛选里 {label} {tier}% 档被剔除：{why}，请重新运行 suggest")


def _load_candidate(ledger_dir: Path, args) -> dict:
    ticker = args.ticker.strip().upper()
    files = _saved_files(ledger_dir, ticker)
    if not files:
        raise ledger_mod.LedgerError(f"没有 {ticker} 的已保存候选，请先运行 suggest")
    stamp, slug, path = cs._pick_file(files, ticker, args.lot)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _check_candidate(doc, ticker, slug)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        raise cs._damaged(path.name, exc) from exc
    _check_not_rejected(ledger_dir, stamp, doc, args.tier, args.put_only)
    doc["file"] = path.name[:-len(".json")] + ".md"
    return doc


def _record(doc: dict, v: dict, tier: int, contracts: int, lot, put_only: bool) -> dict:
    """Explicit-field record (spec 10.1): no credit / short_strike / long_strike keys."""
    covered = contracts * 100
    return {
        "ticker": doc["ticker"],
        "strategy": (ledger_mod.STRATEGY_PROTECTIVE_PUT if put_only
                     else ledger_mod.STRATEGY_COLLAR),
        "contracts": contracts, "snapshot_date": doc["snapshot_date"], "expiry": doc["expiry"],
        "underlying_price": doc["spot"], "suggestion_file": doc["file"],
        "put_strike": v["put_strike"], "put_debit": v["put_debit"],
        "call_strike": None if put_only else v["call_strike"],
        "call_credit": 0 if put_only else v["call_credit"],
        "net_credit": round((0 if put_only else v["call_credit"]) - v["put_debit"], 6),
        "covered_shares": covered, "cost_basis": lot.cost_basis,
        "reference_price": lot.reference_price,
        "lot_ref": {"source": doc["source"], "lot_id": doc["lot_id"], "ticker": doc["ticker"]},
        "dte_mode": doc.get("dte_mode", DEFAULT_DTE),
        "tier": tier,
        "earnings_in_window": EARNINGS_FLAG[doc["earnings"]], "status": "open",
        "exit_date": None, "exit_underlying": None, "pnl": None, "result": None,
        **option_labels.record_fields(doc),
    }


def _live_lot(ledger_dir: Path, doc: dict):
    key = (doc["source"], doc["lot_id"], doc["ticker"])
    lot = next((l for l in cs._all_lots(ledger_dir) if l.key == key), None)
    if lot is None:
        raise ledger_mod.LedgerError(
            f"{doc['ticker']} 的持仓 {cs._lot_slug(doc)} 已不存在，请重新运行 suggest")
    return lot


def _offered_variant(doc: dict, tier: int, put_only: bool) -> dict:
    entry = next((t for t in doc["tiers"] if t["tier"] == tier), None)
    variant, why = (None, "候选里没有这一档") if entry is None else _variant_of(entry, put_only)
    if variant is None:
        raise ledger_mod.LedgerError(f"{doc['file']} 的 {tier}% 档没有可记录的方案：{why}")
    return variant


def _check_coverage(lot, led: dict, contracts: int, put_only: bool) -> None:
    """A collar sells calls so open calls count; a protective put only needs the shares."""
    available = (holdings.contracts_available_for_put(lot, led) if put_only
                 else holdings.contracts_available(lot, led))
    if contracts > available:
        raise ledger_mod.LedgerError(
            f"{lot.ticker} 这份持仓现在最多可再{'买' if put_only else '卖'} {available} 张"
            f"{'保护性 Put' if put_only else ' Call'}，不能记录 {contracts} 张")


def _validate_record(rec: dict, lot) -> None:
    if rec["covered_shares"] != 100 * rec["contracts"] or rec["covered_shares"] > lot.shares:
        raise ledger_mod.LedgerError(
            f"覆盖股数 {rec['covered_shares']} 与 {rec['contracts']} 张或持股 {lot.shares:g} 股对不上")


def cmd_open(args, ledger_dir: Path, today: date) -> int:
    doc = _load_candidate(ledger_dir, args)
    if today >= date.fromisoformat(doc["expiry"]):
        raise ledger_mod.LedgerError(
            f"{doc['file']} 的到期日 {doc['expiry']} 已过期，请重新运行 suggest")
    variant = _offered_variant(doc, args.tier, args.put_only)
    path = ledger_dir / "spreads.json"
    led = ledger_mod.load(path)
    lot = _live_lot(ledger_dir, doc)
    _check_coverage(lot, led, args.contracts, args.put_only)
    record = _record(doc, variant, args.tier, args.contracts, lot, args.put_only)
    _validate_record(record, lot)
    led, stored = ledger_mod.add_spread(led, record)
    ledger_mod.save_keeping_broker(path, led)
    spread_sim._write_report(ledger_dir, led, {}, today)
    _print_opened(stored, doc, variant)
    return 0


def _print_opened(stored: dict, doc: dict, variant: dict) -> None:
    kind = "盘中" if doc.get("intraday") else "收盘"
    cap = "无上限" if stored["call_strike"] is None else f"${stored['call_strike']:g}"
    print(f"已记录 {spread_sim._line(stored)}（快照 {stored['snapshot_date']} {kind}，"
          f"候选 {stored['suggestion_file']}），净权利金 {stored['net_credit']:+.2f}/股（负数为净支出），"
          f"锁定区间 ${stored['put_strike']:g} ～ {cap}，覆盖 {stored['covered_shares']:g} 股")
    for w in variant["warnings"]:
        print(f"  提示：{w}")


def cmd_status(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_status(args, ledger_dir, today)


def cmd_close(args, ledger_dir: Path, today: date) -> int:
    return spread_sim.cmd_close(args, ledger_dir, today)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="collar_screen", description="Collar / 保护性 Put 筛选与纸上交易")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="筛选持仓，保存候选（不写账本）")
    s.add_argument("--dte", choices=tuple(DTE_WINDOWS), default=DEFAULT_DTE,
                   help="到期类型：monthly = 30–60 天最近到期（默认），long = 90–180 天")
    o = sub.add_parser("open", help="记录已保存的候选")
    o.add_argument("ticker", type=spread_sim._ticker_arg)
    o.add_argument("tier", type=int, choices=TIER_PERCENTS, help="下限档位：现价的 90 / 85 / 80 %%")
    o.add_argument("--lot", type=cs._lot_arg, default=None, help="real 或假设持仓编号")
    o.add_argument("--contracts", type=spread_sim._positive_int, default=1)
    o.add_argument("--put-only", action="store_true", help="只记保护性 Put（忽略 Call 腿）")
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
    except (spread_data.DataError, ledger_mod.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
