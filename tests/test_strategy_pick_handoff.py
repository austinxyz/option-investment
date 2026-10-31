"""Tests for the pure Delta-target handoff mapping (design 2026-10-26, D1/D2/6.3, section 8).

Tickers are invented (ZZZQ); TSLA / BRK.B appear only as public example text. Expected commands
are spelled out per (strategy, plan) so a change to a constant that moves a hand-off shows up here.
"""
from dataclasses import asdict
from pathlib import Path

import pytest

import bullcall_sim
import call_screen
import condor_sim
import put_screen
import put_screen_rules as psr
import put_screen_wiki
import spread_core
import spread_sim
import strategy_pick_rules as rules
import strategy_pick_text as text
import strategy_pick_handoff as H
from strategy_pick_render import _range_text
from strategy_pick_tree import Target, plan_targets

T = "ZZZQ"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
THRESHOLD_LITERALS = ("0.10", "0.15", "0.20", "0.50", "0.60", "0.05", "0.40")

EXPECTED = {
    ("cash_secured_put", "put_normal"): (f"/put-screen suggest --dte monthly --tickers {T}", ()),
    ("cash_secured_put", "put_far"):
        (f"/put-screen suggest --dte monthly --tickers {T} --delta 0.15", ()),
    ("bull_put_spread", "put_normal"): (f"/spread-sim suggest {T}", (f"open {T} 0.20",)),
    ("bull_put_spread", "put_far"):
        (f"/spread-sim suggest {T}", (f"open {T} 0.15", f"open {T} 0.10")),
    ("iron_condor", "condor"): (f"/condor-sim suggest {T}", (f"open {T} 0.15",)),
    ("bull_call_spread", "bullcall"):
        (f"/bullcall-sim suggest {T}", (f"open {T} 0.60", f"open {T} 0.50")),
    ("covered_call", "call_normal"): ("/call-screen suggest --dte monthly", ()),
    ("covered_call", "call_far"): ("/call-screen suggest --dte monthly --delta 0.15", ()),
    ("collar", "collar"): ("/collar-screen suggest", ()),
    ("protective_put", "protective"): ("/collar-screen suggest", ()),
}


def _expand(pick):
    if pick.strategy == rules.SELL_PAIR:
        return [(rules.CSP_KEY, pick.plan), (rules.BULL_PUT_KEY, pick.plan)]
    return [(pick.strategy, pick.plan)]


def _targets(plan):
    return plan_targets(plan, 100.0, None)[0]


def _pick(strategy, plan):
    return {"strategy": strategy, "delta_target": [asdict(t) for t in _targets(plan)]}


def _lines(*pairs, ticker=T):
    result = {"ticker": ticker, "picks": [_pick(s, p) for s, p in pairs]}
    return H.handoff_lines(result, lambda targets: _range_text(targets, 2))


def _body_lines(*pairs, ticker=T):
    return [ln for ln in _lines(*pairs, ticker=ticker) if ln.startswith("- ")]


# --- Step 1: the whole leaf table --------------------------------------------------------

@pytest.mark.parametrize("leaf", sorted(rules.LEAF_TABLE))
def test_every_leaf_pick_handoff_matches_table(leaf):
    picks, _ = rules.LEAF_TABLE[leaf]
    for leaf_pick in picks:
        for strategy, plan in _expand(leaf_pick):
            targets = _targets(plan)
            result = H.handoff(strategy, targets, T)
            if not targets:
                assert result is None, (leaf, strategy)
                continue
            assert result is not None, (leaf, strategy)
            assert (result.command, result.open_args) == EXPECTED[(strategy, plan)], leaf


def test_handoff_kind_covers_leg_plans():
    for picks, _ in rules.LEAF_TABLE.values():
        for leaf_pick in picks:
            if leaf_pick.plan is None:
                continue
            for strategy, _plan in _expand(leaf_pick):
                assert strategy in H.HANDOFF_KIND, strategy


def test_condor_leg_plans_equal():
    (_, _, put_range), (_, _, call_range) = rules.LEG_PLANS["condor"]
    assert put_range == call_range


# --- Step 2: agreement with the target commands ------------------------------------------

def _all_handoffs():
    seen = {}
    for picks, _ in rules.LEAF_TABLE.values():
        for leaf_pick in picks:
            for strategy, plan in _expand(leaf_pick):
                if plan is not None:
                    seen[(strategy, plan)] = H.handoff(strategy, _targets(plan), T)
    return seen


def _args_after_slash(command):
    return command.split()[1:]


def test_open_buckets_are_target_choices():
    parsers = {"bull_put_spread": (spread_sim, spread_core.DELTA_BUCKETS),
               "iron_condor": (condor_sim, spread_core.DELTA_BUCKETS),
               "bull_call_spread": (bullcall_sim, spread_core.BULLCALL_DELTA_BUCKETS)}
    checked = 0
    for (strategy, _plan), h in _all_handoffs().items():
        if strategy not in parsers:
            continue
        module, buckets = parsers[strategy]
        for arg in h.open_args:
            tokens = arg.split()
            assert float(tokens[2]) in buckets
            parsed = module.build_parser().parse_args(tokens)
            assert parsed.bucket == float(tokens[2]) and parsed.ticker == T
            checked += 1
    assert checked >= 6


def test_delta_flag_parses_in_target_cli():
    parsers = {"cash_secured_put": put_screen, "covered_call": call_screen}
    flagged = 0
    for (strategy, _plan), h in _all_handoffs().items():
        if strategy not in parsers:
            continue
        args = _args_after_slash(h.command)
        parsed = parsers[strategy].build_parser().parse_args(args)
        if "--delta" in args:
            value = float(args[args.index("--delta") + 1])
            assert psr.DELTA_ARG_MIN <= value <= psr.DELTA_ARG_MAX
            assert abs(value * 100 - round(value * 100)) < 1e-9
            assert parsed.delta == value
            flagged += 1
    assert flagged == 2


def test_command_prefix_is_next_command():
    for (strategy, _plan), h in _all_handoffs().items():
        assert h.command.startswith(text.NEXT_COMMANDS[strategy].format(ticker=T))


def test_put_far_delta_equals_rank_delta():
    h = H.handoff("cash_secured_put", _targets("put_far"), T)
    assert float(h.command.split("--delta ")[1]) == rules.RANK_DELTA_BY_PLAN["put_far"]


@pytest.mark.parametrize("strategy,plan", [("cash_secured_put", "put_normal"),
                                           ("covered_call", "call_normal")])
def test_default_target_omits_delta_flag(strategy, plan):
    assert "--delta" not in H.handoff(strategy, _targets(plan), T).command


def test_no_threshold_literals_in_handoff_module():
    source = (SCRIPTS / "strategy_pick_handoff.py").read_text(encoding="utf-8")
    for literal in THRESHOLD_LITERALS:
        assert literal not in source, literal


def test_handoff_module_no_reverse_imports():
    for name in ("strategy_pick_rules.py", "strategy_pick_text.py"):
        assert "strategy_pick_handoff" not in (SCRIPTS / name).read_text(encoding="utf-8")


# --- Step 3: wording and edges -----------------------------------------------------------

def test_lines_tsla_example():
    got = _lines(("bull_put_spread", "put_far"), ("cash_secured_put", "put_far"), ticker="TSLA")
    assert got == [
        "### 把 Delta 目标带到下一步", "",
        "- Bull Put 价差（短 Put 0.10–0.15）：先 `/spread-sim suggest TSLA`，"
        "再 `open TSLA 0.15`（或更价外的 `open TSLA 0.10`）",
        "- 现金担保 Put（短 Put 0.10–0.15）：`/put-screen suggest --dte monthly --tickers TSLA "
        "--delta 0.15`（目标 0.15，价差过宽时往外最多到 0.10）",
        ""]


def test_lines_base_example():
    assert _body_lines(("cash_secured_put", "put_normal"), ("bull_put_spread", "put_normal")) == [
        "- 现金担保 Put（短 Put 0.20）：`/put-screen suggest --dte monthly --tickers ZZZQ`"
        "（目标 0.20，价差过宽时往外最多到 0.10）",
        "- Bull Put 价差（短 Put 0.20）：先 `/spread-sim suggest ZZZQ`，再 `open ZZZQ 0.20`"]


def test_lines_covered_call_far():
    assert _body_lines(("covered_call", "call_far")) == [
        "- 备兑 Call（短 Call 0.10–0.15）：`/call-screen suggest --dte monthly --delta 0.15`"
        "（目标 0.15，价差过宽时往外最多到 0.10）；它扫全部持股，`--delta` 对这次扫的每只持股都生效"]


def test_lines_condor():
    assert _body_lines(("iron_condor", "condor"), ticker="T") == [
        "- 铁鹰（短 Put 0.15；短 Call 0.15）：先 `/condor-sim suggest T`，再 `open T 0.15`"]


def test_lines_bullcall():
    assert _body_lines(("bull_call_spread", "bullcall"), ticker="T") == [
        "- Bull Call 价差（长 Call 0.50–0.60）：先 `/bullcall-sim suggest T`，"
        "再 `open T 0.60`（或更便宜的 `open T 0.50`）"]


def test_lines_collar_and_protective():
    tail = ("，它不按 Delta 选：按现价 90% / 85% / 80% 三档列出 Put{call}，Delta 目标带不过去；"
            '对照上表"参考价位"挑最接近的一档')
    assert _body_lines(("collar", "collar")) == [
        "- Collar（长 Put 0.20–0.30；短 Call 0.20）：`/collar-screen suggest`"
        + tail.format(call="（Collar 的 Call 取零成本）")]
    protective = _body_lines(("protective_put", "protective"))
    assert protective == ["- 保护性 Put（长 Put 0.20–0.30）：`/collar-screen suggest`"
                          + tail.format(call="")]
    assert "Collar 的 Call" not in protective[0]


@pytest.mark.parametrize("strategy", ["hold", "no_options", "wait_earnings", "reduce_position"])
def test_no_section_when_no_targets(strategy):
    assert H.handoff_lines({"ticker": T, "picks": [{"strategy": strategy, "delta_target": []}]},
                           lambda targets: "") == []


def test_wait_earnings_first_only_second_line():
    result = {"ticker": T, "picks": [{"strategy": "wait_earnings", "delta_target": []},
                                     _pick("cash_secured_put", "put_normal")]}
    got = H.handoff_lines(result, lambda targets: _range_text(targets, 2))
    assert len(got) == 4 and got[2].startswith("- 现金担保 Put（短 Put 0.20）：")


def test_handoff_body_has_no_label_prefix():
    body = H.handoff_body(_pick("bull_put_spread", "put_normal"), T)
    assert body == f"先 `/spread-sim suggest {T}`，再 `open {T} 0.20`"
    assert H.handoff_body(_pick("hold", None), T) is None


def test_nearest_bucket_off_grid():
    buckets = spread_core.DELTA_BUCKETS
    assert H.nearest_bucket(0.25, 0.30, buckets, "short_put") == (0.20, ())
    assert H.nearest_bucket(0.125, 0.125, buckets, "short_put") == (0.10, ())
    assert H.nearest_bucket(0.45, 0.45, spread_core.BULLCALL_DELTA_BUCKETS, "long_call") == (0.50, ())
    assert H.nearest_bucket(0.10, 0.15, buckets, "short_put") == (0.15, (0.10,))
    assert H.nearest_bucket(0.20, 0.20, buckets, "short_put") == (0.20, ())


def test_condor_unequal_legs_raises():
    unequal = [{"leg": "short_put", "low": 0.15, "high": 0.15},
               {"leg": "short_call", "low": 0.10, "high": 0.15}]
    with pytest.raises(ValueError):
        H.handoff("iron_condor", unequal, T)


def test_dotted_ticker_verbatim():
    assert put_screen_wiki._checked_ticker("BRK.B") == "BRK.B"
    csp = H.handoff("cash_secured_put", _targets("put_far"), "BRK.B")
    assert "--tickers BRK.B " in csp.command
    spread = H.handoff("bull_put_spread", _targets("put_far"), "BRK.B")
    assert spread.open_args[0] == "open BRK.B 0.15"
    assert spread_sim.build_parser().parse_args(spread.open_args[0].split()).ticker == "BRK.B"


def test_accepts_json_dicts_and_targets():
    for plan, strategy in (("put_far", "bull_put_spread"), ("condor", "iron_condor"),
                           ("collar", "collar"), ("call_far", "covered_call")):
        as_targets = tuple(_targets(plan))
        as_dicts = [asdict(t) for t in as_targets]
        assert isinstance(as_targets[0], Target)
        assert H.handoff(strategy, as_targets, T) == H.handoff(strategy, as_dicts, T)


def test_empty_targets_none():
    assert H.handoff("hold", [], T) is None
