"""Stubs for the /strategy-pick CLI and render tests: design section 10 (ZZZQ, invented).

Every adapter in strategy_pick_data is replaced, so nothing touches the network, the wiki or
data/positions.md. compute_metrics is wrapped so the hand-given section-10 values (RV1y 30.0%,
231 RV21 windows with 174 at or below 0.32, current RV21 0.26, RV63 0.27) replace the ones the
price fixture happens to produce; everything else comes from base_closes().
"""
import json
from dataclasses import replace
from datetime import date

from strategy_pick_fixtures import SNAPSHOT, base_closes, base_rv21, weekday_dates
import strategy_pick as cli
import strategy_pick_data as D
import strategy_pick_metrics as M

RUN_DATE = date(2026, 10, 25)
EXPIRY = date(2026, 11, 20)
# 9 expiries within 90 days of 2026-10-23, plus one beyond.
EXPIRIES = (date(2026, 10, 30), date(2026, 11, 6), date(2026, 11, 13), EXPIRY,
            date(2026, 11, 27), date(2026, 12, 4), date(2026, 12, 11), date(2026, 12, 18),
            date(2027, 1, 15), date(2027, 3, 19))
ATM_PUT = {"strike": 100.0, "bid": 2.90, "ask": 3.02, "iv": 0.32}
ATM_CALL = {"strike": 100.0, "bid": 3.10, "ask": 3.24, "iv": 0.32}
_REAL_COMPUTE = M.compute_metrics


def history(n: int = 252, volume: float = 4_200_000.0) -> D.PriceHistory:
    closes = base_closes()[-n:] if n <= 252 else base_closes()
    return D.PriceHistory(tuple(weekday_dates(len(closes))), tuple(closes),
                          tuple([volume] * len(closes)))


def hand_metrics(*args, **kw):
    m = _REAL_COMPUTE(*args, **kw)
    if m.n_days < 252:
        return m
    return replace(m, rv1y=0.30, rv21_series=tuple(base_rv21()), rv21_current=0.26, rv63=0.27)


def snapshot(**over) -> D.OptionSnapshot:
    base = dict(snapshot_date=SNAPSHOT, spot=100.0, expiries=EXPIRIES, expiry_used=EXPIRY,
                puts=(ATM_PUT,), calls=(ATM_CALL,), atm_iv=0.32, atm_put=ATM_PUT,
                atm_call=ATM_CALL, strikes_15pct=31)
    base.update(over)
    return D.OptionSnapshot(**base)


def thesis(**over) -> D.ThesisInfo:
    base = dict(status="active", verdict_text="Invented compounder, thesis intact", age_days=56,
                is_etf_page=False, found=True, has_overview=True, has_thesis=True)
    base.update(over)
    return D.ThesisInfo(**base)


def install(monkeypatch, out_dir, **over):
    """Base-example stubs; keyword overrides replace one adapter (a value or a callable)."""
    calls = []

    def const(name, value):
        def fn(*a, **k):
            calls.append(name)
            if isinstance(value, BaseException):
                raise value
            return value
        return fn

    values = dict(load_history=history(), load_info={"market_cap": 180e9, "quote_type": "EQUITY"},
                  option_snapshot=snapshot(), atm_open_interest=6800,
                  next_earnings=date(2027, 1, 28), read_thesis=thesis(), wiki_tier="🏦",
                  read_holding=D.HoldingRead((), None, False, False), portfolio_value=200_000.0)
    values.update(over)
    for name, value in values.items():
        fn = value if callable(value) else const(name, value)
        monkeypatch.setattr(D, name, fn)
    monkeypatch.setattr(M, "compute_metrics", hand_metrics)
    monkeypatch.setattr(cli, "ex_dividend", lambda *a: None)
    monkeypatch.setattr(cli, "OUTPUT_DIR", out_dir)
    return calls


def run(argv, today=RUN_DATE) -> int:
    return cli.main(argv, today=today)


def outputs(out_dir, ticker="ZZZQ", day=RUN_DATE):
    stem = f"{day.isoformat()}-{ticker}"
    return out_dir / (stem + ".md"), out_dir / (stem + ".json")


def base_result(monkeypatch, out_dir, argv=("ZZZQ",), **over) -> tuple[dict, str]:
    install(monkeypatch, out_dir, **over)
    assert run(list(argv)) == 0
    md, js = outputs(out_dir, argv[0].upper())
    return json.loads(js.read_text(encoding="utf-8")), md.read_text(encoding="utf-8")
