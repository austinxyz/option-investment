#!/usr/bin/env python3
"""Weekly review of open paper-trading puts (read-only on the ledger).

    put_review.py        # prints a table, saves reviews/<today>.{md,json}

Advice comes from the pure rules in put_review_rules.py. Nothing is closed
automatically. See docs/superpowers/specs/2026-10-18-put-review-design.md.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import option_labels
import option_settings
import put_review_rules as rules_mod
import spread_core as core
import spread_data
import spread_ledger as ledger_mod
import spread_sim
from spread_ledger import (BEARPUT_STRIKE_KEYS, BULLCALL_STRIKE_KEYS, CONDOR_STRIKE_KEYS,
                           RR_STRIKE_KEYS)

REVIEWS_DIR = "reviews"
ADVICE_LABEL = {
    "close": "平仓", "hold": "继续持有", "attention": "需要关注", "await_settlement": "等待结算",
}
ADVICE_ORDER = ("close", "hold", "attention", "await_settlement")
NO_QUOTE = "期权链里找不到这条腿的有效报价"
NO_CONDOR_QUOTE = "期权链里找不到这四条腿的有效报价"
NO_BULLCALL_QUOTE = "期权链里找不到这两腿的有效报价"
NO_RR_QUOTE = "期权链里找不到这两腿的有效报价"
# Rule 0 prefixes these with "取不到报价："; the shared spread_report text already carries it.
NO_BEARCALL_QUOTE = NO_BEARPUT_QUOTE = "期权链里找不到这两腿的有效报价"
NO_LONGPUT_QUOTE = NO_QUOTE


def _is_lot_put(s: dict) -> bool:
    return ledger_mod.strategy_of(s) in rules_mod.LOT_PUT_STRATEGIES


def _neutral_lot_put(s: dict, days_left: int) -> dict:
    """Collar / protective put row: own strike fields, no credit / short_strike / long_strike."""
    return {
        "id": s["id"], "strategy": ledger_mod.strategy_of(s), "ticker": s["ticker"],
        "expiry": s["expiry"], "put_strike": s["put_strike"], "call_strike": s.get("call_strike"),
        "days_left": days_left, "spot": None, "ex_div": None, "captured": None, "delta": None,
        "itm": False, "stop_flag": False, "cost_to_close": None, "data_ok": True,
        "earnings": "—", "error": None,
    }


def _is_condor(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_IRON_CONDOR


def _neutral_condor(s: dict, days_left: int) -> dict:
    """Iron condor row: four explicit strikes and net_credit, no credit / short_strike / long_strike."""
    return {
        "id": s["id"], "strategy": ledger_mod.STRATEGY_IRON_CONDOR, "ticker": s["ticker"],
        "expiry": s["expiry"], **{k: s[k] for k in CONDOR_STRIKE_KEYS},
        "net_credit": s["net_credit"], "days_left": days_left, "spot": None, "ex_div": None,
        "cost_to_close": None, "itm": False, "stop_flag": False, "captured": None,
        "put_short_delta": None, "call_short_delta": None, "delta": None,
        "data_ok": True, "earnings": "unknown", "error": None,
    }


def _is_bullcall(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_BULL_CALL


def _neutral_bullcall(s: dict, days_left: int) -> dict:
    """Bull call debit spread row: two explicit call strikes and the debit; no credit /
    short_strike / long_strike. `delta` stays None: no Delta rule applies to a buyer."""
    return {
        "id": s["id"], "strategy": ledger_mod.STRATEGY_BULL_CALL, "ticker": s["ticker"],
        "expiry": s["expiry"], **{k: s[k] for k in BULLCALL_STRIKE_KEYS},
        "debit": s["debit"], "width": s["width"], "days_left": days_left, "spot": None,
        "value": None, "pnl": None, "captured": None, "stop_flag": False,
        "take_profit_flag": False, "long_call_delta": None, "delta": None, "ex_div": None,
        "data_ok": True, "earnings": "unknown", "error": None,
    }


def _is_rr(s: dict) -> bool:
    return ledger_mod.strategy_of(s) == ledger_mod.STRATEGY_RR


def _neutral_rr(s: dict, days_left: int) -> dict:
    """Risk reversal row: short put + long call strikes and net_credit; no credit / short_strike /
    long_strike. `delta` mirrors the short put's |Delta| (the only Delta a rule may use)."""
    return {
        "id": s["id"], "strategy": ledger_mod.STRATEGY_RR, "ticker": s["ticker"],
        "expiry": s["expiry"], **{k: s[k] for k in RR_STRIKE_KEYS},
        "net_credit": s["net_credit"], "days_left": days_left, "spot": None,
        "put_mid": None, "call_mid": None, "pnl": None, "captured": None, "stop_flag": False,
        "take_profit_flag": False, "short_put_delta": None, "long_call_delta": None,
        "delta": None, "ex_div": None, "data_ok": True, "earnings": "unknown", "error": None,
    }


def _neutral_bearcall(s: dict, days_left: int) -> dict:
    """Bear call credit spread row: the seller spread fields (call side). `delta` will be the
    short call's |Delta|, the only Delta a rule may use."""
    return {
        "id": s["id"], "strategy": ledger_mod.STRATEGY_BEAR_CALL, "ticker": s["ticker"],
        "expiry": s["expiry"], "short_strike": s["short_strike"], "long_strike": s["long_strike"],
        "credit": s["credit"], "width": s["width"], "days_left": days_left, "spot": None,
        "cost_to_close": None, "pnl": None, "itm": False, "stop_flag": False, "captured": None,
        "delta": None, "ex_div": None, "data_ok": True, "earnings": "unknown", "error": None,
    }


def _neutral_bearput(s: dict, days_left: int) -> dict:
    """Bear put debit spread row: two explicit put strikes and the debit; no credit /
    short_strike / long_strike. `delta` stays None: no Delta rule applies to a buyer."""
    return {
        "id": s["id"], "strategy": ledger_mod.STRATEGY_BEAR_PUT, "ticker": s["ticker"],
        "expiry": s["expiry"], **{k: s[k] for k in BEARPUT_STRIKE_KEYS},
        "debit": s["debit"], "width": s["width"], "days_left": days_left, "spot": None,
        "value": None, "pnl": None, "captured": None, "stop_flag": False,
        "take_profit_flag": False, "long_put_delta": None, "delta": None, "ex_div": None,
        "data_ok": True, "earnings": "unknown", "error": None,
    }


def _neutral_longput(s: dict, days_left: int) -> dict:
    """Long put row: one put strike and the debit; `delta` stays None (buyer, no Delta rule)."""
    return {
        "id": s["id"], "strategy": ledger_mod.STRATEGY_LONG_PUT, "ticker": s["ticker"],
        "expiry": s["expiry"], "put_strike": s["put_strike"], "debit": s["debit"],
        "days_left": days_left, "spot": None, "value": None, "pnl": None, "captured": None,
        "stop_flag": False, "take_profit_flag": False, "put_delta": None, "delta": None,
        "ex_div": None, "data_ok": True, "earnings": "unknown", "error": None,
    }


NEUTRAL_OWN = {ledger_mod.STRATEGY_BEAR_CALL: _neutral_bearcall,
               ledger_mod.STRATEGY_BEAR_PUT: _neutral_bearput,
               ledger_mod.STRATEGY_LONG_PUT: _neutral_longput}


def _neutral(s: dict, days_left: int) -> dict:
    """Display fields plus neutral metrics; gather fills in what it can."""
    own = NEUTRAL_OWN.get(ledger_mod.strategy_of(s))
    if own is not None:  # before anything that reads seller fields: this runs outside the try
        return own(s, days_left)
    if _is_rr(s):  # before anything that reads seller fields: this runs outside the try
        return _neutral_rr(s, days_left)
    if _is_bullcall(s):  # before anything that reads seller fields: this runs outside the try
        return _neutral_bullcall(s, days_left)
    if _is_condor(s):
        return _neutral_condor(s, days_left)
    if _is_lot_put(s):
        return _neutral_lot_put(s, days_left)
    return {
        "id": s["id"], "strategy": ledger_mod.strategy_of(s), "ticker": s["ticker"],
        "expiry": s["expiry"], "short_strike": s["short_strike"],
        "long_strike": s.get("long_strike"), "credit": s["credit"],
        "days_left": days_left, "spot": None, "cost_to_close": None,
        "itm": False, "stop_flag": False, "captured": None, "delta": None,
        "data_ok": True, "earnings": "unknown", "error": None,
    }


def _leg_trusted(q: dict) -> bool:
    """A frozen / close-based quote is trusted only when it carries a broker delta."""
    return core.has_delta(q) or not q["stale"]


def _measure_lot_put(s: dict, m: dict, today: date) -> dict:
    """Both legs of a collar (put only for a protective put); a missing leg is an error, not a guess."""
    expiry = s["expiry"]
    put = core.quote_map(spread_data.fetch_puts(
        s["ticker"], expiry, strikes=(s["put_strike"],))).get(s["put_strike"])
    has_call = s.get("call_strike") is not None
    call = None if not has_call else core.quote_map(spread_data.fetch_calls(
        s["ticker"], expiry, strikes=(s["call_strike"],))).get(s["call_strike"])
    if put is None or (has_call and call is None):
        return {**m, "error": NO_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    ex_div = None
    if has_call and spot >= s["call_strike"]:
        found = spread_data.ex_dividend_in_window(s["ticker"], today, date.fromisoformat(expiry))
        ex_div = found.isoformat() if found else None
    data_ok = _leg_trusted(put) and (call is None or _leg_trusted(call))
    return {**m, "spot": spot, "ex_div": ex_div, "data_ok": data_ok}


def _condor_data_ok(legs: dict) -> bool:
    shorts_priced = all(core.has_delta(legs[k]) or core._usable_iv(legs[k]["iv"])
                        for k in ("put_short", "call_short"))
    return shorts_priced and all(_leg_trusted(q) for q in legs.values())


def _condor_ex_div(s: dict, spot: float, today: date) -> str | None:
    """Looked up only once the short call is in the money, as for a collar's call."""
    if spot <= s["call_short_strike"]:
        return None
    found = spread_data.ex_dividend_in_window(s["ticker"], today, date.fromisoformat(s["expiry"]))
    return found.isoformat() if found else None


def _measure_condor(s: dict, m: dict, today: date) -> dict:
    legs = spread_sim._condor_legs(s)  # all four legs by explicit strike, never guessed
    if legs is None:
        return {**m, "error": NO_CONDOR_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    cost = core.condor_cost_to_close(legs["put_short"]["mid"], legs["put_long"]["mid"],
                                     legs["call_short"]["mid"], legs["call_long"]["mid"], s["width"])
    pnl = core.condor_unrealized_pnl(s["net_credit"], cost, s["contracts"])
    flags = core.threshold_flags(s["net_credit"], s["width"], pnl, s["contracts"])
    data_ok = _condor_data_ok(legs)
    put_d = core.delta_for("P", legs["put_short"], spot, m["days_left"])[0] if data_ok else None
    call_d = core.delta_for("C", legs["call_short"], spot, m["days_left"])[0] if data_ok else None
    side = rules_mod.condor_itm_side(spot, s["put_short_strike"], s["call_short_strike"])
    return {
        **m, "spot": spot, "cost_to_close": cost,
        "captured": (s["net_credit"] - cost) / s["net_credit"], "itm": side is not None,
        "stop_flag": bool(flags["stop"]), "data_ok": data_ok,
        "put_short_delta": put_d, "call_short_delta": call_d,
        "delta": None if put_d is None else max(put_d, call_d),
        "ex_div": _condor_ex_div(s, spot, today),
        "earnings": spread_data.earnings_status(s["ticker"], today, date.fromisoformat(s["expiry"])),
    }


def _bullcall_long_delta(long_q: dict, spot: float, days_left: int) -> float | None:
    """Long-leg delta for reference only (never used by a rule); None when it cannot be priced."""
    if not (core.has_delta(long_q) or core._usable_iv(long_q["iv"])):
        return None
    return core.delta_for("C", long_q, spot, days_left)[0]


def _bullcall_ex_div(s: dict, spot: float, today: date) -> str | None:
    """Looked up only once the short call is strictly in the money (early-assignment risk)."""
    if spot <= s["short_call_strike"]:
        return None
    found = spread_data.ex_dividend_in_window(s["ticker"], today, date.fromisoformat(s["expiry"]))
    return found.isoformat() if found else None


def _measure_bullcall(s: dict, m: dict, today: date) -> dict:
    legs = spread_sim._bullcall_legs(s)  # strikes=(long, short) requested explicitly, never guessed
    if legs is None:
        return {**m, "error": NO_BULLCALL_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    value = core.bullcall_close_value(legs["long"]["mid"], legs["short"]["mid"], s["width"])
    pnl, _ = core.bullcall_pnl(s["debit"], value, s["contracts"])
    flags = core.bullcall_threshold_flags(s["debit"], s["width"], pnl, s["contracts"])
    data_ok = all(_leg_trusted(q) for q in legs.values())
    return {
        **m, "spot": spot, "value": value, "pnl": pnl,
        "captured": core.bullcall_fraction_captured(pnl, s["debit"], s["width"], s["contracts"]),
        "stop_flag": bool(flags["stop"]), "take_profit_flag": bool(flags["take_profit"]),
        "long_call_delta": (_bullcall_long_delta(legs["long"], spot, m["days_left"])
                            if data_ok else None),
        "data_ok": data_ok, "ex_div": _bullcall_ex_div(s, spot, today),
        "earnings": spread_data.earnings_status(s["ticker"], today, date.fromisoformat(s["expiry"])),
    }


def _rr_leg_delta(side: str, quote: dict, spot: float, days_left: int) -> float | None:
    """|Delta| of one risk reversal leg, or None when the quote has neither a broker delta nor a usable iv."""
    if not (core.has_delta(quote) or core._usable_iv(quote["iv"])):
        return None
    return core.delta_for(side, quote, spot, days_left)[0]


def _measure_rr(s: dict, m: dict, today: date) -> dict:
    legs = spread_sim._rr_legs(s)  # put chain for the short put, call chain for the long call
    if legs is None:
        return {**m, "error": NO_RR_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    put_mid, call_mid = legs["put"]["mid"], legs["call"]["mid"]
    pnl = core.rr_close_pnl(s["net_credit"], core.rr_exit_net(put_mid, call_mid), s["contracts"])
    flags = core.rr_threshold_flags(s["short_put_mid"], pnl, s["contracts"])
    put_delta = _rr_leg_delta("P", legs["put"], spot, m["days_left"])
    data_ok = put_delta is not None and all(_leg_trusted(q) for q in legs.values())
    put_delta = put_delta if data_ok else None  # Delta is recorded and used only from trusted data
    call_delta = _rr_leg_delta("C", legs["call"], spot, m["days_left"]) if data_ok else None
    return {
        **m, "spot": spot, "put_mid": put_mid, "call_mid": call_mid, "pnl": pnl,
        "stop_flag": bool(flags["stop"]), "take_profit_flag": bool(flags["take_profit"]),
        "short_put_delta": put_delta, "delta": put_delta, "long_call_delta": call_delta,
        "data_ok": data_ok,
        "earnings": spread_data.earnings_status(s["ticker"], today, date.fromisoformat(s["expiry"])),
    }


def _earnings(s: dict, today: date) -> str:
    return spread_data.earnings_status(s["ticker"], today, date.fromisoformat(s["expiry"]))


def _bearcall_ex_div(s: dict, today: date) -> str | None:
    """Called only once the short call is strictly in the money (early-assignment risk)."""
    found = spread_data.ex_dividend_in_window(s["ticker"], today, date.fromisoformat(s["expiry"]))
    return found.isoformat() if found else None


def _measure_bearcall(s: dict, m: dict, today: date) -> dict:
    legs = spread_sim._bearcall_legs(s)  # call chain, strikes=(short, long), never guessed
    if legs is None:
        return {**m, "error": NO_BEARCALL_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    cost = core.bearcall_close_cost(legs["short"]["mid"], legs["long"]["mid"], s["width"])
    pnl, _ = core.bearcall_pnl(s["credit"], cost, s["contracts"])
    flags = core.threshold_flags(s["credit"], s["width"], pnl, s["contracts"])
    short = legs["short"]
    data_ok = ((core.has_delta(short) or core._usable_iv(short["iv"]))
               and all(_leg_trusted(q) for q in legs.values()))
    itm = core.bearcall_in_the_money(spot, s["short_strike"])
    return {
        **m, "spot": spot, "cost_to_close": cost, "pnl": pnl,
        "captured": (s["credit"] - cost) / s["credit"], "itm": itm,
        "stop_flag": bool(flags["stop"]), "data_ok": data_ok,
        "delta": core.delta_for("C", short, spot, m["days_left"])[0] if data_ok else None,
        "ex_div": _bearcall_ex_div(s, today) if itm else None, "earnings": _earnings(s, today),
    }


def _measure_bearput(s: dict, m: dict, today: date) -> dict:
    legs = spread_sim._bearput_legs(s)  # put chain, strikes=(long, short), never guessed
    if legs is None:
        return {**m, "error": NO_BEARPUT_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    value = core.bearput_close_value(legs["long"]["mid"], legs["short"]["mid"], s["width"])
    pnl, _ = core.bearput_pnl(s["debit"], value, s["contracts"])
    flags = core.bearput_threshold_flags(s["debit"], s["width"], pnl, s["contracts"])
    data_ok = all(_leg_trusted(q) for q in legs.values())
    return {
        **m, "spot": spot, "value": value, "pnl": pnl,
        "captured": core.bearput_fraction_captured(pnl, s["debit"], s["width"], s["contracts"]),
        "stop_flag": bool(flags["stop"]), "take_profit_flag": bool(flags["take_profit"]),
        "long_put_delta": (_rr_leg_delta("P", legs["long"], spot, m["days_left"])
                           if data_ok else None),  # reference only, never used by a rule
        "data_ok": data_ok, "earnings": _earnings(s, today),
    }


def _measure_longput(s: dict, m: dict, today: date) -> dict:
    leg = spread_sim._longput_leg(s)  # put chain, strikes=(put_strike,), never guessed
    if leg is None:
        return {**m, "error": NO_LONGPUT_QUOTE}
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    value = leg["mid"]
    pnl, _ = core.longput_pnl(s["debit"], value, s["contracts"])
    flags = core.longput_threshold_flags(s["debit"], pnl, s["contracts"])
    data_ok = _leg_trusted(leg)
    return {
        **m, "spot": spot, "value": value, "pnl": pnl,
        "captured": core.longput_fraction_of_debit(pnl, s["debit"], s["contracts"]),
        "stop_flag": bool(flags["stop"]), "take_profit_flag": bool(flags["take_profit"]),
        "put_delta": _rr_leg_delta("P", leg, spot, m["days_left"]) if data_ok else None,
        "data_ok": data_ok, "earnings": _earnings(s, today),
    }


MEASURE_OWN = {ledger_mod.STRATEGY_BEAR_CALL: _measure_bearcall,
               ledger_mod.STRATEGY_BEAR_PUT: _measure_bearput,
               ledger_mod.STRATEGY_LONG_PUT: _measure_longput}


def _measure(s: dict, m: dict, today: date) -> dict:
    """One chain fetch: price the position and derive the short-leg delta."""
    own = MEASURE_OWN.get(ledger_mod.strategy_of(s))
    if own is not None:
        return own(s, m, today)
    if _is_rr(s):
        return _measure_rr(s, m, today)
    if _is_bullcall(s):
        return _measure_bullcall(s, m, today)
    if _is_condor(s):
        return _measure_condor(s, m, today)
    if _is_lot_put(s):
        return _measure_lot_put(s, m, today)
    is_cc = m["strategy"] == ledger_mod.STRATEGY_COVERED_CALL
    fetch = spread_data.fetch_calls if is_cc else spread_data.fetch_puts
    single = is_cc or m["strategy"] == ledger_mod.STRATEGY_CSP
    legs = (s["short_strike"],) if single else (s["short_strike"], s["long_strike"])
    quotes = core.quote_map(fetch(s["ticker"], s["expiry"], strikes=legs))  # ITM legs included
    short = quotes.get(s["short_strike"])
    long_ = None if single else quotes.get(s["long_strike"])
    if short is None or (not single and long_ is None):
        return {**m, "error": NO_QUOTE if single else "期权链里找不到这两腿的有效报价"}
    cost = short["mid"] if single else core.cost_to_close(short["mid"], long_["mid"])
    pnl = core.unrealized_pnl(s["credit"], cost, s["contracts"])
    flags = (core.csp_threshold_flags(s["credit"], pnl, s["contracts"]) if single
             else core.threshold_flags(s["credit"], s["width"], pnl, s["contracts"]))
    spot = spread_data.last_close(s["ticker"])[1]
    if not core._usable(spot):
        return {**m, "error": "现价无效"}
    data_ok = ((core.has_delta(short) or core._usable_iv(short["iv"]))
               and _leg_trusted(short) and (single or _leg_trusted(long_)))
    delta = core.delta_for("C" if is_cc else "P", short, spot, m["days_left"])[0] if data_ok else None
    earnings = spread_data.earnings_status(s["ticker"], today, date.fromisoformat(s["expiry"]))
    itm = spot >= s["short_strike"] if is_cc else spot < s["short_strike"]
    return {
        **m, "spot": spot, "cost_to_close": cost,
        "captured": (s["credit"] - cost) / s["credit"], "itm": itm,
        "stop_flag": bool(flags["stop"]), "delta": delta, "data_ok": data_ok,
        "earnings": earnings,
    }


def gather_metrics(s: dict, today: date) -> dict:
    days_left = (date.fromisoformat(s["expiry"]) - today).days
    m = _neutral(s, days_left)
    if days_left <= 0:
        return m
    try:
        return _measure(s, m, today)
    except spread_data.SourceUnavailable:
        raise  # link lost: the whole review is invalid, not one position
    except Exception as exc:  # one bad position must not abort the run
        return {**m, "error": str(exc) or type(exc).__name__}


def _has_data(r: dict) -> bool:
    return r["error"] is None and r["days_left"] > 0


def _quality(r: dict) -> str:
    if not _has_data(r):
        return "none"
    return "ok" if r["data_ok"] else "stale"


def _review(m: dict) -> dict:
    return {**m, **rules_mod.review_position(m)}


def _strikes(r: dict) -> str:
    if r["strategy"] == ledger_mod.STRATEGY_BEAR_CALL:
        return f"{r['short_strike']:g}/{r['long_strike']:g}"  # short/long call
    if r["strategy"] == ledger_mod.STRATEGY_BEAR_PUT:
        return "/".join(f"{r[k]:g}" for k in BEARPUT_STRIKE_KEYS)  # long/short put
    if r["strategy"] == ledger_mod.STRATEGY_LONG_PUT:
        return f"{r['put_strike']:g}"
    if r["strategy"] == ledger_mod.STRATEGY_RR:
        return "/".join(f"{r[k]:g}" for k in RR_STRIKE_KEYS)  # short put/long call
    if r["strategy"] == ledger_mod.STRATEGY_BULL_CALL:
        return "/".join(f"{r[k]:g}" for k in BULLCALL_STRIKE_KEYS)  # long/short
    if r["strategy"] == ledger_mod.STRATEGY_IRON_CONDOR:
        return "/".join(f"{r[k]:g}" for k in CONDOR_STRIKE_KEYS)
    if r["strategy"] in rules_mod.LOT_PUT_STRATEGIES:
        if r["call_strike"] is None:
            return f"{r['put_strike']:g}"
        return f"{r['put_strike']:g}/{r['call_strike']:g}"
    if r["long_strike"] is None:
        return f"{r['short_strike']:g}"
    return f"{r['short_strike']:g}/{r['long_strike']:g}"


def _cells(r: dict) -> list[str]:
    spot = "—" if r["spot"] is None else f"{r['spot']:.2f}"
    cap = "—" if r["captured"] is None else f"{r['captured']:.0%}"
    # always None for a buyer (bull call, bear put, long put): no Delta rule applies to them
    delta = "—" if r["delta"] is None else f"{r['delta']:.2f}"
    return [str(r["id"]), r["strategy"], r["ticker"], _strikes(r), r["expiry"],
            str(r["days_left"]), spot, cap, delta, r["earnings"],
            ADVICE_LABEL[r["advice"]], r["reason"]]


def render_review_md(rows: list[dict], generated_on: str, snapshot_kind: str, rules: dict) -> str:
    head = ["id", "策略", "标的", "行权价", "到期", "剩余天数", "现价", "已赚比例", "Delta",
            "财报", "结论", "理由"]
    lines = [f"# 期权持仓检查 {generated_on}（{snapshot_kind}）", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(c.replace("|", "｜") for c in _cells(r)) + " |" for r in rows]
    counts = {a: sum(1 for r in rows if r["advice"] == a) for a in ADVICE_ORDER}
    lines += ["", "汇总：" + "，".join(f"{ADVICE_LABEL[a]} {counts[a]} 笔" for a in ADVICE_ORDER)]
    if any(not r["data_ok"] for r in rows):
        lines.append(f"提示：{rules_mod.STALE_NOTE}（相关持仓见上表）。")
    lines += ["", "阈值：" + "，".join(f"{k}={v:g}" for k, v in rules.items()), "",
              "本检查只给建议，没有自动平仓或修改账本。", "", "模拟/研究用途，非投资建议"]
    return option_labels.with_banner("\n".join(lines) + "\n")


def snapshot_to_json(rows: list[dict], generated_on: str, snapshot_kind: str, rules: dict) -> dict:
    positions = [{**r, "data_quality": _quality(r)} for r in rows]
    # Top-level data_quality is the source's ("low" / "demo"); each position keeps its own
    # ok / stale / none, which _existing_score ranks.
    return {"version": 1, "generated_on": generated_on, **option_labels.labelled_fields(),
            "snapshot_kind": snapshot_kind,
            "rules": dict(rules), "positions": positions}


def _quality_score(qualities: list[str]) -> tuple[int, int]:
    """Rank a snapshot by (rows with trusted data, rows with stale data); 'none' rows add nothing."""
    return (sum(q == "ok" for q in qualities), sum(q == "stale" for q in qualities))


def _existing_score(path: Path) -> tuple[int, int]:
    """Score of the same-day snapshot on disk; (0, 0) if missing, unreadable or malformed."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return _quality_score([p.get("data_quality") for p in doc["positions"]])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return (0, 0)


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    p = argparse.ArgumentParser(prog="put_review", description="期权持仓每周检查")
    p.add_argument("--ledger-dir", default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    today = today or date.today()
    try:
        option_labels.print_console_header()
        ledger_dir = spread_sim.resolve_ledger_dir(args.ledger_dir)
        led = ledger_mod.load(ledger_dir / "spreads.json")
    except (ledger_mod.LedgerError, option_settings.SettingsError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    open_positions = [s for s in led["spreads"] if s.get("status") == "open"]
    if not open_positions:
        print("无未平仓")
        return 0
    try:
        if any(date.fromisoformat(s["expiry"]) > today for s in open_positions):
            spread_data.ensure_source()  # before anything is written
        rows = [_review(gather_metrics(s, today)) for s in open_positions]
    except (spread_data.DataError, option_settings.SettingsError) as exc:  # stop, write no snapshot
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    live = [r for r in rows if _has_data(r)]
    kind = ("盘中" if spread_data.market_open_now()
            else "陈旧" if not live or any(not r["data_ok"] for r in live) else "收盘")
    rules = rules_mod.thresholds()
    stamp = today.isoformat()
    md = render_review_md(rows, stamp, kind, rules)
    folder = ledger_dir / REVIEWS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    new_score = _quality_score([_quality(r) for r in rows])
    if new_score < _existing_score(folder / f"{stamp}.json"):
        print(md)
        print("警告：今天已有数据质量更好的快照，本次数据较差，保留原快照，没有覆盖")
        return 0
    (folder / f"{stamp}.md").write_text(md, encoding="utf-8")
    doc = snapshot_to_json(rows, stamp, kind, rules)
    (folder / f"{stamp}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=float) + "\n", encoding="utf-8")
    print(md)
    print(f"已保存：{folder / (stamp + '.md')}\n已保存：{folder / (stamp + '.json')}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
