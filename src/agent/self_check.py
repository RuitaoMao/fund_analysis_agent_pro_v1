"""Self-check Agent。

Self-check 检查最终回答是否基本合格。
生产环境中可以使用：规则自检 + LLM 自检 + 人工审核。
这里先实现规则自检，便于学习。
"""

from __future__ import annotations

import re

from src.agent.schemas import AgentPlan, ToolResult, SelfCheckResult


class SelfCheckAgent:
    """最终回答自检。"""

    def check(self, query: str, plan: AgentPlan, tool_result: ToolResult | None, answer: str) -> SelfCheckResult:
        issues: list[str] = []

        if not answer.strip():
            issues.append("最终回答为空。")

        if plan.answer_type == "simple" and not any(
            token in answer for token in ["|", "无数据", "无法", "以下为", "是", "最大", "排名"]
        ):
            issues.append("简单问题没有给出明确结果表或结论。")

        if tool_result and tool_result.tables:
            all_empty = all(len(rows) == 0 for rows in tool_result.tables.values())
            if all_empty and "无数据" not in answer and "为空" not in answer:
                issues.append("工具结果为空，但回答没有说明无数据。")
            # 防编造：回答中出现的 6 位基金代码，应来自工具结果。
            known_fund_codes = {
                str(row.get("基金代码")).zfill(6)
                for rows in tool_result.tables.values()
                for row in rows
                if row.get("基金代码") is not None
            }
            known_stock_codes = {
                str(row.get("股票代码"))
                for rows in tool_result.tables.values()
                for row in rows
                if row.get("股票代码") is not None
            }
            mentioned_codes = set(re.findall(r"(?<!\d)\d{6}(?!\d)", answer))
            fabricated_codes = mentioned_codes - known_fund_codes - known_stock_codes
            if known_fund_codes and fabricated_codes:
                issues.append(f"回答中出现了工具结果之外的基金代码：{sorted(fabricated_codes)}")

        if tool_result and tool_result.notes and "口径" not in answer and "日期" not in answer:
            issues.append("回答可能缺少数据口径说明。")
        if "投资建议" not in answer and any(word in answer for word in ["买入", "卖出", "推荐配置"]):
            issues.append("回答可能缺少非投资建议声明。")

        # 禁止输出投资建议类措辞。
        banned_phrases = ["建议买入", "强烈推荐", "必然上涨", "稳赚"]
        for phrase in banned_phrases:
            if phrase in answer:
                issues.append(f"回答包含不合适的投资建议措辞：{phrase}")

        if ("持仓" in query or "股票" in query) and tool_result and tool_result.tool_name == "get_top_funds_by_size":
            issues.append("用户询问持仓/股票，但最终使用了基金规模工具，可能答非所问。")

        if "1季度" in query and tool_result and "2026-03-31" not in answer:
            issues.append("用户询问1季度口径，但回答没有说明季度末日期。")

        return SelfCheckResult(
            passed=len(issues) == 0,
            issues=issues,
            suggested_fix="请根据自检问题重新生成报告，并补充必要口径说明。" if issues else None,
        )
