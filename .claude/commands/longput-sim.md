# longput-sim

Paper-trade **买入 Put（Long Put）**：单腿买一张 Put，**净付钱**，最直接的看跌押注（**看跌策略**），也是三个看跌工具里 Theta 抽水最重的一个。用户给一个 ticker，脚本按 Put 的 |Delta| 出三档候选（约 0.50 / 0.40 / 0.30），用户挑一档记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"买入 Put（Long Put，看跌）记录"一节看结果。工具回答的问题是：对某只票买一张 Put，要跌多少才回本、到期实际胜率多少、亏的时候平均亏多少。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes `outputs/`。账本和报告都在 `data/paper-trading/`（gitignored），和 `/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim`、`/bullcall-sim`、`/rr-sim`、`/bearcall-sim`、`/bearput-sim` 共用同一个账本。`open` / `status` / `close` 会照常刷新**过滤后**的公开页 `wiki/options/paper-trading.md`；该页**不含买入 Put**，所以本命令不会把任何这类内容写进 `wiki/`。**`/strategy-pick` 不会推荐它**（买入 Put 单腿在它那里永不推荐，有没有本命令，它的输出逐字节不变；看法为不看好时它推荐的是两个看跌价差，见 `/bearput-sim`、`/bearcall-sim`）；只能手动使用：自己给 ticker 用本命令。

**先讲清楚风险：** 买方单腿：每天 Theta 抽水，**到期前不跌过盈亏平衡价就亏光全部权利金**；最大亏损 = 权利金（净支出）。历史上市场整体向上漂移，看跌样本里亏的概率偏高。没有宽度，也**没有"所需胜率"**（收益没有价差式的上限），看的是平均赢额与平均亏额之比、以及回本需要下跌多少。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给 ZZZQ 出个买入 Put" / "放宽到 14 天" | `suggest TICKER [--min-days N]` — 不写账本 |
| open | "ZZZQ 开 0.30 档" / "开 2 张" | `open TICKER {0.50,0.40,0.30} [--contracts N]` — 记录已保存的候选，默认 1 张 |
| status | "看一下模拟盘" | `status`（与 `/spread-sim` 共用 `spread_sim.py`） |
| close | "提前平仓 #7" | `close ID`（同上） |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/longput_sim.py suggest TICKER [--min-days N]
./.venv/Scripts/python.exe scripts/longput_sim.py open TICKER {0.50,0.40,0.30} [--contracts N]
./.venv/Scripts/python.exe scripts/longput_sim.py status
./.venv/Scripts/python.exe scripts/longput_sim.py close ID
```

`--min-days N` 放宽到期日下限（N 必须 ≥ 7，小于 7 会被拒绝）。没有 `--width`：单腿没有宽度。

## 规则（脚本已实现，转述即可）

- **到期日**：距快照日**至少 30 天**的第一个到期日（买方，时间衰减对买方不利）。`--min-days N`（N ≥ 7）可放宽；所选到期日不足 30 天时**警告**"净买方，时间不在你这边"（不排除）。
- **三档按 Put 的 |Delta|：0.50 / 0.40 / 0.30**，各取 |Delta| 最接近目标的 Put；实际 Delta 偏离目标超过 0.05 → 该档剔除，每档各自判断；Delta 或隐含波动率缺失的行权价不参与选择。**为什么是这三档**：0.50 是平值（最贵、回本所需跌幅最小），0.30 是温和虚值（便宜、但要跌得更多），0.40 居中；**不做**深虚值（大概率归零，样本难解读）。平值 Put 不在默认窗口时，脚本会按行权价显式去取；这一步取数出错就整次中止，不会悄悄换成更差的行权价。
- **流动性**：这条腿的买卖价差必须 ≤ 中间价的 **5%**（恰好 5% 通过），否则该档剔除。报价陈旧只警告，不排除。
- **结构有效性**：净支出 = Put 中间价（每股，4 位小数），必须大于 0 且小于行权价，否则该档剔除；`open` 对已保存但数字对不上（文件被改坏）的候选也会拒绝，舍入容差 1e-4。行权价恰等于现价的候选正常（回本幅度 = −净支出 ÷ 现价）。
- **钱怎么算**（每张）：**最大亏损 = 占用资金 = 净支出 × 100**（总账 `book.md` 里买入 Put 的占用资金同样是净支出）；盈亏平衡价 = 行权价 − 净支出；回本幅度 = 盈亏平衡价 ÷ 现价 − 1（为负）；**最大收益（仅展示）** = (行权价 − 净支出) × 100（标的归零），不用于任何阈值。候选 JSON 与账本里存**小数**（−0.074），报告、说明文件显示**百分数**（−7.40%）。

示例（虚构 ZZZQ，现价 100，Long Put 设计第 3 节的手算数字）：买 95 Put（中间价 2.40），1 张：净支出 2.40/股，最大亏损 $240，盈亏平衡 92.60，回本幅度 −7.40%，最大收益（展示）$9,260。

## 警告（只警告、不排除）

relay every warning the file carries and explain briefly。以下一条**每份候选都带**，原样转述，不要淡化：

- **`WARN_THETA_DECAY`**：「买方单腿：每天 Theta 抽水，到期前不跌过盈亏平衡价就亏光全部权利金；历史上市场整体向上漂移，看跌样本里亏的概率偏高」。

其余只在命中时出现：**财报在到期前**（财报后隐含波动率回落，跳空向上照样亏掉全部净支出，按分享页规则建议跳过；财报日期未知也会提示）；**不足 30 天**（见上）；报价陈旧；Delta 由隐含波动率估算。买入的 Put 不会被指派，没有除息日警告。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest TICKER [--min-days N]`。
   **Data source:** 期权到期日、报价、Delta 来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`；汇总开头写明来源和数据质量，原样转述），财报来自 Yahoo。IBKR 模式是只读延迟数据，休市时是延迟冻结的上一交易日数据，按快照日标注，网关设置在 `data/ibkr.env`（gitignored），never print or commit the values。**数据源连不上（或取数出错）整次运行中止、非 0 退出，不写任何文件，不会换别的数据源重试。**
2. 它写两类文件（**不写账本**），先写说明、JSON 最后落地：
   - 说明和机器可读候选：`data/paper-trading/suggestions/<快照日>-<TICKER>-longput.md` 与同名 `.json`（`open` 读的是 `.json`）。同一天重跑会覆盖当天同 ticker 的旧文件；文件名以小写 `-longput` 结尾，其他策略的加载器不会误读。
   - 当天汇总：`data/paper-trading/longput-sim/<快照日>.md`。三档都被剔除时文件照写（写明原因），退出码 0。
3. Read the summary and the per-ticker file and show them：三档各自的行权价、实际 Delta、中间价、净支出、盈亏平衡价、回本幅度、最大亏损、最大收益（展示），或被剔除的理由。Relay **every warning**。本工具**不判断哪一档好**，不推荐，由用户自己挑。
4. 把固定风险讲清楚：Theta 每天抽水；到期前不跌过盈亏平衡价就亏光权利金；市场长期向上漂移对看跌不利。
5. Ask which bucket to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER {0.50,0.40,0.30} [--contracts N]`。它**不取行情、不需要数据源**，只记录最新已保存候选里那一档，所以记下的就是用户看过的。
2. 它会拒绝：没有该 ticker 的已保存候选、候选文件损坏、候选到期日已过、该档在最新一次 suggest 里被剔除、数字对不上（净支出不在 0 到行权价之间、净支出不等于中间价、盈亏平衡价不等于行权价 − 净支出）、与账本里同 ticker 同到期日的记录重复（任意策略）。Relay the message，必要时重新 `suggest`，不要绕过。
3. **一笔买入 Put 只记一条记录**，只有单腿字段（`put_strike`、`put_mid`、`debit`、`breakeven`、`breakeven_move_pct`、Delta 与 IV），**没有**任何价差或卖方字段（`credit`、`short_strike`、`long_strike`、`width`）。输出行显示净支出、盈亏平衡价和最大亏损 / 占用资金，read it back.

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前中间价估值，每股价值 = 当前中间价；浮盈 = (价值 − 净支出) × 100 × 张数。**取不到有效报价 → 该笔标"取不到报价"，不猜价，`close` 也拒绝。** 到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **提示线（只提示，不平仓）**：浮盈 ≥ 净支出的 **100%**（含等号）→ 止盈线；浮亏 ≥ 净支出的 **50%**（含等号）→ 止损线。示例：上面那笔止盈线 +$240，止损线 −$120。
3. **到期结算**（用到期日收盘价 C）：每股价值 = max(行权价 − C, 0)（**不设上限**）；盈亏 = (价值 − 净支出) × 100 × 张数。示例：C 在行权价 95 或以上 → 价值 0（−$240）；C 恰等于盈亏平衡价 92.60 → 盈亏 $0，**记为亏损**（`loss`）；C = 90 → +$260；C = 80 → +$1,260。
4. **净支出倍数**（`fraction_of_debit`）= 盈亏 ÷ (净支出 × 100 × 张数)，4 位小数，可以为负（上例 C = 90 → 1.0833，C ≥ 95 → −1.0）。提前平仓和到期结算都写。
5. **统计口径**：买入 Put 在"按策略对比"里有**自己的一行**，另有一节"买入 Put（Long Put，看跌）记录"（每笔的行权价、净支出、盈亏平衡价、回本幅度、最大亏损、浮盈亏或结算结果）。**没有"所需胜率"**，改看平均赢额与平均亏额。**它不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 各块和阈值重算。少于 30 笔不下结论。**
6. **不在公开页**：`wiki/options/paper-trading.md` 逐字节不含任何买入 Put 内容（ticker、行权价、金额都不出现）；有无这类记录，公开页文本逐字节相同。
7. Summarise: per trade 浮盈亏、现价距行权价多远、是否碰到止盈 / 止损提示，以及任何结算。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前中间价平掉（结果按盈亏记 `win` / `loss`，状态 `closed_early`）。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

买方复核规则（`/option-review`，规则编号对应报告里的"规则"列，第一个命中的算）：

- 0：取不到报价 → 需要关注。
- 1：到期日当天或已过 → 等待结算。
- 2：浮亏已达净支出的 50%（止损提示线）→ 需要关注：平仓止损，或承担最大亏损（全部权利金）。
- 3：浮盈 ≥ 净支出的 100%（止盈提示线）→ 平仓。
- 4：以下任一 → 需要关注：到期前有财报（IV 压缩与事件风险）；剩余 ≤ 10 天且浮盈 ≤ 0（时间止损）；剩余 ≤ 5 天且现价仍在行权价或以上（价外，到期前主动处理）。
- 5：其他 → 继续持有。

**不用 Delta 规则**，也没有除息日规则（买方不会被指派）。只是建议，永远不替用户平仓。

`/option-journal` 在单独的"买入 Put（看跌，单独统计，不计入 A–D 与阈值重算）"一块统计：已结算笔数、赢笔数、胜率、平均赢额、平均亏额、总盈亏；样本不足 30 笔会提示。示例：三笔 +$260 / +$1,260 / −$240 → 胜率 66.7%，平均赢额 $760，平均亏额 $240，总盈亏 +$1,280。顶部"已结算 N 笔"不含它，另起一行写。给它记的笔记只存进笔记文件，报告里不显示。两者读同一个账本，不属于 `/longput-sim` 本身。

## 工具不记录的东西

不模拟提前行权、调整、展期、滚动到下一个到期日；也不记调整本身。平仓时的 IV 只在拿得到时才写。要验证这些，需要自己另外记。

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，不把买入 Put 写进 `wiki/`（只刷新过滤后的公开页）；`/strategy-pick` 不推荐，只能手动使用。
- 不判断该不该做，不推荐档位；不模拟提前行权、调整、展期。
- Theta 抽水、到期前不跌过盈亏平衡价就亏光权利金，要讲；5% 的流动性规则、30 天默认到期、`WARN_THETA_DECAY` 都要原样转述。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees；结果比真实略乐观（真实无法按快照价成交）。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
