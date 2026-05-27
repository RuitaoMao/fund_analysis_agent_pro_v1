"""Report Writer Agent。

Report Writer 的职责是把 ToolResult 转成用户可读的中文答案。
mock 模式使用模板；llm 模式可调用真实 LLM。
"""

from __future__ import annotations

from src.agent.schemas import AgentPlan, ToolResult, ValidationResult
from src.llm.client import LLMClient
from src.llm.prompts import REPORT_SYSTEM_PROMPT
from src.utils.table_utils import records_to_markdown


class ReportWriterAgent:
    """报告写作子智能体。"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    def write(
        self,
        *,
        query: str,
        plan: AgentPlan,
        tool_result: ToolResult | None,
        plan_validation: ValidationResult | None,
        result_validation: ValidationResult | None,
        mode: str = "mock",
    ) -> str:
        """生成回答。"""
        if plan.need_clarification:
            return plan.clarification_question or "请补充您的问题。"

        if tool_result is None:
            issues = plan_validation.issues if plan_validation else []
            return "当前问题无法执行。\n\n" + "\n".join(f"- {x}" for x in issues)

        if mode == "mock":
            return self._template_report(query, plan, tool_result, result_validation)
        return self._llm_report(query, plan, tool_result, result_validation)

    def _llm_report(self, query: str, plan: AgentPlan, tool_result: ToolResult, result_validation: ValidationResult | None) -> str:
        if self.llm_client is None:
            raise RuntimeError("LLMClient 未初始化。")
        user_prompt = (
            f"用户问题：{query}\n\n"
            f"Planner 计划：{plan.model_dump()}\n\n"
            f"工具结果：{tool_result.model_dump()}\n\n"
            f"结果校验：{result_validation.model_dump() if result_validation else None}\n\n"
            "请生成中文回答。要求：\n"
            "- 不要把 Python list/dict 原样贴给用户。\n"
            "- 如有结构化结果，优先整理成 Markdown 表格。\n"
            "- 除非用户明确要求 SQL 或计算过程，否则不要全文展示 SQL；只在口径说明中简要说明数据来自哪个表、如何聚合/筛选。\n"
            "- 如果是 Generated SQL 结果，可以说明 SQL 已通过只读白名单校验和 dry run。\n"
        )
        return self.llm_client.chat(
            role="report",
            system_prompt=REPORT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=False,
            temperature=0.2,
            max_tokens=1800,
        )

    def _template_report(
        self,
        query: str,
        plan: AgentPlan,
        result: ToolResult,
        result_validation: ValidationResult | None,
    ) -> str:
        """模板报告，方便 mock 测试。"""
        if result.tool_name == "get_top_funds_by_size":
            table = records_to_markdown(result.tables.get("fund_size_ranking", []), max_rows=20)
            return self._with_notes("以下为指定口径下基金规模排名：", table, result, result_validation)

        if result.tool_name == "get_top_stocks_by_holding":
            table = records_to_markdown(result.tables.get("stock_holding_ranking", []), max_rows=20)
            return self._with_notes("以下为股票持仓规模排名：", table, result, result_validation)

        if result.tool_name == "multi_tool":
            parts = ["## 多工具综合分析结果"]
            for table_name, rows in result.tables.items():
                parts.extend([f"\n### {table_name}", records_to_markdown(rows, max_rows=50)])
            return self._with_notes("\n".join(parts), "", result, result_validation)

        if result.tool_name == "get_company_total_size":
            total = records_to_markdown(result.tables.get("company_total_size", []), max_rows=20)
            structure = records_to_markdown(result.tables.get("asset_structure", []), max_rows=50)
            body = (
                "## 基金公司总规模\n\n"
                "### 1. 公司总规模\n\n"
                f"{total}\n\n"
                "### 2. 资产类型拆分\n\n"
                f"{structure}"
            )
            return self._with_notes(body, "", result, result_validation)

        if result.tool_name == "list_company_funds_by_size":
            summary = records_to_markdown(result.tables.get("company_summary", []), max_rows=20)
            funds = records_to_markdown(result.tables.get("company_funds", []), max_rows=50)
            body = (
                "## 基金公司旗下基金规模明细\n\n"
                "### 1. 公司汇总\n\n"
                f"{summary}\n\n"
                "### 2. 基金代码/份额明细\n\n"
                f"{funds}"
            )
            return self._with_notes(body, "", result, result_validation)

        if result.tool_name == "get_company_size_trend":
            table = records_to_markdown(result.tables.get("company_size_trend", []), max_rows=50)
            return self._with_notes("## 基金公司规模变化趋势", table, result, result_validation)

        if result.tool_name == "get_fund_size_history":
            table = records_to_markdown(result.tables.get("fund_size_history", []), max_rows=50)
            return self._with_notes("## 基金规模历史变化", table, result, result_validation)

        if result.tool_name == "get_fund_holdings_detail":
            table = records_to_markdown(result.tables.get("fund_holdings_detail", []), max_rows=50)
            return self._with_notes("## 基金持仓明细", table, result, result_validation)

        if result.tool_name == "analyze_fund_holding_concentration":
            table = records_to_markdown(result.tables.get("fund_holding_concentration", []), max_rows=50)
            return self._with_notes("## 基金持仓集中度分析", table, result, result_validation)

        if result.tool_name == "find_funds_holding_stock":
            table = records_to_markdown(result.tables.get("stock_holder_funds", []), max_rows=50)
            return self._with_notes("## 持有该股票的基金列表", table, result, result_validation)

        if result.tool_name == "compare_company_business_structure":
            summary = records_to_markdown(result.tables.get("company_summary", []), max_rows=20)
            structure = records_to_markdown(result.tables.get("asset_structure", []), max_rows=50)
            body = (
                "## 公司业务结构对比\n\n"
                "### 1. 公司总规模对比\n\n"
                f"{summary}\n\n"
                "### 2. 各资产类型规模和占比\n\n"
                f"{structure}\n\n"
                "### 3. 简要结论\n\n"
                "- 请重点关注公司总规模、资产类型占比和基金数量结构。\n"
                "- 当前结论仅基于规模表，不代表完整投研判断。"
            )
            return self._with_notes(body, "", result, result_validation)

        if result.tool_name == "analyze_top_performance_holdings":
            perf = records_to_markdown(result.tables.get("top_performance_funds", []), max_rows=20)
            holdings = records_to_markdown(result.tables.get("stock_holding_summary", []), max_rows=20)
            body = (
                "## 收益率前列基金及持仓分析\n\n"
                "### 1. 收益率前列基金\n\n"
                f"{perf}\n\n"
                "### 2. 这些基金的主要持仓股票汇总\n\n"
                f"{holdings}\n\n"
                "### 3. 简要分析\n\n"
                "- 上表展示了当前区间内收益率前列基金及其主要股票持仓。\n"
                "- 当前数据没有行业字段，因此不做行业归因。"
            )
            return self._with_notes(body, "", result, result_validation)

        if result.tool_name == "lookup_fund":
            table = records_to_markdown(
                result.tables.get("fund_lookup", result.tables.get("lookup_result", [])),
                max_rows=20,
            )
            return self._with_notes("以下为基金检索结果：", table, result, result_validation)

        # ── 通用工具 / Generated SQL 通用渲染 ──
        # 对所有未匹配的工具（新通用工具、generated_sql_query 等），
        # 把每张表渲染为 Markdown 表格，而不是原始 dict。
        parts: list[str] = []
        if len(result.tables) == 1:
            rows = next(iter(result.tables.values()))
            table = records_to_markdown(rows, max_rows=50)
            parts.extend(["以下为查询结果：", "", table])
        else:
            parts.append("## 查询结果")
            for table_name, rows in result.tables.items():
                parts.extend([f"\n### {table_name}", records_to_markdown(rows, max_rows=50)])
        return self._with_notes("\n".join(parts), "", result, result_validation)

    @staticmethod
    def _with_notes(title: str, table_or_empty: str, result: ToolResult, validation: ValidationResult | None) -> str:
        parts = [title]
        if table_or_empty:
            parts.extend(["", table_or_empty])
        if result.notes:
            parts.extend(["", "### 数据口径说明"])
            parts.extend([f"- {note}" for note in result.notes])
        warnings = []
        if result.warnings:
            warnings.extend(result.warnings)
        if validation and validation.warnings:
            warnings.extend(validation.warnings)
        warnings = list(dict.fromkeys(warnings))
        if warnings:
            parts.extend(["", "### 注意事项"])
            parts.extend([f"- {w}" for w in warnings])
        return "\n".join(parts)
