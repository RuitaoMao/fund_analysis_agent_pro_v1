以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (SELECT MAX(date) AS max_date FROM fund_size), latest_holding AS (SELECT MAX(date) AS max_date FROM fund_holding) SELECT h.stock_code AS 股票代码, h.stock_name AS 股票名称, SUM(h.holding_value) AS 持仓总价值 FROM fund_holding h JOIN latest_holding lh ON h.date = lh.max_date GROUP BY h.stock_code, h.stock_name ORDER BY 持仓总价值 DESC LIMIT 1
```

结果行数：1

[{'股票代码': '300750', '股票名称': '宁德时代', '持仓总价值': 253374305251.34}]

### 数据口径说明
- 未指定日期，使用 fund_size 和 fund_holding 最新日期
- 查询最新持仓日期的所有股票，按持仓总价值降序排序，取最大值对应的股票
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。