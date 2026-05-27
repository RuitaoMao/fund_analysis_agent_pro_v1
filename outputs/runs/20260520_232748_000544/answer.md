以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH top_100_funds AS (SELECT s.fund_code, s.fund_name, s.fund_size FROM fund_size s WHERE s.date = (SELECT MAX(date) FROM fund_size) ORDER BY s.fund_size DESC LIMIT 100) SELECT p.fund_code AS 基金代码, p.fund_name AS 基金名称, ROUND(p.portfolio_return * 100, 2) AS 年度收益率 FROM top_100_funds t JOIN fund_performance p ON t.fund_code = p.fund_code WHERE p.period = '本年以来' ORDER BY p.portfolio_return DESC LIMIT 10
```

结果行数：10

[{'基金代码': '518880', '基金名称': '华安黄金易ETF', '年度收益率': 4.34}, {'基金代码': '510500', '基金名称': '南方中证500ETF', '年度收益率': 1.95}, {'基金代码': '000385', '基金名称': '景顺长城景颐双利债券(A类)', '年度收益率': 1.08}, {'基金代码': '511360', '基金名称': '海富通中证短融ETF', '年度收益率': 0.38}, {'基金代码': '004137', '基金名称': '博时合惠货币(B类)', '年度收益率': 0.35}, {'基金代码': '000602', '基金名称': '富国安益货币(A类)', '年度收益率': 0.35}, {'基金代码': '004776', '基金名称': '鹏华金元宝货币', '年度收益率': 0.35}, {'基金代码': '000759', '基金名称': '平安财富宝货币(A类)', '年度收益率': 0.35}, {'基金代码': '003391', '基金名称': '建信天添益货币(A类)', '年度收益率': 0.35}, {'基金代码': '004545', '基金名称': '永赢天天利货币(A类)', '年度收益率': 0.35}]

### 数据口径说明
- 未指定规模日期，使用最新的fund_size数据
- 未指定业绩区间，使用默认的'本年以来'
- 查询最新规模前100的基金中，按年度收益率排序，取前10名
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。