"""CLI tests: `suggest` saves the reviewed candidates, `open` records exactly those.

Reuses the fake-market fixture and helpers from test_spread_sim_cli.py.
"""
from datetime import date
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from spread_fixtures import make_chain
from test_spread_sim_cli import market, run, ledger  # noqa: F401  (market is a fixture)

SUG = "suggestions"


def test_suggest_saves_markdown_and_json_and_still_writes_no_ledger(market, tmp_path, capsys):
    assert run(tmp_path, "suggest", "abc") == 0
    md = tmp_path / SUG / "2026-10-16-ABC.md"
    assert md.exists() and (tmp_path / SUG / "2026-10-16-ABC.json").exists()
    text = md.read_text(encoding="utf-8")
    assert "ABC" in text and "逐档讲解" in text and "盈亏平衡胜率" in text
    assert "spread_sim.py open ABC 0.15" in text
    assert "2026-10-16-ABC.md" in capsys.readouterr().out
    assert not (tmp_path / "spreads.json").exists()


def test_suggest_rerun_same_day_overwrites_the_files(market, tmp_path):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    market["earnings"] = "yes"
    run(tmp_path, "suggest", "ABC", "--width", "5")
    files = sorted(p.name for p in (tmp_path / SUG).iterdir())
    assert files == ["2026-10-16-ABC.json", "2026-10-16-ABC.md"]
    assert "到期前有财报" in (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")


def test_open_records_the_reviewed_suggestion_without_touching_the_market(market, tmp_path, capsys):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    market["fail"] = True  # any market call from here on would raise DataError
    market["puts"] = []
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.15") == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["snapshot_date"] == "2026-10-16" and s["delta_bucket"] == 0.15
    assert s["suggestion_file"] == "2026-10-16-ABC.md"
    assert "2026-10-16-ABC.md" in capsys.readouterr().out


def test_open_after_the_weekend_still_uses_the_fridays_suggestion(market, tmp_path):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    market["spot"] = 80.0  # Monday gapped hard; the saved candidate must not change
    market["puts"] = make_chain(spot=80.0)
    assert run(tmp_path, "open", "ABC", "0.10", today=date(2026, 10, 19)) == 0
    (s,) = ledger(tmp_path)["spreads"]
    assert s["snapshot_date"] == "2026-10-16" and s["underlying_price"] == 100.0


def test_open_without_a_saved_suggestion_is_refused(market, tmp_path, capsys):
    assert run(tmp_path, "open", "ABC", "0.10") == 1
    assert "先运行 suggest" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()


def test_open_with_an_expired_suggestion_is_refused(market, tmp_path, capsys):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.10", today=date(2026, 10, 23)) == 1
    assert "已过期" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()


def test_open_uses_the_latest_saved_suggestion_for_the_ticker(market, tmp_path):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    newest = (tmp_path / SUG / "2026-10-16-ABC.json").read_text(encoding="utf-8")
    older = newest.replace("2026-10-16", "2026-10-09").replace("2026-10-23", "2026-10-16")
    (tmp_path / SUG / "2026-10-09-ABC.json").write_text(older, encoding="utf-8")
    assert run(tmp_path, "open", "ABC", "0.10") == 0
    assert ledger(tmp_path)["spreads"][0]["snapshot_date"] == "2026-10-16"


def test_open_width_must_match_the_saved_suggestion(market, tmp_path, capsys):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.10", "--width", "10") == 1
    assert "宽度" in capsys.readouterr().err
    assert run(tmp_path, "open", "ABC", "0.10", "--width", "5") == 0


def test_unavailable_bucket_is_explained_in_the_file_and_open_refuses_it(market, tmp_path, capsys):
    market["puts"] = [r for r in market["puts"] if r["strike"] in (93.0, 95.0, 96.0)]
    run(tmp_path, "suggest", "ABC", "--width", "5")
    text = (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")
    assert "无合适合约" in text and "找不到合适的长腿" in text
    assert "spread_sim.py open ABC 0.10" not in text
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.10") == 1
    assert "没有合适合约" in capsys.readouterr().err


def test_corrupt_saved_suggestion_is_refused_and_not_overwritten(market, tmp_path, capsys):
    (tmp_path / SUG).mkdir()
    bad = tmp_path / SUG / "2026-10-16-ABC.json"
    bad.write_text("{oops", encoding="utf-8")
    assert run(tmp_path, "open", "ABC", "0.10") == 1
    assert "重新运行 suggest" in capsys.readouterr().err
    assert bad.read_text(encoding="utf-8") == "{oops"


def test_ticker_b_never_picks_up_brk_b_suggestion(market, tmp_path, capsys):
    assert run(tmp_path, "suggest", "BRK-B") == 0
    capsys.readouterr()
    assert run(tmp_path, "open", "B", "0.10") == 1
    assert "没有 B 的已保存候选" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()
    assert run(tmp_path, "open", "BRK-B", "0.10") == 0
    assert ledger(tmp_path)["spreads"][0]["ticker"] == "BRK-B"


def test_suggestion_file_whose_ticker_field_disagrees_is_treated_as_damaged(market, tmp_path, capsys):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    path = tmp_path / SUG / "2026-10-16-ABC.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"ticker": "ABC"', '"ticker": "XYZ"'), encoding="utf-8")
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.10") == 1
    assert "重新运行 suggest" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()


@pytest.mark.parametrize("damage", [
    lambda d: d.pop("spot"),
    lambda d: d.pop("earnings"),
    lambda d: d.__setitem__("earnings", "maybe"),
    lambda d: d.__setitem__("expiry", "not-a-date"),
    lambda d: d.__setitem__("snapshot_date", "garbage"),
    lambda d: d.__setitem__("candidates", "oops"),
    lambda d: d.__setitem__("candidates", []),
    lambda d: d["candidates"][0].pop("credit"),
    lambda d: d["candidates"][0].__setitem__("credit", "x"),
])
def test_valid_json_with_the_wrong_shape_gives_a_message_not_a_traceback(market, tmp_path, capsys, damage):
    import json
    run(tmp_path, "suggest", "ABC", "--width", "5")
    path = tmp_path / SUG / "2026-10-16-ABC.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    damage(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.10") == 1
    assert "重新运行 suggest" in capsys.readouterr().err
    assert not (tmp_path / "spreads.json").exists()


@pytest.mark.parametrize("ticker", ["../x", "AB/C", "A B", "", "WAYTOOLONGTICKER"])
def test_unsafe_tickers_are_rejected_before_any_file_or_market_access(market, tmp_path, ticker):
    market["fail"] = True
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "suggest", ticker)
    assert exc.value.code == 2
    assert not (tmp_path / SUG).exists()


# --- any trading day, 7-day cycle ----------------------------------------------------------------

def _run_on(monkeypatch, market, snapshot, expirations):
    """Re-point the fake market at a different snapshot day / expiry list."""
    import spread_data
    monkeypatch.setattr(spread_data, "last_close", lambda t: (snapshot, market["spot"]))
    monkeypatch.setattr(spread_data, "expirations", lambda t: expirations)


def test_suggest_on_a_wednesday_gets_an_exact_seven_day_expiry(market, tmp_path, monkeypatch):
    wed = date(2026, 10, 14)
    _run_on(monkeypatch, market, wed, ["2026-10-16", "2026-10-19", "2026-10-21", "2026-10-23"])
    assert run(tmp_path, "suggest", "ABC", "--width", "5", today=wed) == 0
    text = (tmp_path / SUG / "2026-10-14-ABC.md").read_text(encoding="utf-8")
    assert "2026-10-21（7 天）" in text and "非整 7 天" not in text
    assert run(tmp_path, "open", "ABC", "0.10", today=wed) == 0
    s = ledger(tmp_path)["spreads"][0]
    assert s["snapshot_date"] == "2026-10-14" and s["expiry"] == "2026-10-21"


def test_suggest_on_a_thursday_for_a_friday_only_name_says_it_is_not_exactly_seven_days(
        market, tmp_path, monkeypatch):
    thu = date(2026, 10, 15)
    _run_on(monkeypatch, market, thu, ["2026-10-16", "2026-10-23", "2026-10-30"])
    assert run(tmp_path, "suggest", "ABC", "--width", "5", today=thu) == 0
    text = (tmp_path / SUG / "2026-10-15-ABC.md").read_text(encoding="utf-8")
    assert "2026-10-23（8 天，非整 7 天" in text


def test_two_different_start_days_can_both_be_open_for_the_same_ticker(market, tmp_path, monkeypatch):
    wed = date(2026, 10, 14)
    _run_on(monkeypatch, market, wed, ["2026-10-16", "2026-10-21", "2026-10-23"])
    assert run(tmp_path, "suggest", "ABC", "--width", "5", today=wed) == 0
    assert run(tmp_path, "open", "ABC", "0.10", today=wed) == 0            # expires 10-21
    thu = date(2026, 10, 15)
    _run_on(monkeypatch, market, thu, ["2026-10-16", "2026-10-23", "2026-10-30"])
    assert run(tmp_path, "suggest", "ABC", "--width", "5", today=thu) == 0
    assert run(tmp_path, "open", "ABC", "0.10", today=thu) == 0            # expires 10-23: allowed
    assert [s["expiry"] for s in ledger(tmp_path)["spreads"]] == ["2026-10-21", "2026-10-23"]


def test_suggest_during_market_hours_flags_the_intraday_snapshot(market, tmp_path):
    market["open"] = True  # SNAP is the fake "today", so the last bar is today's partial one
    assert run(tmp_path, "suggest", "ABC", "--width", "5") == 0
    text = (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")
    assert "盘中快照" in text
    assert '"intraday": true' in (tmp_path / SUG / "2026-10-16-ABC.json").read_text(encoding="utf-8")


def test_suggest_after_hours_or_on_an_older_bar_is_not_flagged_intraday(market, tmp_path):
    market["open"] = False
    run(tmp_path, "suggest", "ABC", "--width", "5")
    assert "盘中快照" not in (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")
    market["open"] = True  # market is open now, but the last bar is Friday's, not today's
    run(tmp_path, "suggest", "ABC", "--width", "5", today=date(2026, 10, 19))
    assert "盘中快照" not in (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")


def test_suggest_and_open_messages_say_intraday_when_the_snapshot_is_intraday(market, tmp_path, capsys):
    market["open"] = True
    assert run(tmp_path, "suggest", "ABC", "--width", "5") == 0
    assert "盘中" in capsys.readouterr().out.splitlines()[0]
    assert run(tmp_path, "open", "ABC", "0.10") == 0
    out = capsys.readouterr().out
    assert "快照 2026-10-16 盘中" in out and "快照 2026-10-16 收盘" not in out


# --- recommended width ------------------------------------------------------------------------

def _doc(tmp_path, name="2026-10-16-ABC"):
    import json
    return json.loads((tmp_path / SUG / f"{name}.json").read_text(encoding="utf-8"))


def test_suggest_without_width_uses_the_recommended_one(market, tmp_path, capsys):
    # fake chain: spot 100, iv 0.40, 1-wide strikes -> em 5.54, 15% = 0.83 -> one strike step = $1
    assert run(tmp_path, "suggest", "ABC") == 0
    doc = _doc(tmp_path)
    assert doc["width"] == 1.0 and doc["width_info"]["source"] == "auto"
    assert doc["width_info"]["recommended"] == 1.0
    assert "宽度 $1（自动推荐）" in capsys.readouterr().out.splitlines()[0]
    text = (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")
    assert "## 为什么是这个宽度" in text and "自动推荐" in text


def test_suggest_with_explicit_width_keeps_it_and_shows_the_recommendation(market, tmp_path, capsys):
    assert run(tmp_path, "suggest", "ABC", "--width", "5") == 0
    doc = _doc(tmp_path)
    assert doc["width"] == 5.0 and doc["width_info"]["source"] == "manual"
    assert doc["width_info"]["recommended"] == 1.0
    assert "宽度 $5（你指定，推荐 $1）" in capsys.readouterr().out.splitlines()[0]
    assert "系统推荐 $1" in (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")


def test_suggest_falls_back_to_the_default_width_when_it_cannot_estimate(market, tmp_path, capsys):
    market["puts"] = [dict(r, iv=None) for r in market["puts"]]
    assert run(tmp_path, "suggest", "ABC") == 0
    doc = _doc(tmp_path)
    assert doc["width"] == 5.0 and doc["width_info"]["source"] == "fallback"
    assert "无法估算推荐宽度" in (tmp_path / SUG / "2026-10-16-ABC.md").read_text(encoding="utf-8")


def test_open_after_an_auto_width_suggestion_needs_no_width_argument(market, tmp_path):
    run(tmp_path, "suggest", "ABC")
    assert run(tmp_path, "open", "ABC", "0.10") == 0
    assert ledger(tmp_path)["spreads"][0]["width"] == 1.0


def test_open_width_check_still_compares_against_the_saved_auto_width(market, tmp_path, capsys):
    run(tmp_path, "suggest", "ABC")
    capsys.readouterr()
    assert run(tmp_path, "open", "ABC", "0.10", "--width", "5") == 1
    assert "宽度" in capsys.readouterr().err


def test_open_still_works_on_an_older_saved_file_without_margin_or_width_info(market, tmp_path):
    import json
    run(tmp_path, "suggest", "ABC", "--width", "5")
    path = tmp_path / SUG / "2026-10-16-ABC.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.pop("width_info", None)
    doc.pop("intraday", None)
    for c in doc["candidates"]:
        c.pop("margin", None)
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(tmp_path, "open", "ABC", "0.15") == 0
    assert ledger(tmp_path)["spreads"][0]["delta_bucket"] == 0.15


# --- friendly HTML report -----------------------------------------------------------------------

def test_status_writes_report_html_with_the_marks(market, tmp_path):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    run(tmp_path, "open", "ABC", "0.20")
    market["puts"] = make_chain(spot=90.0)
    market["spot"] = 90.0
    assert run(tmp_path, "status", today=date(2026, 10, 20)) == 0
    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "ABC" in page
    assert "浮盈亏" in page and "<svg" in page and "-$" in page


def test_open_and_close_also_refresh_report_html(market, tmp_path):
    run(tmp_path, "suggest", "ABC", "--width", "5")
    run(tmp_path, "open", "ABC", "0.20")
    assert "运行 status 查看" in (tmp_path / "report.html").read_text(encoding="utf-8")
    run(tmp_path, "close", "1")
    assert "还没有未平仓" in (tmp_path / "report.html").read_text(encoding="utf-8")
