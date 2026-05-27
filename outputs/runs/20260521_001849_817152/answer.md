以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (
  SELECT fund_code, fund_name, fund_company, fund_size, date
  FROM fund_size
  WHERE fund_name LIKE '%易方达%'
  ORDER BY date DESC
  LIMIT 1
)
SELECT fund_code AS 基金代码, fund_name AS 基金名称, fund_company AS 基金公司, fund_size AS 基金规模, date AS 规模日期
FROM latest_size
ORDER BY fund_size DESC
LIMIT 5
```

结果行数：1

[{'基金代码': '002216', '基金名称': '易方达量化策略A', '基金公司': '易方达', '基金规模': 1.223704645, '规模日期': '2026-03-31'}]

### 数据口径说明
- 未指定日期，使用 fund_size 最新数据
- 查询最新的易方达基金规模数据，按规模降序排列，取前5名
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。