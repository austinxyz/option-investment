import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "scripts"))

DATA_SOURCE_ENV = "OPTION_DATA_SOURCE"
NO_NETWORK_ENV = "OPTION_TESTS_NO_NETWORK"
NO_NETWORK_MESSAGE = "network disabled in share tests"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "data_source(name=None): the test picks its own option data source; with a name the "
        "env var is set to it, without one OPTION_DATA_SOURCE is cleared for the test to set")


@pytest.fixture(autouse=True)
def _pin_data_source(request, monkeypatch):
    """Pin OPTION_DATA_SOURCE=ibkr unless the test carries @pytest.mark.data_source.

    The share edition defaults to Yahoo; pinning keeps every existing test on the IBKR path
    in both repositories.
    """
    marker = request.node.get_closest_marker("data_source")
    if marker is None:
        monkeypatch.setenv(DATA_SOURCE_ENV, "ibkr")
    elif marker.args:
        monkeypatch.setenv(DATA_SOURCE_ENV, str(marker.args[0]))
    else:
        monkeypatch.delenv(DATA_SOURCE_ENV, raising=False)


_SOCKETPAIR_DEPTH = threading.local()


@pytest.fixture(autouse=True)
def _no_network_guard(monkeypatch):
    """With OPTION_TESTS_NO_NETWORK=1, every socket connect raises OSError.

    The variable is read at connect time, so a test may set it itself; unset (the default
    here) the real connect runs unchanged (asyncio's Windows proactor connect is blocked
    the same way). Connects made inside socket.socketpair are let
    through: on Windows it is a loopback connect(127.0.0.1) fallback, and asyncio's
    new_event_loop() needs it, which is local plumbing, not network access.
    """
    real_connect = socket.socket.connect
    real_socketpair = socket.socketpair

    def socketpair(*args, **kwargs):
        _SOCKETPAIR_DEPTH.n = getattr(_SOCKETPAIR_DEPTH, "n", 0) + 1
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            _SOCKETPAIR_DEPTH.n -= 1

    def connect(self, *args, **kwargs):
        if os.environ.get(NO_NETWORK_ENV) == "1" and not getattr(_SOCKETPAIR_DEPTH, "n", 0):
            raise OSError(NO_NETWORK_MESSAGE)
        return real_connect(self, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "socketpair", socketpair)
    _guard_proactor_connect(monkeypatch)


def _guard_proactor_connect(monkeypatch):
    """Windows asyncio (ib_async) connects through IocpProactor.connect / ConnectEx, which
    never calls socket.connect, so it needs its own block."""
    try:
        from asyncio import windows_events
    except ImportError:   # not Windows
        return
    real = windows_events.IocpProactor.connect

    def connect(self, *args, **kwargs):
        if os.environ.get(NO_NETWORK_ENV) == "1":
            raise OSError(NO_NETWORK_MESSAGE)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(windows_events.IocpProactor, "connect", connect)


@pytest.fixture(autouse=True, scope="session")
def _subprocess_stdin_devnull():
    """Default subprocess stdin to DEVNULL for the whole test session.

    Under pytest's default fd-capture, fd 0 is swapped for a handle Windows
    cannot duplicate, so any subprocess.run() that inherits stdin fails
    intermittently with OSError WinError 6 ("The handle is invalid") — observed
    at roughly a 1-in-3 rate across the CLI integration tests, and dependent on
    which test file ran first.

    No test here feeds stdin, so defaulting it removes the failure mode without
    each of the eight call sites having to remember. Explicit stdin= arguments
    still win.
    """
    real_run = subprocess.run

    def run(*args, **kwargs):
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return real_run(*args, **kwargs)

    subprocess.run = run
    try:
        yield
    finally:
        subprocess.run = real_run
