"""Golden decision table for /strategy-pick (design 6.8, 6.9, 6.10, 6.11, section 10).

Every expectation below was written from the spec text, not from running the tree. Context for the
135 combinations: invented ZZZQ, bluechip, spot 100.00, ATM IV 0.32 (>= bluechip THIN_IV 0.20, so no
thin-IV flag from the class check), 28 days, portfolio 200,000, next earnings 2027-01-28 (outside
the 45-day window). With bluechip and 92.56 x 100 / 200,000 = 4.6% (or 90.78 x 100 / 200,000 = 4.5%
for far OTM) <= 10%, "CSP or Bull Put" always ranks cash-secured put first here.
"""
from datetime import date

import pytest

from strategy_pick_fixtures import SNAPSHOT
import strategy_pick_rules as rules
import strategy_pick_text as T
from strategy_pick_tree import (DecisionContext, IvPosition, NeedsView, decide, earnings_state,
                                holding_state, resolve_view)

P, B, BC, IC = "cash_secured_put", "bull_put_spread", "bull_call_spread", "iron_condor"
CC, COL, PP, H = "covered_call", "collar", "protective_put", "hold"
NO, W, R = "no_options", "wait_earnings", "reduce_position"
FAR, THIN, WIN = "far_otm", "thin_premium", "required_win_rate"
WANT, AVG, EARN, REDUCE = "want_shares_only", "premium_average", "earnings_before_expiry", "reduce_hint"

REGIMES = ("steady_up", "high_vol", "range", "down", "unclear")
IVS = ("high", "mid", "low")

# (view, held, regime, iv) -> (picks in order, {strategy: its exact flag set; absent = no flags})
EXPECTED = {
    # 6.8 bull + not held
    ("bull", "not_held", "steady_up", "high"): ([P, B], {}),
    ("bull", "not_held", "steady_up", "mid"): ([B, BC], {}),
    ("bull", "not_held", "steady_up", "low"): ([BC], {}),
    ("bull", "not_held", "high_vol", "high"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("bull", "not_held", "high_vol", "mid"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("bull", "not_held", "high_vol", "low"): ([P, B], {P: {FAR, THIN}, B: {FAR, THIN}}),
    ("bull", "not_held", "range", "high"): ([IC], {}),
    ("bull", "not_held", "range", "mid"): ([IC], {IC: {WIN}}),
    ("bull", "not_held", "range", "low"): ([IC], {IC: {THIN, WIN}}),
    # 6.9 bull + not held + down / unclear (unclear = the neutral not-held table)
    ("bull", "not_held", "down", "high"): ([P, NO], {P: {FAR, WANT}}),
    ("bull", "not_held", "down", "mid"): ([P, NO], {P: {FAR, WANT}}),
    ("bull", "not_held", "down", "low"): ([P, NO], {P: {FAR, WANT}}),
    ("bull", "not_held", "unclear", "high"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("bull", "not_held", "unclear", "mid"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("bull", "not_held", "unclear", "low"): ([NO, P], {P: {FAR, THIN}}),
    # 6.8 bull + held (one account >= 100 shares)
    ("bull", "held", "steady_up", "high"): ([CC], {CC: {FAR}}),
    ("bull", "held", "steady_up", "mid"): ([H, CC], {CC: {FAR, AVG}}),
    ("bull", "held", "steady_up", "low"): ([H, PP], {}),
    ("bull", "held", "high_vol", "high"): ([COL], {}),
    ("bull", "held", "high_vol", "mid"): ([COL], {}),
    ("bull", "held", "high_vol", "low"): ([COL], {}),
    ("bull", "held", "range", "high"): ([CC], {}),
    ("bull", "held", "range", "mid"): ([CC], {}),
    ("bull", "held", "range", "low"): ([CC], {CC: {THIN}}),
    # 6.9 bull + held + down / unclear
    ("bull", "held", "down", "high"): ([COL], {}),
    ("bull", "held", "down", "mid"): ([COL], {}),
    ("bull", "held", "down", "low"): ([COL], {}),
    ("bull", "held", "unclear", "high"): ([CC], {CC: {FAR}}),
    ("bull", "held", "unclear", "mid"): ([CC], {CC: {FAR}}),
    ("bull", "held", "unclear", "low"): ([CC], {CC: {FAR}}),
    # 6.8 neutral + not held: far-OTM sellers (condor when range); IV low: none first + thin CSP;
    # 6.9 neutral + down + not held: no options
    ("neutral", "not_held", "steady_up", "high"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("neutral", "not_held", "steady_up", "mid"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("neutral", "not_held", "steady_up", "low"): ([NO, P], {P: {FAR, THIN}}),
    ("neutral", "not_held", "high_vol", "high"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("neutral", "not_held", "high_vol", "mid"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("neutral", "not_held", "high_vol", "low"): ([NO, P], {P: {FAR, THIN}}),
    ("neutral", "not_held", "range", "high"): ([IC], {}),
    ("neutral", "not_held", "range", "mid"): ([IC], {}),
    ("neutral", "not_held", "range", "low"): ([NO, P], {P: {FAR, THIN}}),
    ("neutral", "not_held", "down", "high"): ([NO], {}),
    ("neutral", "not_held", "down", "mid"): ([NO], {}),
    ("neutral", "not_held", "down", "low"): ([NO], {}),
    ("neutral", "not_held", "unclear", "high"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("neutral", "not_held", "unclear", "mid"): ([P, B], {P: {FAR}, B: {FAR}}),
    ("neutral", "not_held", "unclear", "low"): ([NO, P], {P: {FAR, THIN}}),
    # 6.8 neutral + held: high vol or down -> collar; otherwise covered call (far OTM)
    ("neutral", "held", "steady_up", "high"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "steady_up", "mid"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "steady_up", "low"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "high_vol", "high"): ([COL], {}),
    ("neutral", "held", "high_vol", "mid"): ([COL], {}),
    ("neutral", "held", "high_vol", "low"): ([COL], {}),
    ("neutral", "held", "range", "high"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "range", "mid"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "range", "low"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "down", "high"): ([COL], {}),
    ("neutral", "held", "down", "mid"): ([COL], {}),
    ("neutral", "held", "down", "low"): ([COL], {}),
    ("neutral", "held", "unclear", "high"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "unclear", "mid"): ([CC], {CC: {FAR}}),
    ("neutral", "held", "unclear", "low"): ([CC], {CC: {FAR}}),
}

HOLDINGS = {"held": holding_state([150], [True]), "not_held": holding_state([], []),
            "held_no_lot": holding_state([60, 50], [True, True]),
            "short_ltcg": holding_state([150], [False])}
NO_EARNINGS = earnings_state("bluechip", date(2027, 1, 28), SNAPSHOT, None)


def iv_at(label: str, atm: float | None = 0.32) -> IvPosition:
    method = rules.IV_METHOD_PRIMARY if atm is not None else rules.IV_METHOD_FALLBACK
    return IvPosition(atm_iv=atm, percentile=None, label=label, method=method, iv_rv_ratio=None)


def make_ctx(view, holding, regime, iv_label, **over) -> DecisionContext:
    base = dict(ticker="ZZZQ", view=view, holding=HOLDINGS[holding], regime=regime,
                iv=iv_at(iv_label), asset_class="bluechip", earnings=NO_EARNINGS, spot=100.0, days=28,
                portfolio_value=200_000.0)
    base.update(over)
    return DecisionContext(**base)


def _row(rid, ctx, leaf, picks, flags=None, absent=None, warns=()):
    return dict(id=rid, ctx=ctx, leaf=leaf, picks=picks, flags=flags or {}, absent=absent or {},
                warns=tuple(warns))


# Delta targets from the spec 6.8 tables, written as numbers (not read from rules):
# normal sell 0.20; far OTM 0.10-0.15; Bull Call long 0.50-0.60; condor shorts 0.15;
# Collar / protective put long put 0.20-0.30, Collar short call 0.20.
_SPEC_DELTAS = {
    (P, False): (("short_put", 0.20, 0.20),), (P, True): (("short_put", 0.10, 0.15),),
    (B, False): (("short_put", 0.20, 0.20),), (B, True): (("short_put", 0.10, 0.15),),
    (CC, False): (("short_call", 0.20, 0.20),), (CC, True): (("short_call", 0.10, 0.15),),
    (BC, False): (("long_call", 0.50, 0.60),),
    (IC, False): (("short_put", 0.15, 0.15), ("short_call", 0.15, 0.15)),
    (COL, False): (("long_put", 0.20, 0.30), ("short_call", 0.20, 0.20)),
    (PP, False): (("long_put", 0.20, 0.30),),
}


def spec_deltas(strategy: str, far: bool) -> tuple:
    return _SPEC_DELTAS.get((strategy, far), ())


def warn(key: str, **kw) -> str:
    return T.WARN[key].format(**kw)


NO_LOT_WARN = warn("no_lot", max_lot=60, lot=100)
REDUCE_WARN = warn("reduce_hint", lot=100)


def _combination_rows() -> list[dict]:
    rows = []
    for view in ("bull", "neutral", "bear"):
        for holding in ("held", "not_held", "held_no_lot"):
            for regime in REGIMES:
                for iv in IVS:
                    rows.append(_expected_row(view, holding, regime, iv))
    return rows


def _expected_row(view, holding, regime, iv) -> dict:
    rid = f"{view}-{holding}-{regime}-{iv}"
    ctx = make_ctx(view, holding, regime, iv)
    # 6.11: no single account >= 100 shares -> no-lot warning; neutral / bear add the reduce hint
    no_lot = [NO_LOT_WARN] if holding == "held_no_lot" else []
    if view == "bear":  # 6.8 bear: held lot -> reduce + collar; otherwise no options
        if holding == "held":
            return _row(rid, ctx, "bear.held", [R, COL], warns=[warn("bear_held")])
        flags = {NO: {REDUCE}} if holding == "held_no_lot" else {}
        reduce = [REDUCE_WARN] if no_lot else []
        return _row(rid, ctx, "bear.not_held", [NO], flags, warns=no_lot + reduce)
    branch = "held" if holding == "held" else "not_held"  # 6.11: no lot -> not-held branch
    picks, flags = EXPECTED[(view, branch, regime, iv)]
    flags = {k: set(v) for k, v in flags.items()}
    absent, warns = {}, list(no_lot)
    if holding == "held_no_lot":
        if view == "neutral":
            flags.setdefault(picks[0], set()).add(REDUCE)
            warns.append(REDUCE_WARN)
        else:
            absent = {p: {REDUCE} for p in picks}
    if (view, branch, regime) == ("bull", "not_held", "down"):  # 6.9
        warns.append(warn("bull_down"))
    return _row(rid, ctx, f"{view}.{branch}.{regime}.{iv}", list(picks), flags, absent, warns)


def _special_rows() -> list[dict]:
    hot_soon = earnings_state("hot", date(2026, 10, 28), SNAPSHOT, None)   # 5 days
    hot_45 = earnings_state("hot", date(2026, 11, 12), SNAPSHOT, None)     # 20 days
    etf = earnings_state("etf", date(2026, 10, 28), SNAPSHOT, date(2026, 11, 6))
    blue_45 = earnings_state("bluechip", date(2026, 11, 12), SNAPSHOT, None)
    hot = dict(asset_class="hot")
    return [
        _row("gate_fail", make_ctx(None, "not_held", None, "high", iv=None, gate_passed=False),
             "gate_fail", [NO]),
        _row("history_short", make_ctx("bull", "not_held", None, "high", iv=None,
                                       history_short=True), "history_short", [NO]),
        # 6.10: imminent -> wait first, original first second, original second dropped
        _row("hot_imminent_two", make_ctx("bull", "not_held", "steady_up", "mid", earnings=hot_soon,
                                          **hot), "bull.not_held.steady_up.mid", [W, B],
             {B: {EARN, THIN}}, warns=[warn("earnings_imminent", earn_days=5, limit=7)]),
        _row("hot_imminent_one", make_ctx("bull", "held", "range", "mid", earnings=hot_soon, **hot),
             "bull.held.range.mid", [W, CC], {CC: {EARN, THIN}}),
        # Bull Call is not one of the four earnings-sensitive picks named in 6.10: unchanged
        _row("hot_imminent_buyer_only", make_ctx("bull", "not_held", "steady_up", "low",
                                                 earnings=hot_soon, **hot),
             "bull.not_held.steady_up.low", [BC], {}, {BC: {EARN}}),
        _row("hot_in_45", make_ctx("neutral", "not_held", "range", "high", earnings=hot_45, **hot),
             "neutral.not_held.range.high", [IC], {IC: {EARN, THIN}},
             warns=[warn("earnings_avoid", date="2026-11-12")]),
        _row("etf_ex_div", make_ctx("bull", "held", "range", "high", earnings=etf,
                                    asset_class="etf"), "bull.held.range.high", [CC],
             {CC: {"ex_dividend"}}, {CC: {EARN}}, warns=[warn("ex_dividend", date="2026-11-06")]),
        _row("etf_csp_first", make_ctx("bull", "not_held", "steady_up", "high", earnings=etf,
                                       asset_class="etf"), "bull.not_held.steady_up.high", [P, B]),
        # 6.10 bluechip with earnings in the window: warn only, picks and flags unchanged
        _row("bluechip_earnings_warn_only", make_ctx("bull", "not_held", "steady_up", "high",
                                                     earnings=blue_45),
             "bull.not_held.steady_up.high", [P, B],
             warns=[warn("earnings_warn_only", date="2026-11-12", window=45)]),
        # 6.11 Collar / protective put on a lot short of LTCG: flag plus the Collar-page warning
        _row("ltcg_collar", make_ctx("bull", "short_ltcg", "high_vol", "mid"),
             "bull.held.high_vol.mid", [COL], {COL: {"ltcg_reset"}}, warns=[warn("ltcg_reset")]),
        _row("ltcg_protective", make_ctx("bull", "short_ltcg", "steady_up", "low"),
             "bull.held.steady_up.low", [H, PP], {PP: {"ltcg_reset"}},
             warns=[warn("ltcg_reset")]),
    ]


def _variant_rows() -> list[dict]:
    hot_20 = earnings_state("hot", date(2026, 11, 12), SNAPSHOT, None)
    hot_5 = earnings_state("hot", date(2026, 10, 28), SNAPSHOT, None)
    return [
        _row("base", make_ctx("bull", "not_held", "steady_up", "high"),
             "bull.not_held.steady_up.high", [P, B], {}, {P: {FAR}, B: {FAR}}),
        _row("V1", make_ctx("bull", "held", "steady_up", "high"), "bull.held.steady_up.high",
             [CC], {CC: {FAR}}),
        _row("V2", make_ctx("bull", "not_held", "steady_up", "low"),
             "bull.not_held.steady_up.low", [BC]),
        _row("V3", make_ctx("bull", "not_held", "high_vol", "high"), "bull.not_held.high_vol.high",
             [P, B], {P: {FAR}, B: {FAR}}),
        _row("V4", make_ctx("bull", "not_held", "steady_up", "high", asset_class="hot",
                            earnings=hot_20), "bull.not_held.steady_up.high", [B, P],
             {B: {EARN, THIN}, P: {EARN, THIN}},
             warns=[warn("earnings_avoid", date="2026-11-12"),
                    warn("earnings_cross_expiry", days=28, earn_days=20),
                    warn("thin_iv", atm_iv=0.32, thin=0.35)]),
        _row("V5", make_ctx("neutral", "not_held", "steady_up", "high"),
             "neutral.not_held.steady_up.high", [P, B], {P: {FAR}, B: {FAR}}),
        _row("V6", make_ctx("bull", "not_held", "steady_up", "high", gate_passed=False),
             "gate_fail", [NO]),
        _row("V7", make_ctx("bear", "held", "steady_up", "high"), "bear.held", [R, COL],
             warns=[warn("bear_held")]),
        _row("V8", make_ctx("bull", "not_held", "steady_up", "high", asset_class="hot",
                            earnings=hot_5), "bull.not_held.steady_up.high", [W, B], {B: {EARN, THIN}},
             warns=[warn("earnings_imminent", earn_days=5, limit=7)]),
        _row("V9", make_ctx("neutral", "held_no_lot", "steady_up", "high"),
             "neutral.not_held.steady_up.high", [P, B], {P: {FAR, REDUCE}, B: {FAR}},
             warns=[NO_LOT_WARN, REDUCE_WARN]),
        _row("V10", make_ctx("bull", "not_held", "unclear", "high"), "bull.not_held.unclear.high",
             [P, B], {P: {FAR}, B: {FAR}}),
    ]


COMBINATIONS = _combination_rows()
GOLDEN = COMBINATIONS + _special_rows() + _variant_rows()
DECISIONS = {row["id"]: decide(row["ctx"]) for row in GOLDEN}


def test_golden_has_135_combinations():
    assert len(COMBINATIONS) == 135
    assert len({r["id"] for r in GOLDEN}) == len(GOLDEN)


@pytest.mark.parametrize("row", GOLDEN, ids=[r["id"] for r in GOLDEN])
def test_golden_row(row):
    d = DECISIONS[row["id"]]
    assert d.leaf_id == row["leaf"]
    assert [p.strategy for p in d.picks] == row["picks"]
    flags = {p.strategy: set(p.flags) for p in d.picks}
    assert set(row["flags"]) <= set(flags), row["flags"]
    for strategy, got in flags.items():  # exact flag set per pick
        assert got == set(row["flags"].get(strategy, ())), (strategy, got)
    for strategy, banned in row["absent"].items():
        assert not banned & flags[strategy], (strategy, flags[strategy])
    assert [p.rank for p in d.picks] == list(range(1, len(d.picks) + 1))
    for w in row["warns"]:
        assert w in d.warnings, (w, d.warnings)
    for p in d.picks:
        got = tuple((t.leg, t.low, t.high) for t in p.delta_target)
        assert got == spec_deltas(p.strategy, FAR in p.flags), (p.strategy, got)


def test_golden_v8_drops_csp_as_earnings_imminent():
    d = DECISIONS["V8"]
    assert next(n.reason_code for n in d.not_chosen if n.strategy == P) == "earnings_imminent"


def test_golden_v9_collar_is_held_no_lot_and_v3_bull_call_high_vol():
    v9 = {n.strategy: n.reason_code for n in DECISIONS["V9"].not_chosen}
    assert v9[COL] == "held_no_lot" and v9[CC] == "held_no_lot"
    v3 = {n.strategy: n.reason_code for n in DECISIONS["V3"].not_chosen}
    assert v3[BC] == "high_vol_buyer"
    base = {n.strategy: n.reason_code for n in DECISIONS["base"].not_chosen}
    assert base[BC] == "iv_high_buyer" and base[IC] == "trend_not_range"
    assert base[CC] == "not_held" and base["wheel"] == "wheel_is_flow"
    assert base["long_call"] == "no_tool" and base["naked_call"] == "never_naked"
    assert base["bearish"] == "never_bearish"


def test_golden_v5_no_thesis_needs_view():
    assert isinstance(resolve_view(None, None), NeedsView)
    assert resolve_view(None, "中性").view == "neutral"


def test_golden_no_forbidden_pick_anywhere():
    for d in DECISIONS.values():
        assert not {p.strategy for p in d.picks} & rules.FORBIDDEN_PICKS


def test_golden_bear_rows_have_no_bullish_open():
    for row in GOLDEN:
        if row["ctx"].view == "bear":
            assert not {p.strategy for p in DECISIONS[row["id"]].picks} & rules.BULLISH_OPEN


def test_golden_held_no_lot_never_collar_or_protective_put():
    rows = [r for r in GOLDEN if r["ctx"].holding is HOLDINGS["held_no_lot"]]
    assert len(rows) >= 45
    for row in rows:
        assert not {p.strategy for p in DECISIONS[row["id"]].picks} & {COL, PP}


def test_golden_picks_at_most_two():
    assert all(1 <= len(d.picks) <= 2 for d in DECISIONS.values())


def test_golden_every_pick_has_next_command():
    for d in DECISIONS.values():
        for p in d.picks:
            assert p.next_command.startswith("/") and "{" not in p.next_command


def test_golden_not_chosen_covers_all_other_strategies():
    every = set(rules.STRATEGIES) | set(rules.NOT_CHOSEN_ONLY)
    assert {"long_call", "naked_call", "bearish", "wheel"} <= every
    for d in DECISIONS.values():
        picked = [p.strategy for p in d.picks]
        skipped = [n.strategy for n in d.not_chosen]
        assert set(picked) | set(skipped) >= every
        assert not set(picked) & set(skipped) and len(skipped) == len(set(skipped))
        assert all(n.reason_text and n.page.endswith(".md") for n in d.not_chosen)
