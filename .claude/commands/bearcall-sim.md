# bearcall-sim

Paper-trade **Call 贷方价差（Bear Call Credit Spread）**：卖出一个虚值 Call、同时买入高一个宽度的同到期 Call，**净收钱**，看跌或看不涨（**看跌策略**）。用户给一个 ticker，脚本按**短腿** Call 的 Delta 出三档候选（约 0.10 / 0.15 / 0.20），用户挑一档记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"Call 贷方价差（Bear Call Credit Spread，看跌）记录"一节看结果。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes `outputs/`。账本和报告都在 `data/paper-trading/`（gitignored），和 `/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim`、`/bullcall-sim`、`/rr-sim`、`/bearput-sim`、`/longput-sim` 共用同一个账本。`open` / `status` / `close` 会照常刷新**过滤后**的公开页 `wiki/options/paper-trading.md`；该页**不含 Call 贷方价差**，所以本命令不会把任何这类内容写进 `wiki/`。**`/strategy-pick` 在看法为不看好时推荐它**（标签「Bear Call 价差」：未持有（或单账户不足 100 股）时排第二，排在 Bear Put 价差之后；持股 ≥ 100 股、或热门股财报 7 天内时不推荐它），`open TICKER --from-pick` 可以直接按它的 Delta 目标选档；也可以手动使用：自己给 ticker 用本命令。`/strategy-pick` 只读它的推荐文件，不 import 本命令。

**先讲清楚风险：** 这是卖方策略，方向是看跌。**上涨亏钱**：到期股价在长腿行权价或以上就是满额亏损，**最大亏损是宽度减权利金**，通常是最大收益的好几倍；所需胜率 = 1 − 权利金 ÷ 宽度，往往在 70% 以上。历史上市场整体向上漂移，看跌样本里亏的概率偏高。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "给 ZZZQ 出个 Call 贷方价差" / "宽度 5 的" | `suggest TICKER [--width W]` — 不写账本 |
| open | "ZZZQ 开 0.15 档" / "开 2 张" | `open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]` — 记录已保存的候选，默认 1 张 |
| open（按推荐） | "按 /strategy-pick 的推荐开 ZZZQ" | `open TICKER --from-pick [--contracts N] [--width W]` — 档位改为读今天 `/strategy-pick TICKER` 的 Bear Call 价差推荐（Delta 目标就近放进档位，与报告里 `open` 那行同一规则），控制台先打印 `按 /strategy-pick <日期> 的结果：Delta 目标 … → 档位 …`；档位与 `--from-pick` 二选一。只读**今天**的 `data/strategy-picks/<今天>-<TICKER>.json`（demo 数据源读其下 `demo/`）：今天没跑过、文件是旧的、格式不对、推荐里没有 Bear Call 价差（例如持股 ≥ 100 股时只推荐减仓和 Bear Put 价差），都直接拒绝（退出码 2，什么都不写），不会回落到更早的结果；先重跑 `/strategy-pick TICKER`。其余校验（已保存候选、宽度核对）不变 |
| status | "看一下模拟盘" | `status`（与 `/spread-sim` 共用 `spread_sim.py`） |
| close | "提前平仓 #7" | `close ID`（同上） |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/bearcall_sim.py suggest TICKER [--width W]
./.venv/Scripts/python.exe scripts/bearcall_sim.py open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/bearcall_sim.py open TICKER --from-pick [--contracts N] [--width W]
./.venv/Scripts/python.exe scripts/bearcall_sim.py status
./.venv/Scripts/python.exe scripts/bearcall_sim.py close ID
```

`--width` 是短腿到长腿的目标宽度；不传则自动推荐（用 Call 链估算，链太薄时用默认值 5，说明文件会写明宽度来源）。没有 `--min-days`：卖方吃时间价值，到期日规则同 `/spread-sim`。

## 规则（脚本已实现，转述即可）

- **到期日**：距快照日**至少 7 天**的第一个到期日（同 `/spread-sim`，卖方吃时间衰减，不需要买方的 30 天）。
- **三档按短腿 Call 的 Delta：0.10 / 0.15 / 0.20**（与 Bull Put 同一组卖方档位），各取 Delta 最接近目标的**虚值** Call（行权价不低于现价）；实际 Delta 偏离目标超过 0.05 → 该档剔除，每档各自判断。**为什么是这三档**：卖方价差的通行区间是短腿 Delta 0.10–0.20，越低越不容易被涨穿、但权利金越薄；不做更高的 Delta（离现价太近，涨一点就价内）。三档是名义目标，实际短腿 Delta 另存。
- **等宽长腿**：长腿**必须恰好**在"短腿行权价 + 宽度"的挂牌行权价上，找不到或没有有效报价 → 该档剔除（"上方没有等宽的长腿"），**不凑近似宽度**。
- **两条腿都硬卡 5%**：短腿和长腿各自的买卖价差都必须 ≤ 该腿中间价的 **5%**（恰好 5% 通过），否则该档剔除。和 Bull Put、Bull Call 一致；这里长腿离现价只有一个宽度，不是铁鹰那种只有几美分的远翼，所以不放宽为警告。报价陈旧（休市时 bid/ask 为空，用了最后成交价）只警告，不排除。
- **结构有效性**：短腿 < 长腿且同一到期日；权利金 = 短腿中间价 − 长腿中间价（每股，4 位小数），必须大于 0 且小于宽度，否则该档剔除（"净权利金无效"）。`open` 对已保存但结构无效（文件被改坏）的候选也会拒绝，舍入容差 1e-4。
- **钱怎么算**（每张）：最大收益 = 权利金 × 100；**最大亏损 = 占用资金 = (宽度 − 权利金) × 100**；盈亏平衡价 = 短腿行权价 + 权利金；所需胜率 = 1 − 权利金 ÷ 宽度（卖方口径）；回本幅度 = 盈亏平衡价 ÷ 现价 − 1（为正）；满额亏损幅度 = 长腿行权价 ÷ 现价 − 1（涨到长腿就亏满）。候选 JSON 与账本里存**小数**（0.76、0.062），报告、说明文件显示**百分数**（76%、+6.20%），转述时别混。

示例（虚构 ZZZQ，现价 100，设计文档第 8 节的手算数字）：卖 105 Call（中间价 2.10）、买 110 Call（中间价 0.90），宽度 5，1 张：权利金 1.20/股，最大收益 $120，最大亏损 $380，盈亏平衡 106.20，所需胜率 76%，回本幅度 +6.20%，满额亏损幅度 +10.00%。

## 警告（只警告、不排除）

relay every warning the file carries and explain briefly。以下两条**每份候选都带**，原样转述，不要淡化：

- **`WARN_BEAR_CALL_UPSIDE`**：「上涨亏钱：到期股价在长腿行权价或以上就是满额亏损，最大亏损是宽度减权利金」。
- **`WARN_DRIFT`**：「历史上市场整体向上漂移，看跌样本里亏的概率偏高」。

其余只在命中时出现：权利金占宽度太低（赔率太差）；**财报在到期前**（跳空向上可能直接满额亏损，按分享页规则建议跳过；财报日期未知也会提示）；**除息日在到期前且短 Call 已到价内边缘**（除息前可能被提前指派）；报价陈旧；Delta 由隐含波动率估算。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest TICKER [--width W]`。
   **Data source:** 期权到期日、报价、Delta 来自当前数据源（`ibkr` / `yahoo` / `demo`，设置与质量见 `docs/option-data-sources.md`；汇总开头写明来源和数据质量，原样转述），财报和除息日一律来自 Yahoo。IBKR 模式是只读延迟数据，休市时是延迟冻结的上一交易日数据，按快照日标注，网关设置在 `data/ibkr.env`（gitignored），never print or commit the values。**数据源连不上整次运行中止、非 0 退出，不写任何文件，不会换别的数据源重试。**
2. 它写两类文件（**不写账本**），先写说明、JSON 最后落地：
   - 说明和机器可读候选：`data/paper-trading/suggestions/<快照日>-<TICKER>-bearcall.md` 与同名 `.json`（`open` 读的是 `.json`）。同一天重跑会覆盖当天同 ticker 的旧文件；文件名以小写 `-bearcall` 结尾，其他策略的加载器不会误读。
   - 当天汇总：`data/paper-trading/bearcall-sim/<快照日>.md`。三档都被剔除时文件照写（写明原因），退出码 0。
3. Read the summary and the per-ticker file and show them：三档各自的短腿 / 长腿行权价、实际 Delta、两腿中间价、权利金、盈亏平衡价、回本幅度与满额亏损幅度、最大收益 / 最大亏损、所需胜率，或被剔除的理由。Relay **every warning**。本工具**不判断哪一档好**，不推荐，由用户自己挑。
4. 把固定风险讲清楚：上涨亏钱、最大亏损是宽度减权利金；所需胜率高是卖方的常态；市场长期向上漂移对看跌不利。
5. Ask which bucket to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]`。它**不取行情、不需要数据源**，只记录最新已保存候选里那一档，所以记下的就是用户看过的。
2. 它会拒绝：没有该 ticker 的已保存候选、候选文件损坏、候选到期日已过、该档在最新一次 suggest 里被剔除、结构无效（短 ≥ 长、腿距与宽度不符、权利金不在 0 到宽度之间、权利金与两腿中间价之差或盈亏平衡价对不上）、`--width` 与已保存候选的宽度不一致（要重新 `suggest --width`）、与账本里同 ticker 同到期日的记录重复（任意策略）。Relay the message，必要时重新 `suggest`，不要绕过。
3. **一笔 Call 贷方价差只记一条整体记录**：沿用卖方价差的字段名（`short_strike` 是卖出的低行权价 Call、`long_strike` 是买入的高行权价 Call、`credit`、`width`），另加 `option_type: "C"` 和 `strategy: "bear_call_spread"`，与 Bull Put 区分。所有与方向有关的地方（到期损益、价内判断、取哪条期权链、复核规则）都按 Call 方向单独处理。输出行显示权利金、最大收益、最大亏损 / 占用资金和所需胜率，read it back.

### 3. status

1. Run `status`。与其他策略共用：对每笔未平仓按当前两腿中间价估值，每股平仓成本 = clamp(短腿中间价 − 长腿中间价, 0, 宽度)；浮盈 = (权利金 − 成本) × 100 × 张数。**任一条腿取不到有效报价 → 该笔标"取不到报价"，不猜价，`close` 也拒绝。** 到期当天提示次日再运行；过了到期日自动结算。**Never close anything yourself.**
2. **提示线（只提示，不平仓，沿用卖方口径）**：浮盈 ≥ 权利金的 **50%**（含等号）→ 止盈线；浮亏 ≥ 最大亏损的 **50%**（含等号）→ 止损线。示例：上面那笔止盈线 +$60，止损线 −$190。
3. **到期结算**（用到期日收盘价 C）：每股成本 = clamp(C − 短腿行权价, 0, 宽度)；盈亏 = (权利金 − 成本) × 100 × 张数。C 在短腿或以下 → 成本 0（满额收益 +$120）；C 恰等于盈亏平衡价 106.20 → 盈亏 $0，**记为亏损**（`loss`）；C = 108 → −$180；C 在长腿 110 或以上 → 成本钳到宽度（满额亏损 −$380）。
4. **统计口径**：Call 贷方价差在"按策略对比"里有**自己的一行**，另有一节"Call 贷方价差（Bear Call Credit Spread，看跌）记录"（每笔的腿、权利金、盈亏平衡价、最大收益 / 最大亏损、所需胜率、浮盈亏或结算结果）。**它不进 Bull Put 的胜率与 Delta 档位表，也不进 `/option-journal` 的 A–D 各块和阈值重算**。"所需胜率（逐笔平均）"与"按实际盈亏比算的盈亏平衡胜率"不是同一个口径，报告里已标明。**少于 30 笔不下结论。**
5. **不在公开页**：`wiki/options/paper-trading.md` 逐字节不含任何 Call 贷方价差内容（ticker、行权价、金额都不出现）；有无这类记录，公开页文本逐字节相同。
6. Summarise: per trade 浮盈亏、现价距短腿多远、是否碰到止盈 / 止损提示，以及任何结算。Point to `data/paper-trading/report.html` and `report.md`。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early。它按当前两腿中间价平掉（成本限制在 [0, 宽度]，结果按盈亏记 `win` / `loss`，状态 `closed_early`）。示例：短 1.40 / 长 0.50 → 成本 0.90，+$30。**Never run `close` automatically**, not even when `/option-review` flags a trade.

## 复盘与每周检查

`/option-review` 的 Call 贷方价差规则（卖方顺序，方向取反；规则编号对应报告里的"规则"列，第一个命中的算）：

- 0：任一腿取不到报价 → 需要关注。
- 1：到期日当天或已过 → 等待结算。
- 2：**价内**（现价**严格高于**短腿行权价，正好等于不算）或已到止损线 → 需要关注（平仓止损，或承担最大亏损）；短腿价内且除息日在到期前时，理由加上提前指派风险。
- 3：已赚比例 ≥ 80% 权利金 → 平仓。
- 4：以下任一 → 需要关注：到期前有财报；短腿 Call Delta ≥ 0.35（仅数据可信时）。
- 5：其他 → 继续持有（剩余 ≤ 5 天且短腿 Delta ≤ 0.10 时写"等它自然到期归零"）。

阈值是现有四个常量，不新增。只是建议，永远不替用户平仓。

`/option-journal` 在单独的"Call 贷方价差（看跌，单独统计，不计入 A–D 与阈值重算）"一块统计：已结算笔数、赢笔数、胜率、所需胜率逐笔平均（1 − 权利金 ÷ 宽度）、总盈亏；样本不足 30 笔会提示。顶部"已结算 N 笔"不含它，另起一行写。给它记的笔记只存进笔记文件，报告里不显示。两者读同一个账本，不属于 `/bearcall-sim` 本身。

## 工具不记录的东西

不模拟提前行权、调整、展期、单腿平仓；也不记调整本身。平仓时的两腿 IV 只在拿得到时才写。要验证这些，需要自己另外记。

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps a `.bak` before every write. 文件损坏时停下，把报错给用户看，不要覆盖。
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/`，不把 Call 贷方价差写进 `wiki/`（只刷新过滤后的公开页）；`/strategy-pick` 只在看法为不看好时推荐（`open TICKER --from-pick` 读它的推荐），也可以手动使用。
- 不判断该不该做，不推荐档位；不模拟提前行权、调整、展期、单腿平仓。
- 上涨亏钱、最大亏损是宽度减权利金，要讲；两条腿都硬卡 5% 的流动性规则、等宽长腿、`WARN_BEAR_CALL_UPSIDE` / `WARN_DRIFT` 都要原样转述。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees（两条腿的手续费对小额权利金是实质侵蚀）；结果比真实略乐观（真实无法按快照价成交）。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
