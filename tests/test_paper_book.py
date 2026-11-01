"""Tests for scripts/paper_book.py (+ paper_book_render.py): the all-strategy paper-book overview.

No network: live marks are stubbed by monkeypatching spread_sim._mark and spread_data.ensure_source.
Tickers are invented (ZZZ*). Hand numbers are in the comments next to each fixture.
"""
from datetime import date
from pathlib import Path
import json
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import option_labels
import paper_book
import spread_data
import spread_ledger as L
import spread_sim

TODAY = date(2026, 10, 20)


def _rec(id_, ticker, strategy, status, snap, expiry, exit_date=None, pnl=None, contracts=1,
         **fields):
    rec = {"id": id_, "ticker": ticker, "contracts": contracts, "snapshot_date": snap,
           "expiry": expiry, "status": status, "exit_date": exit_date, "pnl": pnl,
           "result": None if pnl is None else ("win" if pnl > 0 else "loss"), **fields}
    if strategy != L.STRATEGY_BULL_PUT:
        rec["strategy"] = strategy
    return rec


def mixed_ledger() -> list[dict]:
    """Seven records over six strategies (protective put is in the all-seven guard below).

    #1 bull put 100/95 credit 1.00 x1, expired +100     capital (5-1)*100      = 400
    #2 bull put 50/45  credit 0.50 x2, closed  -150     capital (5-0.5)*200    = 900
    #3 CSP 40          credit 1.00 x1, expired +100     capital 40*100         = 4000
    #4 bull call 60/65 debit 2.00 x1, expired  -200     capital 2*100          = 200
    #5 condor 5 wide   net 1.50 x1, OPEN (live +60)     capital (5-1.5)*100    = 350
    #6 covered call    credit 1.20 x1, OPEN (live -30)  capital 0
    #7 collar          net 0.10 x1, expired +40         capital 0 (no win rate)
    realized = 100-150+100-200+40 = -110; live unrealized = 60-30 = +30; total = -80
    win-rate sample #1-#4: 2 wins / 4 = 50%; avg win 100; avg loss (150+200)/2 = 175
    settled capital 400+900+4000+200+0 = 5500 -> realized return -110/5500 = -2.0%
    live counted capital 5500+350+0 = 5850 -> total return -80/5850 = -1.367...% -> -1.4%
    peak: 10-02 -> 400+900 = 1300; 10-09 -> 4000+200+0 = 4200; 10-16 -> 350 => 4200 on 10-09
    """
    return [
        _rec(1, "ZZZQ", L.STRATEGY_BULL_PUT, "expired", "2026-10-02", "2026-10-09", "2026-10-09", 100.0,
             short_strike=100.0, long_strike=95.0, width=5.0, credit=1.00),
        _rec(2, "ZZZR", L.STRATEGY_BULL_PUT, "closed_early", "2026-10-02", "2026-10-09", "2026-10-06",
             -150.0, contracts=2, short_strike=50.0, long_strike=45.0, width=5.0, credit=0.50),
        _rec(3, "ZZZS", L.STRATEGY_CSP, "expired", "2026-10-09", "2026-10-16", "2026-10-16", 100.0,
             short_strike=40.0, long_strike=None, width=None, credit=1.00),
        _rec(4, "ZZZT", L.STRATEGY_BULL_CALL, "expired", "2026-10-09", "2026-10-16", "2026-10-16", -200.0,
             long_call_strike=60.0, short_call_strike=65.0, width=5.0, debit=2.00),
        _rec(5, "ZZZU", L.STRATEGY_IRON_CONDOR, "open", "2026-10-16", "2026-10-23",
             put_long_strike=85.0, put_short_strike=90.0, call_short_strike=110.0,
             call_long_strike=115.0, width=5.0, net_credit=1.50),
        _rec(6, "ZZZV", L.STRATEGY_COVERED_CALL, "open", "2026-10-16", "2026-10-23",
             short_strike=120.0, credit=1.20, cost_basis=87.65,
             lot_ref={"source": "paper", "lot_id": 3, "ticker": "ZZZV"}),
        _rec(7, "ZZZW", L.STRATEGY_COLLAR, "expired", "2026-10-09", "2026-10-16", "2026-10-16", 40.0,
             put_strike=70.0, call_strike=95.0, net_credit=0.10, put_debit=1.00, call_credit=1.10,
             cost_basis=87.65, covered_shares=371, reference_price=80.0,
             lot_ref={"source": "paper", "lot_id": 4, "ticker": "ZZZW"}),
    ]


LIVE_MARKS = {5: {"pnl": 60.0}, 6: {"pnl": -30.0}}


def write_ledger(tmp_path, records) -> Path:
    path = tmp_path / "spreads.json"
    path.write_text(json.dumps({"version": 1, "spreads": records}, ensure_ascii=False),
                    encoding="utf-8")
    return path


@pytest.fixture
def stub_live(monkeypatch):
    """Stub the mark function and the source probe; returns a dict to steer them."""
    state = {"marks": dict(LIVE_MARKS), "probe_calls": 0, "mark_calls": [], "probe_fail": False}

    def probe():
        state["probe_calls"] += 1
        if state["probe_fail"]:
            raise spread_data.SourceUnavailable("IBKR 网关不可用（测试）")

    def mark(s):
        state["mark_calls"].append(s["id"])
        m = state["marks"].get(s["id"])
        if isinstance(m, Exception):
            raise m
        return m if m is not None else {"error": "期权链里找不到这条腿的有效报价"}

    monkeypatch.setattr(spread_data, "ensure_source", probe)
    monkeypatch.setattr(spread_sim, "_mark", mark)
    return state


def run(tmp_path, *argv, today=TODAY):
    return paper_book.main(["--ledger-dir", str(tmp_path), *argv], today=today)


def book(tmp_path) -> str:
    return (tmp_path / "book.md").read_text(encoding="utf-8")


# ---- pure maths ------------------------------------------------------------------------

def test_offline_totals_match_hand_numbers():
    rows = paper_book.trade_rows(mixed_ledger(), None, TODAY)
    t = paper_book.summarize(rows, live=False)
    assert t["realized"] == pytest.approx(-110.0)
    assert t["unrealized"] is None
    assert t["total"] == pytest.approx(-110.0)          # offline: total excludes unrealized
    assert (t["n_settled"], t["n_open"]) == (5, 2)
    assert t["win_rate"] == pytest.approx(0.5) and t["n_win_sample"] == 4
    assert t["avg_win"] == pytest.approx(100.0) and t["avg_loss"] == pytest.approx(175.0)
    assert t["settled_capital"] == pytest.approx(5500.0)
    assert t["realized_return"] == pytest.approx(-110 / 5500)
    assert t["total_return"] == pytest.approx(-110 / 5500)
    assert t["current_capital"] == pytest.approx(350.0)


def test_live_totals_match_hand_numbers():
    rows = paper_book.trade_rows(mixed_ledger(), LIVE_MARKS, TODAY)
    t = paper_book.summarize(rows, live=True)
    assert t["unrealized"] == pytest.approx(30.0)
    assert t["total"] == pytest.approx(-80.0)
    assert t["counted_capital"] == pytest.approx(5850.0)
    assert t["total_return"] == pytest.approx(-80 / 5850)
    assert t["n_missing"] == 0 and t["n_pending"] == 0


@pytest.mark.parametrize("live", [False, True])
def test_strategy_rows_and_detail_rows_sum_to_the_total(live):
    rows = paper_book.trade_rows(mixed_ledger(), LIVE_MARKS if live else None, TODAY)
    total = paper_book.summarize(rows, live=live)
    groups = paper_book.by_strategy(rows, live=live)
    for key in ("realized", "total", "settled_capital", "counted_capital", "current_capital"):
        assert sum(g[key] for _, g in groups) == pytest.approx(total[key])
    for key in ("n_settled", "n_open", "n_missing", "n_pending"):
        assert sum(g[key] for _, g in groups) == total[key]
    assert sum(r["pnl"] for r in rows if r["settled"]) == pytest.approx(total["realized"])
    detail = sum(r["unrealized"] for r in rows if r["unrealized"] is not None)
    assert detail == pytest.approx(total["unrealized"] or 0.0)


def test_strategy_rows_follow_the_fixed_order_and_skip_absent_strategies():
    rows = paper_book.trade_rows(mixed_ledger(), None, TODAY)
    names = [name for name, _ in paper_book.by_strategy(rows, live=False)]
    assert names == [L.STRATEGY_BULL_PUT, L.STRATEGY_CSP, L.STRATEGY_COVERED_CALL,
                     L.STRATEGY_COLLAR, L.STRATEGY_IRON_CONDOR, L.STRATEGY_BULL_CALL]


def test_detail_rows_grouped_by_strategy_then_open_date_then_id():
    recs = mixed_ledger() + [
        _rec(8, "ZZZX", L.STRATEGY_BULL_PUT, "open", "2026-10-01", "2026-10-30",
             short_strike=10.0, long_strike=9.0, width=1.0, credit=0.20)]
    ids = [r["id"] for r in paper_book.trade_rows(recs, None, TODAY)]
    assert ids == [8, 1, 2, 3, 6, 7, 5, 4]


def eight_strategies() -> list[dict]:
    """One open record per strategy. Expected (signed cash per share, capital):
    bull put +1.00 / 400; CSP +1.00 / 4000; covered call +1.20 / 0; collar +0.10 / 0;
    protective put -1.50 / 150; condor +1.50 / 350; bull call -2.00 / 200;
    risk reversal +0.50 / (90-0.50)*100 = 8950."""
    base = mixed_ledger()
    by_id = {r["id"]: r for r in base}
    out = []
    for i, src in enumerate((by_id[1], by_id[3], by_id[6], by_id[7]), start=1):
        out.append({**src, "id": i, "status": "open", "exit_date": None, "pnl": None,
                    "result": None, "expiry": "2026-10-23"})
    out.append(_rec(5, "ZZZP", L.STRATEGY_PROTECTIVE_PUT, "open", "2026-10-16", "2026-10-23",
                    put_strike=70.0, net_credit=-1.50, put_debit=1.50, call_credit=0.0,
                    cost_basis=87.65, covered_shares=100, reference_price=80.0,
                    lot_ref={"source": "paper", "lot_id": 5, "ticker": "ZZZP"}))
    out.append({**by_id[5], "id": 6})
    out.append({**by_id[4], "id": 7, "status": "open", "exit_date": None, "pnl": None,
                "result": None, "expiry": "2026-10-23"})
    out.append(_rec(8, "ZZZQ", L.STRATEGY_RR, "open", "2026-10-16", "2026-10-23",
                    short_put_strike=90.0, long_call_strike=110.0, net_credit=0.50))
    return out


def test_all_eight_strategies_guard():
    recs = eight_strategies()
    # the three bearish strategies after these eight: test_paper_book_bearish.py
    assert {L.strategy_of(r) for r in recs} == set(paper_book.STRATEGY_ORDER[:8])
    rows = {r["id"]: r for r in paper_book.trade_rows(recs, None, TODAY)}
    expected = {1: (1.00, 400.0), 2: (1.00, 4000.0), 3: (1.20, 0.0), 4: (0.10, 0.0),
                5: (-1.50, 150.0), 6: (1.50, 350.0), 7: (-2.00, 200.0), 8: (0.50, 8950.0)}
    for id_, (cash, capital) in expected.items():
        assert rows[id_]["cash"] == pytest.approx(cash), id_
        assert rows[id_]["capital"] == pytest.approx(capital), id_
    text = paper_book.render(recs, None, TODAY)
    assert "收入 $1.00/股（$100.00/张）" in text and "支出 $2.00/股（$200.00/张）" in text
    assert "支出 $1.50/股（$150.00/张）" in text
    for label in ("Bull Put 价差", "现金担保 Put", "备兑 Call", "Collar", "保护性 Put", "铁鹰",
                  "Bull Call 价差", "风险反转"):
        assert f"| {label} |" in text


def test_all_eight_settled_strategy_total_row_equals_overview():
    recs = []
    for r in eight_strategies():
        recs.append({**r, "status": "expired", "exit_date": "2026-10-23", "pnl": 10.0 * r["id"]})
    rows = paper_book.trade_rows(recs, None, date(2026, 10, 26))
    total = paper_book.summarize(rows, live=False)
    groups = paper_book.by_strategy(rows, live=False)
    assert len(groups) == 8
    assert sum(g["realized"] for _, g in groups) == pytest.approx(total["realized"]) == 360.0
    text = paper_book.render(recs, None, date(2026, 10, 26))
    assert "| **合计** | 8 | 0 |" in text


def test_unknown_strategy_is_a_clean_error():
    recs = mixed_ledger() + [_rec(9, "ZZZY", "calendar_spread", "open", "2026-10-16", "2026-10-23")]
    with pytest.raises(L.LedgerError, match="calendar_spread"):
        paper_book.trade_rows(recs, None, TODAY)


def test_malformed_record_is_a_clean_error(tmp_path, capsys):
    recs = mixed_ledger()
    recs[0] = {**recs[0], "pnl": None}                    # settled but no pnl
    write_ledger(tmp_path, recs)
    assert run(tmp_path) == 1
    assert not (tmp_path / "book.md").exists()
    assert "#1 记录缺字段或格式不对" in capsys.readouterr().err


def test_percent_never_shows_negative_zero():
    assert paper_book.pct1(-0.00001) == "0.0%"
    assert paper_book.pct1(0.0123) == "+1.2%"
    assert paper_book.pct1(-0.02) == "-2.0%"
    assert paper_book.pct1(None) == "—"


# ---- rendering ---------------------------------------------------------------------------

def test_offline_render_labels_unrealized_as_missing_and_total_excludes_it():
    text = paper_book.render(mixed_ledger(), None, TODAY)
    assert "—（需 status 取行情）" in text
    assert "不含浮动盈亏" in text
    assert "未取行情" in text
    assert "| 已实现盈亏（已结算 5 笔） | -$110 |" in text
    assert "-2.0%" in text and "已实现盈亏 ÷ 已结算各笔占用资金之和" in text
    assert "历史最大同时占用资金 | $4,200（2026-10-09 起）" in text
    assert "模拟 / 研究用途，非投资建议" in text and "不含佣金" in text
    assert "生成日期 2026-10-20" in text


def test_live_render_shows_three_separate_numbers():
    text = paper_book.render(mixed_ledger(), LIVE_MARKS, TODAY)
    assert "| 浮动盈亏（未平仓 2 笔） | +$30 |" in text
    assert "| 合计 | -$80 |" in text
    assert "| 已实现盈亏（已结算 5 笔） | -$110 |" in text
    assert "已取实时行情" in text
    assert "需 status" not in text
    assert "-1.4%" in text
    assert "| **合计** | 5 | 2 | 50% | -$110 | +$30 | -$80 |" in text


def test_detail_row_contents():
    text = paper_book.render(mixed_ledger(), LIVE_MARKS, TODAY)
    assert ("| 2 | ZZZR | Bull Put 价差 | 50/45 | 2026-10-02 | 2026-10-09 | 2 "
            "| 收入 $0.50/股（$50.00/张） | $900 | 提前平仓 | 2026-10-06 | 亏损 | -$150 | -16.7% |") in text
    assert ("| 5 | ZZZU | 铁鹰 | 85/90/110/115 | 2026-10-16 | 2026-10-23 | 1 "
            "| 收入 $1.50/股（$150.00/张） | $350 | 未平仓 | — | — | +$60 | +17.1% |") in text


def test_privacy_no_share_counts_or_cost_basis():
    text = paper_book.render(mixed_ledger(), LIVE_MARKS, TODAY)
    assert "股数" not in text
    assert "87.65" not in text and "8,765" not in text and "32,518" not in text
    assert "371" not in text


def test_render_is_deterministic():
    assert paper_book.render(mixed_ledger(), LIVE_MARKS, TODAY) == \
        paper_book.render(mixed_ledger(), LIVE_MARKS, TODAY)


@pytest.mark.data_source("yahoo")
def test_yahoo_banner_and_mix_line():
    recs = mixed_ledger()
    recs[0] = {**recs[0], "data_source": "Yahoo", "data_quality": "low"}
    text = paper_book.render(recs, None, TODAY)
    assert option_labels.BANNERS["yahoo"] in text
    assert text.index(option_labels.BANNERS["yahoo"]) < text.index("## 一、总览")
    assert "其中 1 笔按 Yahoo 数据开仓" in text
    assert "ZZZQ·Y" in text
    assert "数据来源 Yahoo" in text


def test_ibkr_has_no_banner():
    text = paper_book.render(mixed_ledger(), None, TODAY)
    assert "⚠️ 数据来源" not in text and "数据来源 IBKR" in text


# ---- CLI ---------------------------------------------------------------------------------

def test_cli_offline_writes_book_only_and_never_touches_the_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPTION_TESTS_NO_NETWORK", "1")
    path = write_ledger(tmp_path, mixed_ledger())
    before = path.read_bytes()

    def boom(*a, **k):
        raise AssertionError("offline mode must not fetch")
    monkeypatch.setattr(spread_data, "ensure_source", boom)
    monkeypatch.setattr(spread_sim, "_mark", boom)
    assert run(tmp_path) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.md", "spreads.json"]
    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert "已实现 -$110" in out and "浮动 —（需 status 取行情）" in out and "合计 -$110" in out
    assert "book.md" in out
    assert run(tmp_path, "report") == 0


def test_cli_offline_same_ledger_same_bytes(tmp_path):
    write_ledger(tmp_path, mixed_ledger())
    assert run(tmp_path) == 0
    first = (tmp_path / "book.md").read_bytes()
    assert run(tmp_path) == 0
    assert (tmp_path / "book.md").read_bytes() == first


@pytest.mark.parametrize("argv", [("status",), ("--live",), ("report", "--live")])
def test_cli_live_uses_stubbed_marks(tmp_path, stub_live, monkeypatch, capsys, argv):
    monkeypatch.setenv("OPTION_TESTS_NO_NETWORK", "1")
    path = write_ledger(tmp_path, mixed_ledger())
    before = path.read_bytes()
    assert run(tmp_path, *argv) == 0
    assert stub_live["probe_calls"] == 1 and sorted(stub_live["mark_calls"]) == [5, 6]
    assert "| 合计 | -$80 |" in book(tmp_path)
    assert path.read_bytes() == before                       # read-only: nothing settled
    out = capsys.readouterr().out
    assert "浮动 +$30" in out and "合计 -$80" in out


def test_cli_live_one_failing_mark_does_not_abort(tmp_path, stub_live):
    stub_live["marks"][5] = spread_data.DataError("ZZZU 期权链取不到")
    write_ledger(tmp_path, mixed_ledger())
    assert run(tmp_path, "status") == 0
    text = book(tmp_path)
    assert "行情缺失" in text
    assert "| 浮动盈亏（未平仓 2 笔） | -$30（另有 1 笔行情缺失，未计入） |" in text
    assert "| 合计 | -$140 |" in text


def test_cli_live_error_mark_counts_as_missing(tmp_path, stub_live):
    stub_live["marks"][6] = {"error": "期权链里找不到这条腿的有效报价"}
    write_ledger(tmp_path, mixed_ledger())
    assert run(tmp_path, "status") == 0
    assert "| 浮动盈亏（未平仓 2 笔） | +$60（另有 1 笔行情缺失，未计入） |" in book(tmp_path)


def test_cli_live_past_expiry_open_record_is_pending_and_not_marked(tmp_path, stub_live):
    write_ledger(tmp_path, mixed_ledger())
    assert run(tmp_path, "status", today=date(2026, 10, 24)) == 0
    assert stub_live["mark_calls"] == []
    assert stub_live["probe_calls"] == 0                     # nothing left to price
    text = book(tmp_path)
    assert "待结算" in text and "2 笔已过到期日待结算" in text


@pytest.mark.parametrize("where", ["probe", "mark"])
def test_cli_live_unreachable_source_aborts_and_writes_nothing(tmp_path, stub_live, capsys, where):
    if where == "probe":
        stub_live["probe_fail"] = True
    else:
        stub_live["marks"][6] = spread_data.SourceUnavailable("连接中途断开")
    write_ledger(tmp_path, mixed_ledger())
    (tmp_path / "book.md").write_text("old", encoding="utf-8")
    assert run(tmp_path, "status") == 1
    assert book(tmp_path) == "old"
    assert "错误" in capsys.readouterr().err


@pytest.mark.parametrize("content", [None, '{"version": 1, "spreads": []}'])
def test_cli_empty_ledger_friendly_line(tmp_path, capsys, content):
    if content is not None:
        (tmp_path / "spreads.json").write_text(content, encoding="utf-8")
    assert run(tmp_path) == 0
    assert not (tmp_path / "book.md").exists()
    assert "还没有记录" in capsys.readouterr().out


def test_cli_corrupt_ledger_clean_error(tmp_path, capsys):
    (tmp_path / "spreads.json").write_text("{not json", encoding="utf-8")
    assert run(tmp_path) == 1
    assert not (tmp_path / "book.md").exists()
    assert "不是有效的 JSON" in capsys.readouterr().err


def test_cli_only_open_trades(tmp_path, stub_live):
    write_ledger(tmp_path, [r for r in mixed_ledger() if r["status"] == "open"])
    assert run(tmp_path) == 0
    text = book(tmp_path)
    assert "| 已实现盈亏（已结算 0 笔） | $0 |" in text and "| 胜率 | — |" in text
    assert run(tmp_path, "status") == 0
    assert "| 合计 | +$30 |" in book(tmp_path)


def test_cli_only_settled_trades_live_needs_no_source(tmp_path, stub_live):
    stub_live["probe_fail"] = True                         # would abort if it were called
    write_ledger(tmp_path, [r for r in mixed_ledger() if r["status"] != "open"])
    assert run(tmp_path, "status") == 0
    assert stub_live["probe_calls"] == 0
    assert "| 合计 | -$110 |" in book(tmp_path)


def test_cli_atomic_write_leaves_no_temp_file(tmp_path):
    write_ledger(tmp_path, mixed_ledger())
    assert run(tmp_path) == 0
    assert not (tmp_path / "book.md.tmp").exists()


def test_risk_reversal_record_is_handled_with_its_own_label_and_signed_cash():
    """Design 7: 90 Put sold 2.00 / 110 Call bought 1.50, 1 contract -> net credit +0.50/share,
    conservative capital (90 - 0.50) * 100 = 8950; settled at C=120 -> +1050."""
    rr = _rec(1, "ZZZQ", L.STRATEGY_RR, "expired", "2026-10-02", "2026-10-09", "2026-10-09", 1050.0,
              short_put_strike=90.0, long_call_strike=110.0, net_credit=0.50)
    open_rr = _rec(2, "ZZZR", L.STRATEGY_RR, "open", "2026-10-16", "2026-10-23",
                   short_put_strike=90.0, long_call_strike=110.0, net_credit=-0.60)
    assert L.STRATEGY_RR in paper_book.STRATEGY_ORDER
    rows = {r["id"]: r for r in paper_book.trade_rows([rr, open_rr], None, TODAY)}
    assert rows[1]["cash"] == pytest.approx(0.50) and rows[1]["capital"] == pytest.approx(8950.0)
    assert rows[2]["cash"] == pytest.approx(-0.60) and rows[2]["capital"] == pytest.approx(9060.0)
    text = paper_book.render([rr, open_rr], None, TODAY)
    assert f"| {L.RR_LABEL} |" in text and "风险反转" in text
    assert [g for g, _ in paper_book.by_strategy(list(rows.values()), live=False)] == [L.STRATEGY_RR]
