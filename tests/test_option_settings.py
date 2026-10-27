"""Data-source setting: env > data/config.md > generated default, profiles and per-source paths."""
import re
import sys
import types
from pathlib import Path

import pytest

import conftest
import ibkr_session
import option_settings as S
import option_source_default

ENV = "OPTION_DATA_SOURCE"


def _config(tmp_path: Path, text: str, name: str = "config.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.data_source
def test_priority_env_over_config_over_default(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "lang: zh\noption_data_source: demo\n")
    monkeypatch.setattr(option_source_default, "DEFAULT_SOURCE", "yahoo")
    monkeypatch.setenv(ENV, "ibkr")
    assert S.data_source(cfg) == "ibkr"
    monkeypatch.delenv(ENV)
    assert S.data_source(cfg) == "demo"
    assert S.data_source(_config(tmp_path, "lang: zh\n", "other.md")) == "yahoo"
    assert S.data_source(tmp_path / "missing.md") == "yahoo"
    monkeypatch.setenv(ENV, "  ")
    assert S.data_source(cfg) == "demo"


@pytest.mark.data_source
def test_invalid_value_raises_settings_error_with_legal_values(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "ibkrx")
    with pytest.raises(S.SettingsError) as env_err:
        S.data_source(tmp_path / "missing.md")
    monkeypatch.delenv(ENV)
    with pytest.raises(S.SettingsError) as cfg_err:
        S.data_source(_config(tmp_path, "option_data_source: bloomberg\n"))
    monkeypatch.setattr(option_source_default, "DEFAULT_SOURCE", "nope")
    with pytest.raises(S.SettingsError):
        S.data_source(tmp_path / "missing.md")
    for err, bad in ((env_err, "ibkrx"), (cfg_err, "bloomberg")):
        text = str(err.value)
        assert bad in text
        assert all(name in text for name in ("ibkr", "yahoo", "demo"))


@pytest.mark.data_source
@pytest.mark.parametrize("line", ["option_data_source: yahoo", "  option_data_source :  Yahoo  ",
                                  "OPTION_DATA_SOURCE:YAHOO", "option_data_source：yahoo",
                                  "- option_data_source: yahoo"])
def test_config_line_parsing_tolerates_spaces_and_case(tmp_path, line):
    cfg = _config(tmp_path, f"# Portfolio Config\n\nportfolio_value: 1\n{line}\nlang: zh\n")
    assert S.data_source(cfg) == "yahoo"


@pytest.mark.data_source
def test_config_with_bom_is_read(tmp_path):
    cfg = tmp_path / "config.md"
    cfg.write_text("option_data_source: demo\n", encoding="utf-8-sig")
    assert S.data_source(cfg) == "demo"


def test_edition_matches_default_source():
    edition = option_source_default.EDITION
    assert {"overlay": "ibkr", "share": "yahoo"}[edition] == option_source_default.DEFAULT_SOURCE


def test_public_source_label_follows_edition():
    label = S.public_source_label()
    assert label == option_source_default.PUBLIC_SOURCE_LABEL
    assert re.fullmatch(r"[a-z][a-z-]*", label)
    if option_source_default.EDITION == "share":
        assert label == "option-investment"


def test_scripts_import_from_this_tree():
    assert Path(S.__file__).parent == Path(conftest.__file__).parent / "scripts"


@pytest.mark.data_source
@pytest.mark.parametrize("source, rel", [("ibkr", "data/paper-trading"),
                                         ("yahoo", "data/paper-trading"),
                                         ("demo", "data/paper-trading-demo")])
def test_paper_dir_per_source(tmp_path, monkeypatch, source, rel):
    monkeypatch.setenv(ENV, source)
    assert S.paper_dir(tmp_path) == tmp_path / Path(rel)


def test_profile_values_match_spec():
    expected = {
        "ibkr": ("IBKR 模拟网关", None, 0.05, None),
        "yahoo": ("Yahoo Finance", "low", 0.10, 0.05),
        "demo": ("演示（虚构数据）", "demo", 0.10, 0.05),
    }
    assert set(S.PROFILES) == set(expected)
    for name, (label, quality, max_spread, warn_spread) in expected.items():
        p = S.PROFILES[name]
        assert (p.name, p.label, p.quality, p.max_spread_pct, p.warn_spread_pct) == (
            name, label, quality, max_spread, warn_spread)
        assert isinstance(p.notes, tuple) and all(isinstance(n, str) for n in p.notes)
    assert S.PROFILES["ibkr"].notes == ()
    with pytest.raises(AttributeError):
        S.PROFILES["ibkr"].name = "yahoo"


@pytest.mark.data_source("yahoo")
def test_profile_and_is_ibkr_follow_the_setting(monkeypatch):
    assert S.profile() is S.PROFILES["yahoo"]
    assert S.is_ibkr() is False
    monkeypatch.setenv(ENV, "ibkr")
    assert S.profile() is S.PROFILES["ibkr"]
    assert S.is_ibkr() is True


def test_settings_error_is_same_class_in_ibkr_session():
    assert ibkr_session.SettingsError is S.SettingsError
    assert issubclass(S.SettingsError, Exception)


@pytest.mark.data_source
@pytest.mark.parametrize("source", ["ibkr", "yahoo"])
def test_yf_module_is_real_yfinance_for_ibkr_and_yahoo(monkeypatch, source):
    stub = types.ModuleType("yfinance")
    monkeypatch.setitem(sys.modules, "yfinance", stub)
    monkeypatch.setenv(ENV, source)
    assert S.yf_module() is stub


@pytest.mark.data_source("demo")
def test_yf_module_demo_is_the_offline_market():
    import demo_market
    assert S.yf_module() is demo_market


def test_option_settings_imports_no_data_layer():
    text = Path(S.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(spread_data|ibkr_session)\b", text, re.M)
