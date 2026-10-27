"""Rule tests for the /strategy-pick decision tree (design 6.2-6.12, section 10).

ZZZQ is invented. Hand arithmetic for the worked example (spot 100.00, ATM IV 0.32, 28 days):
  sigma = 100 x 0.32 x sqrt(28 / 365) = 32 x 0.2769699 = 8.863037 (unrounded into every level)
  0.84 sigma = 7.444951 -> 100 - 7.444951 = 92.555 -> 92.56 ; 100 + 7.444951 -> 107.44
  1.04 sigma = 9.217558 -> 90.78 / 109.22
  1.28 sigma = 11.344687 -> 88.66 / 111.34
  0.52 sigma = 4.608779 -> 100 - 4.608779 = 95.391 -> 95.39
"""
import ast
from datetime import date

import pytest

from strategy_pick_fixtures import REPO_ROOT, SNAPSHOT, base_rv21
import strategy_pick_rules as rules
import strategy_pick_text as text
from strategy_pick_tree import (DecisionContext, GateInputs, IvPosition, NeedsView, Target,
                                ViewDecision, decide, earnings_state, holding_state, iv_position,
                                judge_gate, plan_targets, rank_csp_vs_bull_put, resolve_class,
                                resolve_view, select_iv_expiry, select_oi_expiry, sigma_for)

SPOT, ATM_IV, DAYS, PV = 100.0, 0.32, 28, 200_000.0
EXPIRY = date(2026, 11, 20)
STRATEGIES_DIR = REPO_ROOT / "wiki" / "options" / "strategies"


def gate_inputs(**over) -> GateInputs:
    base = dict(ticker="ZZZQ", asset_class="bluechip", price=100.0, market_cap=180e9,
                dollar_volume=4_200_000 * 92.0, expiries_90d=9, expiry_used=EXPIRY,
                strikes_15pct=31, atm_put_bid=2.90, atm_put_ask=3.02, atm_call_bid=3.10,
                atm_call_ask=3.24, atm_oi=6800)
    base.update(over)
    return GateInputs(**base)


def item(gate, metric: str):
    return next(i for i in gate.items if i.name.split()[0] == metric)


def iv(label="high", atm=ATM_IV, method=rules.IV_METHOD_PRIMARY) -> IvPosition:
    return IvPosition(atm_iv=atm, percentile=None, label=label, method=method, iv_rv_ratio=None)


NOT_HELD = holding_state([], [])
NO_EARNINGS = earnings_state("bluechip", date(2027, 1, 28), SNAPSHOT, None)


def ctx(**over) -> DecisionContext:
    base = dict(ticker="ZZZQ", view="bull", holding=NOT_HELD, regime="steady_up", iv=iv(),
                asset_class="bluechip", earnings=NO_EARNINGS, spot=SPOT, days=DAYS,
                portfolio_value=PV)
    base.update(over)
    return DecisionContext(**base)


def strategies(decision) -> list[str]:
    return [p.strategy for p in decision.picks]


def pick(decision, strategy):
    return next(p for p in decision.picks if p.strategy == strategy)


def reason(decision, strategy) -> str:
    return next(n.reason_code for n in decision.not_chosen if n.strategy == strategy)


# ---- gate (6.7) ----

def test_gate_base_example_all_pass():
    g = judge_gate(gate_inputs())
    assert g.passed
    assert {i.result for i in g.items} == {"pass"}
    # 0.12 / 2.96 = 4.05% ; 0.14 / 3.17 = 4.42% ; 4,200,000 x 92.00 = 386.4M
    assert item(g, "PutSpreadPct").value == pytest.approx(0.0405, abs=5e-5)
    assert item(g, "CallSpreadPct").value == pytest.approx(0.0442, abs=5e-5)
    assert item(g, "DollarVolume").value == pytest.approx(386.4e6)
    assert (item(g, "Expiries90d").value, item(g, "Strikes15pct").value,
            item(g, "AtmOI").value) == (9, 31, 6800)
    assert item(g, "PutSpreadPct").constant == "GATE_MAX_SPREAD_PCT"


def test_gate_v6_dollar_volume_fails():
    g = judge_gate(gate_inputs(dollar_volume=12e6))
    assert not g.passed
    assert item(g, "DollarVolume").result == "fail"
    assert item(g, "DollarVolume").threshold == rules.MIN_DOLLAR_VOLUME


def test_gate_leveraged_etf_fails():
    lev = sorted(rules.LEVERAGED_ETFS)[0]
    g = judge_gate(gate_inputs(ticker=lev, asset_class="etf", market_cap=None))
    assert not g.passed
    assert item(g, "ticker").result == "fail"


def test_gate_market_cap_skipped_for_etf_and_missing():
    etf = judge_gate(gate_inputs(asset_class="etf", market_cap=1e6))
    assert etf.passed and item(etf, "MarketCap").result == "unchecked"
    missing = judge_gate(gate_inputs(market_cap=None))
    assert missing.passed and item(missing, "MarketCap").result == "unchecked"
    assert missing.warnings
    small = judge_gate(gate_inputs(market_cap=1.5e9))
    assert not small.passed and item(small, "MarketCap").result == "fail"


def test_gate_spread_exactly_10pct_passes():
    # 4.75 / 5.25: spread 0.50, mid 5.00, 0.50 / 5.00 = 0.10 exactly (binary-exact inputs)
    g = judge_gate(gate_inputs(atm_put_bid=4.75, atm_put_ask=5.25))
    assert item(g, "PutSpreadPct").value == rules.GATE_MAX_SPREAD_PCT
    assert item(g, "PutSpreadPct").result == "pass" and g.passed
    wide = judge_gate(gate_inputs(atm_call_bid=4.70, atm_call_ask=5.30))
    assert item(wide, "CallSpreadPct").result == "fail" and not wide.passed


def test_gate_stale_quotes_unchecked_not_fail():
    g = judge_gate(gate_inputs(atm_put_bid=None, atm_put_ask=None, atm_call_bid=0.0,
                               atm_call_ask=0.0))
    assert g.passed
    assert item(g, "PutSpreadPct").result == "unchecked"
    assert item(g, "CallSpreadPct").result == "unchecked"
    assert any("报价陈旧" in w for w in g.warnings)


def test_gate_missing_oi_unchecked_with_warning_not_fail():
    """Real AAPL 2026-10-26 after the close: Yahoo returned OI 0 on every strike (missing data)."""
    g = judge_gate(gate_inputs(atm_oi=None))
    assert g.passed
    assert item(g, "AtmOI").result == "unchecked"
    assert text.WARN["oi_missing"] in g.warnings


def test_gate_real_low_oi_still_fails():
    g = judge_gate(gate_inputs(atm_oi=120))
    assert not g.passed
    assert item(g, "AtmOI").result == "fail"
    assert text.WARN["oi_missing"] not in g.warnings


def test_gate_no_expiry_in_20_60_skips_three_items():
    g = judge_gate(gate_inputs(expiry_used=None))
    assert g.passed
    for metric in ("Strikes15pct", "PutSpreadPct", "CallSpreadPct", "AtmOI"):
        assert item(g, metric).result == "unchecked"
    assert item(g, "Expiries90d").result == "pass"
    assert any("20–60" in w for w in g.warnings)
    few = judge_gate(gate_inputs(expiry_used=None, expiries_90d=3))
    assert not few.passed
    no_chain = judge_gate(gate_inputs(expiry_used=None, expiries_90d=0))
    assert not no_chain.passed


def test_select_iv_expiry_nearest_30_tie_earlier():
    snap = SNAPSHOT
    assert select_iv_expiry([date(2026, 11, 13), EXPIRY, date(2026, 12, 18)], snap) == EXPIRY
    # 2026-11-17 is 25 days out, 2026-11-27 is 35 days out: equally near 30 -> earlier
    assert select_iv_expiry([date(2026, 11, 27), date(2026, 11, 17)], snap) == date(2026, 11, 17)
    # 19 and 61 days are outside 20-60; exactly 20 and 60 are inside
    assert select_iv_expiry([date(2026, 11, 11), date(2026, 12, 23)], snap) is None
    assert select_iv_expiry([date(2026, 11, 12)], snap) == date(2026, 11, 12)
    assert select_iv_expiry([date(2026, 12, 22)], snap) == date(2026, 12, 22)
    assert select_iv_expiry([], snap) is None


def test_select_oi_expiry_prefers_monthly_in_window():
    snap = date(2026, 10, 26)  # the real SPY run: 11/27 weekly (32d) beat the 11/20 monthly (25d)
    listed = [date(2026, 11, 20), date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 4),
              date(2026, 12, 18)]
    used = select_iv_expiry(listed, snap)
    assert used == date(2026, 11, 27)
    assert select_oi_expiry(listed, snap, used) == date(2026, 11, 20)
    # used already monthly -> itself; no monthly in 20-60 -> used; no used -> None
    assert select_oi_expiry(listed, snap, date(2026, 11, 20)) == date(2026, 11, 20)
    assert select_oi_expiry([date(2026, 11, 27)], snap, date(2026, 11, 27)) == date(2026, 11, 27)
    assert select_oi_expiry(listed, snap, None) is None
    # holiday-shortened monthly: third-week Thursday counts when its Friday is not listed
    assert select_oi_expiry([date(2027, 3, 25), date(2027, 3, 18)], date(2027, 2, 25),
                            date(2027, 3, 25)) == date(2027, 3, 18)


# ---- view and class (6.3, 6.4) ----

def test_resolve_view_mapping_and_cli_priority():
    assert resolve_view("active", None) == ViewDecision("bull", "wiki", False, "bull")
    assert resolve_view("use", None).view == "bull"
    assert resolve_view("watch", None).view == "neutral"
    assert resolve_view("avoid", None).view == "bear"
    over = resolve_view("avoid", "bull")
    assert (over.view, over.source, over.conflict, over.wiki_view) == ("bull", "user", True, "bear")
    assert resolve_view("watch", "中性").conflict is False
    assert resolve_view(None, "不看好").view == "bear"
    with pytest.raises(ValueError):
        resolve_view("active", "sideways")


def test_resolve_view_needs_user_for_under_review_unrecognized_missing():
    for status in ("under_review", "unrecognized", None):
        out = resolve_view(status, None)
        assert isinstance(out, NeedsView)
        assert "{ticker}" in out.prompt
    assert resolve_view("under_review", "bull").conflict is False


def test_resolve_class_order_and_unknown_is_hot():
    none = dict(cli_class=None, is_etf_page=False, quote_type=None, in_etf_table=False,
                wiki_tier=None, stock_type="")
    assert resolve_class(**{**none, "cli_class": "etf", "wiki_tier": "🏦"})[:2] == ("etf", "cli")
    assert resolve_class(**{**none, "is_etf_page": True, "wiki_tier": "🏦"})[0] == "etf"
    assert resolve_class(**{**none, "quote_type": "ETF"})[0] == "etf"
    assert resolve_class(**{**none, "in_etf_table": True})[0] == "etf"
    assert resolve_class(**{**none, "stock_type": "宽基"})[0] == "etf"
    assert resolve_class(**{**none, "wiki_tier": "🏦"}) == ("bluechip", "wiki_tier", [])
    assert resolve_class(**{**none, "wiki_tier": "⚡"})[0] == "hot"
    cls, src, warns = resolve_class(**{**none, "wiki_tier": "🚀", "stock_type": "🏦 蓝筹"})
    assert (cls, src) == ("hot", "wiki_tier") and len(warns) == 1
    assert resolve_class(**{**none, "stock_type": "🏦 蓝筹"})[:2] == ("bluechip", "stock_type")
    cls, src, warns = resolve_class(**none)
    assert cls == "hot" and warns and "--class" in warns[0]
    with pytest.raises(ValueError):
        resolve_class(**{**none, "cli_class": "midcap"})


# ---- IV position (6.2) ----

def test_iv_position_base_and_v2():
    base = iv_position(ATM_IV, base_rv21(174), 0.26)
    # 174 / 231 = 0.75325 ; IV / RV21 = 0.32 / 0.26 = 1.2308
    assert base.percentile == pytest.approx(0.7532, abs=1e-4)
    assert (base.label, base.method) == ("high", "iv_vs_rv")
    assert base.iv_rv_ratio == pytest.approx(1.23, abs=5e-3)
    v2 = iv_position(ATM_IV, base_rv21(58), 0.26)  # 58 / 231 = 0.25108
    assert v2.percentile == pytest.approx(0.2511, abs=1e-4) and v2.label == "low"
    ten = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    assert iv_position(0.7, ten, 0.5).percentile == 0.7  # 7 of 10 at or below
    assert iv_position(0.7, ten, 0.5).label == "high"
    assert iv_position(0.3, ten, 0.5).label == "low"  # 3 of 10 = 0.30 exactly
    assert iv_position(0.5, ten, 0.5).label == "mid"


def test_iv_missing_falls_back_to_rv_vs_rv():
    out = iv_position(None, base_rv21(174), 0.26)
    assert out.method == "rv_vs_rv" and out.atm_iv is None and out.iv_rv_ratio is None
    # current RV21 0.26 against the same 231 windows
    expected = sum(1 for v in base_rv21(174) if v <= 0.26) / 231
    assert out.percentile == pytest.approx(expected)


# ---- holding (6.11) ----

def test_holding_cross_account_110_is_not_held():
    h = holding_state([60, 50], [True, False])
    assert (h.held_shares_total, h.max_lot_shares, h.lot_count) == (110, 60, 2)
    assert h.treated_as_held is False and h.branch == "no_lot"
    one = holding_state([150], [False])
    assert one.treated_as_held and one.branch == "held" and one.ltcg_reached is False
    assert holding_state([100], [True]).treated_as_held  # exactly 100 is one contract
    assert holding_state([], []).branch == "not_held"


# ---- ranking and reference levels (6.8) ----

def test_csp_first_bluechip_under_10pct():
    first, basis, share = rank_csp_vs_bull_put(92.56, PV, "bluechip")
    assert first == "cash_secured_put" and basis == "share_and_class"
    assert share == pytest.approx(0.04628)  # 92.56 x 100 = 9,256 / 200,000 = 4.6%
    assert rank_csp_vs_bull_put(92.56, PV, "etf")[0] == "cash_secured_put"
    assert rank_csp_vs_bull_put(200.0, PV, "bluechip")[0] == "cash_secured_put"  # = 10%
    assert rank_csp_vs_bull_put(250.0, PV, "bluechip")[0] == "bull_put_spread"  # 12.5%


def test_bull_put_first_for_hot_or_missing_portfolio_value():
    assert rank_csp_vs_bull_put(92.56, PV, "hot")[0] == "bull_put_spread"
    assert rank_csp_vs_bull_put(92.56, None, "hot") == ("bull_put_spread", "class_only", None)
    assert rank_csp_vs_bull_put(92.56, None, "bluechip") == ("cash_secured_put", "class_only",
                                                            None)
    assert rank_csp_vs_bull_put(None, PV, "bluechip")[1:] == ("class_only", None)


def test_reference_levels_hand_numbers():
    sigma = sigma_for(SPOT, ATM_IV, DAYS)
    assert sigma == pytest.approx(8.863037, abs=1e-6)
    refs = {plan: plan_targets(plan, SPOT, sigma)[1] for plan in rules.LEG_PLANS}
    assert refs["put_normal"] == (Target("short_put", 92.56, 92.56),)
    assert refs["call_far"] == (Target("short_call", 109.22, 111.34),)
    assert refs["put_far"] == (Target("short_put", 88.66, 90.78),)
    assert refs["bullcall"] == (Target("long_call", 100.0, 100.0),)
    assert refs["condor"] == (Target("short_put", 90.78, 90.78),
                              Target("short_call", 109.22, 109.22))
    assert refs["collar"] == (Target("long_put", 92.56, 95.39),
                              Target("short_call", 107.44, 107.44))
    deltas = plan_targets("put_far", SPOT, sigma)[0]
    assert deltas == (Target("short_put", 0.10, 0.15),)
    assert plan_targets("bullcall", SPOT, sigma)[0] == (Target("long_call", 0.50, 0.60),)
    base = decide(ctx())
    assert pick(base, "cash_secured_put").reference_level == (Target("short_put", 92.56, 92.56),)
    assert pick(base, "cash_secured_put").delta_target == (Target("short_put", 0.20, 0.20),)


def test_reference_none_when_iv_missing():
    d = decide(ctx(iv=iv(atm=None, method=rules.IV_METHOD_FALLBACK)))
    assert strategies(d) == ["cash_secured_put", "bull_put_spread"]  # class-only ranking
    assert all(p.reference_level is None for p in d.picks)
    assert d.csp_share is None and d.rank_basis == "class_only"
    assert any("参考价位不给" in w for w in d.warnings)


# ---- earnings, thin IV (6.6, 6.10) ----

def test_hot_earnings_in_45_adds_expiry_flag():
    e = earnings_state("hot", date(2026, 11, 12), SNAPSHOT, None)
    assert (e.days, e.in_window, e.imminent, e.unknown) == (20, True, False, False)
    d = decide(ctx(asset_class="hot", earnings=e))  # V4
    assert strategies(d) == ["bull_put_spread", "cash_secured_put"]
    for p in d.picks:
        assert "earnings_before_expiry" in p.flags
    assert any("2026-11-12" in w for w in d.warnings)
    assert any("跨财报" in w for w in d.warnings)  # 20 days <= 28-day expiry


def test_hot_earnings_imminent_replaces_second_pick():
    e = earnings_state("hot", date(2026, 10, 28), SNAPSHOT, None)
    assert (e.days, e.imminent) == (5, True)
    d = decide(ctx(asset_class="hot", earnings=e))  # V8
    assert strategies(d) == ["wait_earnings", "bull_put_spread"]
    assert reason(d, "cash_secured_put") == "earnings_imminent"
    assert pick(d, "wait_earnings").rank == 1 and pick(d, "bull_put_spread").rank == 2


def test_bluechip_earnings_only_warns_and_etf_skips_earnings():
    e = earnings_state("bluechip", date(2026, 11, 12), SNAPSHOT, None)
    d = decide(ctx(earnings=e))
    assert strategies(d) == ["cash_secured_put", "bull_put_spread"]
    assert all("earnings_before_expiry" not in p.flags for p in d.picks)
    assert any("2026-11-12" in w for w in d.warnings)
    unknown = decide(ctx(earnings=earnings_state("bluechip", None, SNAPSHOT, None)))
    assert any("财报日期未知" in w for w in unknown.warnings)
    etf = earnings_state("etf", date(2026, 10, 28), SNAPSHOT, date(2026, 11, 6))
    assert (etf.date, etf.in_window, etf.imminent, etf.unknown) == (None, False, False, False)
    assert etf.ex_div == date(2026, 11, 6)
    assert earnings_state("etf", None, SNAPSHOT, date(2027, 3, 1)).ex_div is None


def test_thin_iv_flag_with_iv_high():
    e = earnings_state("hot", date(2026, 11, 12), SNAPSHOT, None)
    d = decide(ctx(asset_class="hot", earnings=e))  # V4: 0.32 < hot THIN_IV 0.35
    assert all("thin_premium" in p.flags for p in d.picks)
    assert any("0.35" in w for w in d.warnings)
    blue = decide(ctx())  # 0.32 >= bluechip THIN_IV 0.20
    assert all("thin_premium" not in p.flags for p in blue.picks)


def test_collar_ltcg_flag_and_bear_reduce_hint():
    d = decide(ctx(view="bear", holding=holding_state([150], [False])))
    assert d.leaf_id == "bear.held" and strategies(d) == ["reduce_position", "collar"]
    assert "ltcg_reset" in pick(d, "collar").flags
    nolot = decide(ctx(view="bear", holding=holding_state([60, 50], [True, True])))
    assert nolot.leaf_id == "bear.not_held" and strategies(nolot) == ["no_options"]
    assert "reduce_hint" in nolot.picks[0].flags
    assert reason(nolot, "collar") == "held_no_lot"


def test_special_leaves_need_no_view():
    gate = decide(ctx(view=None, holding=None, regime=None, iv=None, gate_passed=False))
    assert gate.leaf_id == "gate_fail" and strategies(gate) == ["no_options"]
    assert reason(gate, "cash_secured_put") == "gate_failed"
    assert reason(gate, "naked_call") == "never_naked"
    short = decide(ctx(regime=None, iv=None, history_short=True))
    assert short.leaf_id == "history_short" and strategies(short) == ["no_options"]


# ---- text tables and guards (6.12) ----

def test_why_not_every_code_has_text_and_existing_page():
    codes_with_text = {code for (_, code) in text.WHY_NOT}
    assert set(rules.WHY_NOT_CODES) <= codes_with_text
    for (strategy, code), sentence in text.WHY_NOT.items():
        assert code in rules.WHY_NOT_CODES and sentence.strip()
        assert (STRATEGIES_DIR / text.STRATEGY_PAGES[strategy]).is_file()
    for key in rules.ALL_STRATEGY_KEYS:
        assert (STRATEGIES_DIR / text.STRATEGY_PAGES[key]).is_file()
        for _, code in rules.REASON_RULES[key]:
            assert (key, code) in text.WHY_NOT


def test_every_strategy_has_label_and_next_command():
    for key in rules.STRATEGIES:
        assert text.STRATEGY_LABELS[key] and text.NEXT_COMMANDS[key].startswith("/")
    for key in rules.NOT_CHOSEN_ONLY:
        assert text.STRATEGY_LABELS[key]
    assert set(text.NEXT_COMMAND_CAVEATS) <= set(rules.STRATEGIES)
    assert "MA200" in text.NEXT_COMMAND_CAVEATS["cash_secured_put"]


def _numeric_literals(path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}


TREE_MODULES = [REPO_ROOT / "scripts" / name
                for name in ("strategy_pick_tree.py", "strategy_pick_inputs.py")]


@pytest.mark.parametrize("path", TREE_MODULES, ids=lambda p: p.name)
def test_tree_has_no_threshold_literals(path):
    assert _numeric_literals(path) <= {0, 1}


@pytest.mark.parametrize("path", TREE_MODULES, ids=lambda p: p.name)
def test_tree_has_no_strategy_key_literals(path):
    # Strategy choice lives in LEAF_TABLE / REASON_RULES; the tree only looks it up.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, str)}
    assert not strings & set(rules.ALL_STRATEGY_KEYS)
