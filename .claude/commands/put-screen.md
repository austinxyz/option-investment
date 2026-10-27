# put-screen

Paper-trade 现金担保 Sell Put（cash-secured put）。脚本扫描 `wiki/tickers/*/overview.md` 里的标的，按硬规则剔除不合格的（Avoid、低于 MA200、财报在到期日前、买卖价差过宽、缺 IV 等），给每个入选 ticker 选出一份 Put 合约（默认**周度**：距快照日 7–14 天内最近的到期日，含周度合约；`--dte monthly` 则取月度，优先 30–45 天；行权价取 Delta 最接近 0.20 的虚值 Put），保存说明文件；用户挑几只记入私有账本，之后用 `status` 看浮盈亏、到期自动结算，用 `report.md` 的"按策略对比"把 Sell Put 和 Bull Put Credit Spread 放在一起比。

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes anything under `outputs/` or `wiki/`. 所有文件都在 `data/paper-trading/`（gitignored）。账本和报告与 `/spread-sim` 共用，见 `.claude/commands/spread-sim.md`。

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "筛一遍 Sell Put" / "今天有哪些 put 可以卖" / "月度的" | `suggest [--dte weekly\|monthly] [--tickers A,B]` — 默认 weekly；标的来源按顺序取第一个非空的：`--tickers` > `wiki/tickers/*/overview.md` > `data/watchlist.txt`（每行 `代码 [🏦|🚀|⚡] [watch|avoid|under review]`，`#` 注释）；都没有则退出码 2、不写文件。不写账本。用户明确要月度才传 `--dte monthly` |
| open | "ZZZQ 开一张" / "ZZQB 2 张" | `open TICKER [--contracts N]` — 记录已保存的候选，默认 1 张 |
| status | "看一下模拟盘" / "周中检查" | `status` |
| close | "提前平仓 #3" | `close ID` |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/put_screen.py <action> ...
```

## 复盘与每周检查

- `/option-review` 对**两种策略**（Sell Put 和价差）的每笔未平仓持仓都给建议，每周检查就在那里看。
- `/option-journal` 把记录下来的建议和最终结果对照（复盘）。
- 两者读的是同一个账本，不属于 `/put-screen` 本身；本命令只负责筛选、开仓、平仓、看盈亏和结算。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest

1. Run `suggest`. It fetches many tickers, so it can take a while; wait for it to finish. Market-data failures on one ticker become a reject line for that ticker, not an abort.
   **Data source:** option expirations, quotes, IV and Delta come from the configured data source (`ibkr`, `yahoo` or `demo`; settings, quality and quirks in `docs/option-data-sources.md`). The summary's first lines name the source and its data quality: relay them (Yahoo and demo runs open with a warning banner). IBKR mode is read-only delayed data: after hours and on weekends it serves delayed-frozen data, labelled with the snapshot day, and it can run at any time, not only during US market hours. Yahoo mode is delayed about 15 minutes and after the close bid/ask are often empty (last trade used, flagged stale), so run it during US market hours. Moving averages, earnings dates and the stock history always come from Yahoo. Compare a candidate's strike / Delta / premium with your broker's option chain before opening it.
   **IBKR settings (IBKR mode only):** `data/ibkr.env` (gitignored) holds `IBKR_HOST`, `IBKR_PORT`, `IBKR_OPTIONS_CLIENT_ID` and `IBKR_MARKET_DATA_TYPE`; never print or commit their values.
   **Data-source failures abort the run.** If the source is unreachable (IBKR gateway down or dropping mid-run, Yahoo offline or rate-limited) `suggest` exits with a non-zero code and writes no files; there is no fallback to another source. Relay the message. In IBKR mode, if a ticker is rejected with the text of error 10197, a live TWS or phone session on the same IBKR account is stealing market data: ask the user to log that session out (or turn off market data sharing) and re-run.
2. Read the summary `data/paper-trading/put-screen/<快照日>.md` and show it: the candidate tables by tier (🏦 蓝筹长持 / 🚀 翻倍候选 / ⚡ 热赛道动量 / 未分类), the 被剔除 section (ticker + reason), and the 状态无法识别 list. Relay **every warning** the file carries (财报日期未知、报价陈旧、低于 MA50、到期日在偏好窗口之外、论点复核中、档位在多个赛道页中不一致 etc.) and explain briefly. If the script says 本日无合适周度到期 (or 本日无合适月度到期 in monthly mode), say that and stop. Names without weekly contracts are rejected with 没有可用的周度到期; that is expected. Do not add trade recommendations beyond what the file flags.
3. Tell the user which kind of snapshot it is (the file's header says): after the close or on a weekend it is the last session (IBKR: delayed-frozen data; Yahoo: the last close) labelled with its snapshot day (bid/ask may be empty, then the last trade price is used and flagged stale); during market hours it is a **盘中快照** with live quotes that will move by the close. Either way the next session's open can differ — that is why every record keeps `monday_gap_pct` (the gap from the snapshot price to the next trading day's open).
4. Point out the fixed risk lines in the output: in weekly mode, 周度合约权利金小、对股价变动更敏感（Gamma 高），到期前一周的波动比月度大; 🚀 / ⚡ 是高 Beta 标的，来源建议单只不超过总资金 10%–15%，只作收益增强；年化收益率是**毛收益**，不含被指派后的下跌损失；占用现金 = 行权价 × 100 每张，被指派后最坏情况（股价归零）就是这个数减权利金。
5. 每个入选 ticker 还有一份逐只说明 `data/paper-trading/suggestions/<快照日>-<TICKER>-put.md`（为什么选这个合约：目标 Delta 对实际 Delta、行权价离现价多少、盈亏平衡价、被指派的条件与损失、该股自己的风险提示）和一份 `-put.json` 孪生数据，`open` 读的是后者。**同一天换模式重跑会覆盖**：文件名不带模式，最后一次 `suggest` 为准；`open` 又会拒绝最新汇总没有选中的 ticker（既有行为），所以换模式后要重新看汇总。同一天重跑会先删掉当天旧的 `-put` 文件再重写（价差的 `suggestions/<日期>-<TICKER>.*` 不动）。Tell the user the path, and summarise a candidate's file in a few lines on request; do not paraphrase numbers differently from the file.
6. Ask which tickers to open, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open TICKER [--contracts N]` for each chosen ticker. It does **not** fetch the market: it records the contract from the latest saved `suggestions/<日期>-<TICKER>-put.json`, so the trade is exactly what the user reviewed. The output line shows `快照 YYYY-MM-DD 收盘/盘中，候选 <file>.md`, the premium per share and the cash it ties up; read it back. The script refuses if there is no saved suggestion, if its expiry has passed, if the saved file is damaged, or if the newest `put-screen/<日期>.json` summary (not older than the candidate) rejected or did not select that ticker — relay the message and, if needed, re-run `suggest`.
2. Same ticker + same expiry is refused, **even if the existing record is a Bull Put spread** (duplicates count across strategies). Relay the message; do not work around it.
3. Report the recorded line and any warnings the script prints.

### 3. status

1. Run `status`. It is shared with `/spread-sim`: it marks every open trade (spreads and puts together) at current mid prices, prints 🟢 已到止盈线 / 🔴 已到止损线 tips (for a Sell Put the tip spells out its basis: take-profit = unrealized profit ≥ 50% of the premium, stop = unrealized loss ≥ 2× the premium; spreads keep their own wording), and settles trades past their expiry day automatically. On the expiry day itself it says to re-run the next day or later. **Flags are advice only — never close anything yourself.**
2. 到期结算：收盘价 ≥ 行权价 → 权利金全收；收盘价 < 行权价 → 记为 assigned，已实现盈亏 = (权利金 − (行权价 − 到期收盘价)) × 100 × 张数。**只记录到到期日**，被指派之后怎么处理股票（持有、Wheel、止损）不在模拟范围内。
3. Summarise: per trade P&L and distance to the strike, which trades hit a flag, anything settled. Point to `data/paper-trading/report.html` (friendly page) and `report.md` (same data as text).

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early. It books the current mid-price buyback as the exit.

### 5. Reading the report

`data/paper-trading/report.md` is regenerated after every `open` / `status` / `close`. When asked for results, read it and report the **按策略对比** section: for each strategy (垂直 Put 贷方价差 / Bull Put Spread vs 现金担保 Put / Cash-Secured Put) the settled count, win rate, average win / loss, break-even win rate computed from that strategy's actual average win/loss, and total P&L, plus the sample-size warning (< 30 settled trades means no conclusions). Win = realized P&L > 0. Only the 现金担保 Put sections of the report carry a 策略 column; the 垂直 Put 贷方价差 tables are unchanged.

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps `spreads.json.bak` before every write. If the script reports a corrupt ledger, stop and show the user the message — do not overwrite.
- 默认 weekly 的原因：月度 30–45 天的 Sell Put 一个月才结算，纸上交易样本积累太慢，也没法和约 7 天的 Bull Put 价差公平比较；账本记录里的 `dte_mode` 字段标明每笔是周度还是月度。
- Dollar figures are per contract ×100, mid-price fills, no commissions or fees.
- Results run slightly optimistic: the real market cannot fill at the snapshot price. Say so when the user compares to live trading.
- 同一 ticker + 同一到期日不能重复开仓，跨策略也算。
- 被指派的仓位只记录到到期日；管理股票之后的走势不在范围内。
- 模拟/研究用途，非投资建议。
