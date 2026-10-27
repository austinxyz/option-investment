# bullcall-sim

Paper-trade **Call 借方价差（Bull Call Debit Spread）**：买入一个 Call、同时卖出一个更高行权价的同到期 Call，**净付钱**，赌股价涨到短腿行权价附近。用户给一个 ticker，脚本按**长腿** Call 的 Delta 出三档候选（约 0.60 / 0.50 / 0.40），用户挑一档记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"Call 借方价差记录"一节看结果。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes `outputs/`。账本和报告都在 `data/paper-trading/`（gitignored），和 `/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim` 共用同一个账本。`open` / `status` / `close` 会照常刷新**过滤后**的公开页 `wiki/options/paper-trading.md`；该页**不含 Call 借方价差**，所以本命令不会把任何这类内容写进 `wiki/`。

**先讲清楚风险：** 这是买方策略。**方向错了亏掉全部净支出**（到期股价在长腿行权价或以下就是满额亏损）；所需胜率 = 净支出 ÷ 宽度，通常低于一半，这是正常的，靠赔率赚钱，不是靠高胜率。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给 ZZQB 出个 Call 借方价差" / "宽度 5 的" / "放宽到 14 天" | `suggest TICKER [--width W] [--min-days N]` — 不写账本 |
| open | "ZZQB 开 0.50 档" / "开 2 张" | `open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]` — 记录已保存的候选，默认 1 张 |
| status | "看一下模拟盘" | `status`（与 `/spread-sim` 共用 `spread_sim.py`） |
| close | "提前平仓 #7" | `close ID`（同上） |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/bullcall_sim.py suggest TICKER [--width W] [--min-days N]
./.venv/Scripts/python.exe scripts/bullcall_sim.py open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/bullcall_sim.py status
./.venv/Scripts/python.exe scripts/bullcall_sim.py close ID
```

`--width` 是长腿到短腿的目标宽度；不传则自动推荐（用 Call 链估算，链太薄时用默认值，说明文件会写明宽度来源）。`--min-days N` 放宽到期日下限（N 必须 ≥ 7，小于 7 会被拒绝）。

## 规则（脚本已实现，转述即可）

- **到期日**：距快照日**至少 30 天**的第一个到期日（策略页：买方建议剩余 30~45 天以上，时间衰减对买方不利）。`--min-days N`（N ≥ 7）可放宽；所选到期日不足 30 天时**警告**"净买方，时间不在你这边"（不排除）。
- **三档按长腿 Delta：0.60 / 0.50 / 0.40**，各取 Delta 最接近目标的 Call（长腿可以是实值）；实际 Delta 偏离目标超过 0.05 → 该档剔除，每档各自判断。**为什么是这三档**：策略页给的三种形态里，"长腿实值 + 短腿虚值"（高概率）对应 0.60，"长腿平值"（教程案例的形态，最常用）对应 0.50，"两腿都虚值"（赔率博弈）只取温和的 0.40；**不做**深虚值（大概率归零，所需胜率虽低但样本也难解读）。三档是名义目标，实际长腿 / 短腿 Delta 另存。
- **宽度与等宽短腿**：短腿**必须恰好**在"长腿行权价 + 宽度"的挂牌行权价上，找不到或没有有效报价 → 该档剔除（"上方没有等宽的短腿"），**不凑近似宽度**。两个档位有可能选到同一个长腿行权价，说明里会标出；`open` 会拦住第二笔重复。
- **宽度怎么选**：显式 `--width` 优先；否则自动推荐；链太薄时回落到默认值。**宽度越宽，净支出越大、最大收益越大，所需胜率不一定变**；这里不判断宽度好坏，由用户自己定。
- **两条腿都硬卡 5%（这是与铁鹰不同的地方）**：**长腿和短腿**各自的买卖价差都必须 ≤ 该腿中间价的 **5%**（恰好 5% 通过），否则该档剔除。策略页明说两条腿都适用，而且这里短腿离现价只有一个宽度，不是铁鹰那种只有几美分的远翼，所以不放宽为警告。报价陈旧（休市时 bid/ask 为空，价差未知、用了最后成交价）只警告，不排除。
- **结构有效性**：长腿 < 短腿且同一到期日；净支出 = 长腿中间价 − 短腿中间价（每股，4 位小数），必须大于 0 且小于宽度，否则该档剔除（"净支出无效"）；盈亏平衡价与长腿、净支出对得上。`open` 对已保存但结构无效（文件被改坏）的候选也会拒绝。
- **净支出 ÷ 宽度 = 所需胜率**。注意口径：这是**买方**口径，和卖方的"1 − 权利金 ÷ 宽度"相反；候选 JSON 与账本里存的是**小数**（0.38），报告、说明文件里显示成**百分数**（38%）；回本幅度 / 满额幅度（`breakeven_move_pct` / `max_profit_move_pct`）同样存小数、显示百分数，转述时别把 0.019 当成 0.019%。
- **占用资金 / 最大亏损**：净支出 × 100 × 张数（= 全部净支出）。**最大收益**：(宽度 − 净支出) × 100 × 张数。**盈亏平衡价**：长腿行权价 + 净支出。

## 警告（只警告、不排除）

relay every warning the file carries and explain briefly:

- **`WARN_FULL_DEBIT`（每份候选都带）**：「方向错了亏掉全部净支出：到期股价在长腿行权价或以下就是满额亏损；所需胜率低于一半是正常的，靠赔率赚钱」。原样转述，不要淡化。
- **所需胜率 > 60%**：花得太多，赔率差。
- **财报在到期前**：财报跳空向下照样满额亏损，按分享页规则建议跳过；财报日期未知也会提示。
- **除息日在到期前且短 Call 偏近现价**（不高于现价的 103%）：提前被指派风险高。
- **不足 30 天**（见上）、报价陈旧、Delta 由隐含波动率估算。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest TICKER [--width W] [--min-days N]`。
   **Data source:** 期权到期日、报价、Delta 来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`；汇总开头写明来源和数据质量，原样转述），财报和除息日一律来自 Yahoo。IBKR 模式是只读延迟数据，休市时是延迟冻结的上一交易日数据，按快照日标注，网关设置在 `data/ibkr.env`（gitignored），never print or commit the values。Yahoo 模式休市后 bid/ask 常为空，建议开盘时间运行。**数据源连不上整次运行中止，不写任何文件，不会换别的数据源重试。**
2. 它写两类文件（**不写账本**）：
   - 说明和机器可读候选：`data/paper-trading/suggestions/<快照日>-<TICKER>-bullcall.md` 与同名 `.json`（`open` 读的是 `.json`）。同一天重跑会覆盖当天同 ticker 的旧文件；文件名以 `-bullcall` 结尾，其他策略的加载器不会误读。
   - 当天汇总：`data/paper-trading/bullcall-sim/<快照日>.md`。
3. Read the summary and the per-ticker file and show them：三档各自的长腿 / 短腿行权价、实际 Delta、两腿中间价、净支出、盈亏平衡价、回本幅度与满额幅度、最大收益 / 最大亏损、所需胜率，或被剔除的理由。Relay **every warning**。本工具**不判断哪一档好**，不推荐，由用户自己挑。
4. 把固定风险讲清楚：方向错了亏掉全部净支出；所需胜率低于一半是正常的；到期前主要靠方向和时间。
5. Ask which bucket to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER {0.60,0.50,0.40} [--contracts N] [--width W]`。它**不取行情、不需要数据源**，只记录最新已保存候选里那一档，所以记下的就是用户看过的。
2. 它会拒绝：没有该 ticker 的已保存候选、候选文件损坏、候选到期日已过、该档在最新一次 suggest 里被剔除、结构无效（长 ≥ 短、腿距与宽度不符、净支出不在 0 到宽度之间、净支出与盈亏平衡对不上）、`--width` 与已保存候选的宽度不一致（要重新 `suggest --width`）、与账本里同 ticker 同到期日的记录重复。Relay the message，必要时重新 `suggest`，不要绕过。
3. **一笔 Call 借方价差只记一条整体记录**（两个行权价、两腿中间价、净支出、盈亏平衡价、回本 / 满额幅度、两腿 Delta 与 IV），不拆成两条单腿。输出行显示净支出、盈亏平衡价、最大收益、最大亏损 / 占用资金和所需胜率，read it back.

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前两腿中间价估值，每股价值 = 长腿中间价 − 短腿中间价，限制在 [0, 宽度]；浮盈 = (价值 − 净支出) × 100 × 张数。**任一条腿取不到有效报价 → 该笔标"取不到报价"，不猜价，`close` 也拒绝。** 到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **提示线（只提示，不平仓）**：浮盈 ≥ 最大收益的 **50%**（含等号）→ 止盈线；浮亏 ≥ 净支出的 **50%**（含等号）→ 止损线。这与卖方的线不同，由脚本单独计算，转述它给的文案即可。
3. **到期结算**（用到期日收盘价 C）：每股价值 = clamp(C − 长腿行权价, 0, 宽度)；盈亏 = (价值 − 净支出) × 100 × 张数。C 恰等于长腿 → 价值 0（满额亏损）；C ≥ 短腿 → 价值 = 宽度（满额收益）；C 恰等于盈亏平衡价 → 盈亏 0，**记为亏损**（`loss`）。
4. **捕获比例**（`fraction_of_max_captured`）= 盈亏 ÷ 最大收益（美元口径），可以为负。提前平仓和到期结算都写；复盘里只对**赢的笔**取平均，回答"赚的那些笔平均拿到了最大收益的几成"。
5. **统计口径**：Call 借方价差在"按策略对比"里有**自己的一行**，另有一节"Call 借方价差记录"（每笔的腿、净支出、盈亏平衡价、最大收益 / 最大亏损、所需胜率、浮盈亏或结算结果、捕获比例）。**它不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 各块**（那些是卖方口径）。该行旁边的"所需胜率（逐笔平均，净支出 ÷ 宽度）"与"按实际盈亏比算的盈亏平衡胜率"不是同一个口径，报告里已标明，转述时别混用。**少于 30 笔不下结论。**
6. **不在公开页**：`wiki/options/paper-trading.md` 逐字节不含任何 Call 借方价差内容（ticker、行权价、金额都不出现）。
7. Summarise: per trade 浮盈亏、现价距长腿多远、是否碰到止盈 / 止损提示，以及任何结算。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前两腿中间价平掉（价值限制在 [0, 宽度]，结果按盈亏记 `win` / `loss`，状态 `closed_early`）。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

买方复核规则（`/option-review`，规则编号对应报告里的"规则"列，第一个命中的算）：

- 0：取不到报价 → 需要关注。
- 1：到期日当天或已过 → 等待结算。
- 2：浮亏已达净支出的 50%（止损提示线）→ 需要关注：平仓止损，或承担最大亏损（全部净支出）。
- **例外（编号 4，但先于下面的平仓判断）**：短腿**严格**价内（现价 > 短腿行权价）且除息日在到期前 → **需要关注**（提前指派风险，考虑平仓）。实现上它在平仓判断之前检查：短腿一价内必然也"触及短腿"，若放在平仓之后，提前指派的理由永远显示不出来。现价**恰好等于**短腿、即使有除息日，仍走下面的平仓。
- 3：浮盈 ≥ 最大收益的 50%，或现价 ≥ 短腿行权价（策略页："触及短腿就平"）→ 平仓。
- 4：以下任一 → 需要关注：到期前有财报；剩余 ≤ 10 天且浮盈 ≤ 0（时间止损）；剩余 ≤ 5 天且现价严格落在两腿之间（到期前主动处理）。
- 5：其他 → 继续持有。

**不用 Delta 规则**：卖方的"Delta 偏高要关注"对买方是好事，快照里仍记长腿 Delta 仅供参考。只是建议，永远不替用户平仓。

`/option-journal` 在单独的"Call 借方价差"一块统计（已结算笔数、赢笔数、胜率、所需胜率逐笔平均、赢的笔平均捕获比例、总盈亏），不并入 A–D，**没有"突破侧"**。两者读同一个账本，不属于 `/bullcall-sim` 本身。

## 工具不记录的东西

不模拟提前行权、调整、展期、单腿平仓；也不记调整本身。平仓时的两腿 IV 只在拿得到时才写。要验证这些，需要自己另外记。

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，不把 Call 借方价差写进 `wiki/`（只刷新过滤后的公开页）。
- 不判断该不该做，不推荐档位；不模拟提前行权、调整、展期、单腿平仓。
- 方向错了亏掉全部净支出，要讲；两条腿都硬卡 5% 的流动性规则、30 天默认到期、`WARN_FULL_DEBIT` 都要原样转述。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees（两条腿的手续费对小额净支出是实质侵蚀）；结果比真实略乐观（真实无法按快照价成交）。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
