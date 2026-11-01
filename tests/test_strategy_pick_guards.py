"""Safety net for /strategy-pick: advisory only, private output, invented tickers in the docs."""
import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import strategy_pick_text as T

MODULES = sorted((REPO_ROOT / "scripts").glob("strategy_pick*.py"))
COMMAND_DOC = REPO_ROOT / ".claude" / "commands" / "strategy-pick.md"
GUIDE = REPO_ROOT / "docs" / "options-skills-guide.md"
ORDER_API = re.compile(r"placeOrder|cancelOrder|reqGlobalCancel|whatIfOrder|MarketOrder|LimitOrder")
FORBIDDEN_IMPORTS = ("broker_orders", "spread_ledger", "paper_shares")
POSITION_WRITERS = ("positions.add(", "positions.update(", "positions.close(", "add_etf(",
                    "update_etf(", "_ensure_file_exists")
PUBLIC_DIRS = ("wiki/", "outputs/")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_names(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names += [a.name for a in node.names]
    return names


def _guide_section() -> str:
    text = _source(GUIDE)
    start = text.index("### /strategy-pick（先选策略）")
    end = text.index("\n## ", start)
    return text[start:end]


def test_modules_found():
    names = {m.name for m in MODULES}
    assert {"strategy_pick.py", "strategy_pick_tree.py", "strategy_pick_steps.py"} <= names


def test_no_order_api_in_strategy_pick_modules():
    for path in MODULES:
        assert not ORDER_API.search(_source(path)), path.name


def test_no_broker_or_ledger_imports():
    for path in MODULES:
        for name in _imported_names(path):
            assert not any(name == bad or name.startswith(bad + ".") or name.startswith(bad)
                           for bad in FORBIDDEN_IMPORTS), (path.name, name)


def test_no_position_writer_calls():
    for path in MODULES:
        text = _source(path)
        for call in POSITION_WRITERS:
            assert call not in text, (path.name, call)


def test_no_writes_to_public_dirs():
    # Mentions of wiki/ as a read path or in a link are fine; a write path is not. The writer
    # functions only ever target OUTPUT_DIR (end-to-end snapshot in test_strategy_pick_cli).
    write_calls = re.compile(r"(write_text|write_bytes|open\(|os\.replace|mkdir)")
    for path in MODULES:
        for lineno, line in enumerate(_source(path).splitlines(), 1):
            if write_calls.search(line) and any(d in line for d in PUBLIC_DIRS):
                raise AssertionError(f"{path.name}:{lineno} writes near a public dir: {line}")
    assert "wiki/" not in _source(REPO_ROOT / "scripts" / "strategy_pick_render.py").replace(
        "wiki/options/strategies", "")


def test_gitignore_has_strategy_picks():
    lines = [l.strip() for l in _source(REPO_ROOT / ".gitignore").splitlines()]
    assert "data/strategy-picks/" in lines


def test_why_not_pages_exist():
    strategies = REPO_ROOT / "wiki" / "options" / "strategies"
    assert T.STRATEGY_PAGES
    for key, page in T.STRATEGY_PAGES.items():
        assert (strategies / page).is_file(), (key, page)


def _ticker_args(text: str) -> set[str]:
    """Tickers written as arguments of the tool's commands."""
    found = set(re.findall(r"strategy_pick\.py\s+([A-Z0-9][A-Z0-9.\-]*)", text))
    found |= set(re.findall(r"(?:strategy-pick|spread-sim suggest|bullcall-sim suggest|"
                            r"condor-sim suggest|bearput-sim suggest|bearcall-sim suggest|"
                            r"stock-review)\s+([A-Z][A-Z0-9.\-]*)", text))
    return {t for t in found if t != "TICKER"}


def test_command_doc_uses_fictional_tickers_only():
    command, section = _source(COMMAND_DOC), _guide_section()
    for name, text in (("command", command), ("guide section", section)):
        assert "ZZZQ" in text, name
        assert _ticker_args(text) <= {"ZZZQ"}, (name, _ticker_args(text))
        assert not re.search(r"\$\s?\d", text), f"{name}: dollar amount"
        assert "Roth" not in text and "401k" not in text.lower(), name


def test_command_doc_covers_exit_codes_and_rules():
    text = _source(COMMAND_DOC)
    for needle in ("--view 看好", "--view 不看好", "退出码 3", "退出码 1", "10197", "docs/option-data-sources.md",
                   "权利金薄", "论点过期", "代理", "portfolio_share", "rank_by_class_only",
                   "非投资建议", "不替用户选"):
        assert needle in text, needle


def test_existing_strategy_guard_untouched():
    text = _source(REPO_ROOT / "tests" / "test_all_strategies_guard.py")
    match = re.search(r"^STRATEGIES = \(([^)]*)\)", text, re.M)
    keys = re.findall(r'"([a-z_]+)"', match.group(1))
    assert len(keys) == 11
    assert not any("pick" in k for k in keys)


_STRATEGY_DIR = REPO_ROOT / "wiki" / "options" / "strategies"


def _unsuitable(page: str) -> str:
    """The '**不适用**' block of a strategy page, up to the next bold label or heading."""
    lines = _source(_STRATEGY_DIR / page).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("**不适用**"))
    block = []
    for ln in lines[start + 1:]:
        if ln.startswith("**") or ln.startswith("#"):
            break
        block.append(ln)
    return "\n".join(block)


def test_why_not_sentences_only_cite_pages_that_say_it():
    gate = T.WHY_NOT[("cash_secured_put", "gate_failed")]
    assert "各策略页" not in gate
    assert "Bull Call、Bull Put、铁鹰三页" in gate
    for page in ("bull-call-debit-spread.md", "bull-put-credit-spread.md", "iron-condor.md"):
        assert "流动性一般" in _unsuitable(page), page
    for page in ("cash-secured-put.md", "covered-call.md", "protective-put-collar.md"):
        assert "5%" in _source(_STRATEGY_DIR / page), page
    iv_low = T.WHY_NOT[("covered_call", "iv_low_seller")]
    assert "策略页'不适用'：低 IV" not in iv_low
    assert "铁鹰、现金担保 Put 两页" in iv_low
    for page in ("iron-condor.md", "cash-secured-put.md"):
        assert "低 IV" in _unsuitable(page), page
    for page in ("cash-secured-put.md", "bull-put-credit-spread.md", "iron-condor.md"):
        assert "已知事件" in _unsuitable(page), page


def test_command_doc_mentions_diagram_local_only():
    text = _source(COMMAND_DOC)
    for needle in ("路径图", "file:///", "不上传"):
        assert needle in text, needle
    assert "不发布" in text
    # Artifact is only ever named in a prohibition, never as an instruction to publish.
    for line in (s for s in text.splitlines() if "Artifact" in s):
        assert "不要发布成 Artifact" in line, line


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_command_doc_no_longer_says_put_screen_takes_no_ticker():
    for name, text in (("command", _source(COMMAND_DOC)), ("guide", _source(GUIDE))):
        assert "带过去" in text, name
        for line in text.splitlines():
            if "/put-screen" in line and "不接 ticker" in line:
                # allowed only when the same line carves out /put-screen's --tickers
                assert "--tickers" in line and line.index("--tickers") < line.index("不接 ticker"), (name, line)
        assert "扫整个 wiki 列表、不接 ticker" not in text, name
        assert "--tickers" in text, name


def test_command_doc_relays_handoff_lines_and_limits():
    text = _source(COMMAND_DOC)
    for needle in ("下一步", "怎么带过去", "限制", "带过去：", "原样", "--tickers ZZZQ",
                   "Avoid", "复核中", "Delta 目标带不过去", "参考价位"):
        assert needle in text, needle


def test_put_and_call_screen_docs_mention_delta():
    for name in ("put-screen.md", "call-screen.md"):
        text = _source(REPO_ROOT / ".claude" / "commands" / name)
        assert "--delta" in text, name
        assert "0.05–0.40" in text, name
        assert "同一天换 `--delta` 重跑会覆盖" in text, name
    guide = _source(GUIDE)
    assert guide.count("--delta") >= 3
    assert "--tickers ZZZQ" in guide


# ---- risk reversal (/rr-sim) is never part of /strategy-pick -----------------------------
RR_WORDS = ("risk_reversal", "rr-sim", "rr_sim", "风险反转")


def test_strategy_pick_vocabulary_has_no_risk_reversal():
    import strategy_pick_rules as R
    pools = {"STRATEGIES": R.STRATEGIES, "FORBIDDEN_PICKS": tuple(R.FORBIDDEN_PICKS),
             "LEAF_TABLE": repr(R.LEAF_TABLE), "ALL_STRATEGY_KEYS": R.ALL_STRATEGY_KEYS,
             "STRATEGY_LABELS": repr(T.STRATEGY_LABELS)}
    for name, pool in pools.items():
        text = repr(pool)
        for word in RR_WORDS:
            assert word not in text, (name, word)
    for path in MODULES:
        for word in RR_WORDS:
            assert word not in _source(path), (path.name, word)


def test_strategy_pick_output_bytes_do_not_depend_on_the_rr_tool(monkeypatch, tmp_path):
    """Same fixed input, rr modules present vs import-blocked: JSON, Markdown and SVG are equal."""
    from strategy_pick_cli_stubs import install, outputs, run

    def one_run(out_dir, block_rr):
        if block_rr:
            for name in ("rr_sim", "rr_sim_rules", "rr_sim_render", "spread_report_rr"):
                monkeypatch.setitem(sys.modules, name, None)
        out_dir.mkdir()
        install(monkeypatch, out_dir)
        assert run(["ZZZQ"]) == 0
        md, js = outputs(out_dir)
        return md.read_bytes(), js.read_bytes(), (out_dir / md.name.replace(".md", ".svg")).read_bytes()

    import rr_sim  # noqa: F401  (the tool exists and is importable)
    with_rr = one_run(tmp_path / "with", block_rr=False)
    without_rr = one_run(tmp_path / "without", block_rr=True)
    assert with_rr == without_rr
    for blob in with_rr:
        text = blob.decode("utf-8")
        for word in RR_WORDS:
            assert word not in text, word


# ---- bearish strategies in /strategy-pick (2026-10-30 bearish design) ----------------------
# The two bear spreads are recommended (by name and next command, as text); the long put and
# every naked sale stay forbidden. The command modules are never imported (dependency runs
# command -> strategy_pick_frompick only), and the outputs stay byte-identical without them.
BEAR_SPREAD_KEYS = ("bear_call_spread", "bear_put_spread")
NEVER_PICKED = ("long_put", "naked_call", "naked_put", "long_call", "bearish")
# Module names: never imported and never named in strategy_pick*.py source.
BEARISH_MODULES = ("bearcall_sim", "bearcall_sim_rules", "bearcall_sim_render", "bearput_sim",
                   "bearput_sim_rules", "bearput_sim_render", "longput_sim", "longput_sim_rules",
                   "longput_sim_render", "spread_report_bearish")
BEARISH_MODULE_WORDS = ("bearcall_sim", "bearput_sim", "longput_sim", "spread_report_bearish")
# Ledger labels of the bearish tools: never in the labels or sentences of /strategy-pick.
LEDGER_LABELS = ("Call 贷方价差", "Put 借方价差")
LONG_PUT_WORD = "买入 Put"  # allowed in exactly one sentence: why the long put is not picked


def test_strategy_pick_recommends_bear_spreads_but_never_long_put_or_naked():
    import strategy_pick_rules as R
    for key in BEAR_SPREAD_KEYS:
        assert key in R.STRATEGIES and key in R.ALL_STRATEGY_KEYS, key
        assert key not in R.FORBIDDEN_PICKS, key
        assert T.STRATEGY_LABELS[key] and T.NEXT_COMMANDS[key].startswith("/"), key
    assert T.NEXT_COMMANDS["bear_put_spread"].startswith("/bearput-sim ")
    assert T.NEXT_COMMANDS["bear_call_spread"].startswith("/bearcall-sim ")
    for key in NEVER_PICKED:
        assert key in R.FORBIDDEN_PICKS, key
        assert key not in R.STRATEGIES, key
    for picks, _ in R.LEAF_TABLE.values():
        assert not {p.strategy for p in picks} & R.FORBIDDEN_PICKS
    assert not {k for k in R.STRATEGIES if k.startswith("naked")}


def test_strategy_pick_never_imports_or_names_the_bearish_modules():
    for path in MODULES:
        for name in _imported_names(path):
            assert not any(name == m or name.startswith(m + ".") for m in BEARISH_MODULES), (
                path.name, name)
        for word in BEARISH_MODULE_WORDS:
            assert word not in _source(path), (path.name, word)


def test_strategy_pick_text_has_no_bearish_ledger_labels():
    sentences = (list(T.STRATEGY_LABELS.values()) + list(T.WHY_NOT.values())
                 + list(T.WARN.values()) + list(T.NEXT_COMMAND_CAVEATS.values()))
    for text in sentences:
        assert not any(label in text for label in LEDGER_LABELS), text
    with_long_put = {k for k, s in T.WHY_NOT.items() if LONG_PUT_WORD in s}
    assert with_long_put == {("bearish", "never_bearish")}
    assert not any(LONG_PUT_WORD in s for s in list(T.STRATEGY_LABELS.values())
                   + list(T.WARN.values()) + list(T.NEXT_COMMAND_CAVEATS.values()))
    for path in MODULES:
        for label in LEDGER_LABELS:
            assert label not in _source(path), (path.name, label)


def _held_150():
    import strategy_pick_data as DATA
    return DATA.HoldingRead((DATA.HoldingRow(150.0, True, "active"),), None, False, False)


# Bull default input, plus the bear view not held and held (>= 100 shares): the bear runs are
# the ones that now name the bear spreads, so they are the ones that prove no command import.
BYTE_GUARD_CASES = {
    "bull": ((), {}),
    "bear_not_held": (("--view", "不看好"), {}),
    "bear_held": (("--view", "不看好"), {"read_holding": "held_150"}),
}


@pytest.mark.parametrize("case", sorted(BYTE_GUARD_CASES))
def test_strategy_pick_outputs_unchanged_by_bearish_commands(monkeypatch, tmp_path, case):
    """Same fixed input, bearish modules present vs import-blocked: JSON, Markdown and SVG equal."""
    from strategy_pick_cli_stubs import install, outputs, run
    extra_argv, over = BYTE_GUARD_CASES[case]
    over = {k: (_held_150() if v == "held_150" else v) for k, v in over.items()}

    def one_run(out_dir, block):
        if block:
            for name in BEARISH_MODULES:
                monkeypatch.setitem(sys.modules, name, None)
        out_dir.mkdir()
        install(monkeypatch, out_dir, **over)
        assert run(["ZZZQ", *extra_argv]) == 0
        md, js = outputs(out_dir)
        return md.read_bytes(), js.read_bytes(), (out_dir / md.name.replace(".md", ".svg")).read_bytes()

    import bearcall_sim, bearput_sim, longput_sim  # noqa: F401,E401  (the tools exist)
    with_tools = one_run(tmp_path / "with", block=False)
    without_tools = one_run(tmp_path / "without", block=True)
    assert with_tools == without_tools
    for blob in with_tools:
        text = blob.decode("utf-8")
        for word in BEARISH_MODULE_WORDS + LEDGER_LABELS:
            assert word not in text, word
    if case != "bull":
        md_text = with_tools[0].decode("utf-8")
        assert "/bearput-sim suggest ZZZQ" in md_text
        assert ("/bearcall-sim suggest ZZZQ" in md_text) == (case == "bear_not_held")


# ---- docs: the bear view recommends Bear Put / Bear Call (2026-10-30 bearish design) -------
BEAR_SIM_DOCS = {"bearput": REPO_ROOT / ".claude" / "commands" / "bearput-sim.md",
                 "bearcall": REPO_ROOT / ".claude" / "commands" / "bearcall-sim.md"}
LONGPUT_DOC = REPO_ROOT / ".claude" / "commands" / "longput-sim.md"
NOT_RECOMMENDED = ("不推荐", "不会推荐", "永不推荐")
BEAR_SPREAD_WORDS = ("bearcall", "bearput", "Bear Call", "Bear Put", "Call 贷方价差",
                     "Put 借方价差", "看跌价差", "三个看跌", "三种看跌")
# Pages about one bear spread: there, any "strategy-pick does not recommend" clause is about it.
BEAR_SPREAD_PAGES = (BEAR_SIM_DOCS["bearput"], BEAR_SIM_DOCS["bearcall"],
                     _STRATEGY_DIR / "bear-call-credit-spread.md",
                     _STRATEGY_DIR / "bear-put-debit-spread.md")
OLD_SPECS = ("2026-10-25-strategy-pick-design.md", "2026-10-27-bearish-and-new-strategies-design.md")
SHARE_DOCS = REPO_ROOT / "share" / "option-investment" / "files"


def _clauses(text: str) -> list[str]:
    return [c for c in re.split(r"[。；;|\n]", text) if c.strip()]


def _says_not_recommended(clause: str) -> bool:
    return "strategy-pick" in clause and any(w in clause for w in NOT_RECOMMENDED)


# Old bear-view wording ("no options / only reduce or Collar"), in any doc copy or chart.
STALE_BEAR_PHRASES = ("不出任何看涨策略", "不出看涨策略", "工具不做（用户偏好看涨与中性）",
                      "提示减仓 / Collar", "减仓，或用 Collar 锁下限", "只提示减仓或 Collar")


def _doc_files() -> list[Path]:
    files = sorted((REPO_ROOT / ".claude" / "commands").glob("*.md"))
    files += sorted((REPO_ROOT / "wiki" / "options").rglob("*.md"))
    files += sorted((REPO_ROOT / "docs").glob("*.md"))
    files += sorted((REPO_ROOT / "docs" / "charts").glob("*.svg"))
    files += sorted(SHARE_DOCS.glob("*.md"))
    return files


def _doc_text(path: Path) -> str:
    """Markdown as is; an SVG chart as its <text> contents, one per line."""
    text = _source(path)
    if path.suffix == ".svg":
        return "\n".join(re.findall(r"<text[^>]*>([^<]*)</text>", text))
    return text


def _source_or_skip(path: Path) -> str:
    """Repo-only files (design specs, the public wiki guide, share/ sources) are not shipped in
    the share edition, where the test skips; in this repo they exist and are fully checked."""
    if not path.is_file():
        pytest.skip(f"{path.relative_to(REPO_ROOT).as_posix()} is not shipped in this copy")
    return _source(path)


def test_no_doc_says_strategy_pick_skips_the_bear_spreads():
    files = _doc_files()
    assert GUIDE in files
    for path in files:
        text = _doc_text(path)
        for phrase in STALE_BEAR_PHRASES:
            assert phrase not in text, (path.name, phrase)
        for clause in _clauses(text):
            if not _says_not_recommended(clause):
                continue
            assert path not in BEAR_SPREAD_PAGES, (path.name, clause)
            assert not any(w in clause for w in BEAR_SPREAD_WORDS), (path.name, clause)
    for tool in ("/bearcall-sim", "/bearput-sim"):
        start = _source(GUIDE).index(f"### {tool}")
        section = _source(GUIDE)[start:_source(GUIDE).index("\n### ", start + 1)]
        assert not any(_says_not_recommended(c) for c in _clauses(section)), tool
        assert "--from-pick" in section, tool


def test_long_put_stays_manual_only():
    assert any(_says_not_recommended(c) for c in _clauses(_source(LONGPUT_DOC)))
    assert "/longput-sim" in _source(COMMAND_DOC) and "/longput-sim" in _guide_section()


def test_bear_sim_docs_document_from_pick():
    for key, path in BEAR_SIM_DOCS.items():
        text = _source(path)
        assert "open TICKER --from-pick" in text, key
        assert f"scripts/{key}_sim.py open TICKER --from-pick" in text, key
        assert "按 /strategy-pick 的推荐开 ZZZQ" in text, key
        assert "退出码 2，什么都不写" in text, key


def test_command_doc_bear_view_rows():
    text = _source(COMMAND_DOC)
    for needle in ("Bear Put 价差：`/bearput-sim suggest ZZZQ`",
                   "Bear Call 价差：`/bearcall-sim suggest ZZZQ`",
                   "`open ZZZQ 0.60`", "`open ZZZQ 0.50`", "`open ZZZQ 0.15`", "`open ZZZQ 0.10`",
                   "净支出可能全亏", "最大亏损是宽度减权利金", "逆势", "IV 偏高", "向上漂移",
                   "考虑减仓", "Collar", "买入 Put"):
        assert needle in text, needle
    assert "不出任何看涨策略" not in text
    assert "看涨或中性的期权策略" not in text
    for table_row in (l for l in text.splitlines() if l.startswith("| Bear ")):
        assert "open ZZZQ --from-pick" in table_row, table_row
    section = _guide_section()
    for needle in ("--view 不看好", "Bear Put 价差", "Bear Call 价差", "/bearput-sim suggest ZZZQ",
                   "/bearcall-sim suggest ZZZQ", "--from-pick", "/longput-sim"):
        assert needle in section, needle


def test_old_specs_point_to_the_bearish_supplement():
    note = "Update 2026-10-30: the bearish part is superseded by 2026-10-30-strategy-pick-bearish-design.md"
    for name in OLD_SPECS:
        path = REPO_ROOT / "docs" / "superpowers" / "specs" / name
        head = "\n".join(_source_or_skip(path).splitlines()[:8])
        assert note in head, name


@pytest.mark.parametrize("rel", ["wiki/options/strategy-pick-guide.md",
                                 "docs/strategy-pick-guide.md"])
def test_public_guide_tree_shows_the_bear_spreads(rel):
    text = _source_or_skip(REPO_ROOT / rel)
    assert "不出任何看涨策略" not in text
    tree = text.split("### 第二层：看法", 1)[1].split("###", 1)[0]
    for needle in ("Bear Put 价差", "Bear Call 价差", "减仓", "/longput-sim"):
        assert needle in tree, needle


def test_share_docs_list_the_bear_spreads_as_recommended():
    for name in ("CLAUDE.md", "README.md"):
        rows = [l for l in _source_or_skip(SHARE_DOCS / name).splitlines()
                if l.startswith("| `/bearcall-sim`") or l.startswith("| `/bearput-sim`")]
        assert len(rows) == 2, name
        for row in rows:
            assert "`/strategy-pick` 在不看好时推荐" in row, (name, row)


def test_bear_not_held_hint_stays_true_when_bear_call_is_dropped():
    # Imminent earnings on a hot stock drop Bear Call ([wait_earnings, bear_put_spread]); the hint
    # states the order as a rule, not as "Bear Call comes second".
    hint = T.WARN["bear_not_held"]
    assert "在后" not in hint and "第二" not in hint
    assert "Bear Put" in hint and "优先" in hint
