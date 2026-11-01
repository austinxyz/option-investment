# rr-sim

Paper-trade **风险反转（Risk Reversal）**：**卖出一个虚值 Put、同时买入一个虚值 Call**，同一到期日，净收入或净支出都有可能。用户给一个 ticker，脚本按**短 Put 的 |Delta|** 出三档候选（约 0.30 / 0.20 / 0.15），再配一个中间价最接近的虚值 Call；用户挑一档记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"风险反转记录"一节看结果。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes `outputs/`。账本和报告都在 `data/paper-trading/`（gitignored），和 `/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim`、`/bullcall-sim` 共用同一个账本。`open` / `status` / `close` 会照常刷新**过滤后**的公开页 `wiki/options/paper-trading.md`；该页**不含风险反转**，所以本命令不会把任何这类内容写进 `wiki/`。**`/strategy-pick` 也不会推荐它**（它的决策树里没有风险反转，输出逐字节不变）；想试就自己点 ticker 用本命令。

**先讲清楚风险（这是本工具最重要的一段）：** 下行风险**接近裸卖 Put**。股价跌破 Put 行权价会被指派，**亏损一直算到股价归零**，**不是封顶**；最大收益**无上限**（Call 一侧）。两个行权价之间是**死区**，两条腿都作废，若 Call 是净支出，这笔钱白付。**只适合真的愿意按 Put 行权价接股的标的。**

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给 ZZZQ 出个风险反转" / "放宽到 14 天" | `suggest TICKER [--min-days N]` — 不写账本 |
| open | "ZZZQ 开 0.20 档" / "开 2 张" | `open TICKER {0.30,0.20,0.15} [--contracts N]` — 记录已保存的候选，默认 1 张 |
| status | "看一下模拟盘" | `status`（与 `/spread-sim` 共用 `spread_sim.py`） |
| close | "提前平仓 #7" | `close ID`（同上） |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/rr_sim.py suggest TICKER [--min-days N]
./.venv/Scripts/python.exe scripts/rr_sim.py open TICKER {0.30,0.20,0.15} [--contracts N]
./.venv/Scripts/python.exe scripts/rr_sim.py status
./.venv/Scripts/python.exe scripts/rr_sim.py close ID
```

`--min-days N` 放宽到期日下限（N 必须 ≥ 7，小于 7 会被拒绝）。没有 `--width`：风险反转没有"宽度"。

## 规则（脚本已实现，转述即可）

- **三档按短 Put 的 |Delta|：0.30 / 0.20 / 0.15**（策略页第 3 节：0.15–0.30 是常见区间，标推导），各取 Delta 最接近目标的**虚值** Put（行权价低于现价）；实际 Delta 偏离目标超过 0.05 → 该档剔除，每档各自判断。Delta 取券商值，取不到时用 Black-Scholes 估算。
- **到期日**：距快照日 **30 至 60 天内最近的一个**；窗口内没有 → 三档都剔除，理由写"没有 30–60 天内的到期日"。`--min-days N`（N ≥ 7）可放宽下限。
- **长 Call**：在行权价**高于现价**的虚值 Call 里，选**中间价最接近短 Put 中间价**的那一个（净值最接近 0），并列取行权价更低的。**不要求**必须零成本：选出来是净收入就是净收入，是净支出就是净支出。找不到合格的虚值 Call → 该档剔除。
- **两条腿都硬卡 5%**：短 Put 和长 Call 各自的买卖价差都必须 ≤ 该腿中间价的 5%（恰好 5% 通过），否则该档剔除。报价陈旧只警告，不排除。
- **净权利金** = 短 Put 中间价 − 长 Call 中间价（每股）。**正 = 净收入，负 = 净支出**，报告和说明文件里都标注。
- **占用资金（保守口径）** = (Put 行权价 − 净权利金) × 100 × 张数，即**现金担保口径**；每份候选、报告、HTML 都标注"现金担保口径，券商实际保证金因家而异"。**最大亏损**同样是这个数字（股价归零），**最大收益显示"无上限"**，不显示数字。
- **下行盈亏平衡价** = Put 行权价 − 净权利金。**死区** = Put 行权价 ～ Call 行权价。Call 是净支出时另显示**上方回本价** = Call 行权价 + 净支出的绝对值；净收入则显示"Call 行权价以下已不亏"。
- **没有"所需胜率"**：无封顶的结构没有"净支出 ÷ 宽度"这个口径，报告里不造这个数。
- **不模拟股票腿**：被指派（到期价低于 Put 行权价）时，账本只记**期权腿**的盈亏；真实情况下会按 Put 行权价接股，本工具不模拟，也不碰纸上持股。

## 警告

relay every warning the file carries and explain briefly。以下三条**每份候选都带**，原样转述，不要淡化：

- **`WARN_RR_DOWNSIDE`**：「下行风险接近裸卖 Put：股价跌破 Put 行权价会被指派，亏损一直算到归零；占用资金按现金担保口径显示（保守），券商实际保证金因家而异」。
- **`WARN_RR_DEAD_ZONE`**：「两个行权价之间两腿都作废；若 Call 是净支出，这笔钱白付」。
- **`WARN_RR_WILLING`**：「只适合真的愿意按 Put 行权价接股的标的」。

只警告、不排除：**净支出偏大**（|净支出| > 短 Put 中间价的 25% → 「Call 太贵，净支出偏大」）、到期前有财报（或财报日期未知）、除息日在到期前（短 Put 价值受股息影响）、报价陈旧。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest TICKER [--min-days N]`。
   **Data source:** 期权到期日、报价、Delta 来自 IBKR 模拟网关（只读、延迟数据；休市时是延迟冻结的上一交易日数据，按快照日标注），财报和除息日来自 Yahoo。网关设置在 `data/ibkr.env`（gitignored），never print or commit the values。**网关连不上整次运行中止，不写任何文件，没有 Yahoo 回退。**
2. 它写两类文件（**不写账本**）：
   - 说明和机器可读候选：`data/paper-trading/suggestions/<快照日>-<TICKER>-rr.md` 与同名 `.json`（`open` 读的是 `.json`）。同一天重跑会覆盖当天同 ticker 的旧文件；文件名以小写 `-rr` 结尾，其他策略的加载器不会误读。
   - 当天汇总：`data/paper-trading/rr-sim/<快照日>.md`。三档都被剔除时文件照写（写明原因），退出码 0。
3. Read the summary and the per-ticker file and show them：三档各自的短 Put / 长 Call 行权价、实际 Delta、两腿中间价、净权利金（标注净收入 / 净支出）、占用资金（保守口径）、下行盈亏平衡价、死区、下行 / 上行幅度，或被剔除的理由。Relay **every warning**。本工具**不判断哪一档好**，不推荐，由用户自己挑。
4. 把固定风险讲清楚：下行接近裸卖 Put、亏损算到归零；占用资金是保守口径；最大收益无上限但有死区；只适合愿意接股的标的。
5. Ask which bucket to open, or none. Nothing is written to the ledger until `open`.

示例（虚构 ZZZQ，现价 100，用设计文档第 8 节的数字）：0.20 档卖 90 Put（中间价 2.00）、买 110 Call（中间价 1.50），净权利金 +0.50/股（净收入）；占用资金 (90 − 0.50) × 100 = $8,950（保守口径）；下行盈亏平衡 89.50；死区 90 ～ 110；下行幅度 −10.00%，上行幅度 +10.00%。

### 2. open

1. Run `open TICKER {0.30,0.20,0.15} [--contracts N]`。它**不取行情、不需要网关**，只记录最新已保存候选里那一档，所以记下的就是用户看过的。
2. 它会拒绝：没有该 ticker 的已保存候选、候选文件损坏、候选到期日已过、该档在最新一次 suggest 里被剔除、候选数字对不上（舍入容差 1e-4）、与账本里同 ticker 同到期日的记录重复（任意策略）。Relay the message，必要时重新 `suggest`，不要绕过。
3. **一笔风险反转只记一条整体记录**（两个行权价、两腿中间价、净权利金、下行盈亏平衡价、上下行幅度、Delta 与 IV），不拆成两条单腿；记录里**没有**其他策略的字段（`credit`、`width`、`debit`、`lot_ref` 等）。它**不占用备兑额度、不碰纸上持股、不写 `data/positions.md`**。输出行显示净权利金、占用资金和下行盈亏平衡价，read it back.

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前两腿中间价估值，每股平仓净成本 = 短 Put 当前中间价 − 长 Call 当前中间价（**不钳位**，可以为负）；浮盈 = (净权利金 − 平仓净成本) × 100 × 张数。**任一条腿取不到有效报价 → 该笔标"取不到报价"，不猜价，`close` 也拒绝。** 到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **提示线（只提示，不平仓）**：浮盈 ≥ 开仓时短 Put 中间价 × 100 × 张数（1 倍，含等号）→ 止盈线；浮亏 ≥ 2 倍同一数字（含等号，同 Sell Put 的止损口径）→ 止损线。由脚本单独计算，转述它给的文案即可。
3. **到期结算**（用到期日收盘价 C）：每股期权腿合计 = max(C − Call 行权价, 0) − max(Put 行权价 − C, 0)；盈亏 = (合计 + 净权利金) × 100 × 张数；恰好为 0 记**亏损**。**结局区**只在到期结算时写：`up`（C > Call 行权价）、`down`（C < Put 行权价）、`dead`（其间含两个行权价）；提前平仓不写，显示"—"。**`down` 时的提示：「Put 价内，真实情况下会按行权价接股；本工具不模拟股票腿」。** 转述时务必带上这句。
4. **统计口径**：风险反转在"按策略对比"里有**自己的一行**，另有一节"风险反转记录"（每笔的腿，如 `90 Put 卖 / 110 Call 买`、净权利金标注净收入 / 净支出、占用资金（保守口径）、下行盈亏平衡价、浮盈亏或结算结果、结局区）。**它不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 各块。少于 30 笔不下结论。**
5. **不在公开页**：`wiki/options/paper-trading.md` 逐字节不含任何风险反转内容（ticker、行权价、金额都不出现）；有无风险反转记录，公开页文本逐字节相同。
6. Summarise: per trade 浮盈亏、现价距 Put 行权价多远、是否碰到止盈 / 止损提示，以及任何结算。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前两腿中间价平掉（平仓净成本不钳位，结果按盈亏记 `win` / `loss`，状态 `closed_early`，不写结局区）。任一腿无报价或已过到期日则拒绝。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

`/option-review` 的风险反转规则（规则编号对应报告里的"规则"列，第一个命中的算）：

- 0：任一腿取不到报价 → 需要关注。
- 1：到期日当天或已过 → 等待结算。
- 2：**短 Put 价内（现价严格低于 Put 行权价）**或已到止损线 → 需要关注，理由写"可能被指派（按 Put 行权价接股）；由你决定接受还是买回"。
- 3：已到止盈线 → 平仓。
- 4：以下任一 → 需要关注：到期前有财报；短 Put |Delta| ≥ 0.35（仅数据可信时）。
- 5：其他 → 继续持有。

阈值用现有常量，不新增。只是建议，永远不替用户平仓。

`/option-journal` 在单独的"风险反转（单独统计，不计入 A–D 与阈值重算）"一块统计：已结算笔数、赢笔数、胜率、期权腿总盈亏、三种结局（up / dead / down）各几笔、下行（被指派）笔的平均期权腿亏损；样本不足 30 笔会提示。顶部"已结算 N 笔"不含它，另起一行写。给它记的笔记只存进笔记文件，报告里不显示。两者读同一个账本，不属于 `/rr-sim` 本身。

## 工具不记录的东西

**不模拟股票腿**（被指派后的接股、成本、之后的备兑 Call）；不模拟提前行权、调整、展期、单腿平仓；也不记调整本身。平仓时的两腿 IV 只在拿得到时才写。要验证这些，需要自己另外记。

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，不把风险反转写进 `wiki/`（只刷新过滤后的公开页）；`/strategy-pick` 不推荐。
- 不判断该不该做，不推荐档位；不模拟股票腿、提前行权、调整、展期、单腿平仓。
- 下行接近裸卖 Put 要讲；两条腿都硬卡 5% 的流动性规则、30–60 天到期、占用资金的保守口径、三条固定提示（`WARN_RR_DOWNSIDE` / `WARN_RR_DEAD_ZONE` / `WARN_RR_WILLING`）都要原样转述。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees（两条腿的手续费不可忽略）；结果比真实略乐观（真实无法按快照价成交）。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
