# StockHunt Product Requirements v0.2

## 1. 产品概述

StockHunt 是一个全静态生成的美股机构持仓分析网页。产品以 SEC 13F 等公开披露为核心数据源，追踪知名机构对美股的买入、卖出、持有变化，再结合价格、估值、市值和指数标签展示多维原始信号。

v0.2 的核心变化：摒弃综合评分和评级系统，不再把多个维度压缩成一个分数。产品改为展示原始信息和原始比例，例如：

```text
买入机构：11 / 200
卖出机构：22 / 200
当前持有机构：38 / 200
```

这样用户能直接看到信号来源和强弱，而不是依赖一个不透明的综合分。

## 2. 核心目标

- 覆盖白名单机构 13F 触达过的美股股票。
- 追踪白名单机构 13F 持仓变化，展示买入、卖出、新建仓、增持、减持、清仓和持有机构数量。
- 追踪股票的价格、市盈率、远期市盈率、市销率和市值。
- 使用 S&P 500、Nasdaq 100、Russell 1000、Russell 2000、Russell 3000 等指数标签辅助识别股票类型。
- 首页展示多个原始信号榜单，而不是综合评分榜单。
- 股票详情页展示机构行为明细、估值、市值、历史表现和公开披露事件。
- 模拟盘基于近一个月机构买入榜、机构持有榜和机构卖出榜的组合规则，不基于综合评分。
- 所有页面由静态生成器生成，适合部署到静态托管服务。

## 3. 非目标

- 不做实时交易系统。
- 不提供自动下单能力。
- 不把 13F 解读为实时买卖信号，因为 13F 通常存在季度级披露延迟。
- MVP 不做综合评分、评级、隐藏模型排序。
- 不直接复刻任何现有 IP 的图标、命名或视觉资产。多维图标可以参考“能力维度”的表达方式，但需要原创设计。

## 4. 股票池和标签

### 4.1 股票池

MVP 不主动扫描全美股后再过滤。候选股票池来自白名单机构 13F 中出现过的股票，再为这些股票实时补充价格、估值、市值和指数标签。

MVP 的股票池生成逻辑：

- 从启用的白名单 13F 管理人持仓中提取股票。
- 尽量映射 CUSIP、ticker、公司名和交易所。
- 对候选股票调用长桥 API 获取市场数据。
- 如果市场数据缺失、无法交易或明显不是普通股票，则在榜单生成时跳过。
- 不单独做全市场 penny stock、市值、流动性扫描过滤。

说明：MVP 只分析“被我们关注的机构交易或持有过”的股票，不尝试覆盖所有美股标的。

### 4.2 标签

指数标签：

- `S&P 500`
- `Nasdaq 100`
- `Russell 1000`
- `Russell 2000`
- `Russell 3000`

主题标签：

- `Mag7`

风险标签：

- `small-cap`
- `micro-cap`
- `low-liquidity`
- `high-volatility`
- `market-data-missing`

说明：标签只用于筛选和解释，不参与评分。

## 5. 机构白名单

13F 机构分析基于可维护白名单，而不是所有 13F 机构一视同仁。

白名单需要支持：

- 新增机构
- 删除机构
- 临时禁用机构
- 标注机构风格，例如 value、growth、quant、activist、multi-strategy
- 根据修改后的白名单重跑历史分析
- 保留白名单版本，确保历史榜单可复现

建议配置字段：

```yaml
managers:
  - cik: "0001067983"
    name: "Berkshire Hathaway"
    enabled: true
    style: "value"
    notes: "Long-term concentrated public equity holdings"
```

说明：MVP 阶段机构等权。每家启用机构在 `11 / 200` 这类分母中都计为 `1`。不因 AUM、知名度或买入金额放大权重。

### 5.1 配置与版本化

机构配置至少拆成两份：

```text
config/institution-whitelist.yaml
config/key-institutions.yaml
```

`institution-whitelist.yaml` 控制哪些 13F 管理人进入追踪机构分母，例如 `11 / 200` 中的 `200`。

`key-institutions.yaml` 控制模拟盘 `allocation_score` 中的重点机构加分。它应该能被简单修改，然后重新跑历史回测。

重点机构配置示例：

```yaml
version: "2026-06-01-v1"
key_institutions:
  - cik: "0001759760"
    name: "H&H International Investment, LLC"
    display_name: "段永平"
    enabled: true
  - cik: "0001709323"
    name: "Himalaya Capital Management LLC"
    display_name: "李录"
    enabled: true
  - cik: "0001067983"
    name: "Berkshire Hathaway Inc"
    display_name: "伯克希尔"
    enabled: true
```

版本化要求：

- 每次生成指标、榜单和模拟盘时，都记录 `whitelist_version` 和 `key_institution_version`。
- 修改白名单后，需要重算候选股票池、机构指标、榜单、模拟盘。
- 只修改重点机构列表时，不需要重新抓取 13F 原始数据，但需要重算 `key_institution_bought`、`allocation_score`、模拟盘和静态 JSON。
- 历史回测必须能指定旧版本或新版本配置，避免结果不可复现。

### 5.2 初始重点观察对象

第一版白名单分成两类：

- 13F 管理人：可以直接从 SEC 13F 抓取和计算持仓变化。
- 其他公开披露观察对象：不是标准 13F 管理人，但可通过其他公开披露跟踪。

重点 13F 管理人：

| 观察对象 | 13F 管理人 | CIK | 类型 |
|---|---|---:|---|
| 李录 | Himalaya Capital Management LLC | `0001709323` | value / concentrated |
| 段永平 | H&H International Investment, LLC | `0001759760` | value / concentrated |
| 巴菲特 / 伯克希尔 | Berkshire Hathaway Inc | `0001067983` | value / concentrated |
| 木头姐 | ARK Investment Management LLC | `0001697748` | growth / innovation |

其他公开披露观察对象：

| 观察对象 | 数据来源 | 类型 | 处理方式 |
|---|---|---|---|
| 佩洛西 | House Financial Disclosure / PTR | congressional disclosure | 独立数据源，不混入 13F 原始表 |

### 5.3 大型 13F 机构基线池

除重点观察对象外，MVP 补充一组大型 active 13F 管理人作为基线池。初始采用 Longbridge `investors` 当前 active 13F AUM 排名前 20 名，生成日期为 2026-05-29。

| Rank | 管理人 | CIK | 最近报告期 | 13F AUM |
|---:|---|---:|---|---:|
| 1 | Capital International Investors | `0001562230` | 31-DEC-2025 | `$637.97B` |
| 2 | Capital Research Global Investors | `0001422848` | 31-DEC-2025 | `$541.73B` |
| 3 | CTC LLC | `0001445893` | 30-SEP-2025 | `$404.44B` |
| 4 | Berkshire Hathaway Inc | `0001067983` | 31-DEC-2025 | `$274.16B` |
| 5 | Dodge & Cox | `0000200217` | 31-DEC-2025 | `$185.26B` |
| 6 | PRIMECAP Management Co/CA | `0000763212` | 31-DEC-2025 | `$132.11B` |
| 7 | State Farm Mutual Automobile Insurance Co | `0000315032` | 31-DEC-2025 | `$127.33B` |
| 8 | Lilly Endowment Inc | `0000316011` | 31-DEC-2025 | `$99.08B` |
| 9 | Sanders Capital LLC | `0001508097` | 31-DEC-2025 | `$86.82B` |
| 10 | Brookfield Corp /ON/ | `0001001085` | 31-DEC-2025 | `$85.84B` |
| 11 | Harris Associates L P | `0000813917` | 31-DEC-2025 | `$79.12B` |
| 12 | Mitsubishi UFJ Financial Group Inc | `0000067088` | 31-DEC-2025 | `$66.94B` |
| 13 | Artisan Partners Limited Partnership | `0001466153` | 31-DEC-2025 | `$66.79B` |
| 14 | Alkeon Capital Management LLC | `0001230239` | 31-DEC-2025 | `$63.13B` |
| 15 | GQG Partners LLC | `0001697233` | 31-DEC-2025 | `$60.72B` |
| 16 | TCI Fund Management Ltd | `0001647251` | 31-DEC-2025 | `$53.65B` |
| 17 | Aristotle Capital Management LLC | `0000860644` | 31-DEC-2025 | `$49.96B` |
| 18 | WCM Investment Management LLC | `0001061186` | 31-DEC-2025 | `$48.57B` |
| 19 | Ninety One UK Ltd | `0001418329` | 31-DEC-2025 | `$46.64B` |
| 20 | Alecta Tjanstepension Omsesidigt | `0001484429` | 30-SEP-2025 | `$45.16B` |

这组基线池应定期重算，但要保留版本号。历史榜单重跑时，需要指定使用哪个白名单版本。

如果重点观察对象与大型基线池重复，例如 Berkshire Hathaway，最终配置需要按 CIK 去重。

说明：大型被动管理人，例如 Vanguard、BlackRock、State Street，后续可以作为被动持仓背景或市场基准，但不建议默认作为“聪明钱”白名单，否则指数化持仓会稀释机构买卖信号。

## 6. 数据来源

### 6.1 13F 数据

主要来源：

- SEC Form 13F-HR
- SEC Form 13F-HR/A 修订文件
- 13F information table XML

处理原则：

- 优先使用结构化 XML 解析，不把 AI Agent 作为唯一事实来源。
- AI Agent 可用于异常字段识别、机构名称归一化、解析失败解释和人工复核辅助。
- 每个 filing 需要记录 filing date、report period、CIK、accession number、amendment 状态。
- 13F 披露的是报告期末持仓，不代表 filing date 当天仍然持有。

### 6.2 其他公开披露

MVP 以 13F 为主。后续可加入：

- Form 4：内部人买卖
- Schedule 13D / 13G：超过 5% 持股披露
- Congressional disclosure / PTR
- 机构公告或基金报告
- 重要 8-K 或新闻事件

这些数据可以作为独立维度或辅助标签，不建议在 MVP 阶段混入 13F 主榜单口径，避免解释不清。

### 6.3 市场和基本面数据

通过长桥 API 获取：

- 股票基础信息
- 当前价格
- 当日涨跌幅
- 市值
- 市盈率
- 远期市盈率
- 市销率
- 行业分类
- 历史 K 线，用于模拟盘和加入榜单后的涨跌幅

## 7. 核心页面

### 7.1 首页：多维原始信号榜单

首页展示多个榜单 tab。每个榜单都有明确排序口径，并直接展示原始指标，不展示综合分。

MVP 榜单：

| 榜单 | 默认排序 | 主要展示 |
|---|---|---|
| 机构综合榜 | `total_tracked_value_usd` 降序 | 持有市值、买入/卖出/新建仓/清仓排名标签、买卖持有机构数 |

说明：页面不再单独展示机构买入、机构卖出、新建仓、清仓、机构持有多个榜单。数据生成阶段仍计算这些原始排名，并在综合榜中显示为 `买入 #1`、`卖出 #2`、`新建仓 #1`、`清仓 #1` 等标签。持有榜本身已按持有市值排序，不额外展示 `持有 #1`。重点机构直接显示具体机构名，例如 `段永平`、`李录`。已被追踪机构全部清仓的股票仍进入综合榜，持有市值为 `0`，并标记 `已清仓`。

首页通用字段：

- 榜单排名
- 股票代码
- 公司名
- 指数标签和主题标签
- 当前价格
- 当日涨跌幅
- 市值和市值分组
- PE
- Forward PE
- PS
- 买入机构数，例如 `11 / 200`
- 买入金额
- 卖出机构数，例如 `22 / 200`
- 卖出金额
- 新建仓机构数
- 新建仓金额
- 增持机构数
- 减持机构数
- 清仓机构数
- 清仓金额
- 当前持有机构数
- 当前持有金额
- 日排名变化
- 周排名变化
- 加入当前榜单后的涨跌幅

首页还展示：

- 机构信号模拟盘累计收益
- 机构信号模拟盘本周收益
- 与 `SPY` 和 `QQQ` 的对比
- 最近一次数据更新时间
- 最近一次 13F 报告期

### 7.2 股票详情页

股票详情页展示：

- 股票基本信息
- 指数标签、主题标签、风险标签
- 当前价格、PE、Forward PE、PS、市值
- 历史价格走势
- 机构买卖变化摘要
- 主要买入机构
- 主要卖出机构
- 主要持有机构
- 13F 原始 filing 链接
- 排名历史，按各个榜单分别展示
- 进入当前榜单后的收益表现
- 其他公开披露事件，例如 congressional disclosure

详情页五维展示改为原始指标维度：

1. 机构买入：买入机构数、新建仓数、增持数。
2. 机构卖出：卖出机构数、减持数、清仓数。
3. 机构持有：当前持有机构数、连续持有机构数、主要持有机构。
4. 估值：PE、Forward PE、PS。
5. 体量：市值、市值分组、指数标签。

## 8. 多维指标定义

详细指标定义见 [metrics-spec.md](metrics-spec.md)。

MVP 不计算综合评分。所有榜单都必须能用用户可见字段解释排序原因。

核心机构指标：

```text
manager_count = 启用白名单 13F 机构总数
buyers_count = 新建仓机构数 + 增持机构数
sellers_count = 减持机构数 + 清仓机构数
new_positions_count = 新建仓机构数
added_count = 增持机构数
reduced_count = 减持机构数
exits_count = 清仓机构数
holders_count = 当前持有机构数
```

展示格式：

```text
买入机构：11 / 200
卖出机构：22 / 200
当前持有：38 / 200
```

可派生但不作为评分的指标：

```text
net_buyers_count = buyers_count - sellers_count
buy_ratio = buyers_count / manager_count
sell_ratio = sellers_count / manager_count
holder_ratio = holders_count / manager_count
```

这些派生指标可以用于排序、筛选或 tooltip，但页面必须同时展示原始分子和分母。

## 9. 排名和模拟盘规则

### 9.1 排名口径

日排名变化：

- 今日某个榜单排名减去上一交易日同一榜单排名。
- 新入榜股票显示为 `new`。
- 跌出当前展示范围的股票不在首页展示，但应保留在历史数据中。

周排名变化：

- 今日某个榜单排名减去五个交易日前同一榜单排名。
- 如果五个交易日前不存在，则使用最近一个可用交易日。

加入榜单后的涨跌幅：

- 默认定义为股票首次进入某个榜单 Top N 后，到当前日期的价格涨跌幅。
- MVP 建议 `N = 100`。
- 入榜价格使用首次入榜后的下一个交易日收盘价，避免使用当日生成榜单时尚不可交易的价格。
- 如果股票跌出榜单后再次进入，保留第一次进入时间，同时可在详情页展示最近一次重新入榜时间。

### 9.2 机构信号模拟盘

MVP 默认模拟盘基于近一个月机构买入、机构持有和机构卖出信号组合，而不是综合分。

MVP 规则：

- 每周一再平衡。如果周一不是交易日，顺延到下一个交易日。
- 使用上一交易日生成的榜单作为信号，避免未来函数。
- 观察窗口为过去 21 个交易日，近似一个交易月。
- 买入候选来自过去 21 个交易日内曾进入“机构买入榜 Top 10”或“机构持有榜 Top 10”的股票。
- 只保留当前价格低于机构平均持有价格的股票。
- 最多持有 10 只股票。
- 使用简单透明的 `allocation_score` 计算目标仓位权重。
- 每次再平衡时，当前持仓和当期候选一起计算 `allocation_score`。
- 候选为空时，不主动卖出，保持原持仓。
- 单只股票目标权重最小 5%，最大 50%。
- 再平衡卖出时，优先卖出过去 21 个交易日内曾进入“机构卖出榜 Top 10”的持仓。
- 交易价格使用再平衡日收盘价。
- 只计算价格差，不计分红、交易成本和滑点。
- 对比基准：`SPY`、`QQQ`。

机构平均持有价格：

```text
institutional_avg_holding_price =
  total_tracked_value_usd / total_tracked_shares
```

说明：这个价格由 13F 披露的持仓价值和股数推导，接近报告期末披露价值，不代表机构真实买入成本。

榜单美元金额口径：

- 买入榜、卖出榜、新建仓榜、清仓榜按美元金额排序。
- 美元金额统一使用当前报告期价格口径。
- 新建仓金额使用当前报告期持仓价值。
- 增持金额使用增持股数乘以当前报告期价格。
- 减持金额使用减持股数乘以当前报告期价格。
- 清仓金额使用上一期持股数乘以当前报告期价格。

仓位权重分数：

```text
allocation_score =
  buying_top10_score +
  holding_top10_score +
  below_institution_avg_score +
  key_institution_buying_bonus +
  selling_top10_penalty
```

分数只用于模拟盘仓位，不作为页面通用股票评分。详细规则见 [metrics-spec.md](metrics-spec.md)。

模拟盘重点机构：

- 段永平 / H&H International Investment
- 李录 / Himalaya Capital Management
- 伯克希尔 / Berkshire Hathaway

展示指标：

- 累计收益
- 本周收益
- 年初至今收益
- 最大回撤
- 相对 `SPY` 超额收益
- 相对 `QQQ` 超额收益
- 当前持仓
- 现金占比
- 买入候选数量
- 持仓权重分数
- 因机构卖出榜优先卖出的持仓
- 历史换仓记录

## 10. 数据存储设计

静态 JSON 字段雏形见 [data-contract.md](data-contract.md)。
数据库 schema 见 [database-schema.md](database-schema.md)。
单文件配置规范见 [config-spec.md](config-spec.md)。
首页布局见 [frontend-layout.md](frontend-layout.md)。

主数据源建议使用 SQLite。静态网页消费 JSON 导出文件。

建议表：

- `securities`
- `index_memberships`
- `institution_managers`
- `institution_whitelist_versions`
- `sec_13f_filings`
- `institution_holdings`
- `holding_changes`
- `market_snapshots`
- `metric_snapshots`
- `rank_history`
- `sim_portfolio_snapshots`
- `sim_portfolio_positions`
- `disclosure_events`

## 11. 数据处理流水线

```text
SEC 13F / Longbridge / Index sources
        ↓
raw data ingestion
        ↓
SQLite raw tables
        ↓
13F parsing and normalization
        ↓
holding change calculation
        ↓
market snapshot refresh
        ↓
metric snapshot calculation
        ↓
rank history calculation
        ↓
simulation backtest
        ↓
static JSON export
        ↓
static site generation
```

建议任务：

- `ingest_13f`
- `parse_13f`
- `refresh_market_data`
- `compute_metric_snapshots`
- `recompute_rank_history`
- `recompute_simulation`
- `export_static_json`
- `build_static_site`

白名单修改后至少需要重跑：

```text
compute_metric_snapshots
recompute_rank_history
recompute_simulation
export_static_json
build_static_site
```

如果机构被新增到白名单但历史 13F 数据未抓取，则需要补跑该机构的历史 `ingest_13f`。

重点机构列表修改后至少需要重跑：

```text
compute_metric_snapshots
recompute_simulation
export_static_json
build_static_site
```

如果重点机构不在已抓取 13F 原始数据中，则需要先补跑该机构的历史 `ingest_13f`。

## 12. 数据更新时间

建议频率：

- 市场数据：每日交易日收盘后更新。
- 13F 数据：每日检查 SEC 新披露，13F 季报密集期可增加频率。
- 指数成分：每周或每月更新。
- 榜单：每日市场数据更新后生成。
- 模拟盘：每周再平衡后更新，同时保留每日净值。

## 13. MVP 验收标准

MVP 完成时应支持：

- 白名单机构 13F 触达股票池。
- 至少一批手工维护的机构白名单。
- 13F 原始数据抓取和结构化解析。
- 机构买卖变化计算。
- 长桥市场数据拉取。
- SQLite 主数据存储。
- 多维原始指标展示，不计算综合评分。
- 首页多维榜单。
- 股票详情页。
- 指数标签展示。
- 日排名和周排名变化。
- 机构信号周度再平衡模拟盘。
- 静态 JSON 导出和静态网页构建。

## 14. 待讨论问题

1. `Mag7` 是否只作为主题标签，还是也需要单独做一个 Mega Cap 科技股榜单。
3. 其他公开披露是否进入 MVP 榜单，还是只进入详情页事件流。
4. 佩洛西等 congressional disclosure 数据是否只做信息展示，还是参与独立榜单。
5. 模拟盘观察窗口是否固定为 21 个交易日，还是支持 1 个月、3 个月等可配置窗口。

## 15. 后续细化点

### 15.1 13F 持仓事实口径

MVP 尽可能合理处理，不在第一版过度复杂化：

- 股票身份以 CUSIP 为主，ticker 为展示字段。
- 每个 CUSIP 需要映射到当前 ticker，同时保留历史 ticker。
- 同一机构同一报告期如果存在 13F-HR/A 修订文件，应以后发布的 amendment 为准。
- 对期权类条目，`PUT`、`CALL` 与普通股分开存储，不直接计入普通股持仓变化。
- 对股票拆分、合并、ticker 变更，需要在持仓变化计算前做标准化。
- 详情页需要同时显示 `report_period` 和 `filing_date`。

### 15.2 其他公开披露模块

佩洛西等 congressional disclosure 与 13F 不同，需要单独建模：

- 披露金额通常是区间，不是精确金额。
- 交易披露可能存在延迟。
- 交易主体可能是本人、配偶或相关账户。
- 同一股票可能出现多笔买卖，需要按披露日期和交易日期分别展示。
- MVP 应优先作为详情页事件和标签，不建议直接混入 13F 主榜单。

建议：先做 `disclosure_events` 表，字段保留 `source`、`actor`、`relationship`、`transaction_date`、`disclosure_date`、`amount_range`、`transaction_type`。

### 15.3 页面交互细节

首页和详情页还需要细化：

- 首页默认展示 Top 100 还是 Top 500。
- 是否支持搜索 ticker / 公司名。
- 是否支持按指数标签、行业、市值区间、机构类型筛选。
- 原始指标是否支持 tooltip 解释，例如“11 / 200 表示 200 家追踪机构中有 11 家买入”。
- 股票详情页是否展示 13F 原始 filing 链接。
- 是否提供“本期新增入榜”“本周排名上升最快”等快捷视图。

建议：MVP 首页默认 Top 100，并提供搜索、指数标签筛选、市值区间筛选、榜单类型切换。

### 15.4 数据质量和运维

全静态生成也需要可观测性：

- 每次任务运行需要生成 run log。
- 抓取失败、解析失败、ticker 映射失败需要进入错误表。
- 构建产物需要显示最后成功更新时间。
- 如果市场数据更新失败，应保留上一版静态站点，而不是发布半成品。
- 关键数据源应记录 provenance，便于回查。

建议：MVP 增加 `data_quality_issues` 表和 `build_runs` 表。

### 15.5 合规和风险提示

产品应明确：

- 内容仅用于研究和信息展示，不构成投资建议。
- 13F 数据存在披露延迟。
- 机构持仓变化不代表机构当前仍然持有。
- 模拟盘是历史回测，不代表未来收益。
- 小市值股票潜力高但风险也高。

建议：在首页页脚、股票详情页和模拟盘区域都展示简短风险提示。
