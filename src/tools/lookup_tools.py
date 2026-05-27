"""基金检索 tools。"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def lookup_fund(store: SQLiteStore, args: dict) -> ToolResult:
    """根据基金代码或名称关键词检索基金。"""
    keyword = str(args.get("keyword") or "").strip()
    top_n = int(args.get("top_n") or 10)
    latest_date = store.max_date("fund_size")

    sql = """
        SELECT
            date AS 日期,
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            fund_company AS 基金公司,
            asset_type AS 资产类型,
            ROUND(fund_size, 2) AS 基金规模
        FROM fund_size
        WHERE date = :date
          AND (fund_code LIKE :kw OR fund_name LIKE :kw)
        ORDER BY fund_size DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, {"date": latest_date, "kw": f"%{keyword}%", "top_n": top_n})

    return ToolResult(
        tool_name="lookup_fund",
        intent="fund_lookup",
        tables={"fund_lookup": df_to_records(df)},
        notes=[f"检索关键词：{keyword}", f"日期：{latest_date}"],
        metadata={"keyword": keyword, "top_n": top_n, "date": latest_date},
    )
