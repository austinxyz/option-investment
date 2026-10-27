# 设置示例

复制到 `data/config.md` 后按需修改（`data/` 不会被提交）。不建这个文件也能跑，下面都是默认值。

lang: zh
option_data_source: yahoo

可选的数据源：`yahoo`（默认）、`demo`（离线演示，虚构标的）、`ibkr`（需要 IBKR 模拟网关和 `data/ibkr.env`）。环境变量 `OPTION_DATA_SOURCE` 优先于这里。

下面这行只给 `/strategy-pick` 排序用（组合总值，单位美元）；需要时去掉行首的 `# `。

# portfolio_value: 100000
