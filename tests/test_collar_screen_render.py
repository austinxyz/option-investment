import json

from spread_fixtures import REPO_ROOT  # noqa: F401  (puts scripts/ on sys.path)
import collar_screen_render as render
from put_screen_rules import Reject


def _variant(*, call=True, **over):
    v = {
        "put_strike": 90.0, "put_debit": 1.50, "put_spread_pct": 0.02, "put_stale": False,
        "put_delta": -0.2, "put_pct_below_spot": 10.0,
        "call_strike": 110.0 if call else None, "call_credit": 1.10 if call else 0.0,
        "call_spread_pct": 0.03 if call else None, "call_delta": 0.2 if call else None,
        "net_credit": -0.40 if call else -1.50, "floor": 90.0, "cap": 110.0 if call else None,
        "cap_cost": 1000.0 if call else None, "contracts_max": 3, "warnings": [],
    }
    v.update(over)
    return v


def _tier(tier=90, **over):
    t = {"tier": tier, "collar": _variant(), "collar_reject": None,
         "put_only": _variant(call=False), "put_only_reject": None}
    t.update(over)
    return t


def _cand(**over):
    c = {
        "ticker": "ZZZQ", "source": "manual", "lot_id": 7, "label": "ZZZQ（假设 #7）",
        "shares": 300.0, "contracts_available": 3, "contracts_available_for_put": 3,
        "cost_basis": 90.0, "original_cost": 92.0, "reference_price": 95.0,
        "cost_estimated": False, "accounts": 1, "snapshot_date": "2026-10-21", "spot": 100.0,
        "expiry": "2026-11-27", "days": 37, "dte_mode": "monthly", "earnings": "no",
        "ex_dividend": None, "note": None, "tiers": [_tier(90), _tier(85), _tier(80)],
    }
    c.update(over)
    return c


def test_candidate_json_roundtrips_and_is_labelled():
    out = render.candidate_to_json(_cand(), "2026-10-21", False)
    back = json.loads(json.dumps(out))
    assert back["strategy"] == "collar" and back["data_source"] == "IBKR"
    assert back["frozen"] is True and back["intraday"] is False
    assert back["tiers"][0]["collar"]["put_strike"] == 90.0
    s = render.summary_to_json([_cand()], [Reject("XYZ", "股数不足 100 股")], "2026-10-21",
                               "2026-10-21", True, {"dte_mode": "monthly"})
    assert s["rejects"] == [{"ticker": "XYZ", "reason": "股数不足 100 股"}]
    assert s["frozen"] is False and s["candidates"][0]["tiers"][1]["tier"] == 85


def test_render_summary_shows_net_cost_floor_cap_and_cap_cost():
    md = render.render_summary_md([_cand()], [], "2026-10-21", "2026-10-21", False)
    assert "-$0.40" in md and "-$40.00" in md      # net per share and per contract, signed
    assert "$90 ～ $110" in md and "$90 ～ 无上限" in md
    assert "$1,000" in md                          # cap cost per contract
    assert "90（-10.0%）" in md and "$1.50" in md and "$1.10" in md
    assert "数据源 IBKR" in md and "冻结行情" in md


def test_render_summary_rejected_tier_lists_reason_and_whole_lot_rejects():
    cand = _cand(tiers=[_tier(90, collar=None, collar_reject="没有可用的虚值 Call"),
                        _tier(85), _tier(80, put_only=None, put_only_reject="行权价间距过粗")])
    md = render.render_summary_md([cand], [Reject("QQQZ（真实）", "已被未平仓 call 覆盖")],
                                  "2026-10-21", "2026-10-21", False)
    assert "剔除：没有可用的虚值 Call" in md and "剔除：行权价间距过粗" in md
    assert "QQQZ（真实） — 已被未平仓 call 覆盖" in md.split("## 被剔除")[1]


def test_render_states_zero_cost_is_cash_flow_not_opportunity_cost():
    for md in (render.render_summary_md([_cand()], [], "2026-10-21", "2026-10-21", False),
               render.render_lot_md(_cand(), False)):
        assert "现金流" in md and "不是机会成本为零" in md and "被让出" in md


def test_lot_md_per_tier_details_and_open_commands():
    md = render.render_lot_md(_cand(), False)
    assert "### 90% 档" in md and "### 80% 档" in md
    assert "行权价 $90（现价以下 10.0%），权利金 $1.50/股" in md
    assert "行权价 $110，权利金 $1.10/股" in md
    assert "每股 -$0.40，每张 -$40.00" in md and "锁定区间：$90 ～ $110" in md
    assert "上限代价：每张 $1,000" in md and "卖出 Call：无" in md
    assert f"{render.OPEN_CMD} ZZZQ 90 --lot 7" in md
    assert f"{render.OPEN_CMD} ZZZQ 85 --lot 7 --put-only" in md


def test_lot_md_real_lot_flag_and_warnings_and_note():
    t = _tier(90, collar=_variant(warnings=["含应税账户且持股不满 1 年"]))
    md = render.render_lot_md(_cand(source="real", lot_id=None, note="已被未平仓 call 覆盖：只出保护性 Put",
                                    tiers=[t]), True)
    assert "--lot real" in md and "含应税账户" in md and "只出保护性 Put" in md
    assert "盘中快照" in md


def test_summary_pipe_in_free_text_does_not_split_the_row():
    cand = _cand(label="ZZZQ|X", tiers=[_tier(90, collar=_variant(warnings=["a|b"]))])
    md = render.render_summary_md([cand], [], "2026-10-21", "2026-10-21", False)
    assert "ZZZQ｜X" in md and "a｜b" in md
