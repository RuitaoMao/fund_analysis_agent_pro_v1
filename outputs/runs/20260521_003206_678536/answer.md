以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_date AS (SELECT MAX(date) AS max_date FROM fund_holding WHERE fund_code = '005827'),
     previous_date AS (SELECT date FROM fund_holding WHERE fund_code = '005827' AND date < (SELECT max_date FROM latest_date) ORDER BY date DESC LIMIT 1),
     current_holdings AS (
         SELECT stock_code, holding_quantity AS current_quantity
         FROM fund_holding
         WHERE fund_code = '005827' AND date = (SELECT max_date FROM latest_date)
     ),
     previous_holdings AS (
         SELECT stock_code, holding_quantity AS previous_quantity
         FROM fund_holding
         WHERE fund_code = '005827' AND date = (SELECT date FROM previous_date)
     ),
     combined AS (
         SELECT c.stock_code,
                c.current_quantity,
                p.previous_quantity
         FROM current_holdings c
         LEFT JOIN previous_holdings p ON c.stock_code = p.stock_code
         UNION ALL
         SELECT p.stock_code,
                c.current_quantity,
                p.previous_quantity
         FROM previous_holdings p
         LEFT JOIN current_holdings c ON p.stock_code = c.stock_code
         WHERE c.stock_code IS NULL
     ),
     change AS (
         SELECT stock_code,
                (COALESCE(current_quantity, 0) - COALESCE(previous_quantity, 0)) AS quantity_change
         FROM combined
     )
     SELECT stock_code, quantity_change
     FROM change
     ORDER BY ABS(quantity_change) DESC
     LIMIT 10
```

结果行数：10

[{'stock_code': '002027', 'quantity_change': -51000000.0}, {'stock_code': 'HK6618', 'quantity_change': -26400000.0}, {'stock_code': '000568', 'quantity_change': -1047400.0}, {'stock_code': 'HK9988', 'quantity_change': 720000.0}, {'stock_code': '600809', 'quantity_change': -647200.0}, {'stock_code': '000858', 'quantity_change': -366600.0}, {'stock_code': 'HK700', 'quantity_change': -260000.0}, {'stock_code': '600519', 'quantity_change': -221124.0}, {'stock_code': 'HK9987', 'quantity_change': 0.0}, {'stock_code': 'HK883', 'quantity_change': 0.0}]

### 数据口径说明
- 未指定日期，使用 fund_holding 最新数据日期
- 季度定义为连续的三个月
- 该查询计算基金005827在最近两次数据日期的持仓变化，筛选出变化最大的前十只股票
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。