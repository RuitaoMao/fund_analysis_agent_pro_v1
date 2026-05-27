"""LangGraph State 定义。

State 是每个 node 之间传递的共享状态。
你可以把它理解成一次 Agent run 的“工作台”：
- 用户问题放进来
- Planner 写入 plan
- Executor 写入 tool_result
- Report Writer 写入 draft_answer
- Self-check 写入检查结果
- 最终输出 final_answer
"""

from __future__ import annotations

from typing import Any, TypedDict

from src.agent.schemas import AgentPlan, ValidationResult, ToolResult, SelfCheckResult, StepTrace


class AgentState(TypedDict, total=False):
    # ===== 输入 =====
    query: str
    session_id: str
    mode: str
    sql_mode: str

    # ===== 多轮上下文 =====
    memory_context: dict[str, Any]
    use_long_memory: bool
    max_steps: int

    # ===== Planner 阶段 =====
    plan: AgentPlan
    tool_route: dict[str, Any]
    plan_validation: ValidationResult

    # ===== Tool 执行阶段 =====
    tool_result: ToolResult
    result_validation: ValidationResult

    # ===== Report / Self-check 阶段 =====
    draft_answer: str
    self_check: SelfCheckResult
    final_answer: str

    # ===== 运行控制 =====
    trace: list[StepTrace]
    tool_history: list[dict[str, Any]]
    observations: list[str]
    errors: list[str]
    retry_count: int
    revision_count: int
    next_action: str
    run_artifacts_dir: str

    # ===== Generated SQL 分支 =====
    sql_plan: Any
    generated_sql: str
    sql_validation_errors: list[str]
    sql_retry_count: int

    # ===== ReAct 闭环增强 =====
    hard_fallback_attempted: bool   # B：hard 失败已转 generated SQL
    auto_routed_mode: str           # E：复杂度分类器选择的模式
    complexity_signals: list[str]   # 复杂度分类器命中的信号
    pending_clarification: dict     # interrupt 暂停时的待补充信息
