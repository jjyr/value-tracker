# Value Tracker Backend Pipeline v0.2

## 目标

后台保持三层：

1. 原始接入层：SEC 13F、Longbridge、指数成分等 provider 只负责生成原始事实或更新 cache。
2. 规范化层：比较最近两个 13F 报告期，生成当前机构变化和股票指标快照。
3. 静态导出层：把 snapshot 和历史模拟盘转成 Hugo 使用的 `data/stockhunt.yaml`。

当前实现不维护中间数据库。修改白名单、重点机构或策略后，直接重跑规范化、回测和静态导出即可；SEC 原始文件和 Longbridge K 线通过 cache 增量复用。

## 当前命令

使用 sample 13F fixture 生成 normalized snapshot：

```bash
uv run python scripts/stockhunt_backend.py \
  --raw raw/sample/13f_holdings.yaml \
  --snapshot-output /private/tmp/stockhunt-snapshot.yaml
```

同时导出 Hugo 数据到临时文件：

```bash
uv run python scripts/stockhunt_backend.py \
  --raw raw/sample/13f_holdings.yaml \
  --snapshot-output /private/tmp/stockhunt-snapshot.yaml \
  --hugo-output /private/tmp/stockhunt.yaml
```

完整项目命令：

```bash
uv run fetch
uv run fetch-all
uv run schedule daily
uv run schedule weekly
uv run build
```

## 输入格式

当前 sample 输入位于 `raw/sample/13f_holdings.yaml`，字段分为：

- `market`：候选股票的价格、市值、估值、指数标签。
- `filings`：每个 13F filing 的基础信息和 holdings 明细。
- `latest_13f_report_period` / `previous_13f_report_period`：用于计算增持、减持、新建仓、清仓。

真实 provider 只要生成同样结构，后面的指标计算和 Hugo 导出可以复用。

## 产物

```text
raw/generated/snapshot.yaml                当前机构变化和股票指标快照
raw/generated/historical_simulation.yaml   2024 至今模拟盘
raw/generated/cache/sec/                   SEC submissions / filing cache
raw/generated/cache/longbridge-kline/      Longbridge 日 K 线 cache
data/stockhunt.yaml                        Hugo 消费的站点数据
content/institutions/*.md                  机构详情页内容入口
```

## 增量策略

- 当前 Longbridge live raw input 在任务进程内生成，不作为标准产物落盘。
- 当前 snapshot 每次从 raw input 和配置纯内存重算。
- SEC submissions index 会刷新，用于发现新增 13F。
- 已有 SEC filing index / information table XML 继续复用 cache。
- Longbridge 日 K 线按 symbol 追加缺失日期。
- 历史回测每次按当前配置重算，但底层 SEC 和 K 线读取增量 cache。

## 下一步接入

1. Index tag adapter：维护 S&P 500、Nasdaq 100、Russell 1000/2000/3000、Mag7 标签。
2. 数据质量报告：展示未映射 CUSIP、缺失价格、退市股票和 13F value 单位修正。
3. 机构详情页历史变动图表。
