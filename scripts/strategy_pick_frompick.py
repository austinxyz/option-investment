#!/usr/bin/env python3
"""--from-pick: read TODAY's /strategy-pick result and hand its Delta target to the next command.

See docs/superpowers/specs/2026-10-28-from-pick-design.md. Only today's file
data/strategy-picks/<today>-<TICKER>.json is ever opened (demo source: its demo/ subfolder, where
strategy_pick writes); anything missing, stale or malformed raises FromPickError, never a silent
fallback. The --delta / bucket mapping is strategy_pick_handoff's, so what the hand-off line
prints and what --from-pick uses cannot differ. The only I/O is reading that one file (and the
data-source setting for the default folder).
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import NamedTuple

import option_settings
import strategy_pick_handoff as H
import strategy_pick_rules as R
import strategy_pick_text as T

REPO = Path(__file__).resolve().parent.parent
PICKS_DIR = REPO / R.OUTPUT_DIR
DEMO_SUBDIR = "demo"  # == strategy_pick.DEMO_OUTPUT_SUBDIR (pinned by a test)
TICKER_PATTERN = r"[A-Z0-9][A-Z0-9.\-]{0,9}"  # == spread_sim.TICKER_PATTERN (pinned by a test)
EXIT_FROM_PICK = 2  # an argument-type error, like the other argparse refusals

MISSING = "今天还没有 {t} 的 /strategy-pick 结果，先运行 /strategy-pick {t}"
STALE = "{t} 的 /strategy-pick 结果是 {run_date} 的，不是今天（{today}）：先重跑 /strategy-pick {t}"
OTHER_TICKER = "{name} 里是 {other} 的结果，不是 {t}：先重跑 /strategy-pick {t}"
CORRUPT = "{name} 格式不对（{why}）：先重跑 /strategy-pick {t}"
UNREADABLE = "读不了 {name}（{why}）：先重跑 /strategy-pick {t}"
NO_STRATEGY = "今天的推荐里没有 {label}：{picked}"
PICKED = "推荐是 {labels}"
NOTHING_PICKED = "今天没有推荐任何策略"
NO_TARGET = "今天 {t} 的 {label} 推荐没有能带过去的 Delta 目标（{why}）"
BAD_TICKER = "ticker 只能是字母、数字、点和连字符，最长 10 位"
LINE = "按 /strategy-pick {run_date} 的结果：Delta 目标 {target} → {arg}"


class FromPickError(Exception):
    """--from-pick cannot use today's result; the message says what to run instead."""


class Leg(NamedTuple):
    leg: str
    low: float
    high: float


class PickTarget(NamedTuple):
    ticker: str
    strategy: str
    run_date: str
    rank: int | None
    delta_target: tuple[Leg, ...]


def default_picks_dir() -> Path:
    """Where /strategy-pick writes for the current data source (read per run)."""
    return PICKS_DIR / DEMO_SUBDIR if option_settings.data_source() == "demo" else PICKS_DIR


def _ticker(text: str) -> str:
    value = str(text).strip().upper()
    if not re.fullmatch(TICKER_PATTERN, value):
        raise FromPickError(BAD_TICKER)
    return value


def _label(strategy: str) -> str:
    return T.STRATEGY_LABELS.get(strategy, strategy)


def _read(path: Path, ticker: str):
    fmt = {"name": path.name, "t": ticker}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise FromPickError(MISSING.format(**fmt)) from None
    except UnicodeDecodeError:
        raise FromPickError(CORRUPT.format(why="不是 UTF-8 文本", **fmt)) from None
    except OSError as exc:
        raise FromPickError(UNREADABLE.format(why=exc, **fmt)) from None
    try:
        return json.loads(text)
    except ValueError:
        raise FromPickError(CORRUPT.format(why="不是 JSON", **fmt)) from None


def _number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 1)


def _legs(raw) -> tuple[Leg, ...] | None:
    """delta_target as Legs; None when its shape is wrong. Missing or null means no target."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    legs = []
    for t in raw:
        if not (isinstance(t, dict) and isinstance(t.get("leg"), str)
                and _number(t.get("low")) and _number(t.get("high")) and t["low"] <= t["high"]):
            return None
        legs.append(Leg(t["leg"], float(t["low"]), float(t["high"])))
    return tuple(legs)


def _check_doc(doc, ticker: str, today: date, name: str) -> list:
    fmt = {"name": name, "t": ticker}
    if not isinstance(doc, dict):
        raise FromPickError(CORRUPT.format(why="顶层不是对象", **fmt))
    if not (isinstance(doc.get("ticker"), str) and isinstance(doc.get("run_date"), str)
            and isinstance(doc.get("picks"), list)):
        raise FromPickError(CORRUPT.format(why="缺 ticker / run_date / picks", **fmt))
    if not all(isinstance(p, dict) and isinstance(p.get("strategy"), str) for p in doc["picks"]):
        raise FromPickError(CORRUPT.format(why="picks 里有不认识的条目", **fmt))
    if doc["ticker"].strip().upper() != ticker:
        raise FromPickError(OTHER_TICKER.format(other=doc["ticker"], **fmt))
    if doc["run_date"] != today.isoformat():
        raise FromPickError(STALE.format(run_date=doc["run_date"], today=today.isoformat(), **fmt))
    return doc["picks"]


def _rank(p: dict) -> float:
    rank = p.get("rank")
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else math.inf


def load_pick(ticker: str, strategy: str, *, today: date | None = None,
              picks_dir: Path | None = None) -> PickTarget:
    """Today's pick for `strategy`, or FromPickError (never an older day's file)."""
    ticker = _ticker(ticker)
    today = today or date.today()
    folder = Path(picks_dir) if picks_dir is not None else default_picks_dir()
    name = f"{today.isoformat()}-{ticker}.json"
    picks = _check_doc(_read(folder / name, ticker), ticker, today, name)
    mine = sorted((p for p in picks if p["strategy"] == strategy), key=_rank)
    if not mine:
        labels = "、".join(_label(p["strategy"]) for p in picks)
        picked = PICKED.format(labels=labels) if picks else NOTHING_PICKED
        raise FromPickError(NO_STRATEGY.format(label=_label(strategy), picked=picked))
    legs = _legs(mine[0].get("delta_target"))
    if legs is None:
        raise FromPickError(CORRUPT.format(why="delta_target 不是 0–1 的 low ≤ high 区间", name=name,
                                           t=ticker))
    if not legs:
        raise FromPickError(NO_TARGET.format(t=ticker, label=_label(strategy), why="没有 delta_target"))
    rank = mine[0].get("rank")
    return PickTarget(ticker, strategy, today.isoformat(),
                      rank if isinstance(rank, int) else None, legs)


def _mapped(pick: PickTarget, fn):
    try:
        return fn(pick.strategy, pick.delta_target)
    except (ValueError, KeyError) as exc:
        raise FromPickError(NO_TARGET.format(t=pick.ticker, label=_label(pick.strategy),
                                             why=exc)) from None


def delta_for_screen(pick: PickTarget) -> float:
    """The --delta handoff() prints for /put-screen or /call-screen."""
    return _mapped(pick, H.screen_delta)


def bucket_for_open(pick: PickTarget, strategy: str) -> float:
    """The main bucket handoff() prints for `open T <bucket>` (alternatives ignored)."""
    if pick.strategy != strategy:
        raise ValueError(f"pick is {pick.strategy}, not {strategy}")
    return _mapped(pick, H.open_bucket)


def _target_text(pick: PickTarget) -> str:
    low, high = _mapped(pick, H.target_range)
    return f"{low:.2f}" if low == high else f"{low:.2f}–{high:.2f}"


def screen_line(pick: PickTarget, delta: float) -> str:
    line = LINE.format(run_date=pick.run_date, target=_target_text(pick), arg=f"--delta {delta:.2f}")
    return line + (H.CALL_ALL_HOLDINGS if pick.strategy == "covered_call" else "")


def open_line(pick: PickTarget, bucket: float) -> str:
    return LINE.format(run_date=pick.run_date, target=_target_text(pick), arg=f"档位 {bucket:.2f}")


# --- argparse glue shared by the seven commands -------------------------------------------------------
class StoreGiven(argparse.Action):
    """Plain `store`, plus `<dest>_given = True`, so a default value can be told from a typed one."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_given", True)


def screen_from_pick(ticker: str, strategy: str, today: date) -> tuple[str, float, str]:
    """(ticker, --delta, console line) for /put-screen or /call-screen suggest --from-pick."""
    pick = load_pick(ticker, strategy, today=today)
    delta = delta_for_screen(pick)
    return pick.ticker, delta, screen_line(pick, delta)


def open_from_pick(ticker: str, strategy: str, today: date) -> tuple[float, str]:
    """(bucket, console line) for `open T --from-pick`."""
    pick = load_pick(ticker, strategy, today=today)
    bucket = bucket_for_open(pick, strategy)
    return bucket, open_line(pick, bucket)


OPEN_NEEDS_ONE = "open 需要档位（例如 0.15）或 --from-pick，二选一"
OPEN_NOT_BOTH = "档位和 --from-pick 只能二选一"


def check_open_args(parser: argparse.ArgumentParser, args) -> None:
    """Exactly one of the positional bucket and --from-pick (argparse error, exit 2)."""
    if getattr(args, "cmd", None) != "open":
        return
    if args.bucket is None and not args.from_pick:
        parser.error(OPEN_NEEDS_ONE)
    if args.bucket is not None and args.from_pick:
        parser.error(OPEN_NOT_BOTH)
