#!/usr/bin/env python3
"""/strategy-pick TICKER: pick 1-2 bullish or neutral option strategies, with every number shown.

    ./.venv/Scripts/python.exe scripts/strategy_pick.py TICKER [--view 看好|中性|不看好] \
        [--class bluechip|hot|etf]

Read-only (design docs/superpowers/specs/2026-10-25-strategy-pick-design.md, 6.1 and 6.13): all
data is fetched first, the tree decides, and only then are data/strategy-picks/<run date>-<TICKER>
.md and .json written. Exit codes: 0 files written (gate fail, bearish and short history
included), 1 gateway or market-data failure (nothing written), 2 bad arguments, 3 no usable
thesis, so the user must give a view (nothing written).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import option_labels
import option_settings
import spread_data
import strategy_pick_data as D
import strategy_pick_metrics as M
import strategy_pick_render as render
import strategy_pick_rules as R
import strategy_pick_steps as S
import strategy_pick_text as T
from ibkr_session import SettingsError
from strategy_pick_regime import classify
from strategy_pick_tree import (DecisionContext, GateInputs, NeedsView, decide, earnings_state,
                                iv_position, judge_gate, normalize_view, resolve_class,
                                resolve_view, select_oi_expiry)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / R.OUTPUT_DIR
WIKI_ROOT = REPO_ROOT / "wiki"
TICKER_PATTERN = r"[A-Z0-9][A-Z0-9.\-]{0,9}"
RERUN_HINT = "请用 --view 看好|中性|不看好（也接受 bull|neutral|bear）重跑：{ticker} --view 中性"
WARN = {
    "view_conflict": "看法来源：用户指定（{view}）；wiki 状态 {status} 对应 {wiki}，两者不一致，按用户指定",
    "thesis_stale": "论点 {age} 天未更新（超过 {limit} 天）",
    "positions_missing": "data/positions.md 不存在：视为未持有（没有建出这个文件）",
    "under_year": "历史不足一年（{n} 个交易日 < {full}），结论更不稳",
    "gaps": "数据有缺口：一年里缺 {n} 个交易日（> {limit}）",
    "history_short": "历史不足 {limit} 个交易日（{n} 个）：不分类、不给策略，不计算 IV 位置",
    "no_options": "网关没有给出期权数据（{err}）：按“没有期权”处理，门槛出局",
    "strikes_proxy": "±15% 内行权价个数按全链合并计数（代理，可能多算）",
    "iv_fallback": "IV 缺失，改用实现波动率自比（再退一步的代理）",
}
# A DataError from the option snapshot means "no options" (gate fail, report written) only when
# it says so; anything else (contract not found, no price) is missing data: exit 1 (design 9).
# Matched against ibkr_data._chain's "{symbol} 没有期权"; a test pins it to that source text.
NO_OPTIONS_MARKERS = ("没有期权",)


def ticker_arg(text: str) -> str:
    value = text.strip().upper()
    if not re.fullmatch(TICKER_PATTERN, value):
        raise argparse.ArgumentTypeError("ticker 只能是字母、数字、点和连字符，最长 10 位")
    return value


def view_arg(text: str) -> str:
    try:
        normalize_view(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("看法只接受 看好|中性|不看好|bull|neutral|bear") from exc
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strategy_pick", description="期权策略推荐（只读，不下单）")
    p.add_argument("ticker", type=ticker_arg)
    p.add_argument("--view", type=view_arg, default=None, help="看好|中性|不看好（优先于 wiki）")
    p.add_argument("--class", dest="asset_class", choices=R.CLASSES, default=None)
    return p


def ex_dividend(ticker: str, snapshot: date) -> date | None:
    """ETF only: ex-dividend date inside the earnings window (None on any failure)."""
    horizon = snapshot + timedelta(days=R.EARNINGS_WINDOW_DAYS)
    return spread_data.ex_dividend_in_window(ticker, snapshot, horizon)


# ---- fetch everything first ----

@dataclass(frozen=True)
class Fetched:
    ticker: str
    thesis: D.ThesisInfo
    view: object                 # ViewDecision
    history: D.PriceHistory
    metrics: M.Metrics
    info: dict
    holding_read: D.HoldingRead
    asset_class: str
    class_source: str
    class_warnings: tuple[str, ...]
    snap: D.OptionSnapshot | None
    snap_error: str | None
    atm_oi: int | None
    next_earnings: date | None
    ex_div: date | None
    portfolio_value: float | None


def _metrics(history: D.PriceHistory) -> M.Metrics:
    try:
        return M.compute_metrics(list(history.closes), list(history.volumes), list(history.dates))
    except ValueError as exc:
        raise D.MarketDataError(f"行情数据不可用：{exc}") from exc


def _class(ticker: str, cli_class, thesis, info: dict, read: D.HoldingRead):
    return resolve_class(cli_class, thesis.is_etf_page, info.get("quote_type"),
                         read.in_etf_table, D.wiki_tier(ticker, WIKI_ROOT), read.stock_type)


def _options(ticker: str, snapshot: date):
    """(snapshot, error text); a lost gateway or an unknown contract raises, 'no options' is a gate fail."""
    try:
        return D.option_snapshot(ticker, snapshot), None
    except spread_data.SourceUnavailable:
        raise
    except spread_data.DataError as exc:
        if not any(m in str(exc).lower() for m in NO_OPTIONS_MARKERS):
            raise
        return None, str(exc)


def _atm_oi(ticker: str, snap: D.OptionSnapshot | None) -> int | None:
    if snap is None or snap.expiry_used is None or not snap.atm_put:
        return None
    expiry = select_oi_expiry(list(snap.expiries), snap.snapshot_date, snap.expiry_used)
    return D.atm_open_interest(ticker, expiry, float(snap.atm_put["strike"]))


def fetch(ticker: str, args, thesis, view, today: date) -> Fetched:
    history = D.load_history(ticker)
    metrics = _metrics(history)
    snapshot = history.dates[-1]
    info, read = D.load_info(ticker), D.read_holding(ticker, today)
    cls, source, cls_warn = _class(ticker, args.asset_class, thesis, info, read)
    snap, snap_error = _options(ticker, snapshot)
    is_etf = cls in R.NO_EARNINGS_CLASSES
    return Fetched(ticker, thesis, view, history, metrics, info, read, cls, source, tuple(cls_warn),
                   snap, snap_error, _atm_oi(ticker, snap),
                   None if is_etf else D.next_earnings(ticker, snapshot),
                   ex_dividend(ticker, snapshot) if is_etf else None, D.portfolio_value())


# ---- decide ----

def _expiries_90d(snap: D.OptionSnapshot | None, snapshot: date) -> int:
    if snap is None:
        return 0
    return sum(1 for e in snap.expiries if 0 < (e - snapshot).days <= R.GATE_EXPIRY_HORIZON_DAYS)


def gate_inputs(f: Fetched) -> GateInputs:
    s, snapshot = f.snap, f.history.dates[-1]
    put, call = (s.atm_put or {}) if s else {}, (s.atm_call or {}) if s else {}
    return GateInputs(f.ticker, f.asset_class, s.spot if s else f.metrics.last_close,
                      f.info.get("market_cap"), f.metrics.dollar_volume_median,
                      _expiries_90d(s, snapshot), s.expiry_used if s else None,
                      s.strikes_15pct if s else None, put.get("bid"), put.get("ask"),
                      call.get("bid"), call.get("ask"), f.atm_oi)


def _iv(f: Fetched):
    m = f.metrics
    if m.history_short:  # not classified at all (6.5), so no IV label either
        return None
    try:
        return iv_position(f.snap.atm_iv if f.snap else None, m.rv21_series, m.rv21_current)
    except ValueError:
        return None


def _input_warnings(f: Fetched) -> list[str]:
    v, t, m = f.view, f.thesis, f.metrics
    out = list(f.class_warnings)
    if v.conflict:
        out.append(WARN["view_conflict"].format(view=S.VIEW_LABELS[v.view], status=t.status,
                                                wiki=S.VIEW_LABELS[v.wiki_view]))
    if t.age_days is not None and t.age_days > R.THESIS_STALE_DAYS:
        out.append(WARN["thesis_stale"].format(age=t.age_days, limit=R.THESIS_STALE_DAYS))
    if f.holding_read.positions_missing:
        out.append(WARN["positions_missing"])
    out += list(f.holding_read.warnings)
    if m.history_short:
        out.append(WARN["history_short"].format(limit=R.MIN_HISTORY_DAYS, n=m.n_days))
    elif m.history_under_year:
        out.append(WARN["under_year"].format(n=m.n_days, full=R.LOOKBACK_DAYS))
    if m.missing_sessions > R.MISSING_SESSIONS_WARN:
        out.append(WARN["gaps"].format(n=m.missing_sessions, limit=R.MISSING_SESSIONS_WARN))
    if f.snap_error:
        out.append(_no_options_text(f.snap_error))
    if f.snap is not None and f.snap.strikes_15pct is not None and f.snap.strikes_15pct_proxy:
        out.append(WARN["strikes_proxy"])
    return out


def _no_options_text(err: str) -> str:
    """"网关" is the IBKR gateway; another source names itself (IBKR text unchanged)."""
    text = WARN["no_options"].format(err=err)
    return text if option_settings.is_ibkr() else text.replace(
        "网关", option_settings.profile().label + " ", 1)


def _yahoo_gate_warnings(gate) -> list[str]:
    """Yahoo only: say plainly that a stale ATM quote left the spread gate unchecked.

    The gate treats an unknown spread as `unchecked` (never a fail) in every mode; IBKR keeps
    its single existing warning, so only a non-IBKR run gets this extra line."""
    if option_settings.is_ibkr() or T.WARN["stale_quotes"] not in gate.warnings:
        return []
    return [T.WARN["stale_quotes_yahoo"]]


def _drop_earnings_noise(warnings: list[str], decision, earnings) -> list[str]:
    """The tree warns 'expiry before earnings' even when no pick carries that constraint."""
    if earnings.date is None or any(R.EARNINGS_BEFORE_EXPIRY_FLAG in p.flags for p in decision.picks):
        return warnings
    noise = T.WARN["earnings_avoid"].format(date=earnings.date.isoformat())
    return [w for w in warnings if w != noise]


def facts(f: Fetched, run_date: date) -> render.RunFacts:
    m, snap, snapshot = f.metrics, f.snap, f.history.dates[-1]
    gate = judge_gate(gate_inputs(f))
    regime = None if m.history_short else classify(m, f.asset_class)
    iv = _iv(f)
    days = (snap.expiry_used - snapshot).days if snap and snap.expiry_used else None
    holding = D.holding_from_read(f.holding_read)
    earnings = earnings_state(f.asset_class, f.next_earnings, snapshot, f.ex_div)
    ctx = DecisionContext(f.ticker, f.view.view, holding, regime.leaf if regime else None, iv,
                          f.asset_class, earnings, snap.spot if snap else m.last_close, days,
                          f.portfolio_value, gate_passed=gate.passed,
                          history_short=m.history_short)
    decision = decide(ctx)
    warnings = _input_warnings(f) + list(gate.warnings) + _yahoo_gate_warnings(gate)
    warnings += list(regime.warnings) if regime else []
    if iv is not None and iv.method == R.IV_METHOD_FALLBACK:
        warnings.append(WARN["iv_fallback"])
    warnings = _drop_earnings_noise(warnings + list(decision.warnings), decision, earnings)
    return render.RunFacts(f.ticker, run_date, snapshot, f.asset_class, f.class_source, gate,
                           f.view, f.thesis.status, f.thesis.verdict_text, f.thesis.age_days, m,
                           regime, iv, snap.expiry_used if snap else None, days, holding,
                           earnings, decision, tuple(dict.fromkeys(warnings)))


# ---- write (only after everything above succeeded) ----

def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _swap_in(pairs: list[tuple[Path, Path]]) -> None:
    """Move each temp onto its target; on any failure put every previous file back.

    Two renames can never be one atomic step, so the old pair is first moved aside to .bak and
    restored if either new file fails to land: the reader sees the old pair or the new pair.
    """
    backups, placed = [], []
    try:
        for _, target in pairs:
            if target.exists():
                bak = target.with_name(target.name + ".bak")
                os.replace(target, bak)
                backups.append((bak, target))
        for tmp, target in pairs:
            os.replace(tmp, target)
            placed.append(target)
    except OSError:
        for target in placed:
            target.unlink(missing_ok=True)
        for bak, target in backups:
            os.replace(bak, target)
        raise
    for bak, _ in backups:
        bak.unlink(missing_ok=True)


def write_outputs(result: dict, out_dir: Path) -> tuple[Path, Path]:
    """md first, then json; temp files, so a failed write leaves the previous pair (or nothing)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result['run_date']}-{result['ticker']}"
    md_path, json_path = out_dir / (stem + ".md"), out_dir / (stem + ".json")
    md_tmp, json_tmp = out_dir / (stem + ".md.tmp"), out_dir / (stem + ".json.tmp")
    try:
        _write(md_tmp, render.render_markdown(result))
        _write(json_tmp, render.render_json(result))
        _swap_in([(md_tmp, md_path), (json_tmp, json_path)])
    finally:
        md_tmp.unlink(missing_ok=True)
        json_tmp.unlink(missing_ok=True)
    return md_path, json_path


# ---- main ----

DEMO_OUTPUT_SUBDIR = "demo"


def _output_dir() -> Path:
    """Demo picks go to their own subfolder so fictional files never mix with real ones."""
    return OUTPUT_DIR / DEMO_OUTPUT_SUBDIR if option_settings.data_source() == "demo" else OUTPUT_DIR


def _parse(argv) -> argparse.Namespace | int:
    try:
        return build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else R.EXIT_USAGE


def _view(args, thesis):
    status = thesis.status if thesis.found else None  # no thesis.md counts as no thesis (6.3)
    return resolve_view(status, args.view)


def _source_down_message(exc: Exception) -> str:
    """The abort line for an unreachable data source; the IBKR text is unchanged."""
    if option_settings.is_ibkr():
        return f"IBKR 网关不可用：{exc}；若是 10197 竞争会话，见 docs/ibkr-options-data.md"
    label = option_settings.profile().label
    # The Yahoo layer's messages already name the source ("连不上 Yahoo Finance：…").
    return str(exc) if label in str(exc) else f"{option_labels.source_unavailable_text()}：{exc}"


def _fail(message: str) -> int:
    print(f"错误：{message}（什么都没写）", file=sys.stderr)
    return R.EXIT_DATA_FAIL


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = _parse(argv)
    if isinstance(args, int):
        return args
    try:
        option_labels.print_console_header()
    except SettingsError as exc:
        return _fail(str(exc))
    run_date, ticker = today or date.today(), args.ticker
    thesis = D.read_thesis(ticker, WIKI_ROOT, run_date)
    view = _view(args, thesis)
    if isinstance(view, NeedsView):
        print(view.prompt.format(ticker=ticker))
        print(RERUN_HINT.format(ticker=ticker))
        return R.EXIT_NEEDS_VIEW
    try:
        fetched = fetch(ticker, args, thesis, view, run_date)
    except spread_data.SourceUnavailable as exc:
        return _fail(_source_down_message(exc))
    except (D.MarketDataError, SettingsError, spread_data.DataError) as exc:
        return _fail(str(exc))
    result = render.build_result(facts(fetched, run_date))
    try:
        md_path, json_path = write_outputs(result, _output_dir())
    except OSError as exc:
        return _fail(f"写报告失败：{exc}")
    print(render.render_console(result, md_path, json_path))
    return R.EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
