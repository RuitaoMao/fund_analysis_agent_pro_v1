"""Result Validator。

ResultValidator 检查工具结果本身是否可靠、是否为空、是否有重要 warning。
"""

from __future__ import annotations

from src.agent.schemas import AgentPlan, ToolResult, ValidationResult


class ResultValidator:
    """工具结果校验器。"""

    def validate(self, query: str, plan: AgentPlan, result: ToolResult) -> ValidationResult:
        warnings = list(result.warnings)
        issues: list[str] = []
        correction_hint: str | None = None

        if not result.tables:
            # generated SQL 执行失败（无任何表）→ 触发重试
            if result.tool_name == "generated_sql_query":
                issues.append("生成 SQL 未能返回结果，请检查 SQL 是否正确。")
            else:
                issues.append("工具没有返回任何表格。")
        else:
            all_empty = all(len(rows) == 0 for rows in result.tables.values())
            if all_empty:
                # 空结果：不是错误，是数据库中没有满足条件的记录
                # 对于 generated SQL 和新通用工具，空结果是正常状态，直接让 report_writer 告知用户
                return ValidationResult(
                    passed=True,
                    warnings=["查询结果为空（0 行），数据库中可能没有满足条件的记录。建议放宽筛选条件。"],
                    next_action="report",
                )

        if issues:
            return ValidationResult(
                passed=False,
                issues=issues,
                warnings=warnings,
                correction_hint=correction_hint,
                next_action="replan" if correction_hint else "clarify",
            )
        return ValidationResult(
            passed=True,
            warnings=warnings,
            correction_hint=correction_hint,
            next_action="report",
        )
