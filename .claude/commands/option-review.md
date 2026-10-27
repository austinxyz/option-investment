# option-review

每周检查模拟盘里所有未平仓的期权持仓（Bull Put Credit Spread、现金担保 Sell Put、备兑 Covered Call、保护性 Put / Collar 和铁鹰 Iron Condor；铁鹰和 Call 借方价差的规则见下方“规则”一节各自的段落），按固定规则给每笔一个建议：继续持有 / 需要关注 / 平仓 / 等待结算，并附理由。把当次结论连同所用阈值存成快照，方便日后拿建议和持仓最终盈亏对照。

**只给建议，不平仓、不改账本、不碰 `data/positions.md` / `outputs/` / `wiki/`。** 脚本只读 `data/paper-trading/spreads.json`，结果只写进 `data/paper-trading/reviews/`（gitignored，私有）。真正平仓由用户决定，用 `put_screen.py close ID`（见 `.claude/commands/put-screen.md`）。

## Arguments

无参数。从仓库根目录运行：

```bash
./.venv/Scripts/python.exe scripts/put_review.py
```

## 规则

按顺序，第一个命中的决定结论：

| 顺序 | 条件 | 结论 |
|---|---|---|
| 0 | 取不到报价或现价无效（数据错误，`rule == 0` 表示没有行情数据） | 需要关注，理由写"取不到报价：…" |
| 1 | 到期日当天或已过期 | 等待结算（运行 `put_screen.py status` 结算） |
| 2 | 价内（Put / 价差：现价 < 短腿行权价；Covered Call：现价 ≥ 行权价）或已到止损标记 | 需要关注 |
| 3 | 权利金已赚比例 ≥ 80% | 平仓 |
| 4 | 到期前有财报，或当前 Delta ≥ 0.35（仅数据可信时） | 需要关注 |
| 5 | 其他 | 继续持有（剩余天数 ≤ 5 且 Delta ≤ 0.10 时，理由写"等它自然到期归零"） |

**Covered Call** 也在检查范围内（账本里 `strategy` 为 covered_call）：规则编号和阈值相同，但价内方向相反，现价到达或高于行权价即价内，理由写"现价已到行权价之上，可能被行权（股票按行权价卖出）；由你决定接受行权还是回购"，Delta 用 Call Delta；平仓用 `call_screen.py close ID`（见 `.claude/commands/call-screen.md`），同样不要自己执行。

**铁鹰**（`strategy` 为 iron_condor）也在检查范围内，四条腿按各自行权价取报价：规则顺序和阈值同上，**两侧任一侧出事都算**。第 2 条：现价跌破短 Put 或涨过短 Call 即价内（正好等于短腿行权价不算），或已到止损标记；短 Call 价内且除息日在到期前时，理由会加上提前指派风险。第 3 条的已赚比例按净权利金算。第 4 条：任一条短腿 Delta ≥ 0.35 即关注。第 5 条的“等它自然到期归零”要求两条短腿的 Delta 都 ≤ 0.10。任一条腿取不到有效报价时按第 0 条处理。平仓用 `condor_sim.py close ID`（见 `.claude/commands/condor-sim.md`），同样不要自己执行。实现见 `scripts/put_review_rules.py` 的 `review_condor`。

**Call 借方价差**（`strategy` 为 bull_call_spread，买方）也在检查范围内，长腿、短腿按各自行权价取报价，**用的是买方规则，不是上面的卖方规则**，也**不用 Delta 规则**（长腿 Delta 只记在快照里供参考）。第一个命中的算：任一腿取不到有效报价 → 需要关注（第 0 条）；浮亏已达净支出的 50% → 需要关注（第 2 条）；**短腿严格价内（现价高于短腿行权价）且除息日在到期前 → 需要关注（提前指派风险，考虑平仓；规则编号 4，但先于下一条检查，否则这个理由永远显示不出来）**；浮盈达最大收益的 50%，或现价到达或高于短腿行权价 → 平仓（第 3 条）；到期前有财报、剩余 ≤ 10 天且浮盈 ≤ 0、剩余 ≤ 5 天且现价严格落在两腿之间 → 需要关注（第 4 条）；其余继续持有。平仓用 `bullcall_sim.py close ID`（见 `.claude/commands/bullcall-sim.md`），同样不要自己执行。实现见 `scripts/put_review_rules.py` 的 `review_bullcall`。

第 3 条排在第 4 条之前：已赚 80% 且有财报时建议平仓，而不是只"关注"。

四个默认阈值（已赚 80%、Delta 0.35、临近到期 5 天、Delta 0.10）是 `scripts/put_review_rules.py` 顶部的常量（`CLOSE_CAPTURED`、`ATTENTION_DELTA`、`NEAR_EXPIRY_DAYS`、`QUIET_DELTA`），要调整就改那里。每次快照都会记下当时用的值。

## 数据陈旧时

休市或报价陈旧、或短腿隐含波动率不可用时，**不使用 Delta 规则**，只用价内/价外、权利金已赚比例、财报和止损标记，并在该行写明"数据陈旧，Delta 未使用"。不要自己用陈旧数据推断 Delta 结论。财报日期查不到时按 unknown 处理，继续持有/平仓的理由末尾会写"财报日期未知"。

## 快照

每次运行写 `data/paper-trading/reviews/<日期>.md` 和 `.json`：同一天重跑覆盖同一天的文件（但数据质量更差的重跑不会覆盖已有的好快照：按 ok > stale > none 比较，较差则保留原快照并提示），不同日期各存一份。JSON 每笔记录以账本里的持仓 id 为键（跨周稳定），含所有数字、建议、命中规则、理由、数据质量（`data_quality`：`ok` 数据可信；`stale` 报价陈旧或隐含波动率不可用，未用 Delta；`none` 没有行情数据，即规则 0 或 1 的行），以及本次的阈值，便于日后把历次建议和该持仓最终的盈亏联起来。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. 脚本输出是中文，如实转述，不要重新计算数字。

### 1. 运行

运行上面的命令。它会逐笔取行情，可能要一会儿，等它结束。账本里没有未平仓持仓时只会打印一行说明，如实告诉用户并结束。

期权行情（报价、IV、Delta）来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`），**不要求在美股盘中运行**：IBKR 模式是只读延迟数据，休市后是延迟冻结数据，快照按交易日标注；Yahoo 模式休市后 bid/ask 常为空，没有买卖报价的腿按“数据陈旧”处理，建议开盘时间运行。IBKR 模式的网关配置在 `data/ibkr.env`（不入库）：`IBKR_HOST`、`IBKR_PORT`、`IBKR_OPTIONS_CLIENT_ID`、`IBKR_MARKET_DATA_TYPE`，不要打印或提交其值。**数据源连不上（或中途掉线）时整次运行中止，退出码非 0，不写任何文件，也不会换别的数据源重试**——如实转述报错。IBKR 模式下若报错提到 10197，说明同一 IBKR 账户的实盘会话（TWS 或手机）占用了行情，请用户先退出该会话（或关闭行情共享）再重跑。

### 2. 转述结果

1. 展示脚本打印的表（id、策略、标的、行权价、到期、剩余天数、现价、已赚比例、Delta、财报、结论、理由）和末尾汇总（平仓 N / 继续持有 N / 需要关注 N）。
2. 先列**需要关注**和**平仓**的持仓及理由，再简述其余。等待结算的提醒用户运行 `put_screen.py status`。
3. 转述数据陈旧提示：说明这次是收盘后/休市快照还是盘中快照，陈旧行没有用 Delta。
4. 告诉用户快照路径 `data/paper-trading/reviews/<日期>.md`。
5. 提醒：这些只是建议，是否平仓由用户决定，要平仓用 `./.venv/Scripts/python.exe scripts/put_screen.py close ID`（按当前中间价回购记账）。不要自己执行 `close`。

## Rules

- 不修改 `spreads.json`、不平仓、不写 `data/positions.md` / `outputs/` / `wiki/`。
- 金额按每张合约 ×100、中间价成交、不含佣金。
- 模拟/研究用途，非投资建议。
