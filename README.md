# StockHunt

StockHunt 是一个全静态生成的美股机构 13F 信号仪表盘。页面由 Hugo 构建，后台数据链路分为三层：

1. 原始接入：Longbridge 13F / 行情、后续 SEC 13F XML、指数成分等 provider 生成原始事实。
2. 后台规范化：原始 filing 和行情落 SQLite，计算机构变化、指标快照和排名历史。
3. 静态导出：normalized snapshot 转成 Hugo 读取的 `data/stockhunt.yaml`。

当前已经接入真实数据链路：`fetch` 会读取白名单机构，拉取 Longbridge 当前 13F holdings / changes 和行情估值，增量更新 SQLite；历史回测会从 SEC EDGAR 增量发现新的 13F，并用 Longbridge 日 K 线生成 2024 至今的周度再平衡模拟盘。

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
uv run build --help
uv run fetch --help
uv run fetch-all --help
uv run schedule --help
```

日常只构建静态站点：

```bash
uv run build
```

增量抓取数据，不运行 Hugo build：

```bash
uv run fetch
```

全量抓取数据：重建 SQLite，刷新 SEC filing cache 和 Longbridge 价格 cache，不运行 Hugo build：

```bash
uv run fetch-all
```

定时任务入口。daily 只更新价格、净值和收益，不碰 13F / SQLite，也不产生新调仓；weekly 会增量更新 13F / SQLite，并允许按规则调仓。两者执行完都会自动 build：

```bash
uv run schedule daily
uv run schedule weekly
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

### 1. 增量抓取数据

```bash
uv run fetch
```

这个命令会更新数据产物，但不会运行 Hugo build。默认产物：

```text
raw/generated/live_13f_holdings.yaml
raw/generated/stockhunt.sqlite
raw/generated/snapshot.yaml
raw/generated/historical_13f_holdings.yaml
raw/generated/historical_simulation.yaml
raw/generated/cache/
data/stockhunt.yaml
```

增量语义：

- SQLite 不清空，filing、holding、market snapshot、metrics、rank history 按唯一键 upsert。
- SEC submissions index 会刷新，用于发现新 13F；已有 filing index / information table XML 继续复用 cache。
- Longbridge 日 K 线按 symbol 追加缺失日期，不再按每个 `start/end` 重新抓整段。
- `data/stockhunt.yaml` 会重新导出，供后续 `build` 使用。

13F 没有 ticker，只有 CUSIP。数据抓取只会导出能通过 `config/cusip-symbols.yaml` 明确映射到 symbol 的持仓。未映射持仓会进入 warnings，不会进入榜单。

### 2. 全量抓取数据

```bash
uv run fetch-all
```

适合修改白名单、CUSIP 映射、核心计算逻辑，或想从干净 SQLite / cache 口径重新生成。它会：

- 对 SQLite 执行 reset 后重新写入。
- 刷新 SEC submissions、filing index、information table XML。
- 刷新 Longbridge 历史 K 线。
- 重新导出 `data/stockhunt.yaml`。

### 3. 历史回测规则

- 只使用 `filing_date <= rebalance_date` 的 13F，避免提前使用尚未披露的报告期数据。
- 每周一再平衡；如果周一休市，则顺延到下一个交易日。
- 每期按配置里的 `allocation_score` 规则选股和定权重，单只股票权重仍受配置里的最小/最大仓位约束。
- 价格使用 Longbridge 日 K 线，默认 `period=day`、`adjust=forward`。
- daily schedule 会把 `rebalance_until` 固定在上次调仓日，只追加 Longbridge 价格、更新模拟盘净值和收益，不改 13F、SQLite 和目标仓位。
- weekly schedule 不冻结 `rebalance_until`，因此会在新的周度调仓日产生调仓记录和新仓位。

### 4. 构建静态站点

```bash
uv run build
```

这个命令只执行 Hugo build，不抓数据、不改 SQLite、不重新回测。

### 5. 定时任务

推荐用两个 schedule：

```bash
# 每周调仓：美股周一收盘后，香港时间周二早上执行
uv run schedule weekly

# 每日价格和收益更新：美股其他交易日收盘后执行
uv run schedule daily
```

第一次部署先跑一次 `uv run schedule weekly` 或 `uv run fetch-all && uv run build`，生成带有 `last_rebalance_date` 的历史模拟文件；之后 daily 才能冻结上次调仓日，只更新价格和收益。

cron 示例：

```cron
# 周度调仓。周二跑；周三再跑一次用于覆盖周一美股休市顺延的情况。
30 8 * * 2,3 cd /Users/jjy/Workspace/stockhunt && uv run schedule weekly >> /tmp/schedule-weekly.log 2>&1

# 日常价格更新。避开周二/周三，防止和 weekly 重复。
30 8 * * 4-6 cd /Users/jjy/Workspace/stockhunt && uv run schedule daily >> /tmp/schedule-daily.log 2>&1
```

## 重跑规则

### 全量重跑

适合修改白名单、CUSIP 映射、核心计算逻辑，或想从干净 SQLite 重新生成：

```bash
uv run fetch-all
uv run build
```

`fetch-all` 会重新拉取当前 Longbridge raw input、重建 SQLite、刷新 SEC / K 线 cache、重新计算历史回测并导出 Hugo 数据。`build` 只把当前 `data/stockhunt.yaml` 构建成 `public/`。

### 增量重跑

适合日常更新或重复跑同一个输入：

```bash
uv run fetch
uv run build
```

当前增量语义：

- SQLite 会保留已有数据。
- 同一个 `accession_number` 会 upsert filing，并重写该 filing 下的 holdings。
- 同一个 `symbol + date + config_hash` 的 metrics 会重算覆盖。
- 同一个 `date + ranking_type + config_hash` 的 rank history 会重算覆盖。
- 如果 raw input 没有包含某个历史 filing，它会保留在 SQLite 里；如果需要严格等于当前输入，请用全量重跑。
- SEC 只刷新 submissions index 来发现新增 filing，已有 filing XML 继续使用 cache。
- 历史 K 线只追加缺失日期。
- 历史回测会按当前配置重新模拟，但底层 SEC 和 K 线抓取是增量的。

### 只重建前端

如果只改了 Hugo 模板、CSS、JS 或文案：

```bash
uv run build
```

### 只重导 Hugo 数据

如果已经有 `raw/generated/snapshot.yaml`，但改了 `scripts/generate_stockhunt_data.py` 或页面数据格式：

```bash
uv run python scripts/generate_stockhunt_data.py --snapshot raw/generated/snapshot.yaml
uv run build
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
uv run fetch-all
uv run build
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
uv run fetch-all
uv run build
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

如果只改重点机构配置，推荐重新增量计算并 build：

```bash
uv run fetch
uv run build
```

如果新增的重点机构本身不在白名单里，先修改白名单，再全量抓取：

```bash
uv run fetch-all
uv run build
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

当前模拟盘默认由历史周度回测生成：每个再平衡日用当时已经披露的 13F、当时价格和当前配置里的 `allocation_score` 规则重新选股，并输出净值曲线、当前持仓和调仓记录。

修改策略参数后，推荐重新抓取并 build：

```bash
uv run fetch
uv run build
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
uv run python -m py_compile scripts/build_live_input.py scripts/historical_backtest.py scripts/stockhunt_backend.py scripts/generate_stockhunt_data.py scripts/tasks.py
uv run build
```

日常只需要 `build`、`fetch`、`fetch-all`、`schedule`。底层拆步调试时，可以直接用 `uv run python scripts/*.py` 调用具体脚本。

## 后续接入

1. Index tag adapter：自动维护 S&P 500、Nasdaq 100、Russell 1000/2000/3000、Mag7 标签。
2. 更完整的历史数据质量报告：展示未映射 CUSIP、退市股票、缺失价格和 13F value 单位修正。
3. 将 SEC 历史 13F 结果写入 SQLite，支持更细的历史榜单页面和多策略对比。
