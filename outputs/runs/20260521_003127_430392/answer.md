以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。

```sql
WITH latest_size AS (SELECT fund_code, fund_name, fund_company, fund_size, date FROM fund_size WHERE fund_company IN ('易方达', '华夏') AND asset_type = '主动权益' ORDER BY date DESC LIMIT 2) SELECT fund_name AS 基金名称, fund_company AS 基金公司, fund_size AS 规模, date AS 日期 FROM latest_size ORDER BY date DESC LIMIT 200
```

结果行数：2

[{'基金名称': '华夏信兴回报A', '基金公司': '华夏', '规模': 1.363098237, '日期': '2026-03-31'}, {'基金名称': '华夏行业龙头', '基金公司': '华夏', '规模': 4.7457156683, '日期': '2026-03-31'}]

### 数据口径说明
- 未指定日期，使用 fund_size 最新数据
- 查询易方达和华夏主动权益基金最新两期规模数据，用于对比近几个季度的增长情况
- SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。