"""Data-quality labels for every option output in Yahoo / demo mode (share plan, Task 5).

One place decides what a report says about where its numbers came from, so no renderer can
forget a label or keep a stale "IBKR". Every helper is a no-op in IBKR mode (None / {} / text
unchanged): the IBKR outputs stay byte-identical to the Task 1 goldens.

The current setting labels what is being written now (banner, json keys, console header).
Ledger records are different: each one carries the source of the CANDIDATE it was opened from
(`record_fields`), because the ibkr and yahoo ledgers share one directory and a setting changed
between suggest and open must not relabel an already-priced candidate.
"""
from __future__ import annotations

import html
from collections import Counter
from typing import Callable, Iterable

import option_settings

LABELS = {"ibkr": "IBKR", "yahoo": "Yahoo", "demo": "Demo"}
QUALITY_TEXT = {"low": "低", "demo": "演示"}
BANNERS = {
    "yahoo": "> ⚠️ 数据来源：Yahoo Finance（延迟约 15 分钟，非券商报价）。数据质量：低。"
             "Delta 为本地 Black-Scholes 估算；休市时用最后成交价（标\"陈旧\"）；"
             "买卖价差门槛放宽到 10%。按中间价成交、不含手续费，模拟结果偏乐观。",
    "demo": "> ⚠️ 演示数据（虚构），不代表任何真实行情。",
}
CONSOLE_HEADER = "数据源：{label} · 数据质量：{quality}"
IBKR_UNAVAILABLE = "IBKR 网关不可用"
UNAVAILABLE = {"yahoo": "Yahoo Finance 不可用", "demo": "演示数据不可用"}
MARKET_CLOSED_HINT = "休市中：Yahoo 的 bid/ask 常为 0、IV 不可靠，很多标的会被剔除；建议美东 9:30–16:00 运行"
STALE_DROP_LINE = "{n} 档报价只有过期的最后成交价，已忽略"
LEDGER_MIX = {
    "Yahoo": "其中 {n} 笔按 Yahoo 数据开仓（数据质量低，结果偏乐观）",
    "Demo": "其中 {n} 笔按演示数据开仓（虚构数据，不代表真实行情）",
}
ROW_MARK = {"Yahoo": "·Y", "Demo": "·D"}
ROW_LEGEND = "标的后的 {marks} 表示该笔的开仓数据源"
JOURNAL_MIX = "样本里混有不同数据源：{counts}"
RECORD_KEYS = ("data_source", "data_quality")
DEMO_SEED_HINT = ("演示模式：先 `call_screen.py shares add ZZZQ --shares 100 --cost 100` "
                  "添加一笔假想持股，再运行 suggest")
YAHOO_MODE, DEMO_MODE = "Yahoo 模式", "演示模式"


def _name() -> str:
    return option_settings.data_source()


def data_source_label() -> str:
    """The json `data_source` value: "IBKR" / "Yahoo" / "Demo"."""
    return LABELS[_name()]


def quality_banner() -> str | None:
    return BANNERS.get(_name())


def quality_fields() -> dict[str, str]:
    """`{"data_quality": ...}` next to an existing `data_source`; {} in IBKR (no new keys)."""
    quality = option_settings.profile().quality
    return {} if quality is None else {"data_quality": quality}


def labelled_fields() -> dict[str, str]:
    """Both keys for outputs that never had `data_source` (IBKR adds nothing)."""
    quality = quality_fields()
    return {"data_source": data_source_label(), **quality} if quality else {}


def console_header() -> str | None:
    prof = option_settings.profile()
    if prof.quality is None:
        return None
    return CONSOLE_HEADER.format(label=LABELS[prof.name], quality=QUALITY_TEXT[prof.quality])


def print_console_header() -> None:
    """First console line of every command in Yahoo / demo mode."""
    header = console_header()
    if header is not None:
        print(header)


def source_unavailable_text() -> str:
    name = _name()
    return IBKR_UNAVAILABLE if name == "ibkr" else UNAVAILABLE[name]


def source_line(snapshot: str, intraday: bool) -> str:
    """'数据源 … · 基于 … 收盘（…）'; the IBKR wording is the pinned original."""
    kind = "盘中延迟行情" if intraday else "冻结行情"
    prof = option_settings.profile()
    if prof.quality is None:
        return f"数据源 IBKR 模拟网关 · 基于 {snapshot} 收盘（{kind}）"
    # "冻结行情" is an IBKR market-data mode; Yahoo / demo only have a delayed or after-close snapshot.
    kind = "盘中延迟行情" if intraday else "收盘后快照"
    return f"数据源 {prof.label} · 基于 {snapshot} 收盘（{kind}）"


def rules_text(text: str, ibkr_fragment: str, yahoo_fragment: str | None = None) -> str:
    """Swap the IBKR 5% spread rule inside a rules line for the relaxed Yahoo / demo one."""
    if option_settings.is_ibkr():
        return text
    if yahoo_fragment is None:
        import put_screen_rules  # lazy: keeps this module importable from the rule modules' peers
        yahoo_fragment = put_screen_rules.RULES_LINE_SPREAD_YAHOO
    if _name() == "demo":
        # Same 10% / 5% numbers, but a demo reader is not looking at Yahoo quotes. The Yahoo
        # wording itself is Global Constraints text and stays verbatim.
        yahoo_fragment = yahoo_fragment.replace(YAHOO_MODE, DEMO_MODE)
    return text.replace(ibkr_fragment, yahoo_fragment)


def demo_seed_hint(lots: list) -> str | None:
    """Demo only: covered-call / collar screens have nothing to screen until a paper lot exists
    (the demo never reads the real positions file), so say how to add one."""
    return DEMO_SEED_HINT if _name() == "demo" and not lots else None


def market_closed_hint(market_open: Callable[[], bool]) -> str | None:
    """Yahoo only (demo has no market hours); the clock is read only in Yahoo mode."""
    if _name() != "yahoo" or market_open():
        return None
    return MARKET_CLOSED_HINT


def stale_drop_total(pairs: Iterable[tuple[str, str]]) -> int:
    """Stale window rows dropped for the given (ticker, expiry) pairs.

    CLIs pass only their CANDIDATES' pairs: a reject carries no expiry, and a ticker whose rows
    were all dropped is already a reject with its own "没有拿到…期权报价" reason. So the
    summary's "N 档报价只有过期的最后成交价，已忽略" counts the expiries of the candidates
    shown, not every ticker screened. (A total over yahoo_data's memo was rejected: the memo
    outlives one command in-process and would mix in earlier runs.)
    """
    import spread_data  # lazy: spread_data must not depend on the labels
    return sum(spread_data.stale_drop_count(t, e) for t, e in sorted(set(pairs)))


def stale_drop_line(count: int) -> str | None:
    return STALE_DROP_LINE.format(n=count) if count > 0 else None


def run_notes(*notes: str | None) -> tuple[str, ...]:
    return tuple(n for n in notes if n)


def print_notes(notes: Iterable[str]) -> None:
    for note in notes:
        print(note)


def _title_index(lines: list[str]) -> int:
    """Index of the '# ' title after any '---' front matter. Without a title: the front
    matter's closing line, or -1, so the banner still goes first in the body."""
    start = 0
    if lines and lines[0] == "---" and "---" in lines[1:]:
        start = lines.index("---", 1) + 1
    for i in range(start, len(lines)):
        if lines[i].startswith("# "):
            return i
        if lines[i].strip():
            break
    return start - 1


def with_banner(md: str, notes: Iterable[str] = ()) -> str:
    """Banner (Yahoo / demo) then notes as the first paragraphs under the title.

    Notes (closed-market hint, stale drops, ledger mix lines) may appear in IBKR mode too, e.g. a
    mixed ledger; with no banner and no notes the text is returned untouched.
    """
    banner = quality_banner()
    return _insert(md, ([banner] if banner else []) + [f"> {n}" for n in notes])


def add_notes(md: str, notes: Iterable[str]) -> str:
    """Notes right after the banner a renderer already put in (CLI-level facts such as the
    closed-market hint and the stale-drop count, which the pure renderers never see)."""
    paragraphs = [f"> {n}" for n in notes]
    banner = quality_banner()
    if not paragraphs:
        return md
    if banner and f"\n{banner}\n\n" in md:
        block = "".join(f"{p}\n\n" for p in paragraphs)
        return md.replace(f"\n{banner}\n\n", f"\n{banner}\n\n{block}", 1)
    return _insert(md, paragraphs)


def _insert(md: str, paragraphs: list[str]) -> str:
    if not paragraphs:
        return md
    lines = md.split("\n")
    t = _title_index(lines)
    head = lines[:t + 1] + ([""] if t >= 0 else [])
    rest = lines[t + 1:]
    if rest and rest[0] == "":
        rest = rest[1:]  # the title's own blank line is re-added after the block
    block = [line for para in paragraphs for line in (para, "")]
    return "\n".join(head + block + rest)


def with_html_banner(page: str, notes: Iterable[str] = ()) -> str:
    """The same banner and notes as `<p class="warn">` right after the page's first </h1>."""
    banner = quality_banner()
    texts = ([banner[2:]] if banner else []) + list(notes)
    if not texts:
        return page
    block = "".join(f'<p class="warn">{html.escape(t)}</p>\n' for t in texts)
    head, sep, rest = page.partition("</h1>\n")
    return head + sep + block + rest if sep else block + page


def record_fields(candidate: dict) -> dict[str, str]:
    """Ledger keys copied from the candidate file (never from the current setting)."""
    if not candidate.get("data_quality"):
        return {}
    return {k: candidate[k] for k in RECORD_KEYS if k in candidate}


def _sources(records: Iterable[dict]) -> Counter:
    return Counter(r.get("data_source", "IBKR") if r.get("data_quality") else "IBKR"
                   for r in records)


def ledger_mix_line(records: list[dict]) -> str | None:
    counts = _sources(records)
    parts = [LEDGER_MIX[src].format(n=counts[src]) for src in LEDGER_MIX if counts.get(src)]
    if not parts:
        return None
    marks = "、".join(ROW_MARK[src] for src in LEDGER_MIX if counts.get(src))
    return "；".join(parts) + "。" + ROW_LEGEND.format(marks=marks) + "。"


def tag_records(records: list[dict]) -> list[dict]:
    """Copies for display: a non-IBKR record's ticker gets its source mark (ZZZQ·Y)."""
    out = []
    for r in records:
        mark = ROW_MARK.get(r.get("data_source", "")) if r.get("data_quality") else None
        out.append({**r, "ticker": f"{r['ticker']}{mark}"} if mark else r)
    return out


def journal_mix_line(records: list[dict]) -> str | None:
    counts = _sources(records)
    if len(counts) < 2:
        return None
    text = "、".join(f"{src} {n} 笔" for src, n in sorted(counts.items()))
    return JOURNAL_MIX.format(counts=text)
