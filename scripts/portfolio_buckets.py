#!/usr/bin/env python3
"""Maps a position's 类型 column to one of seven portfolio buckets.

Shared by portfolio.py (grouping) and positions_validate.py (rule 7) so the
two can never disagree about what counts as a known type.
"""
from __future__ import annotations

ETF_BUCKET = "赛道仓"

# NOTE: "3×ETF" uses a full-width multiplication sign (U+00D7), and "🏦 蓝筹"
# carries the emoji plus one ASCII space. These keys must match positions.md
# byte for byte — a lookalike character here silently becomes an unknown type.
BUCKET_BY_TYPE: dict[str, str] = {
    "宽基": "宽基",
    "🏦 蓝筹": "蓝筹",
    "论文": "小盘/投机",
    "CC": "小盘/投机",
    "小仓": "小盘/投机",
    "锁定": "锁定大仓",
    "信仰仓": "中国",
    "3×ETF": "杠杆",
}

KNOWN_TYPES = frozenset(BUCKET_BY_TYPE)

# Display order, largest holding first as of 2026-08-04.
BUCKET_ORDER: list[str] = [
    "锁定大仓", "宽基", "蓝筹", "中国", "杠杆", ETF_BUCKET, "小盘/投机",
]


class UnknownBucketError(ValueError):
    """Raised for a 类型 value with no bucket mapping.

    Deliberately fatal. This repo has repeatedly been bitten by silent
    catch-all buckets — a position quietly filed under "other" is a position
    nobody looks at. Adding a 类型 to positions.md must also mean adding it
    here, and the only reliable reminder is a crash.
    """


def bucket_for(stock_type: str) -> str:
    """Return the bucket for a 类型 value. Raises on anything unmapped."""
    key = (stock_type or "").strip()
    if key not in BUCKET_BY_TYPE:
        known = "、".join(sorted(BUCKET_BY_TYPE))
        raise UnknownBucketError(
            f"未知类型 {stock_type!r}（规范化为 {key!r}）。"
            f"已知类型：{known}。"
            f"请在 scripts/portfolio_buckets.py 的 BUCKET_BY_TYPE 中补上映射 —— "
            f"绝不要让它落入「其他」桶。"
        )
    return BUCKET_BY_TYPE[key]
