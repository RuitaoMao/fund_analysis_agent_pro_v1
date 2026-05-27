"""跨表聚合分析 tools — 填补"基金公司 × 股票持仓"这条能力线。

这些工具都需要 fund_holding 与 fund_size 的 JOIN，过去 hard 模式没有对应工具，
LLM planner 经常错配到 find_funds_holding_stock（只回答基金维度），
导致"持仓贵州茅台最多的基金公司是谁"这类问题答非所问。
"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def _resolve_stock_filter(stock_keyword: str | None, params: dict) -> str:
    """构造 stock 过滤条件。支持代码或名称模糊匹配。"""
    if not stock_keyword:
        raise ValueError("stock_keyword 不能为空。")
    keyword = str(stock_keyword).strip()
    # 6 位股票代码做精确匹配，避免误命中
    if keyword.isdigit() and len(keyword) == 6:
        params["stock_code"] = keyword
        return "h.stock_code = :stock_code"
    params["stock_name"] = f"%{keyword}%"
    return "h.stock_name LIKE :stock_name"


def _company_filter(companies: list[str] | None, params: dict, alias: str = "s") -> str:
    """构造 company 过滤条件。"""
    if not companies:
        return "1=1"
    if isinstance(companies, str):
        companies = [companies]
    clauses = []
    for i, company in enumerate(companies):
        key = f"company_{i}"
        clauses.append(f"{alias}.fund_company LIKE :{key}")
        params[key] = f"%{company}%"
    return "(" + " OR ".join(clauses) + ")"


def rank_companies_by_stock_holding(store: SQLiteStore, args: dict) -> ToolResult:
    """哪些基金公司持仓某股票规模最大。

    用例：现在持仓贵州茅台最多的基金公司是谁。
    """
    date = args.get("date") or store.max_date("fund_holding")
    stock_keyword = args.get("stock_keyword")
    top_n = int(args.get("top_n") or 10)
    asset_type = args.get("asset_type")

    params: dict = {"date": date, "top_n": top_n}
    where = ["h.date = :date", _resolve_stock_filter(stock_keyword, params)]
    if asset_type:
        where.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type

    sql = f"""
        SELECT
            s.fund_company AS 基金公司,
            ROUND(SUM(h.holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT s.fund_code) AS 持仓基金数,
            ROUND(AVG(h.nav_ratio), 4) AS 平均净值占比
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE {' AND '.join(where)}
        GROUP BY s.fund_company
        ORDER BY SUM(h.holding_value) DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    notes = [
        f"日期：{date}",
        f"股票关键词：{stock_keyword}",
        "按 fund_company 聚合 fund_holding 与 fund_size 的 JOIN 结果。",
    ]
    if asset_type:
        notes.append(f"资产类型过滤：{asset_type}")

    return ToolResult(
        tool_name="rank_companies_by_stock_holding",
        intent="company_stock_holding_ranking",
        tables={"company_stock_holding_ranking": df_to_records(df)},
        notes=notes,
        metadata={"date": date, "stock_keyword": stock_keyword, "asset_type": asset_type, "top_n": top_n},
    )


def get_stock_company_distribution(store: SQLiteStore, args: dict) -> ToolResult:
    """某股票在各基金公司的持仓分布（含占比）。

    用例：贵州茅台被哪些基金公司持有，占比是多少。
    """
    date = args.get("date") or store.max_date("fund_holding")
    stock_keyword = args.get("stock_keyword")
    top_n = int(args.get("top_n") or 20)

    params: dict = {"date": date, "top_n": top_n}
    stock_clause = _resolve_stock_filter(stock_keyword, params)
    where = ["h.date = :date", stock_clause]

    sql = f"""
        WITH base AS (
            SELECT s.fund_company, h.holding_value, h.fund_code
            FROM fund_holding h
            JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE {' AND '.join(where)}
        ), total AS (
            SELECT SUM(holding_value) AS total_value FROM base
        )
        SELECT
            base.fund_company AS 基金公司,
            ROUND(SUM(base.holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT base.fund_code) AS 持仓基金数,
            ROUND(SUM(base.holding_value) / NULLIF((SELECT total_value FROM total), 0) * 100, 2) AS 公司占比百分比
        FROM base
        GROUP BY base.fund_company
        ORDER BY SUM(base.holding_value) DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    return ToolResult(
        tool_name="get_stock_company_distribution",
        intent="stock_company_distribution",
        tables={"stock_company_distribution": df_to_records(df)},
        notes=[f"日期：{date}", f"股票关键词：{stock_keyword}", "公司占比 = 该公司持仓 / 全市场基金对该股票持仓合计"],
        metadata={"date": date, "stock_keyword": stock_keyword, "top_n": top_n},
    )


def rank_funds_holding_stock_by_value(store: SQLiteStore, args: dict) -> ToolResult:
    """持有某股票的基金按持仓规模排名，附带基金公司、资产类型信息。

    增强版 find_funds_holding_stock：按 holding_value 严格排序，并 JOIN 出公司维度。
    用例：哪些基金持仓宁德时代最多，分别属于哪家公司。
    """
    date = args.get("date") or store.max_date("fund_holding")
    stock_keyword = args.get("stock_keyword")
    top_n = int(args.get("top_n") or 20)
    fund_company = args.get("fund_company")
    asset_type = args.get("asset_type")

    params: dict = {"date": date, "top_n": top_n}
    stock_clause = _resolve_stock_filter(stock_keyword, params)
    where = ["h.date = :date", stock_clause]
    if fund_company:
        where.append("s.fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"
    if asset_type:
        where.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type

    sql = f"""
        SELECT
            s.fund_code AS 基金代码,
            s.fund_name AS 基金名称,
            s.fund_company AS 基金公司,
            s.asset_type AS 资产类型,
            ROUND(h.holding_value, 2) AS 持仓规模,
            ROUND(h.nav_ratio, 4) AS 净值占比,
            ROUND(s.fund_size, 2) AS 基金规模
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE {' AND '.join(where)}
        ORDER BY h.holding_value DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    notes = [f"日期：{date}", f"股票关键词：{stock_keyword}", "按 holding_value 严格降序排序。"]
    if fund_company:
        notes.append(f"基金公司过滤：{fund_company}")
    if asset_type:
        notes.append(f"资产类型过滤：{asset_type}")

    return ToolResult(
        tool_name="rank_funds_holding_stock_by_value",
        intent="stock_holder_funds_ranked",
        tables={"funds_holding_stock_ranked": df_to_records(df)},
        notes=notes,
        metadata={
            "date": date,
            "stock_keyword": stock_keyword,
            "fund_company": fund_company,
            "asset_type": asset_type,
            "top_n": top_n,
        },
    )


def get_company_stock_holding_breakdown(store: SQLiteStore, args: dict) -> ToolResult:
    """某基金公司持有某股票的明细（拆解到旗下每只基金）。

    用例：易方达持有贵州茅台的明细是怎样的，旗下哪几只基金贡献最大。
    """
    date = args.get("date") or store.max_date("fund_holding")
    stock_keyword = args.get("stock_keyword")
    fund_company = args.get("fund_company") or (args.get("companies") or [None])[0]
    top_n = int(args.get("top_n") or 30)

    if not fund_company:
        raise ValueError("get_company_stock_holding_breakdown 需要 fund_company 或 companies。")

    params: dict = {"date": date, "top_n": top_n, "fund_company": f"%{fund_company}%"}
    stock_clause = _resolve_stock_filter(stock_keyword, params)

    sql = f"""
        SELECT
            s.fund_code AS 基金代码,
            s.fund_name AS 基金名称,
            s.asset_type AS 资产类型,
            ROUND(h.holding_value, 2) AS 持仓规模,
            ROUND(h.nav_ratio, 4) AS 净值占比,
            ROUND(s.fund_size, 2) AS 基金规模
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE h.date = :date
          AND s.fund_company LIKE :fund_company
          AND {stock_clause}
        ORDER BY h.holding_value DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    # 同时给出公司汇总数据
    summary_sql = f"""
        SELECT
            ROUND(SUM(h.holding_value), 2) AS 公司持仓总规模,
            COUNT(DISTINCT s.fund_code) AS 持仓基金数,
            ROUND(AVG(h.nav_ratio), 4) AS 平均净值占比,
            ROUND(MAX(h.nav_ratio), 4) AS 最高净值占比
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE h.date = :date
          AND s.fund_company LIKE :fund_company
          AND {stock_clause}
    """
    summary_df = store.query_df(summary_sql, params)

    return ToolResult(
        tool_name="get_company_stock_holding_breakdown",
        intent="company_stock_breakdown",
        tables={
            "company_stock_summary": df_to_records(summary_df),
            "company_stock_breakdown": df_to_records(df),
        },
        notes=[f"日期：{date}", f"基金公司：{fund_company}", f"股票关键词：{stock_keyword}"],
        metadata={"date": date, "fund_company": fund_company, "stock_keyword": stock_keyword, "top_n": top_n},
    )


def compare_companies_stock_holding(store: SQLiteStore, args: dict) -> ToolResult:
    """对比多家基金公司对同一只股票的持仓。

    用例：易方达和华夏谁更看好宁德时代。
    """
    date = args.get("date") or store.max_date("fund_holding")
    stock_keyword = args.get("stock_keyword")
    companies = args.get("companies") or []
    if isinstance(companies, str):
        companies = [companies]
    if not companies:
        raise ValueError("compare_companies_stock_holding 至少需要 1 家公司。")

    params: dict = {"date": date}
    stock_clause = _resolve_stock_filter(stock_keyword, params)
    company_clause = _company_filter(companies, params)

    sql = f"""
        SELECT
            s.fund_company AS 基金公司,
            ROUND(SUM(h.holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT s.fund_code) AS 持仓基金数,
            ROUND(AVG(h.nav_ratio), 4) AS 平均净值占比,
            ROUND(MAX(h.nav_ratio), 4) AS 最高净值占比
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE h.date = :date
          AND {stock_clause}
          AND {company_clause}
        GROUP BY s.fund_company
        ORDER BY SUM(h.holding_value) DESC
    """
    df = store.query_df(sql, params)

    return ToolResult(
        tool_name="compare_companies_stock_holding",
        intent="company_stock_comparison",
        tables={"company_stock_comparison": df_to_records(df)},
        notes=[f"日期：{date}", f"股票关键词：{stock_keyword}", f"对比公司：{companies}"],
        metadata={"date": date, "stock_keyword": stock_keyword, "companies": companies},
    )


def rank_stocks_by_company_concentration(store: SQLiteStore, args: dict) -> ToolResult:
    """找出被基金公司最集中持有的股票（多公司同时重仓）。

    用例：哪些股票被最多基金公司同时持有，公募集中度最高的股票是什么。
    """
    date = args.get("date") or store.max_date("fund_holding")
    top_n = int(args.get("top_n") or 20)
    asset_type = args.get("asset_type")
    min_companies = int(args.get("min_companies") or 2)

    params: dict = {"date": date, "top_n": top_n, "min_companies": min_companies}
    where = ["h.date = :date"]
    if asset_type:
        where.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type

    sql = f"""
        SELECT
            h.stock_code AS 股票代码,
            h.stock_name AS 股票名称,
            COUNT(DISTINCT s.fund_company) AS 持仓基金公司数,
            COUNT(DISTINCT s.fund_code) AS 持仓基金数,
            ROUND(SUM(h.holding_value), 2) AS 全市场持仓规模
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE {' AND '.join(where)}
        GROUP BY h.stock_code, h.stock_name
        HAVING COUNT(DISTINCT s.fund_company) >= :min_companies
        ORDER BY COUNT(DISTINCT s.fund_company) DESC, SUM(h.holding_value) DESC
        LIMIT :top_n
    """
    df = store.query_df(sql, params)

    notes = [f"日期：{date}", f"至少 {min_companies} 家公司同时持有"]
    if asset_type:
        notes.append(f"资产类型过滤：{asset_type}")

    return ToolResult(
        tool_name="rank_stocks_by_company_concentration",
        intent="stock_company_concentration",
        tables={"stock_company_concentration": df_to_records(df)},
        notes=notes,
        metadata={"date": date, "top_n": top_n, "asset_type": asset_type, "min_companies": min_companies},
    )
