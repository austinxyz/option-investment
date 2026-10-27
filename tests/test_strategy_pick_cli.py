"""CLI tests for /strategy-pick: exit codes, fetch-everything-then-write, file names (design 6.13, 9).

ZZZQ / ZZ.Q are invented. Every adapter is stubbed (strategy_pick_cli_stubs); the output dir is
tmp_path, so a run that should write nothing must leave tmp_path empty.
"""
import hashlib
import json
from datetime import date

import pytest

from strategy_pick_cli_stubs import (EXPIRIES, RUN_DATE, history, install, outputs, run,
                                     snapshot, thesis)
from strategy_pick_fixtures import REPO_ROOT
import positions
import spread_data
import strategy_pick as cli
import strategy_pick_data as D
from ibkr_session import GatewayUnavailable

# Captured before any test stubs the module.
REAL_OPTION_SNAPSHOT = D.option_snapshot
REAL_READ_HOLDING = D.read_holding
REAL_READ_THESIS = D.read_thesis
REAL_WIKI_TIER = D.wiki_tier
REAL_PORTFOLIO_VALUE = D.portfolio_value


def written(out_dir) -> list[str]:
    return sorted(p.name for p in out_dir.iterdir()) if out_dir.exists() else []


def load(out_dir, ticker="ZZZQ") -> dict:
    return json.loads(outputs(out_dir, ticker)[1].read_text(encoding="utf-8"))


def test_cli_base_example_writes_two_files_exit_0(monkeypatch, tmp_path, capsys):
    install(monkeypatch, tmp_path)
    assert run(["ZZZQ"]) == 0
    md, js = outputs(tmp_path)
    assert written(tmp_path) == sorted([md.name, js.name])
    assert md.name == "2026-10-25-ZZZQ.md"
    doc = load(tmp_path)
    assert doc["run_date"] == "2026-10-25" and doc["snapshot_date"] == "2026-10-23"
    out = capsys.readouterr().out
    assert str(md) in out and str(js) in out


def test_cli_gateway_down_exit_1_no_files(monkeypatch, tmp_path, capsys):
    install(monkeypatch, tmp_path, option_snapshot=GatewayUnavailable("gateway not reachable"))
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []
    assert "gateway not reachable" in capsys.readouterr().err


def test_cli_gateway_down_message_is_ibkr_text(monkeypatch, tmp_path, capsys):
    install(monkeypatch, tmp_path, option_snapshot=GatewayUnavailable("gateway not reachable"))
    assert run(["ZZZQ"]) == 1
    assert "IBKR 网关不可用：gateway not reachable；若是 10197 竞争会话" in capsys.readouterr().err


@pytest.mark.data_source("yahoo")
def test_cli_yahoo_down_names_yahoo_not_ibkr(monkeypatch, tmp_path, capsys):
    down = spread_data.SourceUnavailable("连不上 Yahoo Finance：timed out")
    install(monkeypatch, tmp_path, option_snapshot=down)
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []
    err = capsys.readouterr().err
    # The layer text already names the source: no doubled "不可用：连不上".
    assert "错误：连不上 Yahoo Finance：timed out（什么都没写）" in err
    assert "不可用：连不上" not in err
    assert "IBKR" not in err and "10197" not in err


def test_cli_gateway_drop_midway_exit_1_no_files(monkeypatch, tmp_path):
    # The real adapter runs: last close and expiries succeed, then the gateway drops on the chain.
    install(monkeypatch, tmp_path, option_snapshot=REAL_OPTION_SNAPSHOT)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (date(2026, 10, 23), 100.0))
    monkeypatch.setattr(spread_data, "expirations", lambda t: [e.isoformat() for e in EXPIRIES])
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: [90.0 + i for i in range(21)])

    def boom(*a, **k):
        raise GatewayUnavailable("connection dropped")
    monkeypatch.setattr(spread_data, "fetch_puts", boom)
    monkeypatch.setattr(spread_data, "fetch_calls", boom)
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []


def test_cli_gateway_drop_on_chain_strikes_exit_1_no_files(monkeypatch, tmp_path, capsys):
    install(monkeypatch, tmp_path, option_snapshot=REAL_OPTION_SNAPSHOT)
    monkeypatch.setattr(spread_data, "last_close", lambda t: (date(2026, 10, 23), 100.0))
    monkeypatch.setattr(spread_data, "expirations", lambda t: [e.isoformat() for e in EXPIRIES])

    def boom(t):
        raise GatewayUnavailable("dropped on chain strikes")
    rows = [{"strike": 90.0 + i, "bid": 1.0, "ask": 1.1, "last": 1.05, "iv": 0.32, "delta": 0.5}
            for i in range(21)]
    monkeypatch.setattr(spread_data, "chain_strikes", boom)
    monkeypatch.setattr(spread_data, "fetch_puts", lambda *a, **k: list(rows))
    monkeypatch.setattr(spread_data, "fetch_calls", lambda *a, **k: list(rows))
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []
    assert "dropped on chain strikes" in capsys.readouterr().err


def test_cli_market_data_error_exit_1_no_files(monkeypatch, tmp_path, capsys):
    install(monkeypatch, tmp_path, load_history=D.MarketDataError("ZZZQ 没有行情数据"))
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []
    assert "没有行情数据" in capsys.readouterr().err


def test_cli_no_options_data_error_is_gate_fail_not_gateway(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, option_snapshot=spread_data.DataError("ZZZQ 没有期权"))
    assert run(["ZZZQ"]) == 0
    doc = load(tmp_path)
    assert doc["leaf_id"] == "gate_fail" and doc["gate"]["passed"] is False
    assert [p["strategy"] for p in doc["picks"]] == ["no_options"]
    assert any("ZZZQ 没有期权" in w for w in doc["warnings"])


def test_cli_no_thesis_exit_3_no_files_and_prompt_text(monkeypatch, tmp_path, capsys):
    calls = install(monkeypatch, tmp_path,
                    read_thesis=thesis(status=None, found=False, has_overview=False,
                                       has_thesis=False, verdict_text=None, age_days=None))
    assert run(["ZZZQ"]) == 3
    assert written(tmp_path) == []
    out = capsys.readouterr().out
    assert "ZZZQ 没有可用论点，请告诉我你的看法：看好 / 中性 / 不看好" in out
    assert "--view" in out
    assert "option_snapshot" not in calls


@pytest.mark.parametrize("status", ["under_review", "unrecognized"])
def test_cli_status_needs_user_exit_3(monkeypatch, tmp_path, status):
    install(monkeypatch, tmp_path, read_thesis=thesis(status=status))
    assert run(["ZZZQ"]) == 3
    assert written(tmp_path) == []


def test_cli_overview_without_thesis_exit_3(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, read_thesis=thesis(found=False, has_thesis=False))
    assert run(["ZZZQ"]) == 3


def test_cli_view_rerun_after_exit_3(monkeypatch, tmp_path):
    no_thesis = thesis(status=None, found=False, has_overview=False, has_thesis=False)
    install(monkeypatch, tmp_path, read_thesis=no_thesis)
    assert run(["ZZZQ"]) == 3
    assert run(["ZZZQ", "--view", "中性"]) == 0
    doc = load(tmp_path)
    assert doc["view"] == "neutral" and doc["view_source"] == "user"
    assert doc["leaf_id"] == "neutral.not_held.steady_up.high"
    assert [p["strategy"] for p in doc["picks"]] == ["cash_secured_put", "bull_put_spread"]
    assert all("far_otm" in p["flags"] for p in doc["picks"])


def test_cli_view_conflict_reports_both(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, read_thesis=thesis(status="avoid"))
    assert run(["ZZZQ", "--view", "bull"]) == 0
    doc = load(tmp_path)
    assert doc["view"] == "bull" and doc["thesis_status"] == "avoid"
    conflict = [w for w in doc["warnings"] if "wiki" in w and "用户指定" in w]
    assert conflict and "看好" in conflict[0] and "不看好" in conflict[0]
    assert "bull" not in conflict[0] and "bear" not in conflict[0]


@pytest.mark.parametrize("argv", [[], ["ZZZQ", "--view", "maybe"], ["ZZZQ", "--class", "small"],
                                  ["ZZ/Q"], ["../ZZZQ"], ["ZZZQ", "extra"]])
def test_cli_bad_args_exit_2(monkeypatch, tmp_path, argv):
    install(monkeypatch, tmp_path)
    assert run(argv) == 2
    assert written(tmp_path) == []


def test_cli_gate_fail_writes_file_exit_0(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, load_history=history(volume=130_000.0))  # 130k x 92 = $12M
    assert run(["ZZZQ"]) == 0
    doc = load(tmp_path)
    assert doc["gate"]["passed"] is False and doc["leaf_id"] == "gate_fail"
    failed = [i["constant"] for i in doc["gate"]["items"] if i["result"] == "fail"]
    assert failed == ["MIN_DOLLAR_VOLUME"]
    md = outputs(tmp_path)[0].read_text(encoding="utf-8")
    assert "不做期权建议" in md


def test_cli_atm_oi_measured_on_monthly_not_thin_weekly(monkeypatch, tmp_path):
    """Real SPY 2026-10-26: the 20-60d pick was the 11/27 weekly (ATM OI 276 < 500) while the
    11/20 monthly held thousands. OI is read on the monthly in the window; spreads keep the weekly."""
    seen = []

    def oi(ticker, expiry, strike):
        seen.append((expiry, strike))
        return 80 if expiry == date(2026, 11, 27) else 6800

    install(monkeypatch, tmp_path, atm_open_interest=oi,
            option_snapshot=snapshot(expiry_used=date(2026, 11, 27)))
    assert run(["ZZZQ"]) == 0
    doc = load(tmp_path)
    assert seen == [(date(2026, 11, 20), 100.0)]
    atm = [i for i in doc["gate"]["items"] if i["constant"] == "MIN_ATM_OI"][0]
    assert atm["value"] == 6800 and atm["result"] == "pass"


def test_cli_history_short_writes_file_exit_0(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, load_history=history(150))
    assert run(["ZZZQ"]) == 0
    doc = load(tmp_path)
    assert doc["leaf_id"] == "history_short"
    assert doc["regime_leaf"] is None and doc["vol_tag"] is None
    assert [p["strategy"] for p in doc["picks"]] == ["no_options"]


def test_cli_history_under_year_warns(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, load_history=history(210))
    assert run(["ZZZQ"]) == 0
    doc = load(tmp_path)
    assert doc["direction_tag"] == "unclear"
    assert any("历史不足一年" in w for w in doc["warnings"])
    assert any("MA200 斜率无法计算" in w for w in doc["warnings"])


def test_cli_same_day_rerun_overwrites(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    assert run(["ZZZQ"]) == 0
    install(monkeypatch, tmp_path, read_thesis=thesis(status="watch"))
    assert run(["ZZZQ"]) == 0
    assert len(written(tmp_path)) == 2
    assert load(tmp_path)["view"] == "neutral"


def test_cli_lowercase_and_dotted_ticker(monkeypatch, tmp_path):
    seen = []
    install(monkeypatch, tmp_path, load_history=lambda t: seen.append(t) or history())
    assert run(["zzzq"]) == 0
    assert seen == ["ZZZQ"] and load(tmp_path)["ticker"] == "ZZZQ"
    assert run(["ZZ.Q"]) == 0
    assert (tmp_path / "2026-10-25-ZZ.Q.md").exists()
    assert (tmp_path / "2026-10-25-ZZ.Q.json").exists()
    assert load(tmp_path, "ZZ.Q")["ticker"] == "ZZ.Q"


def test_cli_positions_missing_warns_and_not_created(monkeypatch, tmp_path):
    missing = tmp_path / "nowhere" / "positions.md"
    monkeypatch.setattr(positions, "POSITIONS_FILE", missing)
    out = tmp_path / "out"
    install(monkeypatch, out, read_holding=REAL_READ_HOLDING)
    assert run(["ZZZQ"]) == 0
    doc = load(out)
    assert doc["holding"]["treated_as_held"] is False
    assert any("positions.md" in w for w in doc["warnings"])
    assert not missing.exists() and not missing.parent.exists()


def _tree_digest(root):
    if root.is_file():
        st = root.stat()
        return {str(root): (st.st_size, st.st_mtime_ns)}
    if not root.exists():
        return {}
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def _tmp_wiki(root):
    page = root / "tickers" / "ZZZQ"
    page.mkdir(parents=True)
    overview = ["**Status**: Active", "**Last updated**: 2026-08-30", ""]
    thesis_md = ["**As of**: 2026-08-30", "**Verdict**: Invented compounder", ""]
    (page / "overview.md").write_text(chr(10).join(overview), encoding="utf-8")
    (page / "thesis.md").write_text(chr(10).join(thesis_md), encoding="utf-8")
    (root / "sectors").mkdir()
    return root


def test_cli_never_writes_outside_output_dir(monkeypatch, tmp_path):
    watched = [REPO_ROOT / "wiki", REPO_ROOT / "outputs", REPO_ROOT / "data" / "paper-trading",
               REPO_ROOT / "data" / "positions.md", REPO_ROOT / "data" / "config.md",
               REPO_ROOT / "data" / "strategy-picks"]
    before = [_tree_digest(p) for p in watched]
    out = tmp_path / "out"
    install(monkeypatch, out)
    assert run(["ZZZQ"]) == 0
    assert run(["ZZZQ", "--view", "中性", "--class", "hot"]) == 0
    # Real read-only adapters: thesis and tier from a wiki copy, holding and portfolio value from
    # the real data/ files (ZZZQ is invented, so nothing matches; the files must stay untouched).
    install(monkeypatch, out, read_thesis=REAL_READ_THESIS, wiki_tier=REAL_WIKI_TIER,
            read_holding=REAL_READ_HOLDING, portfolio_value=REAL_PORTFOLIO_VALUE)
    monkeypatch.setattr(cli, "WIKI_ROOT", _tmp_wiki(tmp_path / "wiki"))
    assert run(["ZZZQ", "--class", "bluechip"]) == 0
    assert load(out)["thesis_status"] == "active"
    assert [_tree_digest(p) for p in watched] == before
    assert written(out) == ["2026-10-25-ZZZQ.json", "2026-10-25-ZZZQ.md"]


def test_cli_write_failure_leaves_no_orphan(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    real_replace, calls = cli.os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:  # the .md is in place, the .json move fails
            raise OSError("disk full")
        real_replace(src, dst)
    monkeypatch.setattr(cli.os, "replace", flaky)
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []


def test_gitignore_has_strategy_picks():
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/strategy-picks/" in [line.strip() for line in lines]


def test_cli_module_imports_no_broker_or_ledger_code():
    files = sorted((REPO_ROOT / "scripts").glob("strategy_pick*.py"))
    assert len(files) >= 9
    src = "".join(f.read_text(encoding="utf-8") for f in files)
    for banned in ("broker_orders", "spread_ledger", "paper_shares", "placeOrder",
                   "positions.add", "positions.update", "positions.close", "_ensure_file_exists"):
        assert banned not in src, banned


@pytest.mark.parametrize("message", ["ZZQZ 在 IBKR 找不到这只股票",
                                     "No security definition found for ZZQZ",
                                     "取不到 ZZQZ 的现价"])
def test_cli_contract_not_found_is_missing_data_exit_1(monkeypatch, tmp_path, message):
    install(monkeypatch, tmp_path, option_snapshot=spread_data.DataError(message))
    assert run(["ZZQZ"]) == 1
    assert written(tmp_path) == []


def _ibkr_data_error_messages() -> list[str]:
    """Literal text of every `raise DataError(f"...")` in ibkr_data, with {symbol} filled in."""
    import re
    source = (REPO_ROOT / "scripts" / "ibkr_data.py").read_text(encoding="utf-8")
    found = re.findall(r'raise DataError\(f"([^"]+)"\)', source)
    assert found, "no DataError messages found in ibkr_data.py"
    return [re.sub(r"\{symbol\}", "ZZZQ", m) for m in found]


def test_no_options_markers_pinned_to_ibkr_data_messages():
    messages = _ibkr_data_error_messages()
    no_options = [m for m in messages if any(k in m for k in cli.NO_OPTIONS_MARKERS)]
    assert no_options == ["ZZZQ 没有期权"], no_options
    # Every marker must still occur in a real ibkr_data message, so a reworded message fails here.
    for marker in cli.NO_OPTIONS_MARKERS:
        assert any(marker in m for m in messages), marker


@pytest.mark.parametrize("message", [m for m in _ibkr_data_error_messages()
                                     if "没有期权" in m])
def test_cli_no_options_is_gate_fail_exit_0(monkeypatch, tmp_path, message):
    install(monkeypatch, tmp_path, option_snapshot=spread_data.DataError(message))
    assert run(["ZZZQ"]) == 0
    assert load(tmp_path)["leaf_id"] == "gate_fail"


@pytest.mark.parametrize("message", ["ZZZQ 在 IBKR 找不到这只股票", "no option chain"])
def test_cli_other_data_errors_exit_1(monkeypatch, tmp_path, message):
    install(monkeypatch, tmp_path, option_snapshot=spread_data.DataError(message))
    assert run(["ZZZQ"]) == 1
    assert written(tmp_path) == []


def test_cli_rerun_write_failure_keeps_previous_pair(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    assert run(["ZZZQ"]) == 0
    md, js = outputs(tmp_path)
    old_md, old_js = md.read_bytes(), js.read_bytes()
    install(monkeypatch, tmp_path, read_thesis=thesis(status="watch"))
    real_replace = cli.os.replace

    def flaky(src, dst):
        if str(src).endswith(".json.tmp"):
            raise OSError("disk full")
        real_replace(src, dst)
    monkeypatch.setattr(cli.os, "replace", flaky)
    assert run(["ZZZQ"]) == 1
    assert md.read_bytes() == old_md and js.read_bytes() == old_js
    assert written(tmp_path) == sorted([md.name, js.name])
