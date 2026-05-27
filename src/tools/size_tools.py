"""规模分析 tools。"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def get_top_funds_by_size(store: SQLiteStore, args: dict) -> ToolResult:
    """查询基金规模排名。

    这是 SQL-backed tool：外部只传参数，内部使用固定 SQL 模板。
    LLM 不直接写 SQL，因此更安全、可审计。
    """
    date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")
    fund_company = args.get("fund_company")
    top_n = int(args.get("top_n") or 10)

    where = ["date = :date"]
    params = {"date": date, "top_n": top_n}

    if asset_type:
        where.append("asset_type = :asset_type")
        params["asset_type"] = asset_type
    if fund_company:
        # 基金公司常见简称，如“易方达”匹配“易方达基金”。这里用 LIKE 做宽松匹配。
        where.append("fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"

    sql = f"""
        SELECT
            date AS 日期,
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            fund_company AS 基金公司,
            asset_type AS 资产类型,
            ROUND(fund_size, 2) AS 基金规模
        FROM fund_size
        WHERE {' AND '.join(where)}
        ORDER BY fund_size DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    return ToolResult(
        tool_name="get_top_funds_by_size",
        intent="fund_size_ranking",
        tables={"fund_size_ranking": df_to_records(df)},
        notes=[
            f"日期：{date}",
            f"资产类型：{asset_type or '全类型'}",
            f"基金公司：{fund_company or '全市场'}",
            "规模口径来自规模表的基金规模字段。",
            "默认按基金代码/份额维度统计，不合并 A/C/E 等份额。",
        ],
        metadata={
            "date": date,
            "asset_type": asset_type,
            "fund_company": fund_company,
            "top_n": top_n,
        },
    )
