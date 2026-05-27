以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (SELECT fund_code, fund_name, fund_company, fund_size FROM fund_size WHERE date = (SELECT MAX(date) FROM fund_size WHERE fund_company LIKE '%易方达%') LIMIT 20) SELECT fp.fund_code AS 基金代码, fp.fund_name AS 基金名称, ROUND(fp.portfolio_return * 100, 2) AS 本年收益率 FROM fund_performance fp JOIN latest_size ls ON fp.fund_code = ls.fund_code WHERE fp.period = '本年以来' ORDER BY fp.portfolio_return DESC LIMIT 1
```

结果行数：1

[{'基金代码': '025046', '基金名称': '永赢元享稳健多资产90天持有期混合发起式(FOF)(A类)', '本年收益率': 1.91}]

### 数据口径说明
- 未指定规模日期，使用最新的fund_size数据；未指定业绩区间，默认使用'本年以来'
- 查询易方达公司最新规模前20基金中，本年以来收益率最高的基金
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。