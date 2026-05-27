以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_date AS (SELECT MAX(date) AS max_date FROM fund_holding WHERE fund_code = '005827'),
     previous_date AS (SELECT MAX(date) FROM fund_holding WHERE fund_code = '005827' AND date < (SELECT max_date FROM latest_date)),
     current_holdings AS (
         SELECT stock_code, holding_quantity AS current_quantity
         FROM fund_holding
         WHERE fund_code = '005827' AND date = (SELECT max_date FROM latest_date)
     ),
     previous_holdings AS (
         SELECT stock_code, holding_quantity AS previous_quantity
         FROM fund_holding
         WHERE fund_code = '005827' AND date = (SELECT MAX(date) FROM fund_holding WHERE fund_code = '005827' AND date < (SELECT max_date FROM latest_date))
     ),
     combined AS (
         SELECT c.stock_code,
                c.current_quantity,
                p.previous_quantity,
                (COALESCE(c.current_quantity, 0) - COALESCE(p.previous_quantity, 0)) AS change_quantity
         FROM current_holdings c
         FULL OUTER JOIN previous_holdings p ON c.stock_code = p.stock_code
     )
SELECT stock_code AS "股票代码",
       change_quantity AS "持仓变化量"
FROM combined
ORDER BY ABS(change_quantity) DESC
LIMIT 10;
```

结果行数：10

[{'股票代码': '002027', '持仓变化量': -51000000.0}, {'股票代码': 'HK6618', '持仓变化量': -26400000.0}, {'股票代码': '000568', '持仓变化量': -1047400.0}, {'股票代码': 'HK9988', '持仓变化量': 720000.0}, {'股票代码': '600809', '持仓变化量': -647200.0}, {'股票代码': '000858', '持仓变化量': -366600.0}, {'股票代码': 'HK700', '持仓变化量': -260000.0}, {'股票代码': '600519', '持仓变化量': -221124.0}, {'股票代码': 'HK9987', '持仓变化量': 0.0}, {'股票代码': 'HK883', '持仓变化量': 0.0}]

### 数据口径说明
- 未指定日期，使用 fund_holding 最新数据
- 季度定义为最近两个完整季度
- 该查询比较基金005827最近两个季度的持仓，计算每只股票的持仓变化量，并选出变化最大的前十只股票。
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。