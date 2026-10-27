"""CLI wiring of the Yahoo spread rule (share plan Task 4 follow-up).

Each real CLI `main(...)` runs twice over the same faked chain whose key leg has a 7.7% spread:
Yahoo mode (10% hard line, 5% warn) must PICK the leg and print the warning; IBKR mode must
reject / walk past it and print no such warning. A CLI that drops `spread_kwargs()` fails the
Yahoo test; a CLI hard-wired to the Yahoo profile fails the IBKR test. Only spread_data's fetch
functions are stubbed; ZZZQ and friends are invented.
"""
from datetime import date

import pytest

import bullcall_sim
import call_screen
import collar_screen
import condor_sim
import condor_sim_rules
import holdings
import put_screen
import spread_data
from spread_fixtures import make_call_chain, make_chain

WARN_TEXT = "超过 IBKR 模式的 5% 门槛"
WARN_77 = "买卖价差 7.7%，超过 IBKR 模式的 5% 门槛（Yahoo 报价延迟，放宽到 10%）"
SNAP = date(2026, 10, 16)
PCT = 0.077


def _widen(rows, strike=None, pct=PCT):
    """The same rows with bid/ask replaced by a `pct` spread around the mid (one strike, or all)."""
    out = []
    for r in rows:
        r = dict(r)
        if strike is None or r["strike"] == strike:
            mid = (r["bid"] + r["ask"]) / 2
            r["bid"], r["ask"] = mid * (1 - pct / 2), mid * (1 + pct / 2)
        out.append(r)
    return out


def _common(monkeypatch, spot=100.0):
    monkeypatch.setattr(spread_data, "ensure_source", lambda: None)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (SNAP, spot))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    monkeypatch.setattr(spread_data, "moving_averages",
                        lambda t: {"snapshot_date": SNAP, "close": spot, "ma50": 95.0, "ma200": 90.0})
    monkeypatch.setattr(spread_data, "month_return_pct", lambda t: 3.0)


def _texts(ledger):
    """Every file the suggest run wrote, concatenated (md and json)."""
    files = sorted(p for p in ledger.rglob("*") if p.is_file() and p.suffix in (".md", ".json"))
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def _lot(ticker="ZZZQ"):
    return holdings.HeldLot(
        source="real", lot_id=None, ticker=ticker, shares=300.0, cost_basis=90.0,
        original_cost=90.0, reference_price=90.0, cost_estimated=False, accounts=1,
        label=f"{ticker}（真实）")


# ---- put-screen -----------------------------------------------------------------------

def _put_screen(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "tickers" / "ZZZQ").mkdir(parents=True)
    (wiki / "sectors").mkdir()
    (wiki / "tickers" / "ZZZQ" / "overview.md").write_text("**Status**: Watch\n", encoding="utf-8")
    monkeypatch.setattr(put_screen, "WIKI_DIR", wiki)
    _common(monkeypatch)
    strikes = [float(k) for k in range(70, 100)]
    tight = make_chain(spot=100.0, days=35, iv=0.40, strikes=strikes, quote_spread=0.01)
    target = put_screen.rules.pick_put(tight, 100.0, 35)[0]["strike"]
    chain = _widen(tight, target)
    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-23", "2026-11-20"])
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=(): chain)
    ledger = tmp_path / "ledger"
    code = put_screen.main(["--ledger-dir", str(ledger), "suggest", "--dte", "monthly"],
                           today=date(2026, 10, 18))
    assert code == 0
    return target, _texts(ledger)


@pytest.mark.data_source("yahoo")
def test_put_screen_cli_yahoo_picks_wide_leg_with_warning(monkeypatch, tmp_path):
    target, text = _put_screen(monkeypatch, tmp_path)
    assert WARN_77 in text and f'"strike": {target},' in text


def test_put_screen_cli_ibkr_walks_past_wide_leg(monkeypatch, tmp_path):
    target, text = _put_screen(monkeypatch, tmp_path)
    assert WARN_TEXT not in text and f'"strike": {target},' not in text


# ---- call-screen ----------------------------------------------------------------------

def _call_screen(monkeypatch, tmp_path):
    monkeypatch.setattr(holdings, "real_lots", lambda *a, **k: [_lot()])
    _common(monkeypatch)
    tight = make_call_chain(spot=100.0, days=7, iv=0.40, quote_spread=0.01)
    target = call_screen.rules.pick_call(tight, 100.0, 7)[0]["strike"]
    chain = _widen(tight, target)
    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-23"])
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e: chain)
    ledger = tmp_path / "ledger"
    assert call_screen.main(["--ledger-dir", str(ledger), "suggest"], today=date(2026, 10, 17)) == 0
    return target, _texts(ledger)


@pytest.mark.data_source("yahoo")
def test_call_screen_cli_yahoo_picks_wide_leg_with_warning(monkeypatch, tmp_path):
    target, text = _call_screen(monkeypatch, tmp_path)
    assert WARN_77 in text and f'"strike": {target},' in text


def test_call_screen_cli_ibkr_walks_past_wide_leg(monkeypatch, tmp_path):
    target, text = _call_screen(monkeypatch, tmp_path)
    assert WARN_TEXT not in text and f'"strike": {target},' not in text


# ---- collar-screen --------------------------------------------------------------------

def _collar_screen(monkeypatch, tmp_path):
    monkeypatch.setattr(holdings, "real_lots", lambda *a, **k: [_lot()])
    _common(monkeypatch)
    puts = _widen(make_chain(spot=100.0, days=35, iv=0.35, quote_spread=0.01,
                             strikes=[float(k) for k in range(60, 100)]), 90.0)
    calls = make_call_chain(spot=100.0, iv=0.35, days=35, quote_spread=0.02,
                            strikes=[float(k) for k in range(101, 131)])
    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-23", "2026-11-20"])
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: [float(k) for k in range(60, 141)])
    monkeypatch.setattr(spread_data, "fetch_puts", lambda t, e, strikes=(), **kw: puts)
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(), **kw: calls)
    ledger = tmp_path / "ledger"
    code = collar_screen.main(["--ledger-dir", str(ledger), "suggest"], today=date(2026, 10, 17))
    assert code == 0
    return _texts(ledger)


@pytest.mark.data_source("yahoo")
def test_collar_screen_cli_yahoo_picks_wide_put_with_warning(monkeypatch, tmp_path):
    text = _collar_screen(monkeypatch, tmp_path)
    assert WARN_77 in text and "买卖价差过宽（超过权利金的 10%）：行权价 90" not in text


def test_collar_screen_cli_ibkr_rejects_wide_put(monkeypatch, tmp_path):
    text = _collar_screen(monkeypatch, tmp_path)
    assert WARN_TEXT not in text and "买卖价差过宽（超过权利金的 5%）：行权价 90" in text


# ---- condor-sim (the wing request goes through _wing_strikes) -------------------------

def _condor_sim(monkeypatch, tmp_path):
    _common(monkeypatch)
    listed = [float(k) for k in range(70, 131)]
    # Narrow on purpose: with --width 5 both long wings (90 and 111) lie outside the windows,
    # so a bucket only survives if _wing_strikes still sees the wide short legs as valid.
    put_window = [float(k) for k in range(93, 100)]
    call_window = [float(k) for k in range(101, 109)]
    tight_p = make_chain(spot=100.0, days=7, strikes=put_window, quote_spread=0.01)
    short_put = condor_sim_rules.pick_short_put(tight_p, 100.0, 7, 0.15)["quote"]["strike"]

    asked: list[float] = []   # explicit (wing) strikes the CLI asked for

    def fetch_puts(t, e, strikes=(), window_pct=None, window_limit=None):
        asked.extend(strikes)
        wanted = sorted(set(put_window) | (set(strikes) & set(listed)))
        rows = make_chain(spot=100.0, days=7, strikes=wanted, quote_spread=0.01)
        return _widen(rows)  # every row wide: no bucket can borrow another bucket's wing request

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        asked.extend(strikes)
        wanted = sorted(set(call_window) | (set(strikes) & set(listed)))
        return _widen(make_call_chain(spot=100.0, days=7, strikes=wanted, quote_spread=0.01))

    monkeypatch.setattr(spread_data, "expirations", lambda t: ["2026-10-21", "2026-10-23", "2026-10-30"])
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: listed)
    monkeypatch.setattr(spread_data, "fetch_puts", fetch_puts)
    monkeypatch.setattr(spread_data, "fetch_calls", fetch_calls)
    ledger = tmp_path / "ledger"
    assert condor_sim.main(["--ledger-dir", str(ledger), "suggest", "ZZZQ", "--width", "5"],
                            today=SNAP) == 0
    return short_put, _texts(ledger), asked


@pytest.mark.data_source("yahoo")
def test_condor_sim_cli_yahoo_uses_wide_short_leg_with_warning(monkeypatch, tmp_path):
    short_put, text, asked = _condor_sim(monkeypatch, tmp_path)
    assert asked  # wings outside the window were requested for the wide-but-allowed shorts
    assert WARN_77 in text and f'"put_short_strike": {short_put},' in text
    assert '"status": "ok"' in text  # the wings outside the window were requested and found


def test_condor_sim_cli_ibkr_rejects_wide_short_leg(monkeypatch, tmp_path):
    short_put, text, asked = _condor_sim(monkeypatch, tmp_path)
    assert asked == []  # every short is rejected at 5%, so no wing is worth requesting
    assert WARN_TEXT not in text and f'"put_short_strike": {short_put},' not in text
    assert "买卖价差" in text and "超过 5%" in text


# ---- bullcall-sim (the short leg strike request uses the hard limit) ------------------

def _bullcall_row(k, pct=0.0):
    import math
    mid = {100.0: 3.20, 105.0: 1.30}.get(k, round(3.20 * math.exp(-0.18 * (k - 100)), 4))
    half = max(0.01, mid * pct / 2)
    return {"strike": k, "bid": round(mid - half, 4), "ask": round(mid + half, 4), "last": mid,
            "iv": None, "delta": round(0.50 - (k - 100) / 30, 4)}


def _bullcall_sim(monkeypatch, tmp_path):
    _common(monkeypatch)
    listed = [float(k) for k in range(90, 116)]
    window = [float(k) for k in range(95, 107)]   # the 0.40 bucket's short leg (108) is outside
    long_strike = 103.0                            # delta 0.40 bucket; its quote is 7.7% wide

    asked: list[float] = []

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        asked.extend(strikes)
        wanted = sorted(set(window) | (set(strikes) & set(listed)))
        return [_bullcall_row(k, PCT if k == long_strike else 0.0) for k in wanted]

    monkeypatch.setattr(spread_data, "expirations",
                        lambda t: ["2026-10-23", "2026-11-13", "2026-11-20", "2026-12-18"])
    monkeypatch.setattr(spread_data, "fetch_calls", fetch_calls)
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: listed)  # the ITM long-leg ask
    ledger = tmp_path / "ledger"
    assert bullcall_sim.main(["--ledger-dir", str(ledger), "suggest", "ZZZQ"], today=SNAP) == 0
    return long_strike, _texts(ledger), asked


@pytest.mark.data_source("yahoo")
def test_bullcall_sim_cli_yahoo_uses_wide_long_leg_and_fetches_short(monkeypatch, tmp_path):
    long_strike, text, asked = _bullcall_sim(monkeypatch, tmp_path)
    assert WARN_77 in text and f'"long_call_strike": {long_strike},' in text
    assert 108.0 in asked  # the wide-but-allowed long leg's short strike was requested


def test_bullcall_sim_cli_ibkr_rejects_wide_long_leg(monkeypatch, tmp_path):
    long_strike, text, asked = _bullcall_sim(monkeypatch, tmp_path)
    assert WARN_TEXT not in text and f'"long_call_strike": {long_strike},' not in text
    assert 108.0 not in asked  # a rejected long leg needs no short strike (kills a hard-wired 10%)
    assert "买卖价差 7.7% 超过 5%" in text

