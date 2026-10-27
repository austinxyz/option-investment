"""CLI tests for scripts/collar_screen.py. Gateway, market data and positions are faked."""
from datetime import date
from pathlib import Path
import hashlib
import json
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import collar_screen
import holdings
import paper_shares
import ibkr_session
import spread_data
import spread_ledger
import spread_sim
from spread_fixtures import make_call_chain

SNAP = date(2026, 10, 16)
TODAY = date(2026, 10, 17)
EXPIRY = "2026-11-20"   # 35 days after SNAP
LONG_EXPIRY = "2027-01-15"  # 91 days after SNAP
AFTER = date(2026, 11, 23)
DOWN = ibkr_session.GatewayUnavailable("IBKR 网关不可达：gw.test:14999")


def real_lot(ticker="ZZZQ", shares=300.0, cost=90.0, taxable=None, entry=None):
    return holdings.HeldLot(
        source="real", lot_id=None, ticker=ticker, shares=shares, cost_basis=cost,
        original_cost=cost, reference_price=cost, cost_estimated=False, accounts=1,
        label=f"{ticker}（真实）", has_taxable_account=taxable, earliest_entry_date=entry)


def _row(strike, mid, spread=0.01):
    return {"strike": strike, "bid": mid * (1 - spread / 2), "ask": mid * (1 + spread / 2),
            "last": mid, "iv": 0.35}


def put_rows(strikes, wide_at_or_below=None):
    rows = []
    for k in strikes:
        mid = max(0.3, (k - 60) * 0.05)
        wide = wide_at_or_below is not None and k <= wide_at_or_below
        rows.append(_row(k, mid, 0.5 if wide else 0.01))
    return rows


@pytest.fixture
def env(monkeypatch, tmp_path):
    m = {"real": [real_lot()], "gateway": None, "spot": 100.0, "wide": None, "put_calls": [],
         "listed": [float(k) for k in range(60, 141)], "expirations": ["2026-10-23", EXPIRY, LONG_EXPIRY],
         "snapshot": SNAP, "missing": None}
    monkeypatch.setattr(ibkr_session, "ensure_gateway",
                        lambda: (_ for _ in ()).throw(m["gateway"]) if m["gateway"] else None)
    monkeypatch.setattr(holdings, "real_lots", lambda *a, **k: list(m["real"]))
    monkeypatch.setattr(spread_data, "last_close", lambda t: (m["snapshot"], m["spot"]))
    monkeypatch.setattr(spread_data, "moving_averages",
                        lambda t: {"snapshot_date": SNAP, "close": 100.0, "ma50": 95.0, "ma200": 90.0})
    monkeypatch.setattr(spread_data, "expirations", lambda t: list(m["expirations"]))
    monkeypatch.setattr(spread_data, "chain_strikes", lambda t: list(m["listed"]))

    def fetch_puts(t, e, strikes=(), window_pct=None, window_limit=None):
        m["put_calls"].append({"ticker": t, "expiry": e, "strikes": tuple(strikes),
                               "window_pct": window_pct, "window_limit": window_limit})
        near = [float(k) for k in range(95, 100)]
        wanted = sorted(set(near) | set(strikes))
        if m["missing"] is not None:   # this expiry's grid lacks some chain strikes
            wanted = [k for k in wanted if not m["missing"](k)]
        return put_rows(wanted, m["wide"])

    monkeypatch.setattr(spread_data, "fetch_puts", fetch_puts)
    monkeypatch.setattr(spread_data, "fetch_calls", lambda t, e, strikes=(), **kw: make_call_chain(
        spot=m["spot"], iv=0.35, days=35, strikes=[float(k) for k in range(int(m["spot"]) + 1, int(m["spot"] * 1.3))],
        quote_spread=0.02))
    monkeypatch.setattr(spread_data, "earnings_status", lambda t, s, e: "no")
    monkeypatch.setattr(spread_data, "ex_dividend_in_window", lambda t, s, e: None)
    monkeypatch.setattr(spread_data, "market_open_now", lambda: False)
    m["ledger"] = tmp_path / "ledger"
    return m


def run(env, *argv, today=TODAY):
    return collar_screen.main(["--ledger-dir", str(env["ledger"]), *argv], today=today)


def sug(env, name):
    return env["ledger"] / "suggestions" / name


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cand(env, slug="real", ticker="ZZZQ"):
    return read_json(sug(env, f"{SNAP}-{ticker}-collar-{slug}.json"))


def ledger(env):
    return spread_ledger.load(env["ledger"] / "spreads.json")


def save_ledger(env, spreads):
    env["ledger"].mkdir(parents=True, exist_ok=True)
    spread_ledger.save(env["ledger"] / "spreads.json",
                       {**spread_ledger.empty_ledger(), "spreads": spreads})


def add_paper(env, ticker, shares, cost):
    env["ledger"].mkdir(parents=True, exist_ok=True)
    path = env["ledger"] / paper_shares.LOTS_FILE
    doc, _ = paper_shares.add_lot(paper_shares.load_lots(path), ticker=ticker, shares=shares,
                                  cost=cost, opened_on="2026-10-01")
    paper_shares.save_lots(path, doc)
    return 0


def tier_of(doc, tier):
    return next(t for t in doc["tiers"] if t["tier"] == tier)


# ---- suggest ---------------------------------------------------------------------

def test_suggest_gateway_down_writes_nothing_and_exits_nonzero(env, capsys):
    env["gateway"] = DOWN
    assert run(env, "suggest") == 1
    assert "gw.test:14999" in capsys.readouterr().err
    assert not env["ledger"].exists() or not list(env["ledger"].iterdir())


def test_suggest_gateway_dropping_mid_run_writes_nothing(env, monkeypatch, capsys):
    def boom(t, e, strikes=(), **kw):
        raise DOWN
    monkeypatch.setattr(spread_data, "fetch_puts", boom)
    assert run(env, "suggest") == 1
    assert not (env["ledger"] / "collar-screen").exists()
    assert not (env["ledger"] / "suggestions").exists()


def test_suggest_writes_summary_and_per_ticker_json_md(env):
    assert run(env, "suggest") == 0
    for name in (f"{SNAP}-ZZZQ-collar-real.md", f"{SNAP}-ZZZQ-collar-real.json"):
        assert sug(env, name).exists(), name
    for name in (f"collar-screen/{SNAP}.md", f"collar-screen/{SNAP}.json"):
        assert (env["ledger"] / name).exists(), name
    doc = cand(env)
    assert doc["strategy"] == "collar" and doc["data_source"] == "IBKR" and doc["frozen"] is True
    assert doc["snapshot_date"] == str(SNAP) and doc["expiry"] == EXPIRY
    md = sug(env, f"{SNAP}-ZZZQ-collar-real.md").read_text(encoding="utf-8")
    assert "### 90% 档" in md and "冻结行情" in md
    assert not (env["ledger"] / "spreads.json").exists()  # suggest never writes the ledger


def test_suggest_three_tiers_with_put_only_variants(env):
    assert run(env, "suggest") == 0
    doc = cand(env)
    assert [t["tier"] for t in doc["tiers"]] == [90, 85, 80]
    for t, strike in zip(doc["tiers"], (90.0, 85.0, 80.0)):
        c, p = t["collar"], t["put_only"]
        assert c["put_strike"] == strike and p["put_strike"] == strike
        assert c["call_strike"] > 100.0 and p["call_strike"] is None and p["call_credit"] == 0.0
        assert c["net_credit"] == pytest.approx(c["call_credit"] - c["put_debit"])
        assert p["net_credit"] == pytest.approx(-p["put_debit"])
        assert c["floor"] == strike and c["cap"] == c["call_strike"]
    assert tier_of(doc, 90)["collar"]["put_pct_below_spot"] == pytest.approx(10.0)
    assert doc["contracts_available"] == 3 and doc["contracts_available_for_put"] == 3


def test_suggest_uses_wider_window_for_puts(env):
    assert run(env, "suggest") == 0
    call = env["put_calls"][0]
    assert call["window_pct"] == 0.25 and call["window_limit"] == 60
    assert {90.0, 85.0, 80.0} <= set(call["strikes"]) and call["expiry"] == EXPIRY


def test_suggest_requests_each_tiers_band_on_a_wide_grid(env):
    env["listed"] = [60.0 + 2.5 * i for i in range(0, 33)]  # 2.5-wide strikes
    env["spot"] = 99.0
    assert run(env, "suggest") == 0
    assert env["put_calls"][0]["strikes"] == (87.5, 85.0, 82.5, 80.0, 77.5, 75.0)  # bands 84.15-89.1 / 79.2-84.15 / 74.25-79.2


def test_suggest_dte_long_picks_90_to_180_day_expiry(env):
    assert run(env, "suggest", "--dte", "long") == 0
    assert cand(env)["expiry"] == LONG_EXPIRY and cand(env)["dte_mode"] == "long"
    assert env["put_calls"][0]["expiry"] == LONG_EXPIRY


def test_suggest_no_expiry_in_window_rejects_lot(env):
    env["expirations"] = ["2026-10-23"]
    assert run(env, "suggest") == 0
    summary = read_json(env["ledger"] / "collar-screen" / f"{SNAP}.json")
    assert summary["candidates"] == [] and "到期" in summary["rejects"][0]["reason"]


def test_suggest_tax_warning_for_young_taxable_lot_only(env):
    env["real"] = [real_lot("ZZZQ", taxable=True, entry="2026-08-01"),
                   real_lot("YYYQ", taxable=False, entry="2026-08-01"),
                   real_lot("XXXQ", taxable=True, entry="2024-01-01")]
    assert run(env, "suggest") == 0

    def warned(ticker):
        doc = cand(env, ticker=ticker)
        ws = [w for t in doc["tiers"] for k in ("collar", "put_only") for w in t[k]["warnings"]]
        return [w for w in ws if "应税" in w]

    assert warned("ZZZQ") and not warned("YYYQ") and not warned("XXXQ")
    text = json.dumps(cand(env), ensure_ascii=False)
    assert "Roth" not in text and "IRA" not in text


def test_suggest_paper_lot_has_no_tax_warning(env):
    env["real"] = []
    assert add_paper(env, "ZZZQ", 200.0, 80.0) == 0
    assert run(env, "suggest") == 0
    doc = cand(env, slug="lot1")
    assert doc["source"] == "manual"
    assert not [w for t in doc["tiers"] for k in ("collar", "put_only") for w in t[k]["warnings"]
                if "应税" in w]


def test_suggest_rejected_tier_lists_reason(env):
    env["wide"] = 80.0   # strikes at or below 80 have unusable spreads
    assert run(env, "suggest") == 0
    doc = cand(env)
    t80 = tier_of(doc, 80)
    assert t80["collar"] is None and "价差" in t80["collar_reject"]
    assert t80["put_only"] is None and "价差" in t80["put_only_reject"]
    assert tier_of(doc, 90)["collar"] is not None
    md = (env["ledger"] / "collar-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "剔除：Put 买卖价差过宽" in md


def test_suggest_covered_lot_gets_put_only_and_note(env):
    save_ledger(env, [{
        "id": 1, "ticker": "ZZZQ", "strategy": "covered_call", "status": "open", "contracts": 3,
        "expiry": "2026-10-30", "short_strike": 105.0, "credit": 1.0,
        "lot_ref": {"source": "real", "lot_id": None, "ticker": "ZZZQ"}}])
    assert run(env, "suggest") == 0
    doc = cand(env)
    assert doc["contracts_available"] == 0 and doc["contracts_available_for_put"] == 3
    assert "只出保护性 Put" in doc["note"]
    assert all(t["collar"] is None and t["put_only"] is not None for t in doc["tiers"])


def test_suggest_covered_lot_with_rejected_put_tier_does_not_crash(env):
    save_ledger(env, [{
        "id": 1, "ticker": "ZZZQ", "strategy": "covered_call", "status": "open", "contracts": 3,
        "expiry": "2026-10-30", "short_strike": 105.0, "credit": 1.0,
        "lot_ref": {"source": "real", "lot_id": None, "ticker": "ZZZQ"}}])
    env["wide"] = 80.0   # the 80% put tier is unusable
    assert run(env, "suggest") == 0
    doc = cand(env)
    assert "只出保护性 Put" in doc["note"]
    assert len(doc["tiers"]) == 3
    assert all(t["collar"] is None and "只出保护性 Put" in t["collar_reject"] for t in doc["tiers"])
    assert tier_of(doc, 80)["put_only"] is None and tier_of(doc, 90)["put_only"] is not None
    assert (env["ledger"] / "collar-screen" / f"{SNAP}.json").exists()


def test_tier_entries_tolerates_short_tier_list():
    res = {"tiers": [], "put_only": [rules_reject()] * 3, "note": None}
    entries = collar_screen._tier_entries(res, 100.0)
    assert len(entries) == 3 and all(e["collar_reject"] for e in entries)


def rules_reject():
    import put_screen_rules
    return put_screen_rules.Reject("x", "r")


def test_suggest_short_lot_is_rejected_whole(env):
    env["real"] = [real_lot(shares=50.0)]
    assert run(env, "suggest") == 0
    summary = read_json(env["ledger"] / "collar-screen" / f"{TODAY}.json")  # no market data -> today
    assert {"ticker": "ZZZQ（真实）", "reason": "股数不足 100 股"} in summary["rejects"]
    assert not list((env["ledger"] / "suggestions").glob("*collar*"))
    assert env["put_calls"] == []


def test_suggest_rerun_replaces_same_day_collar_files_only(env):
    assert run(env, "suggest") == 0
    folder = env["ledger"] / "suggestions"
    keep = [f"{SNAP}-ZZZQ-call-real.json", f"{SNAP}-ZZZQ-put.md"]
    for name in keep:
        (folder / name).write_text("x", encoding="utf-8")
    stale = folder / f"{SNAP}-OLDQ-collar-lot9.json"
    stale.write_text("{}", encoding="utf-8")
    assert run(env, "suggest") == 0
    assert not stale.exists()
    assert all((folder / n).read_text(encoding="utf-8") == "x" for n in keep)


def test_suggest_continues_when_one_ticker_fails(env, monkeypatch):
    env["real"] = [real_lot("ZZZQ"), real_lot("YYYQ")]

    def last_close(t):
        if t == "YYYQ":
            raise spread_data.DataError("网络挂了")
        return SNAP, 100.0
    monkeypatch.setattr(spread_data, "last_close", last_close)
    assert run(env, "suggest") == 0
    md = (env["ledger"] / "collar-screen" / f"{SNAP}.md").read_text(encoding="utf-8")
    assert "YYYQ（真实） — 取行情失败" in md.split("## 被剔除")[1]
    assert sug(env, f"{SNAP}-ZZZQ-collar-real.json").exists()


# ---- open ------------------------------------------------------------------------

def test_open_records_saved_candidate_without_refetch(env, monkeypatch):
    assert run(env, "suggest") == 0
    doc = cand(env)

    def forbidden(*a, **k):
        raise AssertionError("open must not re-fetch")
    for name in ("fetch_puts", "fetch_calls", "last_close", "expirations", "chain_strikes"):
        monkeypatch.setattr(spread_data, name, forbidden)
    assert run(env, "open", "ZZZQ", "90") == 0
    rec = ledger(env)["spreads"][0]
    c = tier_of(doc, 90)["collar"]
    assert rec["strategy"] == "collar" and rec["contracts"] == 1
    assert rec["put_strike"] == c["put_strike"] and rec["put_debit"] == c["put_debit"]
    assert rec["call_strike"] == c["call_strike"] and rec["call_credit"] == c["call_credit"]
    assert rec["expiry"] == EXPIRY and rec["snapshot_date"] == str(SNAP)
    assert rec["suggestion_file"] == f"{SNAP}-ZZZQ-collar-real.md"
    assert (env["ledger"] / "report.md").exists()


def test_open_writes_explicit_fields_and_no_legacy_credit_keys(env):
    env["real"] = [real_lot(shares=300.0, cost=88.0)]
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "85", "--contracts", "2") == 0
    rec = ledger(env)["spreads"][0]
    for gone in ("credit", "short_strike", "long_strike", "delta_bucket", "width"):
        assert gone not in rec, gone
    assert rec["net_credit"] == pytest.approx(rec["call_credit"] - rec["put_debit"], abs=1e-6)
    assert rec["covered_shares"] == 200 == 100 * rec["contracts"]
    assert rec["cost_basis"] == 88.0 and rec["reference_price"] == 88.0
    assert rec["lot_ref"] == {"source": "real", "lot_id": None, "ticker": "ZZZQ"}
    assert rec["status"] == "open" and rec["put_strike"] == 85.0


def test_open_put_only_records_protective_put(env):
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90", "--put-only") == 0
    rec = ledger(env)["spreads"][0]
    assert rec["strategy"] == "protective_put"
    assert rec["call_strike"] is None and rec["call_credit"] == 0
    assert rec["net_credit"] == pytest.approx(-rec["put_debit"], abs=1e-6) and rec["put_strike"] == 90.0
    assert "credit" not in rec and "short_strike" not in rec


def test_open_duplicate_rejected(env, capsys):
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 0
    assert run(env, "open", "ZZZQ", "85") == 1
    assert "已经有记录" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_open_over_coverage_rejected_for_collar_but_not_for_put_only(env, capsys):
    env["real"] = [real_lot(shares=200.0)]
    save_ledger(env, [{
        "id": 1, "ticker": "ZZZQ", "strategy": "covered_call", "status": "open", "contracts": 1,
        "expiry": "2026-10-30", "short_strike": 105.0, "credit": 1.0,
        "lot_ref": {"source": "real", "lot_id": None, "ticker": "ZZZQ"}}])
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90", "--contracts", "2") == 1
    assert "最多可再卖 1 张" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1
    assert run(env, "open", "ZZZQ", "90", "--put-only", "--contracts", "2") == 0
    rec = ledger(env)["spreads"][1]
    assert rec["strategy"] == "protective_put" and rec["covered_shares"] == 200
    assert run(env, "open", "ZZZQ", "90", "--put-only", "--contracts", "3") == 1  # > shares // 100


def test_open_without_saved_candidate_rejected(env, capsys):
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "suggest" in capsys.readouterr().err
    assert not (env["ledger"] / "spreads.json").exists()


def test_open_refuses_expired_candidate(env, capsys):
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90", today=date(2026, 11, 20)) == 1
    assert "已过期" in capsys.readouterr().err


def test_open_refuses_damaged_candidate(env, capsys):
    assert run(env, "suggest") == 0
    path = sug(env, f"{SNAP}-ZZZQ-collar-real.json")
    doc = read_json(path)
    doc["tiers"][0]["collar"]["put_debit"] = "abc"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "suggest" in capsys.readouterr().err


def test_open_refuses_inconsistent_net_credit(env, capsys):
    assert run(env, "suggest") == 0
    path = sug(env, f"{SNAP}-ZZZQ-collar-real.json")
    doc = read_json(path)
    doc["tiers"][0]["collar"]["net_credit"] += 1.0
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "suggest" in capsys.readouterr().err


def test_open_refuses_rejected_tier_with_reason(env, capsys):
    env["wide"] = 80.0
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "80") == 1
    assert "价差" in capsys.readouterr().err
    assert run(env, "open", "ZZZQ", "80", "--put-only") == 1


def test_open_refuses_collar_on_covered_lot_with_note(env, capsys):
    save_ledger(env, [{
        "id": 1, "ticker": "ZZZQ", "strategy": "covered_call", "status": "open", "contracts": 3,
        "expiry": "2026-10-30", "short_strike": 105.0, "credit": 1.0,
        "lot_ref": {"source": "real", "lot_id": None, "ticker": "ZZZQ"}}])
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "只出保护性 Put" in capsys.readouterr().err
    assert run(env, "open", "ZZZQ", "90", "--put-only") == 0


def test_open_refuses_when_newest_screen_rejected_the_lot(env, capsys):
    assert run(env, "suggest") == 0
    env["real"] = [real_lot(shares=50.0)]
    assert run(env, "suggest") == 0   # same snapshot: the rerun deletes the old files and rejects the short lot
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "suggest" in capsys.readouterr().err


def test_open_requires_lot_when_ticker_has_several(env, capsys):
    assert add_paper(env, "ZZZQ", 100.0, 80.0) == 0
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 1
    err = capsys.readouterr().err
    assert "--lot" in err and "real" in err and "1" in err
    assert run(env, "open", "ZZZQ", "90", "--lot", "real") == 0
    assert run(env, "open", "ZZZQ", "90", "--lot", "1") == 0   # other lot, same expiry: allowed
    assert len(ledger(env)["spreads"]) == 2


def test_open_refuses_when_lot_no_longer_held(env, capsys):
    assert run(env, "suggest") == 0
    env["real"] = []
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "已不存在" in capsys.readouterr().err


def test_open_rejects_unknown_tier(env):
    with pytest.raises(SystemExit):
        run(env, "open", "ZZZQ", "95")


# ---- shared commands and guards --------------------------------------------------

def test_status_and_close_delegate_to_spread_sim(env, monkeypatch):
    seen = []
    monkeypatch.setattr(spread_sim, "cmd_status", lambda a, d, t: seen.append(("status", d, t)) or 0)
    monkeypatch.setattr(spread_sim, "cmd_close", lambda a, d, t: seen.append(("close", a.id)) or 0)
    assert run(env, "status") == 0 and run(env, "close", "4") == 0
    assert seen == [("status", env["ledger"], TODAY), ("close", 4)]


def test_collar_screen_never_writes_positions_md(env):
    positions = REPO_ROOT / "data" / "positions.md"
    before = hashlib.sha256(positions.read_bytes()).hexdigest() if positions.exists() else None
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 0
    after = hashlib.sha256(positions.read_bytes()).hexdigest() if positions.exists() else None
    assert before == after
    assert not list(env["ledger"].rglob("positions.md"))
    src = (REPO_ROOT / "scripts" / "collar_screen.py").read_text(encoding="utf-8")
    assert "positions.md" not in src  # the module never even names the holdings file


def test_opened_record_settles_through_spread_sim(env, monkeypatch):
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 0
    monkeypatch.setattr(spread_data, "close_on", lambda t, d: 85.0)
    monkeypatch.setattr(spread_data, "monday_gap_pct", lambda *a, **k: None)
    assert run(env, "status", today=AFTER) == 0
    rec = ledger(env)["spreads"][0]
    assert rec["status"] == "expired" and rec["result"] == "protected"


# ---- sparse expiry grids (chain strikes are the union over expiries) -------------

def test_suggest_requests_whole_band_per_tier(env):
    assert run(env, "suggest") == 0
    asked = env["put_calls"][0]["strikes"]
    assert {90.0, 87.0, 85.0, 83.0, 80.0, 76.0, 75.0} <= set(asked)   # [85,90] [80,85] [75,80]
    assert 70.0 not in asked and 91.0 not in asked


def test_suggest_missing_tier_strike_uses_highest_quoted_in_band(env):
    env["missing"] = lambda k: k in (85.0, 84.0)
    assert run(env, "suggest") == 0
    doc = cand(env)
    assert tier_of(doc, 85)["collar"]["put_strike"] == 83.0     # not the 80% tier's strike
    assert tier_of(doc, 80)["collar"]["put_strike"] == 80.0
    assert tier_of(doc, 85)["put_only"]["put_pct_below_spot"] == pytest.approx(17.0)


def test_suggest_tier_with_no_quoted_strike_in_band_is_rejected_accurately(env):
    env["missing"] = lambda k: 80.0 <= k <= 85.0
    assert run(env, "suggest") == 0
    t85 = tier_of(cand(env), 85)
    assert t85["collar"] is None and t85["put_only"] is None
    assert "间距过粗" in t85["collar_reject"] and "79" in t85["put_only_reject"]


def test_suggest_sparse_grid_on_high_priced_name(env):
    env["spot"] = 500.0
    env["listed"] = [float(k) for k in range(300, 700, 5)]
    env["missing"] = lambda k: k % 10 != 0       # this expiry lists only multiples of 10
    assert run(env, "suggest", "--dte", "long") == 0
    doc = cand(env)
    assert [tier_of(doc, t)["collar"]["put_strike"] for t in (90, 85, 80)] == [450.0, 420.0, 400.0]


def test_suggest_data_error_reason_carries_the_text(env, monkeypatch):
    def boom(t, e, strikes=(), **kw):
        raise spread_data.DataError("链不完整 xyz")
    monkeypatch.setattr(spread_data, "fetch_puts", boom)
    assert run(env, "suggest") == 0
    reasons = read_json(env["ledger"] / "collar-screen" / f"{SNAP}.json")["rejects"]
    assert "取不到期权链" in reasons[0]["reason"] and "链不完整 xyz" in reasons[0]["reason"]


# ---- long puts against one lot never exceed shares // 100 ------------------------

def test_put_only_after_collar_on_100_share_lot_refused(env, capsys):
    env["real"] = [real_lot(shares=100.0)]
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 0
    assert run(env, "open", "ZZZQ", "85", "--put-only") == 1
    assert "最多可再买 0 张" in capsys.readouterr().err
    assert len(ledger(env)["spreads"]) == 1


def test_put_only_after_collar_on_200_share_lot_allows_one_more(env, capsys):
    env["real"] = [real_lot(shares=200.0)]
    assert run(env, "suggest") == 0
    assert run(env, "open", "ZZZQ", "90") == 0
    assert run(env, "open", "ZZZQ", "85", "--put-only", "--contracts", "2") == 1
    assert "最多可再买 1 张" in capsys.readouterr().err
    assert run(env, "open", "ZZZQ", "85", "--put-only") == 0


@pytest.mark.parametrize("field,value", [("strategy", "covered_call"), ("version", 2)])
def test_open_refuses_wrong_strategy_or_version(env, capsys, field, value):
    assert run(env, "suggest") == 0
    path = sug(env, f"{SNAP}-ZZZQ-collar-real.json")
    doc = read_json(path)
    doc[field] = value
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(env, "open", "ZZZQ", "90") == 1
    assert "suggest" in capsys.readouterr().err
