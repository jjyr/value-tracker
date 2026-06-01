# StockHunt

StockHunt 是一个全静态生成的美股机构 13F 信号仪表盘。页面由 Hugo 构建，后台数据链路分为三层：

1. 原始接入：Longbridge 13F / 行情、后续 SEC 13F XML、指数成分等 provider 生成原始事实。
2. 后台规范化：原始 filing 和行情落 SQLite，计算机构变化、指标快照和排名历史。
3. 静态导出：normalized snapshot 转成 Hugo 读取的 `data/stockhunt.yaml`。

当前已经接入真实数据链路：`stockhunt-live-input` 会读取白名单机构，拉取 Longbridge 当前 13F holdings / changes 和行情估值，生成 backend 可消费的 raw YAML；完整 live build 还会从 SEC EDGAR 拉取历史 13F，并用 Longbridge 日 K 线生成 2024 至今的周度再平衡模拟盘。

## 目录

```text
config/stockhunt.yaml          主配置：白名单、重点机构、策略参数、数据路径
config/cusip-symbols.yaml      13F CUSIP -> Longbridge symbol 显式映射
raw/sample/13f_holdings.yaml   sample 原始 13F + 行情输入
raw/generated/                 本地生成产物，已 gitignore
data/stockhunt.yaml            Hugo 当前消费的数据
scripts/build_live_input.py    Longbridge live data -> raw input
scripts/historical_backtest.py SEC 历史 13F + Longbridge K 线 -> 周度回测
scripts/stockhunt_backend.py   raw input -> SQLite + normalized snapshot
scripts/generate_stockhunt_data.py
                               normalized snapshot -> Hugo data
```

不要把 SQLite 放进 Hugo 的 `data/` 目录。Hugo 会尝试加载 `data/*`，所以默认 SQLite 路径是 `raw/generated/stockhunt.sqlite`。

## 依赖

需要本机有：

```bash
uv
hugo
longbridge
```

首次使用真实 Longbridge 数据前，需要登录：

```bash
longbridge auth login
```

Python 依赖由 `uv` 根据 `pyproject.toml` / `uv.lock` 管理，不需要手动 `pip install`。

## 常用命令

查看可用命令：

```bash
uv run stockhunt-build-live --help
uv run stockhunt-build-sample --help
uv run stockhunt-check --help
uv run stockhunt-live-input --help
uv run stockhunt-backend --help
uv run stockhunt-backtest --help
uv run stockhunt-export --help
```

真实数据完整生成。默认会同时生成当前榜单、2024 至今历史周度回测、Hugo 数据和静态站点：

```bash
uv run stockhunt-build-live
```

跳过历史回测，只生成当前快照：

```bash
uv run stockhunt-build-live --skip-backtest
```

修改历史回测起点，例如从 2024 年开始：

```bash
uv run stockhunt-build-live --backtest-start 2024-01-01
```

真实数据 smoke test，只拉 1 个机构、Top 5 当前持仓：

```bash
uv run stockhunt-live-input --manager-limit 1 --top 5 --output /private/tmp/stockhunt-live-input.yaml
uv run stockhunt-backend --reset-db --raw /private/tmp/stockhunt-live-input.yaml --sqlite /private/tmp/stockhunt-live.sqlite --snapshot-output /private/tmp/stockhunt-live-snapshot.yaml
uv run stockhunt-export --snapshot /private/tmp/stockhunt-live-snapshot.yaml --output /private/tmp/stockhunt-live-hugo.yaml --skip-content
```

Sample 数据完整生成：

```bash
uv run stockhunt-build-sample
```

本地预览：

```bash
uv run hugo server
```

默认访问：

```text
http://localhost:1313/
```

## 生成流程

### 1. 拉取真实 raw input

```bash
uv run stockhunt-live-input
```

默认产物：

```text
raw/generated/live_13f_holdings.yaml
```

这个文件包含：

- `market`：Longbridge quote/static/calc-index 补出的价格、市值、PE、公司名、交易所。
- `filings`：白名单机构的当前 13F holdings 和根据 Longbridge changes 合成的 previous holdings。
- `warnings`：无法映射的 CUSIP、Longbridge 子命令失败等质量问题。

13F 没有 ticker，只有 CUSIP。`stockhunt-live-input` 只会导出能通过 `config/cusip-symbols.yaml` 明确映射到 symbol 的持仓。未映射持仓会进入 warnings，不会进入榜单。

### 2. 计算后台指标

```bash
uv run stockhunt-backend --reset-db --raw raw/generated/live_13f_holdings.yaml
```

默认产物：

```text
raw/generated/stockhunt.sqlite
raw/generated/snapshot.yaml
```

SQLite 中会写入：

- `sec_13f_filings`
- `institution_holdings`
- `holding_changes`
- `market_snapshots`
- `metric_snapshots`
- `rank_history`

### 3. 导出 Hugo 数据

```bash
uv run stockhunt-export --snapshot raw/generated/snapshot.yaml
```

默认产物：

```text
data/stockhunt.yaml
```

### 4. 生成历史周度回测

完整 live build 默认会跑这一步，也可以单独运行：

```bash
uv run stockhunt-backtest --start-date 2024-01-01
```

默认产物：

```text
raw/generated/historical_13f_holdings.yaml
raw/generated/historical_simulation.yaml
raw/generated/cache/
```

历史回测规则：

- 只使用 `filing_date <= rebalance_date` 的 13F，避免提前使用尚未披露的报告期数据。
- 每周一再平衡；如果周一休市，则顺延到下一个交易日。
- 每期按配置里的 `allocation_score` 规则选股和定权重，单只股票权重仍受配置里的最小/最大仓位约束。
- 价格使用 Longbridge 日 K 线，默认 `period=day`、`adjust=forward`。
- `raw/generated/cache/` 会缓存 SEC submissions、filing index、information table XML 和 Longbridge K 线，重复运行会复用缓存。

如果只想重新计算历史回测并导入 Hugo 数据：

```bash
uv run stockhunt-backtest --start-date 2024-01-01
uv run stockhunt-export --snapshot raw/generated/snapshot.yaml --simulation raw/generated/historical_simulation.yaml
uv run hugo --minify
```

### 5. 构建静态站点

```bash
uv run hugo --minify
```

默认产物：

```text
public/
```

## 重跑规则

### 全量重跑

适合修改白名单、CUSIP 映射、核心计算逻辑，或想从干净 SQLite 重新生成：

```bash
uv run stockhunt-build-live
```

完整 live build 会重新拉取当前 Longbridge raw input、重建 SQLite、重新计算历史回测、重新导出 Hugo 数据并构建 Hugo。后台步骤里的 `--reset-db` 会清空当前 SQLite 中 StockHunt 管理的表，然后重新写入当前输入文件里的 filings、holdings、market snapshots、metrics 和 rank history。

### 增量重跑

适合日常更新或重复跑同一个输入：

```bash
uv run stockhunt-build-live --incremental
```

当前增量语义：

- 不带 `--reset-db` 会保留已有 SQLite。
- 同一个 `accession_number` 会 upsert filing，并重写该 filing 下的 holdings。
- 同一个 `symbol + date + config_hash` 的 metrics 会重算覆盖。
- 同一个 `date + ranking_type + config_hash` 的 rank history 会重算覆盖。
- 如果 raw input 没有包含某个历史 filing，它会保留在 SQLite 里；如果需要严格等于当前输入，请用全量重跑。
- 历史回测本身总是按当前配置重新模拟；SEC 和 K 线请求会优先读 `raw/generated/cache/`。

### 只重建前端

如果只改了 Hugo 模板、CSS、JS 或文案：

```bash
uv run hugo --minify
```

### 只重导 Hugo 数据

如果已经有 `raw/generated/snapshot.yaml`，但改了 `scripts/generate_stockhunt_data.py` 或页面数据格式：

```bash
uv run stockhunt-export --snapshot raw/generated/snapshot.yaml
uv run hugo --minify
```

## 修改白名单

白名单在 `config/stockhunt.yaml`：

```yaml
institutions:
  whitelist_version: "2026-06-01-whitelist-v1"
  managers:
    - cik: "0001709323"
      name: "Himalaya Capital Management LLC"
      display_name: "李录"
      enabled: true
      style: "value"
```

修改规则：

- 新增机构：添加一条 `managers`，设置 `enabled: true`，并更新 `whitelist_version`。
- 暂停机构：保留配置但设置 `enabled: false`，并更新 `whitelist_version`。
- 修改展示名：改 `display_name`，建议也更新 `whitelist_version`，保证静态数据可追溯。
- CIK 必须能被标准化为 10 位数字，脚本会自动补前导 0。

修改白名单后推荐全量重跑：

```bash
uv run stockhunt-build-live
```

如果新增机构的持仓里出现大量 unmapped CUSIP，先补 `config/cusip-symbols.yaml`，再全量重跑。

## 修改 CUSIP 映射

映射在 `config/cusip-symbols.yaml`：

```yaml
mappings:
  "037833100":
    symbol: "AAPL.US"
    company_name: "Apple Inc."
    tags: ["Nasdaq 100", "S&P 500", "Mag7"]
```

修改后需要重新生成 live input，因为 raw input 里已经按映射过滤过持仓：

```bash
uv run stockhunt-build-live
```

历史回测也依赖同一份映射。补充 CUSIP 后重新运行完整 build，历史 SEC 13F 会按新映射重新解析；已缓存的 SEC 原始文件和 K 线会复用。

## 修改重点机构

重点机构在同一个配置文件：

```yaml
key_institutions:
  version: "2026-06-01-key-v1"
  members:
    - cik: "0001759760"
      display_name: "段永平"
      enabled: true
```

修改重点机构会影响：

- `key_institution_bought`
- 重点机构姓名标签
- 模拟盘 `allocation_score`
- 历史周度回测每一期的目标持仓和权重

如果原始 13F 已经在 SQLite 中，可以不重新拉 Longbridge，只重算后台和静态数据：

```bash
uv run stockhunt-backend --raw raw/generated/live_13f_holdings.yaml
uv run stockhunt-export --snapshot raw/generated/snapshot.yaml
uv run hugo --minify
```

如果新增的重点机构不在已有 raw input 中，需要先跑 `stockhunt-live-input`。

如果要让历史回测也使用新的重点机构规则，需要重新跑：

```bash
uv run stockhunt-backtest --start-date 2024-01-01
uv run stockhunt-export --snapshot raw/generated/snapshot.yaml --simulation raw/generated/historical_simulation.yaml
uv run hugo --minify
```

## 修改策略参数

策略在：

```yaml
strategy:
  allocation_score:
    buying_top10_score: 10
    holding_top10_score: 10
    below_institution_avg_score: 20
    buying_top10_key_institution_bonus: 10
    holding_top10_key_institution_bonus: 10
    selling_top10_penalty: -50
```

当前模拟盘默认由 `stockhunt-backtest` 生成完整历史周度回测：每个再平衡日用当时已经披露的 13F、当时价格和当前配置里的 `allocation_score` 规则重新选股，并输出净值曲线、当前持仓和调仓记录。

修改策略参数后，推荐重新跑完整 build：

```bash
uv run stockhunt-build-live
```

## 常用检查

查看后台库记录数量：

```bash
uv run sqlite3 raw/generated/stockhunt.sqlite \
  "select 'filings', count(*) from sec_13f_filings
   union all select 'holdings', count(*) from institution_holdings
   union all select 'changes', count(*) from holding_changes
   union all select 'metrics', count(*) from metric_snapshots
   union all select 'ranks', count(*) from rank_history;"
```

查看机构变化：

```bash
uv run sqlite3 raw/generated/stockhunt.sqlite \
  "select symbol, cik, status, round(change_value_usd, 2)
   from holding_changes
   order by symbol, cik;"
```

验证脚本语法和 Hugo 构建：

```bash
uv run stockhunt-check
```

底层拆步调试时，可以直接使用 `stockhunt-live-input`、`stockhunt-backend`、`stockhunt-backtest`、`stockhunt-export` 四个命令。

## 后续接入

1. Index tag adapter：自动维护 S&P 500、Nasdaq 100、Russell 1000/2000/3000、Mag7 标签。
2. 更完整的历史数据质量报告：展示未映射 CUSIP、退市股票、缺失价格和 13F value 单位修正。
3. 将 SEC 历史 13F 结果写入 SQLite，支持更细的历史榜单页面和多策略对比。
