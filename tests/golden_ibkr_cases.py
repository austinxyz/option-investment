"""Golden cases for the IBKR byte-for-byte pins (share plan, Task 1).

One invented market (ZZZQ-family tickers only, nothing real): spot 100, IV 0.40, chains priced by
Black-Scholes through spread_fixtures and shaped like ibkr_chain rows (broker delta + und_price).
Every spread_data network wrapper, the gateway check and holdings.real_lots are stubbed, so no
case touches the network, data/positions.md or the real wiki.

Timeline (GOLDEN_TODAY = Fri 2026-10-23 is the snapshot day):
  10-23 suggest / open      10-27 status marks, early closes, option-review
  11-02 weekly expiries settled (spread, condor, CSP assigned, covered call), option-journal
  12-21 bull call spread settled after its 12-18 expiry
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

import holdings
import ibkr_data
import ibkr_session
import put_screen
import spread_core as core
import spread_data
from golden_harness import GOLDEN_TODAY, GoldenCase, invoke
from spread_fixtures import make_call_chain, make_chain
import strategy_pick_cli_stubs as pick_stubs
import strategy_pick_rules

SNAP = GOLDEN_TODAY
MIDWEEK = date(2026, 10, 27)
SETTLE = date(2026, 11, 2)
BULLCALL_SETTLE = date(2026, 12, 21)
EXPIRATIONS = ("2026-10-30", "2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27",
               "2026-12-18", "2027-01-15")
LISTED = tuple(float(k) for k in range(60, 141))
PUT_WINDOW = tuple(float(k) for k in range(70, 101))
CALL_WINDOW = tuple(float(k) for k in range(95, 131))
IV = 0.40
SPOT = 100.0
CLOSES = {"ZZZQ": 103.0, "ZQIR": 101.0, "ZZQB": 90.0, "ZQIC": 102.0, "ZQBC": 108.0,
          "ZQCL": 97.0}
WIKI_TICKERS = (("ZZQB", "Active"), ("ZQSP", "Watch"))
REAL_LOTS = (("ZQCL", 300.0, 88.0), ("ZQCC", 100.0, 92.0))

GATEWAY_DOWN = "IBKR 网关不可达：gw.test:14999"
NO_QUOTES = "没有拿到 {t} {e} 的期权报价"
BOUNDARY_FNS = ("last_close", "expirations", "chain_strikes", "fetch_puts", "fetch_calls")
REAL_SPREAD_DATA = {name: getattr(spread_data, name) for name in BOUNDARY_FNS}
REAL_ENSURE_GATEWAY = ibkr_session.ensure_gateway
# Boundary mode: when MODE["log"] is a list, spread_data runs for real and the fakes sit one
# level lower (ibkr_data), recording every call spread_data makes into the list.
MODE: dict = {"log": None}

# The ten option commands and the module each one drives (/paper-book = spread_sim status/close).
COMMAND_MODULES = {"spread-sim": "spread_sim", "put-screen": "put_screen",
                   "call-screen": "call_screen", "collar-screen": "collar_screen",
                   "condor-sim": "condor_sim", "bullcall-sim": "bullcall_sim",
                   "option-review": "put_review", "option-journal": "put_journal",
                   "strategy-pick": "strategy_pick", "paper-book": "spread_sim"}


def _days(expiry: str) -> int:
    return max((date.fromisoformat(expiry) - SNAP).days, 1)


def _ibkr_shape(rows: list[dict], side: str, spot: float, days: int, stale: bool) -> list[dict]:
    """ibkr_chain row shape; stale = weekend quotes (no bid/ask, price from the last close)."""
    delta = core.put_delta if side == "P" else core.call_delta
    out = []
    for r in rows:
        row = {**r, "delta": round(delta(spot, r["strike"], r["iv"], days), 4),
               "und_price": spot, "close": None}
        if stale:
            row.update(bid=None, ask=None, close=r["last"], from_close=True)
        out.append(row)
    return out


def _wanted(window, strikes) -> list[float]:
    return sorted(set(window) | (set(float(k) for k in strikes) & set(LISTED)))


def chain(m: dict, right: str, expiry: str, strikes=()) -> list[dict]:
    days = _days(expiry)
    if right == "P":
        rows = make_chain(spot=m["spot"], iv=IV, days=days, strikes=_wanted(PUT_WINDOW, strikes),
                          quote_spread=0.02)
    else:
        rows = make_call_chain(spot=m["spot"], iv=IV, days=days,
                               strikes=_wanted(CALL_WINDOW, strikes), quote_spread=0.02)
    return _ibkr_shape(rows, right, m["spot"], days, m["stale"])


def _fetchers(m: dict):
    def fetch_puts(t, e, strikes=(), window_pct=None, window_limit=None):
        return chain(m, "P", e, strikes)

    def fetch_calls(t, e, strikes=(), window_pct=None, window_limit=None):
        return chain(m, "C", e, strikes)

    return fetch_puts, fetch_calls


def _jsonable(value):
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _install_boundary(mp: MonkeyPatch, m: dict, log: list) -> None:
    """Real spread_data wrappers over a recording fake ibkr_data."""
    for name, fn in REAL_SPREAD_DATA.items():
        mp.setattr(spread_data, name, fn)

    def record(fn, *args, **kwargs):
        log.append({"fn": fn, "args": _jsonable(list(args)),
                    "kwargs": {k: _jsonable(v) for k, v in sorted(kwargs.items())}})

    def chain_rows(symbol, expiry, right, *args, **kwargs):
        record("chain_rows", symbol, expiry, right, *args, **kwargs)
        return chain(m, right, expiry, kwargs.get("strikes", args[0] if args else ()))

    def last_close(*args, **kwargs):
        record("last_close", *args, **kwargs)
        return SNAP, m["spot"]

    def expirations(*args, **kwargs):
        record("expirations", *args, **kwargs)
        return list(EXPIRATIONS)

    def chain_strikes(*args, **kwargs):
        record("chain_strikes", *args, **kwargs)
        return list(LISTED)

    for name, fn in (("chain_rows", chain_rows), ("last_close", last_close),
                     ("expirations", expirations), ("chain_strikes", chain_strikes)):
        mp.setattr(ibkr_data, name, fn)


def _real_lots() -> list:
    return [holdings.HeldLot(
        source="real", lot_id=None, ticker=t, shares=n, cost_basis=c, original_cost=c,
        reference_price=c, cost_estimated=False, accounts=1, label=f"{t}（真实）",
        has_taxable_account=False, earliest_entry_date="2025-03-03") for t, n, c in REAL_LOTS]


def _wiki(root: Path) -> Path:
    wiki = root / "wiki"
    (wiki / "options").mkdir(parents=True, exist_ok=True)
    (wiki / "sectors").mkdir(parents=True, exist_ok=True)
    for ticker, status in WIKI_TICKERS:
        d = wiki / "tickers" / ticker
        d.mkdir(parents=True, exist_ok=True)
        (d / "overview.md").write_text(f"**Status**: {status}\n", encoding="utf-8")
    return wiki


def market(root: Path, mp: MonkeyPatch, spot: float = SPOT, stale: bool = False,
           intraday: bool = False) -> None:
    """Base fake IBKR market + repo layout under root (wiki/options triggers the public page)."""
    m = {"spot": spot, "stale": stale}
    mp.setattr(put_screen, "WIKI_DIR", _wiki(root))
    mp.setattr(ibkr_session, "ensure_gateway", lambda: None)
    mp.setattr(holdings, "real_lots", lambda *a, **k: _real_lots())
    mp.setattr(spread_data, "market_open_now", lambda: intraday)
    mp.setattr(spread_data, "moving_averages", lambda t: {
        "snapshot_date": SNAP, "close": m["spot"], "ma50": 95.0, "ma200": 90.0})
    mp.setattr(spread_data, "month_return_pct", lambda t: 3.0)
    mp.setattr(spread_data, "earnings_status", lambda t, s, e: "yes" if t == "ZQSP" else "no")
    mp.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: None)
    mp.setattr(spread_data, "close_on", lambda t, d: CLOSES.get(t))
    mp.setattr(spread_data, "monday_gap_pct", lambda t, s, c: 0.8)
    if MODE["log"] is not None:
        _install_boundary(mp, m, MODE["log"])
        return
    mp.setattr(spread_data, "last_close", lambda t: (SNAP, m["spot"]))
    mp.setattr(spread_data, "expirations", lambda t: list(EXPIRATIONS))
    mp.setattr(spread_data, "chain_strikes", lambda t: list(LISTED))
    fetch_puts, fetch_calls = _fetchers(m)
    mp.setattr(spread_data, "fetch_puts", fetch_puts)
    mp.setattr(spread_data, "fetch_calls", fetch_calls)


def _raiser(exc: BaseException):
    def fn(*args, **kwargs):
        raise exc
    return fn


def gateway_down(root: Path, mp: MonkeyPatch) -> None:
    """The gateway is unreachable: the check and every IBKR-backed wrapper raise."""
    market(root, mp)
    exc = ibkr_session.GatewayUnavailable(GATEWAY_DOWN)
    mp.setattr(ibkr_session, "ensure_gateway", _raiser(exc))
    for name in BOUNDARY_FNS:
        mp.setattr(spread_data, name, _raiser(exc))


def quotes_missing(root: Path, mp: MonkeyPatch) -> None:
    """Gateway fine, but no option quotes come back: status shows a per-record error line."""
    market(root, mp, spot=99.0)

    def missing(t, e, *args, **kwargs):
        raise spread_data.DataError(NO_QUOTES.format(t=t, e=e))

    mp.setattr(spread_data, "fetch_puts", missing)
    mp.setattr(spread_data, "fetch_calls", missing)


def one_ticker_fails(root: Path, mp: MonkeyPatch) -> None:
    market(root, mp)
    real = spread_data.moving_averages

    def mas(t):
        if t == "ZQSP":
            raise spread_data.DataError("网络挂了")
        return real(t)

    mp.setattr(spread_data, "moving_averages", mas)


def settings_missing(root: Path, mp: MonkeyPatch) -> None:
    """The real gateway check with no IBKR_HOST anywhere: SettingsError before any socket."""
    market(root, mp)
    mp.setattr(ibkr_session, "ensure_gateway", REAL_ENSURE_GATEWAY)
    mp.setattr(ibkr_session, "ENV_PATH", root / "data" / "ibkr.env")
    mp.delenv("IBKR_HOST", raising=False)
    mp.setitem(ibkr_session._state, "ib", None)
    mp.setitem(ibkr_session._state, "dead", False)


def stale_market(root: Path, mp: MonkeyPatch) -> None:
    market(root, mp, stale=True)


def intraday_market(root: Path, mp: MonkeyPatch) -> None:
    market(root, mp, intraday=True)


def pick_gateway_down(root: Path, mp: MonkeyPatch) -> None:
    pick_stubs.install(mp, root / strategy_pick_rules.OUTPUT_DIR,
                       option_snapshot=ibkr_session.GatewayUnavailable(GATEWAY_DOWN))


def moved(spot: float):
    def setup(root: Path, mp: MonkeyPatch) -> None:
        market(root, mp, spot=spot)
    return setup


def seed_book(root: Path, mp: MonkeyPatch) -> None:
    """Open one position per strategy family through the real CLIs (for review / journal)."""
    market(root, mp)
    steps = (("spread_sim", ("suggest", "ZZZQ")), ("spread_sim", ("open", "ZZZQ", "0.15")),
             ("condor_sim", ("suggest", "ZQIR")), ("condor_sim", ("open", "ZQIR", "0.15")),
             ("bullcall_sim", ("suggest", "ZQBC")), ("bullcall_sim", ("open", "ZQBC", "0.50")),
             ("put_screen", ("suggest",)), ("put_screen", ("open", "ZZQB")),
             ("call_screen", ("shares", "add", "ZQIC", "--shares", "200", "--cost", "95")),
             ("call_screen", ("suggest",)), ("call_screen", ("open", "ZQIC", "--lot", "1")),
             ("collar_screen", ("suggest",)), ("collar_screen", ("open", "ZQCL", "90")))
    for module, argv in steps:
        assert invoke(module, argv, root, SNAP) == 0, (module, argv)


def settle_book(root: Path, mp: MonkeyPatch) -> None:
    market(root, mp, spot=102.0)
    assert invoke("spread_sim", ("status",), root, SETTLE) == 0


def pick_setup(root: Path, mp: MonkeyPatch) -> None:
    pick_stubs.install(mp, root / strategy_pick_rules.OUTPUT_DIR)


def _case(name, module, argv, setup=market, depends_on=None, today=SNAP, ledger_arg=True,
          expect_exit=0):
    return GoldenCase(name=name, module=module, argv=tuple(argv), setup=setup,
                      depends_on=depends_on, today=today, ledger_arg=ledger_arg,
                      expect_exit=expect_exit)


CASES = (
    # spread-sim: bull put spread, full life cycle
    _case("spread_sim_suggest", "spread_sim", ("suggest", "ZZZQ")),
    _case("spread_sim_open", "spread_sim", ("open", "ZZZQ", "0.15", "--contracts", "2"),
          depends_on="spread_sim_suggest"),
    _case("spread_sim_status", "spread_sim", ("status",), moved(97.0), "spread_sim_open", MIDWEEK),
    _case("spread_sim_close", "spread_sim", ("close", "1"), moved(97.0), "spread_sim_open",
          MIDWEEK),
    _case("spread_sim_settle", "spread_sim", ("status",), depends_on="spread_sim_open",
          today=SETTLE),
    # put-screen: cash-secured puts over the wiki universe
    _case("put_screen_suggest", "put_screen", ("suggest",)),
    _case("put_screen_suggest_monthly", "put_screen", ("suggest", "--dte", "monthly")),
    _case("put_screen_open", "put_screen", ("open", "ZZQB"), depends_on="put_screen_suggest"),
    _case("put_screen_status", "put_screen", ("status",), moved(96.0), "put_screen_open",
          MIDWEEK),
    _case("put_screen_close", "put_screen", ("close", "1"), moved(96.0), "put_screen_open",
          MIDWEEK),
    # call-screen: covered calls on paper and (invented) real lots
    _case("call_screen_shares_add", "call_screen",
          ("shares", "add", "ZQIC", "--shares", "200", "--cost", "95")),
    _case("call_screen_shares_list", "call_screen", ("shares", "list"),
          depends_on="call_screen_shares_add"),
    _case("call_screen_suggest", "call_screen", ("suggest",), depends_on="call_screen_shares_add"),
    _case("call_screen_open", "call_screen", ("open", "ZQIC", "--lot", "1"),
          depends_on="call_screen_suggest"),
    _case("call_screen_status", "call_screen", ("status",), moved(104.0), "call_screen_open",
          MIDWEEK),
    # collar-screen: collar / protective put on an invented real lot
    _case("collar_screen_suggest", "collar_screen", ("suggest",)),
    _case("collar_screen_open", "collar_screen", ("open", "ZQCL", "90"),
          depends_on="collar_screen_suggest"),
    _case("collar_screen_status", "collar_screen", ("status",), moved(95.0),
          "collar_screen_open", MIDWEEK),
    # condor-sim: iron condor
    _case("condor_sim_suggest", "condor_sim", ("suggest", "ZQIR")),
    _case("condor_sim_open", "condor_sim", ("open", "ZQIR", "0.15"), depends_on="condor_sim_suggest"),
    _case("condor_sim_status", "condor_sim", ("status",), moved(101.0), "condor_sim_open", MIDWEEK),
    # bullcall-sim: suggest / open / status / close + one expiry settlement
    _case("bullcall_sim_suggest", "bullcall_sim", ("suggest", "ZQBC")),
    _case("bullcall_sim_open", "bullcall_sim", ("open", "ZQBC", "0.50"),
          depends_on="bullcall_sim_suggest"),
    _case("bullcall_sim_status", "bullcall_sim", ("status",), moved(104.0), "bullcall_sim_open",
          MIDWEEK),
    _case("bullcall_sim_close", "bullcall_sim", ("close", "1"), moved(104.0), "bullcall_sim_open",
          MIDWEEK),
    _case("bullcall_sim_settle", "bullcall_sim", ("status",), depends_on="bullcall_sim_open",
          today=BULLCALL_SETTLE),
    # option-review (put_review) and option-journal (put_journal) over a mixed book
    _case("option_review", "put_review", (), seed_book, today=MIDWEEK),
    _case("option_journal_note", "put_journal", ("note", "1", "按计划持有到期", "--kind", "reason"),
          moved(101.0), "option_review", MIDWEEK),
    _case("option_journal", "put_journal", (), settle_book, "option_journal_note", SETTLE),
    # paper-book (spread_sim status over every strategy) and the wheel sync after assignment
    _case("paper_book_status", "spread_sim", ("status",), moved(101.0), "option_review", MIDWEEK),
    _case("call_screen_shares_after_assign", "call_screen", ("shares", "list"), market,
          "option_journal", SETTLE),
    # strategy-pick: the design section 10 base example
    _case("strategy_pick_base", "strategy_pick", ("ZZZQ",), pick_setup, ledger_arg=False),
)

# Error paths, stale (weekend) quotes and the intraday branch. Exit codes are what the code does
# today; the full-root snapshot proves that nothing partial is written.
EDGE_CASES = (
    _case("err_spread_sim_suggest_gateway_down", "spread_sim", ("suggest", "ZZZQ"), gateway_down,
          expect_exit=1),
    _case("err_spread_sim_status_gateway_down", "spread_sim", ("status",), gateway_down,
          "spread_sim_open", MIDWEEK, expect_exit=1),
    _case("err_spread_sim_status_quotes_missing", "spread_sim", ("status",), quotes_missing,
          "spread_sim_open", MIDWEEK),
    _case("err_put_screen_suggest_gateway_down", "put_screen", ("suggest",), gateway_down,
          expect_exit=1),
    _case("err_put_screen_status_gateway_down", "put_screen", ("status",), gateway_down,
          "put_screen_open", MIDWEEK, expect_exit=1),
    _case("err_put_screen_suggest_one_ticker_fails", "put_screen", ("suggest",), one_ticker_fails),
    _case("err_condor_sim_suggest_gateway_down", "condor_sim", ("suggest", "ZQIR"), gateway_down,
          expect_exit=1),
    _case("err_condor_sim_status_gateway_down", "condor_sim", ("status",), gateway_down,
          "condor_sim_open", MIDWEEK, expect_exit=1),
    _case("err_condor_sim_suggest_settings_missing", "condor_sim", ("suggest", "ZQIR"),
          settings_missing, expect_exit=1),
    _case("err_bullcall_sim_suggest_gateway_down", "bullcall_sim", ("suggest", "ZQBC"),
          gateway_down, expect_exit=1),
    _case("err_bullcall_sim_status_gateway_down", "bullcall_sim", ("status",), gateway_down,
          "bullcall_sim_open", MIDWEEK, expect_exit=1),
    _case("err_bullcall_sim_status_quotes_missing", "bullcall_sim", ("status",), quotes_missing,
          "bullcall_sim_open", MIDWEEK),
    _case("err_bullcall_sim_suggest_settings_missing", "bullcall_sim", ("suggest", "ZQBC"),
          settings_missing, expect_exit=1),
    _case("err_strategy_pick_gateway_down", "strategy_pick", ("ZZZQ",), pick_gateway_down,
          ledger_arg=False, expect_exit=1),
    _case("stale_spread_sim_suggest", "spread_sim", ("suggest", "ZZZQ"), stale_market),
    _case("stale_put_screen_suggest", "put_screen", ("suggest",), stale_market),
    _case("stale_call_screen_suggest", "call_screen", ("suggest",), stale_market,
          "call_screen_shares_add"),
    _case("stale_collar_screen_suggest", "collar_screen", ("suggest",), stale_market),
    _case("stale_condor_sim_suggest", "condor_sim", ("suggest", "ZQIR"), stale_market),
    _case("stale_bullcall_sim_suggest", "bullcall_sim", ("suggest", "ZQBC"), stale_market),
    _case("intraday_spread_sim_suggest", "spread_sim", ("suggest", "ZZZQ"), intraday_market),
    _case("intraday_put_screen_suggest", "put_screen", ("suggest",), intraday_market),
)
ALL_CASES = CASES + EDGE_CASES
CASES_BY_NAME = {c.name: c for c in ALL_CASES}
assert len(CASES_BY_NAME) == len(ALL_CASES), "duplicate golden case names"
# Boundary family: every main case that reaches ibkr_data (strategy-pick stubs its own adapters).
BOUNDARY_CASES = tuple(c for c in CASES if c.module != "strategy_pick")


def modules_covered() -> set[str]:
    return {c.module for c in CASES}
