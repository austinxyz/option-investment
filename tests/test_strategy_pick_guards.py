"""Safety net for /strategy-pick: advisory only, private output, invented tickers in the docs."""
import ast
import re
import sys
from pathlib import Path

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
                            r"condor-sim suggest|stock-review)\s+([A-Z][A-Z0-9.\-]*)", text))
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
    for needle in ("--view 看好", "退出码 3", "退出码 1", "10197", "docs/option-data-sources.md",
                   "权利金薄", "论点过期", "代理", "portfolio_share", "rank_by_class_only",
                   "非投资建议", "不替用户选"):
        assert needle in text, needle


def test_existing_strategy_guard_untouched():
    text = _source(REPO_ROOT / "tests" / "test_all_strategies_guard.py")
    match = re.search(r"^STRATEGIES = \(([^)]*)\)", text, re.M)
    keys = re.findall(r'"([a-z_]+)"', match.group(1))
    assert len(keys) == 7
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
