# Value Tracker Config Spec v0.1

## 1. 目标

Value Tracker 使用一个主配置文件：

```text
config/stockhunt.yaml
```

目标：

- 修改简单。
- 能直接重跑历史指标和模拟盘。
- 所有配置都有版本号。
- 静态 JSON 中记录配置版本，保证结果可复现。

## 2. 完整示例

```yaml
version: "2026-06-01-v1"

data:
  timezone: "Asia/Ho_Chi_Minh"
  currency: "USD"
  market: "US"
  symbol_format: "longbridge"
  output_dir: "dist"

institutions:
  whitelist_version: "2026-06-01-whitelist-v1"
  managers:
    - cik: "0001709323"
      name: "Himalaya Capital Management LLC"
      display_name: "李录"
      enabled: true
      style: "value"
      notes: "Concentrated value manager"

    - cik: "0001759760"
      name: "H&H International Investment, LLC"
      display_name: "段永平"
      enabled: true
      style: "value"
      notes: "Concentrated value manager"

    - cik: "0001067983"
      name: "Berkshire Hathaway Inc"
      display_name: "伯克希尔"
      enabled: true
      style: "value"
      notes: "Buffett / Berkshire"

key_institutions:
  version: "2026-06-01-key-v1"
  members:
    - cik: "0001759760"
      display_name: "段永平"
      enabled: true
    - cik: "0001709323"
      display_name: "李录"
      enabled: true
    - cik: "0001067983"
      display_name: "伯克希尔"
      enabled: true

rankings:
  limit: 500
  homepage_display_limit: 100
  top_n_for_simulation: 10
  definitions:
    institutional_buying:
      title: "机构买入榜"
      sort:
        - "total_bought_value_usd desc"
        - "buyers_count desc"
        - "new_positions_count desc"
        - "holders_count desc"
        - "market_cap_usd asc"
        - "symbol asc"
    institutional_selling:
      title: "机构卖出榜"
      sort:
        - "total_sold_value_usd desc"
        - "sellers_count desc"
        - "exits_count desc"
        - "holders_count desc"
        - "market_cap_usd asc"
        - "symbol asc"
    institutional_holding:
      title: "机构持有榜"
      sort:
        - "total_tracked_value_usd desc"
        - "holders_count desc"
        - "buyers_count desc"
        - "sellers_count asc"
        - "market_cap_usd asc"
        - "symbol asc"

company_investors:
  version: "2026-06-22-company-investors-v1"
  members:
    - cik: "0001045810"
      symbol: "NVDA.US"
      name: "NVIDIA Corp."
      display_name: "NVIDIA"
      display_name_zh: "英伟达"
      enabled: true
    - cik: "0000050863"
      symbol: "INTC.US"
      name: "Intel Corporation"
      display_name: "Intel"
      display_name_zh: "英特尔"
      enabled: true

strategy:
  version: "2026-06-01-strategy-v1"
  id: "institutional_signal_weekly"
  rebalance:
    frequency: "weekly"
    weekday: "monday"
    if_market_closed: "next_trading_day"
    price: "close"
  lookback_trading_days: 21
  max_positions: 10
  min_position_weight_pct: 5
  max_position_weight_pct: 50
  if_no_candidates: "hold_existing_positions"
  cash_for_unused_weight: true
  transaction_costs:
    dividends: false
    fees: false
    slippage: false
  allocation_score:
    buying_top10_score: 10
    holding_top10_score: 10
    below_institution_avg_score: 20
    buying_top10_key_institution_bonus: 10
    holding_top10_key_institution_bonus: 10
    selling_top10_penalty: -50

tags:
  index_tags:
    - "S&P 500"
    - "Nasdaq 100"
    - "Russell 1000"
    - "Russell 2000"
    - "Russell 3000"
  theme_tags:
    Mag7:
      symbols:
        - "AAPL.US"
        - "MSFT.US"
        - "GOOGL.US"
        - "AMZN.US"
        - "NVDA.US"
        - "META.US"
        - "TSLA.US"

defaults:
  display:
    missing_number: null
    missing_text: "--"
  ranking:
    missing_price: "exclude"
    missing_market_cap: "rank_last"
    missing_pe: "rank_last"
    missing_forward_pe: "rank_last"
    missing_ps: "rank_last"
  market_data_failure:
    use_last_successful_snapshot: true
    mark_stale: true
    max_stale_days: 5
    if_no_snapshot: "exclude_from_rankings_and_simulation"

build:
  keep_previous_site_on_failure: true
  write_quality_issues: true
  export_json: true
```

可选现金披露文件：

```text
config/institution-cash.yaml
```

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

该文件会在 `fetch` / `fetch-all` / `schedule weekly` 的 live raw input 阶段自动合并。现金只展示已披露值；缺失时显示“未披露”。

## 3. 修改配置后的重跑规则

### 3.1 修改白名单

例如新增或禁用 `institutions.managers`。

需要重跑：

```text
fetch-all
build
```

### 3.2 修改重点机构

例如修改 `key_institutions.members`。

需要重跑：

```text
fetch
build
```

不需要刷新全部 cache，除非新增的重点机构还没有历史 13F 原始数据；这种情况用 `fetch-all`。

### 3.3 修改策略参数

例如修改权重上下限、观察窗口、分数规则。

需要重跑：

```text
fetch
build
```

如果修改会影响 SEC 解析、CUSIP 映射或历史价格口径，则使用：

```text
fetch-all
build
```

### 3.4 修改公司型持股机构

例如修改 `company_investors.members`。这些公司不会计入白名单机构分母；如果公司本身披露 13F，则会在首页作为“公司型持股机构”展示它持有哪些上市公司，用于梳理战略投资链条。需要重跑：

```text
fetch-all
build
```

## 4. 配置 hash

构建时需要计算整个 `stockhunt.json` 的 hash，并写入：

- `raw/generated/snapshot.yaml`
- `raw/generated/historical/`
- `data/stockhunt.json` metadata

这样同一份历史 13F 数据可以用不同配置重复回测。
