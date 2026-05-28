"""LLM + 数据生成 SQL 模式。

这不是“裸奔 SQL”：LLM 只能生成只读查询，系统会做白名单校验、dry run、
LIMIT 保护和结果校验。适合处理专家 tools 没穷举到的组合筛选问题。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from src.agent.schemas import AgentPlan, StepTrace, ToolResult
from src.data.sqlite_store import SQLiteStore
from src.llm.client import LLMClient
from src.utils.json_utils import extract_json_object
from src.utils.table_utils import df_to_records


SQL_SCHEMA_CONTEXT = """
## 可用表和字段

### fund_size  — 基金规模（季度末截面）
  date          TEXT    '2025-03-31'|'2025-06-30'|'2025-09-30'|'2025-12-31'|'2026-03-31'（共5个日期）
  fund_code     TEXT    基金代码，6位字符串
  fund_name     TEXT    基金名称
  fund_company  TEXT    基金公司简称，如 '易方达'|'华夏'|'广发'|'富国'|'嘉实'|'招商'|'南方'|'博时'
  asset_type    TEXT    '主动权益'|'被动权益'|'纯债'|'现金管理'|'量化'|'FOF'|'多资产投资'|'REITs'
  fund_size     REAL    基金规模，单位：**亿元**（范围 0~7993 亿，均值约 27 亿）

### fund_holding  — 基金持仓（季度末截面）
  date            TEXT   '2025-12-31'|'2026-03-31'（只有2个日期）
  fund_code       TEXT   基金代码，与 fund_size.fund_code 一致
  stock_code      TEXT   股票代码
  stock_name      TEXT   股票名称，如 '贵州茅台'|'宁德时代'|'中际旭创'|'腾讯控股'
  holding_value   REAL   持仓市值，单位：**元**（不是亿元！范围约 1.91~220亿元）
                         ⚠️ 用户说"10亿"时写 holding_value > 1e9（即 10*1e8）
  nav_ratio       REAL   持仓占该基金净值的比例，单位：**百分点**（范围 0~31，5 表示 5%）
                         ⚠️ 用户说"超过5%"时写 nav_ratio > 5（不是 > 0.05）

### fund_performance  — 基金业绩
  fund_code         TEXT   基金代码
  fund_name         TEXT   基金名称
  period            TEXT   '本年以来'|'最近一月'|'最近一年'|'最近三年'|'最近五年'
  portfolio_return  REAL   组合收益率，单位：**小数**（0.05 表示 5%）
                           ⚠️ 用户说"收益超过10%"时写 portfolio_return > 0.10
  benchmark_return  REAL   基准收益率，同上小数
  excess_return     REAL   超额收益，同上小数
  max_drawdown      REAL   最大回撤，单位：**小数**（正数，0.15 表示 15%）
                           ⚠️ 用户说"回撤小于15%"时写 max_drawdown < 0.15

## 关键口径规则
- 未指定规模日期 → date = (SELECT MAX(date) FROM fund_size)
- 未指定持仓日期 → date = (SELECT MAX(date) FROM fund_holding)
- 未指定业绩区间 → period = '本年以来'
- 持仓与规模联查时必须对齐日期：JOIN ON h.fund_code=s.fund_code AND h.date=s.date
- fund_performance 没有 date 列，直接用 fund_code 关联
- 展示收益率时建议 ROUND(portfolio_return*100, 2)；展示持仓市值时建议 /1e8 转换为亿元
""".strip()


SQL_FEW_SHOT = """
## 示例（学习 JOIN 模式和单位处理）

Q: 规模前100基金中本年以来超额收益最高的10只
SQL:
WITH top100 AS (
    SELECT fund_code FROM fund_size
    WHERE date=(SELECT MAX(date) FROM fund_size)
    ORDER BY fund_size DESC LIMIT 100
)
SELECT p.fund_code, p.fund_name,
       ROUND(p.excess_return*100,2) AS 超额收益率_pct,
       ROUND(p.portfolio_return*100,2) AS 组合收益率_pct
FROM fund_performance p
WHERE p.fund_code IN (SELECT fund_code FROM top100)
  AND p.period='本年以来'
ORDER BY p.excess_return DESC LIMIT 10

Q: 持仓贵州茅台净值占比超过5%且本年以来收益最高的前5只基金
SQL:
SELECT h.fund_code, s.fund_name, s.fund_company,
       h.nav_ratio AS 茅台净值占比_pct,
       ROUND(p.portfolio_return*100,2) AS 本年收益率_pct
FROM fund_holding h
JOIN fund_size s ON h.fund_code=s.fund_code AND h.date=s.date
JOIN fund_performance p ON h.fund_code=p.fund_code
WHERE h.stock_name='贵州茅台'
  AND h.date=(SELECT MAX(date) FROM fund_holding)
  AND h.nav_ratio > 5
  AND p.period='本年以来'
ORDER BY p.portfolio_return DESC LIMIT 5

Q: 同时被易方达和华夏持有且各自持仓市值超过5亿的股票
SQL:
WITH yfd AS (
    SELECT h.stock_name, SUM(h.holding_value) AS val
    FROM fund_holding h
    JOIN fund_size s ON h.fund_code=s.fund_code AND h.date=s.date
    WHERE s.fund_company='易方达' AND h.date=(SELECT MAX(date) FROM fund_holding)
    GROUP BY h.stock_name HAVING val > 5e8
),
hx AS (
    SELECT h.stock_name, SUM(h.holding_value) AS val
    FROM fund_holding h
    JOIN fund_size s ON h.fund_code=s.fund_code AND h.date=s.date
    WHERE s.fund_company='华夏' AND h.date=(SELECT MAX(date) FROM fund_holding)
    GROUP BY h.stock_name HAVING val > 5e8
)
SELECT y.stock_name,
       ROUND(y.val/1e8,2) AS 易方达持仓亿,
       ROUND(hx.val/1e8,2) AS 华夏持仓亿
FROM yfd y JOIN hx ON y.stock_name=hx.stock_name
ORDER BY (y.val+hx.val) DESC LIMIT 20

Q: 持仓集中度最高的10只基金（前10大持仓nav_ratio之和）及其最大回撤
SQL:
WITH conc AS (
    SELECT fund_code, SUM(nav_ratio) AS concentration
    FROM (
        SELECT fund_code, nav_ratio,
               ROW_NUMBER() OVER (PARTITION BY fund_code ORDER BY nav_ratio DESC) AS rn
        FROM fund_holding WHERE date=(SELECT MAX(date) FROM fund_holding)
    ) WHERE rn <= 10
    GROUP BY fund_code
)
SELECT c.fund_code, s.fund_name, s.fund_company,
       ROUND(c.concentration,2) AS 前10持仓集中度_pct,
       ROUND(p.max_drawdown*100,2) AS 最大回撤_pct
FROM conc c
JOIN fund_size s ON c.fund_code=s.fund_code AND s.date=(SELECT MAX(date) FROM fund_size)
JOIN fund_performance p ON c.fund_code=p.fund_code AND p.period='本年以来'
ORDER BY c.concentration DESC LIMIT 10
""".strip()


SQL_SYSTEM_PROMPT = f"""
你是基金分析系统的 SQL Planner。请根据用户问题生成一条 SQLite 只读 SQL。

## 硬性要求
1. 只返回 JSON，不要 markdown 代码块。
2. SQL 只能使用下方列出的表和字段，禁止访问其他表。
3. SQL 只能是 SELECT 或 WITH 开头的单条查询。
4. SQL 必须包含 LIMIT，且 LIMIT <= 200。
5. 不允许 SELECT *。
6. 未指定日期/period 时使用默认口径。
7. 输出列使用中文别名。
8. ⚠️ 严格遵守单位：holding_value 单位是**元**，fund_size 单位是**亿元**，
   portfolio_return/max_drawdown 是**小数**，nav_ratio 是**百分点**。

{SQL_SCHEMA_CONTEXT}

{SQL_FEW_SHOT}

## 输出 JSON schema
{{
  "query_intent": "一句英文意图",
  "tables": ["使用到的表"],
  "assumptions": ["默认口径说明，包含日期和 period"],
  "sql": "SQLite SQL",
  "expected_columns": ["中文列名"],
  "explanation": "一句话说明查询逻辑"
}}
""".strip()


@dataclass
class SQLPlan:
    query_intent: str
    tables: list[str]
    assumptions: list[str]
    sql: str
    expected_columns: list[str]
    explanation: str


class GeneratedSQLAgent:
    """受控 SQL 生成与执行子系统。"""

    allowed_tables = {"fund_size", "fund_holding", "fund_performance"}
    allowed_columns = {
        "date",
        "fund_code",
        "fund_name",
        "fund_company",
        "wind_level1",
        "wind_level2",
        "wind_level3",
        "asset_type",
        "fund_size",
        "stock_code",
        "stock_name",
        "holding_quantity",
        "holding_value",
        "nav_ratio",
        "period",
        "portfolio_return",
        "benchmark_return",
        "excess_return",
        "max_drawdown",
    }
    banned = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REPLACE|TRUNCATE)\b",
        re.IGNORECASE,
    )

    def __init__(self, store: SQLiteStore, llm_client: LLMClient | None = None):
        self.store = store
        self.llm_client = llm_client

    def run(self, query: str, mode: str = "mock", max_retries: int = 2) -> dict[str, Any]:
        trace: list[StepTrace] = []
        plan: SQLPlan | None = None
        errors: list[str] = []
        last_failed_sql: str = ""
        for attempt in range(max_retries + 1):
            plan = self._plan(query, mode=mode, previous_errors=errors,
                              last_failed_sql=last_failed_sql)
            trace.append(
                StepTrace(
                    node="sql_planner_node",
                    thought="把用户问题转换成受控只读 SQL 查询。",
                    action="GeneratedSQLAgent.plan()",
                    observation=f"intent={plan.query_intent}, tables={plan.tables}",
                )
            )
            validation_errors = self.validate_sql(plan.sql)
            if validation_errors:
                last_failed_sql = plan.sql   # 把失败 SQL 传给下一次重试
                errors.extend(validation_errors)
                trace.append(
                    StepTrace(
                        node="sql_validator_node",
                        thought="检查 SQL 是否只读、是否只访问白名单表、是否包含安全 LIMIT。",
                        action="validate_sql()",
                        observation=f"passed=False, errors={validation_errors}",
                    )
                )
                if mode == "mock":
                    break
                continue
            trace.append(
                StepTrace(
                    node="sql_validator_node",
                    thought="检查 SQL 是否只读、是否只访问白名单表、是否包含安全 LIMIT。",
                    action="validate_sql()",
                    observation="passed=True",
                )
            )
            try:
                self.store.query_df(f"EXPLAIN QUERY PLAN {plan.sql}")
                trace.append(
                    StepTrace(
                        node="sql_dry_run_node",
                        thought="用 SQLite EXPLAIN 做 dry run，提前发现字段或语法错误。",
                        action="EXPLAIN QUERY PLAN",
                        observation="passed=True",
                    )
                )
                df = self.store.query_df(plan.sql)
                result = ToolResult(
                    tool_name="generated_sql_query",
                    intent="generated_sql_query",
                    tables={"generated_sql_result": df_to_records(df)},
                    notes=plan.assumptions + [plan.explanation, "SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。"],
                    metadata={"sql": plan.sql, "tables": plan.tables, "expected_columns": plan.expected_columns},
                )
                trace.append(
                    StepTrace(
                        node="sql_executor_node",
                        thought="执行通过校验的只读 SQL。",
                        action="SQLiteStore.query_df(sql)",
                        observation=f"rows={len(df)}, columns={list(df.columns)}",
                    )
                )
                return {"plan": plan, "tool_result": result, "trace": trace, "errors": errors}
            except Exception as exc:
                last_failed_sql = plan.sql   # 把失败 SQL 传给下一次重试
                errors.append(str(exc))
                trace.append(
                    StepTrace(
                        node="sql_dry_run_node",
                        thought="SQL dry run 或执行失败，记录错误并尝试让 LLM 修复。",
                        action="EXPLAIN/execute",
                        observation=f"error={exc}",
                    )
                )
                if mode == "mock":
                    break
        assert plan is not None
        result = ToolResult(
            tool_name="generated_sql_query",
            intent="generated_sql_query",
            tables={},
            warnings=errors,
            notes=["生成 SQL 模式未能得到可执行结果。"],
            metadata={"sql": plan.sql if plan else ""},
        )
        return {"plan": plan, "tool_result": result, "trace": trace, "errors": errors}

    def _plan(self, query: str, mode: str, previous_errors: list[str],
              last_failed_sql: str = "",
              memory_context: dict | None = None) -> SQLPlan:
        if mode == "mock" or self.llm_client is None:
            return self._mock_plan(query)

        # 从 memory context 提取上轮关键信息，帮助 LLM 解析"这些基金"等指代
        context_lines: list[str] = []
        if memory_context:
            last_query = memory_context.get("last_query")
            fund_codes = memory_context.get("last_fund_codes") or []
            stock_codes = memory_context.get("last_stock_codes") or []
            companies = memory_context.get("last_companies") or []
            if last_query:
                context_lines.append(f"上轮问题：{last_query}")
            if fund_codes:
                context_lines.append(f"上轮结果基金代码（共{len(fund_codes)}只）：{', '.join(fund_codes[:20])}")
            if stock_codes:
                context_lines.append(f"上轮结果股票代码：{', '.join(stock_codes[:10])}")
            if companies:
                context_lines.append(f"上轮涉及基金公司：{', '.join(companies[:5])}")
        context_block = ""
        if context_lines:
            context_block = '\n\n## 上轮会话上下文（用于解析"这些基金"/"刚才"等指代）\n' + "\n".join(context_lines)

        if previous_errors and last_failed_sql:
            # 重试：把上次失败的 SQL 和具体错误一起传回，让 LLM 做定向修复
            user_prompt = (
                f"用户问题：{query}{context_block}\n\n"
                f"上次生成的 SQL（有错误，请修复）：\n```sql\n{last_failed_sql}\n```\n\n"
                f"错误信息：{previous_errors[-1]}\n\n"
                f"请分析错误原因，重新生成正确的 SQL，输出 JSON。"
            )
        else:
            user_prompt = f"用户问题：{query}{context_block}\n\n请输出 JSON。"
        raw = self.llm_client.chat(
            role="sql",
            system_prompt=SQL_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=True,
            temperature=0.0,
            # 复杂多表嵌套 SQL 的 JSON 容易超过 3000 tokens（尤其 thinking 模式占额外预算）；
            # 上限过低会导致 JSON 被截断（"Unterminated string"），进入 clarification 兜底。
            max_tokens=5000,
        )
        try:
            data = extract_json_object(raw)
        except JSONDecodeError as exc:
            preview = raw[:160].replace("\n", " ")
            raise ValueError(f"SQL planner 未返回合法 JSON：{exc}; raw_preview={preview}") from exc
        return SQLPlan(
            query_intent=str(data.get("query_intent") or "generated_sql_query"),
            tables=list(data.get("tables") or []),
            assumptions=list(data.get("assumptions") or []),
            sql=str(data.get("sql") or "").strip(),
            expected_columns=list(data.get("expected_columns") or []),
            explanation=str(data.get("explanation") or ""),
        )

    def _mock_plan(self, query: str) -> SQLPlan:
        """规则 SQL planner，保证 mock 模式不依赖真实 API。"""
        if "规模" in query and ("top100" in query.lower() or "前100" in query) and ("业绩" in query or "收益" in query):
            sql = """
            WITH latest_size AS (
                SELECT date, fund_code, fund_name, fund_company, asset_type, fund_size
                FROM fund_size
                WHERE date = (SELECT MAX(date) FROM fund_size)
            ),
            size_top AS (
                SELECT fund_code, fund_name, fund_company, asset_type, fund_size
                FROM latest_size
                ORDER BY fund_size DESC
                LIMIT 100
            )
            SELECT
                s.fund_code AS 基金代码,
                s.fund_name AS 基金名称,
                s.fund_company AS 基金公司,
                s.asset_type AS 资产类型,
                ROUND(s.fund_size, 2) AS 基金规模,
                p.period AS 业绩区间,
                ROUND(p.portfolio_return * 100, 2) AS 组合收益率,
                ROUND(p.excess_return * 100, 2) AS 超额收益,
                ROUND(p.max_drawdown * 100, 2) AS 最大回撤
            FROM size_top s
            JOIN fund_performance p ON s.fund_code = p.fund_code
            WHERE p.period = '本年以来'
            ORDER BY p.portfolio_return DESC
            LIMIT 10
            """.strip()
            return SQLPlan(
                query_intent="performance_rank_within_size_top",
                tables=["fund_size", "fund_performance"],
                assumptions=["用户未指定日期，使用 fund_size 最新日期。", "用户未指定业绩区间，使用 本年以来。"],
                sql=sql,
                expected_columns=["基金代码", "基金名称", "基金公司", "基金规模", "组合收益率"],
                explanation="先取最新规模前100基金，再关联业绩表并按组合收益率取前10。",
            )
        sql = """
        SELECT
            date AS 日期,
            fund_code AS 基金代码,
            fund_name AS 基金名称,
            fund_company AS 基金公司,
            asset_type AS 资产类型,
            ROUND(fund_size, 2) AS 基金规模
        FROM fund_size
        WHERE date = (SELECT MAX(date) FROM fund_size)
        ORDER BY fund_size DESC
        LIMIT 10
        """.strip()
        return SQLPlan(
            query_intent="default_size_ranking",
            tables=["fund_size"],
            assumptions=["未匹配到更具体的 mock SQL 规则，默认查询最新规模前10基金。"],
            sql=sql,
            expected_columns=["基金代码", "基金名称", "基金规模"],
            explanation="使用最新规模日期按基金规模降序返回前10。",
        )

    def validate_sql(self, sql: str) -> list[str]:
        errors: list[str] = []
        compact = sql.strip().rstrip(";")
        if not compact:
            return ["SQL 为空。"]
        if ";" in compact:
            errors.append("不允许多语句 SQL。")
        if not re.match(r"^\s*(SELECT|WITH)\b", compact, flags=re.IGNORECASE):
            errors.append("SQL 必须以 SELECT 或 WITH 开头。")
        if self.banned.search(compact):
            errors.append("SQL 包含禁止的写入/DDL/系统操作。")
        if re.search(r"SELECT\s+\*", compact, flags=re.IGNORECASE):
            errors.append("不允许 SELECT *。")
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", compact, flags=re.IGNORECASE))
        # WITH latest_size AS (...) 这类 CTE 名称会出现在后续 FROM/JOIN 中，
        # 它们不是物理表，因此从表白名单校验中排除。
        cte_aliases = set(re.findall(r"(?:WITH|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(", compact, flags=re.IGNORECASE))
        tables = tables - cte_aliases
        unknown_tables = sorted(tables - self.allowed_tables)
        if unknown_tables:
            errors.append(f"访问了非白名单表：{unknown_tables}")
        limits = [int(item) for item in re.findall(r"\bLIMIT\s+(\d+)\b", compact, flags=re.IGNORECASE)]
        if not limits:
            errors.append("SQL 必须包含 LIMIT。")
        elif max(limits) > 200:
            errors.append("LIMIT 不能超过 200。")
        return errors


def generated_sql_answer(query: str, sql: str, result: ToolResult) -> str:
    if not result.tables:
        return "生成 SQL 模式未能得到可用结果。\n\n" + "\n".join(f"- {w}" for w in result.warnings)
    rows = result.tables.get("generated_sql_result", [])
    return (
        "以下结果来自 LLM 生成 SQL 模式，SQL 已经过只读白名单校验和 dry run。\n\n"
        f"```sql\n{sql.strip()}\n```\n\n"
        f"结果行数：{len(rows)}\n\n"
        f"{rows}\n\n"
        "### 数据口径说明\n"
        + "\n".join(f"- {note}" for note in result.notes)
    )


def sql_plan_to_agent_plan(sql_plan: SQLPlan) -> AgentPlan:
    return AgentPlan(
        intent="generated_sql_query",
        tool_name="generated_sql_query",
        args={"sql": sql_plan.sql, "tables": sql_plan.tables},
        answer_type="report",
        rationale=sql_plan.explanation,
    )
