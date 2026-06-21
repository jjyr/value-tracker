# Value Tracker Metrics Spec v0.1

## 1. 目标

本文件定义 Value Tracker MVP 的原始指标和榜单排序口径。

v0.1 不做综合评分，不做评级，不把多个维度压缩成一个总分。所有页面展示的核心信息都应尽量使用原始数量、比例和可解释字段，例如：

```text
买入机构：11 / 200
卖出机构：22 / 200
当前持有：38 / 200
```

## 2. 基础定义

### 2.1 机构总数

```text
manager_count = 当前白名单版本中启用的 13F 管理人总数
```

注意：

- 佩洛西等非 13F 公开披露观察对象不计入 `manager_count`。
- 同一个 CIK 只计一次。
- 如果某家机构当前被禁用，不计入分母。
- 榜单和详情页必须显示所使用的 `whitelist_version`。

### 2.2 机构状态

对每个股票和每家启用机构，比较最近两个可比 13F 报告期：

```text
previous_shares = 上一报告期持股数
current_shares = 当前报告期持股数
```

状态定义：

| 状态 | 条件 | 中文展示 |
|---|---|---|
| `new_position` | `previous_shares = 0` 且 `current_shares > 0` | 新建仓 |
| `added` | `previous_shares > 0` 且 `current_shares > previous_shares` | 增持 |
| `unchanged` | `previous_shares > 0` 且 `current_shares = previous_shares` | 不变 |
| `reduced` | `current_shares > 0` 且 `current_shares < previous_shares` | 减持 |
| `exited` | `previous_shares > 0` 且 `current_shares = 0` | 清仓 |
| `not_held` | 两期都没有持有 | 未持有 |
| `unknown_previous` | 当前持有，但上一报告期缺失 | 上期未知 |

MVP 不设置 5% 之类的变动阈值。只要股数变化，就按方向计数。

## 3. 机构行为指标

对单只股票聚合所有启用机构：

```text
new_positions_count = 状态为 new_position 的机构数
added_count = 状态为 added 的机构数
reduced_count = 状态为 reduced 的机构数
exits_count = 状态为 exited 的机构数
unchanged_count = 状态为 unchanged 的机构数
unknown_previous_count = 状态为 unknown_previous 的机构数

buyers_count = new_positions_count + added_count
sellers_count = reduced_count + exits_count
holders_count = current_shares > 0 的机构数
net_buyers_count = buyers_count - sellers_count
```

比例：

```text
buyers_ratio = buyers_count / manager_count
sellers_ratio = sellers_count / manager_count
holders_ratio = holders_count / manager_count
new_positions_ratio = new_positions_count / manager_count
exits_ratio = exits_count / manager_count
```

展示格式：

```text
买入机构：11 / 200
卖出机构：22 / 200
新建仓：4 / 200
清仓：3 / 200
当前持有：38 / 200
```

百分比可以作为辅助展示：

```text
买入机构：11 / 200 (5.5%)
```

## 4. 持仓金额和持仓占比

这些字段不参与综合评分，因为 MVP 没有综合评分；它们用于详情页解释和排序辅助。

每个机构持仓字段：

```text
current_shares
previous_shares
change_shares
change_pct
current_value_usd
previous_value_usd
change_value_usd
portfolio_weight_pct
```

如果当前报告期存在该机构的独立现金披露：

```text
portfolio_weight_pct = current_value_usd / (13F 证券总市值 + cash_value_usd) * 100
cash_weight_pct = cash_value_usd / (13F 证券总市值 + cash_value_usd) * 100
```

如果没有现金披露，`portfolio_weight_pct` 继续使用旧口径：

```text
portfolio_weight_pct = current_value_usd / 13F 证券总市值 * 100
```

注意：13F 不披露现金。`cash_value_usd` 只能来自 10-Q/10-K、N-PORT 或其他明确披露文件；不能用“未出现在 13F 的部分”倒推。

股票聚合字段：

```text
total_tracked_value_usd = 白名单机构当前持有该股票总市值
total_tracked_shares = 白名单机构当前持有该股票总股数
institutional_avg_holding_price = total_tracked_value_usd / total_tracked_shares
total_change_value_usd = 白名单机构该股票股数变化按当前报告期价格折算的总额
total_bought_value_usd = 白名单机构本期新建仓和增持股数按当前报告期价格折算的美元总额
total_sold_value_usd = 白名单机构本期减持和清仓股数按当前报告期价格折算的美元总额
new_position_value_usd = 白名单机构本期新建仓美元总额
exit_value_usd = 白名单机构本期清仓美元总额
key_institution_bought_value_usd = 重点机构本期新建仓和增持的美元变化总额
avg_portfolio_weight_pct = 持有机构中该股票平均组合占比
max_portfolio_weight_pct = 单一机构中最高组合占比
top_holder_name = 当前持仓市值最高的机构
top_buyer_name = 本期增持美元金额最多的机构
top_seller_name = 本期减持美元金额最多的机构
```

注意：13F 的 value 是报告期末持仓价值，不应解释为 filing date 当日价值。

`institutional_avg_holding_price` 是由 13F 披露的持仓价值除以股数推导出的近似价格。它不是机构真实买入成本，只能理解为白名单机构在报告期末披露持仓的平均标记价格。

买入榜、卖出榜、新建仓榜、清仓榜的美元金额统一使用当前报告期价格口径：

```text
current_report_price = current_value_usd / current_shares
buy_value_usd = max(change_shares, 0) * current_report_price
sell_value_usd = abs(min(change_shares, 0)) * current_report_price
new_position_value_usd = current_value_usd
exit_value_usd = previous_shares * current_report_price
```

说明：清仓时当前期没有持仓 value，MVP 使用当前报告期同一股票的机构平均持有价格或市场价格作为替代当前报告期价格。

## 5. 估值和市值指标

市场数据来自长桥 API。

核心字段：

```text
price
price_change_pct
market_cap_usd
pe
forward_pe
ps
sector
industry
```

估值变化字段，后续可选：

```text
pe_change_1d
pe_change_1w
forward_pe_change_1d
forward_pe_change_1w
ps_change_1d
ps_change_1w
```

MVP 如果暂时没有估值历史，可以只展示当前 PE、Forward PE、PS。

## 6. 榜单排序口径

说明：机构买入、机构卖出、新建仓、清仓、机构持有相关 Top 10 榜单按美元金额排序，不按股数排序。

### 6.1 机构综合榜

默认排序：

```text
total_tracked_value_usd desc
holders_count desc
symbol asc
```

展示重点：

- 持有市值：`total_tracked_value_usd`
- 当前持有：`holders_count / manager_count`
- 买入机构：`buyers_count / manager_count`
- 卖出机构：`sellers_count / manager_count`
- 市值：`market_cap_usd`
- 估值：`pe`、`forward_pe`、`ps`
- 信号标签：`买入 #1`、`卖出 #1`、`新建仓 #1`、`清仓 #1`、`已清仓`、具体重点机构名如 `段永平`、`李录`

标签来源：

```text
holding_rank = rank(total_tracked_value_usd desc)
buying_rank = rank(total_bought_value_usd desc)
selling_rank = rank(total_sold_value_usd desc)
new_position_rank = rank(new_position_value_usd desc)
exit_rank = rank(exit_value_usd desc)
```

规则：

- 综合榜排序不使用买入、卖出、新建仓、清仓金额，也不使用 `max_activity_value_usd`。
- 原始排名只用于生成标签，例如 `买入 #1`、`清仓 #2`。
- 持有榜本身已按持有市值排序，不额外生成 `持有 #1` 标签。
- 不同标签类型在前端使用不同颜色。
- 重点机构不显示抽象 `重点机构` 标签，直接显示具体机构名。
- 如果 `holders_count = 0` 且 `total_tracked_value_usd = 0`，但命中清仓榜，则仍进入综合榜并标记 `已清仓`。

## 7. 排名变化指标

每个榜单单独计算排名变化：

```text
rank_daily_change = previous_trading_day_rank - current_rank
rank_weekly_change = five_trading_days_ago_rank - current_rank
```

展示约定：

- 正数表示排名上升。
- 负数表示排名下降。
- 新入榜显示 `new`。
- 跌出榜单的股票不在首页展示，但保留历史数据。

## 8. 加入榜单后的收益

每个榜单单独记录进入时间：

```text
first_joined_date
entry_price
current_price
return_pct
```

MVP 口径：

- 以首次进入某榜单 Top 100 为 `first_joined_date`。
- 入榜价格使用首次入榜后的下一个交易日收盘价。
- 不计分红、交易成本和滑点。

## 9. 机构信号模拟盘指标

MVP 模拟盘使用一个明确的、可解释的组合规则。

### 9.1 观察窗口

```text
lookback_window = 21 trading days
```

21 个交易日近似一个交易月。每次再平衡时，只使用再平衡日前一交易日及以前的数据，避免未来函数。

### 9.2 买入候选

```text
buying_top10_set = 过去 21 个交易日内曾进入机构买入榜 Top 10 的股票集合
holding_top10_set = 过去 21 个交易日内曾进入机构持有榜 Top 10 的股票集合
candidate_pool = buying_top10_set ∪ holding_top10_set
```

价值过滤：

```text
undervalued_to_institutions =
  current_price < institutional_avg_holding_price
```

最终买入候选：

```text
eligible_candidates =
  candidate_pool 中满足 current_price < institutional_avg_holding_price 的股票
```

说明：这里的“低于机构持有平均价格”是相对 13F 披露持仓价值推导出的平均价格，不代表低于机构真实成本。

### 9.3 重点机构

重点机构来自可版本化配置 `config/key-institutions.yaml`。MVP 初始重点机构：

- 段永平：H&H International Investment, LLC，CIK `0001759760`
- 李录：Himalaya Capital Management LLC，CIK `0001709323`
- 伯克希尔：Berkshire Hathaway Inc，CIK `0001067983`

修改重点机构列表后，需要重算历史 `allocation_score` 和模拟盘回测。

重点机构买入定义：

```text
key_institution_bought =
  任一重点机构对该股票状态为 new_position 或 added
```

### 9.4 仓位权重分数

MVP 使用一个简单明确的 `allocation_score` 来决定再平衡权重。这个分数只用于模拟盘仓位，不作为页面通用股票评分。

基础规则：

```text
allocation_score =
  buying_top10_score +
  holding_top10_score +
  below_institution_avg_score +
  key_institution_buying_bonus +
  selling_top10_penalty
```

分项：

```text
buying_top10_score =
  if symbol in buying_top10_set: 10 else 0

holding_top10_score =
  if symbol in holding_top10_set: 10 else 0

below_institution_avg_score =
  if symbol in (buying_top10_set ∪ holding_top10_set)
     and current_price < institutional_avg_holding_price: 20
  else 0

key_institution_buying_bonus =
  (if symbol in buying_top10_set and key_institution_bought: 10 else 0)
  + (if symbol in holding_top10_set and key_institution_bought: 10 else 0)

selling_top10_penalty =
  if symbol in selling_top10_set: -50 else 0
```

说明：

- 同时进入买入榜和持有榜，基础分为 `20`。
- 在买入榜或持有榜 Top 10 中，且当前价格低于机构平均持有价格，额外 `+20`。
- 如果在买入榜 Top 10 且重点机构买入，额外 `+10`。
- 如果在持有榜 Top 10 且重点机构买入，额外 `+10`。
- 同时满足两个条件时，重点机构买入最多额外 `+20`。
- 进入卖出榜 Top 10 强扣 `-50`。
- `allocation_score <= 0` 的股票不进入买入候选；当前持仓如果分数小于等于 0，优先卖出。

### 9.5 候选排序

如果候选超过 10 只，按以下顺序选择前 10：

```text
allocation_score desc
key_institution_bought desc
discount_to_institutional_avg_pct desc
total_bought_value_usd desc
symbol asc
```

字段定义：

```text
buying_top10_days = 过去 21 个交易日内进入机构买入榜 Top 10 的天数
holding_top10_days = 过去 21 个交易日内进入机构持有榜 Top 10 的天数
discount_to_institutional_avg_pct =
  (institutional_avg_holding_price - current_price) / institutional_avg_holding_price * 100
```

### 9.6 卖出优先级

```text
selling_top10_set = 过去 21 个交易日内曾进入机构卖出榜 Top 10 的股票集合
```

再平衡卖出时，优先级：

1. 当前持仓中属于 `selling_top10_set` 的股票。
2. `allocation_score <= 0` 的股票。
3. 如果仍需腾出仓位，则卖出候选排序最弱的股票。

### 9.7 仓位规则

- 每周一再平衡。如果周一不是交易日，顺延到下一个交易日。
- 最多持有 10 只股票。
- 每次再平衡时，当前持仓和当期候选一起计算 `allocation_score`。
- 候选为空时，不主动卖出，保持原持仓。
- 根据 `allocation_score` 计算目标权重并自动调仓。
- 交易价格使用再平衡日收盘价。
- 只计算价格差，不计分红、交易成本和滑点。

目标权重：

```text
raw_weight = allocation_score / sum(allocation_score of selected_positions)
target_weight = clamp(raw_weight, 5%, 50%)
```

如果 clamp 后总权重大于 100%，按比例缩放到 100%。如果总权重小于 100%，剩余资金保留为现金。

## 10. 缺失数据处理

| 数据 | 缺失处理 |
|---|---|
| 当前价格 | 不进入当期榜单 |
| 市值 | 展示为 `null`，并标记 `market_cap_missing` |
| PE | 展示为 `null` |
| Forward PE | 展示为 `null` |
| PS | 展示为 `null` |
| 13F 上期持仓 | 当前持有计入 `holders_count`，不计入 `buyers_count` 或 `sellers_count`，状态为 `unknown_previous` |
| `institutional_avg_holding_price` | 不进入模拟盘买入候选 |

## 11. 用户解释文案

每只股票可以输出一组说明文本，用于详情页和 tooltip：

```json
{
  "explainers": [
    "200 家追踪机构中，11 家本期买入，22 家本期卖出",
    "4 家机构新建仓，3 家机构清仓",
    "当前共有 38 家追踪机构持有",
    "市值分组为 2B-100B"
  ]
}
```

解释文案不参与排序，只用于降低用户理解成本。
