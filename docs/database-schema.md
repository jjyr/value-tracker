# Value Tracker Storage Notes v0.2

Value Tracker 当前不维护中间数据库。这个文件保留为存储设计说明，方便之后判断是否需要重新引入更重的存储层。

## 当前持久化内容

```text
raw/generated/snapshot.yaml
raw/generated/historical/
raw/generated/cache/sec/
raw/generated/cache/longbridge-kline/
data/stockhunt.json
content/en/institutions/*.md
content/en/stocks/*.md
content/zh/institutions/*.md
content/zh/stocks/*.md
```

## 调试产物

`scripts/build_live_input.py` 仍可手工输出 live raw input，方便排查 Longbridge 数据和 CUSIP 映射问题。标准 `fetch` / `fetch-all` 流水线不会写这个中间文件。

## 设计原则

- 站点只消费 `data/stockhunt.json`。
- 当前机构变化快照从 raw input 和配置重算。
- 历史模拟盘从 SEC cache、K 线 cache 和配置重算，并保存为 JSONL store 以支持尾部增量续算。
- 外部抓取成本高的数据使用 cache 增量更新。
- checkpoint 只保存可重放的模拟状态；配置或映射 hash 变化时自动全量重算，避免旧状态残留。

## 何时重新引入更重存储

只有在以下需求出现时，才需要考虑重新引入数据库或对象存储索引：

- 需要跨多年、跨策略快速查询每一期机构变化。
- 需要多用户自定义白名单和策略并发生成。
- 需要在网页上提供复杂筛选、分页和全文搜索。
- 需要保留每次生成的审计记录和数据差异。
