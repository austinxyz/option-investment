"""Yahoo-mode bid-ask rule (share plan Task 4): 10% hard line, 5%-10% warning.

Every rule function that judges a spread takes keyword args `spread_limit` / `spread_warn`.
Defaults reproduce the IBKR behaviour exactly (5% passes, 5.1% rejected, no new warning).
Tickers are invented (ZZZQ); numbers come from the design (section 10.1).
"""
import re
from datetime import date
from pathlib import Path

import pytest

from spread_fixtures import make_call_chain  # also puts scripts/ on sys.path
import bullcall_sim_rules as bull
import call_screen_rules as call_rules
import collar_screen_rules as collar
import condor_sim_rules as condor
import option_settings
import put_screen
import put_screen_rules as put_rules
from holdings import HeldLot
from put_screen_wiki import UniverseRow

SNAP = date(2026, 10, 23)
YAHOO = {"spread_limit": 0.10, "spread_warn": 0.05}
WARN_77 = "买卖价差 7.7%，超过 IBKR 模式的 5% 门槛（Yahoo 报价延迟，放宽到 10%）"
# mid 5.0 in every case; (bid, ask) chosen binary-exact where a boundary is tested.
BID_ASK = {0.04: (4.9, 5.1), 0.05: (4.875, 5.125), 0.051: (4.8725, 5.1275),
           0.0501: (4.87475, 5.12525), 0.077: (4.8075, 5.1925), 0.10: (4.75, 5.25),
           0.12: (4.7, 5.3)}


def _row(strike, pct, iv=0.35):
    bid, ask = BID_ASK[pct]
    return {"strike": strike, "bid": bid, "ask": ask, "last": 5.0, "iv": iv}


def _tight(strike, mid, iv=0.35):
    return {"strike": strike, "bid": mid - 0.02, "ask": mid + 0.02, "last": mid, "iv": iv}


def _lot(label="ZZZQ"):
    return HeldLot(source="manual", lot_id=7, ticker="ZZZQ", shares=300.0, cost_basis=90.0,
                   original_cost=92.0, reference_price=95.0, cost_estimated=False,
                   accounts=1, label=label)


def _spread_warnings(warnings):
    return [w for w in warnings if "超过 IBKR 模式" in w]


# ---- adapters: (pct, kwargs) -> (accepted, warnings) -------------------------------

def _pick_put(pct, kw):
    got = put_rules.pick_put([_row(95.0, pct)], 100.0, 10, **kw)
    return (isinstance(got, tuple), got[2] if isinstance(got, tuple) else [])


def _evaluate_ticker(pct, kw):
    got = put_rules.evaluate_ticker(
        UniverseRow("ZZZQ", "watch", None, ()), snapshot=SNAP, spot=100.0, ma50=95.0,
        ma200=90.0, expirations=["2026-11-03"], earnings_for=lambda e: "no",
        puts_for=lambda e: [_row(95.0, pct)], dte_mode="weekly", **kw)
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _pick_call(pct, kw):
    got = call_rules.pick_call([_row(105.0, pct)], 100.0, 10, **kw)
    return (isinstance(got, tuple), got[2] if isinstance(got, tuple) else [])


def _evaluate_lot_call(pct, kw):
    got = call_rules.evaluate_lot(
        _lot(), snapshot=SNAP, spot=100.0, ma50=95.0, ma200=90.0, month_return=3.0,
        contracts_available=3, expirations=["2026-11-03"], earnings_for=lambda e: "no",
        ex_div_for=lambda e: None, calls_for=lambda e: [_row(105.0, pct)],
        dte_mode="weekly", **kw)
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _collar_eval(puts, calls, kw):
    return collar.evaluate_lot(
        _lot(), snapshot=date(2026, 10, 25), spot=100.0, puts=puts, calls=calls,
        expiry="2026-11-27", earnings="no", ex_div=None, available_for_put=3,
        available_for_call=3, **kw)


def _collar_put(pct, kw):
    got = _collar_eval([_row(90.0, pct)], [_tight(105.0, 5.0)], kw)["put_only"][0]
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _collar_call(pct, kw):
    got = _collar_eval([_tight(90.0, 5.0)], [_row(105.0, pct)], kw)["tiers"][0]
    return (isinstance(got, dict) and got["call"] is not None,
            got["warnings"] if isinstance(got, dict) else [])


def _limit_only(kw):
    """The collar leg pickers take only the hard limit; the warning is added by evaluate_lot."""
    return {k: v for k, v in kw.items() if k == "spread_limit"}


def _collar_pick_put(pct, kw):
    got = collar.pick_put([_row(90.0, pct)], 100.0, 0.90, **_limit_only(kw))
    return (isinstance(got, dict), [])


def _collar_zero_cost_call(pct, kw):
    got = collar.pick_zero_cost_call([_row(105.0, pct)], 100.0, 5.0, **_limit_only(kw))
    return (got is not None, [])


def _short_put(pct, kw):
    got = condor.pick_short_put([_row(95.0, pct)], 100.0, 10, 0.20, **kw)
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _short_call(pct, kw):
    got = condor.pick_short_call([_row(105.0, pct)], 100.0, 10, 0.20, **kw)
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _condor_build(pct, kw):
    puts = [_row(95.0, pct), _tight(90.0, 3.0)]
    calls = [_row(105.0, pct), _tight(110.0, 3.0)]
    got = condor.build_condor_candidates(100.0, 10, puts, calls, 5.0, "no", None,
                                         date(2026, 11, 2), SNAP, **kw)
    ok = [c for c in got if c["status"] == "ok"]
    return (bool(ok), list(dict.fromkeys(ok[0]["warnings"])) if ok else [])  # one per side


def _long_call(pct, kw):
    got = bull.pick_long_call([_row(105.0, pct)], 100.0, 10, 0.20, **kw)
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _short_call_leg(pct, kw):
    got = bull.find_short_call([_row(105.0, pct)], 100.0, 5.0, **kw)
    return (isinstance(got, dict), got["warnings"] if isinstance(got, dict) else [])


def _bullcall_build(pct, kw):
    calls = [_row(100.0, pct), _tight(105.0, 3.0)]
    got = bull.build_bullcall_candidates(100.0, 35, calls, 5.0, "no", None,
                                         date(2026, 11, 30), SNAP, **kw)
    ok = [c for c in got if c["status"] == "ok"]
    return (bool(ok), ok[0]["warnings"] if ok else [])


ADAPTERS = {
    "pick_put": _pick_put, "evaluate_ticker": _evaluate_ticker, "pick_call": _pick_call,
    "evaluate_lot_call": _evaluate_lot_call, "collar_put_leg": _collar_put,
    "collar_call_leg": _collar_call, "collar_pick_put": _collar_pick_put,
    "collar_zero_cost_call": _collar_zero_cost_call, "condor_short_put": _short_put,
    "condor_short_call": _short_call, "condor_build": _condor_build,
    "bullcall_long_call": _long_call, "bullcall_short_leg": _short_call_leg,
    "bullcall_build": _bullcall_build,
}
# These return no warning list themselves (a leg picker the evaluator wraps).
NO_WARNING_LIST = {"collar_pick_put", "collar_zero_cost_call"}
names = pytest.mark.parametrize("name", sorted(ADAPTERS))


@names
def test_yahoo_spread_4pct_passes_without_warning(name):
    accepted, warnings = ADAPTERS[name](0.04, YAHOO)
    assert accepted and _spread_warnings(warnings) == []


@names
def test_yahoo_spread_7_7pct_passes_with_warning_text(name):
    accepted, warnings = ADAPTERS[name](0.077, YAHOO)
    assert accepted
    if name not in NO_WARNING_LIST:
        assert _spread_warnings(warnings) == [WARN_77]


@names
def test_yahoo_spread_12pct_rejected(name):
    assert ADAPTERS[name](0.12, YAHOO)[0] is False


@names
def test_yahoo_spread_exactly_10pct_passes(name):
    accepted, warnings = ADAPTERS[name](0.10, YAHOO)
    assert accepted
    if name not in NO_WARNING_LIST:
        assert len(_spread_warnings(warnings)) == 1


@names
def test_yahoo_warn_boundary_exactly_5pct_silent_and_just_over_warns(name):
    """warn is strict: 5% exactly is silent, 5.01% warns (kills `<=` -> `<` in the warn test)."""
    accepted, warnings = ADAPTERS[name](0.05, YAHOO)
    assert accepted and _spread_warnings(warnings) == []
    accepted, warnings = ADAPTERS[name](0.0501, YAHOO)
    assert accepted
    if name not in NO_WARNING_LIST:
        assert len(_spread_warnings(warnings)) == 1


@names
def test_default_args_behave_as_before(name):
    """No kwargs = IBKR: exactly 5% passes silently, 5.1% is rejected, 7.7% is rejected."""
    accepted, warnings = ADAPTERS[name](0.05, {})
    assert accepted and _spread_warnings(warnings) == []
    assert ADAPTERS[name](0.051, {})[0] is False
    assert ADAPTERS[name](0.077, {})[0] is False


@names
def test_explicit_ibkr_values_equal_defaults(name):
    """spread_limit=MAX_SPREAD_PCT, spread_warn=None is the default, bit for bit."""
    explicit = {"spread_limit": put_rules.MAX_SPREAD_PCT, "spread_warn": None}
    for pct in (0.04, 0.05, 0.051):
        assert ADAPTERS[name](pct, explicit) == ADAPTERS[name](pct, {})


def test_warning_formats_the_real_limit():
    got = put_rules.pick_put([_row(95.0, 0.077)], 100.0, 10, spread_limit=0.15, spread_warn=0.05)
    assert got[2] == ["买卖价差 7.7%，超过 IBKR 模式的 5% 门槛（Yahoo 报价延迟，放宽到 15%）"]


def test_stale_quote_still_passes_with_stale_warning_only():
    stale = {"strike": 95.0, "bid": 0, "ask": 0, "last": 0.84, "iv": 0.35}
    got = put_rules.pick_put([stale], 100.0, 10, **YAHOO)
    assert got[2] == ["报价陈旧"]


# ---- constants ----------------------------------------------------------------------

def test_max_spread_constant_still_0_05():
    assert put_rules.MAX_SPREAD_PCT == 0.05


def test_spread_warn_and_rules_line_constants():
    assert put_rules.SPREAD_WARN_TEXT.format(pct=0.077, ibkr=0.05, limit=0.10) == WARN_77
    assert put_rules.RULES_LINE_SPREAD_YAHOO == "买卖价差 ≤10%（Yahoo 模式放宽；5%–10% 警告）"


RULE_MODULES = ("put_screen_rules", "call_screen_rules", "collar_screen_rules",
                "condor_sim_rules", "bullcall_sim_rules")


def test_rule_modules_have_no_new_threshold_literals():
    scripts = Path(option_settings.__file__).parent
    hits = []
    for mod in RULE_MODULES:
        for n, line in enumerate((scripts / f"{mod}.py").read_text(encoding="utf-8").splitlines(), 1):
            # a named constant definition (MIN_DELTA = 0.10) is not a threshold inside a rule
            if re.search(r"\b0?\.10\b", line) and not re.match(r"[A-Z][A-Z_0-9]* = ", line):
                hits.append((mod, n, line.strip()))
    assert hits == []  # the Yahoo limit lives in option_settings.PROFILES, not in a rule module


# ---- design 10.1 worked example -----------------------------------------------------

def _zzzq_chain(ask95=0.88):
    def r(k, bid, ask):
        return {"strike": float(k), "bid": bid, "ask": ask, "last": (bid + ask) / 2, "iv": 0.35}
    unquoted = {"strike": 96.0, "bid": 0, "ask": 0, "last": 0, "iv": 0.35}
    # 96 has no price at all (dropped by quote_map); 95 is the Delta-nearest-0.20 strike.
    return [unquoted, r(95, 0.80, ask95), r(94, 0.55, 0.57)]


def _zzzq(kw, chain):
    return put_rules.evaluate_ticker(
        UniverseRow("ZZZQ", "watch", None, ()), snapshot=SNAP, spot=100.0, ma50=95.0,
        ma200=90.0, expirations=["2026-11-02"], earnings_for=lambda e: "no",
        puts_for=lambda e: chain, dte_mode="weekly", **kw)


def test_design_10_1_example():
    # 95: bid 0.80 / ask 0.88 -> mid 0.84, spread 0.08 / 0.84 = 9.52%  (> 5%, <= 10%)
    # 94: bid 0.55 / ask 0.57 -> mid 0.56, spread 0.02 / 0.56 = 3.57%
    ibkr = _zzzq({}, _zzzq_chain())
    assert ibkr["strike"] == 94.0  # 95's 9.5% breaks the 5% line: walk down one strike
    assert ibkr["warnings"] == [f"实际 Delta {ibkr['delta']:.2f}，已偏离 0.20"]
    assert f"{ibkr['delta']:.2f}" == "0.13"

    yahoo = _zzzq(YAHOO, _zzzq_chain())
    assert yahoo["strike"] == 95.0
    assert yahoo["warnings"] == ["买卖价差 9.5%，超过 IBKR 模式的 5% 门槛（Yahoo 报价延迟，放宽到 10%）"]
    assert yahoo["cash_required"] == 95 * 100 == 9500             # 95 x 100
    assert yahoo["breakeven"] == pytest.approx(94.16)             # 95 - 0.84
    assert yahoo["buffer_pct"] == pytest.approx(5.0)              # (100 - 95) / 100
    assert yahoo["annualized_pct"] == pytest.approx(32.3, abs=0.05)  # 0.84 / 95 * 365 / 10 = 32.27%
    assert yahoo["days"] == 10


def test_design_10_1_ask_1_25_rejected_in_both_modes():
    # 95 with ask 1.25: spread 1.25 - 0.80 = 0.45, mid 1.025, 0.45 / 1.025 = 43.9% -> over any line
    for kw in ({}, YAHOO):
        got = _zzzq(kw, _zzzq_chain(ask95=1.25))
        assert got["strike"] != 95.0  # that strike is skipped; the walk continues to 94


# ---- rules doc ----------------------------------------------------------------------

def test_rules_doc_ibkr_unchanged():
    doc = put_screen._rules_doc("weekly")
    assert list(doc) == ["dte_mode", "window_days", "target_delta", "max_spread_pct"]
    assert doc["max_spread_pct"] == 0.05


@pytest.mark.data_source("yahoo")
def test_rules_doc_yahoo_fields():
    doc = put_screen._rules_doc("weekly")
    assert doc["max_spread_pct"] == 0.10 and doc["spread_warn_pct"] == 0.05
    assert list(doc)[:4] == ["dte_mode", "window_days", "target_delta", "max_spread_pct"]


def test_spread_kwargs_empty_in_ibkr_and_profile_values_otherwise(monkeypatch):
    assert option_settings.spread_kwargs() == {}
    monkeypatch.setenv("OPTION_DATA_SOURCE", "yahoo")
    assert option_settings.spread_kwargs() == {"spread_limit": 0.10, "spread_warn": 0.05}


# ---- strategy-pick: stale ATM quote ---------------------------------------------------

def _stale_run(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import ATM_CALL, ATM_PUT, base_result, snapshot
    stale_put = {**ATM_PUT, "bid": 0.0, "ask": 0.0}
    stale_call = {**ATM_CALL, "bid": 0.0, "ask": 0.0}
    result, md = base_result(monkeypatch, tmp_path,
                             option_snapshot=snapshot(atm_put=stale_put, atm_call=stale_call))
    return result, md


STALE_YAHOO = "Yahoo 报价陈旧：ATM 买卖价差未知，流动性门槛未验证，开仓前盘中再确认"


@pytest.mark.data_source("yahoo")
def test_strategy_pick_stale_atm_warns_in_yahoo(monkeypatch, tmp_path):
    result, md = _stale_run(monkeypatch, tmp_path)
    assert STALE_YAHOO in md and "流动性未验证（报价陈旧），盘中重跑" in md
    assert STALE_YAHOO in str(result)


def test_strategy_pick_stale_atm_no_extra_warning_in_ibkr(monkeypatch, tmp_path):
    result, md = _stale_run(monkeypatch, tmp_path)
    assert STALE_YAHOO not in md and "流动性未验证（报价陈旧），盘中重跑" in md


@pytest.mark.data_source("yahoo")
def test_strategy_pick_quoted_atm_has_no_stale_warning_in_yahoo(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import base_result
    _, md = base_result(monkeypatch, tmp_path)
    assert STALE_YAHOO not in md
