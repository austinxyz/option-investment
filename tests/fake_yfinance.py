"""A scriptable stand-in for the yfinance module (no network), for the Yahoo data-layer tests.

Only the surface the option tools use exists: `Ticker(sym)` with `.options`, `.option_chain(exp)`
(-> `.puts` / `.calls` pandas DataFrames with the yfinance columns), `.history(...)`, `.calendar`
and `.info`. Every network-shaped access (`options`, `option_chain`, `history`) is logged in
`FakeYF.calls` and counted per symbol, so a `Scene` can say "the k-th call raises X".
`FakeYF(..., swallow=True)` behaves like real yfinance's default (`hide_exceptions`): a
scripted failure in `history` / `options` is swallowed and an empty answer comes back.
Tickers are invented (ZZZQ, ZZQB, ...).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd

import option_settings

CHAIN_COLUMNS = ("strike", "bid", "ask", "lastPrice", "impliedVolatility", "lastTradeDate",
                 "openInterest")
NY_TZ = "America/New_York"
NAN = float("nan")
SWALLOWED = ("history", "options")  # real yfinance hides errors there and answers empty


def chain_frame(rows: list[dict]) -> pd.DataFrame:
    """A puts / calls frame; missing columns are NaN (lastTradeDate NaT)."""
    records = [{col: row.get(col, pd.NaT if col == "lastTradeDate" else NAN)
                for col in CHAIN_COLUMNS} for row in rows]
    return pd.DataFrame(records, columns=list(CHAIN_COLUMNS))


def utc(text: str) -> pd.Timestamp:
    """A tz-aware UTC timestamp, the way yfinance reports lastTradeDate."""
    return pd.Timestamp(text, tz="UTC")


def daily(bars: list[tuple[date, float]]) -> pd.DataFrame:
    """Daily bars indexed by New York midnight, like yfinance's history()."""
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(NY_TZ) for d, _ in bars])
    return pd.DataFrame({"Close": [c for _, c in bars]}, index=index)


def minutes(bars: list[tuple[datetime, float]]) -> pd.DataFrame:
    """1-minute bars; naive datetimes are New York wall-clock times."""
    index = pd.DatetimeIndex([pd.Timestamp(t).tz_localize(NY_TZ) for t, _ in bars])
    return pd.DataFrame({"Close": [c for _, c in bars]}, index=index)


@dataclass
class Scene:
    """One symbol's data. `raises[k]` is raised by that symbol's k-th network call (1-based)."""
    options: tuple[str, ...] = ()
    chains: dict = field(default_factory=dict)        # expiry -> (puts frame, calls frame)
    history: pd.DataFrame | None = None               # daily bars
    minute: pd.DataFrame | None = None                # 1-minute bars
    calendar: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)
    raises: dict = field(default_factory=dict)


class FakeTicker:
    def __init__(self, fake: "FakeYF", symbol: str):
        self._fake, self.ticker = fake, symbol

    def _scene(self) -> Scene:
        return self._fake.scenes.get(self.ticker, Scene())

    def _hit(self, what: str, *args) -> Scene | None:
        """The scene, or None when a scripted failure was swallowed (empty answer)."""
        self._fake.calls.append((self.ticker, what, args))
        count = self._fake.counts.get(self.ticker, 0) + 1
        self._fake.counts[self.ticker] = count
        scene = self._scene()
        if count in scene.raises:
            if self._fake.swallow and what in SWALLOWED:
                return None
            raise scene.raises[count]
        return scene

    @property
    def options(self) -> tuple[str, ...]:
        scene = self._hit("options")
        return () if scene is None else tuple(scene.options)

    def option_chain(self, expiry: str):
        scene = self._hit("option_chain", expiry)
        if expiry not in scene.chains:
            raise ValueError(f"Expiration `{expiry}` cannot be found.")
        puts, calls = scene.chains[expiry]
        return SimpleNamespace(puts=None if puts is None else puts.copy(),
                               calls=None if calls is None else calls.copy())

    def history(self, **kwargs) -> pd.DataFrame:
        scene = self._hit("history", tuple(sorted(kwargs.items())))
        if scene is None:
            return pd.DataFrame({"Close": []})
        frame = scene.minute if kwargs.get("interval") == "1m" else scene.history
        return pd.DataFrame({"Close": []}) if frame is None else frame.copy()

    @property
    def calendar(self) -> dict:
        return dict(self._scene().calendar)

    @property
    def info(self) -> dict:
        return dict(self._scene().info)


class FakeYF:
    """Module-shaped: `fake.Ticker("ZZZQ")`. `tickers_made` counts Ticker() constructions."""

    def __init__(self, scenes: dict[str, Scene], swallow: bool = False):
        self.scenes = dict(scenes)
        self.swallow = swallow
        self.calls: list[tuple] = []
        self.counts: dict[str, int] = {}
        self.tickers_made: list[str] = []

    def Ticker(self, symbol: str) -> FakeTicker:  # noqa: N802 - mirrors yfinance
        self.tickers_made.append(symbol)
        return FakeTicker(self, symbol)

    def network_calls(self, what: str | None = None) -> list[tuple]:
        return [c for c in self.calls if what is None or c[1] == what]


def install(monkeypatch, fake: FakeYF) -> FakeYF:
    """Point option_settings.yf_module at the fake for the rest of the test."""
    monkeypatch.setattr(option_settings, "yf_module", lambda: fake)
    return fake
