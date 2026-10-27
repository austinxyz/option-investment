"""Tests for scripts/ibkr_session.py (fakes only, no network)."""
from pathlib import Path
import re
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ibkr_session as sess
import spread_data


class FakeIB:
    def __init__(self, error=None):
        self.error = error
        self.connects = []
        self.market_data_types = []
        self.disconnected = 0

    def connect(self, host, port, clientId=0, timeout=4, readonly=False):
        self.connects.append((host, port, clientId, timeout, readonly))
        if self.error:
            raise self.error

    def reqMarketDataType(self, n):
        self.market_data_types.append(n)

    def isConnected(self):
        return bool(self.connects) and not self.error

    def disconnect(self):
        self.disconnected += 1


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sess.reset_session()
    monkeypatch.setattr(sess, "_install_loop_handler", lambda: None)
    monkeypatch.setattr(sess, "_load_settings_from_disk",
                        lambda: sess.load_settings({"IBKR_HOST": "10.1.1.1", "IBKR_PORT": "14999"}))
    yield
    sess.reset_session()


def test_load_settings_defaults_and_env_over_file():
    s = sess.load_settings({"IBKR_HOST": "10.1.1.1"})
    assert (s.host, s.port, s.client_id, s.market_data_type) == ("10.1.1.1", 4002, 27, 4)
    s = sess.load_settings({"IBKR_HOST": "10.1.1.2"},
                           "IBKR_HOST=10.9.9.9\nIBKR_PORT=4001\n")
    assert s.host == "10.1.1.2" and s.port == 4001


def test_missing_host_error_names_data_ibkr_env():
    with pytest.raises(sess.SettingsError) as e:
        sess.load_settings({})
    assert "data/ibkr.env" in str(e.value)


def test_invalid_port_and_market_data_type_rejected():
    with pytest.raises(sess.SettingsError):
        sess.load_settings({"IBKR_HOST": "h", "IBKR_PORT": "abc"})
    with pytest.raises(sess.SettingsError):
        sess.load_settings({"IBKR_HOST": "h", "IBKR_PORT": "70000"})
    with pytest.raises(sess.SettingsError):
        sess.load_settings({"IBKR_HOST": "h", "IBKR_MARKET_DATA_TYPE": "5"})


def test_parse_env_file_comments_quotes():
    text = '# c\n\nA="x y"\nB=\'z\'\nbad line\nC = 3\n'
    assert sess.parse_env_file(text) == {"A": "x y", "B": "z", "C": "3"}


def test_get_ib_connects_once_readonly_with_mdtype():
    fake = FakeIB()
    ib = sess.get_ib(lambda: fake)
    assert ib is fake
    assert fake.connects == [("10.1.1.1", 14999, 27, 10, True)]
    assert fake.market_data_types == [4]
    assert sess.get_ib(lambda: FakeIB()) is fake
    assert len(fake.connects) == 1


def test_get_ib_refused_raises_gateway_unavailable_with_hint():
    with pytest.raises(sess.GatewayUnavailable) as e:
        sess.get_ib(lambda: FakeIB(ConnectionRefusedError()))
    assert "10.1.1.1:14999" in str(e.value) and "拒绝" in str(e.value)


def test_get_ib_timeout_hint_mentions_2fa():
    with pytest.raises(sess.GatewayUnavailable) as e:
        sess.get_ib(lambda: FakeIB(TimeoutError()))
    assert "2FA" in str(e.value)


def test_reset_session_allows_reconnect():
    first, second = FakeIB(), FakeIB()
    sess.get_ib(lambda: first)
    sess.reset_session()
    assert sess.get_ib(lambda: second) is second
    assert len(second.connects) == 1


def test_ensure_gateway_connects(monkeypatch):
    fake = FakeIB()
    monkeypatch.setattr(sess, "_new_ib", lambda: fake)
    sess.ensure_gateway()
    assert len(fake.connects) == 1


def test_competing_session_hint_mentions_10197():
    assert "10197" in sess.COMPETING_SESSION_HINT


def test_new_ibkr_modules_never_reference_order_apis():
    pattern = re.compile(r"placeOrder|cancelOrder|reqGlobalCancel|whatIfOrder|MarketOrder|LimitOrder")
    files = sorted((REPO_ROOT / "scripts").glob("ibkr_*.py"))
    assert files
    for f in files:
        assert not pattern.search(f.read_text(encoding="utf-8")), f.name


def test_gateway_unavailable_is_a_data_error():
    assert issubclass(sess.GatewayUnavailable, spread_data.DataError)


def test_client_id_env_name_is_options_specific():
    s = sess.load_settings({"IBKR_HOST": "h", "IBKR_CLIENT_ID": "5"})
    assert s.client_id == 27
    s = sess.load_settings({"IBKR_HOST": "h", "IBKR_OPTIONS_CLIENT_ID": "5"})
    assert s.client_id == 5


def test_mid_run_disconnect_raises_and_no_auto_reconnect():
    first = FakeIB()
    sess.get_ib(lambda: first)
    first.connects.clear()  # isConnected() is now False
    with pytest.raises(sess.GatewayUnavailable) as e:
        sess.get_ib(lambda: FakeIB())
    assert "中断" in str(e.value)
    assert first.disconnected == 1
    with pytest.raises(sess.GatewayUnavailable):
        sess.get_ib(lambda: FakeIB())
    sess.reset_session()
    second = FakeIB()
    assert sess.get_ib(lambda: second) is second


def test_failed_connect_disconnects_half_open_ib():
    fake = FakeIB(ConnectionRefusedError())
    with pytest.raises(sess.GatewayUnavailable):
        sess.get_ib(lambda: fake)
    assert fake.disconnected == 1


def test_failed_market_data_type_disconnects_half_open_ib():
    class Bad(FakeIB):
        def reqMarketDataType(self, n):
            raise RuntimeError("boom")
    fake = Bad()
    with pytest.raises(sess.GatewayUnavailable):
        sess.get_ib(lambda: fake)
    assert fake.disconnected == 1


def test_quiet_connection_reset_handler():
    class Loop:
        handler = None
        delegated = []

        def set_exception_handler(self, h):
            self.handler = h

        def default_exception_handler(self, ctx):
            self.delegated.append(ctx)

    loop = Loop()
    sess.quiet_connection_reset(loop)
    loop.handler(loop, {"exception": ConnectionResetError()})
    assert loop.delegated == []
    other = {"exception": ValueError("x")}
    loop.handler(loop, other)
    assert loop.delegated == [other]


def test_atexit_registered_once(monkeypatch):
    calls = []
    monkeypatch.setattr(sess.atexit, "register", lambda f: calls.append(f))
    sess._state["atexit"] = False
    sess.get_ib(lambda: FakeIB())
    sess.reset_session()
    sess.get_ib(lambda: FakeIB())
    assert len(calls) == 1


def _log(name, msg):
    import logging
    logging.getLogger(name).error(msg)


def test_noise_filter_hides_354_10091_200_unknown_contract(caplog):
    sess.get_ib(lambda: FakeIB())
    for code in (354, 10091, 200, 10167, 300):
        _log("ib_async.wrapper", f"Error {code}, reqId 7: boring")
    _log("ib_async.wrapper", "Warning 10167, reqId 7: delayed data")
    _log("ib_async.ib", "Unknown contract: Option(symbol='X')")
    assert caplog.records == []


def test_noise_filter_keeps_10197_and_1100(caplog):
    sess.get_ib(lambda: FakeIB())
    _log("ib_async.wrapper", "Error 10197, reqId 7: competing session")
    _log("ib_async.wrapper", "Error 1100, reqId -1: connectivity lost")
    _log("ib_async.ib", "Something else happened")
    assert [r.getMessage()[:10] for r in caplog.records] == [
        "Error 1019", "Error 1100", "Something "]


def test_noise_filter_installed_once_across_get_ib_calls():
    import logging
    sess.get_ib(lambda: FakeIB())
    sess.reset_session()
    sess.get_ib(lambda: FakeIB())
    for name in ("ib_async.wrapper", "ib_async.ib"):
        found = [f for f in logging.getLogger(name).filters if isinstance(f, sess._NoiseFilter)]
        assert len(found) == 1


def test_env_file_with_bom_is_parsed(tmp_path, monkeypatch):
    env = tmp_path / "ibkr.env"
    env.write_bytes(b"\xef\xbb\xbfIBKR_HOST=10.9.9.9\nIBKR_PORT=4001\n")
    monkeypatch.undo()  # drop the autouse stub of _load_settings_from_disk
    monkeypatch.setattr(sess, "ENV_PATH", env)
    monkeypatch.delenv("IBKR_HOST", raising=False)
    monkeypatch.delenv("IBKR_PORT", raising=False)
    got = sess._load_settings_from_disk()
    assert got.host == "10.9.9.9" and got.port == 4001
