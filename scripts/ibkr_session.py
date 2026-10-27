"""Read-only IBKR gateway session and settings.

Never imports ib_async at module import time and never places orders:
the connection is opened with readonly=True.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# Re-exported (same class) so every `except ibkr_session.SettingsError` keeps working.
from option_settings import SettingsError
from spread_data import DataError, SourceUnavailable  # broker_* catch ibkr_session.DataError

DEFAULT_PORT = 4002
DEFAULT_CLIENT_ID = 27
DEFAULT_MARKET_DATA_TYPE = 4  # delayed-frozen
CONNECT_TIMEOUT = 10
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
ENV_PATH = Path(__file__).resolve().parent.parent / "data" / "ibkr.env"

COMPETING_SESSION_HINT = (
    "错误 10197：实盘会话抢占行情。同一个 IBKR 账户的实盘会话（手机或 TWS）正在占用行情，"
    "模拟网关拿不到数据；先退出实盘会话再重试。"
)
MISSING_IB_ASYNC = "IBKR 模式需要先 pip install -r requirements-ibkr.txt"


class GatewayUnavailable(SourceUnavailable):
    """The IBKR gateway cannot be reached (or dropped mid-run)."""


@dataclass(frozen=True)
class IbkrSettings:
    host: str
    port: int
    client_id: int
    market_data_type: int


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=VALUE lines; comments, blanks and lines without '=' are ignored; quotes stripped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or " " in key:
            continue
        out[key] = value.strip().strip('"').strip("'")
    return out


def _int(name: str, raw: str, low: int, high: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{name} 必须是整数，当前是 {raw!r}") from None
    if not low <= value <= high:
        raise SettingsError(f"{name} 必须在 {low}–{high} 之间，当前是 {value}")
    return value


def load_settings(environ: Mapping[str, str], dotenv_text: str | None = None) -> IbkrSettings:
    file_values = parse_env_file(dotenv_text) if dotenv_text else {}

    def get(name: str, default: str = "") -> str:
        return (environ.get(name) or file_values.get(name) or default).strip()

    host = get("IBKR_HOST")
    if not host:
        raise SettingsError("没有设置 IBKR_HOST：在环境变量或 data/ibkr.env 里写 "
                            "IBKR_HOST=<网关的局域网地址>（该文件不会被提交）")
    if not HOST_PATTERN.match(host):
        raise SettingsError(f"IBKR_HOST 只能是主机名或 IP，不要带协议或空格；请检查 data/ibkr.env，当前是 {host!r}")
    return IbkrSettings(
        host=host,
        port=_int("IBKR_PORT", get("IBKR_PORT", str(DEFAULT_PORT)), 1, 65535),
        client_id=_int("IBKR_OPTIONS_CLIENT_ID", get("IBKR_OPTIONS_CLIENT_ID", str(DEFAULT_CLIENT_ID)), 0, 2**31 - 1),
        market_data_type=_int("IBKR_MARKET_DATA_TYPE",
                              get("IBKR_MARKET_DATA_TYPE", str(DEFAULT_MARKET_DATA_TYPE)), 1, 4),
    )


def _load_settings_from_disk() -> IbkrSettings:
    text = ENV_PATH.read_text(encoding="utf-8-sig") if ENV_PATH.is_file() else None
    return load_settings(os.environ, text)


def explain_connect_failure(host: str, port: int, exc: Exception) -> str:
    target = f"{host}:{port}"
    if isinstance(exc, ConnectionRefusedError):
        return (f"连不上 {target}：连接被拒绝。网关容器可能没有启动，或者端口没有映射出来"
                "（检查网关容器是否在运行以及端口映射）。")
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return (f"连上了 {target} 但网关没有响应。最常见的原因是网关还卡在登录或 2FA 确认上；"
                "看网关容器日志，必要时在手机上批准 IBKR 的登录提示。")
    if isinstance(exc, OSError):
        return f"连不上 {target}：网络错误（{exc}）。检查这台电脑和网关是否在同一个局域网，地址是否写对。"
    return f"连接 {target} 时出错：{type(exc).__name__}: {exc}"


def quiet_connection_reset(loop) -> None:
    """Silence the ConnectionResetError asyncio logs when Windows tears the socket down."""

    def handler(loop_, context: dict) -> None:
        if isinstance(context.get("exception"), ConnectionResetError):
            return
        loop_.default_exception_handler(context)

    loop.set_exception_handler(handler)


def _new_ib():
    try:
        from ib_async import IB  # lazy: only needed when actually connecting
    except ImportError as exc:
        raise SourceUnavailable(MISSING_IB_ASYNC) from exc
    return IB()


_state: dict[str, object] = {"ib": None, "atexit": False, "dead": False, "loop": None,
                             "log_filter": None}

_NOISE_WRAPPER = re.compile(r"^(Error|Warning) (200|354|10091|10167|300), ")


class _NoiseFilter(logging.Filter):
    """Hide ib_async log lines for expected, non-fatal codes (no market-data
    subscription, delayed data, unknown contract). Everything else, including
    10197 and connectivity codes, is kept; errorEvent is untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.name == "ib_async.wrapper":
            return not _NOISE_WRAPPER.match(msg)
        if record.name == "ib_async.ib":
            return not msg.startswith("Unknown contract:")
        return True


def _install_log_filter() -> None:
    if _state["log_filter"] is not None:
        return
    noise = _NoiseFilter()
    for name in ("ib_async.wrapper", "ib_async.ib"):
        logging.getLogger(name).addFilter(noise)
    _state["log_filter"] = noise

DROPPED_HINT = "网关连接在运行中断开了：已收集的订阅不完整，请检查网关后重新运行（不会自动重连）。"


def _quiet_disconnect(ib) -> None:
    try:
        ib.disconnect()
    except Exception:
        pass


def _disconnect_quietly() -> None:
    ib = _state.get("ib")
    if ib is not None:
        _quiet_disconnect(ib)


def _install_loop_handler() -> None:
    """Install the quiet handler once, on a loop we own (or the running one)."""
    if _state["loop"] is not None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    quiet_connection_reset(loop)
    _state["loop"] = loop


def _alive(ib) -> bool:
    try:
        return bool(ib.isConnected())
    except Exception:
        return False


def get_ib(ib_factory: Callable | None = None):
    """Process-wide read-only session, opened on first use; never auto-reconnects."""
    current = _state["ib"]
    if current is not None:
        if not _state["dead"] and _alive(current):
            return current
        _state["dead"] = True
        _quiet_disconnect(current)
        raise GatewayUnavailable(DROPPED_HINT)
    settings = _load_settings_from_disk()
    _install_loop_handler()
    _install_log_filter()
    ib = (ib_factory or _new_ib)()
    try:
        ib.connect(settings.host, settings.port, clientId=settings.client_id,
                   timeout=CONNECT_TIMEOUT, readonly=True)
        ib.reqMarketDataType(settings.market_data_type)
    except Exception as exc:
        _quiet_disconnect(ib)
        raise GatewayUnavailable(explain_connect_failure(settings.host, settings.port, exc)) from exc
    _state["ib"] = ib
    if not _state["atexit"]:
        atexit.register(_disconnect_quietly)
        _state["atexit"] = True
    return ib


def reset_session() -> None:
    """Drop the session; the only way to allow a new connection."""
    _disconnect_quietly()
    _state["ib"] = None
    _state["dead"] = False


def ensure_gateway() -> None:
    get_ib()
