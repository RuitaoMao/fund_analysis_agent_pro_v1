"""跨表联动分析 tools。"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


def analyze_top_performance_holdings(store: SQLiteStore, args: dict) -> ToolResult:
    """筛选收益率前列基金，并分析这些基金的持仓情况。

    这个工具体现生产级工具的一个核心思想：复杂问题不要让 LLM 自己拼逻辑，
    而是把跨表查询逻辑封装在确定性 tool 中。
    """
    period = args.get("period") or "本年以来"
    top_n = int(args.get("top_n") or 10)
    holding_date = args.get("holding_date") or store.max_date("fund_holding")
    asset_type = args.get("asset_type")

    params: dict = {
        "period": period,
        "top_n": top_n,
        "holding_date": holding_date,
    }

    # 可选资产类型过滤。如果用户明确说“主动权益收益率前10”，则用规模表做过滤。
    if asset_type:
        active_cte = """
        eligible_funds AS (
            SELECT DISTINCT fund_code
            FROM fund_size
            WHERE date = (SELECT MAX(date) FROM fund_size)
              AND asset_type = :asset_type
        ),
        """
        join_eligible = "JOIN eligible_funds e ON p.fund_code = e.fund_code"
        params["asset_type"] = asset_type
    else:
        active_cte = ""
        join_eligible = ""

    perf_sql = f"""
        WITH
        {active_cte}
        top_perf AS (
            SELECT
                p.fund_code,
                p.fund_name,
                p.period,
                p.portfolio_return,
                p.benchmark_return,
                p.excess_return,
                p.max_drawdown
            FROM fund_performance p
            {join_eligible}
            WHERE p.period = :period
            ORDER BY p.portfolio_return DESC
            LIMIT :top_n
        )
        SELECT
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            period AS 区间,
            ROUND(portfolio_return * 100, 2) AS 组合收益率,
            ROUND(benchmark_return * 100, 2) AS 基准收益率,
            ROUND(excess_return * 100, 2) AS 超额收益,
            ROUND(max_drawdown * 100, 2) AS 最大回撤
        FROM top_perf
    """
    top_perf_df = store.query_df(perf_sql, params)
    fund_codes = top_perf_df["基金代码"].tolist() if not top_perf_df.empty else []

    # 这里不用复杂 CTE 再次 join，而是先拿到 top fund_codes，再用 IN 查询持仓。
    # 对 SQLite 来说，这种写法在当前数据规模下更稳定、更容易调试。
    if fund_codes:
        holding_params = {"holding_date": holding_date}
        placeholders = []
        for i, code in enumerate(fund_codes):
            key = f"fund_code_{i}"
            placeholders.append(f":{key}")
            holding_params[key] = code
        in_clause = ", ".join(placeholders)

        stock_summary_df = store.query_df(
            f"""
            SELECT
                h.date AS 日期,
                h.stock_code AS 股票代码,
                h.stock_name AS 股票名称,
                ROUND(SUM(h.holding_value), 2) AS 持仓规模,
                COUNT(DISTINCT h.fund_code) AS 涉及基金数量
            FROM fund_holding h
            WHERE h.date = :holding_date
              AND h.fund_code IN ({in_clause})
            GROUP BY h.date, h.stock_code, h.stock_name
            ORDER BY SUM(h.holding_value) DESC
            LIMIT 20
            """,
            holding_params,
        )

        matched_df = store.query_df(
            f"""
            SELECT DISTINCT h.fund_code
            FROM fund_holding h
            WHERE h.date = :holding_date
              AND h.fund_code IN ({in_clause})
            """,
            holding_params,
        )
    else:
        import pandas as pd
        stock_summary_df = pd.DataFrame()
        matched_df = pd.DataFrame(columns=["fund_code"])

    matched_codes = set(matched_df["fund_code"].tolist()) if not matched_df.empty else set()
    missing_codes = [code for code in fund_codes if code not in matched_codes]

    notes = [
        f"业绩区间：{period}",
        f"持仓日期：{holding_date}",
        "收益率字段已转为百分数显示。",
        "当前数据没有股票行业字段，因此不做行业归因。",
    ]
    if asset_type:
        notes.append(f"资产类型过滤：{asset_type}")
    if missing_codes:
        notes.append(f"部分收益率前列基金未在持仓表中匹配到记录：{', '.join(missing_codes)}")

    return ToolResult(
        tool_name="analyze_top_performance_holdings",
        intent="performance_holding_analysis",
        tables={
            "top_performance_funds": df_to_records(top_perf_df),
            "stock_holding_summary": df_to_records(stock_summary_df),
        },
        notes=notes,
        warnings=[f"未匹配持仓的基金代码：{', '.join(missing_codes)}"] if missing_codes else [],
        metadata={
            "period": period,
            "top_n": top_n,
            "holding_date": holding_date,
            "asset_type": asset_type,
            "fund_codes": fund_codes,
            "missing_fund_codes": missing_codes,
        },
    )


def screen_funds_by_conditions(store: SQLiteStore, args: dict) -> ToolResult:
    """按公司、资产类型、规模下限和收益区间做通用筛选。"""
    date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")
    fund_company = args.get("fund_company")
    period = args.get("period") or "本年以来"
    min_size = args.get("min_size")
    min_return = args.get("min_return")
    top_n = int(args.get("top_n") or 20)

    params: dict = {"date": date, "period": period, "top_n": top_n}
    filters = ["s.date = :date", "p.period = :period"]
    if asset_type:
        filters.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type
    if fund_company:
        filters.append("s.fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"
    if min_size is not None:
        filters.append("s.fund_size >= :min_size")
        params["min_size"] = float(min_size)
    if min_return is not None:
        # 用户常用百分数表达，传入 5 表示 5%。
        filters.append("p.portfolio_return >= :min_return")
        params["min_return"] = float(min_return) / 100

    df = store.query_df(
        f"""
        SELECT
            s.date AS 日期,
            s.fund_code AS 基金代码,
            s.fund_name AS 基金名称,
            s.fund_company AS 基金公司,
            s.asset_type AS 资产类型,
            ROUND(s.fund_size, 2) AS 基金规模,
            p.period AS 业绩区间,
            ROUND(p.portfolio_return * 100, 2) AS 组合收益率,
            ROUND(p.excess_return * 100, 2) AS 超额收益,
            ROUND(p.max_drawdown * 100, 2) AS 最大回撤
        FROM fund_size s
        JOIN fund_performance p ON s.fund_code = p.fund_code
        WHERE {' AND '.join(filters)}
        ORDER BY p.portfolio_return DESC, s.fund_size DESC
        LIMIT :top_n
        """,
        params,
    )
    return ToolResult(
        tool_name="screen_funds_by_conditions",
        intent="fund_screening",
        tables={"fund_screening": df_to_records(df)},
        notes=[
            f"规模日期：{date}",
            f"业绩区间：{period}",
            f"资产类型：{asset_type or '全类型'}",
            "筛选逻辑固定在规模表和业绩表上执行，LLM 不直接写 SQL。",
        ],
        metadata={
            "date": date,
            "asset_type": asset_type,
            "fund_company": fund_company,
            "period": period,
            "min_size": min_size,
            "min_return": min_return,
            "top_n": top_n,
        },
    )


def analyze_size_and_return(store: SQLiteStore, args: dict) -> ToolResult:
    """联合分析规模与收益率，用于回答“大规模基金表现如何”等问题。"""
    date = args.get("date") or store.max_date("fund_size")
    period = args.get("period") or "本年以来"
    asset_type = args.get("asset_type")
    top_n = int(args.get("top_n") or 20)
    params: dict = {"date": date, "period": period, "top_n": top_n}
    filters = ["s.date = :date", "p.period = :period"]
    if asset_type:
        filters.append("s.asset_type = :asset_type")
        params["asset_type"] = asset_type

    ranking_df = store.query_df(
        f"""
        SELECT
            s.fund_code AS 基金代码,
            s.fund_name AS 基金名称,
            s.fund_company AS 基金公司,
            s.asset_type AS 资产类型,
            ROUND(s.fund_size, 2) AS 基金规模,
            ROUND(p.portfolio_return * 100, 2) AS 组合收益率,
            ROUND(p.excess_return * 100, 2) AS 超额收益,
            ROUND(p.max_drawdown * 100, 2) AS 最大回撤
        FROM fund_size s
        JOIN fund_performance p ON s.fund_code = p.fund_code
        WHERE {' AND '.join(filters)}
        ORDER BY s.fund_size DESC
        LIMIT :top_n
        """,
        params,
    )
    summary_df = store.query_df(
        f"""
        SELECT
            s.asset_type AS 资产类型,
            COUNT(*) AS 基金数量,
            ROUND(AVG(s.fund_size), 2) AS 平均规模,
            ROUND(AVG(p.portfolio_return) * 100, 2) AS 平均收益率,
            ROUND(AVG(p.excess_return) * 100, 2) AS 平均超额收益
        FROM fund_size s
        JOIN fund_performance p ON s.fund_code = p.fund_code
        WHERE {' AND '.join(filters)}
        GROUP BY s.asset_type
        ORDER BY AVG(s.fund_size) DESC
        """,
        params,
    )
    return ToolResult(
        tool_name="analyze_size_and_return",
        intent="size_return_analysis",
        tables={"large_fund_return_ranking": df_to_records(ranking_df), "size_return_summary": df_to_records(summary_df)},
        notes=[f"规模日期：{date}", f"业绩区间：{period}", f"资产类型：{asset_type or '全类型'}"],
        metadata={"date": date, "period": period, "asset_type": asset_type, "top_n": top_n},
    )


def build_report_evidence_pack(store: SQLiteStore, args: dict) -> ToolResult:
    """为复杂报告一次性准备规模、业绩和持仓证据包。"""
    from src.tools.company_tools import compare_company_business_structure
    from src.tools.extended_tools import get_company_top_holdings, rank_companies_by_average_return

    companies = args.get("companies") or []
    asset_type = args.get("asset_type")
    structure = compare_company_business_structure(store, {"companies": companies, "date": args.get("date"), "asset_type": asset_type})
    holdings = get_company_top_holdings(store, {"companies": companies, "date": args.get("date"), "asset_type": asset_type, "top_n": args.get("top_n") or 20})
    returns = rank_companies_by_average_return(store, {"period": args.get("period") or "本年以来", "asset_type": asset_type, "top_n": args.get("top_n") or 20})
    tables = {}
    tables.update(structure.tables)
    tables.update(holdings.tables)
    tables.update(returns.tables)
    return ToolResult(
        tool_name="build_report_evidence_pack",
        intent="report_evidence_pack",
        tables=tables,
        notes=structure.notes + holdings.notes + returns.notes + ["该工具用于复杂报告的多表证据准备。"],
        warnings=structure.warnings + holdings.warnings + returns.warnings,
        metadata={"companies": companies, "asset_type": asset_type, "period": args.get("period") or "本年以来"},
    )
