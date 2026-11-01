# bearput-sim

Paper-trade **Put 借方价差（Bear Put Debit Spread）**：买入一个较高行权价的 Put、同时卖出低一个宽度的同到期 Put，**净付钱**，赌股价跌到短腿行权价附近（**看跌策略**）。用户给一个 ticker，脚本按**长腿** Put 的 |Delta| 出三档候选（约 0.60 / 0.50 / 0.40），用户挑一档记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"Put 借方价差（Bear Put Debit Spread，看跌）记录"一节看结果。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes `outputs/`。账本和报告都在 `data/paper-trading/`（gitignored），和 `/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim`、`/bullcall-sim`、`/rr-sim`、`/bearcall-sim`、`/longput-sim` 共用同一个账本。`open` / `status` / `close` 会照常刷新**过滤后**的公开页 `wiki/options/paper-trading.md`；该页**不含 Put 借方价差**，所以本命令不会把任何这类内容写进 `wiki/`。**`/strategy-pick` 在看法为不看好时推荐它**（标签「Bear Put 价差」：未持有（或单账户不足 100 股）时排第一；持股 ≥ 100 股时排第二，在"考虑减仓"之后），`open TICKER --from-pick` 可以直接按它的 Delta 目标选档；也可以手动使用：自己给 ticker 用本命令。`/strategy-pick` 只读它的推荐文件，不 import 本命令。

**先讲清楚风险：** 这是买方策略，方向是看跌。**方向错了亏掉全部净支出**（到期股价在长腿行权价或以上就是满额亏损）；所需胜率 = 净支出 ÷ 宽度，通常低于一半，这是正常的，靠赔率赚钱，不是靠高胜率。历史上市场整体向上漂移，看跌样本里亏的概率偏高。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给 ZZZQ 出个 Put 借方价差" / "宽度 10 的" / "放宽到 14 天" | `suggest TICKER [--width W] [--min-days N]` — 不写账本 |
| open | "ZZZQ 开 0.50 档" / "开 2 张" | `open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]` — 记录已保存的候选，默认 1 张 |
| open（按推荐） | "按 /strategy-pick 的推荐开 ZZZQ" | `open TICKER --from-pick [--contracts N] [--width W]` — 档位改为读今天 `/strategy-pick TICKER` 的 Bear Put 价差推荐（Delta 目标就近放进档位，与报告里 `open` 那行同一规则），控制台先打印 `按 /strategy-pick <日期> 的结果：Delta 目标 … → 档位 …`；档位与 `--from-pick` 二选一。只读**今天**的 `data/strategy-picks/<今天>-<TICKER>.json`（demo 数据源读其下 `demo/`）：今天没跑过、文件是旧的、格式不对、推荐里没有 Bear Put 价差，都直接拒绝（退出码 2，什么都不写），不会回落到更早的结果；先重跑 `/strategy-pick TICKER`。其余校验（已保存候选、宽度核对）不变 |
| status | "看一下模拟盘" | `status`（与 `/spread-sim` 共用 `spread_sim.py`） |
| close | "提前平仓 #7" | `close ID`（同上） |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/bearput_sim.py suggest TICKER [--width W] [--min-days N]
./.venv/Scripts/python.exe scripts/bearput_sim.py open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/bearput_sim.py open TICKER --from-pick [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/bearput_sim.py status
./.venv/Scripts/python.exe scripts/bearput_sim.py close ID
```

`--width` 是长腿到短腿的目标宽度；不传则自动推荐（用 Put 链估算，链太薄时用默认值，说明文件会写明宽度来源）。`--min-days N` 放宽到期日下限（N 必须 ≥ 7，小于 7 会被拒绝）。

## 规则（脚本已实现，转述即可）

- **到期日**：距快照日**至少 30 天**的第一个到期日（买方，时间衰减对买方不利）。`--min-days N`（N ≥ 7）可放宽；所选到期日不足 30 天时**警告**"净买方，时间不在你这边"（不排除）。
- **三档按长腿 Put 的 |Delta|：0.60 / 0.50 / 0.40**，各取 |Delta| 最接近目标的 Put（长腿可以是实值）；实际 Delta 偏离目标超过 0.05 → 该档剔除，每档各自判断。**为什么是这三档**：与 `/bullcall-sim` 镜像，"长腿实值 + 短腿虚值"（高概率）对应 0.60，"长腿平值"（最常用）对应 0.50，"两腿都虚值"（赔率博弈）只取温和的 0.40；**不做**深虚值。三档是名义目标，实际长腿 / 短腿 Delta 另存。实值 / 平值长腿不在默认窗口时，脚本会按行权价显式去取，不会因为窗口没覆盖就选到更差的行权价。
- **宽度与等宽短腿**：短腿**必须恰好**在"长腿行权价 − 宽度"的挂牌行权价上，找不到或没有有效报价 → 该档剔除（"下方没有等宽的短腿"），**不凑近似宽度**。宽度越宽，净支出越大、最大收益越大；这里不判断宽度好坏，由用户自己定。
- **两条腿都硬卡 5%**：长腿和短腿各自的买卖价差都必须 ≤ 该腿中间价的 **5%**（恰好 5% 通过），否则该档剔除（同 `/bullcall-sim`）。报价陈旧只警告，不排除。
- **结构有效性**：长腿 > 短腿且同一到期日；净支出 = 长腿中间价 − 短腿中间价（每股，4 位小数），必须大于 0 且小于宽度，否则该档剔除（"净支出无效"）；盈亏平衡价与长腿、净支出对得上。`open` 对已保存但结构无效（文件被改坏）的候选也会拒绝，舍入容差 1e-4。
- **钱怎么算**（每张）：**最大亏损 = 占用资金 = 净支出 × 100**（= 全部净支出）；最大收益 = (宽度 − 净支出) × 100；盈亏平衡价 = 长腿行权价 − 净支出；**所需胜率 = 净支出 ÷ 宽度**（买方口径，和卖方的"1 − 权利金 ÷ 宽度"相反）；回本幅度 = 盈亏平衡价 ÷ 现价 − 1（为负）；满额幅度 = 短腿行权价 ÷ 现价 − 1（跌到短腿就赚满）。候选 JSON 与账本里存**小数**（0.30、−0.03），报告、说明文件显示**百分数**（30%、−3.00%），转述时别把 −0.03 当成 −0.03%。

示例（虚构 ZZZQ，现价 100，设计文档第 8 节的手算数字）：买 100 Put（中间价 4.80）、卖 90 Put（中间价 1.80），宽度 10，1 张：净支出 3.00/股，最大亏损 $300，最大收益 $700，盈亏平衡 97.00，所需胜率 30%，回本幅度 −3.00%，满额幅度 −10.00%。

## 警告（只警告、不排除）

relay every warning the file carries and explain briefly。以下两条**每份候选都带**，原样转述，不要淡化：

- **`WARN_FULL_DEBIT_BEARPUT`**：「方向错了亏掉全部净支出：到期股价在长腿行权价或以上就是满额亏损；所需胜率低于一半是正常的，靠赔率赚钱」。
- **`WARN_DRIFT`**：「历史上市场整体向上漂移，看跌样本里亏的概率偏高」。

其余只在命中时出现：**财报在到期前**（财报后隐含波动率回落，跳空向上照样亏掉全部净支出，按分享页规则建议跳过；财报日期未知也会提示）；**不足 30 天**（见上）；报价陈旧；Delta 由隐含波动率估算。买入的 Put 不会被指派，所以没有 Bull Call 那条"除息日提前指派"的警告。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest TICKER [--width W] [--min-days N]`。
   **Data source:** 期权到期日、报价、Delta 来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`；汇总开头写明来源和数据质量，原样转述），财报来自 Yahoo。IBKR 模式是只读延迟数据，休市时是延迟冻结的上一交易日数据，按快照日标注，网关设置在 `data/ibkr.env`（gitignored），never print or commit the values。**数据源连不上（或取数出错）整次运行中止、非 0 退出，不写任何文件，不会换别的数据源重试。**
2. 它写两类文件（**不写账本**），先写说明、JSON 最后落地：
   - 说明和机器可读候选：`data/paper-trading/suggestions/<快照日>-<TICKER>-bearput.md` 与同名 `.json`（`open` 读的是 `.json`）。同一天重跑会覆盖当天同 ticker 的旧文件；文件名以小写 `-bearput` 结尾，其他策略的加载器不会误读。
   - 当天汇总：`data/paper-trading/bearput-sim/<快照日>.md`。三档都被剔除时文件照写（写明原因），退出码 0。
3. Read the summary and the per-ticker file and show them：三档各自的长腿 / 短腿行权价、实际 Delta、两腿中间价、净支出、盈亏平衡价、回本幅度与满额幅度、最大收益 / 最大亏损、所需胜率，或被剔除的理由。Relay **every warning**。本工具**不判断哪一档好**，不推荐，由用户自己挑。
4. 把固定风险讲清楚：方向错了亏掉全部净支出；所需胜率低于一半是正常的；到期前主要靠方向和时间；市场长期向上漂移对看跌不利。
5. Ask which bucket to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]`。它**不取行情、不需要数据源**，只记录最新已保存候选里那一档，所以记下的就是用户看过的。
2. 它会拒绝：没有该 ticker 的已保存候选、候选文件损坏、候选到期日已过、该档在最新一次 suggest 里被剔除、结构无效（长 ≤ 短、腿距与宽度不符、净支出不在 0 到宽度之间、净支出与两腿中间价之差或盈亏平衡价对不上）、`--width` 与已保存候选的宽度不一致（要重新 `suggest --width`）、与账本里同 ticker 同到期日的记录重复（任意策略）。Relay the message，必要时重新 `suggest`，不要绕过。
3. **一笔 Put 借方价差只记一条整体记录**，用显式的买方字段（`long_put_strike`、`short_put_strike`、`width`、两腿中间价、`debit`、`breakeven`、回本 / 满额幅度、两腿 Delta 与 IV），**没有**卖方字段（`credit`、`short_strike`、`long_strike`）。输出行显示净支出、盈亏平衡价、最大收益、最大亏损 / 占用资金和所需胜率，read it back.

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前两腿中间价估值，每股价值 = clamp(长腿中间价 − 短腿中间价, 0, 宽度)；浮盈 = (价值 − 净支出) × 100 × 张数。**任一条腿取不到有效报价 → 该笔标"取不到报价"，不猜价，`close` 也拒绝。** 到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **提示线（只提示，不平仓）**：浮盈 ≥ 最大收益的 **50%**（含等号）→ 止盈线；浮亏 ≥ 净支出的 **50%**（含等号）→ 止损线。这与卖方的线不同，由脚本单独计算。示例：上面那笔止盈线 +$350，止损线 −$150。
3. **到期结算**（用到期日收盘价 C）：每股价值 = clamp(长腿行权价 − C, 0, 宽度)；盈亏 = (价值 − 净支出) × 100 × 张数。示例：C 在长腿 100 或以上 → 价值 0（满额亏损 −$300）；C 恰等于盈亏平衡价 97 → 盈亏 $0，**记为亏损**（`loss`）；C = 95 → +$200；C 在短腿 90 或以下 → 价值钳到宽度（满额收益 +$700）。
4. **捕获比例**（`fraction_of_max_captured`）= 盈亏 ÷ 最大收益（美元口径），可以为负（上例 C = 95 → 0.2857，C ≥ 100 → −0.4286）。提前平仓和到期结算都写；复盘里只对**赢的笔**取平均。
5. **统计口径**：Put 借方价差在"按策略对比"里有**自己的一行**，另有一节"Put 借方价差（Bear Put Debit Spread，看跌）记录"（每笔的腿、净支出、盈亏平衡价、最大收益 / 最大亏损、所需胜率、浮盈亏或结算结果、捕获比例）。**它不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 各块和阈值重算**。"所需胜率（逐笔平均，净支出 ÷ 宽度）"与"按实际盈亏比算的盈亏平衡胜率"不是同一个口径，报告里已标明。**少于 30 笔不下结论。**
6. **不在公开页**：`wiki/options/paper-trading.md` 逐字节不含任何 Put 借方价差内容（ticker、行权价、金额都不出现）；有无这类记录，公开页文本逐字节相同。
7. Summarise: per trade 浮盈亏、现价距长腿多远、是否碰到止盈 / 止损提示，以及任何结算。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前两腿中间价平掉（价值限制在 [0, 宽度]，结果按盈亏记 `win` / `loss`，状态 `closed_early`）。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

买方复核规则（`/option-review`，镜像 Call 借方价差，规则编号对应报告里的"规则"列，第一个命中的算）：

- 0：任一腿取不到报价 → 需要关注。
- 1：到期日当天或已过 → 等待结算。
- 2：浮亏已达净支出的 50%（止损提示线）→ 需要关注：平仓止损，或承担最大亏损（全部净支出）。
- 3：浮盈 ≥ 最大收益的 50%，或现价已跌到短腿行权价或以下（再跌也不多赚）→ 平仓。
- 4：以下任一 → 需要关注：到期前有财报；剩余 ≤ 10 天且浮盈 ≤ 0（时间止损）；剩余 ≤ 5 天且现价严格落在两腿之间（到期前主动处理）。
- 5：其他 → 继续持有。

**不用 Delta 规则**（卖方的"Delta 偏高要关注"对买方是好事），**也没有除息日提前指派的例外**（买入的 Put 不会被指派；策略页第 5 节对 Put 提前行权的原因有来源冲突，标了待核）。只是建议，永远不替用户平仓。

`/option-journal` 在单独的"Put 借方价差（看跌，单独统计，不计入 A–D 与阈值重算）"一块统计：已结算笔数、赢笔数、胜率、所需胜率逐笔平均、赢的笔平均捕获比例、总盈亏；样本不足 30 笔会提示。示例：三笔 +$200 / +$700 / −$300 → 胜率 66.7%，赢的笔平均捕获 64.3%，总盈亏 +$600，所需胜率逐笔平均 30%。顶部"已结算 N 笔"不含它，另起一行写。给它记的笔记只存进笔记文件，报告里不显示。两者读同一个账本，不属于 `/bearput-sim` 本身。

## 工具不记录的东西

不模拟提前行权、调整、展期、单腿平仓；也不记调整本身。平仓时的两腿 IV 只在拿得到时才写。要验证这些，需要自己另外记。

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，不把 Put 借方价差写进 `wiki/`（只刷新过滤后的公开页）；`/strategy-pick` 只在看法为不看好时推荐（`open TICKER --from-pick` 读它的推荐），也可以手动使用。
- 不判断该不该做，不推荐档位；不模拟提前行权、调整、展期、单腿平仓。
- 方向错了亏掉全部净支出，要讲；两条腿都硬卡 5% 的流动性规则、等宽短腿、30 天默认到期、`WARN_FULL_DEBIT_BEARPUT` / `WARN_DRIFT` 都要原样转述。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees（两条腿的手续费对小额净支出是实质侵蚀）；结果比真实略乐观（真实无法按快照价成交）。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
