"""spread_sim mark / settle / close dispatch for bear call spread, bear put spread and long put.

All market data is faked (invented tickers ZQBR / ZQBP / ZQLP / ZZZQ); the hand-computed numbers
come from the two designs (bearish-and-new-strategies section 8, long-put section 3). Report
rendering for the new strategies is a later task, so `_write_report` is stubbed.
"""
from datetime import date
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import spread_data
import spread_ledger
import spread_report
import spread_sim
from spread_fixtures import make_chain

SNAP = date(2026, 10, 16)
EXPIRY = "2026-10-23"
MID_WEEK = date(2026, 10, 20)
AFTER = date(2026, 10, 26)


@pytest.fixture
def market(monkeypatch):
    """Fake market. Mutate the returned dict between calls; `seen` records requested strikes."""
    m = {"spot": 100.0, "puts": make_chain(spot=100.0), "calls": [], "closes": {},
         "seen": {"puts": [], "calls": []}}

    def puts(t, e, strikes=()):
        m["seen"]["puts"].append(tuple(strikes))
        return m["puts"]

    def calls(t, e, strikes=()):
        m["seen"]["calls"].append(tuple(strikes))
        return m["calls"]

    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, m["spot"]))
    monkeypatch.setattr(spread_data, "expirations",
                        lambda t: ["2026-10-16", "2026-10-21", EXPIRY, "2026-10-30"])
    monkeypatch.setattr(spread_data, "fetch_puts", puts)
    monkeypatch.setattr(spread_data, "fetch_calls", calls)
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: m["closes"].get(d))
    monkeypatch.setattr(spread_data, "monday_gap_pct", lambda t, s, c: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    return m


@pytest.fixture
def no_report(monkeypatch):
    monkeypatch.setattr(spread_sim, "_write_report", lambda *a, **k: None)


def run(tmp_path, *argv, today=SNAP):
    return spread_sim.main(["--ledger-dir", str(tmp_path), *argv], today=today)


def ledger(tmp_path):
    return spread_ledger.load(tmp_path / "spreads.json")


def _legs(*pairs):
    return [{"strike": k, "bid": mid - 0.02, "ask": mid + 0.02, "last": mid, "iv": 0.4}
            for k, mid in pairs]


def _seed(tmp_path, *recs):
    led = spread_ledger.empty_ledger()
    for rec in recs:
        led, _ = spread_ledger.add_spread(led, rec)
    spread_ledger.save(tmp_path / "spreads.json", led)


def _common(strategy, ticker, contracts, expiry, suffix):
    return {"strategy": strategy, "ticker": ticker, "contracts": contracts,
            "snapshot_date": "2026-10-16", "expiry": expiry, "underlying_price": 100.0,
            "suggestion_file": f"2026-10-16-{ticker}-{suffix}.md", "earnings_in_window": False,
            "status": "open", "exit_date": None, "exit_underlying": None, "pnl": None,
            "result": None}


# ---- records (design section 8 / long-put section 3, spot 100) ----------------------
# Bear Call: sell 105 C @2.10, buy 110 C @0.90 -> credit 1.20, width 5; +$120 / -$380.
# Bear Put:  buy 100 P @4.80, sell 90 P @1.80 -> debit 3.00, width 10; +$700 / -$300.
# Long Put:  buy 95 P @2.40 -> debit 2.40; max loss $240.

def _bearcall_rec(ticker="ZQBR", contracts=1, expiry=EXPIRY, **extra):
    return {**_common("bear_call_spread", ticker, contracts, expiry, "bearcall"),
            "option_type": "C", "short_strike": 105.0, "long_strike": 110.0, "width": 5.0,
            "credit": 1.20, "short_mid": 2.10, "long_mid": 0.90, "delta_bucket": 0.15,
            "short_delta": 0.16, "short_iv": 0.40, "exit_cost": None, **extra}


def _bearput_rec(ticker="ZQBP", contracts=1, expiry=EXPIRY, **extra):
    return {**_common("bear_put_spread", ticker, contracts, expiry, "bearput"),
            "long_put_strike": 100.0, "short_put_strike": 90.0, "width": 10.0,
            "long_put_mid": 4.80, "short_put_mid": 1.80, "debit": 3.00, "breakeven": 97.0,
            "breakeven_move_pct": -0.03, "max_profit_move_pct": -0.10, "delta_bucket": 0.50,
            "long_put_delta": -0.51, "short_put_delta": -0.22, "long_put_iv": 0.40,
            "short_put_iv": 0.42, **extra}


def _longput_rec(ticker="ZQLP", contracts=1, expiry=EXPIRY, **extra):
    return {**_common("long_put", ticker, contracts, expiry, "longput"),
            "put_strike": 95.0, "put_mid": 2.40, "debit": 2.40, "breakeven": 92.6,
            "breakeven_move_pct": -0.074, "delta_bucket": 0.30, "put_delta": -0.31,
            "put_iv": 0.40, **extra}


def _settle(market, tmp_path, rec, close):
    _seed(tmp_path, rec)
    market["closes"][date.fromisoformat(EXPIRY)] = close
    assert run(tmp_path, "status", today=AFTER) == 0
    return ledger(tmp_path)["spreads"][0]


# ---- _line ----------------------------------------------------------------------------

def test_line_texts():
    assert spread_sim._line(_bearcall_rec(id=1)) == "#1 ZQBR 105/110 Call 贷方价差 到期 2026-10-23"
    assert spread_sim._line(_bearput_rec(id=2)) == "#2 ZQBP 100/90 Put 借方价差 到期 2026-10-23"
    assert spread_sim._line(_longput_rec(id=3)) == "#3 ZQLP 95 买入 Put 到期 2026-10-23"


def test_is_predicates_are_exclusive():
    recs = {"bc": _bearcall_rec(), "bp": _bearput_rec(), "lp": _longput_rec()}
    assert [k for k, r in recs.items() if spread_sim._is_bearcall(r)] == ["bc"]
    assert [k for k, r in recs.items() if spread_sim._is_bearput(r)] == ["bp"]
    assert [k for k, r in recs.items() if spread_sim._is_longput(r)] == ["lp"]
    bull_put = {"ticker": "ABC", "short_strike": 105.0, "long_strike": 110.0, "credit": 1.2}
    assert not any(f(bull_put) for f in (spread_sim._is_bearcall, spread_sim._is_bearput,
                                         spread_sim._is_longput))


# ---- Bear Call ---------------------------------------------------------------------------

def test_status_marks_bearcall_with_explicit_call_strikes(market, tmp_path, capsys, no_report):
    market["calls"] = _legs((105.0, 1.40), (110.0, 0.50))   # cost 0.90 -> +$30
    _seed(tmp_path, _bearcall_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    out = capsys.readouterr().out
    assert "#1 ZQBR 105/110 Call 贷方价差 到期 2026-10-23：浮盈亏 +$30，距短腿 5.0%" in out
    assert "止盈" not in out and "止损" not in out
    assert market["seen"]["calls"] == [(105.0, 110.0)] and market["seen"]["puts"] == []
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert m["cost"] == pytest.approx(0.90) and m["pnl"] == 30.0
    assert m["dist_pct"] == pytest.approx(5.0)   # (short 105 - spot 100) / 100, + = OTM
    assert m["flags"] == {"stop": False, "take_profit": False} and m["stale"] is False


def test_bearcall_dist_pct_negative_once_above_short(market, no_report):
    market["calls"], market["spot"] = _legs((105.0, 3.00), (110.0, 1.00)), 107.0
    m = spread_sim._mark(_bearcall_rec())
    assert m["dist_pct"] == pytest.approx((105 - 107) / 107 * 100)


@pytest.mark.parametrize("short_mid,long_mid,pnl,stop,tp", [
    (1.00, 0.40, 60.0, False, True),      # cost 0.60 -> +$60 = 50% of $120 (inclusive)
    (1.01, 0.40, 59.0, False, False),
    (3.60, 0.50, -190.0, True, False),    # cost 3.10 -> -$190 = -50% of $380 (inclusive)
    (3.59, 0.50, -189.0, False, False),
])
def test_bearcall_threshold_boundaries(market, tmp_path, capsys, no_report,
                                       short_mid, long_mid, pnl, stop, tp):
    market["calls"] = _legs((105.0, short_mid), (110.0, long_mid))
    m = spread_sim._mark(_bearcall_rec())
    assert m["pnl"] == pytest.approx(pnl) and m["flags"] == {"stop": stop, "take_profit": tp}
    _seed(tmp_path, _bearcall_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    out = capsys.readouterr().out
    stop_text, tp_text = spread_report.BEARCALL_THRESHOLD_LABELS
    assert (stop_text in out) is stop and (tp_text in out) is tp


@pytest.mark.parametrize("keep", [105.0, 110.0])
def test_bearcall_missing_leg_reports_no_quote(market, tmp_path, capsys, no_report, keep):
    market["calls"] = [r for r in _legs((105.0, 1.40), (110.0, 0.50)) if r["strike"] == keep]
    _seed(tmp_path, _bearcall_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    assert "取不到报价" in capsys.readouterr().out
    assert set(spread_sim._mark(_bearcall_rec())) == {"error"}
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"


@pytest.mark.parametrize("close,cost,pnl,result", [
    (100.0, 0.0, 120.0, "win"),
    (105.0, 0.0, 120.0, "win"),          # = short strike: full credit
    (106.20, 1.20, 0.0, "loss"),         # = breakeven: 0.0, never -0.0
    (108.0, 3.00, -180.0, "loss"),
    (110.0, 5.00, -380.0, "loss"),       # = long strike
    (120.0, 5.00, -380.0, "loss"),       # clamped to width
])
def test_settle_bearcall_design_table(market, tmp_path, capsys, no_report,
                                      close, cost, pnl, result):
    s = _settle(market, tmp_path, _bearcall_rec(), close)
    assert s["status"] == "expired" and s["exit_date"] == EXPIRY and s["exit_underlying"] == close
    assert s["exit_cost"] == pytest.approx(cost) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and str(s["pnl"]) != "-0.0"
    assert "breached_side" not in s and "exit_value" not in s
    out = capsys.readouterr().out
    assert "已到期结算" in out and "-$0" not in out


def test_settle_bearcall_two_contracts(market, tmp_path, no_report):
    s = _settle(market, tmp_path, _bearcall_rec(contracts=2), 108.0)
    assert s["pnl"] == pytest.approx(-360.0) and s["result"] == "loss"


@pytest.mark.parametrize("short_mid,long_mid,cost,pnl,result", [
    (1.40, 0.50, 0.90, 30.0, "win"),
    (0.10, 0.05, 0.05, 115.0, "win"),
    (0.40, 0.50, 0.00, 120.0, "win"),     # short - long < 0 -> clamped to 0
    (6.00, 0.50, 5.00, -380.0, "loss"),   # 5.50 clamped to width
])
def test_close_bearcall_uses_mids_clamped(market, tmp_path, capsys, no_report,
                                          short_mid, long_mid, cost, pnl, result):
    market["calls"] = _legs((105.0, short_mid), (110.0, long_mid))
    _seed(tmp_path, _bearcall_rec())
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["exit_date"] == "2026-10-20"
    assert s["exit_cost"] == pytest.approx(cost) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["exit_underlying"] == 100.0
    assert market["seen"]["calls"] == [(105.0, 110.0)] and market["seen"]["puts"] == []
    assert "已提前平仓 #1 ZQBR 105/110 Call 贷方价差" in capsys.readouterr().out


@pytest.mark.parametrize("keep", [105.0, 110.0])
def test_close_bearcall_refuses_without_quote(market, tmp_path, capsys, no_report, keep):
    market["calls"] = [r for r in _legs((105.0, 1.40), (110.0, 0.50)) if r["strike"] == keep]
    _seed(tmp_path, _bearcall_rec())
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 1
    assert "取不到报价" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


def test_bearcall_never_calls_put_direction_expiry_pnl(market, tmp_path, monkeypatch, no_report):
    def boom(*a, **k):
        raise AssertionError("put-direction helper used for a bear call spread")
    for name in ("expiry_pnl", "expiry_cost", "csp_expiry_pnl"):
        monkeypatch.setattr(spread_sim.core, name, boom)
    market["calls"] = _legs((105.0, 1.40), (110.0, 0.50))
    _seed(tmp_path, _bearcall_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 0
    other = tmp_path / "settle"
    other.mkdir()
    for close in (100.0, 108.0, 120.0):
        s = _settle(market, other, _bearcall_rec(), close)
        assert s["status"] == "expired"
    assert market["seen"]["puts"] == []


# ---- Bear Put ----------------------------------------------------------------------------

def test_status_marks_bearput_with_explicit_put_strikes(market, tmp_path, capsys, no_report):
    market["puts"], market["spot"] = _legs((100.0, 5.50), (90.0, 1.50)), 96.0  # value 4.00 -> +$100
    _seed(tmp_path, _bearput_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    out = capsys.readouterr().out
    assert "#1 ZQBP 100/90 Put 借方价差 到期 2026-10-23：浮盈亏 +$100，距长腿 4.2%" in out
    assert "止盈" not in out and "止损" not in out
    assert market["seen"]["puts"] == [(100.0, 90.0)] and market["seen"]["calls"] == []
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert set(m) == {"value", "pnl", "underlying", "dist_pct", "flags", "stale",
                      "long_mid", "short_mid", "long_iv", "short_iv"}
    assert m["value"] == pytest.approx(4.0) and m["pnl"] == 100.0
    # sign convention pinned: (long strike - spot) / spot, + once the long put is in the money
    assert m["dist_pct"] == pytest.approx((100 - 96) / 96 * 100)


def test_bearput_dist_pct_negative_above_long_strike(market, no_report):
    market["puts"], market["spot"] = _legs((100.0, 2.00), (90.0, 0.50)), 104.0
    assert spread_sim._mark(_bearput_rec())["dist_pct"] == pytest.approx(-4 / 104 * 100)


@pytest.mark.parametrize("long_mid,short_mid,pnl,stop,tp", [
    (8.00, 1.50, 350.0, False, True),     # value 6.50 -> +$350 = 50% of $700 (inclusive)
    (7.99, 1.50, 349.0, False, False),
    (2.50, 1.00, -150.0, True, False),    # value 1.50 -> -$150 = -50% of $300 (inclusive)
    (2.51, 1.00, -149.0, False, False),
])
def test_bearput_threshold_boundaries(market, tmp_path, capsys, no_report,
                                      long_mid, short_mid, pnl, stop, tp):
    market["puts"] = _legs((100.0, long_mid), (90.0, short_mid))
    m = spread_sim._mark(_bearput_rec())
    assert m["pnl"] == pytest.approx(pnl) and m["flags"] == {"stop": stop, "take_profit": tp}
    _seed(tmp_path, _bearput_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    out = capsys.readouterr().out
    stop_text, tp_text = spread_report.BEARPUT_THRESHOLD_LABELS
    assert (stop_text in out) is stop and (tp_text in out) is tp


@pytest.mark.parametrize("keep", [100.0, 90.0])
def test_bearput_missing_leg_reports_no_quote(market, tmp_path, capsys, no_report, keep):
    market["puts"] = [r for r in _legs((100.0, 5.50), (90.0, 1.50)) if r["strike"] == keep]
    _seed(tmp_path, _bearput_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    assert "取不到报价" in capsys.readouterr().out
    assert set(spread_sim._mark(_bearput_rec())) == {"error"}


@pytest.mark.parametrize("spot", [0.0, None])
def test_buyers_invalid_underlying_is_error_not_raise(market, monkeypatch, spot):
    market["puts"] = _legs((100.0, 5.50), (90.0, 1.50), (95.0, 3.00))
    market["calls"] = _legs((105.0, 1.40), (110.0, 0.50))
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, spot))
    for rec in (_bearcall_rec(), _bearput_rec(), _longput_rec()):
        m = spread_sim._mark(rec)
        assert set(m) == {"error"} and "现价" in m["error"]


@pytest.mark.parametrize("close,value,pnl,result,fraction", [
    (105.0, 0.0, -300.0, "loss", -0.4286),
    (100.0, 0.0, -300.0, "loss", -0.4286),   # = long strike
    (97.0, 3.0, 0.0, "loss", 0.0),           # = breakeven: 0.0, never -0.0
    (95.0, 5.0, 200.0, "win", 0.2857),
    (90.0, 10.0, 700.0, "win", 1.0),         # = short strike
    (80.0, 10.0, 700.0, "win", 1.0),         # clamped to width
])
def test_settle_bearput_design_table(market, tmp_path, capsys, no_report,
                                     close, value, pnl, result, fraction):
    s = _settle(market, tmp_path, _bearput_rec(), close)
    assert s["status"] == "expired" and s["exit_date"] == EXPIRY and s["exit_underlying"] == close
    assert s["exit_value"] == pytest.approx(value) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_max_captured"] == fraction
    assert str(s["pnl"]) != "-0.0" and str(s["fraction_of_max_captured"]) != "-0.0"
    for key in ("exit_cost", "breached_side", "credit", "short_strike", "long_strike",
                "net_credit"):
        assert key not in s
    out = capsys.readouterr().out
    assert "已到期结算" in out and "-$0" not in out


@pytest.mark.parametrize("long_mid,short_mid,value,pnl,result,fraction", [
    (5.50, 1.50, 4.00, 100.0, "win", 0.1429),
    (12.00, 1.00, 10.00, 700.0, "win", 1.0),      # 11 clamped to width 10
    (1.00, 1.20, 0.00, -300.0, "loss", -0.4286),  # -0.20 clamped to 0
])
def test_close_bearput_uses_mids_clamped(market, tmp_path, capsys, no_report,
                                         long_mid, short_mid, value, pnl, result, fraction):
    market["puts"] = _legs((100.0, long_mid), (90.0, short_mid))
    _seed(tmp_path, _bearput_rec())
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["exit_date"] == "2026-10-20"
    assert s["exit_value"] == pytest.approx(value) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_max_captured"] == fraction
    assert s["exit_long_put_mid"] == pytest.approx(long_mid)
    assert s["exit_short_put_mid"] == pytest.approx(short_mid)
    assert s["exit_long_put_iv"] == 0.4 and s["exit_short_put_iv"] == 0.4
    assert "exit_cost" not in s and "breached_side" not in s
    assert market["seen"]["puts"] == [(100.0, 90.0)]
    assert "已提前平仓 #1 ZQBP 100/90 Put 借方价差" in capsys.readouterr().out


@pytest.mark.parametrize("keep", [100.0, 90.0])
def test_close_bearput_refuses_without_quote(market, tmp_path, capsys, no_report, keep):
    market["puts"] = [r for r in _legs((100.0, 5.50), (90.0, 1.50)) if r["strike"] == keep]
    _seed(tmp_path, _bearput_rec())
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 1
    assert "取不到报价" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


# ---- Long Put ----------------------------------------------------------------------------

def test_status_marks_longput_with_explicit_strike(market, tmp_path, capsys, no_report):
    market["puts"] = _legs((95.0, 3.00))                     # value 3.00 -> +$60
    _seed(tmp_path, _longput_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    out = capsys.readouterr().out
    assert "#1 ZQLP 95 买入 Put 到期 2026-10-23：浮盈亏 +$60，距行权价 5.0%" in out
    assert "止盈" not in out and "止损" not in out
    assert market["seen"]["puts"] == [(95.0,)] and market["seen"]["calls"] == []
    m = spread_sim._mark(ledger(tmp_path)["spreads"][0])
    assert set(m) == {"value", "pnl", "underlying", "dist_pct", "flags", "stale", "put_mid",
                      "put_iv"}
    assert m["value"] == pytest.approx(3.0) and m["pnl"] == 60.0
    # sign convention pinned: (spot - strike) / spot, + while the put is out of the money
    assert m["dist_pct"] == pytest.approx(5.0)


@pytest.mark.parametrize("mid,pnl,stop,tp", [
    (4.80, 240.0, False, True),    # +$240 = 1.00 x $240 (inclusive)
    (4.79, 239.0, False, False),
    (1.20, -120.0, True, False),   # -$120 = -0.50 x $240 (inclusive)
    (1.21, -119.0, False, False),
])
def test_longput_threshold_boundaries(market, tmp_path, capsys, no_report, mid, pnl, stop, tp):
    market["puts"] = _legs((95.0, mid))
    m = spread_sim._mark(_longput_rec())
    assert m["pnl"] == pytest.approx(pnl) and m["flags"] == {"stop": stop, "take_profit": tp}
    _seed(tmp_path, _longput_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    out = capsys.readouterr().out
    stop_text, tp_text = spread_report.LONGPUT_THRESHOLD_LABELS
    assert (stop_text in out) is stop and (tp_text in out) is tp


def test_longput_missing_leg_reports_no_quote(market, tmp_path, capsys, no_report):
    market["puts"] = _legs((90.0, 1.00))
    _seed(tmp_path, _longput_rec())
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    assert "取不到报价" in capsys.readouterr().out
    assert set(spread_sim._mark(_longput_rec())) == {"error"}


@pytest.mark.parametrize("close,value,pnl,result,fraction", [
    (100.0, 0.0, -240.0, "loss", -1.0),
    (95.0, 0.0, -240.0, "loss", -1.0),       # = strike
    (92.60, 2.40, 0.0, "loss", 0.0),         # = breakeven: 0.0, never -0.0
    (90.0, 5.0, 260.0, "win", 1.0833),
    (80.0, 15.0, 1260.0, "win", 5.25),       # no upper clamp
])
def test_settle_longput_design_table(market, tmp_path, capsys, no_report,
                                     close, value, pnl, result, fraction):
    s = _settle(market, tmp_path, _longput_rec(), close)
    assert s["status"] == "expired" and s["exit_date"] == EXPIRY and s["exit_underlying"] == close
    assert s["exit_value"] == pytest.approx(value) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_debit"] == fraction
    assert str(s["pnl"]) != "-0.0" and str(s["fraction_of_debit"]) != "-0.0"
    for key in ("exit_cost", "breached_side", "credit", "short_strike", "long_strike",
                "net_credit", "width", "fraction_of_max_captured"):
        assert key not in s
    out = capsys.readouterr().out
    assert "已到期结算" in out and "-$0" not in out


@pytest.mark.parametrize("mid,pnl,result,fraction", [
    (3.00, 60.0, "win", 0.25),
    (0.05, -235.0, "loss", -0.9792),
    (12.40, 1000.0, "win", 4.1667),   # no upper clamp
])
def test_close_longput_uses_current_mid(market, tmp_path, capsys, no_report,
                                        mid, pnl, result, fraction):
    market["puts"] = [{"strike": 95.0, "bid": mid, "ask": mid, "last": mid, "iv": 0.4}]
    _seed(tmp_path, _longput_rec())
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["status"] == "closed_early" and s["exit_date"] == "2026-10-20"
    assert s["exit_value"] == pytest.approx(mid) and s["pnl"] == pytest.approx(pnl)
    assert s["result"] == result and s["fraction_of_debit"] == fraction
    assert s["exit_put_mid"] == pytest.approx(mid) and s["exit_put_iv"] == 0.4
    assert "exit_cost" not in s and "breached_side" not in s
    assert market["seen"]["puts"] == [(95.0,)]
    assert "已提前平仓 #1 ZQLP 95 买入 Put" in capsys.readouterr().out


def test_close_longput_refuses_without_quote(market, tmp_path, capsys, no_report):
    market["puts"] = []
    _seed(tmp_path, _longput_rec())
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=MID_WEEK) == 1
    assert "取不到报价" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


# ---- shared behaviour ----------------------------------------------------------------------

@pytest.mark.parametrize("make", [_bearcall_rec, _bearput_rec, _longput_rec])
def test_close_after_expiry_refused(market, tmp_path, capsys, no_report, make):
    _seed(tmp_path, make())
    before = (tmp_path / "spreads.json").read_text(encoding="utf-8")
    assert run(tmp_path, "close", "1", today=date(2026, 10, 24)) == 1
    assert "过到期日" in capsys.readouterr().err
    assert (tmp_path / "spreads.json").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("make", [_bearcall_rec, _bearput_rec, _longput_rec])
def test_waits_on_expiry_day(market, tmp_path, capsys, no_report, make):
    _seed(tmp_path, make())
    market["closes"][date.fromisoformat(EXPIRY)] = 80.0
    assert run(tmp_path, "status", today=date.fromisoformat(EXPIRY)) == 0
    assert ledger(tmp_path)["spreads"][0]["status"] == "open"
    assert "次日或之后再运行" in capsys.readouterr().out


def test_bearput_and_longput_never_call_seller_helpers(market, tmp_path, monkeypatch, no_report):
    def boom(*a, **k):
        raise AssertionError("seller helper used for a buyer strategy")
    for name in ("threshold_flags", "cost_to_close", "unrealized_pnl", "close_pnl", "expiry_pnl",
                 "breakeven_win_rate", "expiry_cost", "csp_expiry_pnl", "csp_threshold_flags",
                 "bearcall_pnl", "bearcall_close_cost", "bearcall_expiry_cost"):
        monkeypatch.setattr(spread_sim.core, name, boom)
    market["puts"] = _legs((100.0, 5.50), (90.0, 1.50), (95.0, 3.00))
    for i, make in enumerate((_bearput_rec, _longput_rec)):
        folder = tmp_path / f"live{i}"
        folder.mkdir()
        _seed(folder, make())
        assert run(folder, "status", today=MID_WEEK) == 0
        assert run(folder, "close", "1", today=MID_WEEK) == 0
        assert ledger(folder)["spreads"][0]["status"] == "closed_early"
        settled = tmp_path / f"settle{i}"
        settled.mkdir()
        assert _settle(market, settled, make(), 93.0)["status"] == "expired"


@pytest.mark.parametrize("failure", ["no_quote", "data_error"])
def test_status_one_error_does_not_abort_the_batch(market, tmp_path, capsys, monkeypatch,
                                                   no_report, failure):
    def puts(t, e, strikes=()):
        if t == "ZQBP":
            if failure == "data_error":
                raise spread_data.DataError("ZQBP 期权链挂了")
            return []
        return _legs((95.0, 3.00))
    monkeypatch.setattr(spread_data, "fetch_puts", puts)
    market["calls"] = _legs((105.0, 1.40), (110.0, 0.50))
    _seed(tmp_path, _bearput_rec(), _longput_rec(), _bearcall_rec(),
          _bearput_rec(ticker="ZQBS", expiry="2026-10-16"))
    market["closes"][date(2026, 10, 16)] = 95.0
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    lines = capsys.readouterr().out.splitlines()
    bad = next(line for line in lines if line.startswith("#1 ZQBP"))
    assert "取不到报价" in bad or "挂了" in bad
    assert any(line.startswith("#2 ZQLP") and "浮盈亏 +$60" in line for line in lines)
    assert any(line.startswith("#3 ZQBR") and "浮盈亏 +$30" in line for line in lines)
    s1, s2, s3, s4 = ledger(tmp_path)["spreads"]
    assert [s1["status"], s2["status"], s3["status"]] == ["open"] * 3
    assert s4["status"] == "expired" and s4["pnl"] == pytest.approx(200.0)


def test_paper_shares_untouched(market, tmp_path, capsys, no_report):
    import paper_shares
    doc, _ = paper_shares.add_lot(paper_shares.empty_lots(), ticker="ZQBR", shares=100,
                                  cost=95.0, opened_on="2026-10-01")
    lots = tmp_path / paper_shares.LOTS_FILE
    paper_shares.save_lots(lots, doc)
    before = lots.read_bytes()
    market["calls"] = _legs((105.0, 1.40), (110.0, 0.50))
    market["puts"] = _legs((100.0, 5.50), (90.0, 1.50), (95.0, 3.00))
    _seed(tmp_path, _bearcall_rec(), _bearput_rec(), _longput_rec(),
          _bearcall_rec(ticker="ZQBX"))
    assert run(tmp_path, "status", today=MID_WEEK) == 0
    assert run(tmp_path, "close", "4", today=MID_WEEK) == 0
    market["closes"][date.fromisoformat(EXPIRY)] = 85.0   # every put in the money
    assert run(tmp_path, "status", today=AFTER) == 0
    assert lots.read_bytes() == before
    assert "假设持仓" not in capsys.readouterr().out
    for s in ledger(tmp_path)["spreads"]:
        assert "lot_applied" not in s and "lot_ref" not in s
    assert [s["status"] for s in ledger(tmp_path)["spreads"]] == (
        ["expired", "expired", "expired", "closed_early"])


def test_existing_strategies_status_and_close_unchanged(market, tmp_path, capsys, no_report):
    """A bull put spread behaves the same whether or not the three new records share the ledger."""
    alone, mixed = tmp_path / "alone", tmp_path / "mixed"
    outputs, records = [], []
    for folder in (alone, mixed):
        folder.mkdir()
        market["puts"], market["spot"] = make_chain(spot=100.0), 100.0
        assert run(folder, "suggest", "ABC", "--width", "5") == 0
        assert run(folder, "open", "ABC", "0.20") == 0
        if folder is mixed:
            led = ledger(folder)
            for rec in (_bearcall_rec(), _bearput_rec(), _longput_rec()):
                led, _ = spread_ledger.add_spread(led, rec)
            spread_ledger.save(folder / "spreads.json", led)
        capsys.readouterr()
        market["puts"], market["spot"] = make_chain(spot=104.0), 104.0
        assert run(folder, "status", today=MID_WEEK) == 0
        abc = [line for line in capsys.readouterr().out.splitlines() if line.startswith("#1 ABC")]
        assert run(folder, "close", "1", today=MID_WEEK) == 0
        outputs.append((abc, capsys.readouterr().out))
        records.append(ledger(folder)["spreads"][0])
    assert outputs[0] == outputs[1] and records[0] == records[1]
    assert outputs[0][0]
    for key in ("exit_value", "fraction_of_max_captured", "fraction_of_debit"):
        assert key not in records[0]
    assert records[0]["exit_cost"] is not None
