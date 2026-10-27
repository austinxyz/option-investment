"""Decision-path diagram: tree model from LEAF_TABLE, the run's highlighted path, render_svg.

A leaf, strategy, regime, IV label or flag with no display name raises DiagramError instead of
drawing an empty box, so a table change that outruns the labels fails loudly (design D3).
render_svg reads only the result dict and constant tables; node text lives in
strategy_pick_diagram_text and strategy_pick_diagram_frame (neither imports this module).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import strategy_pick_rules as R
import strategy_pick_steps as S
import strategy_pick_svg as V
import strategy_pick_text as T
from strategy_pick_diagram_frame import (  # noqa: F401 (re-exported: the diagram's public API)
    NO_ADVICE, chain_lines, diagram_alt, diagram_title, footer, gate_lines, subtitle)
from strategy_pick_diagram_text import (  # noqa: F401 (re-exported, as above)
    CIRCLED, LAYOUT_BEAR, LAYOUT_TREE, PAIR_SEP, PICK_SEP, DiagramError, PathSpec,
    bear_branch_lines, cmp_symbol, iv_lines, leaf_lines, path_of, rank_line, regime_lines,
    root_lines)
from strategy_pick_diagram_text import lookup as _lookup

REGIME_TITLES = {"steady_up": "稳步上涨", "high_vol": "高波动", "range": "盘整",
                 "down": "下行", "unclear": "不明"}
IV_TITLES = {iv: f"IV {name}" for iv, name in S.IV_LABELS.items()}
VIEW_TITLES = {"bull": "看好", "neutral": "中性", "bear": "不看好"}
BRANCH_TITLES = {R.BRANCH_HELD: "已持有", R.BRANCH_NOT_HELD: "未持有"}
BEAR_BRANCHES = (R.BRANCH_HELD, R.BRANCH_NOT_HELD)
STRATEGY_FAMILY = {
    R.SELL_PAIR: "sell", "cash_secured_put": "sell", "bull_put_spread": "sell",
    "covered_call": "sell", "bull_call_spread": "buy", "iron_condor": "range",
    "collar": "protect", "protective_put": "protect", "hold": "neutral",
    "no_options": "neutral", "wait_earnings": "neutral", "reduce_position": "neutral",
}
FAR_OTM_FLAG = "far_otm"
RANK_NOTE_LINE = "按占比和类别排序"
DELTA_SEP = "–"
FOOTER_TEXT = "不构成投资建议。"
IV_PREFIX = "IV "


@dataclass(frozen=True)
class IvGroup:
    ivs: tuple[str, ...]
    leaves: tuple[str, ...]
    picks: tuple[R.LeafPick, ...]
    warns: tuple[str, ...]


@dataclass(frozen=True)
class PathText:
    """Node text of the highlighted path in layout T (root, regime, IV, leaf)."""
    root: tuple[str, ...]
    regime: tuple[str, ...]
    iv: tuple[str, ...]
    leaf: tuple[str, ...]


@dataclass(frozen=True)
class TreeLayout:
    boxes: tuple[V.Box, ...]
    edges: tuple[str, ...]          # not on the path (drawn inside the faded group)
    height: int
    on_edges: tuple[str, ...] = ()  # on the path


def _signature(picks: tuple[R.LeafPick, ...], warns: tuple[str, ...]) -> tuple:
    # Whole picks (plan and flags included): two IV leaves merge only when they are identical.
    return (tuple(picks), tuple(warns))


def iv_groups(view: str, branch: str, regime: str) -> tuple[IvGroup, ...]:
    _lookup(REGIME_TITLES, regime, "regime")
    order: list[tuple] = []
    members: dict[tuple, list[tuple[str, str]]] = {}
    store: dict[tuple, tuple] = {}
    for iv in R.IV_LABELS:
        _lookup(IV_TITLES, iv, "IV label")
        key = f"{view}.{branch}.{regime}.{iv}"
        picks, warns = _lookup(R.LEAF_TABLE, key, "leaf")
        sig = _signature(picks, warns)
        if sig not in members:
            order.append(sig)
            store[sig] = (tuple(picks), tuple(warns))
        members.setdefault(sig, []).append((iv, key))
    return tuple(IvGroup(tuple(i for i, _ in members[s]), tuple(k for _, k in members[s]),
                         *store[s]) for s in order)


def leaf_family(picks: tuple[R.LeafPick, ...]) -> str:
    return _lookup(STRATEGY_FAMILY, picks[0].strategy, "strategy colour family")


def _strategy_label(strategy: str) -> str:
    if strategy == R.SELL_PAIR:
        return PAIR_SEP.join(_lookup(T.STRATEGY_LABELS, k, "strategy")
                             for k in (R.CSP_KEY, R.BULL_PUT_KEY))
    _lookup(STRATEGY_FAMILY, strategy, "strategy colour family")
    return _lookup(T.STRATEGY_LABELS, strategy, "strategy")


def _flag_text(flag: str) -> str:
    if flag == FAR_OTM_FLAG:
        lo, hi = R.FAR_OTM_DELTA
        return f"{_lookup(T.FLAG_LABELS, flag, 'flag')}（Delta {lo:.2f}{DELTA_SEP}{hi:.2f}）"
    return _lookup(T.FLAG_LABELS, flag, "flag")


def _pick_flags(pick: R.LeafPick) -> tuple[str, ...]:
    shown = pick.flags + R.PLAN_FLAGS.get(pick.plan, ())
    return tuple(_flag_text(f) for f in dict.fromkeys(shown) if f not in T.HIDDEN_FLAGS)


def _warning_text(key: str) -> str:
    text = _lookup(T.WARN, key, "warning")
    if "{" in text:
        raise DiagramError(f"warning {key!r} needs values, so it cannot be a static leaf line")
    return text


def static_leaf_lines(group: IvGroup) -> tuple[str, ...]:
    picks = group.picks
    if len(picks) > len(CIRCLED):
        raise DiagramError("more picks than circled digits")
    labels = [_strategy_label(p.strategy) for p in picks]
    if len(picks) == 1:
        lines = [labels[0]]
    else:
        lines = [PICK_SEP.join(f"{CIRCLED[i]} {lab}" for i, lab in enumerate(labels))]
    if any(p.strategy == R.SELL_PAIR for p in picks):
        lines.append(RANK_NOTE_LINE)
    for i, p in enumerate(picks):
        flags = _pick_flags(p)
        if flags:
            prefix = f"{CIRCLED[i]} " if len(picks) > 1 else ""
            lines.append(prefix + "；".join(flags))
    return tuple(lines) + tuple(_warning_text(w) for w in group.warns)


def _box_lines(texts: tuple[str, ...], width: float, cap: int, color: str,
               value_color: str | None = None,
               value_size: float = V.DETAIL_SIZE) -> tuple[V.Line, ...]:
    inner = width - V.TEXT_PAD
    out: list[V.Line] = []
    for i, text in enumerate(texts):
        size = V.ROOT_FONT_SIZE if i == 0 else value_size
        weight = 600 if i == 0 else 400
        tint = color if i == 0 or value_color is None else value_color
        out += [V.Line(s, size, weight, tint) for s in V.wrap(text, inner, size, cap)]
    if len(out) > cap:
        last = out[cap - 1]
        out = out[:cap - 1] + [replace(last, text=V.ellipsize(last.text, inner, last.size))]
    return tuple(out)


def _node(x: float, w: float, texts: tuple[str, ...], family: str, leaves=(),
          cap: int = V.MAX_LINES_OFF, on: bool = False,
          value_size: float = V.DETAIL_SIZE) -> V.Box:
    pal = V.COLORS[family]
    if on:
        lines = _box_lines(texts, w, V.MAX_LINES_ON, pal.text, V.PATH_VALUE, value_size)
    else:
        lines = _box_lines(texts, w, cap, pal.text, None, value_size)
    return V.Box(x, 0.0, w, lines, pal, on, leaves)


def _leaf_box(group: IvGroup, lines: tuple[str, ...] | None = None) -> V.Box:
    x, w = V.COL_LEAF
    # Leaf text is the one place the 3-line off-path cap cannot hold: the far-OTM Delta range,
    # the want-shares flag and the leaf warning together need four lines.
    texts = static_leaf_lines(group) if lines is None else lines
    return _node(x, w, texts, leaf_family(group.picks), group.leaves, V.MAX_LINES_ON,
                 on=lines is not None)


def _iv_box(group: IvGroup, lines: tuple[str, ...] | None = None) -> V.Box:
    x, w = V.COL_IV
    names = PAIR_SEP.strip().join(S.IV_LABELS[i] for i in group.ivs)
    return _node(x, w, (IV_PREFIX + names,) if lines is None else lines, "decision",
                 on=lines is not None, value_size=V.IV_DETAIL_SIZE)


def _sub_bands(view: str, branch: str, regime: str, path, text) -> list[tuple]:
    groups = iv_groups(view, branch, regime)
    split = len(groups) > 1
    on_regime = path is not None and path.regime == regime
    subs = []
    for g in groups:
        on = on_regime and path.iv in g.ivs
        leaf = _leaf_box(g, text.leaf if on else None)
        iv = _iv_box(g, text.iv if on else None) if split else None
        subs.append((leaf, iv, max(leaf.h, iv.h if iv else 0.0), on))
    return subs


def _band_parts(leaf: V.Box, iv: V.Box | None, yc: float, nc: float, on: bool):
    rx = V.COL_REGIME[0] + V.COL_REGIME[1]
    leaf = replace(leaf, y=yc - leaf.h / 2)
    if iv is None:
        return [leaf], [(V.straight(rx, nc, V.COL_LEAF[0], yc, on), on)]
    iv = replace(iv, y=yc - iv.h / 2)
    return [leaf, iv], [(V.elbow(rx, nc, V.COL_IV[0], yc, on), on),
                        (V.elbow(V.COL_IV[0] + V.COL_IV[1], yc, V.COL_LEAF[0], yc, on), on)]


def _regime_block(view: str, branch: str, regime: str, top: float, path=None, text=None):
    subs = _sub_bands(view, branch, regime, path, text)
    tops, acc = [], 0.0
    for s in subs:
        tops.append(acc)
        acc += s[2] + V.BAND_GAP
    stack_h = acc - V.BAND_GAP
    cs = [t + s[2] / 2 for t, s in zip(tops, subs)]
    on = path is not None and path.regime == regime
    texts = text.regime if on else (REGIME_TITLES[regime],)
    node = _node(V.COL_REGIME[0], V.COL_REGIME[1], texts, "decision", on=on)
    node_top = (cs[0] + cs[-1]) / 2 - node.h / 2
    shift = max(0.0, -node_top)
    base = top + shift
    node = replace(node, y=base + node_top)
    boxes, edges = [node], []
    nc = node.y + node.h / 2
    for (leaf, iv, _, band_on), c in zip(subs, cs):
        parts, band_edges = _band_parts(leaf, iv, base + c, nc, band_on)
        boxes += parts
        edges += band_edges
    bottom = top + max(stack_h + shift, node_top + shift + node.h)
    return boxes, edges, nc, bottom


def _check_prefix(view: str, branch: str) -> None:
    prefix = f"{view}.{branch}."
    keys = [k for k in R.LEAF_TABLE if k.startswith(prefix)]
    if not keys:
        raise DiagramError(f"no leaves under {prefix!r}")
    for key in keys:
        parts = key.split(".")
        if len(parts) != 4:
            raise DiagramError(f"leaf id {key!r} is not view.branch.regime.iv")
        _lookup(REGIME_TITLES, parts[2], "regime")
        _lookup(IV_TITLES, parts[3], "IV label")
        if parts[2] not in R.REGIME_LEAVES or parts[3] not in R.IV_LABELS:
            raise DiagramError(f"leaf id {key!r} is outside REGIME_LEAVES x IV_LABELS")


def _finish(boxes: list[V.Box], edges: list[tuple[str, bool]]) -> TreeLayout:
    return TreeLayout(tuple(boxes), tuple(e for e, on in edges if not on),
                      math.ceil(max(b.y + b.h for b in boxes)),
                      tuple(e for e, on in edges if on))


def layout_tree(view: str, branch: str, on_path: PathSpec | None = None,
                texts: PathText | None = None) -> TreeLayout:
    if (on_path is None) != (texts is None):
        raise DiagramError("a highlighted path needs its node text, and only then")
    _lookup(VIEW_TITLES, view, "view")
    _lookup(BRANCH_TITLES, branch, "branch")
    _check_prefix(view, branch)
    boxes, edges, centres, top = [], [], [], float(V.TREE_TOP)
    for regime in R.REGIME_LEAVES:
        blk, blk_edges, nc, bottom = _regime_block(view, branch, regime, top, on_path, texts)
        boxes += blk
        edges += blk_edges
        centres.append((nc, on_path is not None and on_path.regime == regime))
        top = bottom + V.BAND_GAP
    rc = (centres[0][0] + centres[-1][0]) / 2
    rx, rw = V.COL_ROOT
    root_texts = texts.root if texts else (VIEW_TITLES[view], BRANCH_TITLES[branch])
    root = _node(rx, rw, root_texts, "decision", on=on_path is not None)
    root = replace(root, y=rc - root.h / 2)
    root_edges = [(V.elbow(rx + rw, rc, V.COL_REGIME[0], c, on), on) for c, on in centres]
    return _finish([root] + boxes, root_edges + edges)


def _bear_band(result: dict, branch: str, path: PathSpec, top: float):
    key = f"{path.view}.{branch}"
    picks, warns = _lookup(R.LEAF_TABLE, key, "leaf")
    group = IvGroup((), (key,), tuple(picks), tuple(warns))
    on = branch == path.branch
    leaf = _leaf_box(group, leaf_lines(result) if on else None)
    node = _node(V.COL_REGIME[0], V.COL_REGIME[1], bear_branch_lines(result, branch, on),
                 "decision", on=on)
    h = max(leaf.h, node.h)
    c = top + h / 2
    edge = V.straight(V.COL_REGIME[0] + V.COL_REGIME[1], c, V.COL_LEAF[0], c, on)
    return [replace(node, y=c - node.h / 2), replace(leaf, y=c - leaf.h / 2)], (edge, on), c, top + h


def layout_bear(result: dict, path: PathSpec) -> TreeLayout:
    """Layout B: root -> held / not-held branch -> the two bear leaves, straight lines."""
    boxes, edges, centres, top = [], [], [], float(V.TREE_TOP)
    for branch in BEAR_BRANCHES:
        band, edge, c, bottom = _bear_band(result, branch, path, top)
        boxes += band
        edges.append(edge)
        centres.append((c, branch == path.branch))
        top = bottom + V.BAND_GAP
    rc = (centres[0][0] + centres[-1][0]) / 2
    rx, rw = V.COL_ROOT
    root = _node(rx, rw, root_lines(result), "decision", on=True)
    root = replace(root, y=rc - root.h / 2)
    root_edges = [(V.elbow(rx + rw, rc, V.COL_REGIME[0], c, on), on) for c, on in centres]
    return _finish([root] + boxes, root_edges + edges)


def layout_chain(result: dict, path: PathSpec) -> TreeLayout:
    """Layout C: gate -> history -> view/holding -> tree, stopping where the run stopped."""
    texts, stop = chain_lines(result), path.stop_step
    boxes = []
    for i, x in enumerate(V.CHAIN_XS):
        on = i < stop
        box = _node(x, V.CHAIN_W, texts[i] if on else texts[i][:1], "decision", on=on,
                    value_size=V.CHAIN_DETAIL_SIZE)
        boxes.append(replace(box, y=float(V.TREE_TOP)))
    ay = V.TREE_TOP + V.box_height(1) / 2
    edges = [(V.arrow(a.x + a.w, ay, b.x, i + 1 < stop), i + 1 < stop)
             for i, (a, b) in enumerate(zip(boxes, boxes[1:]))]
    end = boxes[stop - 1]
    leaf = _node(end.x, V.CHAIN_W, (NO_ADVICE,), "neutral", (result["leaf_id"],), on=True)
    leaf = replace(leaf, y=end.y + end.h + V.CHAIN_LEAF_GAP)
    cx = end.x + end.w / 2
    edges.append((V.straight(cx, end.y + end.h, cx, leaf.y, True), True))
    return _finish(boxes + [leaf], edges)


def _layout(result: dict, path: PathSpec) -> TreeLayout:
    if path.layout == LAYOUT_TREE:
        texts = PathText(root_lines(result), regime_lines(result), iv_lines(result),
                         leaf_lines(result))
        return layout_tree(path.view, path.branch, path, texts)
    if path.layout == LAYOUT_BEAR:
        return layout_bear(result, path)
    return layout_chain(result, path)


LINE_SPAN = V.CANVAS_W - 2 * V.TITLE_X  # header / footer lines never run off the canvas


def _header(title: str, sub: tuple[str, ...]) -> list[str]:
    head = [V.text_el(V.TITLE_X, V.TITLE_Y, title, V.TITLE_SIZE, V.TITLE_COLOR, V.TITLE_WEIGHT)]
    return head + [V.text_el(V.TITLE_X, y, V.fit(s, LINE_SPAN, V.SUB_SIZE), V.SUB_SIZE,
                             V.SUB_COLOR) for y, s in zip(V.SUB_YS, sub)]


def render_svg(result: dict, source_label: str | None = None) -> str:
    """The run's decision-path diagram; reads only the result dict and constant tables.

    `source_label` ("Yahoo" / "Demo") is passed by the caller in non-IBKR mode for the footer.
    """
    path = path_of(result)
    lay = _layout(result, path)
    title = V.fit(diagram_title(result), LINE_SPAN, V.TITLE_SIZE)
    foot_y = lay.height + V.FOOT_GAP
    lines = footer(result, source_label)
    foot = [V.text_el(V.TITLE_X, foot_y + V.LINE_H * i, V.fit(s, LINE_SPAN, V.DETAIL_SIZE),
                      V.DETAIL_SIZE, V.SUB_COLOR) for i, s in enumerate(lines)]
    height = math.ceil(foot_y + V.LINE_H * (len(lines) - 1) + V.FOOT_PAD)
    off = list(lay.edges) + [V.box_svg(b) for b in lay.boxes if not b.on]
    on_nodes = [V.box_svg(b) for b in lay.boxes if b.on]
    return V.document(title, diagram_alt(result), height, off, list(lay.on_edges), on_nodes,
                      _header(title, subtitle(result)), foot)


def render_tree(view: str, branch: str) -> str:
    lay = layout_tree(view, branch, None)
    title = f"决策树（{VIEW_TITLES[view]} × {BRANCH_TITLES[branch]}）"
    header = [V.text_el(V.TITLE_X, V.TITLE_Y, title, V.TITLE_SIZE, V.TITLE_COLOR, V.TITLE_WEIGHT)]
    foot_y = lay.height + V.FOOT_GAP
    footer_els = [V.text_el(V.TITLE_X, foot_y, FOOTER_TEXT, V.DETAIL_SIZE, V.SUB_COLOR)]
    height = math.ceil(foot_y + V.FOOT_PAD)
    return V.document(title, title, height, [], list(lay.edges),
                      [V.box_svg(b) for b in lay.boxes], header, footer_els)
