"""Shared helper for the --from-pick CLI tests: a hand-written /strategy-pick JSON in tmp_path."""
from datetime import date
from pathlib import Path
import json

import strategy_pick_frompick

LEGS = {"cash_secured_put": ("short_put",), "covered_call": ("short_call",),
        "bull_put_spread": ("short_put",), "iron_condor": ("short_put", "short_call"),
        "bull_call_spread": ("long_call",), "bear_put_spread": ("long_put",),
        "bear_call_spread": ("short_call",)}


def write_picks(folder: Path, ticker: str, run_date: date, picks: list) -> Path:
    """Today's file with several picks as given (e.g. bear.held: reduce_position, bear_put_spread)."""
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"ticker": ticker, "run_date": run_date.isoformat(),
           "snapshot_date": run_date.isoformat(), "picks": picks}
    path = folder / f"{run_date.isoformat()}-{ticker}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def bear_held_picks() -> list:
    """bear.held as /strategy-pick writes it: reduce_position (no Delta), then Bear Put 0.50-0.60."""
    return [{"strategy": "reduce_position", "rank": 1, "delta_target": None},
            {"strategy": "bear_put_spread", "rank": 2,
             "delta_target": [{"leg": "long_put", "low": 0.50, "high": 0.60}]}]


def write_pick(folder: Path, ticker: str, run_date: date, strategy: str, low: float, high: float,
               *, file_date: date | None = None) -> Path:
    """data/strategy-picks/<file_date>-<TICKER>.json with one pick; file_date defaults to run_date."""
    folder.mkdir(parents=True, exist_ok=True)
    target = [{"leg": leg, "low": low, "high": high} for leg in LEGS[strategy]]
    doc = {"ticker": ticker, "run_date": run_date.isoformat(), "snapshot_date": run_date.isoformat(),
           "picks": [{"strategy": strategy, "rank": 1, "delta_target": target}]}
    path = folder / f"{(file_date or run_date).isoformat()}-{ticker}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def use_folder(monkeypatch, folder: Path) -> Path:
    """Point --from-pick at `folder` instead of data/strategy-picks."""
    monkeypatch.setattr(strategy_pick_frompick, "default_picks_dir", lambda: folder)
    return folder
