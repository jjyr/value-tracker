# Value Tracker Backend Pipeline v0.2

## 目标

后台保持三层：

1. 原始接入层：SEC 13F、Longbridge、指数成分等 provider 只负责生成原始事实或更新 cache。
2. 规范化层：比较最近两个 13F 报告期，生成当前机构变化和股票指标快照。
3. 静态导出层：把 snapshot 和历史模拟盘转成 Hugo 使用的 `data/stockhunt.json`。

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
  --hugo-output /private/tmp/stockhunt.json
```

完整项目命令：

```bash
uv run fetch
uv run fetch-all
uv run schedule daily
uv run schedule weekly
uv run schedule weekly --force
uv run build
```

## 输入格式

当前 sample 输入位于 `raw/sample/13f_holdings.yaml`，字段分为：

- `market`：候选股票的价格、市值、估值、指数标签。
- `filings`：每个 13F filing 的基础信息和 holdings 明细。
- `cash_disclosures`：可选。独立披露的机构现金或现金等价物，不从 13F 推导。
- `latest_13f_report_period` / `previous_13f_report_period`：用于计算增持、减持、新建仓、清仓。

真实 provider 只要生成同样结构，后面的指标计算和 Hugo 导出可以复用。

`cash_disclosures` 示例：

```yaml
cash_disclosures:
  - cik: "0001067983"
    report_period: "2026-03-31"
    as_of_date: "2026-03-31"
    filing_date: "2026-05-03"
    cash_value_usd: 334000000000
    cash_label: "Cash, cash equivalents and short-term Treasury Bills"
    source_type: "10-Q"
    source_url: "https://www.sec.gov/Archives/..."
    confidence: "reported"
```

现有定时任务在 live raw input 阶段默认合并 `config/institution-cash.yaml`。没有对应报告期现金披露时，前端显示“未披露”，不会把 13F 之外的资产假设为现金。

## 产物

```text
raw/generated/snapshot.yaml                当前机构变化和股票指标快照
raw/generated/historical/                  2024 至今模拟盘 JSONL store
raw/generated/cache/sec/                   SEC submissions / filing cache
raw/generated/cache/longbridge-kline/      Longbridge 日 K 线 cache
data/stockhunt.json                        Hugo 消费的站点数据
content/en/**, content/zh/**               机构和股票详情页内容入口
```

## 增量策略

- 当前 Longbridge live raw input 在任务进程内生成，不作为标准产物落盘。
- live raw input 会合并 `config/institution-cash.yaml` 中的现金披露；`schedule weekly` / `fetch` / `fetch-all` 重建 snapshot 时生效。
- 当前 snapshot 每次从 raw input 和配置纯内存重算。
- SEC submissions index 会刷新，用于发现新增 13F。
- 已有 SEC filing index / information table XML 继续复用 cache。
- 未在 `config/cusip-symbols.yaml` 映射的 CUSIP，会用 Longbridge US security-list 按 issuer name 自动匹配 symbol；成功匹配会追加写回本地映射表，后续直接复用。
- Longbridge 日 K 线按 symbol 追加缺失日期。
- 历史回测写入 `raw/generated/historical/`：元数据用 JSON，大数组用 JSONL。
- `fetch` / `schedule daily|weekly` 会尝试从 checkpoint 增量续算；配置或 CUSIP 映射 hash 变化时自动全量重算。
- 13F 指纹变化时，`dirty_from` 取最早变动 13F 的 `filing_date`；没有 13F 变化时，只重算最新 equity point 之后的尾部。

## 下一步接入

1. Index tag adapter：维护 S&P 500、Nasdaq 100、Russell 1000/2000/3000、Mag7 标签。
2. 数据质量报告：展示未映射 CUSIP、自动映射 CUSIP、缺失价格、退市股票和 13F value 单位修正。
3. 机构详情页历史变动图表。
