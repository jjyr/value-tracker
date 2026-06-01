# StockHunt Static Data Contract v0.2

## 1. 目标

本文件定义静态网页消费的 JSON 数据雏形。SQLite 是主数据源，静态站点只读取导出的 JSON。

v0.2 不再导出综合评分、评级或隐藏模型字段。所有榜单和详情页都直接展示原始指标，例如：

```text
买入机构：11 / 200
卖出机构：22 / 200
当前持有：38 / 200
```

设计原则：

- 首页少请求，优先加载当前榜单和模拟盘。
- 股票详情页按需加载单个股票文件。
- 所有 JSON 都带构建版本、数据日期、指标版本和白名单版本，保证可追溯。
- 字段先保持直接可读，后续再压缩。

## 2. 文件结构

```text
dist/data/
  metadata/
    build.json
  rankings/
    institutional-buying.json
    institutional-selling.json
    new-positions.json
    exits.json
    institutional-holding.json
    small-cap-attention.json
    valuation-watch.json
    history/
      daily/
        YYYY-MM-DD/
          institutional-buying.json
          institutional-selling.json
          new-positions.json
          exits.json
          institutional-holding.json
          small-cap-attention.json
          valuation-watch.json
  stocks/
    AAPL.US.json
    BRK.B.US.json
  simulation/
    institutional-signal-weekly.json
```

## 3. 通用字段约定

### 3.1 Symbol

统一使用长桥格式：

```text
AAPL.US
BRK.B.US
TSLA.US
```

### 3.2 日期

所有日期使用 ISO 格式：

```text
YYYY-MM-DD
```

所有时间戳使用 ISO datetime：

```text
2026-06-01T12:00:00+07:00
```

### 3.3 金额和比例

- 美元金额使用 number，单位为 USD。
- 百分比使用 number，`12.5` 表示 `12.5%`。
- 缺失值使用 `null`，不使用字符串 `N/A`。

## 4. Build Metadata

路径：

```text
dist/data/metadata/build.json
```

示例：

```json
{
  "build_id": "2026-06-01-120000",
  "built_at": "2026-06-01T12:00:00+07:00",
  "data_date": "2026-05-29",
  "market_data_date": "2026-05-29",
  "latest_13f_report_period": "2026-03-31",
  "metrics_version": "0.1",
  "whitelist_version": "2026-06-01-v1",
  "key_institution_version": "2026-06-01-v1",
  "generator_version": "0.1.0",
  "status": "success",
  "warnings": [
    "12 symbols skipped because market data was unavailable"
  ]
}
```

## 5. Ranking JSON

路径示例：

```text
dist/data/rankings/institutional-buying.json
```

用途：

- 首页每个 tab 加载一个榜单。
- 每个榜单都声明排序口径。
- 前端直接展示原始指标。

MVP 默认：

- 每个榜单导出 Top 500。
- 首页默认展示 Top 100。
- 前端可按指数标签、市值区间、行业做本地筛选。

示例：

```json
{
  "meta": {
    "as_of_date": "2026-05-29",
    "ranking_type": "institutional_buying",
    "title": "机构买入榜",
    "limit": 500,
    "metrics_version": "0.1",
    "whitelist_version": "2026-06-01-v1",
    "key_institution_version": "2026-06-01-v1",
    "manager_count": 200,
    "sort": [
      "total_bought_value_usd desc",
      "buyers_count desc",
      "new_positions_count desc",
      "holders_count desc",
      "market_cap_usd asc",
      "symbol asc"
    ]
  },
  "available_rankings": [
    "institutional_combined"
  ],
  "filters": {
    "index_tags": ["S&P 500", "Nasdaq 100", "Russell 1000", "Russell 2000", "Russell 3000", "Mag7"]
  },
  "rows": [
    {
      "rank": 1,
      "symbol": "AAPL.US",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "exchange": "NASDAQ",
      "detail_path": "/stocks/AAPL.US",
      "index_tags": ["Nasdaq 100", "S&P 500", "Russell 1000", "Russell 3000", "Mag7"],
      "risk_tags": [],
      "market": {
        "price": 190.12,
        "price_change_pct": 1.25,
        "market_cap_usd": 2900000000000,
        "pe": 29.4,
        "forward_pe": 24.8,
        "ps": 7.1
      },
      "institution_metrics": {
        "manager_count": 200,
        "buyers_count": 11,
        "buyers_ratio_pct": 5.5,
        "sellers_count": 22,
        "sellers_ratio_pct": 11,
        "new_positions_count": 4,
        "added_count": 7,
        "reduced_count": 19,
        "exits_count": 3,
        "holders_count": 38,
        "holders_ratio_pct": 19,
        "net_buyers_count": -11,
        "unknown_previous_count": 0,
        "total_bought_value_usd": 850000000,
        "total_sold_value_usd": 420000000,
        "new_position_value_usd": 250000000,
        "exit_value_usd": 120000000,
        "total_tracked_value_usd": 12500000000,
        "total_tracked_shares": 65748000,
        "institutional_avg_holding_price": 190.12,
        "discount_to_institutional_avg_pct": 0,
        "key_institution_bought": true,
        "key_institution_bought_value_usd": 300000000,
        "avg_portfolio_weight_pct": 3.1,
        "max_portfolio_weight_pct": 36.72,
        "top_holder_name": "H&H International Investment, LLC",
        "top_buyer_name": "Berkshire Hathaway Inc",
        "top_seller_name": "Example Capital LLC"
      },
      "rank_change": {
        "daily": 3,
        "weekly": -5,
        "is_new": false
      },
      "since_joined": {
        "ranking_type": "institutional_buying",
        "first_joined_date": "2026-04-15",
        "entry_price": 175.21,
        "return_pct": 8.51
      },
      "explainers": [
        "200 家追踪机构中，11 家本期买入，22 家本期卖出",
        "4 家机构新建仓，3 家机构清仓",
        "当前共有 38 家追踪机构持有"
      ]
    }
  ]
}
```

## 6. Daily Ranking History

路径示例：

```text
dist/data/rankings/history/daily/2026-05-29/institutional-buying.json
```

用途：

- 排名变化计算。
- 历史榜单回看。
- 模拟盘重跑调试。

结构与当前榜单基本一致，但可以只保留必要字段。

示例：

```json
{
  "meta": {
    "as_of_date": "2026-05-29",
    "ranking_type": "institutional_buying",
    "metrics_version": "0.1",
    "whitelist_version": "2026-06-01-v1",
    "key_institution_version": "2026-06-01-v1",
    "manager_count": 200
  },
  "rows": [
    {
      "rank": 1,
      "symbol": "AAPL.US",
      "price": 190.12,
      "buyers_count": 11,
      "sellers_count": 22,
      "holders_count": 38
    }
  ]
}
```

## 7. Stock Detail

路径：

```text
dist/data/stocks/{symbol}.json
```

用途：

- 股票详情页按需加载。
- 展示机构变化、排名历史、估值、价格和公开披露事件。

示例：

```json
{
  "meta": {
    "symbol": "AAPL.US",
    "as_of_date": "2026-05-29",
    "metrics_version": "0.1",
    "whitelist_version": "2026-06-01-v1",
    "key_institution_version": "2026-06-01-v1",
    "manager_count": 200
  },
  "security": {
    "symbol": "AAPL.US",
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "exchange": "NASDAQ",
    "currency": "USD",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "index_tags": ["Nasdaq 100", "S&P 500", "Russell 1000", "Russell 3000", "Mag7"],
    "risk_tags": []
  },
  "market": {
    "price": 190.12,
    "price_change_pct": 1.25,
    "market_cap_usd": 2900000000000,
    "pe": 29.4,
    "forward_pe": 24.8,
    "ps": 7.1
  },
  "institution_metrics": {
    "manager_count": 200,
    "buyers_count": 11,
    "buyers_ratio_pct": 5.5,
    "sellers_count": 22,
    "sellers_ratio_pct": 11,
    "new_positions_count": 4,
    "added_count": 7,
    "reduced_count": 19,
    "exits_count": 3,
    "holders_count": 38,
    "holders_ratio_pct": 19,
    "net_buyers_count": -11,
    "unknown_previous_count": 0,
    "total_bought_value_usd": 850000000,
    "total_sold_value_usd": 420000000,
    "new_position_value_usd": 250000000,
    "exit_value_usd": 120000000,
    "total_tracked_value_usd": 12500000000,
    "total_tracked_shares": 65748000,
    "institutional_avg_holding_price": 190.12,
    "discount_to_institutional_avg_pct": 0,
    "key_institution_bought": true,
    "key_institution_bought_value_usd": 300000000,
    "avg_portfolio_weight_pct": 3.1,
    "max_portfolio_weight_pct": 36.72
  },
  "institution_activity": {
    "latest_report_period": "2026-03-31",
    "managers": [
      {
        "cik": "0001759760",
        "name": "H&H International Investment, LLC",
        "status": "added",
        "previous_shares": 28000000,
        "current_shares": 28945607,
        "change_shares": 945607,
        "change_pct": 3.38,
        "current_value_usd": 7346105601,
        "portfolio_weight_pct": 36.72,
        "filing_date": "2026-05-19",
        "report_period": "2026-03-31",
        "filing_url": "https://www.sec.gov/Archives/..."
      }
    ]
  },
  "ranking_history": {
    "institutional_buying": [
      {
        "date": "2026-05-29",
        "rank": 1,
        "buyers_count": 11,
        "sellers_count": 22,
        "holders_count": 38,
        "price": 190.12
      }
    ],
    "institutional_selling": []
  },
  "price_history": [
    {
      "date": "2026-05-29",
      "close": 190.12
    }
  ],
  "disclosure_events": [
    {
      "source": "house_ptr",
      "actor": "Nancy Pelosi",
      "relationship": "spouse",
      "transaction_type": "buy",
      "transaction_date": "2026-01-10",
      "disclosure_date": "2026-02-01",
      "amount_range": "$500,001-$1,000,000"
    }
  ],
  "explainers": [
    "200 家追踪机构中，11 家本期买入，22 家本期卖出",
    "当前共有 38 家追踪机构持有",
    "市值分组为 >500B"
  ]
}
```

## 8. Institutional Signal Simulation

路径：

```text
dist/data/simulation/institutional-signal-weekly.json
```

用途：

- 首页展示机构信号模拟盘收益。
- 详情页可引用股票是否曾进入该模拟盘。

MVP 口径：

- 每周一再平衡。如果周一不是交易日，顺延到下一个交易日。
- 观察窗口为过去 21 个交易日。
- 买入候选来自过去 21 个交易日内曾进入“机构买入榜 Top 10”或“机构持有榜 Top 10”的股票。
- 只保留当前价格低于机构平均持有价格的股票。
- 再平衡卖出时，优先卖出过去 21 个交易日内曾进入“机构卖出榜 Top 10”的持仓。
- 最多持有 10 只股票。
- 使用简单透明的 `allocation_score` 计算目标仓位权重。
- 每次再平衡时，当前持仓和当期候选一起计算 `allocation_score`。
- 候选为空时，不主动卖出，保持原持仓。
- 单只股票目标权重最小 5%，最大 50%。
- 只看价格差，不计分红、交易成本、滑点。
- 使用再平衡日收盘价。

`allocation_score` 分项：

- 进入买入榜 Top 10：`+10`
- 进入持有榜 Top 10：`+10`
- 在买入榜或持有榜 Top 10 中，且当前价格低于机构平均持有价格：`+20`
- 在买入榜 Top 10 且重点机构买入：`+10`
- 在持有榜 Top 10 且重点机构买入：`+10`
- 进入卖出榜 Top 10：`-50`

示例：

```json
{
  "meta": {
    "simulation_id": "institutional-signal-weekly-v0.1",
    "strategy": "institutional_signal_weekly",
    "as_of_date": "2026-05-29",
    "metrics_version": "0.1",
    "whitelist_version": "2026-06-01-v1",
    "key_institution_version": "2026-06-01-v1",
    "method": "weekly_rebalance_allocation_score_close_price",
    "lookback_trading_days": 21,
    "max_positions": 10,
    "weighting_method": "allocation_score_clamped_5_20"
  },
  "summary": {
    "start_date": "2026-01-01",
    "initial_value": 100000,
    "current_value": 112500,
    "cash_value": 0,
    "cash_weight_pct": 0,
    "total_return_pct": 12.5,
    "weekly_return_pct": 1.2,
    "ytd_return_pct": 12.5,
    "max_drawdown_pct": -8.4,
    "spy_return_pct": 7.1,
    "qqq_return_pct": 9.8,
    "excess_vs_spy_pct": 5.4,
    "excess_vs_qqq_pct": 2.7
  },
  "current_positions": [
    {
      "symbol": "AAPL.US",
      "rank_at_entry": 1,
      "buyers_count_at_entry": 11,
      "sellers_count_at_entry": 22,
      "holders_count_at_entry": 38,
      "source_rankings": ["institutional_buying", "institutional_holding"],
      "institutional_avg_holding_price_at_entry": 195.4,
      "discount_to_institutional_avg_pct_at_entry": 5.21,
      "allocation_score_at_entry": 60,
      "target_weight_pct": 14.5,
      "actual_weight_pct": 14.3,
      "entry_date": "2026-05-25",
      "entry_price": 185.22,
      "current_price": 190.12,
      "return_pct": 2.65
    }
  ],
  "candidate_snapshot": {
    "date": "2026-05-24",
    "buying_top10_symbols": ["AAPL.US", "MSFT.US"],
    "holding_top10_symbols": ["AAPL.US", "BRK.B.US"],
    "selling_top10_symbols": ["TSLA.US"],
    "eligible_candidates": [
      {
        "symbol": "AAPL.US",
        "source_rankings": ["institutional_buying", "institutional_holding"],
        "buying_top10_days": 7,
        "holding_top10_days": 21,
        "current_price": 185.22,
        "institutional_avg_holding_price": 195.4,
        "discount_to_institutional_avg_pct": 5.21,
        "key_institution_bought": true,
        "key_institution_bought_value_usd": 300000000,
        "total_bought_value_usd": 850000000,
        "buyers_count": 11,
        "holders_count": 38,
        "allocation_score": {
          "total": 60,
          "buying_top10_score": 10,
          "holding_top10_score": 10,
          "below_institution_avg_score": 20,
          "key_institution_buying_bonus": 20,
          "selling_top10_penalty": 0
        },
        "target_weight_pct": 14.5
      }
    ]
  },
  "equity_curve": [
    {
      "date": "2026-05-29",
      "value": 112500,
      "return_pct": 12.5,
      "spy_return_pct": 7.1,
      "qqq_return_pct": 9.8
    }
  ],
  "rebalance_history": [
    {
      "date": "2026-05-25",
      "strategy": "institutional_signal_weekly",
      "buys": ["AAPL.US", "MSFT.US", "NVDA.US"],
      "sells": [
        {
          "symbol": "TSLA.US",
          "reason": "appeared_in_institutional_selling_top10"
        }
      ],
      "positions_after_rebalance": ["AAPL.US", "MSFT.US", "NVDA.US"],
      "notes": []
    }
  ]
}
```

## 9. 前端 MVP 读取顺序

首页：

1. 读取 `metadata/build.json`。
2. 默认读取 `rankings/institutional-buying.json`。
3. 根据用户 tab 切换读取其他 ranking JSON。
4. 读取 `simulation/institutional-signal-weekly.json`。

股票详情页：

1. 读取 `metadata/build.json`。
2. 读取 `stocks/{symbol}.json`。

## 10. 下一步待定

- 是否把所有榜单合并为一个 `rankings/latest.json`，减少请求数量。
- 首页 Top 500 是否足够。
- 详情页价格历史是否导出完整日线，还是只导出最近一年。
- 是否为 JSON schema 增加机器校验文件。
