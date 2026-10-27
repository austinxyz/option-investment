"""Which market-data source the option tools use, and what that source implies.

Priority: env OPTION_DATA_SOURCE > a line `option_data_source: yahoo` in data/config.md >
option_source_default.DEFAULT_SOURCE. Read on every call (never cached) so the facade can
dispatch per call and tests can switch sources with monkeypatch.

Must not import spread_data or ibkr_session: both import this module.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import option_source_default

ENV_VAR = "OPTION_DATA_SOURCE"
CONFIG_KEY = "option_data_source"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.md"
SOURCES = ("ibkr", "yahoo", "demo")
PAPER_DIR = Path("data") / "paper-trading"
DEMO_PAPER_DIR = Path("data") / "paper-trading-demo"
_CONFIG_LINE = re.compile(rf"^[\s>*-]*{CONFIG_KEY}\s*[:：]\s*(.*?)\s*$", re.I | re.M)


class SettingsError(ValueError):
    """A setting is missing or invalid; the message says how to fix it."""


@dataclass(frozen=True)
class DataProfile:
    name: str
    label: str
    quality: str | None
    max_spread_pct: float
    warn_spread_pct: float | None
    notes: tuple[str, ...]


PROFILES: dict[str, DataProfile] = {
    "ibkr": DataProfile("ibkr", "IBKR 模拟网关", None, 0.05, None, ()),
    "yahoo": DataProfile("yahoo", "Yahoo Finance", "low", 0.10, 0.05, (
        "延迟约 15 分钟，非券商报价",
        "Delta 为本地 Black-Scholes 估算",
        "休市时用最后成交价（标\"陈旧\"）",
        "买卖价差门槛放宽到 10%",
        "按中间价成交、不含手续费，模拟结果偏乐观",
    )),
    "demo": DataProfile("demo", "演示（虚构数据）", "demo", 0.10, 0.05, (
        "演示数据（虚构），不代表任何真实行情",
    )),
}


def _checked(raw: str, origin: str) -> str:
    value = raw.strip().lower()
    if value not in SOURCES:
        raise SettingsError(f"{origin} 的数据源只能是 {' / '.join(SOURCES)}，当前是 {raw.strip()!r}")
    return value


def _from_config(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"读不了 {path.name}：{exc}") from exc
    match = _CONFIG_LINE.search(text)
    return None if match is None else match.group(1)


def data_source(config_path: Path | None = None) -> str:
    env = os.environ.get(ENV_VAR, "")
    if env.strip():
        return _checked(env, f"环境变量 {ENV_VAR}")
    configured = _from_config(config_path or CONFIG_PATH)
    if configured is not None:
        return _checked(configured, f"data/config.md 的 {CONFIG_KEY}")
    return _checked(option_source_default.DEFAULT_SOURCE, "option_source_default.DEFAULT_SOURCE")


def profile() -> DataProfile:
    return PROFILES[data_source()]


def spread_kwargs() -> dict:
    """Keyword args for the rule functions: empty in IBKR (their defaults ARE the IBKR rule)."""
    prof = profile()
    if prof.name == "ibkr":
        return {}
    return {"spread_limit": prof.max_spread_pct, "spread_warn": prof.warn_spread_pct}


def public_source_label() -> str:
    """Front-matter `source:` of the public paper-trading page; set per edition by the
    generated default file (the share build writes its own label)."""
    return option_source_default.PUBLIC_SOURCE_LABEL


def is_ibkr() -> bool:
    return data_source() == "ibkr"


def paper_dir(repo: Path) -> Path:
    return repo / (DEMO_PAPER_DIR if data_source() == "demo" else PAPER_DIR)


def yf_module() -> ModuleType:
    """The yfinance-shaped module for the calendar helpers and the Yahoo layer."""
    if data_source() == "demo":
        import demo_market  # offline and pure: the module itself is the yfinance look-alike
        return demo_market
    import yfinance  # lazy: pure tests and IBKR-only paths need no network stack
    return yfinance
