"""Tests for scripts/strategy_pick_frompick.py: reading today's /strategy-pick result (--from-pick).

See docs/superpowers/specs/2026-10-28-from-pick-design.md. No network, no repo data: every pick
file lives in tmp_path.
"""
from datetime import date
from pathlib import Path
import json
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import put_screen_rules as psr
import spread_core
import strategy_pick_frompick as fp
import strategy_pick_handoff as H

TODAY = date(2026, 10, 28)
FAR = [{"leg": "short_put", "low": 0.10, "high": 0.15}]
CALL_FAR = [{"leg": "short_call", "low": 0.10, "high": 0.15}]


def write_pick(folder: Path, ticker="ZZZQ", run_date="2026-10-28", picks=None, name=None,
               text=None, encoding="utf-8") -> Path:
    if picks is None:
        picks = [{"strategy": "bull_put_spread", "rank": 1, "delta_target": FAR},
                 {"strategy": "cash_secured_put", "rank": 2, "delta_target": FAR}]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"{TODAY.isoformat()}-{ticker}.json")
    if text is None:
        text = json.dumps({"ticker": ticker, "run_date": run_date, "snapshot_date": run_date,
                           "picks": picks}, ensure_ascii=False, indent=2)
    path.write_text(text, encoding=encoding)
    return path


def load(tmp_path, ticker="ZZZQ", strategy="cash_secured_put"):
    return fp.load_pick(ticker, strategy, today=TODAY, picks_dir=tmp_path)


# --- loader ----------------------------------------------------------------------------------------
def test_load_ok(tmp_path):
    write_pick(tmp_path)
    pick = load(tmp_path)
    assert pick.ticker == "ZZZQ" and pick.strategy == "cash_secured_put"
    assert pick.run_date == "2026-10-28" and pick.rank == 2
    assert [(t.leg, t.low, t.high) for t in pick.delta_target] == [("short_put", 0.10, 0.15)]
    assert fp.delta_for_screen(pick) == 0.15


def test_lowercase_ticker_is_normalized(tmp_path):
    write_pick(tmp_path)
    assert load(tmp_path, ticker="  zzzq ").ticker == "ZZZQ"


def test_dotted_ticker(tmp_path):
    write_pick(tmp_path, ticker="BRK.B")
    assert load(tmp_path, ticker="brk.b").ticker == "BRK.B"


@pytest.mark.parametrize("bad", ["", "../ZZZQ", "..", ".A", "A/B", "A\\B", "C:X", "ABCDEFGHIJK",
                                 "A B", "ZZZQ/../../x"])
def test_bad_ticker_refused_before_any_file_access(tmp_path, bad):
    with pytest.raises(fp.FromPickError) as exc:
        fp.load_pick(bad, "cash_secured_put", today=TODAY, picks_dir=tmp_path / "nowhere")
    assert "ticker" in str(exc.value)


def test_missing_file(tmp_path):
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert str(exc.value) == "今天还没有 ZZZQ 的 /strategy-pick 结果，先运行 /strategy-pick ZZZQ"


def test_older_file_is_never_used(tmp_path):
    write_pick(tmp_path, run_date="2026-10-27", name="2026-10-27-ZZZQ.json")
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert "今天还没有 ZZZQ" in str(exc.value)
    assert "2026-10-27" not in str(exc.value)


def test_stale_run_date_inside_todays_file(tmp_path):
    write_pick(tmp_path, run_date="2026-10-27")
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert "2026-10-27" in str(exc.value) and "/strategy-pick ZZZQ" in str(exc.value)


def test_just_after_midnight_yesterdays_pick_is_stale(tmp_path):
    write_pick(tmp_path)  # written on the 28th
    with pytest.raises(fp.FromPickError):
        fp.load_pick("ZZZQ", "cash_secured_put", today=date(2026, 10, 29), picks_dir=tmp_path)


def test_other_ticker_inside_file(tmp_path):
    write_pick(tmp_path, ticker="ZZZQ", text=json.dumps(
        {"ticker": "YYYQ", "run_date": "2026-10-28", "picks": []}))
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert "YYYQ" in str(exc.value)


@pytest.mark.parametrize("text", ["", "{", "not json", "[]", "null", '"x"',
                                  '{"ticker": "ZZZQ", "run_date": "2026-10-28"}',
                                  '{"ticker": "ZZZQ", "run_date": "2026-10-28", "picks": {}}',
                                  '{"ticker": "ZZZQ", "run_date": 20261028, "picks": []}',
                                  '{"ticker": "ZZZQ", "run_date": "2026-10-28", "picks": [3]}',
                                  '{"ticker": "ZZZQ", "run_date": "2026-10-28", "picks": [{}]}'])
def test_corrupt_or_unexpected_schema(tmp_path, text):
    write_pick(tmp_path, text=text)
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert "格式不对" in str(exc.value) and "/strategy-pick ZZZQ" in str(exc.value)


def test_not_utf8(tmp_path):
    path = write_pick(tmp_path)
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert "格式不对" in str(exc.value)


@pytest.mark.parametrize("target", [
    [{"leg": "short_put", "low": "0.10", "high": 0.15}],
    [{"leg": "short_put", "low": 0.10}],
    [{"leg": "short_put", "low": True, "high": 0.15}],
    [{"leg": "short_put", "low": 0.20, "high": 0.15}],
    [{"leg": "short_put", "low": -0.1, "high": 0.15}],
    [{"leg": "short_put", "low": 0.1, "high": 1.5}],
    [{"leg": "short_put", "low": float("nan"), "high": 0.15}],
    [{"leg": 3, "low": 0.1, "high": 0.15}],
    [3],
    {"leg": "short_put"},
])
def test_bad_delta_target_entries(tmp_path, target):
    write_pick(tmp_path, picks=[{"strategy": "cash_secured_put", "rank": 1, "delta_target": target}])
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert "格式不对" in str(exc.value)


def test_bom_and_crlf_are_fine(tmp_path):
    path = write_pick(tmp_path)
    text = path.read_text(encoding="utf-8").replace("\n", "\r\n")
    path.write_text(text, encoding="utf-8-sig", newline="")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf") and b"\r\n" in path.read_bytes()
    assert load(tmp_path).delta_target[0].high == 0.15


def test_missing_strategy_lists_the_picks(tmp_path):
    write_pick(tmp_path)
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path, strategy="iron_condor")
    assert str(exc.value) == "今天的推荐里没有 铁鹰：推荐是 Bull Put 价差、现金担保 Put"


def test_missing_strategy_when_nothing_was_picked(tmp_path):
    write_pick(tmp_path, picks=[])
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path)
    assert str(exc.value) == "今天的推荐里没有 现金担保 Put：今天没有推荐任何策略"


@pytest.mark.parametrize("target", [[], None])
def test_pick_without_delta_target(tmp_path, target):
    pick = {"strategy": "collar", "rank": 1}
    if target is not None:
        pick["delta_target"] = target
    write_pick(tmp_path, picks=[pick])
    with pytest.raises(fp.FromPickError) as exc:
        load(tmp_path, strategy="collar")
    assert "没有能带过去的 Delta 目标" in str(exc.value)


def test_condor_with_mismatched_legs_is_refused(tmp_path):
    write_pick(tmp_path, picks=[{"strategy": "iron_condor", "rank": 1, "delta_target": [
        {"leg": "short_put", "low": 0.15, "high": 0.15},
        {"leg": "short_call", "low": 0.10, "high": 0.10}]}])
    with pytest.raises(fp.FromPickError) as exc:
        fp.bucket_for_open(load(tmp_path, strategy="iron_condor"), "iron_condor")
    assert "没有能带过去的 Delta 目标" in str(exc.value)


def test_first_ranked_duplicate_wins(tmp_path):
    write_pick(tmp_path, picks=[
        {"strategy": "cash_secured_put", "rank": 2, "delta_target": [
            {"leg": "short_put", "low": 0.2, "high": 0.2}]},
        {"strategy": "cash_secured_put", "rank": 1, "delta_target": FAR}])
    assert fp.delta_for_screen(load(tmp_path)) == 0.15


def test_file_written_by_strategy_pick_loads(monkeypatch, tmp_path):
    """End to end on the JSON /strategy-pick itself writes (stubbed market data, tmp folder)."""
    from strategy_pick_cli_stubs import RUN_DATE, base_result
    doc, _ = base_result(monkeypatch, tmp_path)
    picked = [p for p in doc["picks"] if p["delta_target"]]
    assert picked
    for p in picked:
        pick = fp.load_pick("zzzq", p["strategy"], today=RUN_DATE, picks_dir=tmp_path)
        assert pick.run_date == RUN_DATE.isoformat() and pick.rank == p["rank"]
        h = H.handoff(p["strategy"], p["delta_target"], "ZZZQ")
        if p["strategy"] == "cash_secured_put":
            assert fp.delta_for_screen(pick) == _printed_delta(h.command)
        elif p["strategy"] == "bull_put_spread":
            assert h.open_args[0] == f"open ZZZQ {fp.bucket_for_open(pick, p['strategy']):.2f}"


def test_wrong_strategy_for_bucket():
    pick = fp.PickTarget("ZZZQ", "bull_put_spread", "2026-10-28", 1,
                         (fp.Leg("short_put", 0.1, 0.15),))
    with pytest.raises(ValueError):
        fp.bucket_for_open(pick, "iron_condor")


# --- default folder and pinned twins ----------------------------------------------------------------
def test_ticker_pattern_matches_spread_sim():
    import spread_sim
    assert fp.TICKER_PATTERN == spread_sim.TICKER_PATTERN


def test_demo_subdir_matches_strategy_pick(monkeypatch):
    import option_settings
    import strategy_pick
    assert fp.DEMO_SUBDIR == strategy_pick.DEMO_OUTPUT_SUBDIR
    assert fp.PICKS_DIR == strategy_pick.OUTPUT_DIR
    monkeypatch.setattr(option_settings, "data_source", lambda: "demo")
    assert fp.default_picks_dir() == strategy_pick.OUTPUT_DIR / "demo"
    monkeypatch.setattr(option_settings, "data_source", lambda: "ibkr")
    assert fp.default_picks_dir() == strategy_pick.OUTPUT_DIR


def test_default_folder_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "default_picks_dir", lambda: tmp_path)
    write_pick(tmp_path)
    assert fp.load_pick("ZZZQ", "cash_secured_put", today=TODAY).rank == 2


# --- console lines -----------------------------------------------------------------------------------
def test_lines():
    far = fp.PickTarget("ZZZQ", "bull_put_spread", "2026-10-28", 1, (fp.Leg("short_put", 0.1, 0.15),))
    assert fp.open_line(far, 0.15) == "按 /strategy-pick 2026-10-28 的结果：Delta 目标 0.10–0.15 → 档位 0.15"
    one = fp.PickTarget("ZZZQ", "cash_secured_put", "2026-10-28", 1, (fp.Leg("short_put", 0.2, 0.2),))
    assert fp.screen_line(one, 0.2) == "按 /strategy-pick 2026-10-28 的结果：Delta 目标 0.20 → --delta 0.20"
    call = fp.PickTarget("ZZZQ", "covered_call", "2026-10-28", 1, (fp.Leg("short_call", 0.1, 0.15),))
    assert fp.screen_line(call, 0.15) == ("按 /strategy-pick 2026-10-28 的结果：Delta 目标 0.10–0.15 → "
                                          "--delta 0.15" + H.CALL_ALL_HOLDINGS)


# --- mapping consistency with handoff() --------------------------------------------------------------
LEGS = {"cash_secured_put": ("short_put",), "covered_call": ("short_call",),
        "bull_put_spread": ("short_put",), "iron_condor": ("short_put", "short_call"),
        "bull_call_spread": ("long_call",)}
RANGES = [(0.20, 0.20), (0.10, 0.15), (0.15, 0.15), (0.10, 0.10), (0.11, 0.14), (0.125, 0.125),
          (0.12, 0.13), (0.17, 0.18), (0.175, 0.175), (0.02, 0.03), (0.25, 0.30), (0.45, 0.48),
          (0.50, 0.60), (0.40, 0.60), (0.45, 0.45), (0.55, 0.55), (0.62, 0.70), (0.30, 0.35),
          (0.05, 0.40), (0.01, 0.01), (0.90, 0.95)]


def _pick(strategy, low, high):
    legs = tuple(fp.Leg(leg, low, high) for leg in LEGS[strategy])
    return fp.PickTarget("ZZZQ", strategy, "2026-10-28", 1, legs)


def _printed_delta(command: str) -> float:
    tokens = command.split()
    return float(tokens[tokens.index("--delta") + 1]) if "--delta" in tokens else psr.TARGET_DELTA


@pytest.mark.parametrize("strategy", sorted(LEGS))
@pytest.mark.parametrize("low,high", RANGES)
def test_from_pick_equals_what_handoff_prints(strategy, low, high):
    pick = _pick(strategy, low, high)
    h = H.handoff(strategy, [t._asdict() for t in pick.delta_target], "ZZZQ")
    if strategy in ("cash_secured_put", "covered_call"):
        assert fp.delta_for_screen(pick) == _printed_delta(h.command)
        assert psr.DELTA_ARG_MIN <= fp.delta_for_screen(pick) <= psr.DELTA_ARG_MAX
        assert not h.open_args
    else:
        bucket = fp.bucket_for_open(pick, strategy)
        assert h.open_args[0] == f"open ZZZQ {bucket:.2f}"
        allowed = (spread_core.BULLCALL_DELTA_BUCKETS if strategy == "bull_call_spread"
                   else spread_core.DELTA_BUCKETS)
        assert bucket in allowed


def test_tie_goes_to_the_safer_side():
    assert fp.bucket_for_open(_pick("bull_put_spread", 0.125, 0.125), "bull_put_spread") == 0.10
    assert fp.bucket_for_open(_pick("bull_call_spread", 0.45, 0.45), "bull_call_spread") == 0.50


# --- bear spreads (2026-10-30 bearish design, 3) -----------------------------------------------------
BEAR_PUT_T = [{"leg": "long_put", "low": 0.50, "high": 0.60}]
BEAR_CALL_T = [{"leg": "short_call", "low": 0.10, "high": 0.15}]
BEAR_PICKS = [{"strategy": "bear_put_spread", "rank": 1, "delta_target": BEAR_PUT_T},
              {"strategy": "bear_call_spread", "rank": 2, "delta_target": BEAR_CALL_T}]


def test_frompick_reads_bear_put_and_bear_call_leaf_names(tmp_path):
    write_pick(tmp_path, picks=BEAR_PICKS)
    put = load(tmp_path, strategy="bear_put_spread")
    call = load(tmp_path, strategy="bear_call_spread")
    assert [(t.leg, t.low, t.high) for t in put.delta_target] == [("long_put", 0.5, 0.6)]
    assert [(t.leg, t.low, t.high) for t in call.delta_target] == [("short_call", 0.1, 0.15)]


def test_open_from_pick_maps_bear_buckets(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "default_picks_dir", lambda: tmp_path)
    write_pick(tmp_path, picks=BEAR_PICKS)
    assert fp.open_from_pick("ZZZQ", "bear_put_spread", TODAY) == (
        0.60, "按 /strategy-pick 2026-10-28 的结果：Delta 目标 0.50–0.60 → 档位 0.60")
    assert fp.open_from_pick("ZZZQ", "bear_call_spread", TODAY) == (
        0.15, "按 /strategy-pick 2026-10-28 的结果：Delta 目标 0.10–0.15 → 档位 0.15")


@pytest.mark.parametrize("strategy", ["bear_put_spread", "bear_call_spread"])
def test_bear_open_from_pick_rejections_write_nothing(tmp_path, monkeypatch, strategy):
    monkeypatch.setattr(fp, "default_picks_dir", lambda: tmp_path)
    # missing
    with pytest.raises(fp.FromPickError, match="今天还没有 ZZZQ"):
        fp.open_from_pick("ZZZQ", strategy, TODAY)
    # stale (yesterday's run inside today's file name)
    write_pick(tmp_path, run_date="2026-10-27", picks=BEAR_PICKS)
    with pytest.raises(fp.FromPickError, match="不是今天"):
        fp.open_from_pick("ZZZQ", strategy, TODAY)
    # malformed
    write_pick(tmp_path, text="{not json")
    with pytest.raises(fp.FromPickError, match="格式不对"):
        fp.open_from_pick("ZZZQ", strategy, TODAY)
    # bear.held picks have no Bear Call
    held = [{"strategy": "reduce_position", "rank": 1, "delta_target": []},
            {"strategy": "bear_put_spread", "rank": 2, "delta_target": BEAR_PUT_T}]
    write_pick(tmp_path, picks=held)
    if strategy == "bear_call_spread":
        with pytest.raises(fp.FromPickError) as exc:
            fp.open_from_pick("ZZZQ", strategy, TODAY)
        assert str(exc.value) == "今天的推荐里没有 Bear Call 价差：推荐是 考虑减仓、Bear Put 价差"
    else:
        assert fp.open_from_pick("ZZZQ", strategy, TODAY)[0] == 0.60
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-28-ZZZQ.json"]
