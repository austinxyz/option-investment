"""Write one /strategy-pick run: <run date>-<TICKER>.md, .json and .svg, swapped in together.

Split from strategy_pick.py (plan 2026-10-26 svg diagram, Global Constraints: files <= 400
lines). All three texts are rendered in memory first; only then are temp files written and moved
onto their targets in one `_swap_in`, which puts every previous file back if any move fails.
The decision-path diagram is the only step allowed to fail softly: `render_svg` raising any
Exception gives a report without the image line, and the same-day .svg of an earlier run is
moved away in that same swap (design 2026-10-26, degrade path).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import option_labels
import option_settings
import strategy_pick_diagram
import strategy_pick_render as render

EXTENSIONS = (".md", ".json", ".svg")
TMP_SUFFIX = ".tmp"
BAK_SUFFIX = ".bak"


@dataclass(frozen=True)
class Diagram:
    """The rendered svg text, or None with the exception class name when rendering failed."""
    svg: str | None
    error: str | None = None


def source_label() -> str | None:
    """"Yahoo" / "Demo" for the diagram footer; None in IBKR mode (IBKR output unchanged)."""
    return None if option_settings.is_ibkr() else option_labels.data_source_label()


def render_diagram(result: dict) -> Diagram:
    """Only this call is guarded; a write error (OSError) elsewhere still fails the run."""
    try:
        return Diagram(strategy_pick_diagram.render_svg(result, source_label()))
    except Exception as exc:  # noqa: BLE001 - any diagram bug degrades, the report still lands
        return Diagram(None, type(exc).__name__)


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _swap_in(pairs: list[tuple[Path, Path]], remove: list[Path] | tuple = ()) -> None:
    """Move each temp onto its target and take each `remove` path away; all or nothing.

    Renames can never be one atomic step, so every existing target (and every path to remove)
    is first moved aside to .bak and restored if any new file fails to land: the reader sees
    the old files or the new ones. The .bak files are deleted only after every move succeeded.
    """
    backups, placed = [], []
    try:
        for target in [t for _, t in pairs] + list(remove):
            if target.exists():
                bak = target.with_name(target.name + BAK_SUFFIX)
                os.replace(target, bak)
                backups.append((bak, target))
        for tmp, target in pairs:
            os.replace(tmp, target)
            placed.append(target)
    except OSError:
        for target in placed:
            target.unlink(missing_ok=True)
        for bak, target in backups:
            os.replace(bak, target)
        raise
    for bak, _ in backups:
        bak.unlink(missing_ok=True)


def output_paths(result: dict, out_dir: Path) -> tuple[Path, Path, Path]:
    """Name + extension (never with_suffix), so a dotted ticker such as BRK.B keeps its name."""
    stem = f"{result['run_date']}-{result['ticker']}"
    md, js, svg = (Path(out_dir) / (stem + ext) for ext in EXTENSIONS)
    return md, js, svg


def write_outputs(result: dict, out_dir: Path,
                  diagram: Diagram | None = None) -> tuple[Path, Path, Path | None]:
    """md, json and (unless the diagram failed) svg; a failed write leaves the previous files."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, json_path, svg_path = output_paths(result, out_dir)
    diagram = diagram if diagram is not None else render_diagram(result)
    has_svg = diagram.svg is not None
    texts = [(md_path, render.render_markdown(result, svg_path.name if has_svg else None)),
             (json_path, render.render_json(result))] + ([(svg_path, diagram.svg)] if has_svg else [])
    pairs = [(path.with_name(path.name + TMP_SUFFIX), path) for path, _ in texts]
    try:
        for (tmp, _), (_, text) in zip(pairs, texts):
            _write(tmp, text)
        _swap_in(pairs, remove=() if has_svg else [svg_path])
    finally:
        for tmp, _ in pairs:
            tmp.unlink(missing_ok=True)
    return md_path, json_path, svg_path if has_svg else None
