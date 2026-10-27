# paper-book

看模拟盘的"账本"：每笔持仓现在赚了多少、哪些到期了要结算、要不要提前了结，以及整份报告。**覆盖所有策略**（价差、现金担保 Put、备兑 Call、保护性 Put、Collar、铁鹰、Call 借方价差），不用先想"这笔是哪个命令开的"。

这是一个**入口文档**，不新增脚本：三个子动作都落到现有的账本逻辑上（`scripts/spread_sim.py` 的 `status` / `close`，其余命令的同名子命令走的是同一份代码）。**只模拟，不下单，不碰 `data/positions.md`。**

## Arguments

| 动作 | 例子 | 做什么 |
|---|---|---|
| `status` | "看一下模拟盘""周中检查" | 给每笔未平仓持仓按当前中间价算浮盈亏，到期的自动结算，并刷新报告 |
| `close ID` | "提前平仓 #3" | 按当前中间价了结一笔（到期日之前才能用） |
| `report` | "给我看报告" | 不取行情，只读最近一次生成的报告 |

所有命令从仓库根目录运行：

```bash
./.venv/Scripts/python.exe scripts/spread_sim.py status
./.venv/Scripts/python.exe scripts/spread_sim.py close ID
```

## Steps

### 0. 语言

读 `data/config.md` 的 `lang`（默认 `zh`），用该语言回复。脚本输出是中文，如实转述，不要重新计算数字。

### 1. status

1. 运行 `status`。它对每笔未平仓持仓取行情、算浮盈亏，并提示止盈 / 止损标记。**标记只是建议，永远不要替用户平仓。**
2. 到期日**之后**的持仓会按到期日收盘价自动结算；到期当天只提示"次日再运行"。
3. 转述：每笔的浮盈亏和距离、哪些触发了标记、哪些刚结算。
4. 指给用户看报告：`data/paper-trading/report.html`（网页）和 `report.md`（文字），都由 `status` / `open` / `close` 刷新；只有 `status` 带实时行情。
5. 数据源连不上（IBKR 网关不可达或中途掉线、Yahoo 离线或被限流）时整次中止、退出码非 0，不会换别的数据源重试；如实转述报错。

### 2. close ID

只在用户**明确决定**了结某一笔时运行。按当前中间价记账，状态变为已平仓。价差和单腿用一条腿或两条腿的中间价；保护性 Put、Collar、铁鹰和 Call 借方价差需要每条腿都有有效报价，否则拒绝，不猜价。到期日之后用 `status` 结算，不能 `close`。

### 3. report

读 `data/paper-trading/report.md`，转述：累计结果、各策略对比、样本数提示（少于 30 笔不下结论）。保护性 Put 与 Collar 不进胜率统计，有自己的一节；铁鹰在"按策略对比"里单独一行，另有"铁鹰记录"一节，不进 Bull Put 的胜率和 Delta 档位表。Call 借方价差（买方）同样在"按策略对比"里单独一行，另有"Call 借方价差记录"一节（每笔的腿、净支出、所需胜率、捕获比例），也不进 Bull Put 的胜率和 Delta 档位表；它的"所需胜率"是净支出 ÷ 宽度，与卖方口径不同。

## 各策略怎么开仓（本命令不开仓）

| 策略 | 开仓命令 |
|---|---|
| Bull Put 价差 | `/spread-sim` |
| 现金担保 Put | `/put-screen` |
| 备兑 Call | `/call-screen` |
| 保护性 Put / Collar | `/collar-screen` |
| 铁鹰（Iron Condor） | `/condor-sim` |
| Call 借方价差（Bull Call Debit Spread） | `/bullcall-sim` |

## 相关命令

- `/option-review`：每周给未平仓持仓一个建议。
- `/option-journal`：用历史建议和最终结果比，看"建议准不准"。

## Rules

- 不修改账本以外的东西；不平仓除非用户明确要求；不写 `data/positions.md` / `outputs/`。
- `status` / `open` / `close` 会刷新 `wiki/options/paper-trading.md`（只含价差和现金担保 Put 的过滤版）；其余策略（含铁鹰、Call 借方价差）不会出现在公开页上。
- 金额按每张合约 ×100、中间价成交、不含手续费。
- 模拟 / 研究用途，非投资建议。
