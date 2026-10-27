# call-screen

Paper-trade 备兑 Call（covered call）。脚本把三种来源的持股合成一张"持股表"，按硬规则给每个能覆盖至少 100 股的持仓选出一份 Call 合约（默认**周度**：距快照日 7–14 天内最近的到期日；`--dte monthly` 则取月度，30–45 天；行权价取 Delta 最接近 0.20 的虚值 Call），保存说明文件；用户挑几只记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"按策略对比"和"备兑 Call（Covered Call）记录"看结果。也用来练 Wheel：现金担保 Put 被指派后，那批股票自动成为假设持仓，可以继续卖 Call。

**Paper trading only.** Never touches `data/positions.md`（只读它来知道你有哪些股票），never places an order, never writes anything under `outputs/` or `wiki/`. 所有文件都在 `data/paper-trading/`（gitignored）。账本和报告与 `/put-screen`、`/spread-sim` 共用，见 `.claude/commands/put-screen.md`。

## 三种持股来源

| 来源 | 怎么来的 | 成本 |
|------|----------|------|
| 真实持仓 | 每次 `suggest` 现读 `data/positions.md`（只读，Active 持仓和 ETF，同一 ticker 跨账户合并，不复制存档） | 合并后的加权均价；成本是估算值时会警告 |
| 假设持仓 | `shares add TICKER --shares N --cost C` 手动加，存在 `data/paper-trading/paper_shares.json` | 你给的成本 |
| 被指派的 Put | 账本里已被指派的现金担保 Put，`suggest` 和 `status`（结算后的同步）都会自动补建一个假设持仓（重跑不会重复建；用 `shares remove` 删掉后也不会再被补建） | 行权价 − 权利金（每股） |

每个持仓是一个独立的可覆盖单元：可覆盖张数 = 股数 ÷ 100 取整，再减去已被未平仓 Call 占用的张数；为 0 的不出候选（写明股数不足 100 股或已被覆盖）。真实持仓和假设持仓同一 ticker 可以并存，分别列出。

## Arguments

Natural language or explicit. Parse into one of five actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "筛一遍 covered call" / "我的持股能卖哪些 call" / "月度的" | `suggest [--dte weekly\|monthly]` — 默认 weekly；对整张持股表出候选，不需要传 ticker，不写账本。用户明确要月度才传 `--dte monthly` |
| open | "AAA 开一张 call" / "假设持仓 3 卖 2 张" | `open TICKER [--lot real\|<ID>] [--contracts N]` — 记录已保存的候选，默认 1 张；同一 ticker 有多个持仓候选时必须用 `--lot` 指定 |
| status | "看一下模拟盘" / "周中检查" | `status` |
| close | "提前平仓 #7" | `close ID` |
| shares | "加一个假设持仓 BBB 200 股成本 40" / "列出假设持仓" / "删掉假设持仓 2" | `shares add TICKER --shares N --cost C`、`shares list`、`shares remove ID` |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/call_screen.py <action> ...
```

## 复盘与每周检查

- `/option-review` 对**所有策略**（价差、Sell Put、Covered Call）的每笔未平仓持仓都给建议，每周检查就在那里看。Covered Call 的价内方向与 Put 相反：现价**达到或高于**行权价 = 价内 → 需要关注（理由写"可能被行权"，由用户决定接受行权还是回购）。
- `/option-journal` 把记录下来的建议和最终结果对照；Covered Call 在 journal 里有单独的「备兑看涨」一块（笔数、按期权腿算的胜率、期权腿总盈亏、样本不足提示），**不并入** Put 的平仓建议 / 关注 / 持有 / 连赢统计和阈值重算。
- 两者读的是同一个账本，不属于 `/call-screen` 本身；本命令只负责筛选、开仓、平仓、看盈亏和结算。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest`. 它先把已被指派的 Put 补建成假设持仓（脚本会打印），再逐只取行情，可能要一会儿，等它结束。读取 `data/positions.md` 失败时脚本报错退出，不会用旧数据；某一只取行情失败只会变成那只的剔除行。
   **Data source:** option expirations, quotes, IV and Delta come from the configured data source (`ibkr`, `yahoo` or `demo`; settings, quality and quirks in `docs/option-data-sources.md`). The summary's first lines name the source and its data quality: relay them (Yahoo and demo runs open with a warning banner). IBKR mode is read-only delayed data: after hours and on weekends it serves delayed-frozen data, labelled with the snapshot day, and it can run at any time, not only during US market hours. Yahoo mode is delayed about 15 minutes and after the close bid/ask are often empty (last trade used, flagged stale), so run it during US market hours. Moving averages, earnings and dividend dates always come from Yahoo. Compare a candidate's strike / Delta / premium with your broker's option chain before opening it.
   **IBKR settings (IBKR mode only):** `data/ibkr.env` (gitignored) holds `IBKR_HOST`, `IBKR_PORT`, `IBKR_OPTIONS_CLIENT_ID` and `IBKR_MARKET_DATA_TYPE`; never print or commit their values.
   **Data-source failures abort the run.** If the source is unreachable (IBKR gateway down or dropping mid-run, Yahoo offline or rate-limited) `suggest` exits with a non-zero code and writes no files; there is no fallback to another source. Relay the message. In IBKR mode, if a ticker is rejected with the text of error 10197, a live TWS or phone session on the same IBKR account is stealing market data: ask the user to log that session out (or turn off market data sharing) and re-run.
2. Read the summary `data/paper-trading/call-screen/<快照日>.md` and show it: 每个持仓一行（来源、ticker、可覆盖张数、现价、相对 MA50 / MA200、近一月涨幅、候选合约或剔除理由）。MA50 / MA200 和近一月涨幅只是帮用户判断"哪只涨不动"的信息列，**不是规则**；工具不过滤、不推荐哪只该卖，由用户自己挑。Relay **every warning** the file carries and explain briefly:
   - **低于成本**：行权价低于所持股票的成本时只警告、不排除，文字同时写出相对原始成本和相对有效成本（扣掉已收权利金）被行权每股亏多少（均已含本次权利金）。让用户看清再决定。
   - 除息日在到期前（提前被行权的概率更高）、财报日期未知、成本为估算值（真实持仓）、报价陈旧、真实持仓由多个账户合并而来。
   If the script says 本日无合适周度到期 (or 本日无合适月度到期), say that and stop. Do not add trade recommendations beyond what the file flags.
3. 规则摘要（用户问起时转述）：到期前有财报 → **排除**；除息日在到期前、行权价低于成本、财报日期未知 → 只**警告**；Delta 偏离 0.20 超过 0.05、买卖价差过宽、缺 IV → 排除。**没有"涨不动"过滤，也没有 Avoid / MA200 过滤**：持股是用户自己的，不由本工具判断该不该持有。
4. Tell the user which kind of snapshot it is (the file's header says): after the close or on a weekend it is the last session (IBKR: delayed-frozen data; Yahoo: the last close) labelled with its snapshot day (bid/ask may be empty, then the last trade price is used and flagged stale); during market hours it is a **盘中快照** with live quotes. Point out the fixed risk lines in the output: weekly 合约权利金小、Gamma 高；Covered Call 的上行被封顶——股价涨过行权价时，股票按行权价卖出，错过超出部分的涨幅；下跌时权利金只能缓冲一点点。
5. 每个入选持仓还有一份逐只说明 `data/paper-trading/suggestions/<快照日>-<TICKER>-call-real.md`（真实持仓）或 `-call-lot<ID>.md`（假设持仓）和同名 `.json` 孪生数据，`open` 读的是后者。同一天重跑会先删掉当天旧的 `-call` 文件再重写（Put 和价差的同名文件不动）。Tell the user the path, and summarise a candidate's file in a few lines on request; do not paraphrase numbers differently from the file.
6. Ask which positions to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER [--lot real|<ID>] [--contracts N]`. It does **not** fetch the market: it records the contract from the latest saved `suggestions/<日期>-<TICKER>-call-*.json`, so the trade is exactly what the user reviewed. 同一 ticker 当天有多个持仓的候选时，脚本拒绝并列出可选的 `--lot` 值（`real` 或假设持仓编号），relay it and ask the user which one。合约数默认 1，超过可覆盖张数会被拒绝。
2. 它还会拒绝：没有已保存的候选、候选的到期日已过、文件损坏、持仓已不够覆盖、同 ticker 同到期日已有别的策略的记录（跨策略算重复；同一 ticker 的不同持仓可以各开一张同到期日的 Call）。Relay the message and, if needed, re-run `suggest`; do not work around it.
3. `open` 记录的内容：合约（行权价、到期日、权利金、Delta 等）、持仓引用（来源和编号）、开仓时每股成本的快照（`cost_basis`）、覆盖股数（张数 × 100）、快照日期和类型。输出行里有 `快照 YYYY-MM-DD 收盘/盘中`，read it back. 开仓**不占用额外现金**（股票已持有）。Report the recorded line and any warnings the script prints.

### 3. status

1. Run `status`. It is shared with `/put-screen` and `/spread-sim`: it marks every open trade at current mid prices, prints 🟢 已到止盈线 / 🔴 已到止损线 tips (take-profit = 赚到权利金 50%，stop = 亏到 2 倍权利金), and settles trades past their expiry day automatically. On the expiry day itself it says to re-run the next day or later. **Flags are advice only — never close anything yourself.**
2. 到期结算（用到期日收盘价）：
   - **收盘价 < 行权价（没被行权）**：权利金全收，记为 win（期权腿赢），股票还在；假设持仓的有效成本 −= 权利金（每股）。
   - **收盘价 ≥ 行权价（被行权）**：记为 called_away，股票按行权价卖出。**期权腿**盈亏 = 权利金 × 100 × 张数；**股票腿**盈亏 = (行权价 − 参考价) × 100 × 张数，单独记录（参考价：手动持仓是你给的成本，真实持仓是均价，被指派的 Put 持仓是指派日收盘价）。两条腿分开显示，"含股票腿"总盈亏只显示，不进胜率。假设持仓标记为已被行权，不再可覆盖；真实持仓**不改**（`status` 的结算提示、`report.md` 和网页版都会标注"（真实持仓未变）"，该持仓仍可继续被覆盖）。
   - **胜率只看期权腿**（期权腿盈亏 > 0 算赢）：评价的是"卖 Call 这个决定"，股票的大额浮盈不应淹没它。
   - 只记录到到期日，不模拟提前行权；被行权之后怎么办（重新买回、再开 Wheel）由用户决定。
3. Summarise: per trade P&L and distance to the strike, which trades hit a flag, anything settled (说明是期权腿还是含股票腿). Point to `data/paper-trading/report.html` (friendly page) and `report.md` (same data as text); Covered Call 在报告里有独立的 `## 备兑 Call（Covered Call）记录` 一节。

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early. 它按当前中间价回购，记账；假设持仓的有效成本 −= (权利金 − 回购价)。**Never run `close` automatically**, not even when `status` or `/option-review` flags a trade.

### 5. shares（管理假设持仓）

- `shares add TICKER --shares N --cost C`：新增一个假设持仓（纸上股票，不影响 `data/positions.md`）。
- `shares list`：列出全部假设持仓，含有效成本；`shares remove ID`：删除一个。
- 这些只改 `data/paper-trading/paper_shares.json`；不要手改该文件，脚本写入前会备份 `.bak`，文件损坏时会报错而不是覆盖，relay the message。

### 6. Wheel 怎么用

Wheel = 先卖现金担保 Put 收租金；被指派拿到股票后再对它卖 Call；Call 被行权卖掉后回到第一步。

1. `/put-screen suggest` → `open TICKER`：卖一张现金担保 Put。
2. 到期后（次日起）运行 `status`：若收盘价低于行权价，这笔记为被指派，put 那一段的亏损在账本里已经算好。
3. `call_screen.py suggest`（上一步的 `status` 结算后也会同步）：脚本自动把被指派的 Put 补建成假设持仓，它出现在持股表里，来源标"被指派 put #<Put 编号>，持仓 #<持仓编号>"，**有效成本 = 行权价 − 权利金**（每股）。
4. `call_screen.py open TICKER --lot <持仓编号>`（`--lot` 填 `shares list` 里的持仓编号，不是被指派 Put 的编号）：对这批新股票卖一张 Call。
5. 每收一次权利金，这个持仓的有效成本就再降一点；Call 没被行权就继续下一轮，被行权就按行权价卖出，Wheel 回到第 1 步。
6. 股票腿盈亏从指派日收盘价算起，不会把 Put 那段下跌重复计算。

## Reading the report

`data/paper-trading/report.md` is regenerated after every `open` / `status` / `close`. 回答结果问题时，读 **按策略对比**（Covered Call 单列：已结算笔数、期权腿胜率、平均盈亏、总盈亏，加样本量警告：< 30 笔不要下结论）和 **备兑 Call（Covered Call）记录**（每笔的期权腿、股票腿、含股票腿总盈亏）。

## Rules

- Never edit `data/paper-trading/spreads.json` or `paper_shares.json` by hand; the script keeps a `.bak` before every write. If the script reports a corrupt file, stop and show the user the message — do not overwrite.
- 只做模拟：不下单、不连券商、不写 `data/positions.md` / `outputs/` / `wiki/`。
- 默认 weekly 的原因：和 `/put-screen` 一致，样本积累快，也能和约 7 天的价差公平比较。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees.
- Results run slightly optimistic: the real market cannot fill at the snapshot price. Say so when the user compares to live trading.
- 同一 ticker + 同一到期日不能与别的策略重复开仓。
- 不模拟提前行权、展期、税务；Wheel 的下一步由用户决定，不自动循环。
- Relay every warning from the summary file; never invent a recommendation.
- 模拟/研究用途，非投资建议。
