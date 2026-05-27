"""Agent 内部使用的 Pydantic schema。

Pydantic 的作用：把 LLM 输出、工具结果、校验结果都变成结构化对象。
它主要保证 structural correctness（结构正确），不保证 semantic correctness（语义一定正确）。
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


Intent = Literal[
    "fund_size_ranking",
    "stock_holding_ranking",
    "company_structure_comparison",
    "company_total_size",
    "company_fund_list",
    "company_size_trend",
    "fund_size_history",
    "fund_holding_detail",
    "fund_holding_concentration",
    "stock_holder_funds",
    "performance_holding_analysis",
    "fund_lookup",
    "asset_type_distribution",
    "wind_category_distribution",
    "wind_category_fund_ranking",
    "company_asset_type_ranking",
    "company_wind_category_ranking",
    "size_growth_ranking",
    "fund_size_date_comparison",
    "performance_ranking",
    "performance_bottom_ranking",
    "fund_performance_detail",
    "fund_performance_comparison",
    "company_average_return_ranking",
    "performance_distribution",
    "company_top_holdings",
    "company_holding_comparison",
    "common_holdings",
    "stock_holding_trend",
    "stock_holding_by_asset_type",
    "fund_holding_change",
    "company_product_count",
    "company_active_equity_profile",
    "company_growth_comparison",
    "fund_screening",
    "size_return_analysis",
    "report_evidence_pack",
    "company_stock_holding_ranking",
    "stock_company_distribution",
    "stock_holder_funds_ranked",
    "company_stock_breakdown",
    "company_stock_comparison",
    "stock_company_concentration",
    "generated_sql_query",
    "unknown",
]

AnswerType = Literal["simple", "report", "clarification"]


class ToolCall(BaseModel):
    """单个工具调用计划。

    用于复杂问题的一次多工具执行；保留 AgentPlan.tool_name/args 是为了兼容旧路径。
    """

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    step_id: str | None = None


class AgentPlan(BaseModel):
    """Planner 输出的标准计划。

    Planner LLM 只能输出这个结构。之后 PlanValidator 会进一步检查。
    """

    intent: Intent
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    answer_type: AnswerType = "simple"
    need_clarification: bool = False
    clarification_question: str | None = None
    rationale: str = ""


class ValidationResult(BaseModel):
    """PlanValidator / ResultValidator 通用校验结果。"""

    passed: bool
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    correction_hint: str | None = None
    repaired_args: dict[str, Any] | None = None
    repaired_tool_calls: list[ToolCall] | None = None
    next_action: Literal["execute", "replan", "clarify", "report", "revise", "final", "fail"] = "execute"


class ToolResult(BaseModel):
    """工具执行结果。

    tables 使用 JSON-friendly 的 rows，而不是直接塞 DataFrame，便于日志、LLM 输入、测试。
    """

    tool_name: str
    intent: str
    tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfCheckResult(BaseModel):
    """最终回答自检结果。"""

    passed: bool
    issues: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None


class StepTrace(BaseModel):
    """ReAct 风格执行轨迹。

    这里的 thought/action/observation 不是为了让 LLM 展示思维链，
    而是用于工程可观测性：记录系统为什么做某步、做了什么、看到什么结果。
    """

    node: str
    thought: str
    action: str
    observation: str
