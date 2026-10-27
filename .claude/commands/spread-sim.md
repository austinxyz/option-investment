# spread-sim

现金担保 Put 的纸上交易与本命令共用同一个账本和报告，见 `/put-screen`。

Paper-trade Bull Put Credit Spreads. On any trading day (or the weekend) the user names a ticker; this command proposes three candidates (delta ≈ 0.10 / 0.15 / 0.20) expiring about 7 days after the snapshot day (the first listed expiry at least 7 days out; the file says when it is not exactly 7); the user picks one; the trade is recorded in a private ledger; during the week the user checks mark-to-market and may close early, otherwise it settles at the expiry-day close and the P&L is logged. 2–3 trades per week, accumulated, to test whether the real win rate beats the break-even win rate (75%–86%).

**Paper trading only.** Never touches `data/positions.md`, never places an order, never writes anything under `outputs/`. Ledger and report live in `data/paper-trading/` (gitignored). The one exception under `wiki/`: every `status` run (and any open/close once nothing is left open without marks) also refreshes `wiki/options/paper-trading.md`, a shareable markdown copy of the report with front matter. It withholds every strategy tied to real shares (covered calls, protective puts, collars) via an allowlist, and carries no positions, shares or cost.

## Arguments

Natural language or explicit. Parse into one of four actions:

| Action | Example input | Script call |
|--------|---------------|-------------|
| suggest | "选票 ZZZQ ZZQB"（任意交易日都行） | `suggest TICKER [--width W]` — once per ticker; omit `--width` to get the recommended one |
| open | "ZZZQ 选 0.15 档" / "ZZQB 0.10，2 张" | `open TICKER {0.10,0.15,0.20} [--contracts N] [--width W]` |
| status | "看一下模拟盘" / "周中检查" | `status` |
| close | "提前平仓 #2" | `close ID` |

All calls run from the repo root:

```bash
./.venv/Scripts/python.exe scripts/spread_sim.py <action> ...
```

## 怎么开仓和平仓

1. **开仓分两步，必须先出候选。** 先 `suggest TICKER`（如"选票 ZZZQ ZZQB"），看三档候选；再 `open TICKER {0.10,0.15,0.20}`（如"ZZZQ 选 0.15 档" / "ZZQB 0.10，2 张"，对应 `--contracts N`）。
2. `open` 记录的是该 ticker **最近一次保存的候选**，不重新取行情；没有保存的候选、候选已过期、`--width` 与保存的不一致，都会拒绝，需要重新 `suggest`。同一 ticker + 同一到期日已有记录（不论哪种策略）也会拒绝。
3. **提前平仓**：`close ID`（如"提前平仓 #2"），按当前中间价买回记账；到期日之后会被拒绝，这时运行 `status` 按到期收盘价结算。

## 复盘与每周检查

- `/option-review` 对**两种策略**（Put 贷方价差和现金担保 Put）的每笔未平仓持仓都给建议，价差的每周检查就在那里看。
- `/option-journal` 把记录下来的建议和最终结果对照（复盘）。
- 两者读的是同一个账本，不属于 `/spread-sim` 本身；本命令只负责开仓、平仓、看盈亏和结算。

## Steps

### 0. Resolve output language

Read `data/config.md` and extract the `lang` field (default `zh`). If `--lang zh|en` was passed, use that. Respond in that language. The script's own output is Chinese; relay it faithfully, do not re-derive numbers.

### 1. suggest (any day)

1. Run `suggest` for each ticker the user named (normally 2–3).
2. Show each result table as printed. Do not add trade recommendations beyond what the script flags; point out the warnings (earnings in window, wide bid-ask, stale quotes, thin credit) and explain them briefly.
3. Tell the user which kind of snapshot it is (the file's 基本信息 says): after the close or on a weekend it is the last close and bid/ask are often empty (last trade price used, flagged stale); during market hours it is a **盘中快照** with live quotes that will move by the close. Either way the next session's open can differ — that is why every record keeps `monday_gap_pct` (despite the name: the gap from the snapshot price to the next trading day's open). If the expiry is not exactly 7 days (the underlying has no listing exactly 7 days out, e.g. Friday-only names), say so.
4. **Width and capital.** Without `--width`, `suggest` picks the width itself: about 15% of the one-sigma expected move over the horizon, rounded to the chain's strike step (at least one step, at most 5% of spot). The header and the file's "为什么是这个宽度" section show the working; with an explicit `--width` the file also shows what would have been recommended. Width barely changes the odds (the short strike is set by delta, so the chance of being breached is the same; credit/width only drifts down slightly as width grows) — it mainly changes the dollars per contract and the share eaten by bid-ask and commissions, so explain it that way. The table's **占用资金/张** is the capital one contract ties up: the broker freezes width×100, the credit received comes back, so the net is the max loss. Give the user the figure for the bucket they are considering; do not guess their account size.
5. `suggest` also saves `data/paper-trading/suggestions/<snapshot date>-<TICKER>.md` (a per-bucket walkthrough of *why* these strikes: target vs actual delta, distance in expected moves, break-even win rate, win condition, warnings, the trade-off between buckets) and a `.json` twin that `open` reads. A rerun on the same day overwrites them. Tell the user the file path and summarise the walkthrough in a few lines; do not paraphrase numbers differently from the file.
6. Ask which bucket to open for each ticker, or none. Nothing is written to the ledger until `open`.

### 2. open

1. Run `open`. It does **not** fetch the market: it records the candidate from the saved `suggestions/<date>-<TICKER>.json` that `suggest` wrote (the latest one for that ticker), so the trade is exactly what the user reviewed, even if `open` happens after the weekend. The output line shows `快照 YYYY-MM-DD 收盘，候选 <file>.md`; read it back. The script refuses if there is no saved suggestion, if it is expired, or if `--width` disagrees with it — relay the message and, if needed, re-run `suggest`.
2. Same ticker + same expiry week is refused by the script; relay the message, do not work around it.
3. Report the recorded line (id, strikes, credit, max profit/loss) and any warnings.

### 3. status (mid-week, and after expiry)

1. Run `status`. It marks every open trade at current mid prices and flags the 50% stop line (loss ≥ 50% of max loss) and 50% take-profit line (profit ≥ 50% of credit). **Flags are advice only — never close anything yourself.**
2. Trades past their expiry day are settled automatically at the expiry-day close; on the expiry day itself it says to re-run the next day or later (settlement only happens once today is past the expiry date).
3. Summarise: per trade P&L and distance to the short strike, which trades hit a flag, anything settled. Point to `data/paper-trading/report.html` (the friendly page: summary KPIs, one card per open trade with a P&L bar marking the 50% stop / take-profit lines and a payoff chart with the current price, settled table, stats) and `report.md` (same data as text). Both are regenerated by every `open` / `status` / `close`; only `status` has live marks, the others show "运行 status 查看". If the user asks to "see" or "show" the paper book, send them `report.html`.

### 4. close

Run `close ID` only when the user explicitly decides to end a trade early. It books the current mid-price cost as the exit.

### 5. Reading the report

`data/paper-trading/report.md` is regenerated after every `open` / `status` / `close`. When asked for results, read it and report: win rate vs. the break-even win rate computed from actual average win/loss, the per-delta-bucket comparison, and the sample-size warning (< 30 settled trades means no conclusions). Win = realized P&L > 0.

## Rules

- Never edit `data/paper-trading/spreads.json` by hand; the script keeps `spreads.json.bak` before every write. If the script reports a corrupt ledger, stop and show the user the message — do not overwrite.
- Dollar figures are per contract ×100, mid-price fills, no commissions.
- Results run slightly optimistic: the real market cannot fill at the snapshot price. Say so when the user compares to live trading.
- Not investment advice; simulation only.
