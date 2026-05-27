当前问题暂时无法可靠回答。

- Execution failed on sql 'EXPLAIN QUERY PLAN WITH latest_size AS (
  SELECT fund_code, fund_name, fund_company, fund_size, date
  FROM fund_size
  WHERE fund_company IN ('易方达', '华夏')
  ORDER BY date DESC
  LIMIT 2
)
SELECT fund_name AS 基金名称, fund_company AS 基金公司, fund_size AS 规模(最新), date AS 日期
FROM latest_size
ORDER BY fund_company, date DESC
LIMIT 200;': near "(": syntax error
- Execution failed on sql 'EXPLAIN QUERY PLAN WITH latest_size AS ( SELECT fund_code, fund_name, fund_company, fund_size, date FROM fund_size WHERE fund_company IN ('易方达', '华夏') ORDER BY date DESC LIMIT 2 ) SELECT fund_name AS 基金名称, fund_company AS 基金公司, fund_size AS 规模(最新), date AS 日期 FROM latest_size ORDER BY fund_company, date DESC LIMIT 200;': near "(": syntax error
- Execution failed on sql 'EXPLAIN QUERY PLAN WITH latest_size AS ( SELECT fund_code, fund_name, fund_company, fund_size, date FROM fund_size WHERE fund_company IN ('易方达', '华夏') AND asset_type = '主动权益' ORDER BY date DESC LIMIT 200 ) SELECT fund_name AS 基金名称, fund_company AS 基金公司, fund_size AS 规模, date AS 日期 FROM latest_size WHERE asset_type = '主动权益' ORDER BY fund_company, date DESC LIMIT 200': no such column: asset_type