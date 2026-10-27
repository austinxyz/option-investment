"""Layout primitives for the strategy-pick decision-path SVG."""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import strategy_pick_svg as S  # noqa: E402

NS = "{http://www.w3.org/2000/svg}"
ALLOWED = {"svg", "g", "rect", "path", "text", "title", "desc"}
ONE_DECIMAL = re.compile(r"^-?\d+\.\d$")


def _line(text="标题", size=15, weight=400, color="#2b2a27"):
    return S.Line(text=text, size=size, weight=weight, color=color)


def _box(n=2, on=False, leaves=(), palette="decision", x=12, y=100, w=96):
    lines = tuple(_line(f"行{i}") for i in range(n))
    return S.Box(x=x, y=y, w=w, lines=lines, palette=S.COLORS[palette], on=on, leaves=leaves)


def _doc(**kw):
    args = dict(title="ZZZQ 的决策路径", desc="看好 × 未持有 → ①", height=300,
                off=[S.box_svg(_box(1))], on_edges=[S.elbow(108, 50, 146, 80, True)],
                on_nodes=[S.box_svg(_box(2, on=True))],
                header=[S.text_el(14, 28, "标题", 18, "#2b2a27", 600)],
                footer=[S.text_el(14, 280, "不构成投资建议。", 13.5, "#6f6e69")])
    args.update(kw)
    return S.document(**args)


def _tree(doc=None):
    return ET.fromstring(doc if doc is not None else _doc())


def test_text_width_cjk_full_and_ambiguous_count_one():
    for ch in "现金①≥×":
        assert S.text_width(ch, 15) == pytest.approx(15.0)
    for ch in "A1.":
        assert S.text_width(ch, 15) == pytest.approx(9.0)
    assert S.text_width("现金A", 10) == pytest.approx(10 + 10 + 6)


def test_wrap_prefers_cjk_punct_and_space():
    lines = S.wrap("甲乙丙丁；戊己庚辛壬癸", 5 * 15, 15, 3)
    assert lines[0] == "甲乙丙丁；"
    lines = S.wrap("甲乙丙，丁戊己庚辛壬", 5 * 15, 15, 3)
    assert lines[0] == "甲乙丙，"
    lines = S.wrap("甲乙、丙丁戊己庚辛壬", 5 * 15, 15, 3)
    assert lines[0] == "甲乙、"
    lines = S.wrap("甲乙 丙丁戊己庚辛壬癸", 5 * 15, 15, 3)
    assert lines[0] == "甲乙"
    assert lines[1].startswith("丙")


def test_wrap_breaks_by_char_when_no_breakpoint():
    # A lone CJK character never sits on a line by itself: one moves down from the line above.
    assert S.wrap("甲乙丙丁戊己庚", 3 * 15, 15, 5) == ("甲乙丙", "丁戊", "己庚")


def test_wrap_keeps_bracket_group_that_fits_alone():
    assert S.wrap("甲乙丙丁（戊己庚）", 6 * 15, 15, 3) == ("甲乙丙丁", "（戊己庚）")
    assert S.wrap("ab cd（Delta 0.10）", 200, 15, 3)[-1].endswith("（Delta 0.10）")


def test_wrap_glues_comparison_symbol_to_next_token():
    for sym in ("≥", "≤", "≈", "→", "<", ">"):
        lines = S.wrap(f"甲乙丙丁戊 {sym} 12.5%", 7 * 15, 15, 3)
        assert any(ln.startswith(f"{sym} 12.5%") for ln in lines), (sym, lines)


def test_wrap_prefers_clause_breaks_over_spaces():
    assert S.wrap("甲乙丙丁戊己庚 → AB CD 辛壬", 10 * 15, 15, 3) == ("甲乙丙丁戊己庚", "→ AB CD 辛壬")
    assert S.wrap("甲乙；丙丁 AB CD EF", 7 * 15, 15, 3)[0] == "甲乙；"


def test_esc_drops_control_chars_and_normalises_cr():
    assert S.esc("a\x01b\r\nc\rd\x1f") == "ab\nc\nd"
    assert S.esc_attr('"\x0b\r') == "&quot;\n"
    doc = S.document("t\x01", "d\x08", 100, [], [], [], [], [])
    ET.fromstring(doc)


def test_box_data_leaves_attribute_escaped():
    box = S.Box(10, 10, 100, (_line(),), S.COLORS["neutral"], False, ('a"<&b',))
    g = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{S.box_svg(box)}</svg>')[0]
    assert g.get("data-leaves") == 'a"<&b'


def test_wrap_truncates_with_ellipsis_at_max_lines():
    width = 3 * 15
    lines = S.wrap("甲乙丙丁戊己庚辛壬癸子丑", width, 15, 3)
    assert len(lines) == 3
    assert lines[2].endswith("…")
    assert all(S.text_width(x, 15) <= width + 1e-9 for x in lines)
    assert S.wrap("甲乙丙丁戊己庚辛壬", width, 15, 3) == ("甲乙丙", "丁戊己", "庚辛壬")


def test_box_height_formula():
    assert S.box_height(1) == 42.0
    assert S.box_height(2) == 62.0
    assert S.box_height(6) == 142.0
    assert _box(3).h == 82.0


def test_first_baseline_is_top_plus_26_then_20():
    root = _tree(_doc(off=[], on_edges=[], on_nodes=[S.box_svg(_box(3, y=100))],
                      header=[], footer=[]))
    ys = [float(t.get("y")) for t in root.iter(NS + "text")]
    assert ys == [126.0, 146.0, 166.0]


def test_coordinates_one_decimal():
    doc = _doc(on_edges=[S.elbow(108, 50, 146, 80, True), S.straight(10, 20, 30, 20, False),
                         S.arrow(252, 121, 262, True)])
    root = _tree(doc)
    checked = 0
    for el in root.iter():
        if el is root:
            continue
        for name in ("x", "y", "width", "height"):
            if el.get(name) is not None:
                assert ONE_DECIMAL.match(el.get(name)), (el.tag, name, el.get(name))
                checked += 1
        if el.get("d") is not None:
            nums = re.findall(r"-?\d+(?:\.\d+)?", el.get("d"))
            assert nums and all(ONE_DECIMAL.match(n) for n in nums), el.get("d")
            checked += 1
    assert checked > 10


def test_esc_text_and_attr():
    assert S.esc("<&>") == "&lt;&amp;&gt;"
    assert S.esc('a"b') == 'a"b'
    assert S.esc_attr('a"<&>') == "a&quot;&lt;&amp;&gt;"
    box = S.Box(x=0, y=0, w=96, lines=(_line("<&>"),), palette=S.COLORS["sell"],
                on=False, leaves=('a"b',))
    root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{S.box_svg(box)}</svg>')
    assert root.find(f"{NS}g/{NS}text").text == "<&>"
    assert root.find(NS + "g").get("data-leaves") == 'a"b'


def test_elbow_midpoint():
    assert 'd="M108.0 50.0 H127.0 V80.0 H146.0"' in S.elbow(108, 50, 146, 80, False)


def test_document_root_attrs_and_order():
    root = _tree()
    assert root.tag == NS + "svg"
    assert root.get("viewBox") == "0 0 958 300"
    assert root.get("width") == "958" and root.get("height") == "300"
    assert root.get("role") == "img"
    assert root.get("aria-labelledby") == "spk-title spk-desc"
    kids = list(root)
    assert [k.tag for k in kids[:2]] == [NS + "title", NS + "desc"]
    assert kids[0].get("id") == "spk-title" and kids[1].get("id") == "spk-desc"
    doc = _doc()
    assert doc.index('opacity="0.4"') < doc.index('class="spk-on"')
    off = [g for g in root.iter(NS + "g") if g.get("opacity") == "0.4"]
    assert len(off) == 1


def test_document_parses_and_only_allowed_elements():
    root = _tree(_doc(on_edges=[S.arrow(252, 121, 262, True)]))
    tags = {el.tag.replace(NS, "") for el in root.iter()}
    assert tags <= ALLOWED
    doc = _doc()
    for bad in ("href", "style", "script", "tspan"):
        assert bad not in doc


def test_document_bytes_lf_no_bom_single_trailing_newline():
    raw = _doc().encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert len(raw) < S.SIZE_CAP


def test_same_input_same_bytes():
    assert _doc().encode() == _doc().encode()


def test_no_threshold_literals_in_svg_module():
    src = Path(S.__file__).read_text(encoding="utf-8")
    for lit in ("0.35", "0.25", "0.12", "0.70", "0.30"):
        assert lit not in src
