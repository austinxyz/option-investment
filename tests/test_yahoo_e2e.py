"""Yahoo end to end through the real CLIs (share plan Task 6, spec 10.3).

Only yfinance is faked (FakeYF); the wall clock and the throttle are replaced and every socket
connect is forbidden (OPTION_TESTS_NO_NETWORK=1), so a stray real request fails loudly.
Tickers are invented. Timeline: Friday 2026-10-23 is the snapshot day, the screen runs on
Saturday the 24th, the weekly review on Monday the 26th, the put expires Friday 10-30 and is
settled by `status` on Saturday 10-31.
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import put_journal
import put_review
import put_screen
import spread_data
import yahoo_data
from fake_yfinance import FakeYF, Scene, chain_frame, daily, install, utc
from spread_fixtures import bs_put

pytestmark = pytest.mark.data_source("yahoo")

SNAP = date(2026, 10, 23)
SATURDAY = date(2026, 10, 24)
MONDAY = date(2026, 10, 26)
EXPIRY = date(2026, 10, 30)
AFTER_EXPIRY = date(2026, 10, 31)
EXPIRIES = ("2026-10-30", "2026-11-06")
SETTLE_CLOSE = 94.0           # below the short strike: assigned, so the fake close decides pnl
BANNER = ("> ⚠️ 数据来源：Yahoo Finance（延迟约 15 分钟，非券商报价）。数据质量：低。Delta 为本地 "
          "Black-Scholes 估算；休市时用最后成交价（标\"陈旧\"）；买卖价差门槛放宽到 10%。"
          "按中间价成交、不含手续费，模拟结果偏乐观。")
INTERNAL_JSON = ("spreads.json", "paper_shares.json", "journal-notes.json")


class ReadTimeout(Exception):
    """Same class name as the requests / curl_cffi timeout."""


class YFRateLimitError(Exception):
    """Same class name as yfinance's rate-limit error."""


def _bars() -> pd.DataFrame:
    """260 business days up to the snapshot, drifting 80 -> 100: spot is above MA50 and MA200."""
    days = pd.bdate_range(end=pd.Timestamp(SNAP), periods=260)
    return daily([(d.date(), 80.0 + 20.0 * i / 259) for i, d in enumerate(days)])


def _puts(days: int) -> pd.DataFrame:
    rows = []
    for k in range(70, 100):
        px = bs_put(100.0, float(k), 0.35, days)
        rows.append({"strike": float(k), "bid": max(px - 0.01, 0.01), "ask": px + 0.01,
                     "lastPrice": px, "impliedVolatility": 0.35,
                     "lastTradeDate": utc("2026-10-23 19:45")})
    return chain_frame(rows)


def healthy(**kw) -> Scene:
    chains = {EXPIRIES[0]: (_puts(7), chain_frame([])), EXPIRIES[1]: (_puts(14), chain_frame([]))}
    return Scene(options=EXPIRIES, chains=chains, history=_bars(), **kw)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A repo-shaped tmp root (wiki/options exists, so the public page is written too)."""
    monkeypatch.setenv("OPTION_TESTS_NO_NETWORK", "1")
    monkeypatch.setattr(yahoo_data, "_clock", lambda: 0.0)
    monkeypatch.setattr(yahoo_data, "_sleep", lambda s: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    (tmp_path / "wiki" / "options").mkdir(parents=True)
    monkeypatch.setattr(put_screen, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(put_screen, "WATCHLIST", tmp_path / "data" / "watchlist.txt")
    fake = install(monkeypatch, FakeYF({"SPY": Scene(history=_bars()), "ZZZQ": healthy()}))
    yahoo_data.reset_memo()
    yield {"root": tmp_path, "ledger": tmp_path / "data" / "paper-trading", "fake": fake}
    yahoo_data.reset_memo()


def _run(world, module, argv, today) -> int:
    """One CLI invocation as its own process would see it: nothing remembered from before."""
    yahoo_data.reset_memo()
    return module.main(["--ledger-dir", str(world["ledger"]), *argv], today=today)


def _first_paragraph(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0] == "---":
        lines = lines[lines.index("---", 1) + 1:]
    body = "\n".join(lines).strip("\n").split("\n", 1)[1].strip("\n")
    return body.split("\n\n")[0]


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}


def _assert_labelled(root: Path) -> set[str]:
    """Every md has the banner as first paragraph, every report json the source keys."""
    kinds = set()
    for path in sorted(root.rglob("*.md")):
        assert _first_paragraph(path.read_text(encoding="utf-8")) == BANNER, path
        kinds.add(path.parent.name if path.parent.name != "paper-trading" else path.name)
    for path in sorted(root.rglob("*.json")):
        if path.name in INTERNAL_JSON:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert (doc["data_source"], doc["data_quality"]) == ("Yahoo", "low"), path
        kinds.add(path.parent.name if path.parent.name != "paper-trading" else path.name)
    return kinds


def test_yahoo_e2e_suggest_open_status_review_journal(world, capsys):
    ledger, fake = world["ledger"], world["fake"]

    # 1. suggest from a bare --tickers (no wiki pages, no watchlist)
    assert _run(world, put_screen, ["suggest", "--tickers", "zzzq"], SATURDAY) == 0
    summary = json.loads((ledger / "put-screen" / f"{SNAP}.json").read_text(encoding="utf-8"))
    assert [c["ticker"] for c in summary["candidates"]] == ["ZZZQ"]
    assert (ledger / "suggestions" / f"{SNAP}-ZZZQ-put.md").is_file()

    # 2. open records the saved candidate and never fetches
    calls_before = len(fake.calls)
    assert _run(world, put_screen, ["open", "ZZZQ"], SATURDAY) == 0
    assert len(fake.calls) == calls_before
    led = json.loads((ledger / "spreads.json").read_text(encoding="utf-8"))
    record = led["spreads"][0]
    assert (record["ticker"], record["status"], record["data_source"]) == (
        "ZZZQ", "open", "Yahoo")
    assert date.fromisoformat(record["expiry"]) == EXPIRY
    strike = record["short_strike"]

    # 3. the weekly review while the put is open writes the snapshot the journal compares with
    assert _run(world, put_review, [], MONDAY) == 0
    assert (ledger / "reviews" / f"{MONDAY}.json").is_file()

    # 4. after expiry `status` settles at the fake close (the one bar is the expiry day's)
    settle = daily([(EXPIRY, SETTLE_CLOSE)])
    settle["Open"] = settle["Close"]
    fake.scenes["ZZZQ"] = Scene(history=settle)
    assert _run(world, put_screen, ["status"], AFTER_EXPIRY) == 0
    assert f"收盘 ${SETTLE_CLOSE:,.2f}" in capsys.readouterr().out
    settled = json.loads((ledger / "spreads.json").read_text(encoding="utf-8"))["spreads"][0]
    assert settled["status"] == "expired" and settled["exit_underlying"] == SETTLE_CLOSE
    expected = round((record["credit"] - (strike - SETTLE_CLOSE)) * 100 * record["contracts"], 2)
    assert settled["pnl"] == pytest.approx(expected, abs=0.02)

    # 5. the journal compares the Monday advice with the settlement
    assert _run(world, put_journal, [], AFTER_EXPIRY) == 0
    journal = json.loads((ledger / "journal.json").read_text(encoding="utf-8"))
    assert journal["counts"]["settled"] == 1

    # 6. one more review: nothing open any more, and it writes nothing new
    before = _tree(world["root"])
    assert _run(world, put_review, [], AFTER_EXPIRY) == 0
    assert _tree(world["root"]) == before

    kinds = _assert_labelled(world["root"] / "data")
    assert kinds >= {"suggestions", "put-screen", "reviews", "report.md", "journal.md",
                     "journal.json"}
    assert (world["root"] / "wiki" / "options" / "paper-trading.md").is_file()
    assert _first_paragraph((world["root"] / "wiki" / "options" / "paper-trading.md")
                            .read_text(encoding="utf-8")) == BANNER
    assert fake.network_calls()                        # everything went through the fake


# ---- spec 10.3: the link dies in the middle of a scan ------------------------------------

TICKERS = [f"ZZQ{c}" for c in "ABCDEFGHIJKL"]          # twelve; the last one times out


def _scan_world(world, last: Scene, swallow: bool = False, spy: Scene | None = None):
    scenes = {t: healthy() for t in TICKERS[:-1]}
    scenes[TICKERS[-1]] = last
    scenes["SPY"] = spy or Scene(history=_bars())
    install_fake = FakeYF(scenes, swallow=swallow)
    world["fake"] = install_fake
    return install_fake


def _first_scan(world, monkeypatch) -> dict[str, bytes]:
    """A successful scan of the first eleven, whose files must survive the failed one."""
    install(monkeypatch, FakeYF({t: healthy() for t in TICKERS[:-1]} | {
        "SPY": Scene(history=_bars())}))
    assert _run(world, put_screen, ["suggest", "--tickers", ",".join(TICKERS[:-1])],
                SATURDAY) == 0
    return _tree(world["ledger"])


DISCONNECTS = {
    "timeout": lambda: (Scene(history=_bars(), raises={1: ReadTimeout("Read timed out")}),
                        False, None),
    "rate_limit_twice": lambda: (Scene(history=_bars(), raises={
        1: YFRateLimitError("Too Many Requests"), 2: YFRateLimitError("Too Many Requests")}),
        False, None),
    "swallowed_by_yfinance": lambda: (
        Scene(history=_bars(), raises={1: ReadTimeout("Read timed out")}), True,
        Scene(history=_bars(), raises={2: ReadTimeout("Read timed out")})),
}


@pytest.mark.parametrize("kind", sorted(DISCONNECTS))
def test_yahoo_e2e_midway_disconnect_writes_nothing(kind, world, monkeypatch, capsys):
    old = _first_scan(world, monkeypatch)
    assert any(n.startswith("put-screen") for n in old) and any(
        n.startswith("suggestions") for n in old)
    capsys.readouterr()
    last, swallow, spy = DISCONNECTS[kind]()
    fake = _scan_world(world, last, swallow, spy)
    install(monkeypatch, fake)

    code = _run(world, put_screen, ["suggest", "--tickers", ",".join(TICKERS)], SATURDAY)

    captured = capsys.readouterr()
    assert code != 0
    assert "连不上 Yahoo Finance" in captured.err
    assert _tree(world["ledger"]) == old               # no new file, no deleted or touched file
    assert fake.network_calls("history")                # the scan did reach the twelfth ticker
    assert {c[0] for c in fake.calls} >= set(TICKERS)
