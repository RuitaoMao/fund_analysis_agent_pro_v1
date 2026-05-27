"""Plan Validator。

Pydantic 只能检查结构，PlanValidator 负责进一步检查业务合理性。
"""

from __future__ import annotations

from typing import Any

import re

from src.agent.schemas import AgentPlan, ToolCall, ValidationResult
from src.data.sqlite_store import SQLiteStore
from src.tools.registry import ToolRegistry


def _friendly_schema_error(raw: str) -> str:
    """把 pydantic 的技术报错转成用户/日志友好的短语。"""
    if "input_value=None" in raw and "int_type" in raw:
        field_m = re.search(r"\n(\w+)\n", raw)
        field = field_m.group(1) if field_m else "某字段"
        return f"参数 {field} 不能为空，请提供一个正整数（如 10）。"
    first_line = raw.split("\n")[0]
    return first_line[:120]


class PlanValidator:
    """校验 Planner 输出的计划是否可执行、是否明显不合理。"""

    def __init__(self, registry: ToolRegistry, store: SQLiteStore | None = None):
        self.registry = registry
        self.store = store

    def validate(self, query: str, plan: AgentPlan) -> ValidationResult:
        issues: list[str] = []
        warnings: list[str] = []
        correction_hint: str | None = None
        tool_calls = plan.tool_calls or [ToolCall(tool_name=plan.tool_name, args=dict(plan.args))]
        repaired_tool_calls = [
            ToolCall(tool_name=call.tool_name, args=dict(call.args), step_id=call.step_id)
            for call in tool_calls
        ]
        repaired_args = dict(repaired_tool_calls[0].args) if repaired_tool_calls else dict(plan.args)

        if plan.need_clarification:
            return ValidationResult(
                passed=True,
                warnings=["Planner 判断需要追问用户。"],
                repaired_args=repaired_args,
                repaired_tool_calls=repaired_tool_calls,
                next_action="clarify",
            )

        # tool_name == "none" 是硬性失败；intent == "unknown" 不是失败，
        # 只要 tool_name 是注册表中的合法工具就继续执行。
        if plan.tool_name == "none":
            return ValidationResult(
                passed=False,
                issues=["Planner 未能选择可执行工具。"],
                next_action="clarify",
            )

        for i, call in enumerate(repaired_tool_calls):
            if not self.registry.exists(call.tool_name):
                issues.append(f"tool_name 不在注册表中：{call.tool_name}")
                continue
            # top_n 修复必须在 pydantic validate_args 之前
            self._repair_top_n(call.args, warnings)
            self._validate_args_by_spec(call.tool_name, call.args, issues, warnings)
            if not self._contains_step_ref(call.args):
                try:
                    call.args = self.registry.validate_args(call.tool_name, call.args)
                except Exception as exc:
                    friendly = _friendly_schema_error(str(exc))
                    issues.append(f"{call.tool_name} 参数不符合 schema：{friendly}")
                    continue
            if self.store:
                self._repair_and_validate_with_db(call.tool_name, call.args, issues, warnings)
        repaired_args = dict(repaired_tool_calls[0].args) if repaired_tool_calls else repaired_args

        if issues:
            return ValidationResult(
                passed=False,
                issues=issues,
                warnings=warnings,
                correction_hint=correction_hint,
                repaired_args=repaired_args,
                repaired_tool_calls=repaired_tool_calls,
                next_action="replan" if correction_hint else "clarify",
            )
        return ValidationResult(
            passed=True,
            warnings=warnings,
            correction_hint=correction_hint,
            repaired_args=repaired_args,
            repaired_tool_calls=repaired_tool_calls,
            next_action="execute",
        )

    @staticmethod
    def _repair_top_n(args: dict[str, Any], warnings: list[str]) -> None:
        if "top_n" in args:
            try:
                args["top_n"] = int(args["top_n"])
            except Exception:
                args["top_n"] = 10
                warnings.append("top_n 无法解析，已修复为 10。")
            if args["top_n"] <= 0:
                args["top_n"] = 10
                warnings.append("top_n 小于等于 0，已修复为 10。")
            if args["top_n"] > 50:
                args["top_n"] = 50
                warnings.append("top_n 过大，已限制为 50。")

    def _validate_args_by_spec(
        self,
        tool_name: str,
        args: dict[str, Any],
        issues: list[str],
        warnings: list[str],
    ) -> None:
        """按工具定义做轻量参数校验。"""
        spec = self.registry.get_spec(tool_name)
        if not spec:
            issues.append(f"缺少工具参数规范：{tool_name}")
            return

        # 新工具集中只有 lookup_fund 的 keyword 是真正必填的核心参数
        core_required: dict[str, list[str]] = {
            "lookup_fund": ["keyword"],
        }
        for key in core_required.get(tool_name, []):
            if not args.get(key):
                issues.append(f"缺少必需参数：{key}")

        # companies / fund_codes 列表类型修复
        for list_param in ("companies", "fund_codes"):
            if list_param in args and args[list_param] is not None:
                if isinstance(args[list_param], dict) and "$from_step" in args[list_param]:
                    continue
                if not isinstance(args[list_param], list):
                    args[list_param] = [str(args[list_param])]
                    warnings.append(f"{list_param} 不是列表，已修复为单元素列表。")

        # fund_codes 规范化
        if "fund_codes" in args and args["fund_codes"] is not None:
            if not (isinstance(args["fund_codes"], dict) and "$from_step" in args["fund_codes"]):
                args["fund_codes"] = [str(code).split(".")[0].zfill(6) for code in args["fund_codes"]]

        # group_by 合法值检查（query_stock_holders）
        if tool_name == "query_stock_holders" and args.get("group_by") not in (None, "fund", "company", "concentration"):
            warnings.append(f"query_stock_holders.group_by 值 {args['group_by']} 未识别，已重置为 fund。")
            args["group_by"] = "fund"

    @classmethod
    def _contains_step_ref(cls, value: Any) -> bool:
        if isinstance(value, dict):
            if "$from_step" in value:
                return True
            return any(cls._contains_step_ref(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._contains_step_ref(item) for item in value)
        return False

    def _repair_and_validate_with_db(
        self,
        tool_name: str,
        args: dict[str, Any],
        issues: list[str],
        warnings: list[str],
    ) -> None:
        """基于 SQLite 中真实存在的数据口径修复和校验参数。"""
        assert self.store is not None

        # 持仓类工具用 fund_holding 日期，其余用 fund_size 日期
        holding_tools = {
            "query_fund_holdings",
            "query_stock_holders",
            "screen_funds",
            "query_performance_holdings",
        }
        # 确定需要修复的日期参数名
        date_params: list[tuple[str, str]] = []
        if tool_name in holding_tools:
            date_params.append(("date", "fund_holding"))
            if tool_name in ("screen_funds", "query_performance_holdings"):
                date_params.append(("holding_date", "fund_holding"))
        else:
            date_params.append(("date", "fund_size"))

        for date_arg_name, date_table in date_params:
            if date_arg_name in args:
                if args.get(date_arg_name) is None:
                    args[date_arg_name] = self.store.max_date(date_table)
                    warnings.append(f"{date_arg_name} 未指定，已使用最新日期 {args[date_arg_name]}。")
                elif not self.store.date_exists(date_table, str(args[date_arg_name])):
                    latest = self.store.max_date(date_table)
                    warnings.append(f"{date_arg_name}={args[date_arg_name]} 不在数据库中，已改用最新日期 {latest}。")
                    args[date_arg_name] = latest

        asset_type = args.get("asset_type")
        if asset_type:
            valid_asset_types = set(self.store.distinct_values("fund_size", "asset_type"))
            if asset_type not in valid_asset_types:
                issues.append(f"asset_type 不存在：{asset_type}；可用值：{sorted(valid_asset_types)}")

        companies = args.get("companies")
        if companies:
            matched, missing = self.store.resolve_company_names(companies)
            if missing:
                issues.append(f"以下基金公司无法匹配：{missing}")
            args["companies"] = matched

        fund_company = args.get("fund_company")
        if fund_company:
            matched, missing = self.store.resolve_company_names([fund_company])
            if missing:
                issues.append(f"基金公司无法匹配：{fund_company}")
            elif matched:
                args["fund_company"] = matched[0]
