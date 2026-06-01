# StockHunt Database Schema v0.1

## 1. 目标

SQLite 是 StockHunt 的主数据源。静态 JSON 只作为构建产物。数据库需要支持：

- 13F 原始数据留存
- 白名单和重点机构版本化
- 指标重算
- 榜单历史
- 模拟盘历史回测
- 数据质量追踪

## 2. 配置版本表

### `config_versions`

记录每次生成数据使用的配置版本。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `config_hash` | text unique | 整个配置文件 hash |
| `config_version` | text | 配置版本 |
| `whitelist_version` | text | 白名单版本 |
| `key_institution_version` | text | 重点机构版本 |
| `strategy_version` | text | 策略版本 |
| `config_path` | text | 配置文件路径 |
| `created_at` | text | 创建时间 |
| `notes` | text nullable | 备注 |

### `institution_managers`

机构主数据。

| 字段 | 类型 | 说明 |
|---|---|---|
| `cik` | text pk | SEC CIK，补齐前导 0 |
| `name` | text | 13F 管理人名称 |
| `display_name` | text nullable | 页面展示名 |
| `style` | text nullable | value / growth / quant 等 |
| `source` | text | manual / longbridge / sec |
| `created_at` | text | 创建时间 |
| `updated_at` | text | 更新时间 |

### `institution_config_members`

记录某个配置版本下启用哪些机构。

| 字段 | 类型 | 说明 |
|---|---|---|
| `config_hash` | text | 配置 hash |
| `cik` | text | 机构 CIK |
| `enabled` | integer | 1/0 |
| `is_key_institution` | integer | 是否重点机构 |
| `display_name` | text nullable | 配置内展示名 |

联合主键：`(config_hash, cik)`。

## 3. 股票和标签

### `securities`

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | text pk | 长桥格式，例如 `AAPL.US` |
| `ticker` | text | ticker |
| `company_name` | text | 公司名 |
| `exchange` | text nullable | 交易所 |
| `currency` | text | 默认 USD |
| `cusip` | text nullable | 当前 CUSIP |
| `sector` | text nullable | 行业 |
| `industry` | text nullable | 子行业 |
| `status` | text | active / inactive / unknown |
| `created_at` | text | 创建时间 |
| `updated_at` | text | 更新时间 |

### `security_identifiers`

记录 CUSIP、ticker 历史映射。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `symbol` | text | 股票 symbol |
| `identifier_type` | text | cusip / ticker / isin |
| `identifier` | text | 标识值 |
| `valid_from` | text nullable | 生效日期 |
| `valid_to` | text nullable | 失效日期 |

### `security_tags`

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | text | 股票 symbol |
| `tag` | text | S&P 500 / Nasdaq 100 / Russell 1000 / Mag7 等 |
| `source` | text | manual / provider |
| `as_of_date` | text | 标签日期 |

联合主键：`(symbol, tag, as_of_date)`。

## 4. 13F 原始数据

### `sec_13f_filings`

| 字段 | 类型 | 说明 |
|---|---|---|
| `accession_number` | text pk | SEC accession number |
| `cik` | text | 机构 CIK |
| `filing_type` | text | 13F-HR / 13F-HR/A |
| `filing_date` | text | 披露日期 |
| `report_period` | text | 报告期 |
| `is_amendment` | integer | 是否修订 |
| `supersedes_accession_number` | text nullable | 被替代 filing |
| `sec_url` | text | SEC 链接 |
| `raw_path` | text nullable | 原始文件路径 |
| `parsed_at` | text nullable | 解析时间 |

### `institution_holdings`

13F 持仓明细。一行对应一个 filing 中的一个持仓条目。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `accession_number` | text | filing |
| `cik` | text | 机构 CIK |
| `report_period` | text | 报告期 |
| `cusip` | text | CUSIP |
| `symbol` | text nullable | 映射后的 symbol |
| `issuer_name` | text | 13F issuer 名称 |
| `share_type` | text nullable | SH / PRN 等 |
| `put_call` | text nullable | PUT / CALL |
| `shares` | real | 股数 |
| `value_usd` | real | 13F value，美元 |
| `portfolio_weight_pct` | real nullable | 在该机构 13F 组合中的占比 |

索引：

- `(cik, report_period)`
- `(symbol, report_period)`
- `(cusip, report_period)`

## 5. 持仓变化和指标

### `holding_changes`

按机构、股票、报告期聚合两个报告期之间的变化。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `config_hash` | text | 使用的配置版本 |
| `cik` | text | 机构 CIK |
| `symbol` | text | 股票 symbol |
| `report_period` | text | 当前报告期 |
| `previous_report_period` | text nullable | 上一报告期 |
| `status` | text | new_position / added / reduced / exited / unchanged / unknown_previous |
| `previous_shares` | real | 上期股数 |
| `current_shares` | real | 本期股数 |
| `change_shares` | real | 股数变化 |
| `previous_value_usd` | real | 上期 value |
| `current_value_usd` | real | 本期 value |
| `current_report_price` | real nullable | 当前报告期价格口径 |
| `change_value_usd` | real | 股数变化按当前报告期价格折算 |
| `portfolio_weight_pct` | real nullable | 本期组合占比 |

金额口径：

- 新建仓金额：当前报告期 `current_value_usd`
- 增持金额：`change_shares * current_report_price`
- 减持金额：`abs(change_shares) * current_report_price`
- 清仓金额：`previous_shares * current_report_price`

### `market_snapshots`

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | text | 股票 symbol |
| `date` | text | 市场日期 |
| `price` | real nullable | 收盘价或最新价 |
| `price_change_pct` | real nullable | 当日涨跌幅 |
| `market_cap_usd` | real nullable | 市值 |
| `pe` | real nullable | PE |
| `forward_pe` | real nullable | Forward PE |
| `ps` | real nullable | PS |
| `source` | text | longbridge |
| `is_stale` | integer | 是否沿用旧数据 |

联合主键：`(symbol, date)`。

### `metric_snapshots`

每只股票每日一行，供榜单和静态 JSON 使用。

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | text | 股票 symbol |
| `date` | text | 指标日期 |
| `config_hash` | text | 配置版本 |
| `report_period` | text | 最新 13F 报告期 |
| `manager_count` | integer | 跟踪机构数 |
| `buyers_count` | integer | 买入机构数 |
| `sellers_count` | integer | 卖出机构数 |
| `new_positions_count` | integer | 新建仓机构数 |
| `added_count` | integer | 增持机构数 |
| `reduced_count` | integer | 减持机构数 |
| `exits_count` | integer | 清仓机构数 |
| `holders_count` | integer | 当前持有机构数 |
| `total_bought_value_usd` | real | 买入金额 |
| `total_sold_value_usd` | real | 卖出金额 |
| `new_position_value_usd` | real | 新建仓金额 |
| `exit_value_usd` | real | 清仓金额 |
| `total_tracked_value_usd` | real | 当前持有金额 |
| `total_tracked_shares` | real | 当前持有股数 |
| `institutional_avg_holding_price` | real nullable | 机构平均持有价格 |
| `key_institution_bought` | integer | 是否重点机构买入 |
| `key_institution_bought_value_usd` | real | 重点机构买入金额 |
| `allocation_score` | real nullable | 模拟盘用分数 |

联合主键：`(symbol, date, config_hash)`。

## 6. 榜单和模拟盘

### `rank_history`

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | text | 日期 |
| `ranking_type` | text | institutional_buying 等 |
| `config_hash` | text | 配置版本 |
| `symbol` | text | 股票 symbol |
| `rank` | integer | 排名 |
| `sort_value` | real nullable | 主排序值 |
| `price` | real nullable | 当日价格 |

联合主键：`(date, ranking_type, config_hash, symbol)`。

### `sim_portfolio_snapshots`

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | text | 日期 |
| `strategy_id` | text | 策略 ID |
| `config_hash` | text | 配置版本 |
| `portfolio_value` | real | 总净值 |
| `cash_value` | real | 现金 |
| `cash_weight_pct` | real | 现金占比 |
| `daily_return_pct` | real nullable | 日收益 |
| `total_return_pct` | real nullable | 累计收益 |
| `spy_return_pct` | real nullable | SPY 对比 |
| `qqq_return_pct` | real nullable | QQQ 对比 |

联合主键：`(date, strategy_id, config_hash)`。

### `sim_portfolio_positions`

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | text | 日期 |
| `strategy_id` | text | 策略 ID |
| `config_hash` | text | 配置版本 |
| `symbol` | text | 股票 |
| `target_weight_pct` | real | 目标权重 |
| `actual_weight_pct` | real | 实际权重 |
| `shares` | real | 模拟持股数 |
| `price` | real | 价格 |
| `market_value` | real | 市值 |
| `allocation_score` | real | 当期分数 |

联合主键：`(date, strategy_id, config_hash, symbol)`。

### `sim_rebalance_events`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `date` | text | 再平衡日 |
| `strategy_id` | text | 策略 |
| `config_hash` | text | 配置版本 |
| `symbol` | text | 股票 |
| `action` | text | buy / sell / hold / resize |
| `reason` | text | appeared_in_selling_top10 / target_weight_change 等 |
| `from_weight_pct` | real nullable | 原权重 |
| `to_weight_pct` | real nullable | 新权重 |
| `price` | real nullable | 成交价 |

## 7. 运维和质量

### `build_runs`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `started_at` | text | 开始时间 |
| `finished_at` | text nullable | 结束时间 |
| `status` | text | success / failed / partial |
| `config_hash` | text | 配置版本 |
| `step` | text nullable | 当前步骤 |
| `message` | text nullable | 说明 |

### `data_quality_issues`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer pk | 内部 ID |
| `date` | text | 日期 |
| `severity` | text | info / warning / error |
| `entity_type` | text | symbol / filing / manager / build |
| `entity_id` | text | 对应 ID |
| `issue_type` | text | market_data_missing / cusip_mapping_failed 等 |
| `message` | text | 说明 |
| `resolved_at` | text nullable | 解决时间 |
