"""Strategy-agnostic SVG layout primitives for the strategy-pick decision path.

Pure string building: attribute order is written by hand so output bytes never
depend on an XML library version.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from xml.sax.saxutils import escape

CANVAS_W = 958
COL_ROOT = (12, 96)
COL_REGIME = (146, 236)
COL_IV = (420, 156)
COL_LEAF = (616, 330)
CHAIN_XS = (12, 252, 492, 732)
CHAIN_W = 214
TREE_TOP = 100
BAND_GAP = 10
LINE_H = 20
BOX_PAD_TOP = 26
BOX_BASE_H = 22
TEXT_PAD = 20
FONT_STACK = ("system-ui,-apple-system,'Segoe UI','Microsoft YaHei',"
              "'PingFang SC',Helvetica,Arial,sans-serif")
ROOT_FONT_SIZE = 15
MAX_LINES_ON = 7
MAX_LINES_OFF = 3
SIZE_CAP = 65536
TITLE_X, TITLE_Y, TITLE_SIZE, TITLE_WEIGHT = 14, 28, 18, 600
SUB_YS = (54, 76)
SUB_SIZE = 14
SUB_COLOR = "#6f6e69"
TITLE_COLOR = "#2b2a27"
DETAIL_SIZE = 13.5
FOOT_GAP = 40  # tree bottom to first footer baseline
CHAIN_LEAF_GAP = 40  # layout C: stop box bottom to the hanging "no advice" leaf
# Layout C value lines: a 214-wide box fits "日均成交额 $12.0M < $20.0M" on one line only at 13.
CHAIN_DETAIL_SIZE = 13
# On-path IV values: the 156-wide IV column fits "百分位 75.3% ≥ 70%" on one line only at 12.
IV_DETAIL_SIZE = 12
FOOT_PAD = 18  # last footer baseline to canvas bottom

WIDE_CLASSES = frozenset("WFA")
WIDE_EM = 1.0
NARROW_EM = 0.6
BREAK_AFTER_PUNCT = frozenset("；，、")  # best break: after these ...
BREAK_BEFORE = frozenset("→")  # ... or before an arrow clause; then spaces; then anywhere
NO_LINE_START = frozenset("；，、）)：。")
GROUPS = {"（": "）", "(": ")"}  # kept on one line when the group fits a line by itself
GLUE_SYMBOLS = frozenset("≥≤<>≈→")  # never parted from the token that follows
MIN_LINE_SHARE = 1 / 4  # a preferred break may not leave a line shorter than this share
# XML 1.0 forbids these; carriage returns are normalised to line feeds.
CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
ELLIPSIS = "…"

BOX_RADIUS = 8
CANVAS_RADIUS = 10
CANVAS_FILL = "#ffffff"
CANVAS_STROKE = "#e2e0da"
STROKE_OFF_W = 1.3
STROKE_ON_W = 2.8
EDGE_ON_W = 3
ARROW_W = 1.6
ARROW_HEAD = 8
ARROW_HALF = 5

PATH_STROKE = "#c2410c"
PATH_FILL = "#fff4ec"
PATH_VALUE = "#7a2e0a"
OFF_STROKE = "#a8a69e"
OFF_OPACITY = "0.4"


@dataclass(frozen=True)
class Palette:
    fill: str
    stroke: str
    text: str


COLORS: dict[str, Palette] = {
    "decision": Palette("#f7f6f2", "#c9c7bf", "#2b2a27"),
    "sell": Palette("#e8f0fb", "#2a78d6", "#12365f"),
    "buy": Palette("#e4f6ee", "#1baf7a", "#0b4a33"),
    "range": Palette("#f4ecfa", "#8a55c2", "#3d2159"),
    "protect": Palette("#fdf0e1", "#eb6834", "#6b2f10"),
    "neutral": Palette("#efeeea", "#9a9892", "#4a4944"),
}


@dataclass(frozen=True)
class Line:
    text: str
    size: float
    weight: int
    color: str


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    lines: tuple[Line, ...]
    palette: Palette
    on: bool
    leaves: tuple[str, ...] = ()

    @property
    def h(self) -> float:
        return box_height(len(self.lines))


def fmt(v: float) -> str:
    return f"{v:.1f}"


def clean(s: str) -> str:
    return CONTROL_CHARS.sub("", s.replace("\r\n", "\n").replace("\r", "\n"))


def esc(s: str) -> str:
    return escape(clean(s))


def esc_attr(s: str) -> str:
    return escape(clean(s), {'"': "&quot;"})


def fit(s: str, width: float, size: float) -> str:
    """One line that never runs past `width`: ellipsized when too long."""
    s = clean(s)
    return s if text_width(s, size) <= width else ellipsize(s, width, size)


def text_width(s: str, size: float) -> float:
    # Ambiguous-width glyphs (circled digits, comparison signs) render wide in CJK fonts.
    return sum(WIDE_EM if unicodedata.east_asian_width(c) in WIDE_CLASSES else NARROW_EM
               for c in s) * size


def _wide(c: str) -> bool:
    return unicodedata.east_asian_width(c) in WIDE_CLASSES


def _atoms(s: str, width: float, size: float) -> list[str]:
    """Unbreakable pieces: a bracket group that fits a line, an ASCII run, one wide char, a
    space. Punctuation that may not start a line rides on the piece before it."""
    out: list[str] = []
    i = 0
    while i < len(s):
        ch, j = s[i], i + 1
        close = s.find(GROUPS[ch], i + 1) if ch in GROUPS else -1
        if close != -1 and text_width(s[i:close + 1], size) <= width:
            j = close + 1
        elif ch != " " and not _wide(ch):
            while j < len(s) and s[j] != " " and not _wide(s[j]) and s[j] not in GROUPS:
                j += 1
        piece = s[i:j]
        if out and piece in NO_LINE_START:
            out[-1] += piece
        elif text_width(piece, size) > width:
            out += list(piece)
        else:
            out.append(piece)
        i = j
    return _glue(out, width, size)


def _glue(atoms: list[str], width: float, size: float) -> list[str]:
    """A comparison / approx / arrow symbol stays with the token after it ("≥ 12%")."""
    out: list[str] = []
    i = 0
    while i < len(atoms):
        a = atoms[i]
        if a in GLUE_SYMBOLS and i + 2 < len(atoms) and atoms[i + 1] == " ":
            glued = a + " " + atoms[i + 2]
            if text_width(glued, size) <= width:
                out.append(glued)
                i += 3
                continue
        out.append(a)
        i += 1
    return out


def _break_rank(cur: list[str], k: int, nxt: str) -> int:
    """How good a break before cur[k] (or before nxt when k == len(cur)) is: 2 clause, 1 space."""
    after = cur[k] if k < len(cur) else nxt
    before = cur[k - 1]
    if before[-1] in BREAK_AFTER_PUNCT or after[0] in BREAK_BEFORE:
        return 2
    return 1 if " " in (before, after) else 0


def _cut(cur: list[str], nxt: str, width: float, size: float) -> int:
    best, best_rank = len(cur), -1
    for k in range(1, len(cur) + 1):
        rank = _break_rank(cur, k, nxt)
        if rank >= best_rank and text_width("".join(cur[:k]).strip(" "), size) >= \
                MIN_LINE_SHARE * width:
            best, best_rank = k, rank
    return best


def _fix_orphans(lines: list[str]) -> list[str]:
    """Never leave one CJK character alone: pull the previous line's last character down."""
    out = list(lines)
    for i in range(1, len(out)):
        prev = out[i - 1]
        if len(out[i]) == 1 and _wide(out[i]) and len(prev) > 1 and _wide(prev[-1]) \
                and prev[-1] not in BREAK_AFTER_PUNCT:
            out[i - 1], out[i] = prev[:-1], prev[-1] + out[i]
    return out


def _split(s: str, width: float, size: float) -> list[str]:
    lines: list[str] = []
    cur: list[str] = []
    for atom in _atoms(s, width, size):
        while cur and text_width("".join(cur) + atom, size) > width:
            k = _cut(cur, atom, width, size)
            lines.append("".join(cur[:k]).strip(" "))
            cur = cur[k:]
            while cur and cur[0] == " ":
                cur.pop(0)
        if atom == " " and not cur:
            continue
        cur.append(atom)
    if "".join(cur).strip(" "):
        lines.append("".join(cur).strip(" "))
    return _fix_orphans([ln for ln in lines if ln])


def ellipsize(s: str, width: float, size: float) -> str:
    s = s.rstrip()
    while s and text_width(s + ELLIPSIS, size) > width:
        s = s[:-1]
    return s + ELLIPSIS


def wrap(s: str, width: float, size: float, max_lines: int) -> tuple[str, ...]:
    lines = _split(s, width, size)
    if len(lines) <= max_lines:
        return tuple(lines)
    return tuple(lines[:max_lines - 1]) + (ellipsize(lines[max_lines - 1], width, size),)


def box_height(n_lines: int) -> float:
    return float(BOX_BASE_H + LINE_H * n_lines)


def text_el(x: float, y: float, s: str, size: float, color: str,
            weight: int = 400, anchor: str | None = None) -> str:
    parts = [f'<text x="{fmt(x)}" y="{fmt(y)}"']
    if anchor:
        parts.append(f' text-anchor="{anchor}"')
    parts.append(f' fill="{esc_attr(color)}" font-size="{size:g}"')
    if weight != 400:
        parts.append(f' font-weight="{weight}"')
    return "".join(parts) + f">{esc(s)}</text>"


def box_svg(b: Box) -> str:
    cls = "spk-node spk-on" if b.on else "spk-node"
    attrs = f'class="{cls}"'
    if b.leaves:
        attrs += f' data-leaves="{esc_attr(" ".join(b.leaves))}"'
    if b.on:
        fill = PATH_FILL if b.palette is COLORS["decision"] else b.palette.fill
        stroke, sw = PATH_STROKE, STROKE_ON_W
    else:
        fill, stroke, sw = b.palette.fill, b.palette.stroke, STROKE_OFF_W
    out = [f"<g {attrs}>",
           f'<rect x="{fmt(b.x)}" y="{fmt(b.y)}" width="{fmt(b.w)}" height="{fmt(b.h)}" '
           f'rx="{fmt(BOX_RADIUS)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw:g}"/>']
    cx = b.x + b.w / 2
    for i, ln in enumerate(b.lines):
        out.append(text_el(cx, b.y + BOX_PAD_TOP + LINE_H * i, ln.text, ln.size,
                           ln.color, ln.weight, "middle"))
    out.append("</g>")
    return "\n".join(out)


def _path(d: str, stroke: str, width: float, on: bool, fill: str = "none") -> str:
    cls = ' class="spk-on"' if on else ""
    return f'<path{cls} d="{d}" stroke="{stroke}" stroke-width="{width:g}" fill="{fill}"/>'


def _edge_style(on: bool) -> tuple[str, float]:
    return (PATH_STROKE, EDGE_ON_W) if on else (OFF_STROKE, STROKE_OFF_W)


def elbow(x1: float, y1: float, x2: float, y2: float, on: bool) -> str:
    xm = x1 + (x2 - x1) / 2
    d = f"M{fmt(x1)} {fmt(y1)} H{fmt(xm)} V{fmt(y2)} H{fmt(x2)}"
    return _path(d, *_edge_style(on), on)


def straight(x1: float, y1: float, x2: float, y2: float, on: bool) -> str:
    d = f"M{fmt(x1)} {fmt(y1)} L{fmt(x2)} {fmt(y2)}"
    return _path(d, *_edge_style(on), on)


def arrow(x1: float, y: float, x2: float, on: bool) -> str:
    color = PATH_STROKE if on else OFF_STROKE
    line = _path(f"M{fmt(x1)} {fmt(y)} H{fmt(x2 - ARROW_HEAD)}", color, ARROW_W, on)
    head = (f"M{fmt(x2)} {fmt(y)} L{fmt(x2 - ARROW_HEAD)} {fmt(y - ARROW_HALF)} "
            f"L{fmt(x2 - ARROW_HEAD)} {fmt(y + ARROW_HALF)} Z")
    return line + "\n" + _path(head, color, 0, on, fill=color)


def document(title: str, desc: str, height: int, off: list[str], on_edges: list[str],
             on_nodes: list[str], header: list[str], footer: list[str]) -> str:
    root = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_W} {height}" '
            f'width="{CANVAS_W}" height="{height}" font-family="{esc_attr(FONT_STACK)}" '
            f'font-size="{ROOT_FONT_SIZE}" role="img" aria-labelledby="spk-title spk-desc">')
    canvas = (f'<rect width="{fmt(CANVAS_W)}" height="{fmt(height)}" rx="{fmt(CANVAS_RADIUS)}" '
              f'fill="{CANVAS_FILL}" stroke="{CANVAS_STROKE}"/>')
    parts = [root,
             f'<title id="spk-title">{esc(title)}</title>',
             f'<desc id="spk-desc">{esc(desc)}</desc>',
             canvas, *header,
             f'<g class="spk-off" opacity="{OFF_OPACITY}">', *off, "</g>",
             *on_edges, *on_nodes, *footer, "</svg>"]
    return "\n".join(parts) + "\n"
