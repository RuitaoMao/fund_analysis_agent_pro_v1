"""Tool Router：把问题和计划映射到工具能力簇。"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.schemas import AgentPlan


@dataclass(frozen=True)
class ToolCategory:
    name: str
    description: str
    tools: tuple[str, ...]


TOOL_CATEGORIES: dict[str, ToolCategory] = {
    "size": ToolCategory(
        name="size",
        description="基金规模查询与基金公司规模分析（含历史趋势、资产类型/Wind 分类拆分）。",
        tools=("query_fund_size", "query_company_size"),
    ),
    "performance": ToolCategory(
        name="performance",
        description="基金业绩排名、明细、对比及公司均值排名。",
        tools=("query_fund_performance",),
    ),
    "holding": ToolCategory(
        name="holding",
        description="基金/公司持仓查询、个股持有情况、共识股识别。",
        tools=("query_fund_holdings", "query_stock_holders"),
    ),
    "cross_analysis": ToolCategory(
        name="cross_analysis",
        description="多条件筛选（规模+业绩+持仓联合）以及业绩Top基金的持仓分析。",
        tools=("screen_funds", "query_performance_holdings"),
    ),
    "lookup": ToolCategory(
        name="lookup",
        description="基金基础信息检索。",
        tools=("lookup_fund",),
    ),
}


class ToolRouter:
    """根据 query 和 planner 结果选择工具类别。"""

    def route(self, query: str, plan: AgentPlan) -> dict:
        planned_tools = [call.tool_name for call in plan.tool_calls] or [plan.tool_name]
        categories = [
            name
            for name, category in TOOL_CATEGORIES.items()
            if any(tool in category.tools for tool in planned_tools)
        ]
        if not categories:
            categories = [self._guess_category(query)]
        allowed_tools = sorted({tool for name in categories for tool in TOOL_CATEGORIES[name].tools})
        return {
            "categories": categories,
            "allowed_tools": allowed_tools,
            "planned_tools": planned_tools,
            "description": "；".join(TOOL_CATEGORIES[name].description for name in categories),
        }

    @staticmethod
    def _guess_category(query: str) -> str:
        if any(w in query for w in ["持仓", "重仓", "股票", "共识"]):
            return "holding"
        if any(w in query for w in ["收益", "业绩", "回撤", "超额"]):
            return "performance"
        if any(w in query for w in ["筛选", "找出", "同时满足"]):
            return "cross_analysis"
        if any(w in query for w in ["规模", "份额", "资产类型", "Wind"]):
            return "size"
        return "lookup"


def render_tool_categories_for_prompt() -> str:
    blocks = []
    for category in TOOL_CATEGORIES.values():
        blocks.append(
            f"类别：{category.name}\n说明：{category.description}\n工具：{', '.join(category.tools)}"
        )
    return "\n\n".join(blocks)
