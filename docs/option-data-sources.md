# 期权数据源

期权工具（`/spread-sim`、`/put-screen`、`/call-screen`、`/collar-screen`、`/condor-sim`、`/bullcall-sim`、`/option-review`、`/paper-book`、`/strategy-pick`）的期权数据（到期日、买卖报价、隐含波动率、Delta）来自**当前数据源**。数据源有三种，用同一套规则和同一套命令，差别只在数据从哪来、质量多高。均只读行情，不下单。

## 三种数据源

| 名字 | 数据从哪来 | 数据质量 | 要装什么 | 账本目录 |
|---|---|---|---|---|
| `yahoo` | Yahoo Finance 免费行情（`yfinance`） | 低：延迟约 15 分钟，Delta 是本地估算 | `requirements.txt` | `data/paper-trading` |
| `ibkr` | IBKR 模拟网关（IB Gateway 的模拟账户），只读 | 较好：券商行情（延迟数据），Delta 由网关给 | `requirements-ibkr.txt`、`data/ibkr.env` | `data/paper-trading` |
| `demo` | 内置的离线演示行情，**全部虚构**（ZZZQ、ZZQB、ZQIC） | 演示，不代表任何真实行情 | 不要网络 | `data/paper-trading-demo` |

`demo` 的账本单独放，不会混进真实账本。`yahoo` 和 `ibkr` 共用 `data/paper-trading`；账本里每一笔记着它是按哪个数据源开的，报告里会提示"其中 N 笔按 Yahoo 数据开仓（数据质量低，结果偏乐观）"，复盘时样本里混了不同数据源也会写出来。

不管用哪个数据源，移动平均线、财报日期、除息日和一年日线都来自 Yahoo。

## 怎么设置

三层设置，优先级从高到低：

1. **环境变量** `OPTION_DATA_SOURCE=ibkr|yahoo|demo`：只对这一次启动有效。PowerShell 里写 `$env:OPTION_DATA_SOURCE = "demo"`，再启动 Claude Code。
2. **`data/config.md` 里的一行** `option_data_source: yahoo`：长期设置。例子见 `examples/config.md`。
3. **`scripts/option_source_default.py` 里的 `DEFAULT_SOURCE`**：上面两处都没写时用它。一般不用改。

写了不认识的值（比如 `option_data_source: yhoo`）会直接报错并说明可选值，不会悄悄换一个数据源。每次运行读一次设置，改完不用重启，下次运行就生效。

## Yahoo 模式

Yahoo 是免费数据，比券商行情粗糙。所有报告标题下第一段都有这样一行横幅：

> ⚠️ 数据来源：Yahoo Finance（延迟约 15 分钟，非券商报价）。数据质量：低。Delta 为本地 Black-Scholes 估算；休市时用最后成交价（标"陈旧"）；买卖价差门槛放宽到 10%。按中间价成交、不含手续费，模拟结果偏乐观。

### 价差规则

买卖价差 ÷ 中间价，超过一条线的合约不能用。Yahoo 报价比券商粗，所以线放宽了：

- IBKR 门槛是 5%：超过就剔除，不放行。
- Yahoo 和演示模式的硬线是 10%：价差超过 10% 的合约被剔除。
- 警告线是 5%：价差在 5%–10% 之间的放行，报告里写一句"买卖价差 x%，超过 IBKR 模式的 5% 门槛（Yahoo 报价延迟，放宽到 10%）"。

### 陈旧报价与休市

- 休市后 Yahoo 的 bid/ask 常常是 0。这时那一行只保留最后成交价，标"陈旧"，价差算不出来，只警告、不因价差剔除。
- 最后成交早于快照交易日的"陈旧"报价，在筛选窗口里直接忽略，报告里写"N 档报价只有过期的最后成交价，已忽略"。你明确要求的合约（比如已开仓的腿）不会被忽略，只会标陈旧。
- 休市时整个到期日可能都拿不到买卖报价，报告写"没有拿到 {标的} {到期日} 的期权报价（Yahoo 休市后常没有买卖报价）"；休市期间运行时还会提示"休市中：Yahoo 的 bid/ask 常为 0、IV 不可靠，很多标的会被剔除；建议美东 9:30–16:00 运行"。
- 所以 Yahoo 模式**建议在美东 9:30–16:00 运行**。IBKR 模式则任何时段都能跑：休市时给延迟冻结的上一交易日数据，输出按快照日标注。
- 模拟按中间价成交、不含手续费，真实很难拿到中间价，结果偏乐观。

### 限流

Yahoo 会限制同一个网址短时间内的大量请求，所以脚本放慢了节奏：

- 每两次请求之间至少间隔 0.4 秒。扫 30 只标的的 `/put-screen` 因此多等约 40 秒。
- 被限流（HTTP 429、"Too Many Requests"）时先退避 5 秒再重试一次；再被拒就认为这次运行被 Yahoo 拒绝，整次运行中止。
- 连续 3 只标的出现同一类认不出的异常时，也整次中止，并提示"Yahoo 可能改版"，而不是交出一份全是剔除行的汇总。

这三个数不用你调；它们写在 `scripts/yahoo_data.py` 顶部。

### 标的代码的写法

Yahoo 的写法就是你要写的写法。带点号的代码用连字符：伯克希尔 B 类写 `BRK-B`，不是 `BRK.B`（IBKR 那边才用点号）。工具**不做**两种写法之间的转换，写错了会得到"没有期权"或找不到行情。观察名单 `data/watchlist.txt` 里也照这个写。

## IBKR 模式

适合有盈透（IBKR）账户、想用券商行情的人。

1. `pip install -r requirements-ibkr.txt`（缺 `ib_async` 时会提示"IBKR 模式需要先 pip install -r requirements-ibkr.txt"）。
2. 复制 `examples/ibkr.env.example` 到 `data/ibkr.env`，设 `IBKR_HOST`（IB Gateway 所在机器，同一台电脑就是 `127.0.0.1`）、`IBKR_PORT`（IB Gateway 的**模拟账户**默认 4002）、`IBKR_OPTIONS_CLIENT_ID`（给本工具专用的 client id，别和别的连接重复）、`IBKR_MARKET_DATA_TYPE`（默认 4 = 延迟冻结，盘中也可以设 3 = 延迟）。环境变量优先于这个文件。**不要把这些值写进文档、日志或提交。**
3. IB Gateway 登录**模拟账户**，打开 API 连接。
4. 设 `option_data_source: ibkr`。

要点：

- **只读、只用延迟数据**，代码里没有下单接口。
- **网关连不上（或中途掉线）**：整次运行中止，退出码非 0，不写任何文件，**不会回退到 Yahoo**。恢复网关后重跑。
- **竞争的实盘会话（错误 10197）**：同一个 IBKR 账户的实盘会话（手机 App 或 TWS）会抢走行情，模拟网关就拿不到数据。症状是整只标的没有报价，错误信息里写着 10197。处理：退出实盘会话，或在账户设置里关闭行情共享，再重跑。
- 休市后有报价但没有 greeks 的合约，行会保留但 IV 和 Delta 为空，复核里记为"数据陈旧"，筛选不会拿它当卖出腿。
- 单只标的的链约十几秒，整表扫描要几分钟，请耐心等。

## 演示模式

没有账号、没有网络、不在开盘时间，也想把整套流程走一遍，就用 `demo`：

- 只有三只虚构标的：ZZZQ、ZZQB、ZQIC。用真实代码会提示"演示数据只有虚构标的"。
- 行情是公式算出来的，同一天同样的命令每次结果一样，所以文档里的例子可以照着复现。
- 想固定演示里的"今天"，设环境变量 `OPTION_DEMO_TODAY=YYYY-MM-DD`（例如 `$env:OPTION_DEMO_TODAY = "2026-10-23"`）。日期太早时一年日线不够长，部分指标算不出来。
- 账本在 `data/paper-trading-demo/`，用完删掉这个目录即可，不影响真实账本。
- 报告第一段是"演示数据（虚构），不代表任何真实行情"。

## 常见报错

| 看到什么 | 意思 | 怎么办 |
|---|---|---|
| `连不上 Yahoo Finance：…` | 网络或代理有问题 | 检查网络、代理设置，重试 |
| `被 Yahoo 限流，请稍后再试` | 退避重试后仍被拒 | 等几分钟再跑，少扫几只（`--tickers`） |
| `{标的} 没有期权` | 这只标的在 Yahoo 没有期权 | 换一只；检查代码写法（如 `BRK-B`） |
| `没有可筛的标的：…` | 没给标的，也没有观察名单 | 用 `--tickers`，或照 `examples/watchlist.txt` 建 `data/watchlist.txt` |
| `演示数据只有虚构标的` | 演示模式下用了真实代码 | 用 ZZZQ / ZZQB / ZQIC，或换回别的数据源 |
| 错误 10197 | 实盘会话抢了行情（IBKR 模式） | 退出实盘会话后重跑 |
| `option_data_source` 报"只能是 ibkr / yahoo / demo" | 设置写错了 | 改成三个值之一 |
