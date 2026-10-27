# option-investment：期权纸上交易工具

## 这是什么

一套在 Claude Code 里用的期权**纸上交易**工具，加上一组期权策略笔记。

- 你用斜杠命令（比如 `/spread-sim`）让 Claude 去拿期权行情、按固定规则挑合约、记一本假想的账、到期自动结算、每周给建议、攒够样本后复盘。
- 默认数据来自 Yahoo Finance，不需要券商账户。
- **不下单，不连券商**（可选的 IBKR 模式也只读行情，不发单）。
- **不构成投资建议。** 这是练手和验证规则用的工具，不是荐股。

## 里面有什么

**策略笔记**（`wiki/options/`）：

- `knowledge.md`：期权知识总结，入口页是 [wiki/options/index.md](wiki/options/index.md)。
- `strategies/`：9 篇策略页（Bull Put 价差、现金担保 Put、备兑 Call、Wheel、保护性 Put / Collar、铁鹰、Bull Call 价差、买入 Call / Put、裸卖 Call），讲概念、规则和风险。
- `sources/`：5 篇视频来源的个人总结（版权说明见下文）。

**10 个命令**（详细用法见 [docs/options-skills-guide.md](docs/options-skills-guide.md)）：

| 命令 | 做什么 |
|---|---|
| `/strategy-pick` | 不知道用哪个策略时先跑它：按你的看法、走势和波动率推荐 1–2 个策略和下一步命令，只建议不开仓 |
| `/spread-sim` | Bull Put 价差：给一只标的出三档候选，选一档开仓 |
| `/put-screen` | 现金担保 Put（Sell Put）：从你的观察名单里筛候选 |
| `/call-screen` | 备兑 Call：对假设持股出候选；也用来练 Wheel |
| `/collar-screen` | Collar / 保护性 Put：给假设持股设下限 |
| `/condor-sim` | 铁鹰（Iron Condor）：赌价格留在区间里 |
| `/bullcall-sim` | Bull Call 借方价差：净付钱的看涨策略 |
| `/paper-book` | 账本总入口：`status` 看浮动盈亏并结算到期的，`close` 提前了结，`report` 看报告 |
| `/option-review` | 每周一次：给每笔没平的单子一个建议（平仓 / 继续拿 / 需要关注） |
| `/option-journal` | 复盘：拿历次建议和最终结果比，看建议准不准 |

**数据放在哪**：所有账本、报告、筛选结果都写在 `data/` 下（第一次运行时自动建），`data/` 已在 `.gitignore` 里，不会被提交。

## 安装

下面按 Windows 写。macOS / Linux 未测试，路径换成 `.venv/bin/python`。

1. 装 Python 3.11 或更新版本（安装时勾选 "Add python.exe to PATH"）。
2. 按 Anthropic 官方文档装好 Claude Code，并登录。
3. 克隆本仓库并进入目录：

   ```powershell
   git clone <本仓库地址> option-investment
   cd option-investment
   ```

4. 建虚拟环境并装依赖：

   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   .venv\Scripts\python.exe -m pip install -r requirements-ibkr.txt
   ```

   第二条装的是 IBKR 模式的库。你不用 IBKR 也要装：测试里有一组 IBKR 相关的用例会导入它，不装会有 36 个用例报 `No module named 'ib_async'`。

5. 跑一遍测试，看到全部通过（不联网，大约一两分钟）：

   ```powershell
   $env:OPTION_TESTS_NO_NETWORK = "1"
   .venv\Scripts\python.exe -m pytest -q
   ```

6. 可选：建自己的设置和观察名单（不建也能跑，全部有默认值）：

   ```powershell
   mkdir data
   copy examples\config.md data\config.md
   copy examples\watchlist.txt data\watchlist.txt
   ```

   `data/watchlist.txt` 是 `/put-screen` 的标的来源，每行一个代码，照示例文件里的注释改成你想看的票。

**权限提示**：本项目不带 `.claude/settings.json`，所以 Claude Code 每次要运行 `python` 都会先问你是否允许。这是正常的。看清它要跑的命令后，你可以自己选"允许"，也可以选"本项目总是允许"；本项目不替你预先放行任何命令。

## 快速开始：第一笔 Yahoo 纸上交易

建议在美东时间 9:30–16:00（开盘时）运行，原因见下面"Yahoo 数据质量"。在项目目录里打开 Claude Code，然后依次：

1. **先选策略**：

   ```text
   /strategy-pick SPY --view 中性 --class etf
   ```

   `--view` 是你自己的看法（看好 / 中性 / 不看好），`--class etf` 告诉它这是 ETF。它会推荐 1–2 个策略和下一步该用的命令，报告写进 `data/strategy-picks/`（演示模式写进 `data/strategy-picks/demo/`）。

   **可能出现"门槛出局"**：Yahoo 给的未平仓量（OI）数据有时偏小。如果某只标的没过门槛，报告会写明是哪一项没过，并写"不做期权建议"，下一步指向 `stock-review` 命令。这个命令属于原作者的股票系统，本项目没有，忽略即可。通常换一个流动性更好的标的，或者换成月度到期日，重跑就能解决；也可以直接跳到第 2 步，`/spread-sim` 不依赖这一步的结果。

   运行时终端里可能夹着 `Pandas4Warning` 和 `Error 404 ... No fundamentals data` 之类的几行，是 Yahoo 库的警告和 ETF 没有财报数据，不影响结果。

2. **按它指的命令出候选**，例如 Bull Put 价差：

   ```text
   /spread-sim suggest SPY
   ```

   你会看到三档候选（Delta 0.10 / 0.15 / 0.20，越大权利金越多、风险也越大），每档写清行权价、收多少钱、最多亏多少、盈亏平衡点。说明文件写在 `data/paper-trading/suggestions/`。

3. **选一档开仓**（纸上的）：

   ```text
   /spread-sim open SPY 0.15
   ```

   记进账本 `data/paper-trading/spreads.json`，同时刷新报告 `data/paper-trading/report.html`（网页版）和 `report.md`。

4. **之后随时看**：

   ```text
   /paper-book status
   ```

   看每笔的浮动盈亏；到期的会自动按收盘价结算，不用你做什么。

5. **每周一次** `/option-review`，给每笔没平的单子一个建议；**攒到一些已结算的样本后** `/option-journal`，看这些建议准不准。

## 离线试一下（演示数据）

不在开盘时间，或者只想先看看流程，可以用演示数据。它完全离线，用虚构的标的 ZZZQ、ZZQB、ZQIC（其中 ZQIC 是 ETF），价格由公式生成，任何一天运行结果都确定。

打开方式二选一：

- 在 `data/config.md` 里写一行 `option_data_source: demo`，然后打开 Claude Code；
- 或者在 PowerShell 里先设环境变量再启动 Claude Code：

  ```powershell
  $env:OPTION_DATA_SOURCE = "demo"
  claude
  ```

然后照常用命令，只是标的换成虚构的，例如 `/spread-sim suggest ZZZQ`、`/strategy-pick ZZZQ --view 看好`。`/put-screen` 在演示模式下没有观察名单时会自动用这三只。`/call-screen` 和 `/collar-screen` 需要先有一笔假设持股，按提示先运行 `/call-screen shares add ZZZQ --shares 100 --cost 100`。

- 演示账本单独放在 `data/paper-trading-demo/`，不会和真实的 Yahoo 账本混在一起。
- 想固定演示里的"今天"，设 `OPTION_DEMO_TODAY=YYYY-MM-DD`（例如 `$env:OPTION_DEMO_TODAY = "2026-10-23"`）。日期太早时一年日线不够长，部分指标会算不出来。
- 演示数据里用真实代码会提示"演示数据只有虚构标的"。
- 用完把 `data/config.md` 那行删掉（或关掉 PowerShell 窗口）就回到 Yahoo。

## 可选：用 IBKR 模拟网关

有盈透（IBKR）账户、想用券商行情的话：

1. 装额外依赖：

   ```powershell
   .venv\Scripts\python.exe -m pip install -r requirements-ibkr.txt
   ```

   （上面"安装"第 4 步已经装过的话，这步不用重复。）

2. 复制 `examples/ibkr.env.example` 到 `data/ibkr.env`，把 `IBKR_HOST` 改成运行 IB Gateway 的机器地址（同一台电脑就保持 `127.0.0.1`），端口默认 4002（模拟账户）。
3. 在 `data/config.md` 里写 `option_data_source: ibkr`。
4. IB Gateway 要登录**模拟账户**并打开 API 连接。

注意：如果同一个 IBKR 账户的实盘会话（手机 App 或 TWS）开着，会抢走行情，脚本报错误 10197；先退出实盘会话再试。IBKR 模式同样只读行情，不下单。

## Yahoo 数据质量

Yahoo 是免费数据，比券商行情粗糙。所有报告标题下第一段都会写这样一行：

> ⚠️ 数据来源：Yahoo Finance（延迟约 15 分钟，非券商报价）。数据质量：低。Delta 为本地 Black-Scholes 估算；休市时用最后成交价（标"陈旧"）；买卖价差门槛放宽到 10%。按中间价成交、不含手续费，模拟结果偏乐观。

意思是：

- **延迟约 15 分钟**，不是实时报价。
- **Delta 是本地估算的**（Black-Scholes 公式），不是券商给的。
- **休市后 bid/ask 常是 0**：这时改用最后成交价，并标"陈旧"；太旧的报价直接忽略。所以休市时很多标的会被剔除，建议开盘时间运行。
- **买卖价差门槛放宽**：IBKR 模式下价差超过 5% 就剔除；Yahoo 模式放宽到 10%，5%–10% 之间放行但给警告，超过 10% 剔除。
- **按中间价成交、不算手续费**，真实交易很难拿到中间价，所以**模拟结果偏乐观**。

看结果时把这些打个折扣。

## 版权说明

来源笔记是视频的个人总结，版权归原作者，请到原视频观看。

## 免责声明

本项目非投资建议，不构成投资建议。纸上交易的结果不代表真实成交，过去的模拟表现不代表未来。任何真实交易请自己判断、自担风险。

## 更新与反馈

- 本仓库由生成器定期从作者的开发仓库重新生成。直接改这里的文件，会在下次更新时被覆盖；想保留的改动请告诉作者。
- 有问题或建议请开 issue。
- 报告里的券商列永远为空（本项目不下单，没有券商订单可记），可以忽略。

## License

代码和作者自己写的文字用 MIT 协议，见 [LICENSE](LICENSE)。来源总结页的版权见上面"版权说明"，不在 MIT 范围内。
