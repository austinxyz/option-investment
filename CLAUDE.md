# option-investment：给 Claude 的说明

这是一套期权**纸上交易**工具和策略笔记。用户通过斜杠命令让你运行 `scripts/` 里的 Python 脚本，脚本拿行情、按规则挑合约、记假想账本、结算和复盘。命令的详细步骤在 `.claude/commands/<命令名>.md`，总览在 `docs/options-skills-guide.md`，策略笔记在 `wiki/options/`。

## 10 个命令

| 命令 | 做什么 |
|---|---|
| `/strategy-pick` | 按看法、走势和波动率推荐 1–2 个策略和下一步命令；只建议，不开仓 |
| `/spread-sim` | Bull Put 价差：三档候选、开仓 |
| `/put-screen` | 现金担保 Put：从 `data/watchlist.txt`（或 `--tickers`）筛候选、开仓 |
| `/call-screen` | 备兑 Call：对假设持股（`shares add`）出候选；Wheel 的第二步 |
| `/collar-screen` | Collar / 保护性 Put：给假设持股设下限 |
| `/condor-sim` | 铁鹰：三档候选、开仓 |
| `/bullcall-sim` | Bull Call 借方价差：三档候选、开仓 |
| `/paper-book` | 账本总入口：`status`（浮动盈亏 + 到期结算）、`close ID`、`report` |
| `/option-review` | 每周给每笔未平仓的单子一个建议，存快照 |
| `/option-journal` | 复盘：历次建议和最终结果对比 |

## 一周流程

1. 不知道用哪个策略时先 `/strategy-pick TICKER`，再用它指向的命令。
2. 用对应命令 `suggest` 看候选，用户选定后 `open`。
3. 持有期间 `/paper-book status` 看盈亏；到期的会自动结算。
4. 每周一次 `/option-review`；攒到已结算样本后 `/option-journal`。

## 规则

- **只做纸上交易**：不下单，不联系券商，不替用户做交易决定。可选的 IBKR 模式也只读行情。
- **`data/` 是私有的**：里面的账本、报告、设置不要写进 `wiki/`、`docs/` 或任何会被提交的文件，也不要在回复里大段贴出。
- **如实转述**：脚本输出是中文，照着转述，数字照抄，不要自己重算，也不要换一种算法给出不同的数字。
- **每次都转述数据源和数据质量那一行**：Yahoo 模式下每份报告开头都有"数据来源 … 数据质量：低 … 模拟结果偏乐观"的说明，演示模式下是"演示数据（虚构）"。每次回复都要把这一行告诉用户，不要省略。
- 例子一律用虚构代码（ZZZQ、ZZQB、ZQIC），不要编造用户的持仓。
- 不构成投资建议；回复里不要写成荐股口吻。

## Python 路径

命令文档里写的 `./.venv/Scripts/python.exe` 是 Windows 写法（本项目只在 Windows 上测过）。在 macOS / Linux 上换成 `.venv/bin/python`。

## 权限弹窗

本项目不带 `.claude/settings.json`：每次运行 `python` 都会弹权限确认，这是预期行为。不要试图修改权限配置（不要创建或改动 `.claude/settings.json`、`.claude/settings.local.json`），由用户自己在弹窗里决定是否允许。

## 报告里出现 `/stock-review`、`/stock-exit` 时

`/strategy-pick` 的报告（`.md`、`.json` 和同名 `.svg` 路径图）写在 `data/strategy-picks/`；路径图只在本地打开，不上传、不发布。报告和控制台会写出"带过去"的命令（选哪档、加哪些参数），转述时原样给命令；`/put-screen`、`/call-screen` 可用 `--delta`（0.05–0.40，默认 0.20）。报告在"不看好"分支和"门槛出局"（`gate_fail`，Yahoo 的未平仓量数据有时偏小，换更流动的标的或月度到期日通常可解决）里可能提到 `/stock-review`、`/stock-exit`。这两个命令属于原作者的股票系统，本项目没有。向用户解释时说：意思是"自己复核这只股票、考虑减仓"，不要去找或运行这两个命令。

## 数据源

默认 Yahoo Finance（数据质量低，见 README 的"Yahoo 数据质量"）。用户可以在 `data/config.md` 里写 `option_data_source: demo`（离线演示，虚构标的）或 `option_data_source: ibkr`（需要 IBKR 模拟网关和 `data/ibkr.env`），环境变量 `OPTION_DATA_SOURCE` 优先。不要替用户改这个设置，除非用户明确要求。
