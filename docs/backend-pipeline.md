# StockHunt Backend Pipeline v0.1

## 目标

后台分三层：

1. 原始接入层：SEC 13F、Longbridge、指数成分等 provider 只负责写入原始事实。
2. 规范化层：比较最近两个 13F 报告期，生成 `holding_changes`、`metric_snapshots`、`rank_history`。
3. 静态导出层：把 normalized snapshot 转成 Hugo 使用的 `data/stockhunt.yaml`。

这样修改白名单或重点机构后，可以直接重跑规范化层和静态导出层，不需要重新解析已有 filing。

## 当前命令

使用 sample 13F fixture 生成 SQLite 和 normalized snapshot：

```bash
python3 scripts/stockhunt_backend.py --reset-db
```

默认产物写到 `raw/generated/`。不要把 SQLite 放进 Hugo 的 `data/` 目录，否则 Hugo 会尝试把 `.sqlite` / `.sqlite-journal` 当成站点数据加载。

同时导出 Hugo 数据到临时文件：

```bash
python3 scripts/stockhunt_backend.py \
  --reset-db \
  --snapshot-output /private/tmp/stockhunt-snapshot.yaml \
  --sqlite /private/tmp/stockhunt.sqlite \
  --hugo-output /private/tmp/stockhunt.yaml
```

生成站点仍然使用：

```bash
hugo --minify
```

## 输入格式

当前 sample 输入位于 `raw/sample/13f_holdings.yaml`，字段分为：

- `market`：候选股票的价格、市值、估值、指数标签。
- `filings`：每个 13F filing 的基础信息和 holdings 明细。
- `latest_13f_report_period` / `previous_13f_report_period`：用于计算增持、减持、新建仓、清仓。

后续真实 provider 只要生成同样结构，后面的 SQLite 写入、指标计算和 Hugo 导出可以复用。

## 已落库的表

当前脚本初始化并写入这些核心表：

- `config_versions`
- `institution_managers`
- `institution_config_members`
- `securities`
- `security_tags`
- `sec_13f_filings`
- `institution_holdings`
- `holding_changes`
- `market_snapshots`
- `metric_snapshots`
- `rank_history`

## 下一步接入

1. SEC adapter：按 CIK 拉取最新 `13F-HR` / `13F-HR/A`，优先解析 information table XML。
2. Longbridge market adapter：按 13F touched symbols 批量补价格、市值、PE、Forward PE、PS。
3. Index tag adapter：维护 S&P 500、Nasdaq 100、Russell 1000/2000/3000、Mag7 标签。
4. Simulation builder：用 `rank_history` 和 `metric_snapshots` 重跑每周模拟盘。
