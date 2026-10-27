"""Tree model of the strategy-pick decision diagram: built only from LEAF_TABLE."""
import xml.etree.ElementTree as ET

import pytest

import strategy_pick_diagram as D
import strategy_pick_rules as R
import strategy_pick_svg as V
import strategy_pick_text as T

SVG_NS = "{http://www.w3.org/2000/svg}"
TREES = [(v, b) for v in ("bull", "neutral") for b in ("held", "not_held")]
CHEAP = R.LeafPick("no_options")


def _root(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _leaf_sets(svg: str) -> list[set[str]]:
    return [set(g.get("data-leaves").split())
            for g in _root(svg).iter(f"{SVG_NS}g") if g.get("data-leaves")]


def _prefix_keys(view: str, branch: str) -> set[str]:
    return {k for k in R.LEAF_TABLE if k.startswith(f"{view}.{branch}.")}


def test_iv_groups_exact_identity():
    high_vol = D.iv_groups("bull", "not_held", "high_vol")
    assert [g.ivs for g in high_vol] == [("high", "mid"), ("low",)]
    assert len(D.iv_groups("bull", "not_held", "range")) == 3
    down = D.iv_groups("bull", "not_held", "down")
    assert len(down) == 1 and down[0].ivs == R.IV_LABELS


def test_bull_not_held_tree_has_11_leaf_boxes():
    assert len(_leaf_sets(D.render_tree("bull", "not_held"))) == 11


@pytest.mark.parametrize("view,branch", TREES)
def test_render_tree_covers_leaf_table(view, branch):
    sets = _leaf_sets(D.render_tree(view, branch))
    union = set().union(*sets)
    assert union == _prefix_keys(view, branch)
    assert sum(len(s) for s in sets) == len(union)  # pairwise disjoint


@pytest.mark.parametrize("view,branch", TREES)
def test_static_trees_never_truncate(view, branch):
    assert "…" not in D.render_tree(view, branch)


@pytest.mark.parametrize("view,branch", TREES)
def test_static_trees_parse_and_size_cap(view, branch):
    svg = D.render_tree(view, branch)
    root = _root(svg)
    assert root.get("viewBox") == f"0 0 {root.get('width')} {root.get('height')}"
    assert len(svg.encode("utf-8")) < V.SIZE_CAP
    assert not list(root.iter(f"{SVG_NS}g"))[0].get("data-leaves", "")


def test_static_tree_nodes_are_plain_not_highlighted():
    root = _root(D.render_tree("bull", "not_held"))
    classes = {g.get("class") for g in root.iter(f"{SVG_NS}g")}
    assert "spk-node spk-on" not in classes
    off = [g for g in root.iter(f"{SVG_NS}g") if g.get("class") == "spk-off"]
    assert all(not list(g) for g in off)


def test_leaf_family_by_first_pick():
    held_low = R.LEAF_TABLE["bull.held.steady_up.low"][0]
    assert [p.strategy for p in held_low] == ["hold", "protective_put"]
    assert D.leaf_family(held_low) == "neutral"
    assert D.leaf_family((R.LeafPick(R.SELL_PAIR, "put_normal"),)) == "sell"
    assert D.leaf_family(R.LEAF_TABLE["bull.not_held.range.high"][0]) == "range"
    assert D.leaf_family((R.LeafPick("bull_call_spread", "bullcall"),)) == "buy"
    assert D.leaf_family((R.LeafPick("collar", "collar"),)) == "protect"


def _with_leaf(monkeypatch, key, picks, warns=()):
    monkeypatch.setitem(R.LEAF_TABLE, key, (tuple(picks), tuple(warns)))


def test_unknown_flag_raises(monkeypatch):
    _with_leaf(monkeypatch, "bull.not_held.range.high",
               [R.LeafPick("no_options", None, ("zz_unknown",))])
    with pytest.raises(D.DiagramError):
        D.render_tree("bull", "not_held")


def test_unknown_strategy_raises(monkeypatch):
    _with_leaf(monkeypatch, "bull.not_held.range.high", [R.LeafPick("zz_strategy")])
    with pytest.raises(D.DiagramError):
        D.render_tree("bull", "not_held")


def test_unknown_regime_raises(monkeypatch):
    _with_leaf(monkeypatch, "bull.not_held.zz_regime.high", [CHEAP])
    with pytest.raises(D.DiagramError):
        D.render_tree("bull", "not_held")
    monkeypatch.undo()
    monkeypatch.setattr(R, "REGIME_LEAVES", R.REGIME_LEAVES + ("zz_regime",))
    with pytest.raises(D.DiagramError):
        D.render_tree("bull", "not_held")


def test_unknown_warning_raises(monkeypatch):
    _with_leaf(monkeypatch, "bull.not_held.range.high", [CHEAP], ("zz_warn",))
    with pytest.raises(D.DiagramError):
        D.render_tree("bull", "not_held")


def test_far_otm_delta_range_from_constants(monkeypatch):
    assert "远价外（Delta 0.10–0.15）" in D.render_tree("bull", "not_held")
    monkeypatch.setattr(R, "FAR_OTM_DELTA", (0.11, 0.16))
    svg = D.render_tree("bull", "not_held")
    assert "远价外（Delta 0.11–0.16）" in svg and "0.10–0.15" not in svg


def test_hidden_flags_and_sell_pair_text():
    lines = D.static_leaf_lines(D.iv_groups("bull", "not_held", "steady_up")[0])
    assert lines[0] == "现金担保 Put / Bull Put 价差"
    assert lines[1] == "按占比和类别排序"
    group = D.iv_groups("bull", "not_held", "down")[0]
    lines = D.static_leaf_lines(group)
    assert lines[0] == f"① {T.STRATEGY_LABELS['cash_secured_put']}   ② {T.STRATEGY_LABELS['no_options']}"
    assert T.WARN["bull_down"] in lines
    assert not any("reduce" in ln for ln in lines)


def _centers(boxes, x):
    return [b.y + b.h / 2 for b in boxes if b.x == x]


def test_layout_vertical_rules():
    lay = D.layout_tree("bull", "not_held", None)
    leaves = sorted((b for b in lay.boxes if b.x == V.COL_LEAF[0]), key=lambda b: b.y)
    ivs = [b for b in lay.boxes if b.x == V.COL_IV[0]]
    regimes = sorted((b for b in lay.boxes if b.x == V.COL_REGIME[0]), key=lambda b: b.y)
    root, = (b for b in lay.boxes if b.x == V.COL_ROOT[0])

    def band(leaf):
        mates = [i for i in ivs if abs(i.y + i.h / 2 - (leaf.y + leaf.h / 2)) < 1e-6]
        h = max([leaf.h] + [m.h for m in mates])
        c = leaf.y + leaf.h / 2
        return c - h / 2, c + h / 2

    bands = [band(b) for b in leaves]
    # Adjacent leaf bands inside a regime are exactly BAND_GAP apart; across regimes the
    # regime block may be taller, so the gap is never smaller.
    gaps = [round(b[0] - a[1], 6) for a, b in zip(bands, bands[1:])]
    assert all(g >= V.BAND_GAP for g in gaps) and V.BAND_GAP in gaps
    assert bands[0][0] == V.TREE_TOP or regimes[0].y >= V.TREE_TOP - 1e-6
    # Regime node centre = mean of its first and last sub-band centres.
    by_regime = [_centers([b for b in leaves if b.leaves[0].split(".")[2] == reg], V.COL_LEAF[0])
                 for reg in R.REGIME_LEAVES]
    for node, centres in zip(regimes, by_regime):
        assert node.y + node.h / 2 == pytest.approx((centres[0] + centres[-1]) / 2)
    mid = [(r.y + r.h / 2) for r in regimes]
    assert root.y + root.h / 2 == pytest.approx((mid[0] + mid[-1]) / 2)
    assert min(b.y for b in lay.boxes) >= V.TREE_TOP - 1e-6
    assert lay.height >= max(b.y + b.h for b in lay.boxes)


def test_adjacent_bands_in_one_regime_gap_exactly_ten():
    lay = D.layout_tree("bull", "not_held", None)
    leaves = sorted((b for b in lay.boxes if b.x == V.COL_LEAF[0]), key=lambda b: b.y)
    ivs = {round(b.y + b.h / 2, 3): b for b in lay.boxes if b.x == V.COL_IV[0]}
    # range regime: three IV groups, all with IV boxes, no regime taller than its stack.
    rng = [b for b in leaves if set(b.leaves) & {f"bull.not_held.range.{i}" for i in R.IV_LABELS}]
    assert len(rng) == 3

    def band(b):
        m = ivs.get(round(b.y + b.h / 2, 3))
        h = max(b.h, m.h if m else 0)
        return b.y + b.h / 2 - h / 2, b.y + b.h / 2 + h / 2

    spans = [band(b) for b in rng]
    assert [round(y[0] - x[1], 6) for x, y in zip(spans, spans[1:])] == [10.0, 10.0]


def test_render_tree_deterministic():
    assert D.render_tree("bull", "not_held") == D.render_tree("bull", "not_held")
    assert D.render_tree("neutral", "held").endswith("</svg>\n")


def test_module_has_no_threshold_literals():
    src = open(D.__file__, encoding="utf-8").read()
    for lit in ("0.35", "0.25", "0.12", "0.70", "0.30", "0.10", "0.15"):
        assert lit not in src


# ---- Task 3: highlighted path and node text (render_svg / diagram_alt) ----

import copy  # noqa: E402
from dataclasses import replace  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from datetime import date  # noqa: E402

import strategy_pick as CLI  # noqa: E402
import strategy_pick_data as DATA  # noqa: E402
import strategy_pick_metrics as M  # noqa: E402
import strategy_pick_steps as S  # noqa: E402
from strategy_pick_cli_stubs import (base_result, history, install, outputs, run,  # noqa: E402
                                     snapshot, thesis)

TEXT_TAG, PATH_TAG, G_TAG = f"{SVG_NS}text", f"{SVG_NS}path", f"{SVG_NS}g"
ALLOWED = {f"{SVG_NS}{n}" for n in ("svg", "g", "rect", "path", "text", "title", "desc")}
PCT_AFTER_CMP = re.compile(r"[≥≤<>] (-?\d+(?:\.\d+)?)%")
PCT = re.compile(r"(-?\d+(?:\.\d+)?)%")


def _rows(*shares):
    rows = tuple(DATA.HoldingRow(float(n), True, "active") for n in shares)
    return DATA.HoldingRead(rows, None, False, False)


CASES = {
    "base": {},
    "V1": dict(read_holding=_rows(150)),
    "V6": dict(load_history=history(volume=130_000.0)),
    "V7": dict(read_thesis=thesis(status="avoid"), read_holding=_rows(150)),
    "V8": dict(argv=("ZZZQ", "--class", "hot"), next_earnings=date(2026, 10, 28)),
    "V9": dict(read_thesis=thesis(status="watch"), read_holding=_rows(60, 50)),
    "V10": dict(load_history=history(219)),
    "history_short": dict(load_history=history(150)),
    "bear_no_lot": dict(read_thesis=thesis(status="avoid"), read_holding=_rows(60, 50)),
    "bull_no_lot": dict(read_holding=_rows(60, 50)),
    "rv_vs_rv": dict(option_snapshot=snapshot(atm_iv=None)),
    "class_only": dict(portfolio_value=None),
    "hot": dict(argv=("ZZZQ", "--class", "hot")),
}


def _case(monkeypatch, tmp_path, name, **extra) -> tuple[dict, str]:
    over = {**CASES[name], **extra}
    argv = over.pop("argv", ("ZZZQ",))
    return base_result(monkeypatch, tmp_path, argv=argv, **over)


def _texts(el) -> list[str]:
    return ["".join(t.itertext()) for t in el.iter(TEXT_TAG)]


def _on_lines(root) -> list:
    return [p for p in root.iter(PATH_TAG)
            if p.get("class") == "spk-on" and p.get("fill") == "none"]


def _on_boxes(root) -> list:
    return [g for g in root.iter(G_TAG) if g.get("class") == "spk-node spk-on"]


def _off_group(root):
    return next(g for g in root.iter(G_TAG) if g.get("class") == "spk-off")


def _on_texts(root) -> list[str]:
    return [t for g in _on_boxes(root) for t in _texts(g)]


def _relabel(doc: dict, leaf_id: str, **metrics) -> dict:
    out = copy.deepcopy(doc)
    out["leaf_id"] = leaf_id
    out["metrics"].update(metrics)
    picks, warns = R.LEAF_TABLE[leaf_id]
    out["thresholds_used"]["LEAF_TABLE"] = {leaf_id: {
        "picks": [{"strategy": p.strategy, "plan": p.plan, "flags": list(p.flags)} for p in picks],
        "warnings": list(warns)}}
    return out


# Step 1: structure and path

def test_base_parses_root_attrs_allowed_elements(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    root = _root(D.render_svg(doc))
    assert root.tag == f"{SVG_NS}svg" and root.get("width") == str(V.CANVAS_W)
    assert root.get("viewBox") == f"0 0 {root.get('width')} {root.get('height')}"
    assert root.get("role") == "img" and root.get("aria-labelledby") == "spk-title spk-desc"
    assert [c.tag for c in list(root)[:2]] == [f"{SVG_NS}title", f"{SVG_NS}desc"]
    assert {e.tag for e in root.iter()} <= ALLOWED
    for e in root.iter():
        assert not any("href" in k or k in ("style", "script") for k in e.attrib)


def test_base_path_counts(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    svg = D.render_svg(doc)
    root = _root(svg)
    assert len(_on_lines(root)) == 3 and len(_on_boxes(root)) == 4
    on_leaves = [g for g in _on_boxes(root) if g.get("data-leaves")]
    assert len(on_leaves) == 1 and doc["leaf_id"] in on_leaves[0].get("data-leaves").split()
    sets = _leaf_sets(svg)
    assert len(sets) == 11 and set().union(*sets) == _prefix_keys("bull", "not_held")


def test_down_regime_path_has_two_edges(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    down = _relabel(doc, "bull.not_held.down.high", r1y=-0.2, last_close=80.0, ma200=95.0,
                      ma200_slope=-0.01, ma50=85.0)
    assert S.regime_of(down).leaf == "down"
    root = _root(D.render_svg(down))
    assert len(_on_lines(root)) == 2 and len(_on_boxes(root)) == 3
    assert D.path_of(down) == D.PathSpec("T", "bull", "not_held", "down", "high", None)


@pytest.mark.parametrize("name,leaf", [("V7", "bear.held"), ("bear_no_lot", "bear.not_held")])
def test_layout_b_two_edges(monkeypatch, tmp_path, name, leaf):
    doc, _ = _case(monkeypatch, tmp_path, name)
    assert doc["leaf_id"] == leaf and D.path_of(doc).layout == "B"
    root = _root(D.render_svg(doc))
    assert len(_on_lines(root)) == 2 and len(_on_boxes(root)) == 3
    assert {g.get("data-leaves") for g in root.iter(G_TAG) if g.get("data-leaves")} == {
        "bear.held", "bear.not_held"}


@pytest.mark.parametrize("name,stop", [("V6", 1), ("history_short", 2)])
def test_layout_c_edges_by_stop_step(monkeypatch, tmp_path, name, stop):
    doc, _ = _case(monkeypatch, tmp_path, name)
    path = D.path_of(doc)
    assert path.layout == "C" and path.stop_step == stop
    root = _root(D.render_svg(doc))
    assert len(_on_lines(root)) == stop and len(_on_boxes(root)) == stop + 1
    off = _texts(_off_group(root))
    assert len([t for t in off if t[0] in "①②③④"]) == 4 - stop


def test_title_and_desc_text(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    root = _root(D.render_svg(doc))
    title = "ZZZQ 的决策路径（看好 × 未持有）"
    assert root.find(f"{SVG_NS}title").text == title and title in _texts(root)
    desc = root.find(f"{SVG_NS}desc").text
    assert desc == D.diagram_alt(doc) and "]" not in desc
    assert "看好 × 未持有 × 稳步上涨 × IV 高" in desc and "现金担保 Put" in desc


# Step 2: numbers and comparison symbols

@pytest.mark.parametrize("op,hit,want", [
    ("≥", True, "≥"), ("≥", False, "<"), (">", True, ">"), (">", False, "≤"),
    ("≤", True, "≤"), ("≤", False, ">"), ("<", True, "<"), ("<", False, "≥"),
    (">=", True, "≥"), ("<=", False, ">"), ("≥", None, "未知"), ("<", None, "未知")])
def test_cmp_symbol_flips_on_miss(op, hit, want):
    assert D.cmp_symbol(op, hit) == want


def test_base_regime_lines(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    m = doc["metrics"]
    sym = {"".join(e.name.split()[:2]): D.cmp_symbol(e.name.split()[1], e.result == "hit")
           for e in S.regime_of(doc).evidence}
    assert D.regime_lines(doc) == (
        "稳步上涨", f"一年 +25.0% {sym['R1y>=']} 12%",
        f"收盘 {S.num(m['last_close'])} {sym['Pn>']} MA200 {S.num(m['ma200'])}",
        f"MA200 斜率 +2.04% {sym['MA200_slope>']} 0",
        f"MA50 {S.num(m['ma50'])} {sym['MA50>']} MA200",
        f"波动率 30.0% {sym['RV1y>=']} 35%", f"回撤 15.0% {sym['MaxDD>=']} 25%")
    assert (sym["R1y>="], sym["RV1y>="], sym["MaxDD>="]) == ("≥", "<", "<")


def test_thresholds_follow_class_hot(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "hot")
    text = " ".join(_texts(_root(D.render_svg(doc))))
    for want in ("< 60%", "< 40%", "≥ 25%"):
        assert want in text
    assert "35%" not in text and "12%" not in text


def _md_step_threshold_pcts(md: str) -> set[float]:
    table = md.split("## 七步逐步表", 1)[1].split("## ", 1)[0]
    cells = [ln.split("|")[4] for ln in table.splitlines()
             if ln.startswith("| ") and "---" not in ln]
    return {round(float(x), 6) for c in cells for x in PCT.findall(c)}


def test_every_threshold_in_svg_appears_in_md_steps(monkeypatch, tmp_path):
    doc, md = _case(monkeypatch, tmp_path, "base")
    found = {round(float(x), 6) for t in _texts(_root(D.render_svg(doc)))
             for x in PCT_AFTER_CMP.findall(t)}
    assert {12.0, 35.0, 25.0, 70.0, 10.0} <= found
    assert found <= _md_step_threshold_pcts(md)


def test_rank_line_csp_first_base(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    assert D.rank_line(doc) == "占组合约 5% ≤ 10% → 现金担保 Put 排前"
    lines = D.leaf_lines(doc)
    assert lines[0] == "① 现金担保 Put   ② Bull Put 价差"
    assert "参考短 Put ≈ 92.56（粗略）" in lines
    texts = _texts(_root(D.render_svg(doc)))
    assert "占组合约 5% ≤ 10% → 现金担保 Put 排前" in texts
    assert "参考短 Put ≈ 92.56（粗略）" in texts


def test_rank_line_rounds_to_10_but_result_decides(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    out = copy.deepcopy(doc)
    csp, bp = out["picks"]
    csp["flags"] = ["portfolio_share=0.10"]
    out["picks"] = [dict(bp, rank=1), dict(csp, rank=2)]
    assert D.rank_line(out) == "占组合约 10% > 10% → Bull Put 价差 排前"


def test_rank_line_class_only(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "class_only")
    assert D.rank_line(doc) == "组合总值取不到，只按类别 → 现金担保 Put 排前"


def test_rank_line_hot_not_preferred(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "hot")
    assert D.rank_line(doc) == f"类别 {S.CLASS_LABELS['hot']}不在优先类 → Bull Put 价差 排前"


def test_iv_rv_vs_rv_method_line(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "rv_vs_rv")
    assert doc["iv"]["method"] == R.IV_METHOD_FALLBACK and doc["iv"]["label"] == "mid"
    lines = D.iv_lines(doc)
    assert lines[0] == "IV 中" and "30% < 65.8% < 70%" in lines
    assert "平值 IV 缺失" in lines and "RV21 自比（再退一步的代理）" in lines
    on = _on_texts(_root(D.render_svg(doc)))
    assert "30% < 65.8% < 70%" in on and any("再退一步的代理" in t for t in on)


def test_iv_missing_reference_not_given(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "rv_vs_rv")
    assert "IV 缺失，参考价位不给" in D.leaf_lines(doc)
    assert not any(t.startswith("参考") for t in _texts(_root(D.render_svg(doc))))


def test_slope_unknown_writes_unknown(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "V10")
    assert doc["metrics"]["n_days"] == 219 and doc["metrics"]["ma200_slope"] is None
    lines = D.regime_lines(doc)
    want = f"MA200 斜率未知（历史 219 < {doc['thresholds_used']['MIN_SLOPE_DAYS']} 日）"
    assert lines[0] == "不明" and want in lines
    assert not any(ln.startswith("MA200 斜率 ") for ln in lines)
    assert not any(ln.startswith("MA50") for ln in lines)


# Step 3: special leaves and privacy

def test_gate_fail_v6_chain(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "V6")
    assert D.gate_lines(doc) == ("日均成交额 $12.0M < $20.0M",)
    assert D.subtitle(doc)[0] == "停在：① 流动性门槛"
    root = _root(D.render_svg(doc))
    on = _on_texts(root)
    assert "日均成交额 $12.0M < $20.0M" in on and "不做期权建议" in on
    assert {"② 历史长度", "③ 看法与持有", "④ 决策树"} <= set(_texts(_off_group(root)))
    assert not any("IV" in t or "参考" in t for t in _texts(root))


def test_gate_fail_lists_unchecked_count_and_none_value(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "V6")
    out = copy.deepcopy(doc)
    items = out["gate"]["items"]
    items[2].update(value=None, result="fail")
    items[4].update(value=0, result="fail")
    for i in items[5:]:
        i["result"] = "unchecked"
    assert D.gate_lines(out) == ("股价 未取到", "日均成交额 $12.0M < $20.0M", "没有期权数据",
                                 "未检查 4 项")
    for i in items:
        i["result"] = "fail"
    lines = D.gate_lines(out)
    assert len(lines) == 6 and lines[-1] == "另 4 项不过"


def test_history_short_chain(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "history_short")
    chain = D.chain_lines(doc)
    assert chain[0] == ("① 流动性门槛", "全部通过")
    limit = doc["thresholds_used"]["MIN_HISTORY_DAYS"]
    assert chain[1] == ("② 历史长度", f"交易日 150 < {limit}，不分类、不给策略")
    on = " ".join(_on_texts(_root(D.render_svg(doc))))
    assert "全部通过" in on and "交易日 150 <" in on


def test_bear_held_v7(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "V7")
    assert D.leaf_lines(doc)[0] == "① 考虑减仓   ② Collar"
    root = _root(D.render_svg(doc))
    leaf, = [g for g in _on_boxes(root) if g.get("data-leaves")]
    assert leaf.find(f"{SVG_NS}rect").get("fill") == V.COLORS["neutral"].fill == "#efeeea"
    assert "① 考虑减仓   ② Collar" in _texts(leaf)
    assert not any(re.search(r"(?<![A-Za-z\d.])150(?![\d%])", t) for t in _texts(root))


def test_bear_not_held_held_no_lot_no_collar(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "bear_no_lot")
    assert doc["leaf_id"] == "bear.not_held"
    assert f"持股不足 {R.MIN_LOT_SHARES} 股" in D.root_lines(doc)
    on = _on_texts(_root(D.render_svg(doc)))
    assert any("考虑减仓" in t for t in on) and not any("Collar" in t for t in on)


@pytest.mark.parametrize("name,view", [("V9", "neutral"), ("bull_no_lot", "bull")])
def test_held_no_lot_bull_uses_not_held_subtree_v9(monkeypatch, tmp_path, name, view):
    doc, _ = _case(monkeypatch, tmp_path, name)
    assert doc["leaf_id"].startswith(f"{view}.not_held.")
    assert D.root_lines(doc)[1:3] == (f"持股不足 {R.MIN_LOT_SHARES} 股", "按未持有")
    assert D.path_of(doc).branch == "not_held"
    assert "未持有）" in _root(D.render_svg(doc)).find(f"{SVG_NS}title").text


def test_earnings_imminent_v8(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "V8")
    lines = D.leaf_lines(doc)
    assert lines[0] == "① 等财报后再看   ② Bull Put 价差"
    assert "财报 5 天内，先等财报（原第二 现金担保 Put 让位）" in lines
    assert "叶子表：现金担保 Put / Bull Put 价差" in lines
    assert D.rank_line(doc) is None


def test_etf_ex_div_flag(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, load_info={"market_cap": None, "quote_type": "ETF"},
            read_thesis=thesis(status="use", is_etf_page=True), read_holding=_rows(150))
    # ETF VOL_HIGH equals the stub's 30% RV1y; a calmer year keeps the covered-call leaf.
    calm = M.compute_metrics
    monkeypatch.setattr(M, "compute_metrics", lambda *a, **k: replace(calm(*a, **k), rv1y=0.2))
    monkeypatch.setattr(CLI, "ex_dividend", lambda *a: date(2026, 11, 6))
    assert run(["ZZQE"]) == 0
    doc = json.loads(outputs(tmp_path, "ZZQE")[1].read_text(encoding="utf-8"))
    assert doc["asset_class"] == "etf" and doc["picks"][0]["strategy"] == "covered_call"
    lines = D.leaf_lines(doc)
    assert any("除息日在窗口内" in ln for ln in lines)
    assert not any("财报" in ln for ln in lines)


@pytest.mark.parametrize("name", ["base", "V1"])
def test_no_portfolio_value_anywhere(monkeypatch, tmp_path, name):
    doc, _ = _case(monkeypatch, tmp_path, name)
    svg = D.render_svg(doc)
    for needle in ("200000", "200,000", "200.0K", "$200"):
        assert needle not in svg


@pytest.mark.parametrize("name,counts", [("V1", ("150",)), ("V7", ("150",)),
                                         ("V9", ("60", "50", "110"))])
def test_no_share_counts(monkeypatch, tmp_path, name, counts):
    doc, _ = _case(monkeypatch, tmp_path, name)
    texts = _texts(_root(D.render_svg(doc))) + [D.diagram_alt(doc)]
    for n in counts:
        pat = re.compile(rf"(?<![A-Za-z\d.]){n}(?![\d%])")
        assert not any(pat.search(t) for t in texts), n


def test_no_thesis_text(monkeypatch, tmp_path):
    sentinel = "ZZSENTINEL thesis words"
    for name, status in (("base", "active"), ("V7", "avoid")):
        doc, _ = _case(monkeypatch, tmp_path, name,
                       read_thesis=thesis(status=status, verdict_text=sentinel))
        assert doc["thesis_verdict_text"] == sentinel
        assert "ZZSENTINEL" not in D.render_svg(doc) and "ZZSENTINEL" not in D.diagram_alt(doc)


def test_escaping_hostile_labels(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    hostile_s, hostile_r = 'C<&">P', 'R<&">G'
    monkeypatch.setitem(T.STRATEGY_LABELS, "cash_secured_put", hostile_s)
    monkeypatch.setattr(D, "REGIME_TITLES", {**D.REGIME_TITLES, "range": hostile_r})
    hostile_v, hostile_h = 'V<&">W', 'H<&">N'
    monkeypatch.setitem(S.VIEW_LABELS, "bull", hostile_v)
    monkeypatch.setitem(S.HELD_LABELS, "not_held", hostile_h)
    svg = D.render_svg(doc)
    joined = " ".join(_texts(_root(svg)))
    assert hostile_s in joined and hostile_r in joined and hostile_v in joined
    assert hostile_s in _root(svg).find(f"{SVG_NS}desc").text
    title = _root(svg).find(f"{SVG_NS}title").text
    assert hostile_v in title and hostile_h in title


def test_dotted_ticker_title_parses(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base", argv=("ZZ.Q",))
    root = _root(D.render_svg(doc))
    assert root.find(f"{SVG_NS}title").text == "ZZ.Q 的决策路径（看好 × 未持有）"


def test_root_subtitle_footer_base(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    assert D.root_lines(doc) == ("看好", "未持有", "来源：wiki")
    sub = D.subtitle(doc)
    assert sub[0] == ("已通过：流动性门槛 · 历史 252 日 ≥ 200 · 类别 大盘蓝筹 · 看法 看好（wiki）"
                      f" · 持有 = 单账户 ≥ {R.MIN_LOT_SHARES} 股")
    assert sub[1].startswith("高亮 = 本次走过的路径")
    foot = D.footer(doc)
    assert "231 个 RV21 窗口" in foot[0]
    assert "行情快照 2026-10-23 · 运行日 2026-10-25" in foot
    assert "警告 1 条，见报告「警告」一节" in foot and foot[-1] == "不构成投资建议。"
    doc_v1, _ = _case(monkeypatch, tmp_path, "V1")
    assert D.root_lines(doc_v1) == ("看好", "已持有", "来源：wiki")
    assert "数据源" not in " ".join(foot)


# Step 4: determinism and bytes

def test_render_svg_same_bytes_twice(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    assert D.render_svg(doc) == D.render_svg(copy.deepcopy(doc))


def _reverse_keys(value):
    if isinstance(value, dict):
        return {k: _reverse_keys(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_reverse_keys(v) for v in value]
    return value


@pytest.mark.parametrize("name", ["base", "V6", "V7"])
def test_render_svg_reversed_key_order_same_bytes(monkeypatch, tmp_path, name):
    doc, _ = _case(monkeypatch, tmp_path, name)
    rev = _reverse_keys(doc)
    assert list(rev) == list(reversed(list(doc)))
    assert D.render_svg(rev) == D.render_svg(doc)


@pytest.mark.parametrize("name", sorted(CASES))
def test_bytes_lf_no_bom_trailing_newline_under_cap(monkeypatch, tmp_path, name):
    doc, _ = _case(monkeypatch, tmp_path, name)
    raw = D.render_svg(doc).encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    assert raw.endswith(b"</svg>\n") and not raw.endswith(b"\n\n")
    assert len(raw) < V.SIZE_CAP
    _root(raw.decode("utf-8"))


def _module_sources() -> list[str]:
    import strategy_pick_diagram_frame as DF
    import strategy_pick_diagram_text as DT
    return [open(m.__file__, encoding="utf-8").read() for m in (D, DT, DF)]


def test_no_threshold_literals_in_diagram_module():
    for src in _module_sources():
        for lit in ("0.35", "0.25", "0.12", "0.70", "0.30", "0.10", "0.15", "0.20"):
            assert lit not in src, lit
        assert not re.search(r"(?<![A-Za-z\d.])(100|200|220|252)(?!\d)", src)


def test_diagram_module_imports_no_io():
    for src in _module_sources():
        for needle in ("open(", "Path(", "date.today", "datetime.now", "requests", "urllib",
                       "socket"):
            assert needle not in src, needle


# ---- review fixes: data-source label, thresholds from the result, pinned columns, wrapping ----

@pytest.mark.parametrize("label", ["Yahoo", "Demo"])
def test_footer_source_label_passed_by_caller(monkeypatch, tmp_path, label):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    assert not any("数据源" in t for t in _texts(_root(D.render_svg(doc))))
    assert f"行情快照 2026-10-23 · 运行日 2026-10-25 · 数据源 {label}" in D.footer(doc, label)
    assert f"数据源 {label}" in " ".join(_texts(_root(D.render_svg(doc, source_label=label))))


def test_iv_thresholds_read_from_result(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    monkeypatch.setitem(doc["thresholds_used"], "IV_HIGH_PCTL", 0.74)
    assert "百分位 75.3% ≥ 74%" in _on_texts(_root(D.render_svg(doc)))
    mid, _ = _case(monkeypatch, tmp_path, "rv_vs_rv")
    monkeypatch.setitem(mid["thresholds_used"], "IV_LOW_PCTL", 0.31)
    monkeypatch.setitem(mid["thresholds_used"], "IV_HIGH_PCTL", 0.69)
    assert "31% < 65.8% < 69%" in D.iv_lines(mid)


def test_iv_threshold_text_matches_md(monkeypatch, tmp_path):
    import strategy_pick_render as RENDER
    doc, _ = _case(monkeypatch, tmp_path, "base")
    monkeypatch.setitem(doc["thresholds_used"], "IV_HIGH_PCTL", 0.725)
    want = f"≥ {S.pct(0.725, 0)}"
    assert f"百分位 75.3% {want}" in D.iv_lines(doc)
    assert f"{want} 记高" in RENDER.render_markdown(doc)


def test_csp_share_threshold_read_from_result(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    monkeypatch.setitem(doc["thresholds_used"], "CSP_MAX_PORTFOLIO_SHARE", 0.12)
    assert D.rank_line(doc) == "占组合约 5% ≤ 12% → 现金担保 Put 排前"


def test_min_slope_days_read_from_result(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "V10")
    monkeypatch.setitem(doc["thresholds_used"], "MIN_SLOPE_DAYS", 230)
    assert "MA200 斜率未知（历史 219 < 230 日）" in D.regime_lines(doc)


def _box_x(root, needle: str) -> float:
    g = next(g for g in _on_boxes(root) if any(t.startswith(needle) for t in _texts(g)))
    return float(g.find(f"{SVG_NS}rect").get("x"))


def test_pinned_columns_layout_t(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    root = _root(D.render_svg(doc))
    assert _box_x(root, "看好") == 12.0
    assert _box_x(root, "稳步上涨") == 146.0
    assert _box_x(root, "IV 高") == 420.0
    assert _box_x(root, "① 现金担保 Put") == 616.0


def test_pinned_columns_layout_c(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "history_short")
    root = _root(D.render_svg(doc))
    xs = []
    for mark in "①②③④":
        g = next(g for g in root.iter(G_TAG) if g.get("class", "").startswith("spk-node")
                 and any(t.startswith(mark) for t in _texts(g)))
        xs.append(float(g.find(f"{SVG_NS}rect").get("x")))
    assert xs == [12.0, 252.0, 492.0, 732.0]


def test_control_chars_and_long_ticker(monkeypatch, tmp_path):
    doc, _ = _case(monkeypatch, tmp_path, "base")
    bad = dict(copy.deepcopy(doc), ticker="ZZ\x01Q\r")
    svg = D.render_svg(bad)
    assert "\x01" not in svg and "\r" not in svg
    assert _root(svg).find(f"{SVG_NS}title").text.startswith("ZZQ")
    long = dict(copy.deepcopy(doc), ticker="Z" * 300)
    root = _root(D.render_svg(long))
    shown = _texts(root)[0]
    assert shown.endswith("…")
    assert V.text_width(shown, V.TITLE_SIZE) <= V.CANVAS_W - 2 * V.TITLE_X


def _seq(texts: list[str], *lines: str) -> bool:
    n = len(lines)
    return any(tuple(texts[i:i + n]) == lines for i in range(len(texts)))


@pytest.mark.parametrize("name,lines", [
    ("V1", ("看好", "已持有", "来源：wiki")),
    ("bear_no_lot", ("持股不足", "100 股")),
    ("base", ("① 只在真想接股时做；", "远价外（Delta 0.10–0.15）")),
    ("V7", ("参考长 Put ≈ 92.56–95.39；", "短 Call ≈ 107.44（粗略）")),
    ("V8", ("财报 5 天内，", "先等财报（原第二 现金担保 Put 让位）")),
    ("hot", ("类别 热门赛道热门股不在优先类", "→ Bull Put 价差 排前")),
])
def test_six_awkward_wraps_fixed(monkeypatch, tmp_path, name, lines):
    doc, _ = _case(monkeypatch, tmp_path, name)
    assert _seq(_texts(_root(D.render_svg(doc))), *lines)
