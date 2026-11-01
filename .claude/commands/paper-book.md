# paper-book

看模拟盘的"账本"：所有策略合起来赚了多少、每个策略各赚了多少、每一笔的明细，以及每笔持仓现在赚了多少、哪些到期了要结算、要不要提前了结。**覆盖所有策略**（价差、现金担保 Put、备兑 Call、保护性 Put、Collar、铁鹰、Call 借方价差、风险反转，以及三种看跌策略：Call 贷方价差、Put 借方价差、买入 Put），不用先想"这笔是哪个命令开的"。

结算和平仓落到现有的账本逻辑上（`scripts/spread_sim.py` 的 `status` / `close`，其余命令的同名子命令走的是同一份代码）；总账由只读脚本 `scripts/paper_book.py` 生成，写到 `data/paper-trading/book.md`（与 `report.md` 同目录，私有）。**只模拟，不下单，不碰 `data/positions.md`。**

## Arguments

| 动作 | 例子 | 做什么 |
|---|---|---|
| `status` | "看一下模拟盘""周中检查" | 先结算到期的、刷新旧报告，再按当前中间价生成总账（已实现 / 浮动 / 合计、分策略、每笔明细） |
| `close ID` | "提前平仓 #3" | 按当前中间价了结一笔（到期日之前才能用） |
| `report` | "给我看报告" | 不取行情，生成离线总账（浮动盈亏显示"—"）；旧的 `report.md` 仍可看 |

所有命令从仓库根目录运行：

```bash
./.venv/Scripts/python.exe scripts/spread_sim.py status
./.venv/Scripts/python.exe scripts/paper_book.py status
./.venv/Scripts/python.exe scripts/paper_book.py
./.venv/Scripts/python.exe scripts/spread_sim.py close ID
```

## Steps

### 0. 语言

读 `data/config.md` 的 `lang`（默认 `zh`），用该语言回复。脚本输出是中文，如实转述，不要重新计算数字。

### 1. status

1. 先运行 `spread_sim.py status`。它对每笔未平仓持仓取行情、算浮盈亏，并提示止盈 / 止损标记。**标记只是建议，永远不要替用户平仓。**
2. 到期日**之后**的持仓会按到期日收盘价自动结算；到期当天只提示"次日再运行"。它也刷新 `data/paper-trading/report.html` 和 `report.md`（与以前相同）。
3. 再运行 `paper_book.py status`：只读，按当前中间价重新取一次浮盈亏，写 `data/paper-trading/book.md` 并打印摘要；不结算、不平仓、不改账本。某一笔取不到行情时那笔标"行情缺失"、不计入浮动，其余照常。
4. 按顺序转述总账三层：① 总览：已实现、浮动、合计三个数分开说（不要相加成一个数再报），加胜率、占用资金和两个收益率；② 分策略表；③ 每笔明细（笔数多时只挑未平仓和最近结算的几笔，并说明完整表在 `book.md`）。再补一句 `spread_sim.py status` 打印的止盈 / 止损标记和刚结算的笔。
5. 数据源连不上（IBKR 网关不可达或中途掉线、Yahoo 离线或被限流）时整次中止、退出码非 0，不会换别的数据源重试，`paper_book.py status` 也不写 `book.md`；如实转述报错，可改用离线的 `paper_book.py`（浮动显示"—"）。

### 2. close ID

只在用户**明确决定**了结某一笔时运行。按当前中间价记账，状态变为已平仓。价差和单腿用一条腿或两条腿的中间价；保护性 Put、Collar、铁鹰、Call 借方价差、风险反转、Call 贷方价差和 Put 借方价差需要每条腿都有有效报价（买入 Put 只有一条腿，也必须有有效报价），否则拒绝，不猜价。到期日之后用 `status` 结算，不能 `close`。

### 3. report

运行 `paper_book.py`（默认离线，不取行情），按同样三层转述 `book.md`：已实现与合计（离线时合计不含浮动盈亏，要说出来）、分策略、每笔明细；样本少于 30 笔不下结论。

旧的 `data/paper-trading/report.md` 仍然保留，想看各策略的细节表时再读它：保护性 Put 与 Collar 不进胜率统计，有自己的一节；铁鹰在"按策略对比"里单独一行，另有"铁鹰记录"一节，不进 Bull Put 的胜率和 Delta 档位表。Call 借方价差（买方）同样在"按策略对比"里单独一行，另有"Call 借方价差记录"一节（每笔的腿、净支出、所需胜率、捕获比例），也不进 Bull Put 的胜率和 Delta 档位表；它的"所需胜率"是净支出 ÷ 宽度，与卖方口径不同。风险反转同样在"按策略对比"里单独一行，另有"风险反转记录"一节（每笔的腿、净权利金标注净收入 / 净支出、占用资金（现金担保保守口径）、下行盈亏平衡价、结局区）；它没有"所需胜率"，也不进 Bull Put 的胜率和 Delta 档位表。本总账里风险反转的占用资金同样是 (Put 行权价 − 净权利金) × 100 × 张数，盈亏只含期权腿，不含被指派后的股票腿。三种看跌策略同样各在"按策略对比"里单独一行，各有一节记录（标题都标明"看跌"），都不进 Bull Put 的胜率和 Delta 档位表：Call 贷方价差（卖方）的所需胜率是 1 − 权利金 ÷ 宽度，占用资金 = (宽度 − 权利金) × 100 × 张数；Put 借方价差（买方）的所需胜率是净支出 ÷ 宽度，另有捕获比例，占用资金 = 净支出 × 100 × 张数；买入 Put 没有所需胜率（看平均赢额 / 平均亏额），占用资金 = 净支出 × 100 × 张数（全部权利金）。本总账的占用资金与 report.md 同口径；页脚的口径说明没有逐一列出这三种，以这里为准。

## 各策略怎么开仓（本命令不开仓）

| 策略 | 开仓命令 |
|---|---|
| Bull Put 价差 | `/spread-sim` |
| 现金担保 Put | `/put-screen` |
| 备兑 Call | `/call-screen` |
| 保护性 Put / Collar | `/collar-screen` |
| 铁鹰（Iron Condor） | `/condor-sim` |
| Call 借方价差（Bull Call Debit Spread） | `/bullcall-sim` |
| 风险反转（Risk Reversal） | `/rr-sim` |
| Call 贷方价差（Bear Call Credit Spread，看跌） | `/bearcall-sim` |
| Put 借方价差（Bear Put Debit Spread，看跌） | `/bearput-sim` |
| 买入 Put（Long Put，看跌） | `/longput-sim` |

## 相关命令

- `/option-review`：每周给未平仓持仓一个建议。
- `/option-journal`：用历史建议和最终结果比，看"建议准不准"。

## Rules

- 不修改账本以外的东西（`book.md` 是只读总账，只由 `paper_book.py` 覆盖写）；不平仓除非用户明确要求；不写 `data/positions.md` / `outputs/`。
- `status` / `open` / `close` 会刷新 `wiki/options/paper-trading.md`（只含价差和现金担保 Put 的过滤版，与 book.md 同样三层：总览 / 分策略 / 每笔明细）；其余策略（含铁鹰、Call 借方价差、风险反转和三种看跌策略）不会出现在公开页上。
- 金额按每张合约 ×100、中间价成交、不含手续费。
- 模拟 / 研究用途，非投资建议。
