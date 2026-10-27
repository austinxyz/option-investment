# condor-sim

Paper-trade **铁鹰（Iron Condor）**：同时卖出一个虚值 Put 价差和一个虚值 Call 价差，赌到期时股价留在两个短腿之间。用户给一个 ticker，脚本出三档候选（短腿 |Delta| 约 0.10 / 0.15 / 0.20），用户挑一档记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"铁鹰记录"一节看结果。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes `outputs/`。账本和报告都在 `data/paper-trading/`（gitignored），和 `/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen` 共用同一个账本。`open` / `status` / `close` 会照常刷新**过滤后**的公开页 `wiki/options/paper-trading.md`；该页**不含铁鹰**，所以本命令不会把任何铁鹰内容写进 `wiki/`。

**先讲清楚风险：** 铁鹰只赚一份权利金，最多亏单侧宽度减权利金；**大涨对铁鹰是满额亏损**（涨过上方长翼，Call 价差亏满宽度），不是只怕下跌。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给 ZZZQ 出个铁鹰" / "宽度 5 的" | `suggest TICKER [--width W]` — 不写账本 |
| open | "ZZZQ 开 0.15 档" / "开 2 张" | `open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]` — 记录已保存的候选，默认 1 张 |
| status | "看一下模拟盘" | `status`（与 `/spread-sim` 共用 `spread_sim.py`） |
| close | "提前平仓 #7" | `close ID`（同上） |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/condor_sim.py suggest TICKER [--width W]
./.venv/Scripts/python.exe scripts/condor_sim.py open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/condor_sim.py status
./.venv/Scripts/python.exe scripts/condor_sim.py close ID
```

`--width` 是 Put 侧的目标宽度；不传则自动推荐（链太薄时用默认值，说明文件会写明来源）。

## 规则（脚本已实现，转述即可）

- **到期日**：同 `/spread-sim`，距快照日至少 7 天的第一个到期日。
- **三档**：两个短腿 |Delta| 分别最接近 0.10 / 0.15 / 0.20；实际 Delta 偏离目标超过 0.05 → 该档剔除。每档各自判断，一档剔除不影响另外两档。
- **等宽规则**：先定 Put 价差，Call 长翼恰好放在"短 Call + Put 侧实际宽度"的行权价上。两侧宽度必须相等；Call 侧找不到这个行权价 → 该档剔除，**不凑近似宽度**。
- **结构有效性**：行权价必须是 长 Put < 短 Put < 短 Call < 长 Call，四个行权价互不复用；两侧权利金各自要大于 0 且小于宽度；净权利金 = 两侧之和且小于宽度；两个盈亏平衡价与行权价、净权利金对得上。不满足 → 该档剔除；`open` 对已保存但结构无效（文件被改坏）的候选也会拒绝。
- **流动性规则（与策略页不同，必须说清楚）**：
  - **两个短腿**：买卖价差必须 ≤ 该腿权利金的 **5%**，超了 → 该档剔除（硬规则）。
  - **两个长翼**：买卖价差 **> 15% 只警告，不剔除**。
  - **为什么偏离策略页**：策略页（来源 A 的标准）写的是四条腿都要过 5%。但长翼是远虚值，权利金通常只有几美分，哪怕价差只有一两分，比例也天然很大；四条腿都硬卡 5% 会把几乎所有档位剔除，工具就没法用。所以本工具只对决定成交质量的两个短腿硬卡 5%，长翼放宽为 15% 警告，并在每份说明里写明这一偏离。转述时直说，不要含糊带过。
- **净权利金过薄**：净权利金占单侧宽度低于 15% → 警告"赔率太差"（不排除）。
- **所需胜率**：1 − 净权利金 ÷ 宽度（理论盈亏平衡）。
- **占用资金 / 最大亏损**：(宽度 − 净权利金) × 100 × 张数（只会有一侧亏满，所以按单侧算）。
- **盈亏平衡价**：短 Put − 净权利金，短 Call + 净权利金。

## 警告（只警告、不排除）

relay every warning the file carries and explain briefly:

- **大涨对铁鹰是满额亏损**：每份说明都带这一条。
- **财报在到期前**：财报前 IV 会先让持仓亏，按分享页规则建议跳过；财报日期未知也会提示。
- **除息日在到期前且短 Call 偏近现价**（不高于现价的 103%）：提前被指派风险高。
- **长翼价差 > 15%**、报价陈旧（休市时 bid/ask 为空，用了最后成交价）、Delta 由隐含波动率估算。
- 净权利金过薄（见上）。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest TICKER [--width W]`。
   **Data source:** 期权到期日、报价、Delta 来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`；汇总开头写明来源和数据质量，原样转述），财报和除息日一律来自 Yahoo。IBKR 模式是只读延迟数据，休市时是延迟冻结的上一交易日数据，按快照日标注，网关设置在 `data/ibkr.env`（gitignored），never print or commit the values。Yahoo 模式休市后 bid/ask 常为空，建议开盘时间运行。**数据源连不上整次运行中止，不写任何文件，不会换别的数据源重试。**
2. 它写两类文件（**不写账本**）：
   - 说明和机器可读候选：`data/paper-trading/suggestions/<快照日>-<TICKER>-condor.md` 与同名 `.json`（`open` 读的是 `.json`）。同一天重跑会覆盖当天同 ticker 的旧文件；`.json` 以 `-condor` 结尾，所以不会被价差的加载器误读。
   - 当天汇总：`data/paper-trading/condor-sim/<快照日>.md`。
3. Read the summary and the per-ticker file and show them：三档各自的四个行权价、实际 Delta、两侧权利金与净权利金、盈亏平衡价、最大亏损、所需胜率，或被剔除的理由。Relay **every warning**。本工具**不判断哪一档好**，不推荐，由用户自己挑。
4. 把固定风险讲清楚：大涨对铁鹰是满额亏损；净权利金很小而最大亏损是它的几倍；只要有一侧出事，另一侧的权利金也救不回来。
5. Ask which bucket to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]`。它**不取行情**，只记录最新已保存候选里那一档，所以记下的就是用户看过的。
2. 它会拒绝：没有该 ticker 的已保存候选、候选文件损坏、候选到期日已过、该档在最新一次 suggest 里被剔除、结构无效、`--width` 与已保存候选的目标宽度不一致（要重新 `suggest --width`）、与账本里同 ticker 同到期日的记录重复。Relay the message，必要时重新 `suggest`，不要绕过。
3. **一笔铁鹰只记一条整体记录**（四个行权价、两侧权利金、净权利金、两个盈亏平衡价、短腿 Delta 与 IV），不拆成两条价差。输出行显示净权利金、盈亏平衡价、最大盈利、最大亏损 / 占用资金和所需胜率，read it back.

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前中间价估值，每一侧的平仓成本各自限制在 [0, 宽度] 内再相加。铁鹰没有止盈止损线之外的特殊提示，只显示浮盈亏；到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **到期结算**（用到期日收盘价）：每一侧的亏损各自限制在 [0, 宽度]。收盘落在两个短腿之间 → 保留全部权利金；穿过一侧短腿 → 亏（穿过的幅度 − 净权利金）× 100 × 张数，最多亏满（宽度 − 净权利金）。盈亏为 0 记为亏损（`loss`）。
3. **`breached_side`（哪一侧出事）只在到期结算时写**：`none`（两侧都没被突破）、`put`、`call`。**提前平仓不写**，因为没到期就无法确定最终结果；报告里显示"—"。不要把提前平仓的记录当成"没被突破"。
4. **统计口径**：铁鹰在"按策略对比"里有**自己的一行**，另有一节"铁鹰记录"（笔数、胜率、突破侧分布、总盈亏；提前平仓单列，因为没有突破侧）。**它不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 各块**（那些是单腿 Put 的口径）。表里的"理论盈亏平衡"（1 − 净权利金 ÷ 宽度，按笔平均）与按实际盈亏比算出的盈亏平衡不是同一个口径，报告里已标明，转述时别混用。**少于 30 笔不下结论。**
5. **不在公开页**：`wiki/options/paper-trading.md` 逐字节不含任何铁鹰内容（ticker、行权价、金额都不出现）。
6. Summarise: per trade 浮盈亏、距离两个短腿多远，以及任何结算（突破了哪一侧）。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前四条腿中间价平掉（结果按盈亏记 `win` / `loss`，状态 `closed_early`）。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

- `/option-review` 对铁鹰按四条腿读行情：现价跌破短 Put 或涨过短 Call（一侧价内）→ 建议平仓止损或承担该侧最大亏损；短腿 Delta 偏高、除息日前短 Call 在价内等 → 需要关注；其余继续持有或按通用规则建议了结。只是建议，永远不替用户平仓。
- `/option-journal` 在单独的"铁鹰"一块统计（笔数、胜率、理论盈亏平衡平均、突破侧分布），不并入 A–D。
- 两者读同一个账本，不属于 `/condor-sim` 本身。

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，不把铁鹰写进 `wiki/`（只刷新过滤后的公开页）。
- 不判断该不该做，不推荐档位；不模拟提前行权、调整、展期、单侧平仓。
- 大涨对铁鹰是满额亏损，要讲；流动性规则与策略页的偏离（短腿硬卡 5%、长翼只警告 15%）要讲。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees（铁鹰四条腿，真实手续费会明显侵蚀小权利金）；结果比真实略乐观（真实无法按快照价成交）。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
