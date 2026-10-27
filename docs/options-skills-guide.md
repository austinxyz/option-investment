# 期权工具使用指南

模拟盘（paper trading）期权工具的总览：有哪些命令、怎么用、一周怎么走、数据放在哪。**筛选、记账、复盘这几个命令是模拟/研究用途，不下单，不碰 `data/positions.md`。不构成投资建议。**

## 一览

| 命令 | 做什么 | 什么时候用 | 状态 |
|------|--------|-----------|------|
| `/spread-sim` | Bull Put 价差：出三档候选、开仓（要你给 ticker）；`status` / `close` 也能在这里用，但统一入口见 `/paper-book` | 想试价差时，你自己点 ticker | 可用 |
| `/paper-book` | 账本总入口：`status` 看浮盈亏并结算到期的、`close ID` 提前了结、`report` 看报告。**覆盖所有策略**，不用先想这笔是哪个命令开的 | 周中检查、到期后结算、想看整体情况时 | 可用 |
| `/put-screen` | 从 wiki 自动筛 Sell Put（现金担保 Put）候选，选中后记入同一个账本 | 想找 Sell Put 时（IBKR 模式任何时段都能跑） | 可用 |
| `/call-screen` | 对持股筛备兑 Call（covered call）候选，选中后记入同一个账本；也用来练 Wheel | 想对涨不动的持股收租金时（IBKR 模式任何时段都能跑） | 可用 |
| `/collar-screen` | 对持股筛 Collar（买 Put 下限 + 卖 Call 上限）和保护性 Put 候选，选中后记入同一个账本 | 想给已有持股设一个下限、又不想多掏现金时 | 可用，只做模拟 |
| `/condor-sim` | 对你给的 ticker 出三档铁鹰（Iron Condor）候选，选中后记入同一个账本；一笔记一条整体记录 | 想试"赌股价留在区间里"的策略时，你自己点 ticker | 可用，只做模拟 |
| `/bullcall-sim` | 对你给的 ticker 出三档 Call 借方价差（Bull Call Debit Spread）候选，选中后记入同一个账本；一笔记一条整体记录 | 想试"净付钱、靠赔率赚钱"的看涨买方策略时，你自己点 ticker | 可用，只做模拟 |
| `/strategy-pick` | 对你给的 ticker 先选策略：读 wiki 论点、一年日线和期权数据（当前数据源），按七步决策树推荐 1–2 个看涨或中性策略，每一步的数字都写进报告；只建议，不开仓 | 不知道这只票该用哪个策略时，先跑它，再去用它指向的命令 | 可用，只读，报告写在私有的 `data/strategy-picks/` |
| `/option-review` | 给账本里每笔未平仓持仓一个建议：平仓 / 继续持有 / 需要关注，并存快照 | 每周一次；想看时随时跑 | 可用 |
| `/option-journal` | 复盘：用快照里的建议和最终结果比，看"建议准不准" | 有已结算持仓之后 | 可用 |

## 策略和工具的对应

`wiki/options/strategies/` 里有 9 篇策略页，但模拟盘工具只覆盖其中一部分。下表说清楚哪个策略能在模拟盘里试、用哪个命令，哪个没有工具以及为什么。

| 策略页 | 模拟盘工具 | 说明 |
|---|---|---|
| [垂直 Put 贷方价差](../wiki/options/strategies/bull-put-credit-spread.md) | `/spread-sim` | 已覆盖：出候选、开仓、平仓、结算 |
| [现金担保 Put](../wiki/options/strategies/cash-secured-put.md) | `/put-screen` | 已覆盖 |
| [备兑 Call](../wiki/options/strategies/covered-call.md) | `/call-screen` | 已覆盖 |
| [Wheel](../wiki/options/strategies/wheel.md) | `/put-screen` → `/call-screen` | 没有单独命令：卖现金担保 Put、被指派后自动变成假设持仓、再对它卖备兑 Call，流程见下面"Wheel 怎么用" |
| [保护性 Put / Collar](../wiki/options/strategies/protective-put-collar.md) | `/collar-screen` | 已覆盖：出三档候选（含只买 Put 的变体）、开仓、结算 |
| [铁鹰（Iron Condor）](../wiki/options/strategies/iron-condor.md) | `/condor-sim` | 已覆盖：出三档候选、开仓、平仓、结算（按整体一条记录）；只做模拟 |
| [Bull Call 借方价差](../wiki/options/strategies/bull-call-debit-spread.md) | `/bullcall-sim` | 已覆盖：出三档候选（按长腿 Delta 0.60 / 0.50 / 0.40）、开仓、平仓、结算（按整体一条记录，买方口径）；只做模拟 |
| [买入 Call / 买入 Put](../wiki/options/strategies/long-call-long-put.md) | 无 | 不计划加：没有可检验的规则，模拟盘测不出东西 |
| [裸卖 Call](../wiki/options/strategies/naked-call.md) | 无 | **不计划加**：风险无限，策略页本身也不建议 |

策略页只讲概念和风险，数字是来源里说的，不构成投资建议；"无工具"不代表不能做，只是没法在模拟盘里自动筛选和记账。

八个命令共用**同一个账本**（价差、Sell Put、Covered Call、Collar / 保护性 Put、铁鹰、Call 借方价差记在一起，用 `strategy` 字段区分），所以报告里能并排比较各策略的胜率。

**review（每周建议）和复盘不在 `/spread-sim` 里，而是单独的命令，但覆盖整个账本——价差也在内。** `/spread-sim` 负责开仓、平仓、看盈亏、到期结算；`/option-review` 给价差和 Sell Put 的每笔未平仓持仓出建议；`/option-journal` 比较这些建议和最终结果。三者读写同一份数据，所以你的价差持仓不需要另外做什么就会被检查。

## 一周怎么走（建议流程）

0. **不知道该用哪个策略时**：先跑 `/strategy-pick TICKER`，它按你的看法、走势和 IV 位置推荐 1–2 个策略和下一步命令（见下面 "/strategy-pick" 一节），再用对应的命令挑合约。
1. **IBKR 模式任何时段都能跑**（盘中数据最新，休市后用的是延迟冻结的上一交易日数据；Yahoo 模式建议美东开盘时间，休市后 bid/ask 常为空，见 `docs/option-data-sources.md`）：`/put-screen` → 看汇总表和每只的说明 → 想开的用 `open`。
2. **想试价差**：`/spread-sim suggest TICKER` → 看三档候选 → 选一档 `open`（详见下面"价差怎么开仓和平仓"）。
3. **每周五**：手动 `/option-review`。
4. **持仓过程中**：`status` 看浮盈亏（到期的会自动结算）；想提前了结就 `close ID`。
5. **到期之后**：不用做什么，`status` 会自动结算；数据积累到一定笔数，用 `/option-journal` 看建议准不准。

## 各命令怎么用

### /spread-sim（Bull Put 价差）

```bash
./.venv/Scripts/python.exe scripts/spread_sim.py suggest TICKER [--width W]
./.venv/Scripts/python.exe scripts/spread_sim.py open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/spread_sim.py status
./.venv/Scripts/python.exe scripts/spread_sim.py close ID
```

- `suggest`：对一个 ticker 出三档候选（卖出腿 Delta 约 0.10 / 0.15 / 0.20，到期日取离快照日 7 天以上的第一个），存说明文件到 `data/paper-trading/suggestions/<日期>-<TICKER>.md`。**不写账本。**
- `open`：把你看过的那一档原样记进账本（不重新取行情）。
- 在对话里直接说"选票 ZZZQ ZZQB""ZZZQ 选 0.15 档""看一下模拟盘""提前平仓 #2"也行。

**价差怎么开仓和平仓：**

1. **开仓（两步，必须先出候选）**
   1. `suggest TICKER`：出三档候选并保存。可以一次对几个 ticker 各跑一次。想指定宽度加 `--width 5`，不加则自动推荐。
   2. `open TICKER 0.15`（0.10 / 0.15 / 0.20 三选一；想开多张加 `--contracts 2`）：把你**刚看过的那份候选**原样记进账本，不重新取行情。没有保存的候选、或候选已过期，会拒绝并要你重新 `suggest`；`--width` 和保存的不一致也会拒绝。
   3. 同一个 ticker + 同一个到期日已经有记录（价差或 Sell Put）时会拒绝，不重复开。
2. **提前平仓**：`close ID`（ID 是 `status` 或 `report.md` 里的 `#` 号）。按**当前中间价**买回，记入盈亏，状态变成已平仓。只在到期日**之前**能用；到期日过了要运行 `status`，不能再用报价平仓。
3. **不平仓、拿到到期**：什么都不用做。到期日**之后**的某一天运行 `status`（到期当天只会提示"次日再运行"），它按到期日收盘价自动结算：价差在两个行权价之间算部分亏损，跌破买入腿算最大亏损。
4. **看当前状态**：`status`，它打印每笔的浮盈亏和距离卖出腿多远，到止盈线 / 止损线会提示（仅建议，不会自动平仓），并更新 `report.md`。

### /put-screen（Sell Put 筛选）

```bash
./.venv/Scripts/python.exe scripts/put_screen.py suggest [--dte weekly|monthly]
./.venv/Scripts/python.exe scripts/put_screen.py open TICKER [--contracts N]
./.venv/Scripts/python.exe scripts/put_screen.py status
./.venv/Scripts/python.exe scripts/put_screen.py close ID
```

- `suggest`（不用传 ticker）：扫描 `wiki/tickers/*/overview.md`，用规则剔除不合格的，给每个入选的选一个 Put 合约。默认 `--dte weekly`（周度），也可以 `--dte monthly`。
  - **筛选规则**：Status 不是 Avoid；收盘价高于 MA200；到期前没有财报；买卖价差 ≤ 权利金的 5%；有隐含波动率。
  - **到期规则（两种模式，其余规则完全相同）**：
    - `weekly`（默认）：在所有挂牌到期日（含周度）里，取距快照日 7–14 天内最早的一个；窗口内没有就剔除，理由"没有可用的周度到期"（没有周度合约的标的会被剔除，不回退到别的到期）。
    - `monthly`：月度到期，优先距今天 30–45 天（原有行为不变）。
    - 为什么默认周度：月度 Sell Put 一个月才结算，纸上交易样本积累太慢，也没法和约 7 天的 Bull Put 价差公平比较。周度权利金小、Gamma 高，到期前一周波动比月度大，汇总和说明文件里有固定提示。
    - 同一天换模式重跑会覆盖当天文件（最后一次 `suggest` 为准）。
  - **合约规则**：虚值 Put，Delta 最接近 0.20（偏离超过 0.05 就剔除，价差过宽时最多往下找 3 档，Delta 不低于 0.10）。
  - 输出：汇总 `data/paper-trading/put-screen/<日期>.md`（按 🏦 蓝筹 / 🚀 翻倍 / ⚡ 动量 / 未分类分表，加被剔除原因）；每只入选的说明 `suggestions/<日期>-<TICKER>-put.md`。
- `open`：记录保存的那个合约；如果最新一次筛选已经把这只剔除了，会拒绝，请重新 `suggest`。
- `status` / `close`：和 `/spread-sim` 是同一个（两种策略一起处理）。
- **期权数据来自当前数据源**（`ibkr` / `yahoo` / `demo`，见下面"数据源"一节和 `docs/option-data-sources.md`）。IBKR 模式是只读延迟数据，不要求在美股盘中跑：休市（周末、收盘后）网关给的是延迟冻结的上一交易日数据，汇总和说明文件按快照日标注；Yahoo 模式建议开盘时间跑。Moving average、财报日期、历史价格一律来自 Yahoo。开仓前，请把说明文件里的行权价、Delta、权利金和券商期权链核对一下。设置、数据质量、行情抢占（错误 10197）、数据源不可达时的行为见下面"数据源"一节。
- Sell Put 到期结算：收盘价 ≥ 行权价，权利金全收；低于行权价记为被指派，盈亏 =（权利金 −（行权价 − 到期收盘价））× 100 × 张数。**只记录到到期日**，被指派之后怎么处理股票不在模拟范围内。

### /call-screen（备兑 Call 筛选）

```bash
./.venv/Scripts/python.exe scripts/call_screen.py suggest [--dte weekly|monthly]
./.venv/Scripts/python.exe scripts/call_screen.py open TICKER [--lot real|<ID>] [--contracts N]
./.venv/Scripts/python.exe scripts/call_screen.py status
./.venv/Scripts/python.exe scripts/call_screen.py close ID
./.venv/Scripts/python.exe scripts/call_screen.py shares add TICKER --shares N --cost C
./.venv/Scripts/python.exe scripts/call_screen.py shares list
./.venv/Scripts/python.exe scripts/call_screen.py shares remove ID
```

- **三种持股来源**：真实持仓（每次现读 `data/positions.md`，**只读**，同一 ticker 跨账户合并）；手动添加的假设持仓（`shares add`）；账本里被指派的现金担保 Put（`suggest` 或 `status` 时自动变成假设持仓）。每个持仓独立出候选，每 100 股可卖一张。
- `suggest`（不用传 ticker）：对持股表里每个能覆盖至少一张的持仓，选一个虚值 Call，Delta 最接近 0.20。默认 `--dte weekly`（距今天 7–14 天最近到期），也可以 `monthly`。汇总在 `data/paper-trading/call-screen/<日期>.md`，逐只说明在 `suggestions/<日期>-<TICKER>-call-real.md` 或 `-call-lot<ID>.md`。
  - **规则**：到期前有财报 → 排除；除息日在到期前、行权价低于成本、财报日期未知 → 只警告，不排除；Delta 偏离、价差过宽、缺 IV → 排除。
  - **不判断"涨不动"**：汇总里列出现价、相对 MA50 / MA200、近一月涨幅，只是信息；卖哪只由你自己挑。
  - **低于成本的警告**：行权价低于成本时，会写明被行权相对原始成本、相对有效成本（扣掉已收权利金）每股亏多少。
- `open`：把保存的候选原样记入账本（不重新取行情），记下合约、持仓来源、开仓时的每股成本、覆盖股数。同一 ticker 有多个持仓候选时要用 `--lot` 指定；超过可覆盖张数会拒绝。不占用额外现金。
- `status` / `close`：和另外两个策略共用。**不会自动平仓**，到止盈 / 止损线只提示。
- **到期结算**（用到期日收盘价）：
  - 收盘价低于行权价 → 没被行权，权利金全收，股票还在（假设持仓的有效成本降低）。
  - 收盘价达到或高于行权价 → 被行权，股票按行权价卖出。**期权腿**（权利金）和**股票腿**（行权价 − 参考价）分开记录；胜率**只看期权腿**，"含股票腿"总盈亏只显示。真实持仓不会被改动。
  - 只在到期日结算，不模拟提前行权。
- `/option-review` 也会检查 Covered Call：现价达到或高于行权价 = 价内 → 需要关注（可能被行权，由你决定接受还是回购）。
- 数据来源和运行时段同 `/put-screen`（当前数据源；IBKR 模式任何时段可跑）；模拟结果比真实乐观（中间价成交、无手续费）。

#### Wheel 怎么用

Wheel：卖现金担保 Put → 被指派拿到股票 → 对股票卖 Call → 被行权卖掉股票 → 回到卖 Put。下面用虚构的 ticker AAA 示范：

```bash
# 1. 卖一张现金担保 Put
./.venv/Scripts/python.exe scripts/put_screen.py suggest
./.venv/Scripts/python.exe scripts/put_screen.py open AAA

# 2. 到期后（次日起）运行 status：收盘价低于行权价则这笔记为被指派
./.venv/Scripts/python.exe scripts/call_screen.py status

# 3. suggest（或上一步的 status）会把被指派的 Put 变成假设持仓，有效成本 = 行权价 − 权利金
./.venv/Scripts/python.exe scripts/call_screen.py suggest
./.venv/Scripts/python.exe scripts/call_screen.py shares list

# 4. 对这批新股票卖一张 Call（--lot 填 shares list 里的持仓编号，不是被指派 Put 的编号）
./.venv/Scripts/python.exe scripts/call_screen.py open AAA --lot 1

# 5. 以后每次 status 结算：没被行权 → 权利金再降低有效成本，继续下一轮；被行权 → 股票按行权价卖出，回到第 1 步
./.venv/Scripts/python.exe scripts/call_screen.py status
```

要点：每收一次 Call 权利金，这批股票的有效成本就下降一点；股票腿盈亏从指派日收盘价算起，Put 那段下跌不会被重复计算。也可以不走 Wheel，用 `shares add AAA --shares 100 --cost 50` 手动加一个假设持仓，或直接对真实持仓出候选。Wheel 的每一步由你决定，工具不会自动循环。

### /option-review（每周管理检查）

```bash
./.venv/Scripts/python.exe scripts/put_review.py
```

不用传参数。对每笔未平仓持仓给结论，并把这次的建议存成快照 `data/paper-trading/reviews/<日期>.md` 和 `.json`（同一天重跑覆盖，不同日期各存一份；按持仓 id 关联，之后复盘用）。**只给建议，不平仓、不改账本。**

规则（按顺序，第一个命中的算；阈值在 `scripts/put_review_rules.py` 顶部，可改）：

| 顺序 | 条件 | 结论 |
|---|---|---|
| 0 | 取不到报价或现价 | 需要关注（该行没有数据） |
| 1 | 到期日当天或已过期 | 等待结算（运行 `status`） |
| 2 | 已价内（Put / 价差：现价低于卖出腿行权价；Covered Call：现价达到或高于行权价，可能被行权）或已触及止损标记 | 需要关注 |
| 3 | 权利金已赚 ≥ 80% | **平仓** |
| 4 | 到期前有财报，或当前 Delta ≥ 0.35 | 需要关注 |
| 5 | 其他 | 继续持有（剩余 ≤ 5 天且 Delta ≤ 0.10 时注明"等它自然到期归零"） |

- 数据陈旧（休市）时**不使用 Delta**，该行会写"数据陈旧，Delta 未使用"。
- 价差只看卖出腿的 Delta。止损标记：价差是亏损达到最大亏损的 50%；Sell Put 是浮亏达到权利金的 2 倍；止盈标记：赚到权利金的 50%。
- 这些阈值是常见经验值，没有来源支撑，要靠你自己的模拟数据检验，这正是 `/option-journal` 的目的。

### /option-journal（复盘）

```bash
./.venv/Scripts/python.exe scripts/put_journal.py
./.venv/Scripts/python.exe scripts/put_journal.py note ID "文字" [--kind reason|mindset|exit|other]
```

离线、只读：不联网，不改账本和快照，只写 `data/paper-trading/journal.md` / `journal.json`（以及你主动记的笔记 `journal-notes.json`）。把 `/option-review` 存的快照和已结算持仓（到期或提前平仓）的最终盈亏对照，只用日期不晚于平仓日（含当天）的快照；至少有一条可用快照的已结算持仓才算"可比"。报告内容：

- **A 平仓建议值不值得听**：对出现过"平仓"建议的持仓，比较"最早那次建议时平仓"和"实际结果"的盈亏差额（正数 = 听建议更好）。
- **B "需要关注"准不准**：被标过关注的持仓里亏损的比例（精确率）、亏损的持仓里被标过关注的比例（召回率），并按首次命中的规则分。
- **C "继续持有"后来有没有事**：最后一条建议是继续持有的持仓里最终盈利的比例。
- **D 连赢之后**：前面连赢 3 笔后下一笔的胜率（样本几乎总是不足，仅供参考）。
- **阈值重算（what-if）**：用快照里存的数字重跑规则，平仓阈值扫 0.50–0.90，关注的 Delta 阈值扫 0.25–0.45，不重新取行情。
- **备兑看涨（单独一块）**：Covered Call 不计入上面 A–D 和阈值重算，另有一块写笔数、赢了几笔（只看期权腿盈亏）、胜率和期权腿总盈亏，样本不足同样提示。
- 每笔已结算持仓的时间线（各次建议、你实际怎么做、是否和建议一致、笔记），以及进行中持仓已积累的建议数。

`note ID "文字"` 给某笔持仓记一条笔记（只追加，id 必须在账本里），只显示在时间线里，不参与统计。

**30 笔规则**：每个统计块都写样本数，可比持仓少于 30 笔会写"样本不足，别下结论"；刚开始几乎一定不足，数字只能看，不能据此改阈值。口径提醒：当时平仓按快照中间价算，真实有滑点，"听建议"会略显乐观。

### /collar-screen（Collar / 保护性 Put 筛选）

```bash
./.venv/Scripts/python.exe scripts/collar_screen.py suggest [--dte monthly|long]
./.venv/Scripts/python.exe scripts/collar_screen.py open TICKER {90,85,80} [--lot real|<ID>] [--contracts N] [--put-only]
./.venv/Scripts/python.exe scripts/collar_screen.py status
./.venv/Scripts/python.exe scripts/collar_screen.py close ID
```

- **持股来源**和 `/call-screen` 一样：真实持仓（只读）、手动假设持仓、被指派的 Put。假设持仓用 `call_screen.py shares add ...` 管理。
- `suggest`（不用传 ticker）：对每个至少 100 股的持仓，在**现价的 90% / 85% / 80%** 三档各取一个 Put 下限（不高于目标价的最近真实行权价），再选一个权利金最接近 Put 保费的虚值 Call，让净现金流接近零。默认 `--dte monthly`（30–60 天内最近到期），`--dte long` 取 90–180 天。每条腿买卖价差都要 ≤ 该腿权利金的 5%，否则那一档被剔除并写明原因。汇总在 `data/paper-trading/collar-screen/<日期>.md`，逐只说明在 `suggestions/<日期>-<TICKER>-collar-real.md` 或 `-collar-lot<ID>.md`。
- `open`：把保存的那一档原样记入账本（不重新取行情）。加 `--put-only` 则只记保护性 Put，不卖 Call。
- **净成本与锁定区间**：净权利金 = Call 收入 − Put 支出，负数是净支出。锁定区间 = Put 行权价 ～ Call 行权价；"上限代价"是现价到 Call 行权价的空间，涨过上限的部分被让出；这是**每张、以现价为起点**的数字，报告里的上限代价标注"相对参考价（真实持仓为平均成本）"，起点不同。**零成本只是现金流，不是机会成本为零。**
- **只警告、不排除**：到期前有财报（只是信息，Collar 常为财报而做）；除息日在到期前且 Call 偏实值（行权价不高于现价的 103%，只是筛选时的警告）；Call 行权价低于成本；报价陈旧；真实持仓在含应税账户且持股不满 1 年（或买入日未知）时的税务提醒——买入 Put 可能重置持有期，**税务细节策略页的来源没有覆盖，请先咨询税务专业人士**。
- **到期结算**（用到期日收盘价）：`called_away`（被行权）、`protected`（Put 价内，下限生效）、`expired_worthless`（两腿作废）、`closed`（提前平仓）。**记账盈亏只含期权腿**；股票腿单独记录、只显示。Collar 在假设持仓上会把期权腿盈亏折入成本，保护性 Put 不改任何假设持仓；真实持仓不改，被行权时报告结果列和 `status` 结算提示里标注"（真实持仓未变）"。
- **不计入胜率**，也**不出现在过滤后的公开页**（`open` / `status` / `close` 仍会刷新该页，但其中不含这两个策略）；私有报告里有单独的"保护性 Put 与 Collar 记录"一节。`/option-review` 对它们只给继续持有或需要关注（Call 变价内、临近除息且 Call 已价内（现价 ≥ Call 行权价）、取不到报价），**不会建议止盈平仓**；`/option-journal` 有单独的「保护 / 领口」块。
- `status` / `close` 与其他策略共用。

用虚构的 ticker AAA（一手假设持仓，每股成本 50，现价 100）示范：

```bash
./.venv/Scripts/python.exe scripts/call_screen.py shares add AAA --shares N --cost 50   # N 是 100 的倍数
./.venv/Scripts/python.exe scripts/collar_screen.py suggest
# 汇总里 AAA 的 85% 档：Put 85（保费 1.60）、Call 112（权利金 1.55），净权利金 -0.05/股，
# 锁定区间 $85 ～ $112，上限代价 $1,200/张
./.venv/Scripts/python.exe scripts/collar_screen.py open AAA 85 --lot 1
./.venv/Scripts/python.exe scripts/collar_screen.py status
```

到期日收盘 120：Call 被行权，结果 `called_away`。期权腿盈亏 = −0.05 × 100 = −$5（Call 被行权的代价体现在股票按 112 卖出，不重复算进期权腿）；股票腿 = (112 − 50) × 100 = +$6,200，其中 112 以上的涨幅被让出；股票腿单独显示，不并入记账盈亏。到期日收盘 70：Put 价内，结果 `protected`，期权腿 = −0.05 × 100 + (85 − 70) × 100 = +$1,495。收盘 100：落在两个行权价之间，结果 `expired_worthless`，期权腿 −$5。

### /condor-sim（铁鹰）

```bash
./.venv/Scripts/python.exe scripts/condor_sim.py suggest TICKER [--width W]
./.venv/Scripts/python.exe scripts/condor_sim.py open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/condor_sim.py status
./.venv/Scripts/python.exe scripts/condor_sim.py close ID
```

- `suggest`：出三档候选，两个短腿（Put 和 Call）的 |Delta| 约 0.10 / 0.15 / 0.20，偏离目标超过 0.05 的档被剔除。**两侧等宽**：先定 Put 价差，Call 长翼恰好放在"短 Call + Put 侧实际宽度"处，找不到就剔除，不凑近似。到期日同 `/spread-sim`（距快照日至少 7 天的第一个）。不写账本。
- **流动性规则和策略页不同**：策略页要求四条腿买卖价差都 < 权利金的 5%；本工具只对**两个短腿**硬卡 5%（超了该档剔除），**两个长翼价差 > 15% 只警告**。原因：长翼是远虚值，权利金只有几美分，价差比例天然很大，四条腿都硬卡 5% 会把几乎所有档位剔除，工具就没法用。
- `open`：把保存的那一档原样记成**一条**整体记录（不重新取行情，不拆成两条价差）。`--width` 要与保存候选的目标宽度一致，否则要重新 `suggest`。
- **只警告、不排除**：大涨对铁鹰是满额亏损（每份说明都带）；到期前有财报；短 Call 偏近现价且除息日在到期前；净权利金不到宽度的 15%（赔率太差）；长翼价差 > 15%；报价陈旧。
- **结算**（到期日收盘价）：每一侧亏损各自限制在 [0, 宽度]。**`breached_side` 只在到期结算时写**：`none` / `put` / `call`；提前平仓不写，报告里显示"—"。盈亏为 0 记为亏损。
- **统计**：铁鹰在"按策略对比"里有自己的一行和一节"铁鹰记录"，**不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 块**；"理论盈亏平衡"（1 − 净权利金 ÷ 宽度）与按实际盈亏比算的盈亏平衡口径不同，报告里已标明。少于 30 笔不下结论。
- **不出现在公开页**。`status` / `close` 与其他策略共用。

用虚构的 ZZZQ（现价 100）示范：

```bash
./.venv/Scripts/python.exe scripts/condor_sim.py suggest ZZZQ
# 汇总里 0.15 档：85/90/110/115（长 Put / 短 Put / 短 Call / 长 Call），单侧宽度 5，
# Put 侧权利金 0.80、Call 侧权利金 0.70，净权利金 1.50/股
./.venv/Scripts/python.exe scripts/condor_sim.py open ZZZQ 0.15
./.venv/Scripts/python.exe scripts/condor_sim.py status
```

核算（1 张，每股 ×100）：最大盈利 = 1.50 × 100 = $150；最大亏损 / 占用资金 = (5 − 1.50) × 100 = $350；盈亏平衡价 = 90 − 1.50 = 88.50 与 110 + 1.50 = 111.50；所需胜率 = 1 − 1.50 ÷ 5 = 70%。到期日收盘：100 → 在两个短腿之间，保留全部权利金 +$150，`breached_side` 为 `none`；87 → 跌破短 Put 3，盈亏 = (1.50 − 3) × 100 = −$150，Put 侧被突破；120 → 涨过长 Call，Call 侧亏满宽度 5，盈亏 = (1.50 − 5) × 100 = −$350（最大亏损）。**大涨和大跌一样是满额亏损。**

### /bullcall-sim（Call 借方价差）

```bash
./.venv/Scripts/python.exe scripts/bullcall_sim.py suggest TICKER [--width W] [--min-days N]
./.venv/Scripts/python.exe scripts/bullcall_sim.py open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/bullcall_sim.py status
./.venv/Scripts/python.exe scripts/bullcall_sim.py close ID
```

- `suggest`：出三档候选，按**长腿** Call 的 Delta 约 0.60 / 0.50 / 0.40（实值长腿 + 虚值短腿 / 平值长腿 / 两腿都虚值的温和版），偏离目标超过 0.05 的档被剔除。**短腿必须恰好在"长腿 + 宽度"的行权价上**，找不到就剔除，不凑近似。到期日默认距快照日**至少 30 天**；`--min-days N`（N ≥ 7）可放宽，不足 30 天会警告"净买方，时间不在你这边"。不写账本。
- **流动性规则：两条腿都硬卡 5%**。长腿和短腿各自的买卖价差都必须 ≤ 该腿中间价的 5%，超了该档剔除。这和铁鹰（长翼只警告）不同：这里短腿离现价只有一个宽度，不是几美分的远翼。
- **所需胜率 = 净支出 ÷ 宽度**（买方口径，和卖方的"1 − 权利金 ÷ 宽度"相反），通常低于一半，这是正常的。存档里是小数（0.38），报告里显示为百分数（38%）。
- `open`：把保存的那一档原样记成**一条**整体记录（不重新取行情，不需要数据源）。`--width` 要与保存候选的宽度一致，否则要重新 `suggest`。
- **只警告、不排除**：每份说明都带"方向错了亏掉全部净支出：到期股价在长腿行权价或以下就是满额亏损；所需胜率低于一半是正常的，靠赔率赚钱"；所需胜率 > 60%（花得太多，赔率差）；到期前有财报；短 Call 偏近现价且除息日在到期前；不足 30 天；报价陈旧。
- **结算**（到期日收盘价 C）：每股价值 = C − 长腿行权价，限制在 [0, 宽度]；盈亏 = (价值 − 净支出) × 100 × 张数。恰好回本记为亏损。**捕获比例** = 盈亏 ÷ 最大收益，复盘里只对赢的笔平均。`status` 另会提示浮盈到最大收益的 50% 或浮亏到净支出的 50%。
- **`/option-review` 的买方规则**：浮亏到净支出的 50% → 需要关注；浮盈到最大收益的 50%，或现价到了短腿行权价 → 建议平仓；到期前有财报、剩余 10 天内仍没浮盈、剩余 5 天内现价还在两腿之间 → 需要关注。例外：短腿严格价内且除息日在到期前，先判需要关注（提前指派风险），再判平仓。不用 Delta 规则。
- **统计**：在"按策略对比"里有自己的一行和一节"Call 借方价差记录"，**不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 块**（它有自己的一块，没有"突破侧"）。少于 30 笔不下结论。
- **不出现在公开页**。`status` / `close` 与其他策略共用。
- 工具**不记**：出事时有没有做调整（没有调整操作），平仓时的 IV 只在拿得到时才记。

用虚构的 ZZQB（现价 100）示范：

```bash
./.venv/Scripts/python.exe scripts/bullcall_sim.py suggest ZZQB --width 5
# 汇总里 0.50 档：买 100 Call（中间价 3.20）、卖 105 Call（中间价 1.30），净支出 1.90/股
./.venv/Scripts/python.exe scripts/bullcall_sim.py open ZZQB 0.50
./.venv/Scripts/python.exe scripts/bullcall_sim.py status
```

核算（1 张，每股 ×100）：净支出 = 3.20 − 1.30 = 1.90；最大亏损 / 占用资金 = 1.90 × 100 = $190；最大收益 = (5 − 1.90) × 100 = $310；盈亏平衡价 = 100 + 1.90 = 101.90（比现价高 1.90%）；短腿 105 比现价高 5.00%（满额幅度）；所需胜率 = 1.90 ÷ 5 = 38%。到期日收盘：99 或 100 → 价值 0，盈亏 = −$190（最大亏损，收盘等于长腿也是满额亏损）；101.90 → 价值 1.90，盈亏 0，记亏损；103 → 价值 3.00，盈亏 = (3.00 − 1.90) × 100 = +$110，捕获比例 110 ÷ 310 = 0.3548；120 → 价值钳到宽度 5，盈亏 +$310（最大收益）。**方向错了亏掉全部净支出，胜率低于一半是正常的。**

### /strategy-pick（先选策略）

```bash
./.venv/Scripts/python.exe scripts/strategy_pick.py TICKER [--view 看好|中性|不看好] [--class bluechip|hot|etf]
```

**只建议，不开仓。** 它不下单、不碰账本和 `data/positions.md`，只把报告写到私有的 `data/strategy-picks/<运行日期>-<TICKER>.md` 和同名 `.json`（gitignored，里面有持仓判定和论点看法）。研究用途，不构成投资建议。

- **七步**：流动性门槛 → 标的类别（蓝筹 / 热门股 / ETF）→ 你的看法（wiki 论点或 `--view`）→ 一年走势 → IV 位置 → 是否持有 → 输出（1–2 个策略；财报 / 除息按类别并入警告）。报告里每一步都列出输入值、阈值、阈值常量名和结果，阈值表由代码按本次实际用到的常量生成。
- **看法**：来自 wiki 的 overview 状态和 thesis；`--view` 优先，两者不一致会写警告。没有可用论点时**退出码 3**：不写文件，由你说出看法（看好 / 中性 / 不看好），再带 `--view` 重跑，工具不替你选。
- **类别**：`--class bluechip|hot|etf`，不写则按 wiki 档位和 ETF 赛道表判定；热门股的阈值更严，财报在 7 天内第一推荐会变成"等财报后再看"。
- **退出码**：0 写了文件（门槛出局、不看好、历史不足一年也写，结论是"不做期权建议"之类）；1 数据源或行情失败，什么都不写；2 参数错误；3 需要你给看法。"数据源明确说没有期权数据"算门槛出局（退出码 0），"找不到合约"算缺数据（退出码 1）。
- **IV 位置是代理**：数据源没有 IV 历史，所以拿平值 IV 对自己过去一年的 21 日实现波动率分布做百分位，不是真正的 IV Rank；IV 缺失时退到实现波动率自比，是再退一步的代理。过去一年整体偏高或偏低时，百分位会误导。
- **±15% 内行权价个数是代理**：按全链合并计数，可能多算。
- **占比标记**：现金担保 Put 与 Bull Put 谁排第一，取决于"参考行权价 × 100 占组合约 x%"（≤ 10% 且蓝筹或 ETF → 现金担保 Put 第一）。这个占比挂在其中一个推荐的标记 `portfolio_share` 上；组合总值取不到时标 `rank_by_class_only`，只按类别排。文件里只写取整到两位小数的占比（如 `0.16`），组合总值本身不写。
- **下一步命令有它们自己的限制**：`/put-screen`、`/call-screen`、`/collar-screen` 都扫整个列表、不接 ticker；`/put-screen` 和 `/call-screen` 固定取 Delta 0.20（Put 往下最多到 0.10），`/collar-screen` 不按 Delta，而是按现价 90% / 85% / 80% 三档选 Put，所以远价外或"下行"的情形它们可能不出这只票；`/spread-sim`、`/condor-sim`、`/bullcall-sim` 才是对你给的 ticker 出候选。
- 它**不选合约**：参考价位 = 现价 ∓ z × 1σ 预期波动（线性近似，忽略漂移和偏度，标"粗略"），只是给你去挑合约时的区间。

用虚构的 ZZZQ 示范（运行日 2026-10-25，行情快照日 2026-10-23；蓝筹，wiki 状态 Active → 看好，论点 56 天未过期；未持有；虚构的组合总值 200,000）：

```bash
./.venv/Scripts/python.exe scripts/strategy_pick.py ZZZQ
```

核算（报告里每一项都能对上）：

- 走势：R1y = 100.00 ÷ 80.00 − 1 = +25.0%，MA50 96.00 > MA200 90.00，MA200 斜率 +2.04%，最大回撤 15.0%、一年实现波动率 30.0% 都低于蓝筹的高波动线 → 叶子"稳步上涨"。
- IV 位置：252 个收盘 → 251 个日对数收益 → 251 − 21 + 1 = **231 个 21 日窗口**；其中 174 个 ≤ 当前平值 IV 0.32，174 ÷ 231 = 0.75325 → **75.3%，高**（≥ 70%）。
- 1σ 预期波动 = 100 × 0.32 × √(28 ÷ 365) = 8.863037（显示 8.86）。Delta 0.20 的参考行权价 ≈ 100 − 0.84 × 8.863037 = **92.56**（粗略）。
- 排序：92.56 × 100 = 9,256，占组合 9,256 ÷ 200,000 = 4.6% ≤ 10%，且是蓝筹 → **第一：现金担保 Put（下一步 `/put-screen suggest --dte monthly`）；第二：Bull Put 价差（下一步 `/spread-sim suggest ZZZQ`）**。Bull Call、铁鹰、备兑 Call / Collar 各有一句"为什么不选"和策略页链接。

只改一处的变体（其余同上）：

- **IV 低**：231 个窗口里只有 58 个 ≤ 0.32 → 58 ÷ 231 = 0.25108 → 25.1% ≤ 30%，IV 低 → 看好 × 未持有 × 稳步上涨 × IV 低 → **Bull Call 价差**，长腿 Delta 0.50–0.60，参考 = 现价 100.00，下一步 `/bullcall-sim suggest ZZZQ`。
- **没有 wiki 论点**：退出码 3，不写文件，问你看法；你答"中性"后用 `--view 中性` 重跑 → 中性 × 未持有 × IV 高 → 现金担保 Put 远价外或 Bull Put 远价外（0.10 / 0.15 档）。
- **热门股财报 5 天后**：第一推荐变成"等财报后再看"，第二是 Bull Put 价差，现金担保 Put 进"为什么不选"（`earnings_imminent`）；仍然最多 2 个。

## 数据源（期权数据）

期权工具有三种数据源：`ibkr`（IBKR 模拟盘网关，只读、延迟数据）、`yahoo`（Yahoo Finance 免费行情，数据质量低）、`demo`（离线演示，全部虚构）。`/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim`、`/bullcall-sim`、`/option-review`、`/strategy-pick` 的期权数据（到期日、报价、隐含波动率、Delta）都来自当前数据源；Moving average、财报和除息日一律来自 Yahoo。

**选哪个**，优先级从高到低：环境变量 `OPTION_DATA_SOURCE=ibkr|yahoo|demo` > `data/config.md` 里的一行 `option_data_source: yahoo` > `scripts/option_source_default.py` 的 `DEFAULT_SOURCE`。写错值会报错，不会悄悄换源。每份报告开头写明数据来源和数据质量；非 IBKR 时数据质量标“低”或“演示”，账本和 json 里也带 `data_quality`。演示数据的账本单独放在 `data/paper-trading-demo/`。

Yahoo 和演示模式的买卖价差硬线放宽到 10%（5%–10% 放行但警告），IBKR 模式仍是 5%；休市后 Yahoo 的 bid/ask 常为空，会标“陈旧”。Yahoo 质量、限流、`BRK-B` 写法、演示模式和 `OPTION_DEMO_TODAY` 见 `docs/option-data-sources.md`。

### IBKR 模式

IBKR 模式装依赖、网关设置和端口见 `docs/option-data-sources.md`。

- **设置**在 `data/ibkr.env`（不进 git，只有 IBKR 模式用）：`IBKR_HOST`、`IBKR_PORT`、`IBKR_OPTIONS_CLIENT_ID`（给本工具专用的 client id，别和其他连接重复）、`IBKR_MARKET_DATA_TYPE`（默认 4 = 延迟冻结，盘中也可以设 3 = 延迟）。环境变量优先于文件。不要把这些值写进文档或提交。
- **任何时段都能跑**：休市时网关给延迟冻结的上一交易日数据，输出按快照日标注。
- **行情被抢占（错误 10197）**：同一个 IBKR 账户的实盘会话（手机 App 或 TWS）会抢走行情，模拟网关就拿不到数据；先退出实盘会话（或关闭行情共享）再重跑。
- **数据源连不上**（网络断了、Yahoo 被限流、IBKR 网关中途掉线）：整次运行中止，退出码非 0，不写任何文件，也**不会换别的数据源重试**。恢复后重跑即可。
- IBKR 模式下全市场筛选（`/put-screen suggest`）实测约 6 分钟，请耐心等。

## 看结果、数据在哪

| 要看什么 | 文件 |
|---|---|
| 持仓盈亏、占用资金、盈亏%、两种策略对比 | `data/paper-trading/report.md`（文字）、`report.html`（网页）；每次 `open` / `status` / `close` 后更新 |
| Sell Put 筛选汇总 | `data/paper-trading/put-screen/<日期>.md` |
| Covered Call 筛选汇总 | `data/paper-trading/call-screen/<日期>.md` |
| Collar / 保护性 Put 筛选汇总 | `data/paper-trading/collar-screen/<日期>.md`（和同名 `.json`） |
| Collar / 保护性 Put 逐只候选 | `data/paper-trading/suggestions/<日期>-<TICKER>-collar-real.md` 或 `-collar-lot<ID>.md`（和同名 `.json`，`open` 读的是 `.json`） |
| 铁鹰筛选汇总 | `data/paper-trading/condor-sim/<日期>.md` |
| 铁鹰逐只候选 | `data/paper-trading/suggestions/<日期>-<TICKER>-condor.md`（和同名 `.json`，`open` 读的是 `.json`） |
| Call 借方价差筛选汇总 | `data/paper-trading/bullcall-sim/<日期>.md` |
| Call 借方价差逐只候选 | `data/paper-trading/suggestions/<日期>-<TICKER>-bullcall.md`（和同名 `.json`，`open` 读的是 `.json`） |
| 假设持仓（手动添加或 Put 被指派而来） | `data/paper-trading/paper_shares.json`（**不要手改**，用 `shares` 子命令） |
| 每只候选为什么选这个合约 | `data/paper-trading/suggestions/<日期>-<TICKER>[-put].md` |
| 每周建议和历史 | `data/paper-trading/reviews/<日期>.md` / `.json` |
| 复盘报告（建议准不准、阈值重算） | `data/paper-trading/journal.md` / `journal.json`（`/option-journal` 生成，覆盖写） |
| 你记的笔记 | `data/paper-trading/journal-notes.json`（只有你用 `note` 记了才有） |
| 账本 | `data/paper-trading/spreads.json`（**不要手改**，脚本每次写之前会备份 `.bak`） |

占用资金的口径：价差 = 最大亏损（宽度 − 权利金）× 100 × 张数；Sell Put = 行权价 × 100 × 张数；Covered Call 不占用额外现金（股票已持有，盈亏%以成本 × 股数为分母）；Collar / 保护性 Put 不占用买股票的现金，只有净支出（Put 付出多于 Call 收到的部分）计入占用资金，净收入或零成本不占用；铁鹰 = (单侧宽度 − 净权利金) × 100 × 张数（到期最多一侧亏满）；Call 借方价差 = 净支出 × 100 × 张数（方向错了亏掉全部净支出）。盈亏% = 盈亏 ÷ 占用资金。所有金额按每张合约 ×100、中间价成交、不含手续费；模拟结果会比真实略乐观（真实无法按快照价成交）。

`data/` 整个目录不进 git，不会公开。

## 记住几件事

- 同一个 ticker + 同一个到期日不能重复开仓，**价差、Sell Put、Covered Call 之间也算重复**（同一 ticker 的不同持仓可以各开一张同到期日的 Call）。Collar 也是卖 Call：它和 Covered Call 不能同时覆盖同一批股份，可用张数要扣掉未平仓的 Call；保护性 Put 只需要有股票，可以和一张卖 Call 的策略共存，但不能重复买。
- 买入 Put 的张数不能超过持股数 ÷ 100：同一持仓上未平仓的 Collar 和保护性 Put 合计受这条限制，超了会被拒绝。
- Collar / 保护性 Put 不计入胜率统计，也不在公开页出现；模拟盘只做模拟。
- 铁鹰按整体记一条，不计入 Bull Put 的胜率和 Delta 档位表，也不在公开页出现；短腿硬卡 5%、长翼只警告 15%，与策略页"四条腿都过 5%"不同；大涨对它是满额亏损。
- Call 借方价差是**买方**策略，按整体记一条，不计入 Bull Put 的胜率和 Delta 档位表，也不在公开页出现；两条腿都硬卡 5%；所需胜率是净支出 ÷ 宽度（与卖方口径相反）；方向错了亏掉全部净支出。
- 模拟盘里你用的"愿意持有"这条线，来自 wiki 的结论和档位；Avoid 的会被剔除，Watch 不等于买入信号。
- 想学期权的基础概念和策略，看 `wiki/options/`（知识综合总结、各视频解析）。
