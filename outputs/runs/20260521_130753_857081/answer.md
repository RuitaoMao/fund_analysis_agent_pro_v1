以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH top100 AS (SELECT fund_code FROM fund_size WHERE date = (SELECT MAX(date) FROM fund_size) ORDER BY fund_size DESC LIMIT 100) SELECT p.fund_code, p.fund_name, ROUND(p.portfolio_return * 100, 2) AS 收益率 FROM fund_performance p JOIN top100 t ON p.fund_code = t.fund_code WHERE p.period = '本年以来' ORDER BY p.portfolio_return DESC LIMIT 10
```

结果行数：10

[{'fund_code': '518880', 'fund_name': '华安黄金易ETF', '收益率': 4.34}, {'fund_code': '510500', 'fund_name': '南方中证500ETF', '收益率': 1.95}, {'fund_code': '000385', 'fund_name': '景顺长城景颐双利债券(A类)', '收益率': 1.08}, {'fund_code': '511360', 'fund_name': '海富通中证短融ETF', '收益率': 0.38}, {'fund_code': '004137', 'fund_name': '博时合惠货币(B类)', '收益率': 0.35}, {'fund_code': '000602', 'fund_name': '富国安益货币(A类)', '收益率': 0.35}, {'fund_code': '004776', 'fund_name': '鹏华金元宝货币', '收益率': 0.35}, {'fund_code': '000759', 'fund_name': '平安财富宝货币(A类)', '收益率': 0.35}, {'fund_code': '003391', 'fund_name': '建信天添益货币(A类)', '收益率': 0.35}, {'fund_code': '004545', 'fund_name': '永赢天天利货币(A类)', '收益率': 0.35}]

### 数据口径说明
- Latest fund size date is used
- Performance period defaults to '本年以来'
- Get top 100 funds by latest size, then join with performance for default period and select top 10 by return
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。