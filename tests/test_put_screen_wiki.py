import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import put_screen_wiki as w


@pytest.mark.parametrize("text,expected", [
    ("Watch — fundamentals confirmed", "watch"),
    ("🟡 Watch (Weakened)", "watch"),
    ("🟢 Active — x", "active"),
    ("Avoid（投机性，论点 Weakened）", "avoid"),
    ("⚠️ Under Review — x", "under_review"),
    ("Watch+ — Q1", "watch"),
    ("Hold — x", "unrecognized"),
    ("回避 — 投机", "avoid"),
    ("Use — core", "use"),
])
def test_parse_status_variants(text, expected):
    assert w.parse_status(text) == expected


def _page(path: Path, date: str | None, rows: list[str]) -> None:
    head = f"**Last updated**: {date}\n\n" if date else ""
    body = "| Ticker | Role | Tier |\n|---|---|---|\n" + "\n".join(rows) + "\n"
    path.write_text(head + body, encoding="utf-8")


def test_sector_tiers_pick_latest_page_and_flag_conflict(tmp_path):
    _page(tmp_path / "a.md", "2026-01-01", ["| MU | memory | 🏦 core |"])
    _page(tmp_path / "b.md", "2026-06-01", ["| **MU** | memory | 🚀 double |"])
    assert w.parse_sector_tiers(tmp_path)["MU"] == ("🚀", True)


def test_sector_tiers_agreeing_pages_no_conflict(tmp_path):
    _page(tmp_path / "a.md", "2026-01-01", ["| `MU` | m | ⚡ x |"])
    _page(tmp_path / "b.md", "2026-06-01", ["| MU | m | ⚡ y |"])
    assert w.parse_sector_tiers(tmp_path)["MU"] == ("⚡", False)


def test_sector_tiers_ignore_rows_without_ticker_or_emoji(tmp_path):
    _page(tmp_path / "a.md", "2026-01-01", [
        "| RKLB | $50 | -26% |",
        "| Name | desc | 🏦 x |",
        "| TOOLONG | d | 🏦 x |",
        "| CRM | 🏦 蓝筹 | x |",
    ])
    (tmp_path / "a.zh.md").write_text("| ZZ | d | 🚀 |\n", encoding="utf-8")
    assert w.parse_sector_tiers(tmp_path) == {"CRM": ("🏦", False)}


def _overview(wiki: Path, t: str) -> None:
    d = wiki / "tickers" / t
    d.mkdir(parents=True)
    (d / "overview.md").write_text(f"# {t}\r\n**Status**: Watch — x\r\n", encoding="utf-8")


def test_build_universe_unclassified_ticker_has_none_tier(tmp_path):
    _overview(tmp_path, "MU")
    _overview(tmp_path, "ZZZ")
    (tmp_path / "sectors").mkdir()
    _page(tmp_path / "sectors" / "m.md", "2026-01-01", ["| MU | m | 🏦 x |"])
    rows = w.build_universe(tmp_path)
    assert [r.ticker for r in rows] == ["MU", "ZZZ"]
    assert rows[0].tier == "🏦" and rows[0].status == "watch"
    assert rows[1].tier is None and rows[1].notes == ()


def test_build_universe_conflict_note(tmp_path):
    _overview(tmp_path, "MU")
    (tmp_path / "sectors").mkdir()
    _page(tmp_path / "sectors" / "a.md", "2026-01-01", ["| MU | m | 🏦 x |"])
    _page(tmp_path / "sectors" / "b.md", "2026-02-01", ["| MU | m | 🚀 x |"])
    assert "档位在多个赛道页中不一致" in w.build_universe(tmp_path)[0].notes


def test_build_universe_skips_zh_pages_and_missing_overview(tmp_path):
    _overview(tmp_path, "MU")
    (tmp_path / "tickers" / "EMPTY").mkdir()
    (tmp_path / "tickers" / "MU" / "overview.zh.md").write_text("**Status**: Avoid\n", encoding="utf-8")
    (tmp_path / "sectors").mkdir()
    rows = w.build_universe(tmp_path)
    assert [r.ticker for r in rows] == ["MU"] and rows[0].status == "watch"


def test_status_line_format_variants(tmp_path):
    for t, line in [("A", "> **Status**: Watch — x"), ("B", "**Status:** Active（x）")]:
        d = tmp_path / "tickers" / t
        d.mkdir(parents=True)
        (d / "overview.md").write_text(f"# {t}\n{line}\n", encoding="utf-8")
    rows = {r.ticker: r.status for r in w.build_universe(tmp_path)}
    assert rows == {"A": "watch", "B": "active"}
