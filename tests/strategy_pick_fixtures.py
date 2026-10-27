"""Shared invented-ticker fixtures for the /strategy-pick tests (design section 10, ZZZQ).

Plain module, imported as `from strategy_pick_fixtures import ...` (pytest puts tests/ on sys.path,
same as spread_fixtures). Numbers are invented; nothing here touches the network.
"""
from datetime import date, timedelta
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

SNAPSHOT = date(2026, 10, 23)  # Friday; the quote snapshot day of the worked example


def weekday_dates(n: int, end: date = SNAPSHOT) -> list[date]:
    """n consecutive weekdays ending on `end` (ascending)."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def base_closes() -> list[float]:
    """252 closes hand-built so the section-10 numbers all hold at once.

    P1 80.00, Pn 100.00, max 104.00 (idx 100), lowest after it 88.40 (idx 125), min 78.00 (idx 5),
    median 92.00 (125 values below 92, the rest at or above), MA50 96.00, MA200 90.00,
    MA200 twenty sessions earlier 88.20, close 63 sessions before the last 92.00.
    Group sums (idx ranges): 32-51 = 1621, 52-201 = 13200, 202-231 = 2819, 232-251 = 1981.
    """
    g0 = [80.0, 79.0, 78.5, 78.2, 78.1, 78.0] + [80.0] * 26          # idx 0-31
    g1 = [81.0] * 19 + [82.0]                                         # idx 32-51
    pre_peak = [80.0] * 48                                            # idx 52-99
    peak = [104.0]                                                    # idx 100
    post_low = [90.65] * 24 + [88.40]                                 # idx 101-125
    post_flat = [92.0] * 76                                           # idx 126-201
    g3 = [94.0] * 29 + [93.0]                                         # idx 202-231
    g4 = [99.0] * 19 + [100.0]                                        # idx 232-251
    return g0 + g1 + pre_peak + peak + post_low + post_flat + g3 + g4


def base_rv21(n_le: int = 174) -> list[float]:
    """231 rolling-RV21 values: n_le of them <= 0.32 (one exactly 0.32), current (last) 0.26."""
    total = 231
    above = [0.40 + 0.001 * i for i in range(total - n_le)]           # strictly > 0.32
    below = [0.32] + [0.20 + 0.0004 * i for i in range(n_le - 2)]     # <= 0.32
    return above + below + [0.26]


def split_closes_unadjusted() -> list[float]:
    """100 -> 50 overnight 2:1 split left unadjusted: a fake 50% drawdown."""
    return [100.0 + 0.1 * (i % 3) for i in range(126)] + [50.0 + 0.05 * (i % 3) for i in range(126)]


def split_closes_adjusted() -> list[float]:
    """Same history after back-adjustment: flat around 50, no crash."""
    return [50.0 + 0.05 * (i % 3) for i in range(252)]
