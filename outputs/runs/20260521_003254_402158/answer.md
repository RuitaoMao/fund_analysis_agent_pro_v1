以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH max_size_fund AS (SELECT s.fund_code, s.fund_name, s.fund_size FROM fund_size s WHERE s.asset_type = '主动权益' ORDER BY s.fund_size DESC LIMIT 1) SELECT p.fund_code AS '基金代码', p.fund_name AS '基金名称', ROUND(p.portfolio_return * 100, 2) AS '收益率(%)' FROM max_size_fund m JOIN fund_performance p ON m.fund_code = p.fund_code WHERE p.period = '本年以来' LIMIT 1
```

结果行数：1

[{'基金代码': '005827', '基金名称': '易方达蓝筹精选混合', '收益率(%)': -5.17}]

### 数据口径说明
- 未指定日期，使用 fund_size 最新日期，业绩区间默认 '本年以来'
- 先找到规模最大的主动权益基金，然后查询其本年度的收益表现
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。