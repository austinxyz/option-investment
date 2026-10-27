"""Where /put-screen gets its tickers (share plan Task 6, spec 7.6).

Order: --tickers > a wiki with any overview.md > data/watchlist.txt > demo tickers > nothing
(exit 2, no file written). All tickers are invented. The watchlist is written by hand on
Windows, so the parser is fed what Notepad really produces: BOM, full-width spaces, lower
case, repeats, comments, blank lines, even UTF-16.
"""
import argparse
from datetime import date
from pathlib import Path

import pytest

import golden_harness as H
import golden_ibkr_cases as C
import option_settings
import put_screen
import put_screen_wiki as pw

NOTHING = "没有可筛的标的：用 --tickers，或照 examples/watchlist.txt 建 data/watchlist.txt"
TODAY = date(2026, 10, 18)


def _watchlist(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / "watchlist.txt"
    path.write_bytes(text.encode(encoding))
    return path


def _wiki_with(tmp_path: Path, *tickers: str) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "tickers").mkdir(parents=True)
    for t in tickers:
        (wiki / "tickers" / t).mkdir()
        (wiki / "tickers" / t / "overview.md").write_text("**Status**: Watch\n", encoding="utf-8")
    return wiki


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}


# ---- universe_from_tickers / universe_from_list -------------------------------------

def test_from_tickers_uppercases_dedups_and_defaults():
    rows = pw.universe_from_tickers(" zzzq, ZZQB ,zzzq,,ZZQC")
    assert [(r.ticker, r.status, r.tier, r.notes) for r in rows] == [
        ("ZZZQ", "watch", None, ()), ("ZZQB", "watch", None, ()), ("ZZQC", "watch", None, ())]


@pytest.mark.parametrize("bad", ["../etc", "ZZ/Q", "ZZ\\Q", "ZZ Q", "TOOLONGTICKER1", "ZZ$Q"])
def test_from_tickers_rejects_path_chars_and_junk(bad):
    with pytest.raises(ValueError):
        pw.universe_from_tickers(f"ZZZQ,{bad}")


def test_from_tickers_keeps_dots_and_hyphens_like_other_clis():
    assert [r.ticker for r in pw.universe_from_tickers("zzq.b,zzq-c")] == ["ZZQ.B", "ZZQ-C"]


def test_from_tickers_with_nothing_in_it_is_an_error():
    with pytest.raises(ValueError):
        pw.universe_from_tickers(" , ,")


def test_watchlist_bom_fullwidth_lowercase_duplicates_comments_blank(tmp_path):
    text = ("﻿# my list\r\n"
            "\r\n"
            "zzzq　🏦　watch\r\n"      # full-width spaces, lower case
            "   \r\n"
            "ZZQB # inline comment\r\n"
            "ＺＺＱＣ　avoid\r\n"          # full-width letters too
            "ZZZQ 🚀 avoid\r\n"                # duplicate: the first one wins
            "＃ full-width hash comment\r\n"
            "zzqd")                            # no trailing newline
    rows = pw.universe_from_list(_watchlist(tmp_path, text))
    assert [(r.ticker, r.status, r.tier) for r in rows] == [
        ("ZZZQ", "watch", "🏦"), ("ZZQB", "watch", None), ("ZZQC", "avoid", None),
        ("ZZQD", "watch", None)]


def test_watchlist_tier_and_status_parsing(tmp_path):
    text = ("ZZZQ 🏦\n"
            "ZZQB 🚀 under review\n"
            "ZZQC ⚡️ watch\n"            # emoji with a variation selector
            "ZZQD avoid\n"
            "ZZQE Under   Review\n"
            "ZZQF 🏦🚀\n"                      # two tiers: the first listed wins
            "ZZQG mystery\n"                   # unknown word: visible reject, not silent
            "ZZQH⚡\n")                        # emoji glued to the code
    rows = {r.ticker: (r.status, r.tier) for r in pw.universe_from_list(
        _watchlist(tmp_path, text))}
    assert rows == {
        "ZZZQ": ("watch", "🏦"), "ZZQB": ("under_review", "🚀"), "ZZQC": ("watch", "⚡"),
        "ZZQD": ("avoid", None), "ZZQE": ("under_review", None), "ZZQF": ("watch", "🏦"),
        "ZZQG": ("unrecognized", None), "ZZQH": ("watch", "⚡")}


def test_watchlist_utf16_from_notepad_is_read(tmp_path):
    rows = pw.universe_from_list(_watchlist(tmp_path, "﻿zzzq 🏦\nzzqb\n", "utf-16"))
    assert [(r.ticker, r.tier) for r in rows] == [("ZZZQ", "🏦"), ("ZZQB", None)]


def test_watchlist_bad_ticker_names_the_line(tmp_path):
    with pytest.raises(ValueError, match="第 3 行"):
        pw.universe_from_list(_watchlist(tmp_path, "ZZZQ\n# c\n../evil 🏦\n"))


def test_watchlist_empty_or_only_comments_is_empty(tmp_path):
    assert pw.universe_from_list(_watchlist(tmp_path, "﻿# nothing\n\n")) == []


# ---- resolve_universe ---------------------------------------------------------------

def _resolve(tmp_path, tickers=None, wiki=None, watch=None, source="yahoo"):
    return put_screen.resolve_universe(
        tickers, wiki or tmp_path / "no-wiki", watch or tmp_path / "no-watchlist.txt", source)


def test_tickers_flag_wins(tmp_path):
    wiki = _wiki_with(tmp_path, "AAA")
    watch = _watchlist(tmp_path, "WLST\n")
    rows = _resolve(tmp_path, tickers="zzzq", wiki=wiki, watch=watch)
    assert [r.ticker for r in rows] == ["ZZZQ"]


def test_wiki_beats_watchlist(tmp_path):
    wiki = _wiki_with(tmp_path, "AAA")
    rows = _resolve(tmp_path, wiki=wiki, watch=_watchlist(tmp_path, "WLST\n"))
    assert [r.ticker for r in rows] == ["AAA"]


def test_wiki_dir_with_only_gitkeep_falls_to_watchlist(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "tickers").mkdir(parents=True)
    (wiki / "tickers" / ".gitkeep").write_text("", encoding="utf-8")
    (wiki / "tickers" / "EMPTYDIR").mkdir()      # a folder without overview.md is not a page
    rows = _resolve(tmp_path, wiki=wiki, watch=_watchlist(tmp_path, "zzzq 🏦\n"))
    assert [(r.ticker, r.tier) for r in rows] == [("ZZZQ", "🏦")]


def test_missing_wiki_and_watchlist_gives_empty(tmp_path):
    assert _resolve(tmp_path) == []


def test_demo_uses_demo_tickers_after_watchlist(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("demo_market")
    fake.DEMO_TICKERS = ("ZZDA", "ZZDB")
    monkeypatch.setitem(__import__("sys").modules, "demo_market", fake)
    assert [r.ticker for r in _resolve(tmp_path, source="demo")] == ["ZZDA", "ZZDB"]
    rows = _resolve(tmp_path, source="demo", watch=_watchlist(tmp_path, "WLST\n"))
    assert [r.ticker for r in rows] == ["WLST"]
    assert _resolve(tmp_path, source="yahoo") == []          # demo list only in demo mode


# ---- CLI ----------------------------------------------------------------------------

def _no_market(monkeypatch):
    """Any market access is a failure: the 'nothing to screen' run must not need a source."""
    def boom(*a, **k):
        raise AssertionError("market data touched")
    for name in ("ensure_source", "last_close", "moving_averages", "expirations"):
        monkeypatch.setattr(put_screen.spread_data, name, boom)


@pytest.mark.data_source("yahoo")
def test_nothing_exit_2_no_files_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(put_screen, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(put_screen, "WATCHLIST", tmp_path / "data" / "watchlist.txt")
    _no_market(monkeypatch)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "old.txt").write_text("keep", encoding="utf-8")
    before = _snapshot(tmp_path)
    assert put_screen.main(["--ledger-dir", str(ledger), "suggest"], today=TODAY) == 2
    err = capsys.readouterr().err
    assert err.strip() == NOTHING
    assert _snapshot(tmp_path) == before


@pytest.mark.data_source("yahoo")
def test_empty_watchlist_file_is_still_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(put_screen, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(put_screen, "WATCHLIST", _watchlist(tmp_path, "# only comments\n"))
    _no_market(monkeypatch)
    assert put_screen.main(["--ledger-dir", str(tmp_path / "ledger"), "suggest"],
                           today=TODAY) == 2
    assert NOTHING in capsys.readouterr().err
    assert not (tmp_path / "ledger").exists()


def test_tickers_flag_validates_like_other_clis(capsys):
    with pytest.raises(SystemExit) as exc:
        put_screen.main(["suggest", "--tickers", "ZZZQ,../x"], today=TODAY)
    assert exc.value.code == 2
    assert "ticker" in capsys.readouterr().err


def test_bad_watchlist_line_is_a_clear_error_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(put_screen, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(put_screen, "WATCHLIST", _watchlist(tmp_path, "ZZZQ\n../x\n"))
    assert put_screen.main(["--ledger-dir", str(tmp_path / "ledger"), "suggest"],
                           today=TODAY) == 1
    assert "第 2 行" in capsys.readouterr().err


def test_wiki_present_output_identical(tmp_path, monkeypatch, capsys):
    """The golden put_screen_suggest case, with a stray watchlist lying around: unchanged."""
    if not (H.GOLDEN_DIR / "put_screen_suggest").is_dir():
        # The IBKR goldens guard this repository only and never ship with the share edition;
        # test_ibkr_pin.py fails loudly here if they ever go missing.
        pytest.skip("IBKR goldens are not part of the share edition")
    other = _watchlist(tmp_path, "WLST\n")
    with monkeypatch.context() as mp:
        mp.setattr(put_screen, "WATCHLIST", other)
        outputs = H.run_case(C.CASES_BY_NAME["put_screen_suggest"], tmp_path / "root", mp, capsys)
    H.pin("put_screen_suggest", outputs)
    assert not any(b"WLST" in data for data in outputs.values())


def test_tickers_flag_is_only_on_suggest():
    parser = put_screen.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["status", "--tickers", "ZZZQ"])
    assert isinstance(parser, argparse.ArgumentParser)


def test_list_ticker_pattern_matches_spread_sim():
    """Two copies of one rule (put_screen_wiki avoids importing spread_sim): keep them equal."""
    import spread_sim
    assert pw.LIST_TICKER.pattern == spread_sim.TICKER_PATTERN
