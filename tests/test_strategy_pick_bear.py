"""/strategy-pick for the bear view: bear put / bear call spreads (2026-10-30 bearish design).

ZZZQ is invented. Hand arithmetic (spot 100.00, ATM IV 0.32, 28 days):
  sigma = 100 x 0.32 x sqrt(28 / 365) = 8.863037
  Bear Put long put, side 0 -> at spot: 100.00
  Bear Call short call 0.10-0.15, side +1: 1.04 sigma = 9.217558 -> 109.22 ; 1.28 sigma -> 111.34
"""
import hashlib
import json
from datetime import date

from strategy_pick_fixtures import REPO_ROOT, SNAPSHOT
import strategy_pick_rules as rules
import strategy_pick_text as T
from strategy_pick_tree import (DecisionContext, IvPosition, Target, decide, earnings_state,
                                holding_state, plan_targets, sigma_for)

SPOT, ATM_IV, DAYS, PV = 100.0, 0.32, 28, 200_000.0

# ---- pin: non-bear leaves byte-identical (written and passing before the bear change) ----

# Every strategy key that existed before the bear spreads joined the vocabulary.
OLD_KEYS = ("cash_secured_put", "bull_put_spread", "bull_call_spread", "iron_condor",
            "covered_call", "collar", "protective_put", "hold", "no_options",
            "wait_earnings", "reduce_position", "long_call", "naked_call", "bearish", "wheel")
PIN_HOLDINGS = {"held": (holding_state([150], [True]), holding_state([150], [False])),
                "not_held": (holding_state([], []), holding_state([60, 50], [True, True]))}
PIN_CLASSES = ("bluechip", "hot", "etf")
BEARISH = "bearish"


def _iv(label, atm=ATM_IV):
    method = rules.IV_METHOD_PRIMARY if atm is not None else rules.IV_METHOD_FALLBACK
    return IvPosition(atm_iv=atm, percentile=None, label=label, method=method, iv_rv_ratio=None)


def _earnings_variants(cls):
    return (earnings_state(cls, date(2027, 1, 28), SNAPSHOT, None),     # outside 45 days
            earnings_state(cls, date(2026, 11, 12), SNAPSHOT, None),    # 20 days
            earnings_state(cls, date(2026, 10, 28), SNAPSHOT, date(2026, 11, 6)))  # 5 days


def _pin_contexts():
    """(label, ctx) for every non-bear leaf x class x holding x earnings x IV present / missing."""
    out = []
    for lid in sorted(rules.LEAF_TABLE):
        parts = lid.split(".")
        if parts[0] == "bear":
            continue
        for cls in PIN_CLASSES:
            for e_idx, earnings in enumerate(_earnings_variants(cls)):
                for atm in (ATM_IV, None):
                    base = dict(ticker="ZZZQ", asset_class=cls, earnings=earnings, spot=SPOT,
                                days=DAYS, portfolio_value=PV)
                    if len(parts) == 1:  # gate_fail / history_short
                        special = ({"gate_passed": False} if lid == rules.LEAF_GATE_FAIL
                                   else {"history_short": True})
                        ctx = DecisionContext(view="bull", holding=PIN_HOLDINGS["not_held"][0],
                                              regime=None, iv=None, **base, **special)
                        out.append((f"{lid}|{cls}|{e_idx}|{atm}", ctx))
                        continue
                    view, branch, regime, iv_label = parts
                    for h_idx, holding in enumerate(PIN_HOLDINGS[branch]):
                        ctx = DecisionContext(view=view, holding=holding, regime=regime,
                                              iv=_iv(iv_label, atm), **base)
                        out.append((f"{lid}|{cls}|{e_idx}|{atm}|{h_idx}", ctx))
    return out


def _targets(ts):
    return None if ts is None else [[t.leg, t.low, t.high] for t in ts]


def _core(d) -> dict:
    return {"leaf": d.leaf_id, "warnings": list(d.warnings), "share": d.csp_share,
            "basis": d.rank_basis,
            "picks": [[p.strategy, p.rank, _targets(p.delta_target), _targets(p.reference_level),
                       p.next_command, list(p.flags)] for p in d.picks]}


def _old_not_chosen(d) -> list:
    """Old keys only. The `bearish` sentence is rewritten on purpose (design 3, 词表 row), so its
    text is pinned separately; its code and page stay part of the hash."""
    return [[n.strategy, n.reason_code, n.page] + ([] if n.strategy == BEARISH else [n.reason_text])
            for n in d.not_chosen if n.strategy in OLD_KEYS]


def _sha(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


PIN_CORE_SHA256 = "38855193b0b9ebeb41744fd4d7eafb62521367f904fe6e7becdf30ad93298985"
PIN_NOT_CHOSEN_SHA256 = "49cf0598a21fce7dbbf311d66a56f212e3468f78654887dc11d5c3fb2bc8d6f1"


def test_non_bear_leaves_unchanged_except_not_chosen_keys():
    contexts = _pin_contexts()
    assert len(contexts) == 2196
    decisions = [(label, decide(ctx)) for label, ctx in contexts]
    assert _sha([[label, _core(d)] for label, d in decisions]) == PIN_CORE_SHA256
    assert _sha([[label, _old_not_chosen(d)] for label, d in decisions]) == PIN_NOT_CHOSEN_SHA256


# ---- the bear leaves ----------------------------------------------------------------------

BP, BCS, RED, WAIT = "bear_put_spread", "bear_call_spread", "reduce_position", "wait_earnings"
HELD, NOT_HELD = holding_state([150], [True]), holding_state([], [])
NO_LOT = holding_state([60, 50], [True, True])
QUIET = earnings_state("bluechip", date(2027, 1, 28), SNAPSHOT, None)
HOT_SOON = earnings_state("hot", date(2026, 10, 28), SNAPSHOT, None)   # 5 days
HOT_20 = earnings_state("hot", date(2026, 11, 12), SNAPSHOT, None)     # 20 days
AGAINST, IV_HIGH_BUY, DRIFT = "bear_against_trend", "bear_put_iv_high", "bear_drift"


def bear(**over) -> DecisionContext:
    base = dict(ticker="ZZZQ", view="bear", holding=NOT_HELD, regime="down", iv=_iv("mid"),
                asset_class="bluechip", earnings=QUIET, spot=SPOT, days=DAYS, portfolio_value=PV)
    base.update(over)
    return DecisionContext(**base)


def names(d) -> list:
    return [p.strategy for p in d.picks]


def pick(d, strategy):
    return next(p for p in d.picks if p.strategy == strategy)


def reasons(d) -> dict:
    return {n.strategy: n.reason_code for n in d.not_chosen}


def test_bear_not_held_picks_bear_put_then_bear_call():
    d = decide(bear())
    assert d.leaf_id == "bear.not_held" and names(d) == [BP, BCS]
    assert [p.rank for p in d.picks] == [1, 2]
    assert pick(d, BP).next_command == "/bearput-sim suggest ZZZQ"
    assert pick(d, BCS).next_command == "/bearcall-sim suggest ZZZQ"


def test_bear_held_picks_reduce_then_bear_put():
    d = decide(bear(holding=HELD))
    assert d.leaf_id == "bear.held" and names(d) == [RED, BP]
    assert "collar" not in names(d)
    assert reasons(d)["collar"] == "leaf_choice"
    assert reasons(d)[BCS] == "held_use_shares"
    assert pick(d, RED).delta_target == ()


def test_bear_no_lot_uses_not_held_leaf_with_reduce_hint_on_first_pick():
    d = decide(bear(holding=NO_LOT))
    assert d.leaf_id == "bear.not_held" and names(d) == [BP, BCS]
    assert "reduce_hint" in d.picks[0].flags and "reduce_hint" not in d.picks[1].flags
    assert T.WARN["reduce_hint"].format(lot=100) in d.warnings
    assert T.WARN["no_lot"].format(max_lot=60, lot=100) in d.warnings
    assert reasons(d)["collar"] == "held_no_lot"


def test_bear_put_plan_targets_long_put_near_spot():
    assert rules.BEARPUT_LONG_DELTA == (0.50, 0.60)
    assert rules.LEG_PLANS["bearput"] == (("long_put", 0, (0.50, 0.60)),)
    sigma = sigma_for(SPOT, ATM_IV, DAYS)
    deltas, refs = plan_targets("bearput", SPOT, sigma)
    assert deltas == (Target("long_put", 0.50, 0.60),)
    assert refs == (Target("long_put", 100.0, 100.0),)
    p = pick(decide(bear()), BP)
    assert p.delta_target == deltas and p.reference_level == refs


def test_bear_call_plan_targets_short_call_above_spot():
    assert rules.BEARCALL_SHORT_DELTA == rules.FAR_OTM_DELTA == (0.10, 0.15)
    assert rules.LEG_PLANS["bearcall"] == (("short_call", 1, (0.10, 0.15)),)
    sigma = sigma_for(SPOT, ATM_IV, DAYS)
    deltas, refs = plan_targets("bearcall", SPOT, sigma)
    assert deltas == (Target("short_call", 0.10, 0.15),)
    assert refs == (Target("short_call", 109.22, 111.34),)  # 1.04 / 1.28 sigma above spot
    assert pick(decide(bear()), BCS).reference_level == refs


def test_bear_call_is_seller_pick_with_thin_premium_and_earnings_gating():
    assert BCS in rules.SELLER_PICKS and BCS in rules.EARNINGS_SENSITIVE
    thin = decide(bear(asset_class="hot", earnings=HOT_20))  # 0.32 < hot THIN_IV 0.35
    assert "thin_premium" in pick(thin, BCS).flags
    assert "earnings_before_expiry" in pick(thin, BCS).flags
    assert any("0.35" in w for w in thin.warnings)
    assert "thin_premium" not in pick(decide(bear()), BCS).flags


def test_bear_put_is_not_earnings_gated():
    assert BP not in rules.SELLER_PICKS and BP not in rules.EARNINGS_SENSITIVE
    assert pick(decide(bear(asset_class="hot", earnings=HOT_20)), BP).flags == ()
    held = decide(bear(asset_class="hot", earnings=HOT_SOON, holding=HELD))
    assert pick(held, BP).flags == ()


def test_hot_imminent_earnings_bear_not_held_waits_then_bear_put():
    d = decide(bear(asset_class="hot", earnings=HOT_SOON))
    assert names(d) == [WAIT, BP]
    assert reasons(d)[BCS] == "earnings_imminent"
    assert T.WARN["earnings_imminent"].format(earn_days=5, limit=7) in d.warnings


def test_bear_held_hot_imminent_earnings_unaffected():
    d = decide(bear(asset_class="hot", earnings=HOT_SOON, holding=HELD))
    assert names(d) == [RED, BP]
    assert reasons(d)[WAIT] == "wait_not_triggered"


def test_forbidden_picks_keep_long_put_and_naked():
    assert rules.FORBIDDEN_PICKS == frozenset({"long_call", "naked_call", "naked_put", "bearish",
                                               "long_put"})
    assert BP in rules.STRATEGIES and BCS in rules.STRATEGIES
    assert rules.NOT_CHOSEN_ONLY == ("long_call", "naked_call", "bearish", "wheel")
    for picks, _ in rules.LEAF_TABLE.values():
        assert not {p.strategy for p in picks} & rules.FORBIDDEN_PICKS


def _every_context():
    """Every leaf x class x earnings x IV present / missing x holding (bear: every regime x IV)."""
    for lid in sorted(rules.LEAF_TABLE):
        parts = lid.split(".")
        for cls in PIN_CLASSES:
            for earnings in _earnings_variants(cls):
                for atm in (ATM_IV, None):
                    base = dict(ticker="ZZZQ", asset_class=cls, earnings=earnings, spot=SPOT,
                                days=DAYS, portfolio_value=PV)
                    if len(parts) == 1:
                        yield lid, DecisionContext(
                            view=None, holding=None, regime=None, iv=None,
                            gate_passed=lid != rules.LEAF_GATE_FAIL,
                            history_short=lid == rules.LEAF_HISTORY_SHORT, **base)
                        continue
                    combos = ([(r, i) for r in rules.REGIME_LEAVES for i in rules.IV_LABELS]
                              if parts[0] == "bear" else [(parts[2], parts[3])])
                    for holding in PIN_HOLDINGS[parts[1]]:
                        for regime, iv_label in combos:
                            yield lid, DecisionContext(view=parts[0], holding=holding,
                                                       regime=regime, iv=_iv(iv_label, atm),
                                                       **base)


def test_every_leaf_every_class_decides_without_error():
    seen = set()
    for lid, ctx in _every_context():
        d = decide(ctx)
        assert d.leaf_id == lid
        assert 1 <= len(d.picks) <= rules.MAX_PICKS
        assert not {p.strategy for p in d.picks} & rules.FORBIDDEN_PICKS
        seen.add(lid)
    assert seen == set(rules.LEAF_TABLE)


def test_not_chosen_covers_every_strategy_key_including_the_two_new():
    assert {BP, BCS} <= set(rules.ALL_STRATEGY_KEYS)
    for ctx in (bear(), bear(holding=HELD), bear(view="bull", regime="steady_up"),
                bear(view="neutral", regime="range", holding=HELD)):
        d = decide(ctx)
        listed = [p.strategy for p in d.picks] + [n.strategy for n in d.not_chosen]
        assert sorted(listed) == sorted(rules.ALL_STRATEGY_KEYS)
    bull = reasons(decide(bear(view="bull", regime="steady_up", iv=_iv("high"))))
    assert bull[BP] == "view_not_bearish" and bull[BCS] == "view_not_bearish"
    neutral_held = reasons(decide(bear(view="neutral", regime="range", holding=HELD)))
    assert neutral_held[BP] == "view_not_bearish" and neutral_held[BCS] == "view_not_bearish"
    assert "view_not_bearish" in rules.WHY_NOT_CODES


def test_bear_against_trend_warning_only_on_steady_up():
    text = T.WARN[AGAINST]
    for regime in rules.REGIME_LEAVES:
        for holding in (NOT_HELD, HELD, NO_LOT):
            d = decide(bear(regime=regime, holding=holding))
            assert (text in d.warnings) == (regime == "steady_up"), (regime, holding.branch)
    for view in ("bull", "neutral"):
        assert text not in decide(bear(view=view, regime="steady_up")).warnings
    gate = decide(bear(regime="steady_up", gate_passed=False))
    assert gate.leaf_id == "gate_fail" and text not in gate.warnings


def test_bear_put_iv_high_warning_only_for_bear_put_with_high_iv():
    text = T.WARN[IV_HIGH_BUY]
    for label in rules.IV_LABELS:
        for holding in (NOT_HELD, HELD):
            d = decide(bear(iv=_iv(label), holding=holding))
            assert BP in names(d)
            assert (text in d.warnings) == (label == "high"), (label, holding.branch)
    assert text not in decide(bear(view="bull", regime="steady_up", iv=_iv("high"))).warnings


def test_bear_picks_carry_the_drift_warning_once():
    text = T.WARN[DRIFT]
    assert text == "历史上市场整体向上漂移，看跌策略样本里亏的概率偏高"
    for holding in (NOT_HELD, HELD, NO_LOT):
        assert decide(bear(holding=holding)).warnings.count(text) == 1
    assert decide(bear(asset_class="hot", earnings=HOT_SOON)).warnings.count(text) == 1
    for view in ("bull", "neutral"):
        assert text not in decide(bear(view=view, regime="steady_up")).warnings


def test_missing_iv_gives_ref_missing_iv_not_crash():
    d = decide(bear(iv=_iv("mid", atm=None)))
    assert names(d) == [BP, BCS]
    assert all(p.delta_target and p.reference_level is None for p in d.picks)
    assert T.WARN["ref_missing_iv"] in d.warnings
    held = decide(bear(iv=None, holding=HELD))
    assert names(held) == [RED, BP] and T.WARN["ref_missing_iv"] in held.warnings


def test_thresholds_used_lists_the_two_new_delta_constants(monkeypatch, tmp_path):
    from strategy_pick_cli_stubs import base_result
    doc, _ = base_result(monkeypatch, tmp_path, argv=("ZZZQ", "--view", "不看好"))
    assert doc["leaf_id"] == "bear.not_held"
    used = doc["thresholds_used"]
    assert used["BEARPUT_LONG_DELTA"] == [0.50, 0.60]
    assert used["BEARCALL_SHORT_DELTA"] == [0.10, 0.15]
    bull, _ = base_result(monkeypatch, tmp_path)  # bull leaves keep thresholds_used unchanged
    assert "BEARPUT_LONG_DELTA" not in bull["thresholds_used"]
    assert "BEARCALL_SHORT_DELTA" not in bull["thresholds_used"]


STRATEGIES_DIR = REPO_ROOT / "wiki" / "options" / "strategies"


def _block(page: str, label: str) -> str:
    lines = (STRATEGIES_DIR / page).read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"**{label}**"))
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith("**") or ln.startswith("#"):
            break
        out.append(ln)
    return "\n".join(out)


def test_why_not_sentences_for_new_keys_cite_existing_pages():
    assert T.STRATEGY_PAGES[BP] == "bear-put-debit-spread.md"
    assert T.STRATEGY_PAGES[BCS] == "bear-call-credit-spread.md"
    for key in (BP, BCS):
        assert (STRATEGIES_DIR / T.STRATEGY_PAGES[key]).is_file()
        for _, code in rules.REASON_RULES[key]:
            assert T.WHY_NOT[(key, code)].strip()
    assert "温和看跌" in T.WHY_NOT[(BP, "view_not_bearish")]
    assert "温和看跌" in _block("bear-put-debit-spread.md", "适用")
    assert "看跌到横盘" in T.WHY_NOT[(BCS, "view_not_bearish")]
    assert "看跌到横盘" in _block("bear-call-credit-spread.md", "适用")
    assert "Bear Call 页" in T.WHY_NOT[(BCS, "earnings_imminent")]
    assert "事件窗口" in _block("bear-call-credit-spread.md", "不适用")
    assert "方向冲突" in T.WHY_NOT[(BCS, "held_use_shares")]
    banned = ("Put 借方价差", "Call 贷方价差", "买入 Put")
    texts = [T.STRATEGY_LABELS[BP], T.STRATEGY_LABELS[BCS], T.NEXT_COMMAND_CAVEATS[BP],
             T.NEXT_COMMAND_CAVEATS[BCS], T.WARN["bear_held"], T.WARN["bear_not_held"],
             T.WARN[AGAINST], T.WARN[IV_HIGH_BUY], T.WARN[DRIFT]]
    texts += [s for (k, _), s in T.WHY_NOT.items() if k in (BP, BCS)]
    for s in texts:
        assert not any(b in s for b in banned), s
    assert (T.STRATEGY_LABELS[BP], T.STRATEGY_LABELS[BCS]) == ("Bear Put 价差", "Bear Call 价差")
    assert "全亏" in T.NEXT_COMMAND_CAVEATS[BP]
    assert "宽度减权利金" in T.NEXT_COMMAND_CAVEATS[BCS]
    assert "Collar 锁一个下限（/collar-screen）" in T.WARN["bear_held"]
    assert "Bear Put 价差（/bearput-sim）" in T.WARN["bear_held"]
    assert "不做期权" not in T.WARN["bear_not_held"]


def test_bearish_not_chosen_sentence_mentions_longput_only():
    sentence = T.WHY_NOT[("bearish", "never_bearish")]
    assert sentence == "买入 Put 单腿：不单独推荐，需要时手动用 /longput-sim"
    assert "/bearput-sim" not in sentence and "/bearcall-sim" not in sentence
    for ctx in (bear(), bear(view="bull", regime="steady_up")):
        assert reasons(decide(ctx))["bearish"] == "never_bearish"
    others = [s for (k, c), s in T.WHY_NOT.items() if (k, c) != ("bearish", "never_bearish")]
    others += list(T.STRATEGY_LABELS.values()) + list(T.WARN.values())
    assert not any("买入 Put" in s for s in others)
