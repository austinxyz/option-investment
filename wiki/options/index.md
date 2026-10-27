# 期权（Options）

这里集中放期权相关的知识、各策略的分析，以及视频/文章的解析。

> 来源页是**总结**，不是转录或转载；数字是来源里说的，没有独立核实，页内标了"核算"或"待核"的地方请先核对再引用。不构成投资建议。

## 目录结构
- [知识综合总结](knowledge.md)：基础概念、策略库、现金担保 Put 现金流体系、下单与风控、风险清单、心态方法、数字核对、来源对照。
- `sources/`：每个来源一页的解析（见下表）。
- `strategies/`：每个策略一页的深入分析，从 [模板](strategies/_TEMPLATE.md) 复制开始。
  - [现金担保 Put（Cash-Secured Put）](strategies/cash-secured-put.md)
  - [垂直 Put 贷方价差（Bull Put Credit Spread）](strategies/bull-put-credit-spread.md)
  - [备兑 Call（Covered Call）](strategies/covered-call.md)
  - [Wheel（轮动）](strategies/wheel.md)
  - [垂直 Call 借方价差（Bull Call Debit Spread）](strategies/bull-call-debit-spread.md)
  - [Protective Put / Collar](strategies/protective-put-collar.md)
  - [Long Call / Long Put](strategies/long-call-long-put.md)
  - [Naked Call](strategies/naked-call.md)
  - [铁鹰（Iron Condor）](strategies/iron-condor.md)

## 来源解析
| 页面 | 类型 | 内容 |
|------|------|------|
| [projectoption 新手完整指南](sources/projectoption-beginners-guide.md) | 视频（教程） | 定价、希腊字母、各策略盈亏数学、保证金与风险 |
| [Invest with Henry：Sell Put 做月入](sources/henry-sell-put-income.md) | 视频（策略） | 选股、Delta 0.20、月度到期、Wheel、收益倒推 |
| [天哥复利之道：4% 法则与 Sell Put](sources/tiange-sell-put-retirement.md) | 视频（策略+规划） | 三档标的、双重收益、Wheel、算术核对 |
| [老周横眉：期权的秘密](sources/laozhou-option-secrets.md) | 视频（历史） | 从泰勒斯到 Black-Scholes 到西蒙斯 |
| [期权小师妹：稳定盈利的 3 件事](sources/xiaoshimei-three-habits.md) | 文章摘要（心态） | 模拟盘、复盘、一个策略做透 |

## 模拟盘
- [模拟盘报告](paper-trading.md)：垂直 Put 贷方价差（`bull_put_spread`）与现金担保 Put（`cash_secured_put`）的纸上交易记录和累积统计。报告自动生成，这是一份快照，不是实时数据；纸上交易，不含真实持仓，不构成投资建议。

## 策略索引
状态说明：**已分析** = 有单独页面；**概览** = 只在知识综合总结里有一节；**待深入** = 还没有单独页面。九个策略现在都有单独页面。

| 策略 | 概览位置 | 模拟盘记账代码 | 状态 |
|------|----------|----------------|------|
| 现金担保 Put（Cash-Secured Put） | [知识综合总结](knowledge.md) 第二、三部分 | `cash_secured_put` | **[已分析](strategies/cash-secured-put.md)** |
| Wheel（现金担保 Put → 备兑 Call） | 第二、三部分 | —（是流程，不是单独记账单位） | **[已分析](strategies/wheel.md)** |
| 备兑 Call（Covered Call） | 第二部分 | —（涉及真实持股，不在模拟盘公开） | **[已分析](strategies/covered-call.md)** |
| 垂直 Call 借方价差（Bull Call Debit Spread） | 第二部分 | — | **[已分析](strategies/bull-call-debit-spread.md)** |
| 垂直 Put 贷方价差（Bull Put Credit Spread） | [知识综合总结](knowledge.md) 第二部分（来源未展开，推导 + 模拟盘案例） | `bull_put_spread` | **[已分析](strategies/bull-put-credit-spread.md)** |
| Protective Put / Collar | 第二部分 | — | **[已分析](strategies/protective-put-collar.md)** |
| Long Call / Long Put | 第二部分 | — | **[已分析](strategies/long-call-long-put.md)** |
| Naked Call | 第二部分（来源强烈不建议新手做） | — | **[已分析](strategies/naked-call.md)**（来源强烈不建议做） |
| 铁鹰（Iron Condor） | 来源只有 E 的一句旁证，结构未讲 | — | **[已分析](strategies/iron-condor.md)**（推导为主） |

**记账代码**是[模拟盘报告](paper-trading.md)"按策略对比"表里用的标识。报告正文已统一用「Put 贷方价差」和「现金担保 Put」，代码不变：`bull_put_spread` / `cash_secured_put`。

## 怎么继续加
1. 新来源：在 `sources/` 加一页（只写自己的总结，不贴全文），在上表加一行，并在 [知识综合总结](knowledge.md) 对应章节补充或标注冲突。
2. 新策略分析：复制 [模板](strategies/_TEMPLATE.md) 到 `strategies/<策略>.md`，填完后在上表把状态改成"已分析"。
3. 数字：优先用 `yfinance` 等可复算的数据，来源里的数字保持"来源说"的口径。
