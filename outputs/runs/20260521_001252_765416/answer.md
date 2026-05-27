以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (SELECT fund_code, fund_name, fund_company, asset_type, fund_size, date FROM fund_size WHERE asset_type = '主动权益' ORDER BY date DESC LIMIT 1), previous_size AS (SELECT fund_code, fund_size, date FROM fund_size WHERE asset_type = '主动权益' ORDER BY date DESC LIMIT 1 OFFSET 1), size_change AS (SELECT l.fund_code, l.fund_name, l.fund_company, l.asset_type, l.fund_size AS latest_size, p.fund_size AS previous_size, (l.fund_size - p.fund_size) AS size_diff FROM latest_size l LEFT JOIN previous_size p ON l.fund_code = p.fund_code), top_growth AS (SELECT fund_code, fund_name, fund_company, asset_type, latest_size, size_diff FROM size_change ORDER BY size_diff DESC LIMIT 10) SELECT fund_code AS "基金代码", fund_name AS "基金名称", fund_company AS "基金公司", asset_type AS "资产类型", latest_size AS "最新规模", size_diff AS "规模增长" FROM top_growth LIMIT 200
```

结果行数：1

[{'基金代码': '001226', '基金名称': '中邮稳健添利', '基金公司': '中邮创业', '资产类型': '主动权益', '最新规模': 0.3509833031, '规模增长': None}]

### 数据口径说明
- 未指定日期，使用 fund_size 最新数据
- 通过获取最新一期和前一期的基金规模，计算规模变化，筛选增长最快的前10只主动权益基金
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。