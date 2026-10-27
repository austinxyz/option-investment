# collar-screen

Paper-trade **Collar** 和 **保护性 Put**。对已持有的股票（每 100 股一张），脚本按固定档位给出一份 Put 下限，并选一个让净现金流大致为零的虚值 Call（Collar）；也可以只记保护性 Put（不卖 Call）。用户挑几只记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"保护性 Put 与 Collar 记录"一节看结果。

**Paper trading only.** Never touches `data/positions.md`（只读它来知道你有哪些股票），never places an order, never writes `outputs/`。所有账本与报告文件都在 `data/paper-trading/`（gitignored）。`open` / `status` / `close` 会照常刷新**过滤后**的公开模拟盘页面 `wiki/options/paper-trading.md`（见 `/spread-sim` 文档）；该页**不含**这两个策略，只含价差与现金担保 Put，所以本命令不会把任何 Collar / 保护性 Put 内容写进 `wiki/`。账本、`status`、`close` 与 `/put-screen`、`/call-screen`、`/spread-sim` 共用。

## 持股来源

和 `/call-screen` 用同一张"持股表"，三种来源：

| 来源 | 怎么来的 |
|------|----------|
| 真实持仓 | 每次 `suggest` 现读 `data/positions.md`（**只读**，同一 ticker 跨账户合并）；成本是合并后的加权均价，成本为估算值时会警告 |
| 假设持仓 | 手动加：`./.venv/Scripts/python.exe scripts/call_screen.py shares add TICKER --shares N --cost C`（管理假设持仓复用 `call_screen.py shares`，本命令没有 `shares` 子命令） |
| 被指派的 Put | 账本里已被指派的现金担保 Put，`suggest` 会自动补建一个假设持仓（有效成本 = 行权价 − 权利金） |

每个持仓是独立的单元。**Collar** 的最多张数 = 股数 ÷ 100 取整，再减去已被未平仓 Call（备兑 Call 或 Collar）占用的张数；**保护性 Put** 只需要有股票，所以只扣已被其他未平仓 Put 占用的张数，不扣 Call。不足 100 股的持仓整只剔除；**每个持仓的多头 Put 合计（未平仓的 Collar Put 腿加保护性 Put）不得超过股数 ÷ 100 张**，已用完的持仓整只剔除，理由写"已有的多头 Put 已用完该持仓可对冲的张数"；Call 已被占满的持仓不出 Collar，但仍出保护性 Put 变体并写明原因。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给我的持股做个 collar" / "对冲一下" / "远月的" | `suggest [--dte monthly\|long]` — 默认 monthly；不需要传 ticker，不写账本。用户明确要远月才传 `--dte long` |
| open | "AAA 开 85% 档 collar" / "AAA 只买保护性 put" | `open TICKER {90,85,80} [--lot real\|<ID>] [--contracts N] [--put-only]` — 记录已保存的候选，默认 1 张；同一 ticker 有多个持仓候选时必须用 `--lot` 指定；`--put-only` 只记保护性 Put |
| status | "看一下模拟盘" | `status` |
| close | "提前平仓 #7" | `close ID` |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/collar_screen.py <action> ...
```

## 规则（脚本已实现，转述即可）

- **到期日**：`monthly` = 距快照日 30–60 天内最近的到期日；`long` = 90–180 天内最近的到期日。窗口内没有到期日 → 该持仓剔除，写明"没有 30–60 天（或 90–180 天）内的到期日"。
- **三档下限**：现价的 **90% / 85% / 80%**。每档取**不高于目标价**的最近一个真实行权价；若这个行权价低于目标超过现价的 5 个百分点（链太稀疏）→ 该档剔除，理由"行权价间距过粗，达不到该档"。相邻两档落在同一行权价时只警告"与上一档行权价相同"，不剔除。
- **每条腿的价差规则**：买卖价差 ≤ 该腿自己权利金的 **5%**；Put 超了 → 该档剔除；Call 候选超了会被跳过，一个合格的虚值 Call 都没有 → 该档的 Collar 剔除（保护性 Put 变体不受影响）。报价陈旧（价差未知）的腿放行，但警告"报价陈旧"。
- **零成本 Call 的选法**：所有行权价高于现价的虚值 Call 里，选权利金（中间价）最接近 Put 保费的那一个；并列取行权价更低的。
- **净成本**：净权利金 = Call 收到 − Put 付出（每股）。**正数是净收入，负数是净支出**；"零成本"指**现金流**大致抵平，**不是机会成本为零**——真正的代价是 Call 行权价以上的涨幅被让出，说明文件里写作"上限代价"（现价到 Call 行权价的空间 × 100，每张；涨过上限的部分被让出）。**口径提醒**：筛选说明里的上限代价是**每张、以现价为起点**；之后报告里的上限代价标注"相对参考价（真实持仓为平均成本）"，起点是开仓时的参考价（真实持仓即平均成本），两者数字不同属正常，不要混用。
- **锁定区间**：下限 = Put 行权价；上限 = Call 行权价（保护性 Put 没有上限，写"无上限"）。
- **每档的剔除理由**各自单独写，一档被剔除不影响另外两档。

## 警告（只警告、不排除）

relay every warning the file carries and explain briefly:

- **财报在到期前**：只是**信息**——Collar 常常就是为财报而做的，**不排除**。财报日期未知也会提示。
- **除息日在到期前，且 Call 偏实值**（行权价不高于现价的 103%；这条 103% 规则只属于筛选时的警告）：提前被指派风险高。
- **Call 行权价低于成本**：被行权会亏；成本未知时写"无法判断"。
- **税务提醒**（只针对真实持仓）：涉及的账户里包含应税账户，且持股不满 1 年或买入日未知时，提示"买入 Put 可能重置持有期，将来卖出享受不到长期资本利得税率"。**税务细节策略页的来源没有覆盖，本工具不处理，请先咨询税务专业人士**；工具不会据此排除任何候选。
- 报价陈旧、成本为估算值、真实持仓由多个账户合并而来。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest [--dte monthly|long]`。它先把已被指派的 Put 补建成假设持仓（脚本会打印），再逐只取行情，可能要一会儿，等它结束。读取 `data/positions.md` 失败时报错退出，不会用旧数据；某一只取行情失败只会变成那只的剔除行。
   **Data source:** 期权到期日、报价、Delta 来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`；汇总开头写明来源和数据质量，原样转述）。IBKR 模式是只读延迟数据，休市时是延迟冻结的上一交易日数据，汇总按快照日标注，任何时段都能跑；Yahoo 模式休市后 bid/ask 常为空，建议开盘时间运行；财报和除息日一律来自 Yahoo。IBKR 模式的网关设置在 `data/ibkr.env`（gitignored），never print or commit the values. **数据源连不上整次运行中止，不写任何文件，不会换别的数据源重试**；IBKR 模式下错误 10197 表示同账户的实盘会话在抢行情，请用户先退出实盘会话再重跑。
2. Read the summary `data/paper-trading/collar-screen/<快照日>.md` and show it：每个持仓一行（来源、ticker、可用张数、现价、各档的 Collar / 保护性 Put 方案或剔除理由）。Relay **every warning**. 本工具**不判断该不该做保护**，不推荐哪只、哪一档，由用户自己挑。
3. 每个入选持仓还有逐只说明 `data/paper-trading/suggestions/<快照日>-<TICKER>-collar-real.md`（真实持仓）或 `-collar-lot<ID>.md`（假设持仓）和同名 `.json`，`open` 读的是后者。同一天重跑会先删当天旧的 `-collar` 文件再重写（Put、Call、价差的同名文件不动）。把路径告诉用户，按需用几行概括某个文件，不要改写数字。
4. 把固定风险讲清楚：Collar 的 Call 腿让出行权价以上的全部涨幅；保护性 Put 有净支出，到期保护即结束；保护只在到期日之前有效，不模拟展期。
5. Ask which positions / tiers to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER {90,85,80} [--lot real|<ID>] [--contracts N] [--put-only]`。它**不取行情**，只记录最新已保存候选里那一档的方案，所以记下的就是用户看过的。`--put-only` 记成保护性 Put（忽略 Call 腿）；不加则记成 Collar。
2. 它会拒绝：没有已保存的候选、候选到期日已过、文件损坏、最新一次筛选已把该档（或该变体）剔除、持仓已不存在、张数超过可用张数（Collar 受未平仓 Call 约束；保护性 Put 只受股数约束，且**同一持仓上未平仓的长 Put（Collar 的 Put 加保护性 Put）合计不得超过持股数 ÷ 100，不能超买**）、**重复/覆盖冲突**（同 ticker 同到期日已有别的策略的记录算重复；同一持仓上，保护性 Put 可以和一个卖 Call 的策略共存，但两个卖 Call 的策略不能同时覆盖同一批股份，也不能重复买保护性 Put）。Relay the message，必要时重新 `suggest`，不要绕过。
3. 记录内容：两条腿（行权价、权利金）、净权利金、覆盖股数（张数 × 100）、持仓引用、开仓时每股成本和参考价快照、档位和到期类型。输出行显示"锁定区间"和净权利金（负数 = 净支出），read it back. **股票已持有，不占用买股票的现金**；但**净支出（Put 付出多于 Call 收到）算占用资金**，净收入或零成本则不占用。

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前中间价估值。Collar / 保护性 Put 没有止盈止损线，只显示期权腿的浮盈亏；到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **到期结算**（用到期日收盘价）。结果标签：
   - `called_away`（被行权）：收盘价 ≥ Call 行权价，股票按 Call 行权价卖出；
   - `protected`（下限生效）：收盘价 < Put 行权价，Put 价内；
   - `expired_worthless`（两腿均作废）：落在两个行权价之间，或保护性 Put 价外；
   - `closed`：提前平仓。
3. **盈亏口径**：记账的 `pnl`（也是 headline 盈亏）**只含期权腿**；**股票腿**盈亏单独记录（`stock_leg_pnl`，从参考价算到收盘价，被行权时封顶在 Call 行权价），只显示、不并入 headline。**Collar** 在假设持仓上会把期权腿盈亏折入有效成本（净收入降低成本，净支出抬高成本），被行权的假设持仓减少对应股数；**保护性 Put 不会改动任何假设持仓**；**真实持仓一律不改**，被行权时在报告的结果列（md 与 html）和 `status` 的结算提示里都标注"（真实持仓未变）"。
4. **不计入胜率**：这两个策略没有胜率，不进"按策略对比"的胜率统计，也**不显示在公开页** `wiki/options/paper-trading.md`；它们在私有报告里有单独的"保护性 Put 与 Collar 记录"一节（笔数、按结果分类、期权腿总盈亏、总净成本、净成本、上限代价）。
5. Summarise: per trade 期权腿盈亏、锁定区间、距离两个行权价多远，以及任何结算（说明是期权腿还是含股票腿）。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前中间价平掉各条腿，记账（结果标签 `closed`）。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

- `/option-review` 对 Collar / 保护性 Put 只给**继续持有**或**需要关注**，**不会建议"止盈平仓"**：Call 腿变价内（现价 ≥ Call 行权价，可能被行权）、除息日临近且 Call 已价内（现价 ≥ Call 行权价；Call 偏实值但未价内不触发，103% 规则只属于本命令的筛选警告）、取不到某条腿的报价 → 需要关注；Put 价内是保护在起作用，只是信息，不是警告；临近到期会提醒保护即将结束。
- `/option-journal` 在「保护 / 领口」一块单独统计这两个策略（笔数、各结果笔数、期权腿总盈亏、总净成本），**不并入**平仓建议 / 关注 / 持有 / 连赢统计和阈值重算，也没有胜率。
- 两者读同一个账本，不属于 `/collar-screen` 本身。

## Rules

- Never edit `data/paper-trading/spreads.json` or `paper_shares.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，也不把这两个策略写进 `wiki/`（只会刷新过滤后的公开页）。
- 不判断该不该保护，不推荐档位；税务不处理（见上）；不模拟提前行权、展期、续保。
- 零成本只是现金流，不是机会成本；上限代价（涨过 Call 行权价的部分被让出）要讲。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees; 结果比真实略乐观（真实无法按快照价成交）。
- 同一持仓同 ticker 同到期日的覆盖规则见 open 一节；跨策略算重复。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
