"""业绩分析 tools。"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def get_top_funds_by_performance(store: SQLiteStore, args: dict) -> ToolResult:
    """查询收益率排名。

    这个 tool 在当前 workflow 中不是主要入口，主要给 joint tool 复用和未来扩展。
    """
    period = args.get("period") or "本年以来"
    top_n = int(args.get("top_n") or 10)

    sql = """
        SELECT
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            period AS 区间,
            ROUND(portfolio_return * 100, 2) AS 组合收益率,
            ROUND(benchmark_return * 100, 2) AS 基准收益率,
            ROUND(excess_return * 100, 2) AS 超额收益,
            ROUND(max_drawdown * 100, 2) AS 最大回撤
        FROM fund_performance
        WHERE period = :period
        ORDER BY portfolio_return DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, {"period": period, "top_n": top_n})

    return ToolResult(
        tool_name="get_top_funds_by_performance",
        intent="performance_ranking",
        tables={"performance_ranking": df_to_records(df)},
        notes=[f"业绩区间：{period}", "收益率字段已转为百分数显示。"],
        metadata={"period": period, "top_n": top_n},
    )
