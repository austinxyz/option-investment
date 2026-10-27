"""Tests for scripts/put_journal.py (tmp dirs and synthetic data only)."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import put_journal as pj  # noqa: E402
import spread_ledger as led  # noqa: E402

TODAY = date(2026, 10, 23)


def record(ticker="AAA", expiry="2026-10-23", status="open", pnl=None,
           exit_date=None, **kw):
    d = {"ticker": ticker, "expiry": expiry, "status": status,
         "short_strike": 150.0, "long_strike": 145.0, "width": 5.0,
         "credit": 0.5, "contracts": 2, "pnl": pnl, "exit_date": exit_date,
         "result": None if pnl is None else ("win" if pnl > 0 else "loss")}
    d.update(kw)
    return d


def write_ledger(folder: Path, records: list[dict]) -> None:
    ledger = led.empty_ledger()
    for r in records:
        ledger, _ = led.add_spread(ledger, r)
    led.save(folder / "spreads.json", ledger)


def prow(id, advice="hold", rule=0, **kw):
    d = {"id": id, "strategy": "bull_put_spread", "ticker": "AAA",
         "expiry": "2026-10-23", "short_strike": 150.0, "long_strike": 145.0,
         "credit": 0.5, "days_left": 5, "spot": 160.0, "cost_to_close": 0.3,
         "itm": False, "stop_flag": False, "captured": 0.4, "delta": 0.2,
         "data_ok": True, "earnings": "no", "error": None, "advice": advice,
         "rule": rule, "reason": "测试理由", "data_quality": "ok"}
    d.update(kw)
    return d


def write_snapshot(folder: Path, day: str, rows: list[dict]) -> None:
    (folder / "reviews").mkdir(exist_ok=True)
    doc = {"version": 1, "generated_on": day, "snapshot_kind": "收盘",
           "rules": {}, "positions": rows}
    (folder / "reviews" / f"{day}.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def settled_setup(folder: Path) -> None:
    write_ledger(folder, [record(status="expired", pnl=100.0, exit_date="2026-10-23")])
    write_snapshot(folder, "2026-10-13", [prow(1, "attention", 4)])
    write_snapshot(folder, "2026-10-20", [prow(1, "close", 1, captured=0.85, cost_to_close=0.07)])


def run(folder: Path, *extra) -> int:
    return pj.main(["--ledger-dir", str(folder), *extra], today=TODAY)


def test_report_end_to_end_for_one_settled_position(tmp_path):
    settled_setup(tmp_path)
    assert run(tmp_path) == 0
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "样本不足" in md
    assert "#1" in md
    assert "不一致" in md
    assert "建议准不准" in md and "阈值重算" in md
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert data["counts"]["settled"] == 1
    assert data["counts"]["comparable"] == 1
    assert data["counts"]["snapshots"] == 2
    assert data["counts"]["snapshot_dates"] == ["2026-10-13", "2026-10-20"]


def test_no_settled_positions_still_writes_report(tmp_path):
    write_ledger(tmp_path, [record()])
    write_snapshot(tmp_path, "2026-10-20", [prow(1, "hold")])
    assert run(tmp_path) == 0
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "还没有已结算的持仓" in md
    assert "进行中的持仓" in md
    assert "AAA" in md
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert data["counts"]["settled"] == 0
    assert data["open_positions"][0]["advice_rows"] == 1
    assert data["open_positions"][0]["last_advice"] == "hold"


def test_empty_ledger_dir_still_works(tmp_path):
    assert run(tmp_path) == 0
    assert (tmp_path / "journal.md").exists()


def test_settled_without_usable_snapshot_is_listed_not_comparable(tmp_path):
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23")])
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert data["counts"]["settled"] == 1
    assert data["counts"]["comparable"] == 0
    assert "无建议可比" in (tmp_path / "journal.md").read_text(encoding="utf-8")


def _strict(text: str):
    def boom(token):
        raise AssertionError(f"non-finite constant {token}")
    return json.loads(text, parse_constant=boom)


def test_report_json_has_no_nan(tmp_path):
    settled_setup(tmp_path)
    assert run(tmp_path) == 0
    _strict((tmp_path / "journal.json").read_text(encoding="utf-8"))
    # empty case too: every rate is None
    other = tmp_path / "empty"
    other.mkdir()
    assert run(other) == 0
    _strict((other / "journal.json").read_text(encoding="utf-8"))


def test_non_finite_value_raises():
    with pytest.raises(ValueError):
        pj.dump_json({"x": float("nan")})


def test_zero_denominators_render_dash(tmp_path):
    # settled, comparable, but never flagged and no losses: precision/recall undefined
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23")])
    write_snapshot(tmp_path, "2026-10-20", [prow(1, "hold")])
    assert run(tmp_path) == 0
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "精确率：—" in md
    assert "召回率：—" in md
    assert "nan" not in md.lower()
    assert "None" not in md


def test_damaged_snapshot_file_skipped_with_warning(tmp_path):
    settled_setup(tmp_path)
    (tmp_path / "reviews" / "2026-10-21.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "reviews" / "2026-10-22.json").write_text(
        json.dumps({"generated_on": "2026-10-22"}), encoding="utf-8")
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert len(data["warnings"]) == 2
    assert data["counts"]["snapshots"] == 2
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "2026-10-21.json" in md


def test_load_snapshots_never_raises_on_odd_shapes(tmp_path):
    d = tmp_path / "reviews"
    d.mkdir()
    (d / "a.json").write_text("[1,2]", encoding="utf-8")
    (d / "b.json").write_text(json.dumps({"generated_on": 5, "positions": []}), encoding="utf-8")
    (d / "c.json").write_text(json.dumps({"generated_on": "2026-10-20", "positions": {}}),
                              encoding="utf-8")
    snaps, warns = pj.load_snapshots(d)
    assert snaps == [] and len(warns) == 3
    assert pj.load_snapshots(tmp_path / "missing") == ([], [])


def test_load_snapshots_skips_wrong_version_but_accepts_missing(tmp_path):
    d = tmp_path / "reviews"
    d.mkdir()
    good = {"generated_on": "2026-10-20", "positions": []}
    (d / "a.json").write_text(json.dumps(good), encoding="utf-8")
    (d / "b.json").write_text(json.dumps({**good, "version": 1}), encoding="utf-8")
    (d / "c.json").write_text(json.dumps({**good, "version": 2}), encoding="utf-8")
    snaps, warns = pj.load_snapshots(d)
    assert len(snaps) == 2
    assert warns == ["快照 c.json 的版本 2 不是 1，已跳过"]


def test_note_command_appends_and_shows_in_timeline(tmp_path):
    settled_setup(tmp_path)
    assert run(tmp_path, "note", "1", "当时觉得还能再拿一点", "--kind", "mindset") == 0
    assert run(tmp_path, "note", "1", "第二条") == 0
    notes = json.loads((tmp_path / "journal-notes.json").read_text(encoding="utf-8"))
    assert notes["version"] == 1
    assert [n["kind"] for n in notes["notes"]["1"]] == ["mindset", "other"]
    assert notes["notes"]["1"][0]["date"] == "2026-10-23"
    assert run(tmp_path) == 0
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "当时觉得还能再拿一点" in md


def test_note_unknown_id_rejected_exit_1(tmp_path, capsys):
    settled_setup(tmp_path)
    assert run(tmp_path, "note", "99", "x") == 1
    assert capsys.readouterr().err
    assert not (tmp_path / "journal-notes.json").exists()


def test_note_empty_text_rejected(tmp_path):
    settled_setup(tmp_path)
    assert run(tmp_path, "note", "1", "   ") == 1
    assert not (tmp_path / "journal-notes.json").exists()
    with pytest.raises(pj.NotesError):
        pj.add_note(tmp_path / "n.json", 1, "", "other", TODAY, {1})


def test_add_note_rejects_bad_kind(tmp_path):
    with pytest.raises(pj.NotesError):
        pj.add_note(tmp_path / "n.json", 1, "x", "bogus", TODAY, {1})


def test_note_file_damaged_note_command_exit_1_and_report_warns(tmp_path, capsys):
    settled_setup(tmp_path)
    (tmp_path / "journal-notes.json").write_text("garbage", encoding="utf-8")
    assert run(tmp_path, "note", "1", "x") == 1
    assert "journal-notes.json" in capsys.readouterr().err
    assert (tmp_path / "journal-notes.json").read_text(encoding="utf-8") == "garbage"
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert any("journal-notes.json" in w for w in data["warnings"])


def test_load_notes_wrong_structure_raises(tmp_path):
    p = tmp_path / "n.json"
    p.write_text(json.dumps({"version": 1, "notes": []}), encoding="utf-8")
    with pytest.raises(pj.NotesError):
        pj.load_notes(p)
    assert pj.load_notes(tmp_path / "none.json") == {"version": 1, "notes": {}}


def test_journal_run_never_modifies_ledger_or_snapshots(tmp_path):
    settled_setup(tmp_path)
    files = [tmp_path / "spreads.json", *sorted((tmp_path / "reviews").glob("*.json"))]
    before = {f: f.read_bytes() for f in files}
    assert run(tmp_path) == 0
    assert {f: f.read_bytes() for f in files} == before
    assert {p.name for p in tmp_path.iterdir()} <= {
        "spreads.json", "spreads.json.bak", "reviews", "journal.md", "journal.json"}


def test_ledger_error_exit_1(tmp_path, capsys):
    (tmp_path / "spreads.json").write_text("{bad", encoding="utf-8")
    assert run(tmp_path) == 1
    assert "错误" in capsys.readouterr().err


def test_same_day_batch_streak_note_text_present(tmp_path):
    settled_setup(tmp_path)
    assert run(tmp_path) == 0
    assert "同日结算按 id 排序" in (tmp_path / "journal.md").read_text(encoding="utf-8")


def test_footer_and_strikes_for_cash_secured_put(tmp_path):
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23",
                                   strategy="cash_secured_put", long_strike=None,
                                   width=None)])
    assert run(tmp_path) == 0
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "150 Put" in md
    assert "非投资建议" in md
    assert "pnl 为 0" in md


def test_journal_handles_settled_covered_call(tmp_path):
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23",
                                   strategy="covered_call", long_strike=None, width=None,
                                   cost_basis=140.0, covered_shares=200,
                                   lot_ref={"source": "real", "lot_id": None, "ticker": "AAA"})])
    write_snapshot(tmp_path, "2026-10-20",
                   [prow(1, "hold", 0, strategy="covered_call", long_strike=None)])
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert data["counts"]["settled"] == 1
    assert [c["strategy"] for c in data["timelines"]] == ["covered_call"]
    assert "AAA" in (tmp_path / "journal.md").read_text(encoding="utf-8")


def _cc(**kw):
    base = dict(status="expired", pnl=100.0, exit_date="2026-10-23",
                strategy="covered_call", long_strike=None, width=None,
                cost_basis=140.0, covered_shares=200,
                lot_ref={"source": "real", "lot_id": None, "ticker": "AAA"}, result="called_away")
    base.update(kw)
    return record(**base)


def _attention(folder):
    return json.loads((folder / "journal.json").read_text(encoding="utf-8"))["attention"]


def test_covered_call_excluded_from_put_stats(tmp_path):
    # a called-away covered call (pnl > 0) flagged "attention" must not touch put precision
    settled_setup(tmp_path)
    assert run(tmp_path) == 0
    before = _attention(tmp_path)
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23"),
                            _cc(ticker="CCC", lot_ref={"source": "real", "lot_id": None,
                                                       "ticker": "CCC"})])
    write_snapshot(tmp_path, "2026-10-13", [prow(1, "attention", 4),
                                            prow(2, "attention", 2, strategy="covered_call")])
    write_snapshot(tmp_path, "2026-10-20", [prow(1, "close", 1, captured=0.85, cost_to_close=0.07)])
    assert run(tmp_path) == 0
    assert _attention(tmp_path) == before
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert data["streak"]["n_settled"] == 1
    assert data["counts"]["settled"] == 2


def test_covered_call_block_present_with_numbers(tmp_path):
    write_ledger(tmp_path, [_cc(), _cc(pnl=-40.0, result="expired_worthless", expiry="2026-10-16")])
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert data["covered_call"] == {"n": 2, "wins": 1, "win_rate": 0.5, "total_pnl": 60.0}
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "备兑看涨" in md
    assert "别下结论" in md


def test_covered_call_block_absent_without_covered_calls(tmp_path):
    settled_setup(tmp_path)
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert "covered_call" not in data
    assert "备兑看涨" not in (tmp_path / "journal.md").read_text(encoding="utf-8")


# ---- collar / protective put block (task 6) ---------------------------------------

def lot_rec(strategy="collar", ticker="ZZZQ", status="expired", pnl=100.0, result="protected",
            net_credit=0.6, contracts=2, **kw):
    d = {"strategy": strategy, "ticker": ticker, "expiry": "2026-10-23", "status": status,
         "put_strike": 90.0, "put_debit": 1.2, "call_strike": 110.0, "call_credit": 1.8,
         "net_credit": net_credit, "covered_shares": 200, "cost_basis": 80.0,
         "reference_price": 80.0, "lot_ref": {"source": "real", "ticker": ticker},
         "contracts": contracts, "pnl": pnl, "result": result, "stock_leg_pnl": 500.0,
         "exit_date": "2026-10-23" if status != "open" else None}
    d.update(kw)
    return d


def _report(records):
    withids = [{"id": i, **r} for i, r in enumerate(records, 1)]
    return pj.build_report(withids, [], {"version": 1, "notes": {}}, "2026-10-23")


def test_journal_new_block_counts_labels_and_net_cost():
    recs = [lot_rec(result="called_away", pnl=120.0),
            lot_rec(ticker="ZZZR", result="protected", pnl=-40.0, net_credit=-0.5),
            lot_rec("protective_put", ticker="ZZZP", result="expired_worthless", pnl=-240.0,
                    net_credit=-1.2, call_strike=None, call_credit=0.0),
            lot_rec(ticker="ZZZS", result="closed", status="closed_early", pnl=10.0),
            lot_rec(ticker="ZZZO", status="open", pnl=None, result=None)]
    block = _report(recs)["lot_put"]
    assert block["n"] == 4
    assert block["by_result"] == {"called_away": 1, "protected": 1, "expired_worthless": 1,
                                  "closed": 1}
    assert block["total_pnl"] == pytest.approx(120.0 - 40.0 - 240.0 + 10.0)
    # net cost is the negated net credit: credit 0.6*200 + (-0.5*200) + (-1.2*200) + 0.6*200
    assert block["total_net_cost"] == pytest.approx(-(0.6 * 200 - 0.5 * 200 - 1.2 * 200 + 0.6 * 200))
    md = pj.render_journal_md(_report(recs))
    assert "保护 / 领口" in md and "called_away" in md and "样本不足" in md


def test_journal_new_strategies_excluded_from_a_to_d():
    old = record(status="expired", pnl=100.0, exit_date="2026-10-23")
    report = _report([{**old, "id": 1}, {**lot_rec(), "id": 2}])
    assert report["counts"]["settled"] == 1
    assert report["close_advice"]["n_comparable"] == 0
    assert report["streak"]["n_settled"] == 1
    assert [t["id"] for t in report["timelines"]] == [1]
    assert "covered_call" not in report


def test_journal_new_block_small_sample_warning():
    md = pj.render_journal_md(_report([{**lot_rec(), "id": 1}]))
    section = md.split("## 保护 / 领口")[1].split("\n## ")[0]
    assert "样本不足" in section and "别下结论" in section


def test_journal_no_new_strategies_has_no_block():
    report = _report([{**record(status="expired", pnl=1.0, exit_date="2026-10-23"), "id": 1}])
    assert "lot_put" not in report
    assert "保护 / 领口" not in pj.render_journal_md(report)


def test_journal_cli_with_new_strategy_ledger(tmp_path):
    write_ledger(tmp_path, [lot_rec()])
    assert run(tmp_path) == 0
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "保护 / 领口" in md


# ---- iron condor block (condor plan task 6) -----------------------------------------
# ZZZQ condors (invented). Theoretical breakeven win rate = 1 - net credit / width:
# 1.50/5 -> 0.70, 1.00/5 -> 0.80, 2.00/5 -> 0.60.

def condor_rec(ticker="ZZZQ", status="expired", pnl=150.0, net_credit=1.5, width=5.0,
               breached_side="none", **kw):
    d = {"strategy": "iron_condor", "ticker": ticker, "expiry": "2026-10-23", "status": status,
         "contracts": 1, "put_short_strike": 90.0, "put_long_strike": 90.0 - width,
         "call_short_strike": 110.0, "call_long_strike": 110.0 + width, "width": width,
         "put_credit": net_credit / 2, "call_credit": net_credit / 2, "net_credit": net_credit,
         "breakeven_low": 90.0 - net_credit, "breakeven_high": 110.0 + net_credit,
         "pnl": pnl, "result": None if pnl is None else ("win" if pnl > 0 else "loss"),
         "exit_date": "2026-10-23" if status != "open" else None}
    if breached_side is not None:
        d["breached_side"] = breached_side
    d.update(kw)
    return d


def _condor_mix():
    return [condor_rec(),                                                   # +150, none, 0.70
            condor_rec(ticker="ZZZR", pnl=-50.0, breached_side="put"),      # -50, put, 0.70
            condor_rec(ticker="ZZZS", status="closed_early", pnl=90.0,      # +90, no side, 0.80
                       net_credit=1.0, breached_side=None),
            condor_rec(ticker="ZZZT", pnl=50.0, net_credit=2.0, breached_side="call"),  # 0.60
            condor_rec(ticker="ZZZU", status="open", pnl=None, breached_side=None)]


def test_journal_condor_block_counts_and_breached_side():
    block = _report(_condor_mix())["iron_condor"]
    assert block["n"] == 4 and block["wins"] == 3
    assert block["win_rate"] == pytest.approx(0.75)
    # (0.70 + 0.70 + 0.80 + 0.60) / 4 = 0.70
    assert block["theory_breakeven_avg"] == pytest.approx(0.70)
    assert block["breached"] == {"none": 1, "put": 1, "call": 1, "unrecorded": 1}
    assert block["total_pnl"] == pytest.approx(150.0 - 50.0 + 90.0 + 50.0)
    md = pj.render_journal_md(_report(_condor_mix()))
    section = md.split("## 铁鹰")[1].split("\n## ")[0]
    assert "胜率：75%" in section and "宽度）：70%" in section and "+$240" in section
    assert "Put 侧被突破 1 笔" in section and "未记录 1 笔" in section


def test_journal_condor_excluded_from_a_to_d_and_whatif():
    spread = {**record(status="expired", pnl=100.0, exit_date="2026-10-23"), "id": 1}
    snaps = [{"version": 1, "generated_on": "2026-10-20", "positions": [
        prow(1, "close", 3, captured=0.85, cost_to_close=0.07),
        prow(2, "attention", 2, strategy="iron_condor", cost_to_close=4.0)]}]
    notes = {"version": 1, "notes": {}}
    base = pj.build_report([spread], snaps, notes, "2026-10-23")
    withc = pj.build_report([spread, {**condor_rec(pnl=-350.0, breached_side="put"), "id": 2}],
                            snaps, notes, "2026-10-23")
    for key in ("close_advice", "attention", "hold", "streak", "sweep_close", "sweep_delta",
                "timelines", "counts"):
        assert withc[key] == base[key], key
    assert "iron_condor" not in base and withc["iron_condor"]["n"] == 1


def test_journal_report_uses_strategy_allowlist_not_exclusion():
    # an unknown future strategy that happens to carry credit must not reach A-D
    spread = {**record(status="expired", pnl=100.0, exit_date="2026-10-23"), "id": 1}
    stranger = {**record(ticker="ZZZQ", status="expired", pnl=-300.0, exit_date="2026-10-22",
                         strategy="calendar_spread"), "id": 2}
    snaps = [{"version": 1, "generated_on": "2026-10-20",
              "positions": [prow(2, "attention", 4, strategy="calendar_spread")]}]
    notes = {"version": 1, "notes": {}}
    base = pj.build_report([spread], [], notes, "2026-10-23")
    got = pj.build_report([spread, stranger], snaps, notes, "2026-10-23")
    for key in ("close_advice", "attention", "hold", "streak", "sweep_close", "sweep_delta"):
        assert got[key] == base[key], key
    assert pj.PUT_STATS_STRATEGIES == ("bull_put_spread", "cash_secured_put")


def test_journal_open_list_with_condor_does_not_crash(tmp_path):
    write_ledger(tmp_path, [condor_rec(status="open", pnl=None, breached_side=None)])
    write_snapshot(tmp_path, "2026-10-20", [prow(1, "hold", 5, strategy="iron_condor")])
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    (p,) = data["open_positions"]
    assert p["strikes"] == "85/90/110/115" and p["last_advice"] == "hold"
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "#1 ZZZQ 85/90/110/115" in md
    assert "## 铁鹰" not in md and data["counts"]["open"] == 1


def test_journal_condor_small_sample_warning():
    md = pj.render_journal_md(_report([condor_rec()]))
    section = md.split("## 铁鹰")[1].split("\n## ")[0]
    assert "样本不足（1 < 30），别下结论" in section


def test_journal_no_condor_has_no_block_and_unchanged_md():
    recs = [{**record(status="expired", pnl=100.0, exit_date="2026-10-23"), "id": 1}]
    report = pj.build_report(recs, [], {"version": 1, "notes": {}}, "2026-10-23")
    assert "iron_condor" not in report
    assert "铁鹰" not in pj.render_journal_md(report)


# ---- final review: condor counts in header/CLI, notes on condors ------------------

def test_journal_only_condors_settled_header_and_cli_point_to_condor_block(tmp_path, capsys):
    write_ledger(tmp_path, [condor_rec(), condor_rec(ticker="ZZZR", pnl=-50.0, breached_side="put")])
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    header = md.split("\n## ")[0]
    assert "已结算 0 笔" in header and "另有铁鹰已结算 2 笔，见铁鹰块" in header
    assert "另有铁鹰已结算 2 笔，见铁鹰块" in out
    assert pj.NO_SETTLED not in md and pj.NO_SETTLED not in out
    assert pj.NO_SETTLED_EXCEPT_CONDOR in md and pj.NO_SETTLED_EXCEPT_CONDOR in out
    assert "## 铁鹰" in md


def test_journal_without_condors_keeps_header_and_no_settled_text(tmp_path, capsys):
    write_ledger(tmp_path, [record()])
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "另有铁鹰" not in md and "另有铁鹰" not in out
    assert pj.NO_SETTLED in md and pj.NO_SETTLED in out


def test_note_on_condor_says_it_is_not_shown(tmp_path, capsys):
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23"),
                            condor_rec()])
    assert run(tmp_path, "note", "2", "试试") == 0
    assert pj.CONDOR_NOTE_HIDDEN in capsys.readouterr().out
    assert run(tmp_path, "note", "1", "普通") == 0
    assert pj.CONDOR_NOTE_HIDDEN not in capsys.readouterr().out
    notes = json.loads((tmp_path / pj.NOTES_FILE).read_text(encoding="utf-8"))
    assert notes["notes"]["2"][0]["text"] == "试试"


# ---- bull call debit spread block (bull call plan task 6) ---------------------------
# Pin first: the journal output for the six older strategies must not move.

import hashlib  # noqa: E402

PIN_JOURNAL_MD = "f86be366b742c7fe9a1847649c05353db4a99343b31fb7706bc01de12d1cc3af"
PIN_JOURNAL_JSON = "8c4c585892f49c52acaa9473ab65f4745f805b2038db818a2df66489b2948650"


def _old_strategy_records():
    return [record(status="expired", pnl=100.0, exit_date="2026-10-23"),
            record(ticker="ZZZA", strategy="cash_secured_put", long_strike=None, width=None,
                   status="closed_early", pnl=-40.0, exit_date="2026-10-21"),
            _cc(ticker="ZZZC", lot_ref={"source": "real", "lot_id": None, "ticker": "ZZZC"}),
            lot_rec(ticker="ZZZL"),
            lot_rec("protective_put", ticker="ZZZP", result="expired_worthless", pnl=-240.0,
                    net_credit=-1.2, call_strike=None, call_credit=0.0),
            condor_rec(ticker="ZZZQ"),
            record(ticker="ZZZO", expiry="2026-11-20")]


def _old_strategy_snaps():
    return [{"version": 1, "generated_on": "2026-10-13",
             "positions": [prow(1, "attention", 4), prow(2, "hold", 5, ticker="ZZZA")]},
            {"version": 1, "generated_on": "2026-10-20",
             "positions": [prow(1, "close", 3, captured=0.85, cost_to_close=0.07),
                           prow(7, "hold", 5, ticker="ZZZO")]}]


def _old_strategy_report(extra=()):
    recs = [{"id": i, **r} for i, r in enumerate([*_old_strategy_records(), *extra], 1)]
    notes = {"version": 1, "notes": {"1": [{"date": "2026-10-20", "kind": "exit", "text": "止盈"}]}}
    return pj.build_report(recs, _old_strategy_snaps(), notes, "2026-10-23", ["示例提示"])


def test_journal_md_unchanged_for_old_strategies():
    report = _old_strategy_report()
    md = pj.render_journal_md(report).encode("utf-8")
    js = pj.dump_json(report).encode("utf-8")
    assert hashlib.sha256(md).hexdigest() == PIN_JOURNAL_MD
    assert hashlib.sha256(js).hexdigest() == PIN_JOURNAL_JSON


# ZZZQ bull calls (invented, design section 10): long 100 / short 105, width 5, debit 1.90,
# 1 contract, max profit $310. Required win rate 1.90 / 5 = 0.38 for each.
#   C=103 -> +$110, captured 110/310 = 0.3548;  C=120 -> +$310, 1.0;  C=99 -> -$190, -0.6129
# Settled: 3, wins 2 -> 66.7%; winners' captured (0.3548 + 1.0) / 2 = 0.6774 -> 67.7%;
# total 110 + 310 - 190 = +$230.

def bc_rec(ticker="ZZZQ", status="expired", pnl=110.0, captured=0.3548, debit=1.9, width=5.0,
           **kw):
    d = {"strategy": "bull_call_spread", "ticker": ticker, "expiry": "2026-10-23",
         "status": status, "contracts": 1, "long_call_strike": 100.0,
         "short_call_strike": 100.0 + width, "width": width, "long_call_mid": 3.2,
         "short_call_mid": 1.3, "debit": debit, "breakeven": 100.0 + debit,
         "pnl": pnl, "result": None if pnl is None else ("win" if pnl > 0 else "loss"),
         "fraction_of_max_captured": captured,
         "exit_date": "2026-10-23" if status != "open" else None}
    d.update(kw)
    return d


def _bc_mix():
    return [bc_rec(),
            bc_rec(ticker="ZZZR", pnl=310.0, captured=1.0),
            bc_rec(ticker="ZZZS", status="closed_early", pnl=-190.0, captured=-0.6129),
            bc_rec(ticker="ZZZU", status="open", pnl=None, captured=None)]


def test_journal_bullcall_block_hand_numbers():
    block = _report(_bc_mix())["bull_call_spread"]
    assert block["n"] == 3 and block["wins"] == 2
    assert block["win_rate"] == pytest.approx(2 / 3)
    assert block["required_avg"] == pytest.approx(0.38)
    assert block["captured_avg_winners"] == pytest.approx(0.6774)
    assert block["total_pnl"] == pytest.approx(230.0)
    md = pj.render_journal_md(_report(_bc_mix()))
    section = md.split("## Call 借方价差")[1].split("\n## ")[0]
    assert "胜率：67%" in section and "66.7%" not in section and "+$230" in section
    assert "宽度）：38%" in section and "最大收益的 67.7%" in section
    assert "+67.7%" not in section and "突破" not in section


def test_journal_bullcall_excluded_from_a_to_d_and_whatif():
    spread = {**record(status="expired", pnl=100.0, exit_date="2026-10-23"), "id": 1}
    snaps = [{"version": 1, "generated_on": "2026-10-20", "positions": [
        prow(1, "close", 3, captured=0.85, cost_to_close=0.07),
        prow(2, "attention", 4, strategy="bull_call_spread", cost_to_close=None)]}]
    notes = {"version": 1, "notes": {}}
    base = pj.build_report([spread], snaps, notes, "2026-10-23")
    withb = pj.build_report([spread, {**bc_rec(pnl=-190.0, captured=-0.6129), "id": 2}],
                            snaps, notes, "2026-10-23")
    for key in ("close_advice", "attention", "hold", "streak", "sweep_close", "sweep_delta",
                "timelines", "counts"):
        assert withb[key] == base[key], key
    assert "bull_call_spread" not in base and withb["bull_call_spread"]["n"] == 1


def test_journal_header_counts_exclude_bullcall_with_own_line(tmp_path, capsys):
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23"),
                            condor_rec(ticker="ZZZC"), bc_rec(),
                            bc_rec(ticker="ZZZR", pnl=310.0, captured=1.0)])
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    header = (tmp_path / "journal.md").read_text(encoding="utf-8").split("\n## ")[0]
    assert "- 已结算 1 笔" in header
    assert "- 另有铁鹰已结算 1 笔，见铁鹰块" in header
    assert "- 另有 Call 借方价差已结算 2 笔，见其块" in header
    assert header.index("另有铁鹰") < header.index("另有 Call 借方价差")
    assert "另有铁鹰已结算 1 笔，见铁鹰块" in out and "另有 Call 借方价差已结算 2 笔，见其块" in out


def test_journal_no_settled_text_when_only_bullcall_settled(tmp_path, capsys):
    write_ledger(tmp_path, [bc_rec()])
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert pj.NO_SETTLED not in md and pj.NO_SETTLED not in out
    assert pj.NO_SETTLED_EXCEPT_CONDOR not in md
    assert pj.NO_SETTLED_EXCEPT_BULLCALL in md and pj.NO_SETTLED_EXCEPT_BULLCALL in out
    assert "## Call 借方价差" in md
    # both own-block strategies settled: one sentence names both blocks
    folder = tmp_path / "both"
    folder.mkdir()
    write_ledger(folder, [bc_rec(), condor_rec(ticker="ZZZC")])
    assert run(folder) == 0
    md = (folder / "journal.md").read_text(encoding="utf-8")
    assert pj.NO_SETTLED_EXCEPT_OWN_BLOCKS in md and pj.NO_SETTLED_EXCEPT_CONDOR not in md


def test_journal_note_on_bullcall_prints_hidden_notice(tmp_path, capsys):
    write_ledger(tmp_path, [record(status="expired", pnl=100.0, exit_date="2026-10-23"),
                            bc_rec()])
    assert run(tmp_path, "note", "2", "看涨理由") == 0
    out = capsys.readouterr().out
    assert pj.BULLCALL_NOTE_HIDDEN in out and pj.CONDOR_NOTE_HIDDEN not in out
    assert run(tmp_path, "note", "1", "普通") == 0
    assert pj.BULLCALL_NOTE_HIDDEN not in capsys.readouterr().out
    notes = json.loads((tmp_path / pj.NOTES_FILE).read_text(encoding="utf-8"))
    assert notes["notes"]["2"][0]["text"] == "看涨理由"


def test_journal_open_list_with_bullcall_does_not_crash(tmp_path):
    write_ledger(tmp_path, [bc_rec(status="open", pnl=None, captured=None)])
    write_snapshot(tmp_path, "2026-10-20", [prow(1, "hold", 5, strategy="bull_call_spread")])
    assert run(tmp_path) == 0
    data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    (p,) = data["open_positions"]
    assert p["strikes"] == "100/105 Call" and p["last_advice"] == "hold"
    md = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "#1 ZZZQ 100/105 Call" in md
    assert "## Call 借方价差" not in md and "bull_call_spread" not in data


def test_journal_bullcall_small_sample_warning():
    md = pj.render_journal_md(_report([bc_rec()]))
    section = md.split("## Call 借方价差")[1].split("\n## ")[0]
    assert "样本不足（1 < 30），别下结论" in section


def test_journal_old_output_with_open_bullcall_adds_no_block():
    # an open bull call only adds its own line to the open list
    report = _old_strategy_report([bc_rec(ticker="ZZZK", status="open", pnl=None,
                                          captured=None)])
    assert "bull_call_spread" not in report
    md = pj.render_journal_md(report)
    assert "另有 Call 借方价差" not in md and "## Call 借方价差" not in md
    assert "ZZZK 100/105 Call" in md
