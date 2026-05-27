"""Tool Executor。

Executor 的职责是根据通过校验的 plan 调用白名单 tool。
它不理解自然语言，也不写报告。
"""

from __future__ import annotations

from typing import Any

from src.agent.schemas import AgentPlan, ToolCall, ToolResult
from src.data.sqlite_store import SQLiteStore
from src.tools.registry import ToolRegistry


class ToolExecutorAgent:
    """工具执行子智能体。"""

    def __init__(self, registry: ToolRegistry, store: SQLiteStore):
        self.registry = registry
        self.store = store

    def execute(
        self,
        plan: AgentPlan,
        repaired_args: dict | None = None,
        repaired_tool_calls: list[ToolCall] | None = None,
    ) -> ToolResult:
        args = repaired_args or plan.args
        tool_calls = repaired_tool_calls or plan.tool_calls
        if not tool_calls:
            return self.registry.execute(plan.tool_name, self.store, args)
        if len(tool_calls) == 1:
            call = tool_calls[0]
            return self.registry.execute(call.tool_name, self.store, call.args)

        results: list[ToolResult] = []
        step_results: dict[str, ToolResult] = {}
        for index, call in enumerate(tool_calls):
            resolved_args = self._resolve_arg_refs(call.args, step_results)
            result = self.registry.execute(call.tool_name, self.store, resolved_args)
            results.append(result)
            step_id = call.step_id or f"step_{index + 1}"
            step_results[step_id] = result
        return self._merge_results(plan, tool_calls, results)

    @staticmethod
    def _merge_results(plan: AgentPlan, tool_calls: list[ToolCall], results: list[ToolResult]) -> ToolResult:
        """把多工具结果合并成一个 ToolResult，供后续 validator/report 统一处理。"""
        tables: dict[str, list[dict]] = {}
        notes: list[str] = []
        warnings: list[str] = []
        metadata = {"tool_calls": [call.model_dump() for call in tool_calls], "tool_results": []}

        for result in results:
            metadata["tool_results"].append(result.model_dump())
            notes.append(f"[{result.tool_name}]")
            notes.extend(result.notes)
            warnings.extend(result.warnings)
            for table_name, rows in result.tables.items():
                key = table_name
                if key in tables:
                    key = f"{result.tool_name}.{table_name}"
                tables[key] = rows

        return ToolResult(
            tool_name="multi_tool",
            intent=plan.intent,
            tables=tables,
            notes=notes,
            warnings=list(dict.fromkeys(warnings)),
            metadata=metadata,
        )

    @classmethod
    def _resolve_arg_refs(cls, value: Any, step_results: dict[str, ToolResult]) -> Any:
        """解析后续工具参数中的上一步结果引用。

        引用格式：
        {"$from_step": "top_perf", "table": "top_performance_funds", "column": "基金代码", "limit": 10}
        """
        if isinstance(value, dict):
            if "$from_step" in value:
                step_id = value["$from_step"]
                result = step_results.get(step_id)
                if result is None:
                    raise ValueError(f"无法解析工具参数引用，未知 step_id：{step_id}")
                table_name = value.get("table")
                column = value.get("column")
                limit = value.get("limit")
                rows = result.tables.get(table_name, []) if table_name else next(iter(result.tables.values()), [])
                values = [row.get(column) for row in rows if column in row]
                values = [item for item in values if item is not None]
                return values[: int(limit)] if limit else values
            return {key: cls._resolve_arg_refs(item, step_results) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve_arg_refs(item, step_results) for item in value]
        return value
