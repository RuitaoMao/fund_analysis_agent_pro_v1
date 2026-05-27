"""基金公司业务结构分析 tools。"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def get_company_total_size(store: SQLiteStore, args: dict) -> ToolResult:
    """查询基金公司总规模。

    口径：同一日期下，匹配基金公司的所有 fund_size 行求和。
    这避免把历史日期重复加总，也避免把“最大单只基金”误当成公司总规模。
    """
    companies = args.get("companies") or []
    if isinstance(companies, str):
        companies = [companies]
    if not companies and args.get("fund_company"):
        companies = [args["fund_company"]]
    date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")

    params = {"date": date}
    company_conditions = []
    for i, company in enumerate(companies):
        key = f"company_{i}"
        company_conditions.append(f"fund_company LIKE :{key}")
        params[key] = f"%{company}%"
    company_where = " OR ".join(company_conditions) if company_conditions else "1=1"

    filters = [f"({company_where})"]
    if asset_type:
        filters.append("asset_type = :asset_type")
        params["asset_type"] = asset_type
    filter_sql = " AND ".join(filters)

    total_df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_company AS 基金公司,
            ROUND(SUM(fund_size), 2) AS 公司总规模,
            COUNT(*) AS 份额数量,
            COUNT(DISTINCT fund_code) AS 基金代码数量
        FROM fund_size
        WHERE date = :date AND {filter_sql}
        GROUP BY date, fund_company
        ORDER BY SUM(fund_size) DESC
        """,
        params,
    )

    asset_df = store.query_df(
        f"""
        WITH base AS (
            SELECT *
            FROM fund_size
            WHERE date = :date AND {filter_sql}
        ),
        total AS (
            SELECT fund_company, SUM(fund_size) AS total_size
            FROM base
            GROUP BY fund_company
        )
        SELECT
            b.date AS 日期,
            b.fund_company AS 基金公司,
            b.asset_type AS 资产类型,
            ROUND(SUM(b.fund_size), 2) AS 基金规模,
            ROUND(SUM(b.fund_size) / t.total_size * 100, 2) AS 规模占比,
            COUNT(*) AS 份额数量
        FROM base b
        JOIN total t ON b.fund_company = t.fund_company
        GROUP BY b.date, b.fund_company, b.asset_type, t.total_size
        ORDER BY b.fund_company, SUM(b.fund_size) DESC
        """,
        params,
    )

    return ToolResult(
        tool_name="get_company_total_size",
        intent="company_total_size",
        tables={
            "company_total_size": df_to_records(total_df),
            "asset_structure": df_to_records(asset_df),
        },
        notes=[
            f"日期：{date}",
            f"基金公司：{', '.join(companies) if companies else '全市场'}",
            f"资产类型：{asset_type or '全类型'}",
            "计算过程：先按日期过滤规模表，再匹配基金公司，最后对该公司所有基金代码/份额行的基金规模求和。",
            "为避免重复计算历史数据，只使用单一日期截面的数据；用户未指定日期时使用规模表最新日期。",
            "用到文件：data/raw/规模.xlsx；入库表：fund_size。",
            "基金规模单位沿用规模.xlsx 的基金规模字段，当前按亿元口径展示。",
            "默认按基金代码/份额维度求和，不合并 A/C/E 等份额为产品主基金。",
        ],
        metadata={
            "date": date,
            "companies": companies,
            "asset_type": asset_type,
            "source_files": ["data/raw/规模.xlsx"],
            "source_tables": ["fund_size"],
            "calculation": "SUM(fund_size) by date and fund_company",
        },
    )


def list_company_funds_by_size(store: SQLiteStore, args: dict) -> ToolResult:
    """查询某基金公司旗下基金规模明细。"""
    fund_company = args.get("fund_company")
    companies = args.get("companies") or ([fund_company] if fund_company else [])
    if isinstance(companies, str):
        companies = [companies]
    date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")
    top_n = int(args.get("top_n") or 20)

    params = {"date": date, "top_n": top_n}
    company_conditions = []
    for i, company in enumerate(companies):
        key = f"company_{i}"
        company_conditions.append(f"fund_company LIKE :{key}")
        params[key] = f"%{company}%"
    company_where = " OR ".join(company_conditions) if company_conditions else "1=1"

    filters = [f"({company_where})"]
    if asset_type:
        filters.append("asset_type = :asset_type")
        params["asset_type"] = asset_type
    filter_sql = " AND ".join(filters)

    funds_df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            fund_company AS 基金公司,
            asset_type AS 资产类型,
            ROUND(fund_size, 2) AS 基金规模
        FROM fund_size
        WHERE date = :date AND {filter_sql}
        ORDER BY fund_size DESC
        LIMIT :top_n
        """,
        params,
    )

    summary_df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_company AS 基金公司,
            ROUND(SUM(fund_size), 2) AS 公司总规模,
            COUNT(*) AS 份额数量
        FROM fund_size
        WHERE date = :date AND {filter_sql}
        GROUP BY date, fund_company
        ORDER BY SUM(fund_size) DESC
        """,
        params,
    )

    return ToolResult(
        tool_name="list_company_funds_by_size",
        intent="company_fund_list",
        tables={
            "company_summary": df_to_records(summary_df),
            "company_funds": df_to_records(funds_df),
        },
        notes=[
            f"日期：{date}",
            f"基金公司：{', '.join(companies) if companies else '全市场'}",
            f"资产类型：{asset_type or '全类型'}",
            f"返回规模最大的前 {top_n} 条基金代码/份额明细。",
            "用到文件：data/raw/规模.xlsx；入库表：fund_size。",
            "为避免历史日期重复计算，只使用单一日期截面；用户未指定日期时使用最新日期。",
            "基金规模单位沿用规模.xlsx 的基金规模字段，当前按亿元口径展示。",
        ],
        metadata={
            "date": date,
            "companies": companies,
            "asset_type": asset_type,
            "top_n": top_n,
            "source_files": ["data/raw/规模.xlsx"],
            "source_tables": ["fund_size"],
        },
    )


def compare_company_business_structure(store: SQLiteStore, args: dict) -> ToolResult:
    """对比基金公司的业务结构。"""
    companies = args.get("companies") or ["易方达", "华夏"]
    date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")

    params = {"date": date}
    company_conditions = []
    for i, company in enumerate(companies):
        key = f"company_{i}"
        company_conditions.append(f"fund_company LIKE :{key}")
        params[key] = f"%{company}%"

    company_where = " OR ".join(company_conditions)
    filters = [f"({company_where})"]
    if asset_type:
        filters.append("asset_type = :asset_type")
        params["asset_type"] = asset_type
    filter_sql = " AND ".join(filters)

    summary_sql = f"""
        SELECT
            date AS 日期,
            fund_company AS 基金公司,
            ROUND(SUM(fund_size), 2) AS 公司总规模,
            COUNT(DISTINCT fund_code) AS 基金数量
        FROM fund_size
        WHERE date = :date AND {filter_sql}
        GROUP BY date, fund_company
        ORDER BY SUM(fund_size) DESC
    """

    structure_sql = f"""
        WITH base AS (
            SELECT *
            FROM fund_size
            WHERE date = :date AND {filter_sql}
        ),
        total AS (
            SELECT fund_company, SUM(fund_size) AS total_size
            FROM base
            GROUP BY fund_company
        )
        SELECT
            b.date AS 日期,
            b.fund_company AS 基金公司,
            b.asset_type AS 资产类型,
            ROUND(SUM(b.fund_size), 2) AS 基金规模,
            ROUND(SUM(b.fund_size) / t.total_size * 100, 2) AS 规模占比,
            COUNT(DISTINCT b.fund_code) AS 基金数量
        FROM base b
        JOIN total t ON b.fund_company = t.fund_company
        GROUP BY b.date, b.fund_company, b.asset_type, t.total_size
        ORDER BY b.fund_company, SUM(b.fund_size) DESC
    """

    summary_df = store.query_df(summary_sql, params)
    structure_df = store.query_df(structure_sql, params)

    return ToolResult(
        tool_name="compare_company_business_structure",
        intent="company_structure_comparison",
        tables={
            "company_summary": df_to_records(summary_df),
            "asset_structure": df_to_records(structure_df),
        },
        notes=[
            f"日期：{date}",
            f"资产类型过滤：{asset_type or '全类型'}",
            "业务结构基于规模表中的资产类型字段汇总。",
            "该结果不包含费率、渠道、客户结构、收益表现等维度，因此不代表完整经营竞争力判断。",
        ],
        metadata={"date": date, "companies": companies, "asset_type": asset_type},
    )
