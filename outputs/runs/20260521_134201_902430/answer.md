以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
SELECT h.date AS 日期, ROUND(SUM(h.holding_value) / 100000000.0, 2) AS 持有规模_亿元 FROM fund_holding h WHERE h.stock_name = '宁德时代' GROUP BY h.date ORDER BY h.date LIMIT 200;
```

结果行数：2

[{'日期': '2025-12-31', '持有规模_亿元': 2862.31}, {'日期': '2026-03-31', '持有规模_亿元': 2545.86}]

### 数据口径说明
- 使用 fund_holding 表中所有可用日期
- 持有规模通过 SUM(holding_value) 计算
- 股票名称匹配 '宁德时代'
- holding_value 单位假设为元，转换为亿元展示
- 按日期分组统计所有基金持有宁德时代的持仓市值总和，反映持有规模的时间变化趋势。
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。