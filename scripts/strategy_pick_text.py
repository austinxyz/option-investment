#!/usr/bin/env python3
"""Fixed wording for /strategy-pick: labels, next commands, why-not sentences, warnings (no I/O).

See docs/superpowers/specs/2026-10-25-strategy-pick-design.md (6.8, 6.10-6.12). The why-not
sentences paraphrase the "适用 / 不适用" lists of wiki/options/strategies/*.md; they are written
here instead of parsed at run time because Markdown structure drifts. A guard test checks every
page named in STRATEGY_PAGES exists.
"""
from __future__ import annotations

STRATEGY_LABELS = {
    "cash_secured_put": "现金担保 Put",
    "bull_put_spread": "Bull Put 价差",
    "bull_call_spread": "Bull Call 价差",
    "iron_condor": "铁鹰",
    "covered_call": "备兑 Call",
    "collar": "Collar",
    "protective_put": "保护性 Put",
    "hold": "继续持有",
    "no_options": "不做期权",
    "wait_earnings": "等财报后再看",
    "reduce_position": "考虑减仓",
    "long_call": "买入 Call",
    "naked_call": "裸卖 Call",
    "bearish": "看跌策略",
    "wheel": "Wheel",
}

CLASS_ONLY_FLAG = "rank_by_class_only"
FLAG_LABELS = {"far_otm": "远价外", "thin_premium": "权利金薄", "required_win_rate": "所需胜率高",
               "want_shares_only": "只在真想接股时做", "premium_average": "权利金一般",
               "earnings_before_expiry": "到期日须早于财报日", "ex_dividend": "除息日在窗口内",
               "ltcg_reset": "持有期会被重置", "reduce_hint": "考虑减仓",
               CLASS_ONLY_FLAG: "组合总值取不到，只按类别排序"}
LEG_LABELS = {"short_put": "短 Put", "short_call": "短 Call", "long_call": "长 Call",
              "long_put": "长 Put"}
# Shown as a warning line instead: it is about the shares held, not about this pick.
HIDDEN_FLAGS = frozenset({"reduce_hint"})

# Page under wiki/options/strategies/ each not-chosen line links to. Non-option keys point at the
# page whose lists carry the reason (holding -> covered call, event window -> cash-secured put).
STRATEGY_PAGES = {
    "cash_secured_put": "cash-secured-put.md",
    "bull_put_spread": "bull-put-credit-spread.md",
    "bull_call_spread": "bull-call-debit-spread.md",
    "iron_condor": "iron-condor.md",
    "covered_call": "covered-call.md",
    "collar": "protective-put-collar.md",
    "protective_put": "protective-put-collar.md",
    "hold": "covered-call.md",
    "no_options": "cash-secured-put.md",
    "wait_earnings": "cash-secured-put.md",
    "reduce_position": "protective-put-collar.md",
    "long_call": "long-call-long-put.md",
    "naked_call": "naked-call.md",
    "bearish": "long-call-long-put.md",
    "wheel": "wheel.md",
}

# "{ticker}" is filled in by the tree.
NEXT_COMMANDS = {
    "cash_secured_put": "/put-screen suggest --dte monthly",
    "bull_put_spread": "/spread-sim suggest {ticker}",
    "bull_call_spread": "/bullcall-sim suggest {ticker}",
    "iron_condor": "/condor-sim suggest {ticker}",
    "covered_call": "/call-screen suggest --dte monthly",
    "collar": "/collar-screen suggest",
    "protective_put": "/collar-screen suggest",
    "hold": "/stock-review {ticker}",
    "no_options": "/stock-review {ticker}",
    "reduce_position": "/stock-review {ticker}",
    "wait_earnings": "/strategy-pick {ticker}",
}

NEXT_COMMAND_CAVEATS = {
    "cash_secured_put": ("加 --tickers 只筛这一只（不再读 wiki 的 Avoid / 复核中状态）；"
                         "仍要求收盘价在 MA200 之上、到期前无财报，远价外和下行的情形它可能剔掉这只票"),
    "bull_put_spread": "三档 0.20 / 0.15 / 0.10，远价外选 0.10 或 0.15 档",
    "covered_call": "扫全部持股，不接 ticker；Delta 用 --delta 指定（见上一节），不指定就是 0.20",
    "collar": "扫全部持股，按 90% / 85% / 80% 现价选 Put、Call 取零成本，具体档由它定",
    "protective_put": "扫全部持股，按 90% / 85% / 80% 现价选 Put，具体档由它定",
    "reduce_position": "减仓走 /stock-review、/stock-exit，本工具不下单",
    "wait_earnings": "财报落地后重跑本命令",
}

_ALL_CHOOSABLE = ("cash_secured_put", "bull_put_spread", "bull_call_spread", "iron_condor",
                  "covered_call", "collar", "protective_put", "hold", "no_options",
                  "wait_earnings", "reduce_position")
_SELL_PUTS = ("cash_secured_put", "bull_put_spread")
_SHARE_BASED = ("covered_call", "collar", "protective_put")

# (code, strategies, sentence) -> WHY_NOT[(strategy, code)]
_WHY_NOT_ROWS = (
    ("earnings_imminent", _ALL_CHOOSABLE,
     "热门股财报在 7 天内：第一推荐改为'等财报后再看'，原第二推荐让位（现金担保 Put、"
     "Bull Put、铁鹰三页的'不适用'都列了已知事件窗口）"),
    ("gate_failed", _ALL_CHOOSABLE,
     "第 ① 步流动性门槛没过，不做期权建议：Bull Call、Bull Put、铁鹰三页的'不适用'列了流动性"
     "一般的标的，其余策略页的流动性一节也要求 OI 数千以上、买卖价差 < 期权价格的 5%"),
    ("history_short", _ALL_CHOOSABLE, "历史不足 200 个交易日，算不出 MA200，不分类、不给策略"),
    ("leaf_choice", _ALL_CHOOSABLE,
     "本叶子按决策树（设计 6.8 / 6.9）选了排在前面的策略，这一个不在本叶子的推荐里"),
    ("view_not_bullish", _SELL_PUTS + ("iron_condor", "covered_call"),
     "看法不看好：工具不出任何看涨开仓策略"),
    ("view_not_bullish", ("bull_call_spread",),
     "看法不是看好：中性只做远价外卖方或不做，不为上涨付钱；不看好不出任何看涨开仓"),
    ("view_not_bullish", ("hold",), "看法不看好：不建议原样继续持有，考虑减仓或用 Collar 锁下限"),
    ("held_use_shares", _SELL_PUTS + ("bull_call_spread", "iron_condor"),
     "已有单账户 ≥ 100 股：本叶子用现有持股做备兑 Call / Collar，不再开新的看涨或中性仓位"),
    ("range_not_trend", _SELL_PUTS + ("bull_call_spread",),
     "走势是盘整：有区间、没趋势，决策树改用铁鹰（单边策略要的是方向判断）"),
    ("downtrend", _SELL_PUTS + ("bull_call_spread",),
     "走势下行：价差要的是'不大跌'或上涨；看好时只在真想接股时做远价外现金担保 Put，中性时不做"),
    ("iv_low_seller", _SELL_PUTS + ("iron_condor", "covered_call"),
     "IV 低：权利金薄，同样的 Delta 收不到钱（铁鹰、现金担保 Put 两页的'不适用'列了低 IV；"
     "Bull Put 页的'适用'要中高 IV，备兑 Call 页的'适用'要高 IV）"),
    ("high_vol_buyer", ("bull_call_spread",), "高波动：用户规则高波动不做买方，只做远价外卖方"),
    ("iv_high_buyer", ("bull_call_spread",),
     "IV 高时买方付的是贵的波动率，用户规则 IV 高走卖方；Bull Call 页：判断只是'不会跌'"
     "应做贷方价差或现金担保 Put"),
    ("iv_high_buyer", ("protective_put",),
     "IV 高时保险最贵（Collar 页'不适用'：权利金很贵时）"),
    ("trend_unclear", ("bull_call_spread",), "走势不明：按中性处理，只做远价外卖方或不做"),
    ("trend_unclear", ("iron_condor",), "走势不明：铁鹰要的是预期横盘且有明确区间（铁鹰页'适用'）"),
    ("trend_not_range", ("iron_condor",), "铁鹰页：趋势明确的标的不适用，横盘假设不成立"),
    ("held_no_lot", _SHARE_BASED,
     "持股不足一张合约（没有单个账户 ≥ 100 股，跨账户加总凑不出一张）：备兑 Call / Collar / "
     "保护性 Put 做不了，按未持有分支选"),
    ("held_no_lot", ("hold",), "持股不足一张合约：按未持有分支选策略，持股本身另行判断"),
    ("not_held", _SHARE_BASED, "现股不在手：备兑 Call / Collar / 保护性 Put 都要求单账户先有一整张合约对应的现股"),
    ("not_held", ("hold",), "未持有，谈不上继续持有"),
    ("needs_floor", ("covered_call",),
     "高波动或下行：持股需要下限，决策树用 Collar（备兑 Call 页：只想降低下跌风险时缓冲太薄）"),
    ("upside_capped", ("collar",),
     "看好且走势不需要锁下限：Collar 的上限会把最赚的一段切掉（Collar 页'不适用'：预期大涨）"),
    ("option_fits", ("no_options",), "本叶子有符合规则的期权策略，所以不是'不做'"),
    ("wait_not_triggered", ("wait_earnings",),
     "不是热门股财报 7 天内且推荐含卖方 / 铁鹰 / 备兑的情形，不需要等财报"),
    ("reduce_not_applicable", ("reduce_position",),
     "减仓只在看法不看好且单账户持股 ≥ 100 股时列为推荐（持股不足一张时只作提示）"),
    ("no_tool", ("long_call",),
     "买入 Call 页：横盘或慢涨被 Theta 每天抽水；仓库也没有它的模拟工具"),
    ("never_naked", ("naked_call",), "裸卖 Call 页：最大亏损无限，工具永不推荐"),
    ("never_bearish", ("bearish",), "看跌策略：工具不做（用户偏好看涨与中性）"),
    ("wheel_is_flow", ("wheel",), "Wheel 是现金担保 Put 被指派后的流程，不单列"),
)


def _build_why_not() -> dict[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    for code, keys, sentence in _WHY_NOT_ROWS:
        for key in keys:
            table[(key, code)] = sentence
    return table


WHY_NOT = _build_why_not()

NEEDS_VIEW_PROMPT = "{ticker} 没有可用论点，请告诉我你的看法：看好 / 中性 / 不看好"
# One stderr line when the decision-path diagram fails; the report and data are still written.
DIAGRAM_FAIL_TEXT = "路径图生成失败（{name}），报告照常写"

WARN = {
    "market_cap_missing": "市值未取到，市值子项未检查",
    "stale_quotes": "流动性未验证（报价陈旧），盘中重跑",
    "stale_quotes_yahoo": "Yahoo 报价陈旧：ATM 买卖价差未知，流动性门槛未验证，开仓前盘中再确认",
    "oi_missing": "持仓量未取到/全为 0，未验证",
    "no_expiry": "20–60 天内没有到期日，依赖到期日的检查跳过，IV 缺失",
    "class_conflict": "wiki 档位（{wiki}）与持仓类型列（{stock_type}）不一致，按 wiki",
    "class_unknown": "类别未知，按热门股处理；可用 --class 指定",
    "bull_down": "论点看好但走势向下",
    "bear_held": "考虑减仓（/stock-review、/stock-exit）或用 Collar 锁一个下限（/collar-screen）",
    "bear_not_held": "看法不看好：不做期权",
    "no_lot": "持股不足一张合约（最大单账户 {max_lot:g} 股 < {lot:g}），备兑 Call / Collar 做不了，"
              "按未持有选策略",
    "reduce_hint": "持有不足 {lot:g} 股且看法不是看好：考虑减仓（/stock-review、/stock-exit）",
    "earnings_avoid": "到期日必须早于财报日 {date}，或等财报后再开",
    "earnings_cross_expiry": "所选到期日（{days} 天）跨财报（{earn_days} 天后），需另选更早的到期日",
    "earnings_imminent": "财报在 {earn_days} 天内（≤ {limit} 天）：先等财报后再看",
    "earnings_warn_only": "财报日 {date} 在 {window} 天窗口内（蓝筹只警告，不改推荐）",
    "earnings_unknown": "财报日期未知，开仓前自己查",
    "ex_dividend": "除息日 {date} 在窗口内，备兑 Call 有提前指派风险",
    "thin_iv": "平值 IV {atm_iv:.2f} 低于本类 THIN_IV {thin:.2f}：权利金薄",
    "ref_missing_iv": "IV 缺失，参考价位不给",
    "ltcg_reset": "持股不满 1 年的美国应税账户：持有期会被重置（Collar 页）",
}
