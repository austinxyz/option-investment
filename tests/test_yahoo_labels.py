"""Data-quality labels on every Yahoo / demo output (share plan, Task 5).

The golden cases (tests/golden_ibkr_cases.py) stub every spread_data market wrapper, so the same
scripted market can be replayed under OPTION_DATA_SOURCE=yahoo and =demo: only the labels may
differ. Every md / json / console the CLIs write is then checked file by file. Demo needs no
Task 7 data here: the stubs answer the market calls and FakeYF stands in for yf_module().
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import golden_harness as H
import golden_ibkr_cases as C
import option_labels
import option_settings
import put_screen_rules
import spread_data
import strategy_pick_cli_stubs as pick_stubs
import strategy_pick_rules
import yahoo_data
from fake_yfinance import FakeYF, install

YAHOO = pytest.param("yahoo", marks=pytest.mark.data_source("yahoo"), id="yahoo")
DEMO = pytest.param("demo", marks=pytest.mark.data_source("demo"), id="demo")
SOURCES = (YAHOO, DEMO)
LABEL = {"yahoo": "Yahoo", "demo": "Demo"}
QUALITY = {"yahoo": "low", "demo": "demo"}
HEADER = {"yahoo": "数据源：Yahoo · 数据质量：低", "demo": "数据源：Demo · 数据质量：演示"}
BANNER = {
    "yahoo": "> ⚠️ 数据来源：Yahoo Finance（延迟约 15 分钟，非券商报价）。数据质量：低。Delta 为本地 "
             "Black-Scholes 估算；休市时用最后成交价（标\"陈旧\"）；买卖价差门槛放宽到 10%。"
             "按中间价成交、不含手续费，模拟结果偏乐观。",
    "demo": "> ⚠️ 演示数据（虚构），不代表任何真实行情。",
}
HINT = "休市中：Yahoo 的 bid/ask 常为 0、IV 不可靠，很多标的会被剔除；建议美东 9:30–16:00 运行"
# Ledger-internal state, not reports: no labels (records are checked on their own).
INTERNAL_JSON = ("spreads.json", "paper_shares.json", "journal-notes.json")
SETUP_INPUTS = ("wiki/tickers/",)   # written by the case setup, not by a CLI
SUGGEST_CASES = tuple(c.name for c in C.CASES if c.argv[:1] == ("suggest",))
_CACHE: dict[str, dict[str, dict[str, bytes]]] = {}


def _quiet_yahoo(mp) -> None:
    """The probe and yf_module answer without a network (demo's yf_module is a Task 7 stub)."""
    install(mp, FakeYF({}))
    mp.setattr(yahoo_data, "ensure_available", lambda: None)
    yahoo_data.reset_memo()


def _run_all(source: str, base: Path, capsys) -> dict[str, dict[str, bytes]]:
    """Every main golden case under `source` (cached: the market is scripted, runs repeat)."""
    if source not in _CACHE:
        runs = {}
        for case in C.CASES:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv(option_settings.ENV_VAR, source)
                _quiet_yahoo(mp)
                runs[case.name] = H.run_case(case, base / case.name, mp, capsys)
        _CACHE[source] = runs
    return _CACHE[source]


def _files(runs, suffix: str):
    for case, outputs in runs.items():
        for rel, data in outputs.items():
            if rel.endswith(suffix) and not rel.startswith(SETUP_INPUTS):
                yield case, rel, _text(data)

def _text(data: bytes) -> str:
    """Windows write_text writes CRLF; the checks are about content, not line endings."""
    return data.decode("utf-8").replace("\r\n", "\n")


def _first_paragraph_under_title(text: str) -> str:
    lines = text.split("\n")
    if lines[0] == "---":
        lines = lines[lines.index("---", 1) + 1:]
    lines = [*lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    assert lines[0].startswith("# "), lines[0]
    rest = "\n".join(lines[1:]).strip("\n")
    return rest.split("\n\n")[0]


def _stdout_first_line(console: bytes) -> str:
    return _text(console).split("--- stdout ---\n", 1)[1].split("\n", 1)[0]


@pytest.mark.parametrize("source", SOURCES)
def test_every_md_output_has_banner_first_paragraph(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    kinds = set()
    for case, rel, text in _files(runs, ".md"):
        assert _first_paragraph_under_title(text) == BANNER[source], (case, rel)
        parent, name = rel.split("/")[-2:]
        parent = "strategy-picks" if parent == "demo" else parent   # demo picks: own subfolder
        kinds.add(name if parent == "paper-trading" else parent)
    for case, rel, text in _files(runs, ".html"):
        assert f'</h1>\n<p class="warn">{BANNER[source][2:]}</p>' in text.replace(
            "&quot;", '"'), (case, rel)
        kinds.add(rel)
    # One entry per output type: candidate explanations, each summary, both ledger reports, the
    # public page, review snapshots, the journal and the strategy-pick report.
    assert kinds >= {"suggestions", "put-screen", "call-screen", "collar-screen", "condor-sim",
                     "bullcall-sim", "report.md", "data/paper-trading/report.html", "options",
                     "reviews", "journal.md", "strategy-picks"}, kinds


@pytest.mark.parametrize("source", SOURCES)
def test_every_json_has_source_and_quality(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    seen = set()
    for case, rel, text in _files(runs, ".json"):
        doc = json.loads(text)
        name = rel.rsplit("/", 1)[-1]
        if name == "spreads.json":
            for record in doc["spreads"]:
                assert (record["data_source"], record["data_quality"]) == (
                    LABEL[source], QUALITY[source]), (case, record["id"])
            continue
        if name in INTERNAL_JSON:
            continue
        assert doc["data_source"] == LABEL[source], (case, rel)
        assert doc["data_quality"] == QUALITY[source], (case, rel)
        kind = rel.split("/")[-2] if "/" in rel else rel
        seen.add("strategy-picks" if kind == "demo" else kind)
    assert seen >= {"suggestions", "put-screen", "call-screen", "collar-screen", "reviews",
                    "paper-trading", "strategy-picks"}, seen


def _renderer_docs():
    import bullcall_sim_render
    import call_screen_render
    import collar_screen_render
    import condor_sim_render
    import put_screen_render
    out = {}
    for mod in (put_screen_render, call_screen_render, collar_screen_render):
        out[mod.__name__] = [mod.candidate_to_json({}, "2026-10-23", False),
                             mod.summary_to_json([], [], "2026-10-23", "2026-10-23", False, {})]
    for mod in (condor_sim_render, bullcall_sim_render):
        out[mod.__name__] = [mod.candidate_to_json({}, "2026-10-23")]
    return out


@pytest.mark.data_source("yahoo")
@pytest.mark.parametrize("module", ["put_screen_render", "call_screen_render",
                                    "collar_screen_render", "condor_sim_render",
                                    "bullcall_sim_render"])
def test_each_renderer_json_data_source_is_yahoo(module):
    for doc in _renderer_docs()[module]:
        assert doc["data_source"] == "Yahoo"
        assert doc["data_quality"] == "low"


def test_put_screen_render_has_no_DATA_SOURCE_attr():
    import put_screen_render
    assert not hasattr(put_screen_render, "DATA_SOURCE")
    assert put_screen_render.source_line("2026-10-23", False) == (
        "数据源 IBKR 模拟网关 · 基于 2026-10-23 收盘（冻结行情）")


def _ledger(root: Path) -> list[dict]:
    path = H.ledger_dir(root) / "spreads.json"
    return json.loads(path.read_text(encoding="utf-8"))["spreads"]


@pytest.mark.data_source()
def test_ledger_record_carries_candidate_source_not_current_setting(tmp_path, monkeypatch):
    root = tmp_path / "root"
    H.ledger_dir(root).mkdir(parents=True)
    C.market(root, monkeypatch)
    _quiet_yahoo(monkeypatch)
    monkeypatch.setenv(option_settings.ENV_VAR, "yahoo")
    assert H.invoke("spread_sim", ("suggest", "ZZZQ"), root, C.SNAP) == 0
    assert H.invoke("condor_sim", ("suggest", "ZQIR"), root, C.SNAP) == 0
    monkeypatch.setenv(option_settings.ENV_VAR, "ibkr")
    assert H.invoke("spread_sim", ("open", "ZZZQ", "0.15"), root, C.SNAP) == 0
    assert H.invoke("put_screen", ("suggest",), root, C.SNAP) == 0
    monkeypatch.setenv(option_settings.ENV_VAR, "yahoo")
    assert H.invoke("put_screen", ("open", "ZZQB"), root, C.SNAP) == 0
    assert H.invoke("condor_sim", ("open", "ZQIR", "0.15"), root, C.SNAP) == 0
    by_ticker = {r["ticker"]: r for r in _ledger(root)}
    assert (by_ticker["ZZZQ"]["data_source"], by_ticker["ZZZQ"]["data_quality"]) == ("Yahoo", "low")
    assert (by_ticker["ZQIR"]["data_source"], by_ticker["ZQIR"]["data_quality"]) == ("Yahoo", "low")
    assert "data_source" not in by_ticker["ZZQB"] and "data_quality" not in by_ticker["ZZQB"]


# (module, setup steps, suggest argv, open argv, ticker) for every CLI that writes a record.
OPENERS = (
    ("spread_sim", (), ("suggest", "ZZZQ"), ("open", "ZZZQ", "0.15"), "ZZZQ"),
    ("put_screen", (), ("suggest",), ("open", "ZZQB"), "ZZQB"),
    ("call_screen", (("shares", "add", "ZQIC", "--shares", "200", "--cost", "95"),),
     ("suggest",), ("open", "ZQIC", "--lot", "1"), "ZQIC"),
    ("collar_screen", (), ("suggest",), ("open", "ZQCL", "90"), "ZQCL"),
    ("condor_sim", (), ("suggest", "ZQIR"), ("open", "ZQIR", "0.15"), "ZQIR"),
    ("bullcall_sim", (), ("suggest", "ZQBC"), ("open", "ZQBC", "0.50"), "ZQBC"),
)
SWITCHES = (("yahoo", "ibkr"), ("ibkr", "yahoo"), ("demo", "ibkr"), ("yahoo", "demo"))
CANDIDATE_KEYS = {"yahoo": ("Yahoo", "low"), "demo": ("Demo", "demo"), "ibkr": None}


@pytest.mark.data_source()
@pytest.mark.parametrize("suggest_src, open_src", SWITCHES, ids=[f"{a}-then-{b}" for a, b in SWITCHES])
@pytest.mark.parametrize("module, pre, suggest, open_argv, ticker", OPENERS,
                         ids=[o[0] for o in OPENERS])
def test_every_cli_records_candidate_source(module, pre, suggest, open_argv, ticker,
                                            suggest_src, open_src, tmp_path, monkeypatch):
    """Suggest under one source, open under another: the record keeps the candidate's source."""
    root = tmp_path / "root"
    H.ledger_dir(root).mkdir(parents=True)
    C.market(root, monkeypatch)
    _quiet_yahoo(monkeypatch)
    monkeypatch.setenv(option_settings.ENV_VAR, suggest_src)
    for argv in pre:
        assert H.invoke(module, argv, root, C.SNAP) == 0
    assert H.invoke(module, suggest, root, C.SNAP) == 0
    monkeypatch.setenv(option_settings.ENV_VAR, open_src)
    assert H.invoke(module, open_argv, root, C.SNAP) == 0
    (record,) = [r for r in _ledger(root) if r["ticker"] == ticker]
    expected = CANDIDATE_KEYS[suggest_src]
    if expected is None:
        assert "data_source" not in record and "data_quality" not in record
    else:
        assert (record["data_source"], record["data_quality"]) == expected


def test_ledger_report_mix_line_counts():
    ibkr = {"ticker": "ZZZQ"}
    yahoo = {"ticker": "ZZQB", "data_source": "Yahoo", "data_quality": "low"}
    demo = {"ticker": "ZQIR", "data_source": "Demo", "data_quality": "demo"}
    assert option_labels.ledger_mix_line([ibkr, ibkr]) is None
    assert option_labels.ledger_mix_line([]) is None
    line = option_labels.ledger_mix_line([ibkr, yahoo, yahoo])
    assert line.startswith("其中 2 笔按 Yahoo 数据开仓（数据质量低，结果偏乐观）")
    assert "·Y" in line
    both = option_labels.ledger_mix_line([yahoo, demo])
    assert "其中 1 笔按 Yahoo 数据开仓" in both and "其中 1 笔按演示数据开仓" in both
    tagged = option_labels.tag_records([ibkr, yahoo, demo])
    assert [r["ticker"] for r in tagged] == ["ZZZQ", "ZZQB·Y", "ZQIR·D"]
    assert yahoo["ticker"] == "ZZQB"  # copies, the ledger record is untouched


@pytest.mark.data_source()
def test_ledger_report_marks_rows_only_when_non_ibkr_records_exist(tmp_path, monkeypatch):
    root = tmp_path / "root"
    H.ledger_dir(root).mkdir(parents=True)
    C.market(root, monkeypatch)
    _quiet_yahoo(monkeypatch)
    monkeypatch.setenv(option_settings.ENV_VAR, "ibkr")
    assert H.invoke("spread_sim", ("suggest", "ZZZQ"), root, C.SNAP) == 0
    assert H.invoke("spread_sim", ("open", "ZZZQ", "0.15"), root, C.SNAP) == 0
    report = (H.ledger_dir(root) / "report.md").read_text(encoding="utf-8")
    assert "·Y" not in report and "其中" not in report
    monkeypatch.setenv(option_settings.ENV_VAR, "yahoo")
    assert H.invoke("condor_sim", ("suggest", "ZQIR"), root, C.SNAP) == 0
    monkeypatch.setenv(option_settings.ENV_VAR, "ibkr")
    assert H.invoke("condor_sim", ("open", "ZQIR", "0.15"), root, C.SNAP) == 0
    for name in ("report.md", "report.html"):
        text = (H.ledger_dir(root) / name).read_text(encoding="utf-8")
        assert "其中 1 笔按 Yahoo 数据开仓（数据质量低，结果偏乐观）" in text, name
        assert "ZQIR·Y" in text and "ZZZQ·Y" not in text, name
    assert H.invoke("spread_sim", ("status",), root, C.MIDWEEK) == 0  # marks -> public page
    public = (root / "wiki" / "options" / "paper-trading.md").read_text(encoding="utf-8")
    assert "·Y" not in public and "其中" not in public  # the condor is not public; the put is IBKR
    assert "ZQIR·Y" in (H.ledger_dir(root) / "report.md").read_text(encoding="utf-8")


@pytest.mark.data_source()
def test_journal_mixed_sources_note(tmp_path, monkeypatch):
    assert option_labels.journal_mix_line([{"ticker": "A"}]) is None
    mixed = [{"ticker": "A"}, {"ticker": "B", "data_source": "Yahoo", "data_quality": "low"}]
    assert option_labels.journal_mix_line(mixed) == "样本里混有不同数据源：IBKR 1 笔、Yahoo 1 笔"
    root = tmp_path / "root"
    H.ledger_dir(root).mkdir(parents=True)
    C.market(root, monkeypatch)
    _quiet_yahoo(monkeypatch)
    monkeypatch.setenv(option_settings.ENV_VAR, "ibkr")
    assert H.invoke("spread_sim", ("suggest", "ZZZQ"), root, C.SNAP) == 0
    assert H.invoke("spread_sim", ("open", "ZZZQ", "0.15"), root, C.SNAP) == 0
    monkeypatch.setenv(option_settings.ENV_VAR, "yahoo")
    assert H.invoke("condor_sim", ("suggest", "ZQIR"), root, C.SNAP) == 0
    assert H.invoke("condor_sim", ("open", "ZQIR", "0.15"), root, C.SNAP) == 0
    assert H.invoke("put_journal", (), root, C.SETTLE) == 0
    md = (H.ledger_dir(root) / "journal.md").read_text(encoding="utf-8")
    assert "> 样本里混有不同数据源：IBKR 1 笔、Yahoo 1 笔" in md
    assert _first_paragraph_under_title(md) == BANNER["yahoo"]


@pytest.mark.parametrize("source", SOURCES)
def test_console_first_line_header(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    for case, outputs in runs.items():
        assert _stdout_first_line(outputs[H.CONSOLE_KEY]) == HEADER[source], case


@pytest.mark.parametrize("source", SOURCES)
def test_market_closed_hint_yahoo_suggest_only(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    for case, outputs in runs.items():
        console = outputs[H.CONSOLE_KEY].decode("utf-8")
        expect = source == "yahoo" and case in SUGGEST_CASES
        assert (HINT in console) is expect, case
        if expect:
            assert console.split("--- stdout ---\n", 1)[1].split("\n")[1] == HINT, case
    for case, rel, text in _files(runs, ".md"):
        if rel.split("/")[-2] in ("put-screen", "call-screen", "collar-screen", "condor-sim",
                                   "bullcall-sim") and case in SUGGEST_CASES:
            assert (f"> {HINT}" in text) is (source == "yahoo"), (case, rel)


@pytest.mark.data_source("yahoo")
def test_market_closed_hint_absent_when_open(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    H.ledger_dir(root).mkdir(parents=True)
    C.intraday_market(root, monkeypatch)
    _quiet_yahoo(monkeypatch)
    assert H.invoke("put_screen", ("suggest",), root, C.SNAP) == 0
    assert HINT not in capsys.readouterr().out


SUGGESTS = (("spread_sim", ("suggest", "ZZZQ"), "suggestions/2026-10-23-ZZZQ.md", None),
            ("put_screen", ("suggest",), "put-screen/2026-10-23.md", None),
            ("call_screen", ("suggest",), "call-screen/2026-10-23.md", "shares"),
            ("collar_screen", ("suggest",), "collar-screen/2026-10-23.md", None),
            ("condor_sim", ("suggest", "ZQIR"), "condor-sim/2026-10-23.md", None),
            ("bullcall_sim", ("suggest", "ZQBC"), "bullcall-sim/2026-10-23.md", None))


@pytest.mark.data_source("yahoo")
@pytest.mark.parametrize("module, argv, summary, pre", SUGGESTS, ids=[s[0] for s in SUGGESTS])
def test_stale_drop_line_in_summary(module, argv, summary, pre, tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    H.ledger_dir(root).mkdir(parents=True)
    C.market(root, monkeypatch)
    _quiet_yahoo(monkeypatch)
    if pre:
        assert H.invoke("call_screen", ("shares", "add", "ZQIC", "--shares", "200", "--cost",
                                        "95"), root, C.SNAP) == 0
    monkeypatch.setattr(spread_data, "stale_drop_count", lambda t, e: 2)
    capsys.readouterr()
    assert H.invoke(module, argv, root, C.SNAP) == 0
    out = capsys.readouterr().out
    assert "档报价只有过期的最后成交价，已忽略" in out
    text = (H.ledger_dir(root) / summary).read_text(encoding="utf-8")
    line = next(x for x in out.split("\n") if "档报价只有过期" in x)
    # 2 per (ticker, expiry) the run priced: the count is a positive multiple of the stub's 2.
    count = int(line.split(" ")[0])
    assert count >= 2 and count % 2 == 0
    assert line == f"{count} 档报价只有过期的最后成交价，已忽略"
    assert f"> {line}" in text


@pytest.mark.parametrize("source", SOURCES)
def test_stale_drop_line_absent_without_drops(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    for case, outputs in runs.items():
        assert "已忽略" not in outputs[H.CONSOLE_KEY].decode("utf-8"), case


@pytest.mark.parametrize("source", SOURCES)
def test_rules_line_yahoo_text(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    checked = set()
    for case, rel, text in _files(runs, ".md"):
        if "规则：" not in text:
            continue
        checked.add(rel.split("/")[-2])
        yahoo_rule = put_screen_rules.RULES_LINE_SPREAD_YAHOO
        if source == "yahoo":
            assert yahoo_rule in text, (case, rel)  # Global Constraints text, verbatim
            assert "演示模式" not in text, (case, rel)
        else:
            assert yahoo_rule.replace("Yahoo 模式", "演示模式") in text, (case, rel)
            assert "Yahoo 模式放宽" not in text, (case, rel)
        for ibkr in ("价差 ≤5%", "价差 ≤ 5%", "价差都 ≤ 5%", "权利金的 5%", "硬卡 5%"):
            assert ibkr not in text, (case, rel, ibkr)
    assert {"put-screen", "call-screen", "collar-screen", "condor-sim", "bullcall-sim"} <= checked


@pytest.mark.data_source("yahoo")
def test_strategy_pick_yahoo_unavailable_text(tmp_path, monkeypatch, capsys):
    assert option_labels.source_unavailable_text() == "Yahoo Finance 不可用"
    pick_stubs.install(monkeypatch, tmp_path / strategy_pick_rules.OUTPUT_DIR,
                       option_snapshot=spread_data.SourceUnavailable("探测超时"))
    _quiet_yahoo(monkeypatch)
    assert pick_stubs.run(["ZZZQ"]) != 0
    err = capsys.readouterr().err
    assert "Yahoo Finance 不可用：探测超时" in err
    assert "IBKR" not in err and "网关" not in err


def test_source_unavailable_text_ibkr_unchanged():
    assert option_labels.source_unavailable_text() == "IBKR 网关不可用"


@pytest.mark.data_source("demo")
def test_source_unavailable_text_demo():
    assert option_labels.source_unavailable_text() == "演示数据不可用"


def test_ibkr_has_no_banner_no_new_keys(tmp_path, monkeypatch, capsys):
    assert option_labels.data_source_label() == "IBKR"
    assert option_labels.quality_banner() is None
    assert option_labels.quality_fields() == {}
    assert option_labels.labelled_fields() == {}
    assert option_labels.console_header() is None
    assert option_labels.with_banner("# T\n\nbody\n") == "# T\n\nbody\n"
    assert option_labels.with_html_banner("<h1>T</h1>\n<p>x</p>") == "<h1>T</h1>\n<p>x</p>"
    assert option_labels.record_fields({"data_source": "IBKR"}) == {}
    assert option_labels.market_closed_hint(lambda: False) is None
    for name in ("put_screen_suggest", "option_journal", "strategy_pick_base"):
        outputs = H.run_case(C.CASES_BY_NAME[name], tmp_path / name, monkeypatch, capsys)
        for rel, data in outputs.items():
            text = data.decode("utf-8")
            assert "⚠️ 数据来源" not in text and "演示数据" not in text, (name, rel)
            assert "data_quality\": \"low" not in text, (name, rel)
            assert "数据源：" not in text, (name, rel)


@pytest.mark.parametrize("source", SOURCES)
def test_no_hardcoded_ibkr_left_in_yahoo_outputs(source, tmp_path, capsys):
    runs = _run_all(source, tmp_path, capsys)
    allowed = "IBKR 模式的 5% 门槛"
    for case, outputs in runs.items():
        for rel, data in outputs.items():
            if rel.startswith(SETUP_INPUTS):
                continue
            text = data.decode("utf-8").replace(allowed, "")
            assert "IBKR 模拟网关" not in text, (case, rel)
            assert '"IBKR"' not in text, (case, rel)
            assert "IBKR" not in text, (case, rel)


# ---- the banner helper's edge cases ------------------------------------------------------------

@pytest.mark.data_source("demo")
@pytest.mark.parametrize("md, expected", [
    ("# T\n\nbody\n", "# T\n\n{b}\n\nbody\n"),
    ("# T\nbody", "# T\n\n{b}\n\nbody"),
    ("---\nsource: x\n---\n\n# T\n\nbody", "---\nsource: x\n---\n\n# T\n\n{b}\n\nbody"),
    ("plain\n", "{b}\n\nplain\n"),
    ("# T", "# T\n\n{b}\n"),
])
def test_with_banner_shapes(md, expected):
    assert option_labels.with_banner(md) == expected.format(b=BANNER["demo"])


@pytest.mark.data_source("yahoo")
def test_with_banner_notes_follow_banner():
    out = option_labels.with_banner("# T\n\nbody", ("n1", "n2"))
    assert out == f"# T\n\n{BANNER['yahoo']}\n\n> n1\n\n> n2\n\nbody"
