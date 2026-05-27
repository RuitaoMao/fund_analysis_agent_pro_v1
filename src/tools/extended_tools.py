"""扩展分析 tools。

这些工具都只基于三张入库表做固定 SQL 查询：
- fund_size：来自规模.xlsx，含 asset_type 与 wind_level1/2/3
- fund_holding：来自持仓.xlsx
- fund_performance：来自业绩.xlsx
"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def _like_companies(companies: list[str] | str | None, params: dict) -> str:
    if isinstance(companies, str):
        companies = [companies]
    companies = companies or []
    clauses = []
    for i, company in enumerate(companies):
        key = f"company_{i}"
        clauses.append(f"fund_company LIKE :{key}")
        params[key] = f"%{company}%"
    return "(" + " OR ".join(clauses) + ")" if clauses else "1=1"


def _fund_code_filter(fund_codes: list[str] | str | None, params: dict, column: str = "fund_code") -> str:
    if isinstance(fund_codes, str):
        fund_codes = [fund_codes]
    fund_codes = [str(code).split(".")[0].zfill(6) for code in (fund_codes or [])]
    if not fund_codes:
        return "1=1"
    placeholders = []
    for i, code in enumerate(fund_codes):
        key = f"fund_code_{i}"
        placeholders.append(f":{key}")
        params[key] = code
    return f"{column} IN ({', '.join(placeholders)})"


def _wind_column(level: int | str | None) -> str:
    level_text = str(level or 1)
    if "3" in level_text or "三级" in level_text:
        return "wind_level3"
    if "2" in level_text or "二级" in level_text:
        return "wind_level2"
    return "wind_level1"


def get_asset_type_size_distribution(store: SQLiteStore, args: dict) -> ToolResult:
    date = args.get("date") or store.max_date("fund_size")
    fund_company = args.get("fund_company")
    params = {"date": date}
    filters = ["date = :date"]
    if fund_company:
        filters.append("fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"
    df = store.query_df(
        f"""
        WITH base AS (
            SELECT * FROM fund_size WHERE {' AND '.join(filters)}
        ), total AS (
            SELECT SUM(fund_size) AS total_size FROM base
        )
        SELECT
            date AS 日期,
            asset_type AS 资产类型,
            ROUND(SUM(fund_size), 2) AS 基金规模,
            COUNT(*) AS 份额数量,
            ROUND(SUM(fund_size) / (SELECT total_size FROM total) * 100, 2) AS 规模占比
        FROM base
        GROUP BY date, asset_type
        ORDER BY SUM(fund_size) DESC
        """,
        params,
    )
    return ToolResult(
        tool_name="get_asset_type_size_distribution",
        intent="asset_type_distribution",
        tables={"asset_type_size_distribution": df_to_records(df)},
        notes=[f"日期：{date}", f"基金公司：{fund_company or '全市场'}", "按规模.xlsx 的资产类型字段汇总。"],
        metadata={"date": date, "fund_company": fund_company},
    )


def get_wind_category_size_distribution(store: SQLiteStore, args: dict) -> ToolResult:
    date = args.get("date") or store.max_date("fund_size")
    wind_level = args.get("wind_level") or args.get("level") or 1
    fund_company = args.get("fund_company")
    column = _wind_column(wind_level)
    params = {"date": date}
    filters = ["date = :date", f"{column} IS NOT NULL"]
    if fund_company:
        filters.append("fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"
    df = store.query_df(
        f"""
        WITH base AS (
            SELECT * FROM fund_size WHERE {' AND '.join(filters)}
        ), total AS (
            SELECT SUM(fund_size) AS total_size FROM base
        )
        SELECT
            date AS 日期,
            {column} AS Wind分类,
            ROUND(SUM(fund_size), 2) AS 基金规模,
            COUNT(*) AS 份额数量,
            ROUND(SUM(fund_size) / (SELECT total_size FROM total) * 100, 2) AS 规模占比
        FROM base
        GROUP BY date, {column}
        ORDER BY SUM(fund_size) DESC
        """,
        params,
    )
    return ToolResult(
        tool_name="get_wind_category_size_distribution",
        intent="wind_category_distribution",
        tables={"wind_category_size_distribution": df_to_records(df)},
        notes=[f"日期：{date}", f"Wind 分类层级：{column}", f"基金公司：{fund_company or '全市场'}"],
        metadata={"date": date, "wind_level": column, "fund_company": fund_company},
    )


def get_top_funds_by_wind_category(store: SQLiteStore, args: dict) -> ToolResult:
    date = args.get("date") or store.max_date("fund_size")
    column = _wind_column(args.get("wind_level") or args.get("level") or 1)
    category = args.get("wind_category") or args.get("category")
    fund_company = args.get("fund_company")
    top_n = int(args.get("top_n") or 10)
    params = {"date": date, "top_n": top_n}
    filters = ["date = :date"]
    if category:
        filters.append(f"{column} LIKE :category")
        params["category"] = f"%{category}%"
    if fund_company:
        filters.append("fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"
    df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            fund_company AS 基金公司,
            {column} AS Wind分类,
            asset_type AS 资产类型,
            ROUND(fund_size, 2) AS 基金规模
        FROM fund_size
        WHERE {' AND '.join(filters)}
        ORDER BY fund_size DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(
        tool_name="get_top_funds_by_wind_category",
        intent="wind_category_fund_ranking",
        tables={"wind_category_fund_ranking": df_to_records(df)},
        notes=[f"日期：{date}", f"Wind 分类层级：{column}", f"分类关键词：{category or '全部'}"],
        metadata={"date": date, "wind_level": column, "wind_category": category, "top_n": top_n},
    )


def rank_companies_by_asset_type_size(store: SQLiteStore, args: dict) -> ToolResult:
    date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")
    top_n = int(args.get("top_n") or 20)
    params = {"date": date, "top_n": top_n}
    filters = ["date = :date"]
    if asset_type:
        filters.append("asset_type = :asset_type")
        params["asset_type"] = asset_type
    df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_company AS 基金公司,
            asset_type AS 资产类型,
            ROUND(SUM(fund_size), 2) AS 基金规模,
            COUNT(*) AS 份额数量
        FROM fund_size
        WHERE {' AND '.join(filters)}
        GROUP BY date, fund_company, asset_type
        ORDER BY SUM(fund_size) DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(
        tool_name="rank_companies_by_asset_type_size",
        intent="company_asset_type_ranking",
        tables={"company_asset_type_ranking": df_to_records(df)},
        notes=[f"日期：{date}", f"资产类型：{asset_type or '全部'}"],
        metadata={"date": date, "asset_type": asset_type, "top_n": top_n},
    )


def rank_companies_by_wind_category_size(store: SQLiteStore, args: dict) -> ToolResult:
    date = args.get("date") or store.max_date("fund_size")
    column = _wind_column(args.get("wind_level") or args.get("level") or 1)
    category = args.get("wind_category") or args.get("category")
    top_n = int(args.get("top_n") or 20)
    params = {"date": date, "top_n": top_n}
    filters = ["date = :date"]
    if category:
        filters.append(f"{column} LIKE :category")
        params["category"] = f"%{category}%"
    df = store.query_df(
        f"""
        SELECT
            date AS 日期,
            fund_company AS 基金公司,
            {column} AS Wind分类,
            ROUND(SUM(fund_size), 2) AS 基金规模,
            COUNT(*) AS 份额数量
        FROM fund_size
        WHERE {' AND '.join(filters)}
        GROUP BY date, fund_company, {column}
        ORDER BY SUM(fund_size) DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(
        tool_name="rank_companies_by_wind_category_size",
        intent="company_wind_category_ranking",
        tables={"company_wind_category_ranking": df_to_records(df)},
        notes=[f"日期：{date}", f"Wind 分类层级：{column}", f"分类关键词：{category or '全部'}"],
        metadata={"date": date, "wind_level": column, "wind_category": category, "top_n": top_n},
    )


def get_size_growth_ranking(store: SQLiteStore, args: dict) -> ToolResult:
    entity = args.get("entity") or "fund"
    asset_type = args.get("asset_type")
    top_n = int(args.get("top_n") or 20)
    params = {"top_n": top_n}
    filter_sql = ""
    if asset_type:
        filter_sql = "WHERE asset_type = :asset_type"
        params["asset_type"] = asset_type
    if entity == "company":
        df = store.query_df(
            f"""
            WITH dates AS (
                SELECT date, ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
                FROM (SELECT DISTINCT date FROM fund_size)
            ), base AS (
                SELECT s.date, s.fund_company, SUM(s.fund_size) AS size
                FROM fund_size s JOIN dates d ON s.date = d.date
                {filter_sql}
                GROUP BY s.date, s.fund_company
            ), paired AS (
                SELECT n.fund_company, n.size AS latest_size, p.size AS previous_size
                FROM base n
                JOIN base p ON n.fund_company = p.fund_company
                JOIN dates dn ON n.date = dn.date AND dn.rn = 1
                JOIN dates dp ON p.date = dp.date AND dp.rn = 2
            )
            SELECT
                fund_company AS 基金公司,
                ROUND(latest_size, 2) AS 最新规模,
                ROUND(previous_size, 2) AS 上期规模,
                ROUND(latest_size - previous_size, 2) AS 规模变化,
                ROUND((latest_size / previous_size - 1) * 100, 2) AS 变化率
            FROM paired
            ORDER BY 规模变化 DESC
            LIMIT :top_n
            """,
            params,
        )
    else:
        df = store.query_df(
            f"""
            WITH dates AS (
                SELECT date, ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
                FROM (SELECT DISTINCT date FROM fund_size)
            ), base AS (
                SELECT s.*
                FROM fund_size s JOIN dates d ON s.date = d.date
                {filter_sql}
            ), paired AS (
                SELECT n.fund_code, n.fund_name, n.fund_company, n.asset_type,
                       n.fund_size AS latest_size, p.fund_size AS previous_size
                FROM base n
                JOIN base p ON n.fund_code = p.fund_code
                JOIN dates dn ON n.date = dn.date AND dn.rn = 1
                JOIN dates dp ON p.date = dp.date AND dp.rn = 2
            )
            SELECT
                fund_code AS 基金代码,
                fund_name AS 基金名称,
                fund_company AS 基金公司,
                asset_type AS 资产类型,
                ROUND(latest_size, 2) AS 最新规模,
                ROUND(previous_size, 2) AS 上期规模,
                ROUND(latest_size - previous_size, 2) AS 规模变化,
                ROUND((latest_size / previous_size - 1) * 100, 2) AS 变化率
            FROM paired
            ORDER BY 规模变化 DESC
            LIMIT :top_n
            """,
            params,
        )
    return ToolResult(
        tool_name="get_size_growth_ranking",
        intent="size_growth_ranking",
        tables={"size_growth_ranking": df_to_records(df)},
        notes=[f"统计对象：{entity}", f"资产类型：{asset_type or '全类型'}", "比较最新两个规模日期截面。"],
        metadata={"entity": entity, "asset_type": asset_type, "top_n": top_n},
    )


def compare_fund_size_across_dates(store: SQLiteStore, args: dict) -> ToolResult:
    fund_codes = args.get("fund_codes") or []
    date_start = args.get("date_start")
    date_end = args.get("date_end") or store.max_date("fund_size")
    if not date_start:
        dates = [row[0] for row in store.query_df("SELECT DISTINCT date FROM fund_size ORDER BY date DESC LIMIT 2").itertuples(index=False)]
        date_start = dates[-1] if len(dates) > 1 else date_end
    params = {"date_start": date_start, "date_end": date_end}
    code_filter = _fund_code_filter(fund_codes, params)
    df = store.query_df(
        f"""
        WITH base AS (
            SELECT * FROM fund_size
            WHERE date IN (:date_start, :date_end) AND {code_filter}
        )
        SELECT
            e.fund_code AS 基金代码,
            e.fund_name AS 基金名称,
            e.fund_company AS 基金公司,
            e.asset_type AS 资产类型,
            ROUND(s.fund_size, 2) AS 起始规模,
            ROUND(e.fund_size, 2) AS 结束规模,
            ROUND(e.fund_size - s.fund_size, 2) AS 规模变化,
            ROUND((e.fund_size / s.fund_size - 1) * 100, 2) AS 变化率
        FROM base e
        JOIN base s ON e.fund_code = s.fund_code
        WHERE e.date = :date_end AND s.date = :date_start
        ORDER BY 规模变化 DESC
        """,
        params,
    )
    return ToolResult(
        tool_name="compare_fund_size_across_dates",
        intent="fund_size_date_comparison",
        tables={"fund_size_date_comparison": df_to_records(df)},
        notes=[f"起始日期：{date_start}", f"结束日期：{date_end}", "按基金代码/份额比较规模变化。"],
        metadata={"fund_codes": fund_codes, "date_start": date_start, "date_end": date_end},
    )


def get_bottom_funds_by_performance(store: SQLiteStore, args: dict) -> ToolResult:
    period = args.get("period") or "本年以来"
    top_n = int(args.get("top_n") or 10)
    df = store.query_df(
        """
        SELECT fund_code AS 基金代码, fund_name AS 基金名称, period AS 区间,
               ROUND(portfolio_return * 100, 2) AS 组合收益率,
               ROUND(benchmark_return * 100, 2) AS 基准收益率,
               ROUND(excess_return * 100, 2) AS 超额收益,
               ROUND(max_drawdown * 100, 2) AS 最大回撤
        FROM fund_performance
        WHERE period = :period
        ORDER BY portfolio_return ASC
        LIMIT :top_n
        """,
        {"period": period, "top_n": top_n},
    )
    return ToolResult(tool_name="get_bottom_funds_by_performance", intent="performance_bottom_ranking", tables={"performance_bottom_ranking": df_to_records(df)}, notes=[f"业绩区间：{period}", "收益率字段已转为百分数显示。"], metadata={"period": period, "top_n": top_n})


def get_fund_performance_detail(store: SQLiteStore, args: dict) -> ToolResult:
    fund_codes = args.get("fund_codes") or []
    keyword = args.get("keyword")
    params: dict = {}
    filters = []
    if fund_codes:
        filters.append(_fund_code_filter(fund_codes, params))
    if keyword:
        filters.append("(fund_code LIKE :kw OR fund_name LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    where = " AND ".join(filters) if filters else "1=1"
    df = store.query_df(
        f"""
        SELECT fund_code AS 基金代码, fund_name AS 基金名称, period AS 区间,
               ROUND(portfolio_return * 100, 2) AS 组合收益率,
               ROUND(benchmark_return * 100, 2) AS 基准收益率,
               ROUND(excess_return * 100, 2) AS 超额收益,
               ROUND(max_drawdown * 100, 2) AS 最大回撤
        FROM fund_performance
        WHERE {where}
        ORDER BY fund_code, period
        LIMIT 100
        """,
        params,
    )
    return ToolResult(tool_name="get_fund_performance_detail", intent="fund_performance_detail", tables={"fund_performance_detail": df_to_records(df)}, notes=["查询基金在业绩.xlsx 中所有可用区间表现。"], metadata={"fund_codes": fund_codes, "keyword": keyword})


def compare_fund_performance(store: SQLiteStore, args: dict) -> ToolResult:
    fund_codes = args.get("fund_codes") or []
    period = args.get("period") or "本年以来"
    params = {"period": period}
    code_filter = _fund_code_filter(fund_codes, params)
    df = store.query_df(
        f"""
        SELECT fund_code AS 基金代码, fund_name AS 基金名称, period AS 区间,
               ROUND(portfolio_return * 100, 2) AS 组合收益率,
               ROUND(benchmark_return * 100, 2) AS 基准收益率,
               ROUND(excess_return * 100, 2) AS 超额收益,
               ROUND(max_drawdown * 100, 2) AS 最大回撤
        FROM fund_performance
        WHERE period = :period AND {code_filter}
        ORDER BY portfolio_return DESC
        """,
        params,
    )
    return ToolResult(tool_name="compare_fund_performance", intent="fund_performance_comparison", tables={"fund_performance_comparison": df_to_records(df)}, notes=[f"业绩区间：{period}"], metadata={"fund_codes": fund_codes, "period": period})


def rank_companies_by_average_return(store: SQLiteStore, args: dict) -> ToolResult:
    period = args.get("period") or "本年以来"
    asset_type = args.get("asset_type")
    top_n = int(args.get("top_n") or 20)
    params = {"period": period, "top_n": top_n}
    filters = ["p.period = :period"]
    if asset_type:
        filters.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type
    df = store.query_df(
        f"""
        SELECT
            s.fund_company AS 基金公司,
            s.asset_type AS 资产类型,
            COUNT(DISTINCT p.fund_code) AS 基金数量,
            ROUND(AVG(p.portfolio_return) * 100, 2) AS 平均组合收益率,
            ROUND(AVG(p.excess_return) * 100, 2) AS 平均超额收益,
            ROUND(AVG(p.max_drawdown) * 100, 2) AS 平均最大回撤
        FROM fund_performance p
        JOIN fund_size s ON p.fund_code = s.fund_code
        WHERE s.date = (SELECT MAX(date) FROM fund_size) AND {' AND '.join(filters)}
        GROUP BY s.fund_company, s.asset_type
        ORDER BY AVG(p.portfolio_return) DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(tool_name="rank_companies_by_average_return", intent="company_average_return_ranking", tables={"company_average_return_ranking": df_to_records(df)}, notes=[f"业绩区间：{period}", f"资产类型：{asset_type or '全类型'}"], metadata={"period": period, "asset_type": asset_type, "top_n": top_n})


def analyze_performance_distribution(store: SQLiteStore, args: dict) -> ToolResult:
    period = args.get("period") or "本年以来"
    asset_type = args.get("asset_type")
    params = {"period": period}
    join_sql = ""
    filters = ["p.period = :period"]
    if asset_type:
        join_sql = "JOIN fund_size s ON p.fund_code = s.fund_code AND s.date = (SELECT MAX(date) FROM fund_size)"
        filters.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type
    df = store.query_df(
        f"""
        SELECT
            p.period AS 区间,
            COUNT(*) AS 基金数量,
            ROUND(AVG(p.portfolio_return) * 100, 2) AS 平均收益率,
            ROUND(MIN(p.portfolio_return) * 100, 2) AS 最低收益率,
            ROUND(MAX(p.portfolio_return) * 100, 2) AS 最高收益率,
            ROUND(AVG(p.excess_return) * 100, 2) AS 平均超额收益,
            ROUND(AVG(p.max_drawdown) * 100, 2) AS 平均最大回撤
        FROM fund_performance p
        {join_sql}
        WHERE {' AND '.join(filters)}
        GROUP BY p.period
        """,
        params,
    )
    return ToolResult(tool_name="analyze_performance_distribution", intent="performance_distribution", tables={"performance_distribution": df_to_records(df)}, notes=[f"业绩区间：{period}", f"资产类型：{asset_type or '全市场'}"], metadata={"period": period, "asset_type": asset_type})


def get_company_top_holdings(store: SQLiteStore, args: dict) -> ToolResult:
    companies = args.get("companies") or []
    date = args.get("date") or store.max_date("fund_holding")
    asset_type = args.get("asset_type")
    top_n = int(args.get("top_n") or 20)
    params = {"date": date, "top_n": top_n}
    filters = ["h.date = :date", _like_companies(companies, params)]
    if asset_type:
        filters.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type
    df = store.query_df(
        f"""
        SELECT
            h.date AS 日期,
            s.fund_company AS 基金公司,
            h.stock_code AS 股票代码,
            h.stock_name AS 股票名称,
            ROUND(SUM(h.holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT h.fund_code) AS 涉及基金数量
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE {' AND '.join(filters)}
        GROUP BY h.date, s.fund_company, h.stock_code, h.stock_name
        ORDER BY SUM(h.holding_value) DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(tool_name="get_company_top_holdings", intent="company_top_holdings", tables={"company_top_holdings": df_to_records(df)}, notes=[f"日期：{date}", f"公司：{companies}", f"资产类型：{asset_type or '全类型'}"], metadata={"date": date, "companies": companies, "asset_type": asset_type, "top_n": top_n})


def compare_holdings_between_companies(store: SQLiteStore, args: dict) -> ToolResult:
    return get_company_top_holdings(store, {**args, "top_n": args.get("top_n") or 50}).model_copy(update={"tool_name": "compare_holdings_between_companies", "intent": "company_holding_comparison"})


def get_common_holdings_between_funds(store: SQLiteStore, args: dict) -> ToolResult:
    fund_codes = args.get("fund_codes") or []
    date = args.get("date") or store.max_date("fund_holding")
    top_n = int(args.get("top_n") or 20)
    params = {"date": date, "top_n": top_n}
    code_filter = _fund_code_filter(fund_codes, params, "h.fund_code")
    df = store.query_df(
        f"""
        SELECT
            h.date AS 日期,
            h.stock_code AS 股票代码,
            h.stock_name AS 股票名称,
            COUNT(DISTINCT h.fund_code) AS 持有基金数量,
            ROUND(SUM(h.holding_value), 2) AS 合计持仓规模,
            ROUND(AVG(h.nav_ratio), 2) AS 平均净值占比
        FROM fund_holding h
        WHERE h.date = :date AND {code_filter}
        GROUP BY h.date, h.stock_code, h.stock_name
        HAVING COUNT(DISTINCT h.fund_code) >= 2
        ORDER BY COUNT(DISTINCT h.fund_code) DESC, SUM(h.holding_value) DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(tool_name="get_common_holdings_between_funds", intent="common_holdings", tables={"common_holdings": df_to_records(df)}, notes=[f"日期：{date}", "统计至少被两只输入基金共同持有的股票。"], metadata={"date": date, "fund_codes": fund_codes, "top_n": top_n})


def get_stock_holding_trend(store: SQLiteStore, args: dict) -> ToolResult:
    stock_keyword = args.get("stock_keyword") or args.get("stock_code")
    df = store.query_df(
        """
        SELECT
            date AS 日期,
            stock_code AS 股票代码,
            stock_name AS 股票名称,
            ROUND(SUM(holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT fund_code) AS 涉及基金数量
        FROM fund_holding
        WHERE stock_code LIKE :kw OR stock_name LIKE :kw
        GROUP BY date, stock_code, stock_name
        ORDER BY date
        """,
        {"kw": f"%{stock_keyword}%"},
    )
    return ToolResult(tool_name="get_stock_holding_trend", intent="stock_holding_trend", tables={"stock_holding_trend": df_to_records(df)}, notes=[f"股票关键词：{stock_keyword}", "按持仓日期聚合全市场基金持仓。"], metadata={"stock_keyword": stock_keyword})


def get_stock_holding_by_asset_type(store: SQLiteStore, args: dict) -> ToolResult:
    stock_keyword = args.get("stock_keyword") or args.get("stock_code")
    date = args.get("date") or store.max_date("fund_holding")
    df = store.query_df(
        """
        SELECT
            h.date AS 日期,
            h.stock_code AS 股票代码,
            h.stock_name AS 股票名称,
            s.asset_type AS 资产类型,
            ROUND(SUM(h.holding_value), 2) AS 持仓规模,
            COUNT(DISTINCT h.fund_code) AS 涉及基金数量
        FROM fund_holding h
        JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
        WHERE h.date = :date AND (h.stock_code LIKE :kw OR h.stock_name LIKE :kw)
        GROUP BY h.date, h.stock_code, h.stock_name, s.asset_type
        ORDER BY SUM(h.holding_value) DESC
        """,
        {"date": date, "kw": f"%{stock_keyword}%"},
    )
    return ToolResult(tool_name="get_stock_holding_by_asset_type", intent="stock_holding_by_asset_type", tables={"stock_holding_by_asset_type": df_to_records(df)}, notes=[f"日期：{date}", f"股票关键词：{stock_keyword}"], metadata={"date": date, "stock_keyword": stock_keyword})


def get_fund_holding_change(store: SQLiteStore, args: dict) -> ToolResult:
    fund_codes = args.get("fund_codes") or []
    date_end = args.get("date_end") or store.max_date("fund_holding")
    if not args.get("date_start"):
        dates = [row[0] for row in store.query_df("SELECT DISTINCT date FROM fund_holding ORDER BY date DESC LIMIT 2").itertuples(index=False)]
        date_start = dates[-1] if len(dates) > 1 else date_end
    else:
        date_start = args["date_start"]
    top_n = int(args.get("top_n") or 20)
    params = {"date_start": date_start, "date_end": date_end, "top_n": top_n}
    code_filter = _fund_code_filter(fund_codes, params)
    df = store.query_df(
        f"""
        WITH base AS (
            SELECT date, fund_code, stock_code, stock_name, SUM(holding_value) AS value
            FROM fund_holding
            WHERE date IN (:date_start, :date_end) AND {code_filter}
            GROUP BY date, fund_code, stock_code, stock_name
        )
        SELECT
            e.fund_code AS 基金代码,
            e.stock_code AS 股票代码,
            e.stock_name AS 股票名称,
            ROUND(COALESCE(s.value, 0), 2) AS 起始持仓规模,
            ROUND(e.value, 2) AS 结束持仓规模,
            ROUND(e.value - COALESCE(s.value, 0), 2) AS 持仓变化
        FROM base e
        LEFT JOIN base s ON e.fund_code = s.fund_code AND e.stock_code = s.stock_code AND s.date = :date_start
        WHERE e.date = :date_end
        ORDER BY ABS(e.value - COALESCE(s.value, 0)) DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(tool_name="get_fund_holding_change", intent="fund_holding_change", tables={"fund_holding_change": df_to_records(df)}, notes=[f"起始日期：{date_start}", f"结束日期：{date_end}"], metadata={"fund_codes": fund_codes, "date_start": date_start, "date_end": date_end, "top_n": top_n})


def get_company_product_count_by_asset_type(store: SQLiteStore, args: dict) -> ToolResult:
    companies = args.get("companies") or []
    if isinstance(companies, str):
        companies = [companies]
    fund_company = companies[0] if companies else args.get("fund_company")
    return get_asset_type_size_distribution(store, {"date": args.get("date"), "fund_company": fund_company}).model_copy(update={"tool_name": "get_company_product_count_by_asset_type", "intent": "company_product_count"})


def get_company_active_equity_profile(store: SQLiteStore, args: dict) -> ToolResult:
    companies = args.get("companies") or []
    return get_company_total_size_like(store, companies, "主动权益")


def get_company_total_size_like(store: SQLiteStore, companies: list[str] | str | None, asset_type: str) -> ToolResult:
    from src.tools.company_tools import get_company_total_size, list_company_funds_by_size

    total = get_company_total_size(store, {"companies": companies or [], "date": None, "asset_type": asset_type})
    funds = list_company_funds_by_size(store, {"companies": companies or [], "date": None, "asset_type": asset_type, "top_n": 10})
    tables = dict(total.tables)
    tables.update(funds.tables)
    return ToolResult(
        tool_name="get_company_active_equity_profile",
        intent="company_active_equity_profile",
        tables=tables,
        notes=total.notes + ["主动权益画像额外返回头部基金明细。"],
        metadata=total.metadata,
    )


def compare_company_growth(store: SQLiteStore, args: dict) -> ToolResult:
    from src.tools.analytics_tools import get_company_size_trend

    return get_company_size_trend(store, args).model_copy(update={"tool_name": "compare_company_growth", "intent": "company_growth_comparison"})
