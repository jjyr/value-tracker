# Value Tracker

价值追踪是一个全静态生成的美股机构 13F 信号仪表盘。Value Tracker tracks the trades of value investors, helping you see what they buy, sell, and hold over time.

页面由 Hugo 构建，后台数据链路现在保持为轻量的文件流水线：

1. 原始接入：Longbridge 当前 13F / 行情、SEC 历史 13F、Longbridge 日 K 线等 provider 生成原始事实或更新缓存。
2. 规范化：`scripts/stockhunt_backend.py` 在内存中比较最近两个 13F 报告期，生成 `raw/generated/snapshot.yaml`。
3. 回测：`scripts/historical_backtest.py` 基于 SEC cache 和 Longbridge K 线 cache 生成 2024 至今的周度模拟盘。
4. 静态导出：`scripts/generate_stockhunt_data.py` 合并 snapshot 和模拟盘，输出 Hugo 读取的 `data/stockhunt.yaml`。

流水线不再维护中间数据库，也不把 live raw input 作为标准产物落盘。可持久化的是最终 YAML 和外部数据 cache。

## 目录

```text
config/stockhunt.yaml          主配置：白名单、重点机构、策略参数
config/cusip-symbols.yaml      13F CUSIP -> Longbridge symbol 显式映射
raw/sample/13f_holdings.yaml   sample 原始 13F + 行情输入
raw/generated/                 本地生成产物，已 gitignore
raw/generated/cache/           SEC / Longbridge K 线 cache
data/stockhunt.yaml            Hugo 当前消费的数据
scripts/build_live_input.py    Longbridge live data 调试入口
scripts/stockhunt_backend.py   raw input -> normalized snapshot
scripts/historical_backtest.py SEC 历史 13F + Longbridge K 线 -> 周度回测
scripts/generate_stockhunt_data.py
                               snapshot + simulation -> Hugo data
```

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

只构建静态站点：

```bash
uv run build
```

增量抓取数据，不运行 Hugo build：

```bash
uv run fetch
```

全量抓取数据，刷新 SEC filing cache 和 Longbridge 价格 cache，不运行 Hugo build：

```bash
uv run fetch-all
```

定时任务入口。`daily` 只更新价格、净值和收益，不产生新调仓；`weekly` 会增量更新 13F，并允许按规则调仓。两者执行完都会自动 build：

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

## GitHub Pages 部署

仓库已支持 GitHub Actions 部署到 GitHub Pages。

GitHub Pages 设置：

```text
Settings -> Pages -> Build and deployment -> Source: GitHub Actions
```

### Workflow

```text
.github/workflows/deploy.yml
.github/workflows/schedule.yml
```

`deploy.yml`：

- `main` 分支 push 或手动触发。
- 使用仓库当前 `data/stockhunt.yaml` 构建 Hugo。
- 发布 `public/` 到 GitHub Pages。

`schedule.yml`：

- 手动触发时可选择 `daily`、`weekly`、`fetch-all`。
- 定时触发：
  - 周二/周三 09:30 Asia/Shanghai 跑 `weekly`。
  - 周四/周五/周六 09:30 Asia/Shanghai 跑 `daily`。
- 恢复 `raw/generated/cache/`、`snapshot.yaml`、`historical_simulation.yaml` 的 Actions cache。
- 跑完数据任务后发布 GitHub Pages。

### Longbridge Secrets

定时数据任务需要 GitHub Secrets：

```text
LONGBRIDGE_CLIENT_ID
LONGBRIDGE_TOKEN_FILE_B64
```

本地登录 Longbridge 后，token 文件在：

```text
~/.longbridge/openapi/tokens/<client_id>
```

生成 secrets：

```bash
CLIENT_ID="$(ls "$HOME/.longbridge/openapi/tokens" | head -n 1)"
printf "%s" "$CLIENT_ID"
base64 -i "$HOME/.longbridge/openapi/tokens/$CLIENT_ID" | tr -d '\n'
```

把第一行输出填入 `LONGBRIDGE_CLIENT_ID`，第二行输出填入 `LONGBRIDGE_TOKEN_FILE_B64`。

### 首次部署

第一次建议手动运行：

```text
Actions -> Update Data and Deploy Pages -> Run workflow -> mode: fetch-all
```

之后定时任务会复用 Actions cache。若 cache 丢失，`daily` 会自动 fallback 到 `fetch-all`。

## 生成流程

### 1. 增量抓取数据

```bash
uv run fetch
```

默认产物：

```text
raw/generated/snapshot.yaml
raw/generated/historical_simulation.yaml
raw/generated/cache/sec/
raw/generated/cache/longbridge-kline/
data/stockhunt.yaml
content/institutions/*.md
```

增量语义：

- Longbridge 当前 13F / 行情会在内存中生成 live raw input。
- 当前 snapshot 每次都从 raw input 和配置纯内存重算，不保留中间状态。
- SEC submissions index 会刷新，用于发现新 13F；已有 filing index / information table XML 继续复用 cache。
- Longbridge 日 K 线按 symbol 追加缺失日期，不再按每个 `start/end` 重新抓整段。
- 历史回测会按当前配置重新模拟，但底层 SEC 和 K 线抓取是增量的。
- `data/stockhunt.yaml` 会重新导出，供后续 `build` 使用。
- `content/institutions/*.md` 会按白名单机构自动补齐，用于机构详情页。

13F 没有 ticker，只有 CUSIP。数据抓取只会导出能通过 `config/cusip-symbols.yaml` 明确映射到 symbol 的持仓。未映射持仓会进入 warnings，不会进入榜单。

### 2. 全量抓取数据

```bash
uv run fetch-all
```

适合修改白名单、CUSIP 映射、核心计算逻辑，或想刷新外部数据 cache 口径。它会：

- 重新生成当前 Longbridge raw input。
- 刷新 SEC submissions、filing index、information table XML。
- 刷新 Longbridge 历史 K 线。
- 重新计算 snapshot、历史回测和 `data/stockhunt.yaml`。

### 3. 历史回测规则

- 只使用 `filing_date <= rebalance_date` 的 13F，避免提前使用尚未披露的报告期数据。
- 每周一再平衡；如果周一休市，则顺延到下一个交易日。
- 每期候选池由“当前模拟盘持仓 + 本期重点机构新建仓或增持股票”组成，普通白名单机构只用于榜单、标签和辅助信息。
- 目标权重按重点机构信号分线性分配；当前价低于重点机构最近买入价会提高权重，重点机构减持或清仓会降低权重。
- 买卖按当前仓位到目标仓位的权重缺口分步成交：买入缺口 `< 5%` 时不买，买入缺口 `5%~20%` 时补齐到目标，买入缺口 `> 20%` 时本次买入 `20%` 组合权重；卖出不设 5% 最小缺口，每周最多卖出 `20%` 组合权重，尾仓可直接卖到目标。
- 价格使用 Longbridge 日 K 线，默认 `period=day`、`adjust=forward`。
- `schedule daily` 会把 `rebalance_until` 固定在上次调仓日，只追加 Longbridge 价格、更新模拟盘净值和收益，不改目标仓位。
- `schedule weekly` 不冻结 `rebalance_until`，因此会在新的周度调仓日产生调仓记录和新仓位。

### 4. 构建静态站点

```bash
uv run build
```

这个命令只执行 Hugo build，不抓数据、不重新回测。

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

```bash
uv run fetch-all
uv run build
```

`fetch-all` 会重新拉取当前 Longbridge live raw input、刷新 SEC / K 线 cache、重新计算 snapshot 和历史回测并导出 Hugo 数据。`build` 只把当前 `data/stockhunt.yaml` 构建成 `public/`。

### 增量重跑

```bash
uv run fetch
uv run build
```

当前增量语义：

- 当前 raw input 和 snapshot 每次重算。
- SEC 只刷新 submissions index 来发现新增 filing，已有 filing XML 继续使用 cache。
- 历史 K 线只追加缺失日期。
- 历史回测会按当前配置重新模拟。

### 只重建前端

如果只改了 Hugo 模板、CSS、JS 或文案：

```bash
uv run build
```

### 只重导 Hugo 数据

如果已经有 `raw/generated/snapshot.yaml` 和 `raw/generated/historical_simulation.yaml`，但改了 `scripts/generate_stockhunt_data.py` 或页面数据格式：

```bash
uv run python scripts/generate_stockhunt_data.py \
  --snapshot raw/generated/snapshot.yaml \
  --simulation raw/generated/historical_simulation.yaml
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

- 重点机构姓名标签
- 模拟盘候选池和目标权重
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
  max_positions: 10
  max_position_weight_pct: 50
  weighting_method: "key_institution_signal_score"
  score_weight_exponent: 1.0
  rebalance_step_weight_pct: 20
  min_buy_gap_weight_pct: 5
  allocation_signal:
    key_new_position_score: 30
    key_added_score: 20
    key_holding_score: 8
    key_buy_intensity_score_per_pct: 8
    key_buy_intensity_max_score: 40
    below_key_latest_buy_price_bonus: 15
    multiple_key_institution_bonus: 8
    key_reduced_penalty: -15
    key_exit_penalty: -50
```

当前模拟盘默认由历史周度回测生成：每个再平衡日只使用当时已经披露的 13F 和当时价格。候选池只由当前模拟盘持仓和本期重点机构新建仓或增持股票组成。目标权重按重点机构信号分计算，分数使用 `score_weight_exponent` 做幂次归一化，默认 `1.0` 即按分数线性分配。重点机构买入金额占该机构 13F 组合比例越高，`key_buy_intensity_score_per_pct` 贡献的力度分越高，并由 `key_buy_intensity_max_score` 封顶。买卖节奏由 `rebalance_step_weight_pct` 和 `min_buy_gap_weight_pct` 控制：买卖每周最多按 `rebalance_step_weight_pct` 的组合权重执行，买入缺口低于 `min_buy_gap_weight_pct` 时跳过，卖出不设最小缺口以避免尾仓残留。

修改策略参数后，推荐重新抓取并 build：

```bash
uv run fetch
uv run build
```

## 常用检查

验证脚本语法和 Hugo 构建：

```bash
uv run python -m py_compile scripts/build_live_input.py scripts/historical_backtest.py scripts/stockhunt_backend.py scripts/generate_stockhunt_data.py scripts/tasks.py
uv run build
```

检查关键产物：

```bash
ls -lh raw/generated/snapshot.yaml raw/generated/historical_simulation.yaml data/stockhunt.yaml
```

日常只需要 `build`、`fetch`、`fetch-all`、`schedule`。底层拆步调试时，可以直接用 `uv run python scripts/*.py` 调用具体脚本。

## 后续接入

1. Index tag adapter：自动维护 S&P 500、Nasdaq 100、Russell 1000/2000/3000、Mag7 标签。
2. 更完整的历史数据质量报告：展示未映射 CUSIP、退市股票、缺失价格和 13F value 单位修正。
3. 为机构详情页补充更细的历史变动图表。
