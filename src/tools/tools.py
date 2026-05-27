"""通用基金分析工具（8 个核心工具）。

设计原则：
- 每个工具覆盖一个数据维度，参数通用，通过 group_by / include_* 等开关适配多种查询场景。
- 不为特定问题定制，通过参数组合覆盖原先 40+ 个专精工具的功能。
- 无法覆盖的复杂查询由 generated SQL 路径兜底。
"""

from __future__ import annotations

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.utils.table_utils import df_to_records


# ──────────────────────────────────────────────────────────────────────────
# 内部辅助
# ──────────────────────────────────────────────────────────────────────────

def _normalize_codes(codes) -> list[str]:
    if not codes:
        return []
    if isinstance(codes, str):
        codes = [codes]
    return [str(c).split(".")[0].zfill(6) for c in codes]


def _stock_filter(keyword: str, params: dict, alias: str = "") -> str:
    """构造股票过滤子句。6位纯数字→精确匹配代码；否则模糊匹配名称。"""
    kw = str(keyword).strip()
    col_code = f"{alias}.stock_code" if alias else "stock_code"
    col_name = f"{alias}.stock_name" if alias else "stock_name"
    if kw.isdigit() and len(kw) == 6:
        params["_sk_code"] = kw
        return f"{col_code} = :_sk_code"
    params["_sk_name"] = f"%{kw}%"
    return f"{col_name} LIKE :_sk_name"


def _company_filter(companies: list[str], params: dict, alias: str = "") -> str:
    """构造多家公司的 OR 过滤子句。"""
    col = f"{alias}.fund_company" if alias else "fund_company"
    clauses = []
    for i, c in enumerate(companies):
        k = f"_co_{i}"
        params[k] = f"%{c}%"
        clauses.append(f"{col} LIKE :{k}")
    return "(" + " OR ".join(clauses) + ")" if clauses else "1=1"


# ──────────────────────────────────────────────────────────────────────────
# Tool 1: query_fund_size
# ──────────────────────────────────────────────────────────────────────────

def query_fund_size(store: SQLiteStore, args: dict) -> ToolResult:
    """通用基金规模查询。

    通过 group_by 控制维度：
    - null（默认）：基金级别排名
    - "asset_type"：按资产类型汇总
    - "wind_level1" / "wind_level2"：按 Wind 分类汇总
    - "company"：按基金公司汇总
    通过 include_history=True 展示指定基金的历史规模变化。
    """
    actual_date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")
    fund_company = args.get("fund_company")
    fund_codes = _normalize_codes(args.get("fund_codes"))
    wind_category = args.get("wind_category")
    group_by = args.get("group_by")
    include_history = bool(args.get("include_history", False))
    top_n = min(int(args.get("top_n") or 20), 50)

    params: dict = {"top_n": top_n}
    where: list[str] = []

    # 历史模式且有基金代码 → 不限制日期，展示全期
    if not (include_history and fund_codes):
        where.append("date = :date")
        params["date"] = actual_date

    if asset_type:
        where.append("asset_type = :asset_type")
        params["asset_type"] = asset_type
    if fund_company:
        where.append("fund_company LIKE :fund_company")
        params["fund_company"] = f"%{fund_company}%"
    if fund_codes:
        for i, c in enumerate(fund_codes):
            params[f"_fc_{i}"] = c
        where.append(f"fund_code IN ({','.join(f':_fc_{i}' for i in range(len(fund_codes)))})")
    if wind_category:
        where.append("(wind_level1 LIKE :_wcat OR wind_level2 LIKE :_wcat OR wind_level3 LIKE :_wcat)")
        params["_wcat"] = f"%{wind_category}%"

    w = " AND ".join(where) if where else "1=1"

    if include_history and fund_codes:
        sql = f"""
            SELECT date AS 日期, fund_code AS 基金代码, fund_name AS 基金名称,
                   fund_company AS 基金公司, asset_type AS 资产类型,
                   ROUND(fund_size, 2) AS 基金规模_亿,
                   ROUND(fund_size - LAG(fund_size) OVER (PARTITION BY fund_code ORDER BY date), 2) AS 较上期变化,
                   ROUND((fund_size / NULLIF(LAG(fund_size) OVER (PARTITION BY fund_code ORDER BY date), 0) - 1) * 100, 2) AS 变化率_pct
            FROM fund_size WHERE {w}
            ORDER BY fund_code, date LIMIT :top_n
        """
        table_key = "fund_size_history"
    elif group_by == "asset_type":
        sql = f"""
            SELECT asset_type AS 资产类型, COUNT(DISTINCT fund_code) AS 基金数量,
                   ROUND(SUM(fund_size), 2) AS 合计规模_亿
            FROM fund_size WHERE {w}
            GROUP BY asset_type ORDER BY SUM(fund_size) DESC LIMIT :top_n
        """
        table_key = "size_by_asset_type"
    elif group_by in ("wind_level1", "wind_level2"):
        col = group_by
        sql = f"""
            SELECT {col} AS Wind分类, COUNT(DISTINCT fund_code) AS 基金数量,
                   ROUND(SUM(fund_size), 2) AS 合计规模_亿
            FROM fund_size WHERE {w} AND {col} IS NOT NULL
            GROUP BY {col} ORDER BY SUM(fund_size) DESC LIMIT :top_n
        """
        table_key = "size_by_wind"
    elif group_by == "company":
        sql = f"""
            SELECT fund_company AS 基金公司, COUNT(DISTINCT fund_code) AS 基金数量,
                   ROUND(SUM(fund_size), 2) AS 合计规模_亿
            FROM fund_size WHERE {w}
            GROUP BY fund_company ORDER BY SUM(fund_size) DESC LIMIT :top_n
        """
        table_key = "size_by_company"
    else:
        sql = f"""
            SELECT date AS 日期, fund_code AS 基金代码, fund_name AS 基金名称,
                   fund_company AS 基金公司, asset_type AS 资产类型,
                   ROUND(fund_size, 2) AS 基金规模_亿
            FROM fund_size WHERE {w}
            ORDER BY fund_size DESC LIMIT :top_n
        """
        table_key = "fund_size_ranking"

    df = store.query_df(sql, params)
    notes = [
        f"日期：{actual_date}" + (" (历史全期)" if include_history else ""),
        f"资产类型：{asset_type or '全类型'}",
        f"基金公司：{fund_company or '全市场'}",
        "基金规模单位：亿元。",
    ]
    if group_by:
        notes.append(f"按 {group_by} 汇总展示。")

    return ToolResult(
        tool_name="query_fund_size",
        intent="fund_size_query",
        tables={table_key: df_to_records(df)},
        notes=notes,
        metadata={"date": actual_date, "asset_type": asset_type, "fund_company": fund_company,
                   "group_by": group_by, "include_history": include_history, "top_n": top_n},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 2: query_company_size
# ──────────────────────────────────────────────────────────────────────────

def query_company_size(store: SQLiteStore, args: dict) -> ToolResult:
    """公司维度规模查询。

    - 无 companies：全市场公司规模排名
    - 有 companies：指定公司的规模快照（按资产类型拆分）
    - include_trend=True：展示历史趋势（含 LAG 变化）
    """
    companies_raw = args.get("companies") or []
    if isinstance(companies_raw, str):
        companies_raw = [companies_raw]
    companies: list[str] = [str(c).strip() for c in companies_raw if c]
    actual_date = args.get("date") or store.max_date("fund_size")
    asset_type = args.get("asset_type")
    include_trend = bool(args.get("include_trend", False))
    top_n = min(int(args.get("top_n") or 20), 50)

    params: dict = {}
    co_where = _company_filter(companies, params) if companies else "1=1"
    at_clause = ""
    if asset_type:
        params["_asset_type"] = asset_type
        at_clause = "AND asset_type = :_asset_type"

    tables: dict = {}

    if include_trend:
        # 历史趋势：全部日期
        sql = f"""
            WITH cd AS (
                SELECT date, fund_company, SUM(fund_size) AS ts, COUNT(*) AS sc
                FROM fund_size WHERE {co_where} {at_clause}
                GROUP BY date, fund_company
            )
            SELECT date AS 日期, fund_company AS 基金公司,
                   ROUND(ts, 2) AS 公司总规模_亿, sc AS 份额数量,
                   ROUND(ts - LAG(ts) OVER (PARTITION BY fund_company ORDER BY date), 2) AS 较上期变化,
                   ROUND((ts / NULLIF(LAG(ts) OVER (PARTITION BY fund_company ORDER BY date), 0) - 1) * 100, 2) AS 变化率_pct
            FROM cd ORDER BY fund_company, date
        """
        df = store.query_df(sql, params)
        tables["company_size_trend"] = df_to_records(df)
    else:
        params["_date"] = actual_date
        if not companies:
            # 全市场公司排名
            params["_top_n"] = top_n
            sql = f"""
                SELECT fund_company AS 基金公司, COUNT(DISTINCT fund_code) AS 基金数量,
                       ROUND(SUM(fund_size), 2) AS 合计规模_亿
                FROM fund_size WHERE date = :_date {at_clause}
                GROUP BY fund_company ORDER BY SUM(fund_size) DESC LIMIT :_top_n
            """
            df = store.query_df(sql, params)
            tables["company_ranking"] = df_to_records(df)
        else:
            # 指定公司快照（资产类型拆分）
            sql_total = f"""
                SELECT fund_company AS 基金公司,
                       COUNT(DISTINCT fund_code) AS 基金数量,
                       ROUND(SUM(fund_size), 2) AS 合计规模_亿
                FROM fund_size WHERE date = :_date AND {co_where} {at_clause}
                GROUP BY fund_company ORDER BY SUM(fund_size) DESC
            """
            df_total = store.query_df(sql_total, params)
            tables["company_total"] = df_to_records(df_total)

            sql_breakdown = f"""
                SELECT fund_company AS 基金公司, asset_type AS 资产类型,
                       COUNT(DISTINCT fund_code) AS 基金数量,
                       ROUND(SUM(fund_size), 2) AS 规模_亿,
                       ROUND(SUM(fund_size) / NULLIF(SUM(SUM(fund_size)) OVER (PARTITION BY fund_company), 0) * 100, 1) AS 占公司比例_pct
                FROM fund_size WHERE date = :_date AND {co_where} {at_clause}
                GROUP BY fund_company, asset_type
                ORDER BY fund_company, SUM(fund_size) DESC
            """
            df_bd = store.query_df(sql_breakdown, params)
            tables["company_breakdown"] = df_to_records(df_bd)

    notes = [
        f"日期：{actual_date}" + (" (历史全期)" if include_trend else ""),
        f"公司：{', '.join(companies) if companies else '全市场'}",
        f"资产类型：{asset_type or '全类型'}",
        "规模单位：亿元，来源：fund_size 表。",
    ]
    return ToolResult(
        tool_name="query_company_size",
        intent="company_size_query",
        tables=tables,
        notes=notes,
        metadata={"companies": companies, "date": actual_date, "asset_type": asset_type,
                   "include_trend": include_trend},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 3: query_fund_performance
# ──────────────────────────────────────────────────────────────────────────

def query_fund_performance(store: SQLiteStore, args: dict) -> ToolResult:
    """通用基金业绩查询。

    - 默认：业绩排名（降序 top N 或升序 bottom N）
    - fund_codes 指定：该基金所有区间的业绩明细
    - rank_by_company=True：按公司旗下基金平均收益排名
    """
    period = str(args.get("period") or "本年以来")
    top_n = min(int(args.get("top_n") or 10), 50)
    ascending = bool(args.get("ascending", False))
    # sort_by: portfolio_return (default) | excess_return | max_drawdown
    sort_by = str(args.get("sort_by") or "portfolio_return").strip()
    if sort_by not in {"portfolio_return", "excess_return", "max_drawdown"}:
        sort_by = "portfolio_return"
    fund_codes = _normalize_codes(args.get("fund_codes"))
    fund_company = args.get("fund_company")
    asset_type = args.get("asset_type")
    rank_by_company = bool(args.get("rank_by_company", False))

    tables: dict = {}
    params: dict = {}

    if fund_codes:
        # 指定基金全区间业绩明细
        for i, c in enumerate(fund_codes):
            params[f"_fc_{i}"] = c
        in_clause = ",".join(f":_fc_{i}" for i in range(len(fund_codes)))
        sql = f"""
            SELECT fund_code AS 基金代码, fund_name AS 基金名称, period AS 业绩区间,
                   ROUND(portfolio_return * 100, 2) AS 组合收益率_pct,
                   ROUND(benchmark_return * 100, 2) AS 基准收益率_pct,
                   ROUND(excess_return * 100, 2) AS 超额收益_pct,
                   ROUND(max_drawdown * 100, 2) AS 最大回撤_pct
            FROM fund_performance
            WHERE fund_code IN ({in_clause})
            ORDER BY fund_code, period
        """
        df = store.query_df(sql, params)
        tables["fund_performance_detail"] = df_to_records(df)
        notes = [f"基金范围：{fund_codes}", "组合收益率、基准收益率、超额收益、最大回撤均已转换为百分比展示。"]
    elif rank_by_company:
        # 公司维度平均收益排名
        params.update({"_period": period, "_top_n": top_n})
        size_join = ""
        extra_where = ""
        if asset_type:
            params["_asset_type"] = asset_type
            size_join = f"JOIN fund_size s ON p.fund_code = s.fund_code AND s.date = (SELECT MAX(date) FROM fund_size)"
            extra_where = "AND s.asset_type = :_asset_type"
        sql = f"""
            SELECT s.fund_company AS 基金公司,
                   COUNT(DISTINCT p.fund_code) AS 基金数量,
                   ROUND(AVG(p.portfolio_return) * 100, 2) AS 平均收益率_pct,
                   ROUND(AVG(p.excess_return) * 100, 2) AS 平均超额_pct,
                   ROUND(AVG(p.max_drawdown) * 100, 2) AS 平均回撤_pct
            FROM fund_performance p
            JOIN fund_size s ON p.fund_code = s.fund_code AND s.date = (SELECT MAX(date) FROM fund_size)
            WHERE p.period = :_period {extra_where}
            GROUP BY s.fund_company ORDER BY AVG(p.portfolio_return) {'ASC' if ascending else 'DESC'}
            LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["company_avg_performance"] = df_to_records(df)
        notes = [f"业绩区间：{period}", "按基金公司旗下基金平均收益率排名。", f"资产类型：{asset_type or '全类型'}"]
    else:
        # 基金级别排名
        params.update({"_period": period, "_top_n": top_n})
        extra_where_parts = ["p.period = :_period"]
        join_sql = ""
        if fund_company or asset_type:
            join_sql = "JOIN fund_size s ON p.fund_code = s.fund_code AND s.date = (SELECT MAX(date) FROM fund_size)"
            if fund_company:
                params["_company"] = f"%{fund_company}%"
                extra_where_parts.append("s.fund_company LIKE :_company")
            if asset_type:
                params["_asset_type"] = asset_type
                extra_where_parts.append("s.asset_type = :_asset_type")

        extra_where = " AND ".join(extra_where_parts)
        company_col = ", s.fund_company AS 基金公司, s.asset_type AS 资产类型" if (fund_company or asset_type) else ""

        # sort_by 决定排序字段；max_drawdown 存为正小数（0.37=37%回撤）
        _sort_col_map = {
            "portfolio_return": "p.portfolio_return",
            "excess_return": "p.excess_return",
            "max_drawdown": "p.max_drawdown",
        }
        _sort_col = _sort_col_map.get(sort_by, "p.portfolio_return")
        if sort_by == "max_drawdown":
            # max_drawdown 正数越大越差；ascending=False → "最优优先" → 值最小 → ASC
            _order_dir = "DESC" if ascending else "ASC"
        else:
            # portfolio_return / excess_return：值越大越好；ascending=False → DESC
            _order_dir = "ASC" if ascending else "DESC"

        sql = f"""
            SELECT p.fund_code AS 基金代码, p.fund_name AS 基金名称{company_col},
                   p.period AS 业绩区间,
                   ROUND(p.portfolio_return * 100, 2) AS 组合收益率_pct,
                   ROUND(p.benchmark_return * 100, 2) AS 基准收益率_pct,
                   ROUND(p.excess_return * 100, 2) AS 超额收益_pct,
                   ROUND(p.max_drawdown * 100, 2) AS 最大回撤_pct
            FROM fund_performance p {join_sql}
            WHERE {extra_where}
            ORDER BY {_sort_col} {_order_dir}
            LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["performance_ranking"] = df_to_records(df)
        _sort_label = {"portfolio_return": "组合收益率", "excess_return": "超额收益", "max_drawdown": "最大回撤"}.get(sort_by, "组合收益率")
        order_word = "最小" if (ascending and sort_by != "max_drawdown") or (not ascending and sort_by == "max_drawdown") else "最大"
        notes = [
            f"业绩区间：{period}",
            f"按{_sort_label}{order_word}排序，前 {top_n} 只基金。",
            f"基金公司：{fund_company or '全市场'}",
            f"资产类型：{asset_type or '全类型'}",
            "组合收益率/超额收益/最大回撤均已转换为百分比（最大回撤为正值，值越小表示回撤越轻）。",
        ]

    return ToolResult(
        tool_name="query_fund_performance",
        intent="fund_performance_query",
        tables=tables,
        notes=notes,
        metadata={"period": period, "top_n": top_n, "fund_codes": fund_codes,
                   "fund_company": fund_company, "ascending": ascending, "rank_by_company": rank_by_company},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 4: query_fund_holdings
# ──────────────────────────────────────────────────────────────────────────

def query_fund_holdings(store: SQLiteStore, args: dict) -> ToolResult:
    """基金/公司持仓查询。

    - fund_codes 指定：这些基金持有哪些股票
    - companies 指定：这些基金公司整体重仓哪些股票（JOIN fund_size）
    - 两者均不填：全市场股票持仓规模排名
    - include_concentration=True：附加前 top_n 大持仓集中度统计
    """
    fund_codes = _normalize_codes(args.get("fund_codes"))
    companies_raw = args.get("companies") or []
    if isinstance(companies_raw, str):
        companies_raw = [companies_raw]
    companies: list[str] = [str(c).strip() for c in companies_raw if c]
    actual_date = args.get("date") or store.max_date("fund_holding")
    top_n = min(int(args.get("top_n") or 10), 50)
    include_concentration = bool(args.get("include_concentration", False))

    tables: dict = {}
    params: dict = {"_date": actual_date, "_top_n": top_n}
    notes = [f"持仓日期：{actual_date}"]

    if fund_codes:
        # 指定基金的持仓明细
        for i, c in enumerate(fund_codes):
            params[f"_fc_{i}"] = c
        in_clause = ",".join(f":_fc_{i}" for i in range(len(fund_codes)))
        sql = f"""
            SELECT h.date AS 日期, h.fund_code AS 基金代码,
                   COALESCE(s.fund_name, h.fund_code) AS 基金名称,
                   s.fund_company AS 基金公司,
                   h.stock_code AS 股票代码, h.stock_name AS 股票名称,
                   ROUND(h.holding_value / 1e8, 4) AS 持仓规模_亿,
                   ROUND(h.nav_ratio, 2) AS 净值占比_pct
            FROM fund_holding h
            LEFT JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE h.date = :_date AND h.fund_code IN ({in_clause})
            ORDER BY h.fund_code, h.holding_value DESC
            LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["fund_holdings_detail"] = df_to_records(df)
        notes.append(f"基金范围：{fund_codes}")

        if include_concentration:
            conc_sql = f"""
                WITH ranked AS (
                    SELECT fund_code, stock_code, holding_value, nav_ratio,
                           ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY holding_value DESC) AS rn
                    FROM fund_holding WHERE date = :_date AND fund_code IN ({in_clause})
                ),
                total AS (
                    SELECT fund_code, SUM(holding_value) AS tv, COUNT(*) AS sc
                    FROM fund_holding WHERE date = :_date AND fund_code IN ({in_clause})
                    GROUP BY fund_code
                )
                SELECT r.fund_code AS 基金代码,
                       ROUND(t.tv / 1e8, 4) AS 持仓总规模_亿, t.sc AS 持仓股票数,
                       ROUND(SUM(CASE WHEN r.rn <= :_top_n THEN r.holding_value ELSE 0 END) / NULLIF(t.tv, 0) * 100, 2) AS 前N大持仓占比_pct,
                       ROUND(SUM(CASE WHEN r.rn <= :_top_n THEN r.nav_ratio ELSE 0 END), 2) AS 前N大净值占比合计_pct
                FROM ranked r JOIN total t ON r.fund_code = t.fund_code
                GROUP BY r.fund_code, t.tv, t.sc ORDER BY 前N大持仓占比_pct DESC
            """
            df_conc = store.query_df(conc_sql, params)
            tables["holding_concentration"] = df_to_records(df_conc)
            notes.append(f"集中度口径：前 {top_n} 大持仓。")

    elif companies:
        # 公司整体重仓股
        co_where = _company_filter(companies, params, alias="s")
        sql = f"""
            SELECT h.stock_code AS 股票代码, h.stock_name AS 股票名称,
                   s.fund_company AS 基金公司,
                   ROUND(SUM(h.holding_value) / 1e8, 4) AS 持仓规模_亿,
                   COUNT(DISTINCT h.fund_code) AS 持仓基金数,
                   ROUND(AVG(h.nav_ratio), 2) AS 平均净值占比_pct
            FROM fund_holding h
            JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE h.date = :_date AND {co_where}
            GROUP BY h.stock_code, h.stock_name, s.fund_company
            ORDER BY SUM(h.holding_value) DESC LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["company_holdings"] = df_to_records(df)
        notes.append(f"基金公司：{companies}")
    else:
        # 全市场股票持仓排名
        sql = """
            SELECT date AS 日期, stock_code AS 股票代码, stock_name AS 股票名称,
                   ROUND(SUM(holding_value) / 1e8, 2) AS 持仓规模_亿,
                   COUNT(DISTINCT fund_code) AS 持仓基金数
            FROM fund_holding WHERE date = :_date
            GROUP BY date, stock_code, stock_name
            ORDER BY SUM(holding_value) DESC LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["stock_holding_ranking"] = df_to_records(df)
        notes.append("全市场股票持仓规模排名。")

    notes.append("持仓规模：已从元换算为亿元；净值占比单位为百分点（5 表示 5%）。")
    return ToolResult(
        tool_name="query_fund_holdings",
        intent="fund_holdings_query",
        tables=tables,
        notes=notes,
        metadata={"date": actual_date, "fund_codes": fund_codes, "companies": companies,
                   "top_n": top_n, "include_concentration": include_concentration},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 5: query_stock_holders
# ──────────────────────────────────────────────────────────────────────────

def query_stock_holders(store: SQLiteStore, args: dict) -> ToolResult:
    """个股持有情况查询。

    有 stock_keyword：
      - group_by="fund"（默认）：列出持有该股票的基金
      - group_by="company"：按公司聚合；companies 指定时对比特定公司
    无 stock_keyword：
      - group_by="concentration"：被最多公司同时持有的股票（共识股）
      - 默认：全市场股票持仓排名
    """
    stock_keyword = str(args.get("stock_keyword") or "").strip()
    actual_date = args.get("date") or store.max_date("fund_holding")
    group_by = str(args.get("group_by") or "fund")
    companies_raw = args.get("companies") or []
    if isinstance(companies_raw, str):
        companies_raw = [companies_raw]
    companies: list[str] = [str(c).strip() for c in companies_raw if c]
    fund_company = args.get("fund_company")
    asset_type = args.get("asset_type")
    top_n = min(int(args.get("top_n") or 20), 50)
    min_companies = int(args.get("min_companies") or 2)

    params: dict = {"_date": actual_date, "_top_n": top_n}
    tables: dict = {}
    notes: list[str] = [f"持仓日期：{actual_date}"]

    if not stock_keyword and group_by == "concentration":
        # 共识股：被最多公司同时持有的股票
        at_join = ""
        at_where = ""
        if asset_type:
            params["_asset_type"] = asset_type
            at_where = "AND s.asset_type = :_asset_type"
        params["_min_co"] = min_companies
        sql = f"""
            SELECT h.stock_code AS 股票代码, h.stock_name AS 股票名称,
                   COUNT(DISTINCT s.fund_company) AS 持仓公司数,
                   COUNT(DISTINCT h.fund_code) AS 持仓基金数,
                   ROUND(SUM(h.holding_value) / 1e8, 2) AS 持仓规模_亿
            FROM fund_holding h
            JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE h.date = :_date {at_where}
            GROUP BY h.stock_code, h.stock_name
            HAVING COUNT(DISTINCT s.fund_company) >= :_min_co
            ORDER BY COUNT(DISTINCT s.fund_company) DESC, SUM(h.holding_value) DESC
            LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["stock_concentration"] = df_to_records(df)
        notes += [f"至少被 {min_companies} 家公司同时持有。", f"资产类型：{asset_type or '全类型'}"]
    elif stock_keyword and group_by == "company":
        # 公司维度：哪家公司持有该股票最多
        stock_clause = _stock_filter(stock_keyword, params, alias="h")
        co_filter = _company_filter(companies, params, alias="s") if companies else "1=1"
        at_clause = ""
        if asset_type:
            params["_asset_type"] = asset_type
            at_clause = "AND s.asset_type = :_asset_type"
        sql = f"""
            SELECT s.fund_company AS 基金公司,
                   ROUND(SUM(h.holding_value) / 1e8, 2) AS 持仓规模_亿,
                   COUNT(DISTINCT h.fund_code) AS 持仓基金数,
                   ROUND(AVG(h.nav_ratio), 2) AS 平均净值占比_pct,
                   ROUND(MAX(h.nav_ratio), 2) AS 最高净值占比_pct
            FROM fund_holding h
            JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE h.date = :_date AND {stock_clause} AND {co_filter} {at_clause}
            GROUP BY s.fund_company
            ORDER BY SUM(h.holding_value) DESC LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["company_stock_holding"] = df_to_records(df)
        notes += [f"股票关键词：{stock_keyword}", f"对比公司：{companies or '全市场'}"]
    elif stock_keyword:
        # 基金维度：哪些基金持有该股票最多
        stock_clause = _stock_filter(stock_keyword, params, alias="h")
        extra_where = ""
        if fund_company:
            params["_fund_co"] = f"%{fund_company}%"
            extra_where += " AND s.fund_company LIKE :_fund_co"
        if asset_type:
            params["_asset_type"] = asset_type
            extra_where += " AND s.asset_type = :_asset_type"
        sql = f"""
            SELECT h.fund_code AS 基金代码, s.fund_name AS 基金名称,
                   h.stock_name AS 股票名称,
                   ROUND(h.nav_ratio, 2) AS 净值占比_pct,
                   ROUND(h.holding_value / 1e8, 4) AS 持仓规模_亿,
                   s.fund_company AS 基金公司, s.asset_type AS 资产类型,
                   ROUND(s.fund_size, 2) AS 基金规模_亿
            FROM fund_holding h
            JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE h.date = :_date AND {stock_clause} {extra_where}
            ORDER BY h.nav_ratio DESC LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["fund_stock_holding"] = df_to_records(df)
        notes += [f"股票：{stock_keyword}", f"排序：净值占比（高→低）", f"基金公司：{fund_company or '全市场'}"]
    else:
        # 无股票关键词 → 全市场股票持仓排名
        sql = """
            SELECT h.stock_code AS 股票代码, h.stock_name AS 股票名称,
                   ROUND(SUM(h.holding_value) / 1e8, 2) AS 持仓规模_亿,
                   COUNT(DISTINCT h.fund_code) AS 持仓基金数,
                   COUNT(DISTINCT s.fund_company) AS 持仓公司数
            FROM fund_holding h
            JOIN fund_size s ON h.fund_code = s.fund_code AND h.date = s.date
            WHERE h.date = :_date
            GROUP BY h.stock_code, h.stock_name
            ORDER BY SUM(h.holding_value) DESC LIMIT :_top_n
        """
        df = store.query_df(sql, params)
        tables["global_stock_ranking"] = df_to_records(df)
        notes.append("全市场股票持仓规模排名。")

    notes.append("持仓规模：已从元换算为亿元；净值占比单位为百分点（5 表示 5%）。")
    return ToolResult(
        tool_name="query_stock_holders",
        intent="stock_holders_query",
        tables=tables,
        notes=notes,
        metadata={"date": actual_date, "stock_keyword": stock_keyword, "group_by": group_by,
                   "companies": companies, "top_n": top_n},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 6: screen_funds
# ──────────────────────────────────────────────────────────────────────────

def screen_funds(store: SQLiteStore, args: dict) -> ToolResult:
    """多条件筛选基金。联合三张表：规模 + 业绩 + 持仓。

    参数：
    - date/period: 规模日期 / 业绩区间
    - asset_type / fund_company: 分类过滤
    - min_size: 规模下限（亿元）
    - min_return: 收益率下限（小数，0.10=10%）
    - max_drawdown: 最大回撤上限（小数，0.15=15%）
    - stock_keyword: 持仓股票关键词（选填）
    - min_nav_ratio: 持仓该股票净值占比下限（百分点，5=5%，选填）
    """
    date = args.get("date") or store.max_date("fund_size")
    holding_date = args.get("holding_date") or store.max_date("fund_holding")
    period = str(args.get("period") or "本年以来")
    asset_type = args.get("asset_type")
    fund_company = args.get("fund_company")
    min_size = args.get("min_size")
    min_return = args.get("min_return")
    min_excess_return = args.get("min_excess_return")   # 超额收益下限（小数）
    max_drawdown = args.get("max_drawdown")             # 最大回撤上限（正小数，DB存负数）
    stock_keyword = str(args.get("stock_keyword") or "").strip()
    min_nav_ratio = args.get("min_nav_ratio")
    top_n = min(int(args.get("top_n") or 20), 50)

    params: dict = {"_date": date, "_period": period, "_top_n": top_n}
    size_where = ["s.date = :_date", "p.period = :_period"]

    if asset_type:
        params["_asset_type"] = asset_type
        size_where.append("s.asset_type = :_asset_type")
    if fund_company:
        params["_fund_co"] = f"%{fund_company}%"
        size_where.append("s.fund_company LIKE :_fund_co")
    if min_size is not None:
        params["_min_size"] = float(min_size)
        size_where.append("s.fund_size >= :_min_size")
    if min_return is not None:
        params["_min_return"] = float(min_return)
        size_where.append("p.portfolio_return >= :_min_return")
    if min_excess_return is not None:
        params["_min_excess"] = float(min_excess_return)
        size_where.append("p.excess_return >= :_min_excess")
    if max_drawdown is not None:
        # max_drawdown 在 DB 中存为正小数（0.10 = 10%回撤）
        # 用户传入正小数（0.10 = 10%上限）→ p.max_drawdown <= 0.10
        params["_max_dd"] = abs(float(max_drawdown))
        size_where.append("p.max_drawdown <= :_max_dd")

    stock_subquery = ""
    if stock_keyword:
        params["_holding_date"] = holding_date
        kw = stock_keyword.strip()
        if kw.isdigit() and len(kw) == 6:
            params["_sk"] = kw
            sk_clause = "stock_code = :_sk"
        else:
            params["_sk"] = f"%{kw}%"
            sk_clause = "stock_name LIKE :_sk"
        nav_cond = ""
        if min_nav_ratio is not None:
            params["_min_nav"] = float(min_nav_ratio)
            nav_cond = "AND nav_ratio >= :_min_nav"
        stock_subquery = (
            f"AND s.fund_code IN "
            f"(SELECT fund_code FROM fund_holding WHERE date = :_holding_date AND {sk_clause} {nav_cond})"
        )

    where_sql = " AND ".join(size_where)
    sql = f"""
        SELECT s.fund_code AS 基金代码, s.fund_name AS 基金名称,
               s.fund_company AS 基金公司, s.asset_type AS 资产类型,
               ROUND(s.fund_size, 2) AS 基金规模_亿,
               ROUND(p.portfolio_return * 100, 2) AS 组合收益率_pct,
               ROUND(p.excess_return * 100, 2) AS 超额收益_pct,
               ROUND(p.max_drawdown * 100, 2) AS 最大回撤_pct
        FROM fund_size s
        JOIN fund_performance p ON s.fund_code = p.fund_code
        WHERE {where_sql} {stock_subquery}
        ORDER BY p.portfolio_return DESC LIMIT :_top_n
    """
    df = store.query_df(sql, params)

    conds = []
    if asset_type:
        conds.append(f"资产类型={asset_type}")
    if fund_company:
        conds.append(f'基金公司含"{fund_company}"')
    if min_size is not None:
        conds.append(f"规模≥{min_size}亿")
    if min_return is not None:
        conds.append(f"总收益率≥{min_return*100:.1f}%")
    if min_excess_return is not None:
        conds.append(f"超额收益≥{min_excess_return*100:.1f}%")
    if max_drawdown is not None:
        conds.append(f"最大回撤≤{abs(max_drawdown)*100:.1f}%")
    if stock_keyword:
        conds.append(f"持仓{stock_keyword}")
        if min_nav_ratio is not None:
            conds.append(f"净值占比≥{min_nav_ratio}%")

    return ToolResult(
        tool_name="screen_funds",
        intent="fund_screening",
        tables={"screened_funds": df_to_records(df)},
        notes=[
            f"规模日期：{date}，业绩区间：{period}，持仓日期：{holding_date}",
            f"筛选条件：{'; '.join(conds) if conds else '无'}",
            "组合收益率/超额收益/最大回撤已转换为百分比；规模单位为亿元。",
        ],
        metadata={"date": date, "period": period, "asset_type": asset_type,
                   "min_size": min_size, "min_return": min_return, "max_drawdown": max_drawdown,
                   "stock_keyword": stock_keyword, "min_nav_ratio": min_nav_ratio, "top_n": top_n},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 7: query_performance_holdings
# ──────────────────────────────────────────────────────────────────────────

def query_performance_holdings(store: SQLiteStore, args: dict) -> ToolResult:
    """业绩前 N 基金的持仓分析。先取收益率前 top_n，再汇总其持仓。"""
    period = str(args.get("period") or "本年以来")
    top_n = min(int(args.get("top_n") or 10), 50)
    holding_date = args.get("holding_date") or store.max_date("fund_holding")
    asset_type = args.get("asset_type")

    params: dict = {"_period": period, "_top_n": top_n, "_holding_date": holding_date}
    at_join = ""
    at_where = ""
    if asset_type:
        params["_asset_type"] = asset_type
        at_join = "JOIN fund_size s2 ON p.fund_code = s2.fund_code AND s2.date = (SELECT MAX(date) FROM fund_size)"
        at_where = "AND s2.asset_type = :_asset_type"

    top_funds_sql = f"""
        SELECT p.fund_code, p.fund_name, ROUND(p.portfolio_return * 100, 2) AS 组合收益率_pct
        FROM fund_performance p {at_join}
        WHERE p.period = :_period {at_where}
        ORDER BY p.portfolio_return DESC LIMIT :_top_n
    """
    df_top = store.query_df(top_funds_sql, params)

    holdings_sql = f"""
        WITH top_funds AS (
            SELECT p.fund_code FROM fund_performance p {at_join}
            WHERE p.period = :_period {at_where}
            ORDER BY p.portfolio_return DESC LIMIT :_top_n
        )
        SELECT h.stock_code AS 股票代码, h.stock_name AS 股票名称,
               ROUND(SUM(h.holding_value) / 1e8, 2) AS 合计持仓_亿,
               COUNT(DISTINCT h.fund_code) AS 持有基金数,
               ROUND(AVG(h.nav_ratio), 2) AS 平均净值占比_pct
        FROM fund_holding h
        WHERE h.date = :_holding_date AND h.fund_code IN (SELECT fund_code FROM top_funds)
        GROUP BY h.stock_code, h.stock_name
        ORDER BY SUM(h.holding_value) DESC LIMIT :_top_n
    """
    df_holdings = store.query_df(holdings_sql, params)

    return ToolResult(
        tool_name="query_performance_holdings",
        intent="performance_holdings_analysis",
        tables={
            "top_performance_funds": df_to_records(df_top),
            "top_fund_holdings": df_to_records(df_holdings),
        },
        notes=[
            f"业绩区间：{period}，持仓日期：{holding_date}",
            f"资产类型：{asset_type or '全类型'}",
            f"业绩前 {top_n} 只基金的持仓汇总。",
            "持仓规模已从元换算为亿元。",
        ],
        metadata={"period": period, "top_n": top_n, "holding_date": holding_date, "asset_type": asset_type},
    )


# ──────────────────────────────────────────────────────────────────────────
# Tool 8: lookup_fund
# ──────────────────────────────────────────────────────────────────────────

def lookup_fund(store: SQLiteStore, args: dict) -> ToolResult:
    """按关键词（代码或名称）检索基金基础信息。"""
    keyword = str(args.get("keyword") or "").strip()
    top_n = min(int(args.get("top_n") or 10), 50)

    if not keyword:
        return ToolResult(
            tool_name="lookup_fund",
            intent="lookup",
            tables={},
            notes=["keyword 为空，请提供基金代码或名称关键词。"],
            metadata={},
        )

    params = {"_kw": f"%{keyword}%", "_kw_exact": keyword, "_top_n": top_n}
    date = store.max_date("fund_size")
    params["_date"] = date

    df = store.query_df(
        """
        SELECT fund_code AS 基金代码, fund_name AS 基金名称,
               fund_company AS 基金公司, asset_type AS 资产类型,
               ROUND(fund_size, 2) AS 最新规模_亿, date AS 数据日期
        FROM fund_size
        WHERE date = :_date
          AND (fund_code = :_kw_exact OR fund_code LIKE :_kw OR fund_name LIKE :_kw)
        ORDER BY fund_size DESC LIMIT :_top_n
        """,
        params,
    )

    return ToolResult(
        tool_name="lookup_fund",
        intent="lookup",
        tables={"lookup_result": df_to_records(df)},
        notes=[f"检索关键词：{keyword}", f"数据日期：{date}"],
        metadata={"keyword": keyword, "top_n": top_n},
    )
