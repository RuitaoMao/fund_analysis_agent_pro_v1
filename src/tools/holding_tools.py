"""持仓分析 tools。"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def get_top_stocks_by_holding(store: SQLiteStore, args: dict) -> ToolResult:
    """查询股票持仓规模排名。"""
    date = args.get("date") or store.max_date("fund_holding")
    fund_codes = args.get("fund_codes")
    top_n = int(args.get("top_n") or 10)

    params: dict = {"date": date, "top_n": top_n}
    where = ["date = :date"]

    if fund_codes:
        placeholders = []
        for i, code in enumerate(fund_codes):
            key = f"fund_code_{i}"
            placeholders.append(f":{key}")
            params[key] = str(code).zfill(6)
        where.append(f"fund_code IN ({', '.join(placeholders)})")

    sql = f"""
        SELECT
            date AS 日期,
            stock_code AS 股票代码,
            stock_name AS 股票名称,
            ROUND(SUM(holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT fund_code) AS 涉及基金数量
        FROM fund_holding
        WHERE {' AND '.join(where)}
        GROUP BY date, stock_code, stock_name
        ORDER BY SUM(holding_value) DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    notes = [
        f"日期：{date}",
        "股票持仓按 股票代码 + 股票名称 聚合。",
    ]
    if fund_codes:
        notes.append(f"基金范围：{len(fund_codes)} 只基金。")
    else:
        notes.append("基金范围：全市场。")

    return ToolResult(
        tool_name="get_top_stocks_by_holding",
        intent="stock_holding_ranking",
        tables={"stock_holding_ranking": df_to_records(df)},
        notes=notes,
        metadata={"date": date, "fund_codes": fund_codes, "top_n": top_n},
    )
