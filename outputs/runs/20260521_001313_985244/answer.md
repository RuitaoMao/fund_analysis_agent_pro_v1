以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (SELECT MAX(date) AS max_date FROM fund_size), latest_holding AS (SELECT MAX(date) AS max_date FROM fund_holding) SELECT s.asset_type AS 资产类型, SUM(s.fund_size) AS 持仓规模总和 FROM fund_size s JOIN latest_size ON s.date = latest_size.max_date JOIN fund_holding h ON s.fund_code = h.fund_code AND h.date = (SELECT max_date FROM latest_holding) GROUP BY s.asset_type LIMIT 200;
```

结果行数：6

[{'资产类型': 'FOF', '持仓规模总和': 7772.0945913056}, {'资产类型': '主动权益', '持仓规模总和': 389034.9536977445}, {'资产类型': '多资产投资', '持仓规模总和': 219201.131625834}, {'资产类型': '纯债', '持仓规模总和': 7901.9749553012}, {'资产类型': '被动权益', '持仓规模总和': 433640.4663012544}, {'资产类型': '量化', '持仓规模总和': 38592.473044557}]

### 数据口径说明
- 未指定日期，使用 fund_size 最新日期，持仓也使用最新日期
- 查询最新日期的基金规模表和持仓表，按资产类型汇总持仓规模总和
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。