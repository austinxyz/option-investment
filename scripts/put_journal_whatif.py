"""Put journal what-if sweeps: re-judge snapshot rows under alternative thresholds.

Pure module: no I/O, no network, no clock. Consumes the case dicts built by
`put_journal_stats.build_cases`; never mutates them.
"""
from __future__ import annotations

from put_journal_stats import pnl_if_closed
from put_review_rules import review_position, thresholds

CLOSE_GRID: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90)
DELTA_GRID: tuple[float, ...] = (0.25, 0.35, 0.45)


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _comparable(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c["comparable"]]


def _first_with_advice(case: dict, th: dict[str, float], advice: str) -> dict | None:
    """Earliest row (rows are date-sorted) judged `advice` under thresholds `th`."""
    return next(
        (r for r in case["rows"] if review_position(r, th)["advice"] == advice),
        None,
    )


def sweep_close(cases: list[dict], grid: tuple[float, ...] = CLOSE_GRID) -> list[dict]:
    """For each CLOSE_CAPTURED value: what if the earliest "close" row had been followed?

    A triggered position's pnl is replaced by pnl_if_closed at that row; others
    keep their actual pnl.
    """
    comparable = _comparable(cases)
    total_actual = round(sum(c["pnl"] for c in comparable), 2)
    out: list[dict] = []
    for value in grid:
        th = {**thresholds(), "CLOSE_CAPTURED": value}
        triggered = 0
        followed = 0.0
        for c in comparable:
            row = _first_with_advice(c, th, "close")
            if row is None:
                followed += c["pnl"]
            else:
                triggered += 1
                followed += pnl_if_closed(c, row)
        total_followed = round(followed, 2)
        out.append({
            "value": value,
            "n_comparable": len(comparable),
            "n_triggered": triggered,
            "total_followed": total_followed,
            "total_actual": total_actual,
            "diff": round(total_followed - total_actual, 2),
        })
    return out


def sweep_delta(cases: list[dict], grid: tuple[float, ...] = DELTA_GRID) -> list[dict]:
    """For each ATTENTION_DELTA value: precision/recall of "attention" vs losing cases."""
    comparable = _comparable(cases)
    trouble_flags = [c["pnl"] < 0 for c in comparable]
    n_trouble = sum(trouble_flags)
    out: list[dict] = []
    for value in grid:
        th = {**thresholds(), "ATTENTION_DELTA": value}
        flags = [_first_with_advice(c, th, "attention") is not None for c in comparable]
        n_flagged = sum(flags)
        both = sum(1 for f, t in zip(flags, trouble_flags) if f and t)
        out.append({
            "value": value,
            "n_comparable": len(comparable),
            "n_flagged": n_flagged,
            "n_trouble": n_trouble,
            "precision": _rate(both, n_flagged),
            "recall": _rate(both, n_trouble),
        })
    return out
