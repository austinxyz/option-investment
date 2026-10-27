import json

from spread_fixtures import REPO_ROOT  # noqa: F401  (puts scripts/ on sys.path)
import call_screen_render as render
from put_screen_rules import Reject


def _cand(**over):
    c = {
        "ticker": "ABC", "source": "manual", "lot_id": 7, "label": "ABC（假设 #7）",
        "shares": 300.0, "contracts_available": 3, "cost_basis": 90.0,
        "original_cost": 92.0, "reference_price": 95.0, "cost_estimated": False,
        "accounts": 1, "snapshot_date": "2026-10-21", "spot": 100.0,
        "ma50_pct": 5.0, "ma200_pct": None, "month_return_pct": 3.2,
        "expiry": "2026-11-03", "days": 13, "in_window": True, "dte_mode": "weekly",
        "strike": 106.0, "mid": 0.85, "stale": False, "spread_pct": 0.03,
        "delta": 0.21, "iv": 0.35, "earnings": "no", "ex_dividend": None,
        "premium_per_contract": 85.0, "upside_to_strike_pct": 6.0,
        "annualized_pct": 23.9, "warnings": [],
    }
    c.update(over)
    return c


def test_candidate_json_roundtrips_required_numbers():
    out = render.candidate_to_json(_cand(), "2026-10-21", False)
    back = json.loads(json.dumps(out))
    assert back["strategy"] == "covered_call" and back["intraday"] is False
    for k in ("strike", "mid", "premium_per_contract", "lot_id", "source", "expiry",
              "reference_price", "cost_basis", "original_cost", "contracts_available"):
        assert back[k] == _cand()[k]
    s = render.summary_to_json([_cand()], [Reject("XYZ", "到期前有财报")], "2026-10-21",
                               "2026-10-21", True, {"dte_mode": "weekly"})
    assert s["strategy"] == "covered_call" and s["rejects"] == [
        {"ticker": "XYZ", "reason": "到期前有财报"}]
    assert s["candidates"][0]["strike"] == 106.0 and s["intraday"] is True


def test_lot_md_contains_open_command_with_lot_flag():
    md = render.render_lot_md(_cand(), False)
    assert render.OPEN_CMD in md
    assert "--lot 7" in md and "ABC" in md
    assert "被行权会怎样" in md and "$95.00" in md and "$85" in md and "6.0%" in md


def test_lot_md_real_lot_has_no_lot_id_flag_ambiguity():
    md = render.render_lot_md(_cand(source="real", lot_id=None), False)
    assert f"{render.OPEN_CMD} ABC --lot real" in md


def test_lot_md_shows_warnings():
    md = render.render_lot_md(_cand(warnings=["财报日期未知"]), True)
    assert "财报日期未知" in md and "盘中快照" in md


def test_summary_md_lists_rejects_and_warnings():
    md = render.render_summary_md(
        [_cand(warnings=["来自 2 个账户合并"], ma200_pct=-2.0)],
        [Reject("XYZ（真实）", "已被未平仓 call 覆盖")], "2026-10-21", "2026-10-21", False)
    assert "来自 2 个账户合并" in md and "XYZ（真实）" in md and "已被未平仓 call 覆盖" in md
    assert "ABC" in md and "106" in md and "-2.0%" in md and "—" in md


def test_summary_md_message_branch():
    md = render.render_summary_md([], [], "2026-10-21", "2026-10-21", False,
                                  message="没有可覆盖的持仓")
    assert "没有可覆盖的持仓" in md and "被剔除" not in md


def test_lot_md_called_away_wording_uses_ge_and_open_default():
    md = render.render_lot_md(_cand(), False)
    assert "≥ $" in md and "被行权" in md
    assert "< $" in md and "不被行权" in md
    assert "高于 $" not in md and "≤ $" not in md
    assert "open 默认 1 张" in md and "--contracts" in md


def test_data_source_labelling_frozen_and_intraday():
    c = _cand()
    j = render.candidate_to_json(c, "2026-10-21", False)
    assert j["data_source"] == "IBKR" and j["frozen"] is True
    sj = render.summary_to_json([c], [], "2026-10-21", "2026-10-21", True, {})
    assert sj["data_source"] == "IBKR" and sj["frozen"] is False
    frozen = "数据源 IBKR 模拟网关 · 基于 2026-10-21 收盘（冻结行情）"
    assert frozen in render.render_summary_md([c], [], "2026-10-21", "2026-10-21", False)
    assert frozen in render.render_lot_md(c, False)
    live = "数据源 IBKR 模拟网关 · 基于 2026-10-21 收盘（盘中延迟行情）"
    assert live in render.render_summary_md([c], [], "2026-10-21", "2026-10-21", True)
    assert live in render.render_lot_md(c, True)
