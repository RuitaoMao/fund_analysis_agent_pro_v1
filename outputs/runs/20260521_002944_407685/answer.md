以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH performance_q1 AS (
  SELECT fund_code, fund_name, period, portfolio_return
  FROM fund_performance
  WHERE period = '本年以来'
  ORDER BY portfolio_return DESC
  LIMIT 5
), latest_holding AS (
  SELECT h.fund_code, h.stock_code, h.holding_quantity, h.holding_value, s.fund_size
  FROM fund_holding h
  JOIN (SELECT fund_code, MAX(date) AS max_date FROM fund_holding GROUP BY fund_code) m ON h.fund_code = m.fund_code AND h.date = m.max_date
  JOIN fund_size s ON h.fund_code = s.fund_code AND s.date = (SELECT MAX(date) FROM fund_size)
)
SELECT
  p.fund_code AS "基金代码",
  p.fund_name AS "基金名称",
  ROUND(p.portfolio_return * 100, 2) AS "季度收益率(%)",
  SUM(h.holding_value) AS "持仓总值",
  MAX(h.holding_value) / SUM(h.holding_value) AS "最大持仓占比"
FROM performance_q1 p
JOIN latest_holding h ON p.fund_code = h.fund_code
GROUP BY p.fund_code, p.fund_name, p.portfolio_return
ORDER BY p.portfolio_return DESC
LIMIT 5;
```

结果行数：4

[{'基金代码': '016873', '基金名称': '广发远见智选混合(A类)', '季度收益率(%)': 58.03, '持仓总值': 192508954.14, '最大持仓占比': 0.12798042465122295}, {'基金代码': '020722', '基金名称': '国寿安保数字经济股票发起式(A类)', '季度收益率(%)': 45.38, '持仓总值': 12997894.57, '最大持仓占比': 0.14254493218281275}, {'基金代码': '513350', '基金名称': '富国标普石油天然气勘探及生产精选行业ETF(QDII)', '季度收益率(%)': 44.92, '持仓总值': 163031636.04, '最大持仓占比': 0.11305476941590532}, {'基金代码': '159518', '基金名称': '嘉实标普石油天然气勘探及生产精选行业ETF(QDII)', '季度收益率(%)': 44.91, '持仓总值': 341350451.19, '最大持仓占比': 0.11304581407604848}]

### 数据口径说明
- 业绩表默认 period 为 '本年以来'，即包含第一季度数据；持仓表使用最新日期数据；未指定具体日期，使用默认口径。
- 持仓集中度分析通过计算持仓占比最大前几名的股票比例。
- 收益率以百分比显示，保留两位小数。
- 查询首先筛选出季度收益率排名前5的基金，然后结合最新持仓数据，计算每个基金的持仓总值和最大持仓股票占比，以分析持仓集中度。
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。

### 自检修订说明
- 用户询问1季度口径，但回答没有说明季度末日期。