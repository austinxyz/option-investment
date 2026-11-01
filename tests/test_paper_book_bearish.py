"""paper_book / paper_book_render / paper_book_public with the three bearish strategies.

Invented tickers; hand numbers from the two designs (spot 100, 1 contract):
  Bear Call 105/110, credit 1.20 -> opening cash +1.20/share, capital (5 - 1.20) x 100 = 380.
  Bear Put 100/90, debit 3.00    -> opening cash -3.00/share, capital 300.
  Long Put 95, debit 2.40        -> opening cash -2.40/share, capital 240.
Settled: bear call +120, bear put +700, long put -240 -> realized +580.
"""
from datetime import date
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import paper_book  # noqa: E402
import paper_book_public as pub  # noqa: E402
import spread_ledger as L  # noqa: E402
from test_paper_book import TODAY, _rec, book, eight_strategies, mixed_ledger, run, write_ledger  # noqa: E402
from test_paper_book_public import DAY, MARKS, PRIVATE, ledger  # noqa: E402


def bearish_records(status="open"):
    settled = status != "open"
    exit_date = "2026-10-23" if settled else None
    pnls = (120.0, 700.0, -240.0) if settled else (None, None, None)
    return [
        _rec(21, "ZQBR", L.STRATEGY_BEAR_CALL, status, "2026-10-16", "2026-10-23", exit_date,
             pnls[0], option_type="C", short_strike=105.0, long_strike=110.0, width=5.0,
             credit=1.20, short_mid=2.10, long_mid=0.90, delta_bucket=0.15),
        _rec(22, "ZQBP", L.STRATEGY_BEAR_PUT, status, "2026-10-16", "2026-10-23", exit_date,
             pnls[1], long_put_strike=100.0, short_put_strike=90.0, width=10.0, debit=3.00,
             long_put_mid=4.80, short_put_mid=1.80, delta_bucket=0.50),
        _rec(23, "ZQLP", L.STRATEGY_LONG_PUT, status, "2026-10-16", "2026-10-23", exit_date,
             pnls[2], put_strike=95.0, put_mid=2.40, debit=2.40, delta_bucket=0.30),
    ]


def test_bearish_strategies_follow_rr_in_display_order():
    order = paper_book.STRATEGY_ORDER
    assert order[order.index(L.STRATEGY_RR) + 1:] == (
        L.STRATEGY_BEAR_CALL, L.STRATEGY_BEAR_PUT, L.STRATEGY_LONG_PUT)
    assert set(order) == set(paper_book.OPENING_CASH)


@pytest.mark.parametrize("status", ["open", "expired"])
def test_bearish_opening_cash_signs_and_capital(status):
    rows = {r["id"]: r for r in paper_book.trade_rows(bearish_records(status), None, TODAY)}
    assert rows[21]["cash"] == pytest.approx(1.20) and rows[21]["capital"] == pytest.approx(380.0)
    assert rows[22]["cash"] == pytest.approx(-3.00) and rows[22]["capital"] == pytest.approx(300.0)
    assert rows[23]["cash"] == pytest.approx(-2.40) and rows[23]["capital"] == pytest.approx(240.0)


def test_book_renders_open_and_settled_bearish_with_labels():
    recs = [*mixed_ledger(), *bearish_records("open"),
            *({**r, "id": r["id"] + 10, "ticker": r["ticker"] + "S"}
              for r in bearish_records("expired"))]
    text = paper_book.render(recs, None, date(2026, 10, 26))
    for label in (L.BEARCALL_LABEL, L.BEARPUT_LABEL, L.LONGPUT_LABEL):
        assert f"| {label} | 1 | 1 |" in text, label
    assert "| 105/110 Call |" in text and "| 100/90 Put |" in text and "| 95 Put |" in text
    assert "收入 $1.20/股（$120.00/张）" in text
    assert "支出 $3.00/股（$300.00/张）" in text and "支出 $2.40/股（$240.00/张）" in text
    rows = paper_book.trade_rows(recs, None, date(2026, 10, 26))
    groups = dict(paper_book.by_strategy(rows, live=False))
    assert groups[L.STRATEGY_BEAR_CALL]["realized"] == pytest.approx(120.0)
    assert groups[L.STRATEGY_BEAR_PUT]["realized"] == pytest.approx(700.0)
    assert groups[L.STRATEGY_LONG_PUT]["realized"] == pytest.approx(-240.0)


def test_book_live_marks_for_bearish():
    recs = bearish_records("open")
    marks = {21: {"pnl": 30.0}, 22: {"pnl": -150.0}, 23: {"error": "取不到报价：期权链里找不到这条腿的有效报价"}}
    text = paper_book.render(recs, marks, TODAY)
    total = paper_book.summarize(paper_book.trade_rows(recs, marks, TODAY), live=True)
    assert total["unrealized"] == pytest.approx(-120.0) and total["n_missing"] == 1
    assert "行情缺失" in text


def test_cli_book_with_all_eleven_strategies(tmp_path, capsys):
    recs = [*eight_strategies(), *bearish_records("open")]
    assert {L.strategy_of(r) for r in recs} == set(paper_book.STRATEGY_ORDER)
    write_ledger(tmp_path, recs)
    assert run(tmp_path) == 0
    text = book(tmp_path)
    assert "| **合计** | 0 | 11 |" in text
    out = capsys.readouterr().out
    for label in (L.BEARCALL_LABEL, L.BEARPUT_LABEL, L.LONGPUT_LABEL):
        assert f"  - {label}：已结算 0、未平仓 1" in out


def test_public_page_byte_identical_with_bearish_records():
    base = pub.render_public_page(ledger() + PRIVATE, MARKS, DAY)
    extra = bearish_records("open") + [
        {**r, "id": r["id"] + 10} for r in bearish_records("expired")]
    marks = {**MARKS, 21: {"pnl": 30.0, "dist_pct": 5.0, "stale": False,
                           "flags": {"stop": False, "take_profit": False}}}
    with_bearish = pub.render_public_page(ledger() + PRIVATE + extra, marks, DAY)
    assert with_bearish.encode("utf-8") == base.encode("utf-8")
    offline = pub.render_public_page(ledger() + extra, None, DAY)
    assert offline == pub.render_public_page(ledger(), None, DAY)
    for word in ("ZQBR", "ZQBP", "ZQLP", L.BEARCALL_LABEL, L.BEARPUT_LABEL, L.LONGPUT_LABEL):
        assert word not in with_bearish
