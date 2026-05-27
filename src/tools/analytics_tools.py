"""更细粒度的分析 tools。

这些工具补足趋势、单基金画像、持仓集中度、股票反查等复杂问题。
所有计算仍然由固定 SQL 完成，LLM 只负责选择工具和写报告。
"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def get_company_size_trend(store: SQLiteStore, args: dict) -> ToolResult:
    """查询基金公司规模随时间变化。"""
    companies = args.get("companies") or []
    if isinstance(companies, str):
        companies = [companies]
    asset_type = args.get("asset_type")

    params: dict = {}
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

    trend_df = store.query_df(
        f"""
        WITH company_date AS (
            SELECT
                date,
                fund_company,
                SUM(fund_size) AS total_size,
                COUNT(*) AS share_count
            FROM fund_size
            WHERE {filter_sql}
            GROUP BY date, fund_company
        )
        SELECT
            date AS 日期,
            fund_company AS 基金公司,
            ROUND(total_size, 2) AS 公司总规模,
            share_count AS 份额数量,
            ROUND(total_size - LAG(total_size) OVER (PARTITION BY fund_company ORDER BY date), 2) AS 较上期变化,
            ROUND(
                CASE
                    WHEN LAG(total_size) OVER (PARTITION BY fund_company ORDER BY date) IS NULL THEN NULL
                    WHEN LAG(total_size) OVER (PARTITION BY fund_company ORDER BY date) = 0 THEN NULL
                    ELSE (total_size / LAG(total_size) OVER (PARTITION BY fund_company ORDER BY date) - 1) * 100
                END,
                2
            ) AS 较上期变化率
        FROM company_date
        ORDER BY fund_company, date
        """,
        params,
    )

    return ToolResult(
        tool_name="get_company_size_trend",
        intent="company_size_trend",
        tables={"company_size_trend": df_to_records(trend_df)},
        notes=[
            f"基金公司：{', '.join(companies) if companies else '全市场'}",
            f"资产类型：{asset_type or '全类型'}",
            "计算过程：按每个日期截面汇总该公司所有基金代码/份额的基金规模，并计算较上期变化。",
            "用到文件：data/raw/规模.xlsx；入库表：fund_size。",
            "基金规模单位沿用规模.xlsx 的基金规模字段，当前按亿元口径展示。",
        ],
        metadata={
            "companies": companies,
            "asset_type": asset_type,
            "source_files": ["data/raw/规模.xlsx"],
            "source_tables": ["fund_size"],
        },
    )


def get_fund_size_history(store: SQLiteStore, args: dict) -> ToolResult:
    """查询单只基金/份额的规模历史。"""
    fund_code = args.get("fund_code")
    keyword = str(args.get("keyword") or "").strip()
    top_n = int(args.get("top_n") or 20)

    if fund_code:
        fund_code = str(fund_code).split(".")[0].zfill(6)
        where = "fund_code = :fund_code"
        params = {"fund_code": fund_code, "top_n": top_n}
        label = fund_code
    else:
        where = "(fund_code LIKE :kw OR fund_name LIKE :kw)"
        params = {"kw": f"%{keyword}%", "top_n": top_n}
        label = keyword

    df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            fund_company AS 基金公司,
            asset_type AS 资产类型,
            ROUND(fund_size, 2) AS 基金规模,
            ROUND(fund_size - LAG(fund_size) OVER (PARTITION BY fund_code ORDER BY date), 2) AS 较上期变化,
            ROUND(
                CASE
                    WHEN LAG(fund_size) OVER (PARTITION BY fund_code ORDER BY date) IS NULL THEN NULL
                    WHEN LAG(fund_size) OVER (PARTITION BY fund_code ORDER BY date) = 0 THEN NULL
                    ELSE (fund_size / LAG(fund_size) OVER (PARTITION BY fund_code ORDER BY date) - 1) * 100
                END,
                2
            ) AS 较上期变化率
        FROM fund_size
        WHERE {where}
        ORDER BY fund_code, date
        LIMIT :top_n
        """,
        params,
    )

    return ToolResult(
        tool_name="get_fund_size_history",
        intent="fund_size_history",
        tables={"fund_size_history": df_to_records(df)},
        notes=[
            f"检索对象：{label}",
            "计算过程：按基金代码/份额查看不同日期截面的基金规模，并计算较上期变化。",
            "用到文件：data/raw/规模.xlsx；入库表：fund_size。",
            "基金规模单位沿用规模.xlsx 的基金规模字段，当前按亿元口径展示。",
        ],
        metadata={"fund_code": fund_code, "keyword": keyword, "top_n": top_n},
    )


def get_fund_holdings_detail(store: SQLiteStore, args: dict) -> ToolResult:
    """查询指定基金的持仓明细。"""
    fund_codes = args.get("fund_codes") or []
    if isinstance(fund_codes, str):
        fund_codes = [fund_codes]
    fund_codes = [str(code).split(".")[0].zfill(6) for code in fund_codes]
    date = args.get("date") or store.max_date("fund_holding")
    top_n = int(args.get("top_n") or 10)

    params = {"date": date, "top_n": top_n}
    placeholders = []
    for i, code in enumerate(fund_codes):
        key = f"fund_code_{i}"
        placeholders.append(f":{key}")
        params[key] = code
    in_clause = ", ".join(placeholders) if placeholders else "NULL"

    df = store.query_df(
        f"""
        SELECT
            h.date AS 日期,
            h.fund_code AS 基金代码,
            COALESCE(s.fund_name, p.fund_name) AS 基金名称,
            h.stock_code AS 股票代码,
            h.stock_name AS 股票名称,
            ROUND(h.holding_quantity, 2) AS 持仓数量,
            ROUND(h.holding_value, 2) AS 持仓规模,
            ROUND(h.nav_ratio, 2) AS 占基金净值比例
        FROM fund_holding h
        LEFT JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        LEFT JOIN fund_performance p ON h.fund_code = p.fund_code
        WHERE h.date = :date
          AND h.fund_code IN ({in_clause})
        GROUP BY h.date, h.fund_code, h.stock_code, h.stock_name
        ORDER BY h.fund_code, h.holding_value DESC
        LIMIT :top_n
        """,
        params,
    )

    return ToolResult(
        tool_name="get_fund_holdings_detail",
        intent="fund_holding_detail",
        tables={"fund_holdings_detail": df_to_records(df)},
        notes=[
            f"日期：{date}",
            f"基金范围：{', '.join(fund_codes) if fund_codes else '未指定'}",
            f"返回每只基金持仓规模最大的前 {top_n} 条明细。",
            "用到文件：data/raw/持仓.xlsx；入库表：fund_holding。",
            "持仓规模沿用持仓.xlsx 的持仓规模字段，代码不做二次汇率换算。",
        ],
        metadata={"date": date, "fund_codes": fund_codes, "top_n": top_n},
    )


def analyze_fund_holding_concentration(store: SQLiteStore, args: dict) -> ToolResult:
    """分析指定基金的前 N 大持仓集中度。"""
    fund_codes = args.get("fund_codes") or []
    if isinstance(fund_codes, str):
        fund_codes = [fund_codes]
    fund_codes = [str(code).split(".")[0].zfill(6) for code in fund_codes]
    date = args.get("date") or store.max_date("fund_holding")
    top_n = int(args.get("top_n") or 10)

    params = {"date": date, "top_n": top_n}
    placeholders = []
    for i, code in enumerate(fund_codes):
        key = f"fund_code_{i}"
        placeholders.append(f":{key}")
        params[key] = code
    in_clause = ", ".join(placeholders) if placeholders else "NULL"

    df = store.query_df(
        f"""
        WITH ranked AS (
            SELECT
                fund_code,
                stock_code,
                stock_name,
                holding_value,
                nav_ratio,
                ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY holding_value DESC) AS rn
            FROM fund_holding
            WHERE date = :date
              AND fund_code IN ({in_clause})
        ),
        total AS (
            SELECT fund_code, SUM(holding_value) AS total_holding_value, COUNT(*) AS stock_count
            FROM fund_holding
            WHERE date = :date
              AND fund_code IN ({in_clause})
            GROUP BY fund_code
        )
        SELECT
            r.fund_code AS 基金代码,
            ROUND(t.total_holding_value, 2) AS 持仓总规模,
            t.stock_count AS 持仓股票数量,
            ROUND(SUM(CASE WHEN r.rn <= :top_n THEN r.holding_value ELSE 0 END), 2) AS 前N大持仓规模,
            ROUND(SUM(CASE WHEN r.rn <= :top_n THEN r.holding_value ELSE 0 END) / t.total_holding_value * 100, 2) AS 前N大持仓占比,
            ROUND(SUM(CASE WHEN r.rn <= :top_n THEN r.nav_ratio ELSE 0 END), 2) AS 前N大净值占比合计
        FROM ranked r
        JOIN total t ON r.fund_code = t.fund_code
        GROUP BY r.fund_code, t.total_holding_value, t.stock_count
        ORDER BY 前N大持仓占比 DESC
        """,
        params,
    )

    return ToolResult(
        tool_name="analyze_fund_holding_concentration",
        intent="fund_holding_concentration",
        tables={"fund_holding_concentration": df_to_records(df)},
        notes=[
            f"日期：{date}",
            f"基金范围：{', '.join(fund_codes)}",
            f"集中度口径：前 {top_n} 大持仓。",
            "用到文件：data/raw/持仓.xlsx；入库表：fund_holding。",
            "前N大持仓占比按持仓规模字段计算；前N大净值占比合计按占基金净值比例字段求和。",
        ],
        metadata={"date": date, "fund_codes": fund_codes, "top_n": top_n},
    )


def find_funds_holding_stock(store: SQLiteStore, args: dict) -> ToolResult:
    """反查持有某只股票的基金。"""
    stock_keyword = str(args.get("stock_keyword") or args.get("stock_code") or "").strip()
    date = args.get("date") or store.max_date("fund_holding")
    top_n = int(args.get("top_n") or 20)

    df = store.query_df(
        """
        SELECT
            h.date AS 日期,
            h.stock_code AS 股票代码,
            h.stock_name AS 股票名称,
            h.fund_code AS 基金代码,
            s.fund_name AS 基金名称,
            s.fund_company AS 基金公司,
            s.asset_type AS 资产类型,
            ROUND(h.holding_value, 2) AS 持仓规模,
            ROUND(h.nav_ratio, 2) AS 占基金净值比例
        FROM fund_holding h
        LEFT JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE h.date = :date
          AND (h.stock_code LIKE :kw OR h.stock_name LIKE :kw)
        ORDER BY h.holding_value DESC
        LIMIT :top_n
        """,
        {"date": date, "kw": f"%{stock_keyword}%", "top_n": top_n},
    )

    return ToolResult(
        tool_name="find_funds_holding_stock",
        intent="stock_holder_funds",
        tables={"stock_holder_funds": df_to_records(df)},
        notes=[
            f"日期：{date}",
            f"股票检索关键词：{stock_keyword}",
            f"返回持仓规模最大的前 {top_n} 只基金。",
            "用到文件：data/raw/持仓.xlsx 和 data/raw/规模.xlsx；入库表：fund_holding、fund_size。",
        ],
        metadata={
            "date": date,
            "stock_keyword": stock_keyword,
            "top_n": top_n,
            "source_files": ["data/raw/持仓.xlsx", "data/raw/规模.xlsx"],
            "source_tables": ["fund_holding", "fund_size"],
        },
    )
