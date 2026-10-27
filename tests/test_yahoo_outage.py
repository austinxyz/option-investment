"""Yahoo outages that real yfinance hides (design 8.5, 10.3).

By default yfinance swallows connection errors and answers with empty data, so an empty reply
alone cannot tell "bad symbol" from "Yahoo is gone". The layer re-probes SPY on an empty reply:
probe down -> SourceUnavailable (the run aborts, nothing is written); probe up -> DataError
(that one ticker is rejected). The fake runs in swallow mode, like real yfinance.
"""
from datetime import date
from pathlib import Path

import pytest

import put_screen
import spread_data
import yahoo_data
from fake_yfinance import FakeYF, Scene, chain_frame, daily, install, utc
from spread_data import DataError, SourceUnavailable

pytestmark = pytest.mark.data_source("yahoo")

SNAP = date(2026, 10, 23)
TODAY = date(2026, 10, 25)
EXP = "2026-11-20"


class ConnectionError(Exception):  # noqa: A001 - mirrors requests / curl_cffi naming
    pass


DOWN = ConnectionError("Failed to perform, curl: (7) Could not connect to server")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    yahoo_data.reset_memo()
    monkeypatch.setattr(yahoo_data, "_clock", lambda: 0.0)
    monkeypatch.setattr(yahoo_data, "_sleep", lambda s: None)
    yield
    yahoo_data.reset_memo()


def bars():
    return daily([(date(2026, 10, d), 100.0) for d in (19, 20, 21, 22, 23)])


def healthy() -> Scene:
    puts = chain_frame([{"strike": float(k), "bid": 0.9, "ask": 1.0, "lastPrice": 0.95,
                         "impliedVolatility": 0.4, "lastTradeDate": utc("2026-10-23 19:00")}
                        for k in range(80, 100)])
    return Scene(options=(EXP,), chains={EXP: (puts, chain_frame([]))}, history=bars())


# --- unit level ----------------------------------------------------------------

@pytest.mark.parametrize("call", ["last_close", "expirations"])
def test_swallowed_outage_mid_run_is_source_unavailable(monkeypatch, call):
    zzzq = Scene(options=(EXP,), history=bars(), raises={1: DOWN})
    install(monkeypatch, FakeYF({"ZZZQ": zzzq, "SPY": Scene(history=bars(), raises={1: DOWN})},
                                swallow=True))
    with pytest.raises(SourceUnavailable) as err:
        getattr(yahoo_data, call)("ZZZQ")
    assert str(err.value).startswith("连不上 Yahoo Finance：")


def test_unknown_ticker_with_healthy_probe_stays_data_error(monkeypatch):
    fake = install(monkeypatch, FakeYF({"SPY": Scene(history=bars())}, swallow=True))
    with pytest.raises(DataError) as err:
        yahoo_data.last_close("ZZQX")
    assert not isinstance(err.value, SourceUnavailable)
    assert str(err.value) == "ZZQX 没有行情数据（代码是否正确？）"
    with pytest.raises(DataError) as err:
        yahoo_data.expirations("ZZQX")
    assert not isinstance(err.value, SourceUnavailable) and str(err.value) == "ZZQX 没有期权"
    assert [c[0] for c in fake.network_calls("history")] == ["ZZQX", "SPY", "SPY"]


def test_reprobe_is_not_cached(monkeypatch):
    fake = install(monkeypatch, FakeYF({"SPY": Scene(history=bars(), raises={2: DOWN})},
                                       swallow=True))
    with pytest.raises(DataError) as err:
        yahoo_data.last_close("ZZQX")
    assert not isinstance(err.value, SourceUnavailable)
    with pytest.raises(SourceUnavailable):
        yahoo_data.last_close("ZZQY")
    assert len([c for c in fake.calls if c[0] == "SPY"]) == 2


# --- end to end: put-screen suggest --------------------------------------------

def _wiki(tmp_path: Path, tickers) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "sectors").mkdir(parents=True)
    for t in tickers:
        d = wiki / "tickers" / t
        d.mkdir(parents=True)
        (d / "overview.md").write_text("**Status**: Watch\n", encoding="utf-8")
    return wiki


def _suggest(monkeypatch, tmp_path, scenes, tickers) -> tuple[int, Path]:
    install(monkeypatch, FakeYF(scenes, swallow=True))
    monkeypatch.setattr(put_screen, "WIKI_DIR", _wiki(tmp_path, tickers))
    monkeypatch.setattr(spread_data, "moving_averages", lambda t: {
        "snapshot_date": SNAP, "close": 100.0, "ma50": 95.0, "ma200": 90.0})
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    ledger = tmp_path / "ledger"
    code = put_screen.main(["--ledger-dir", str(ledger), "suggest"], today=TODAY)
    return code, ledger


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


def test_put_screen_outage_mid_run_aborts_and_writes_nothing(monkeypatch, tmp_path, capsys):
    # SPY call 1 is the start-up probe (healthy); call 2 is the re-probe after ZZQB's
    # history came back empty because yfinance swallowed the connection error.
    scenes = {"SPY": Scene(history=bars(), raises={2: DOWN}), "ZZZQ": healthy(),
              "ZZQB": Scene(options=(EXP,), history=bars(), raises={1: DOWN}),
              "ZQIC": healthy()}
    code, ledger = _suggest(monkeypatch, tmp_path, scenes, ["ZZZQ", "ZZQB", "ZQIC"])
    assert code != 0
    assert _files(ledger) == []
    assert "连不上 Yahoo Finance" in capsys.readouterr().err


def test_put_screen_unknown_ticker_with_healthy_probe_is_one_reject(monkeypatch, tmp_path):
    scenes = {"SPY": Scene(history=bars()), "ZZZQ": healthy()}
    code, ledger = _suggest(monkeypatch, tmp_path, scenes, ["ZZZQ", "ZZQX"])
    assert code == 0
    summaries = [p for p in _files(ledger / put_screen.SUMMARY_DIR) if p.suffix == ".md"]
    assert summaries
    text = summaries[0].read_text(encoding="utf-8")
    assert "ZZQX" in text and "没有行情数据" in text
