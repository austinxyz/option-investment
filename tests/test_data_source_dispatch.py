"""The spread_data facade dispatches per call on the data-source setting; IBKR args are unchanged."""
import sys
from types import SimpleNamespace

import pytest

import ibkr_data
import ibkr_session
import option_settings
import spread_data

EXP = "2026-11-20"


def _recorder(log: list, name: str, result):
    def fn(*args, **kwargs):
        log.append((name, args, kwargs))
        return result
    return fn


def _forbid(monkeypatch, mod, names):
    def boom(*a, **k):
        raise AssertionError("wrong data layer called")
    for name in names:
        monkeypatch.setattr(mod, name, boom)


IBKR_NAMES = ("chain_rows", "last_close", "expirations", "chain_strikes")


def test_gateway_unavailable_is_source_unavailable_and_data_error():
    assert issubclass(spread_data.SourceUnavailable, spread_data.DataError)
    assert issubclass(ibkr_session.GatewayUnavailable, spread_data.SourceUnavailable)
    assert issubclass(ibkr_session.GatewayUnavailable, spread_data.DataError)
    assert ibkr_session.GatewayUnavailable.__name__ == "GatewayUnavailable"


def test_dispatch_ibkr_calls_ibkr_data_with_identical_args(monkeypatch):
    log: list = []
    monkeypatch.setattr(ibkr_data, "chain_rows", _recorder(log, "rows", ["row"]))
    monkeypatch.setattr(ibkr_data, "last_close", _recorder(log, "close", ("d", 1.0)))
    monkeypatch.setattr(ibkr_data, "expirations", _recorder(log, "exp", [EXP]))
    monkeypatch.setattr(ibkr_data, "chain_strikes", _recorder(log, "strikes", [95.0]))
    monkeypatch.setattr(spread_data, "_yahoo_layer", lambda: pytest.fail("yahoo layer used"))
    assert spread_data.fetch_puts("ZZZQ", EXP) == ["row"]
    spread_data.fetch_calls("ZZZQ", EXP, strikes=(95.0,))
    spread_data.fetch_puts("ZZZQ", EXP, window_pct=0.25, window_limit=60)
    spread_data.fetch_calls("ZZZQ", EXP, strikes=(95.0,), window_pct=0.0, window_limit=0)
    spread_data.fetch_puts("ZZZQ", EXP, window_limit=60)
    assert spread_data.last_close("ZZZQ") == ("d", 1.0)
    assert spread_data.expirations("ZZZQ") == [EXP]
    assert spread_data.chain_strikes("ZZZQ") == [95.0]
    assert log == [
        ("rows", ("ZZZQ", EXP, "P"), {"strikes": ()}),
        ("rows", ("ZZZQ", EXP, "C"), {"strikes": (95.0,)}),
        ("rows", ("ZZZQ", EXP, "P"), {"strikes": (), "window_pct": 0.25, "window_limit": 60}),
        ("rows", ("ZZZQ", EXP, "C"), {"strikes": (95.0,), "window_pct": 0.0, "window_limit": 0}),
        ("rows", ("ZZZQ", EXP, "P"), {"strikes": (), "window_limit": 60}),
        ("close", ("ZZZQ",), {}),
        ("exp", ("ZZZQ",), {}),
        ("strikes", ("ZZZQ",), {}),
    ]


def _fake_yahoo(log: list) -> SimpleNamespace:
    return SimpleNamespace(
        ensure_available=_recorder(log, "ensure", None),
        chain_rows=_recorder(log, "rows", ["yrow"]),
        last_close=_recorder(log, "close", ("y", 2.0)),
        expirations=_recorder(log, "exp", [EXP]),
        chain_strikes=_recorder(log, "strikes", [90.0]),
        stale_drop_count=_recorder(log, "stale", 3))


@pytest.mark.data_source("yahoo")
def test_dispatch_yahoo_calls_yahoo_layer(monkeypatch):
    log: list = []
    monkeypatch.setattr(spread_data, "_yahoo_layer", lambda: _fake_yahoo(log))
    _forbid(monkeypatch, ibkr_data, IBKR_NAMES)
    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: pytest.fail("gateway used"))
    spread_data.ensure_source()
    assert spread_data.fetch_puts("ZZZQ", EXP) == ["yrow"]
    spread_data.fetch_calls("ZZZQ", EXP, strikes=(95.0,), window_pct=0.0, window_limit=0)
    assert spread_data.last_close("ZZZQ") == ("y", 2.0)
    assert spread_data.expirations("ZZZQ") == [EXP]
    assert spread_data.chain_strikes("ZZZQ") == [90.0]
    assert spread_data.stale_drop_count("ZZZQ", EXP) == 3
    assert log == [
        ("ensure", (), {}),
        ("rows", ("ZZZQ", EXP, "P"), {"strikes": ()}),
        ("rows", ("ZZZQ", EXP, "C"), {"strikes": (95.0,), "window_pct": 0.0, "window_limit": 0}),
        ("close", ("ZZZQ",), {}),
        ("exp", ("ZZZQ",), {}),
        ("strikes", ("ZZZQ",), {}),
        ("stale", ("ZZZQ", EXP), {}),
    ]


@pytest.mark.data_source("demo")
def test_dispatch_demo_uses_the_yahoo_layer(monkeypatch):
    log: list = []
    monkeypatch.setattr(spread_data, "_yahoo_layer", lambda: _fake_yahoo(log))
    _forbid(monkeypatch, ibkr_data, IBKR_NAMES)
    spread_data.ensure_source()
    assert spread_data.expirations("ZZZQ") == [EXP]
    assert [entry[0] for entry in log] == ["ensure", "exp"]


@pytest.mark.data_source("yahoo")
def test_yahoo_layer_is_the_yahoo_data_module():
    # Task 3 replaced the placeholder: the layer is the real provider module.
    import yahoo_data
    assert spread_data._yahoo_layer() is yahoo_data
    assert not hasattr(spread_data, "YAHOO_NOT_READY")


@pytest.mark.data_source("bogus")
def test_invalid_source_surfaces_settings_error():
    with pytest.raises(option_settings.SettingsError):
        spread_data.ensure_source()


def test_ensure_source_looks_up_ensure_gateway_at_call_time(monkeypatch):
    calls = []
    monkeypatch.setattr(ibkr_session, "ensure_gateway", lambda: calls.append("gw"))
    spread_data.ensure_source()
    assert calls == ["gw"]
    down = ibkr_session.GatewayUnavailable("gw.test:14999")

    def raiser():
        raise down
    monkeypatch.setattr(ibkr_session, "ensure_gateway", raiser)
    with pytest.raises(spread_data.SourceUnavailable) as err:
        spread_data.ensure_source()
    assert err.value is down


def test_missing_ib_async_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "ib_async", None)
    with pytest.raises(spread_data.SourceUnavailable) as err:
        ibkr_session._new_ib()
    assert str(err.value) == "IBKR 模式需要先 pip install -r requirements-ibkr.txt"
    assert not isinstance(err.value, ibkr_session.GatewayUnavailable)


def test_stale_drop_count_zero_in_ibkr(monkeypatch):
    monkeypatch.setattr(spread_data, "_yahoo_layer", lambda: pytest.fail("yahoo layer used"))
    assert spread_data.stale_drop_count("ZZZQ", EXP) == 0


@pytest.mark.data_source("yahoo")
def test_ticker_goes_through_yf_module(monkeypatch):
    seen = []
    fake = SimpleNamespace(Ticker=lambda sym: seen.append(sym) or sym)
    monkeypatch.setattr(option_settings, "yf_module", lambda: fake)
    import strategy_pick_data
    assert spread_data._ticker(" zzzq ") == "ZZZQ"
    assert strategy_pick_data._ticker("zzqb") == "ZZQB"
    assert seen == ["ZZZQ", "ZZQB"]


def _settings_raiser(*args, **kwargs):
    raise option_settings.SettingsError("没有设置 IBKR_HOST")


@pytest.mark.parametrize("module_name", ["spread_sim", "put_screen", "call_screen",
                                         "collar_screen"])
def test_settings_error_is_a_clean_error_line(tmp_path, monkeypatch, capsys, module_name):
    mod = __import__(module_name)
    monkeypatch.setitem(mod.COMMANDS, "status", _settings_raiser)
    assert mod.main(["--ledger-dir", str(tmp_path), "status"]) == 1
    assert capsys.readouterr().err == "错误：没有设置 IBKR_HOST\n"


def test_put_review_settings_error_is_a_clean_error_line(tmp_path, monkeypatch, capsys):
    import put_review
    import spread_ledger
    led, _ = spread_ledger.add_spread(spread_ledger.empty_ledger(), {
        "strategy": "cash_secured_put", "ticker": "ZZZQ", "contracts": 1,
        "snapshot_date": "2026-10-16", "expiry": "2026-11-20", "underlying_price": 100.0,
        "short_strike": 95.0, "long_strike": None, "width": None, "credit": 1.00,
        "status": "open", "exit_date": None, "exit_cost": None, "pnl": None, "result": None})
    spread_ledger.save(tmp_path / "spreads.json", led)
    monkeypatch.setattr(ibkr_session, "ensure_gateway", _settings_raiser)
    from datetime import date
    assert put_review.main(["--ledger-dir", str(tmp_path)], today=date(2026, 10, 23)) == 1
    assert capsys.readouterr().err == "错误：没有设置 IBKR_HOST\n"
