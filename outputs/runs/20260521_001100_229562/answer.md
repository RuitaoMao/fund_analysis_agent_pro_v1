以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
SELECT fund_code AS 基金代码, fund_name AS 基金名称, fund_size AS 基金规模 FROM fund_size WHERE fund_name LIKE '%易方达%' ORDER BY date DESC LIMIT 1
```

结果行数：1

[{'基金代码': '002216', '基金名称': '易方达量化策略A', '基金规模': 1.223704645}]

### 数据口径说明
- 未指定日期，使用 fund_size 最新日期
- 查询包含“易方达”的基金的最新规模信息，按最新日期排序，限制返回一条记录
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。