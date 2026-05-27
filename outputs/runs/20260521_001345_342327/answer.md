以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH active_equity_funds AS (SELECT fp.fund_code, fp.fund_name, fp.portfolio_return, s.fund_company FROM fund_performance fp JOIN fund_size s ON fp.fund_code = s.fund_code WHERE fp.period = '本年以来' AND s.asset_type = '主动权益') SELECT fund_company AS 基金公司, ROUND(AVG(portfolio_return) * 100, 2) AS 平均收益百分比 FROM active_equity_funds GROUP BY fund_company ORDER BY 平均收益百分比 DESC LIMIT 10;
```

结果行数：10

[{'基金公司': '红土创新', '平均收益百分比': 14.46}, {'基金公司': '汇百川', '平均收益百分比': 9.24}, {'基金公司': '施罗德', '平均收益百分比': 7.93}, {'基金公司': '国寿安保', '平均收益百分比': 6.45}, {'基金公司': '路博迈', '平均收益百分比': 3.82}, {'基金公司': '先锋', '平均收益百分比': 3.64}, {'基金公司': '华商', '平均收益百分比': 3.22}, {'基金公司': '中庚', '平均收益百分比': 3.0}, {'基金公司': '中国人保资管', '平均收益百分比': 2.64}, {'基金公司': '博道', '平均收益百分比': 2.52}]

### 数据口径说明
- 默认业绩区间为'本年以来'。
- 查询主动权益基金在今年以来的平均收益，按公司分组，取前10名。
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。