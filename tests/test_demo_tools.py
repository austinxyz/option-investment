"""The demo source through every tool: full candidates, labels, seed hint, clean refusals.

Each test runs the real CLIs against demo_market in a tmp repo (OPTION_DATA_SOURCE=demo,
OPTION_TESTS_NO_NETWORK=1). The date sweep is the proof that a newcomer gets distinct tiers on
any day they happen to try the demo, not only on the day the constants were tuned.
"""
import json
from collections import Counter
from datetime import date, timedelta

import pytest

import bullcall_sim
import call_screen
import collar_screen
import condor_sim
import demo_market as M
import holdings
import put_screen
import spread_data
import spread_sim
import yahoo_data
from fake_yfinance import FakeYF, Scene, install

pytestmark = pytest.mark.data_source("demo")

FIRST = date(2026, 10, 24)                       # a Saturday: the snapshot is Friday the 23rd
SWEEP_DATES = [FIRST + timedelta(days=7 * k) for k in range(20)]
KEYS = {"spread": ("short_strike", "long_strike"),
        "condor": ("put_short_strike", "call_short_strike"),
        "bullcall": ("long_call_strike", "short_call_strike")}
SUFFIX = {"spread": "", "condor": "-condor", "bullcall": "-bullcall"}
TOOLS = {"spread": spread_sim, "condor": condor_sim, "bullcall": bullcall_sim}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTION_DATA_SOURCE", "demo")
    monkeypatch.setenv("OPTION_TESTS_NO_NETWORK", "1")
    monkeypatch.delenv(M.ENV_TODAY, raising=False)
    monkeypatch.setattr(spread_sim, "REPO", tmp_path)
    monkeypatch.setattr(put_screen, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(put_screen, "WATCHLIST", tmp_path / "data" / "watchlist.txt")
    monkeypatch.setattr(M, "_state", {"today": None, "wall": None})
    monkeypatch.setattr(holdings, "real_lots", lambda: [])    # the dev repo's own positions
    yahoo_data.reset_memo()
    yield tmp_path / "data" / "paper-trading-demo"
    yahoo_data.reset_memo()


def _run(module, argv, day) -> int:
    M.set_today(day)
    yahoo_data.reset_memo()                      # every CLI call is its own process
    return module.main(argv, today=day)


def _doc(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_demo_gives_distinct_tiers_on_any_day(world, capsys):
    distinct: dict[str, Counter] = {name: Counter() for name in TOOLS}
    put_days = 0
    for day in SWEEP_DATES:
        snap = M.last_trading_day(day)
        assert _run(put_screen, ["suggest"], day) == 0
        put_days += bool(_doc(world / "put-screen" / f"{snap}.json")["candidates"])
        for name, module in TOOLS.items():
            for symbol in M.DEMO_TICKERS:
                assert _run(module, ["suggest", symbol], day) == 0
                doc = _doc(world / "suggestions" / f"{snap}-{symbol}{SUFFIX[name]}.json")
                ok = [c for c in doc["candidates"] if c.get("status", "ok") == "ok"]
                tiers = {tuple(c[k] for k in KEYS[name]) for c in ok}
                distinct[name][len(tiers)] += 1
                if name == "bullcall":     # 0.60 / 0.50 / 0.40 long calls: never a duplicate
                    assert len(tiers) == len(ok) == 3, (day, symbol)
    cells = len(SWEEP_DATES) * len(M.DEMO_TICKERS)
    capsys.readouterr()
    assert put_days >= 0.9 * len(SWEEP_DATES)
    # a $0.5-1 strike grid is finer than the gap between the 0.10 / 0.15 / 0.20 delta strikes,
    # so duplicates are the exception: always two tiers, three on most days
    assert min(distinct["spread"]) >= 2 and distinct["spread"][3] >= 0.6 * cells
    assert min(distinct["condor"]) >= 1 and distinct["condor"][3] >= 0.75 * cells
    assert distinct["bullcall"] == Counter({3: cells})


def test_demo_banner_and_json_labels_on_every_tool(world):
    for name, module in TOOLS.items():
        assert _run(module, ["suggest", "ZZZQ"], FIRST) == 0
        doc = _doc(world / "suggestions" / f"2026-10-23-ZZZQ{SUFFIX[name]}.json")
        assert (doc["data_source"], doc["data_quality"]) == ("Demo", "demo")


def _yahoo_scene(symbol: str, day: date) -> Scene:
    """The demo's own bars and chains served through the Yahoo-shaped fake (source = yahoo)."""
    t = M.Ticker(symbol, today=day)
    return Scene(options=t.options,
                 chains={e: (t.option_chain(e).puts, t.option_chain(e).calls) for e in t.options},
                 history=t.history(period="1y"), calendar=t.calendar, info=t.info)


@pytest.mark.data_source("yahoo")
def test_yahoo_bullcall_offers_the_itm_long_leg(world, monkeypatch):
    """The default Yahoo call window is out-of-the-money only; a 0.60 long call is in the money."""
    monkeypatch.setenv("OPTION_DATA_SOURCE", "yahoo")
    monkeypatch.setattr(yahoo_data, "_clock", lambda: 0.0)
    monkeypatch.setattr(yahoo_data, "_sleep", lambda s: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    fake = install(monkeypatch, FakeYF({"ZZZQ": _yahoo_scene("ZZZQ", FIRST),
                                        "SPY": _yahoo_scene("ZZZQ", FIRST)}))
    ledger = world.parent / "paper-trading"
    assert bullcall_sim.main(["--ledger-dir", str(ledger), "suggest", "ZZZQ"], today=FIRST) == 0
    doc = _doc(ledger / "suggestions" / "2026-10-23-ZZZQ-bullcall.json")
    ok = {c["delta_bucket"]: c for c in doc["candidates"] if c["status"] == "ok"}
    assert set(ok) == {0.6, 0.5, 0.4}
    assert ok[0.6]["long_call_strike"] < doc["spot"] < ok[0.5]["long_call_strike"]
    assert fake.network_calls("option_chain")


# ---- covered call / collar: the demo needs a hypothetical holding ---------------------

def test_demo_call_and_collar_say_how_to_seed_a_holding(world, capsys):
    for module in (call_screen, collar_screen):
        assert _run(module, ["suggest"], FIRST) == 0
        out = capsys.readouterr().out
        assert "演示模式：先 `call_screen.py shares add ZZZQ --shares 100 --cost 100`" in out
    assert not (world.parent / "positions.md").exists()


def test_demo_covered_call_and_collar_work_after_the_hint(world, capsys):
    assert _run(call_screen, ["shares", "add", "ZZZQ", "--shares", "100", "--cost", "100"],
                FIRST) == 0
    for module in (call_screen, collar_screen):
        assert _run(module, ["suggest"], FIRST) == 0
        out = capsys.readouterr().out
        assert "演示模式：先" not in out and "候选 1 个" in out
    assert (world / "suggestions" / "2026-10-23-ZZZQ-call-lot1.json").is_file()


def test_real_ticker_reject_reads_plainly_in_every_screen(world, capsys):
    assert _run(call_screen, ["shares", "add", "AAPL", "--shares", "100", "--cost", "100"],
                FIRST) == 0
    for module, folder in ((call_screen, "call-screen"), (collar_screen, "collar-screen")):
        assert _run(module, ["suggest"], FIRST) == 0
        reasons = [r["reason"] for r in _doc(next((world / folder).glob("*.json")))["rejects"]]
        assert reasons and all(r.startswith("取行情失败：演示数据只有虚构标的") for r in reasons)
        assert not any("DataError" in r for r in reasons)
    assert _run(put_screen, ["suggest", "--tickers", "AAPL"], FIRST) == 0
    reason = _doc(next((world / "put-screen").glob("*.json")))["rejects"][0]["reason"]
    assert reason.startswith("取行情失败：演示数据只有虚构标的") and "DataError" not in reason


def test_other_failures_keep_their_wording():
    plain = spread_data.fetch_failure_reason(spread_data.DataError("boom"))
    assert plain == "取行情失败：DataError: boom"
    assert spread_data.fetch_failure_reason(ValueError("x")) == "取行情失败：ValueError: x"
    assert spread_data.fetch_failure_reason(spread_data.DemoDataRefused("只有虚构")) == \
        "取行情失败：只有虚构"


def test_demo_ledger_never_writes_the_public_page(world):
    """The public wiki page belongs to the real ledger; the demo dir name keeps it out."""
    public = world.parent.parent / "wiki" / "options"
    public.mkdir(parents=True)
    assert _run(put_screen, ["suggest", "--tickers", "ZZZQ"], FIRST) == 0
    assert _run(put_screen, ["open", "ZZZQ"], FIRST) == 0
    assert _run(put_screen, ["status"], FIRST) == 0
    assert list(public.iterdir()) == []
    assert (world / "report.md").is_file()
