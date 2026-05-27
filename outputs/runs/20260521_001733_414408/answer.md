以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (SELECT MAX(date) AS max_date FROM fund_size), latest_holding AS (SELECT MAX(date) AS max_date FROM fund_holding) SELECT fh.stock_code AS 股票代码, fh.stock_name AS 股票名称, SUM(fh.holding_value) AS 持仓总值 FROM fund_holding fh JOIN latest_holding lh ON fh.date = lh.max_date GROUP BY fh.stock_code, fh.stock_name ORDER BY 持仓总值 DESC LIMIT 1
```

结果行数：1

[{'股票代码': '300750', '股票名称': '宁德时代', '持仓总值': 253374305251.34}]

### 数据口径说明
- 未指定日期，使用 fund_size 和 fund_holding 最新日期
- 2026年为未来年份，假设用户意图为查询最新数据
- 查询最新持仓日期的所有股票，按持仓总值降序排列，取最大值的股票
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。