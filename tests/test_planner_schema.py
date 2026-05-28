"""Planner 计划测试。

注意：rule-based _mock_plan 内部仍使用旧工具名（教学保留），
但 PlannerAgent.plan() 出口会通过 _heal_tool_name 自愈到当前 9 工具白名单。
因此测试断言新工具名（healed 后）。
"""

from src.agent.planner import PlannerAgent


def test_mock_planner_size_query():
    plan = PlannerAgent().plan("1季度末规模最大的10只主动权益基金是谁", mode="mock")
    # get_top_funds_by_size → query_fund_size
    assert plan.tool_name == "query_fund_size"
    assert plan.intent == "fund_size_ranking"
    assert plan.args["asset_type"] == "主动权益"
    assert plan.args["top_n"] == 10


def test_mock_planner_resolves_follow_up_funds():
    plan = PlannerAgent().plan(
        "这些基金主要持有哪些股票",
        mode="mock",
        memory_context={"last_fund_codes": ["005827", "003095"]},
    )
    # get_top_stocks_by_holding → query_stock_holders
    assert plan.tool_name == "query_stock_holders"
    assert plan.args["fund_codes"] == ["005827", "003095"]


def test_mock_planner_unknown_query_clarifies():
    plan = PlannerAgent().plan("帮我看看这个怎么样", mode="mock")
    assert plan.need_clarification
    assert plan.answer_type == "clarification"


def test_mock_planner_company_total_size_query():
    plan = PlannerAgent().plan("易方达基金总规模是多少，给出你的计算过程，用到了哪几个文件", mode="mock")
    # get_company_total_size → query_company_size
    assert plan.tool_name == "query_company_size"
    assert plan.intent == "company_total_size"
    assert plan.args["companies"] == ["易方达"]


def test_mock_planner_company_fund_list_query():
    plan = PlannerAgent().plan("列出易方达旗下规模最大的5只基金", mode="mock")
    # list_company_funds_by_size → query_fund_size
    assert plan.tool_name == "query_fund_size"
    assert plan.intent == "company_fund_list"
    assert plan.args["fund_company"] == "易方达"
    assert plan.args["top_n"] == 5


def test_mock_planner_company_size_trend_query():
    plan = PlannerAgent().plan("易方达近几个季度总规模怎么变化", mode="mock")
    # get_company_size_trend → query_company_size + include_trend=True (patch)
    assert plan.tool_name == "query_company_size"
    assert plan.intent == "company_size_trend"
    assert plan.args.get("include_trend") is True


def test_mock_planner_fund_holding_detail_query():
    plan = PlannerAgent().plan("005827具体持有哪些股票", mode="mock")
    # get_fund_holdings_detail → query_fund_holdings
    assert plan.tool_name == "query_fund_holdings"
    assert plan.intent == "fund_holding_detail"
    assert plan.args["fund_codes"] == ["005827"]


def test_mock_planner_stock_holder_query():
    plan = PlannerAgent().plan("哪些基金持有宁德时代", mode="mock")
    # find_funds_holding_stock → query_stock_holders + group_by=fund (patch)
    assert plan.tool_name == "query_stock_holders"
    assert plan.intent == "stock_holder_funds"
    assert plan.args["stock_keyword"] == "宁德时代"
    assert plan.args.get("group_by") == "fund"


# ──────────────────────────────────────────────────────────────────
# 新增：自愈机制本身的回归测试
# ──────────────────────────────────────────────────────────────────

def test_heal_tool_name_maps_legacy_to_new():
    """_heal_tool_name 应把旧工具名映射到当前 9 工具之一。"""
    from src.agent.planner import _heal_tool_name, _VALID_TOOLS

    new_name, args = _heal_tool_name("compare_company_business_structure", {"companies": ["易方达"]})
    assert new_name == "query_company_size"
    assert new_name in _VALID_TOOLS
    assert args["companies"] == ["易方达"]


def test_heal_tool_name_preserves_existing_args():
    """补丁不应覆盖 LLM/用户显式给的参数。"""
    from src.agent.planner import _heal_tool_name

    new_name, args = _heal_tool_name(
        "get_company_size_trend",
        {"companies": ["华夏"], "include_trend": False},  # 用户明确传 False
    )
    assert new_name == "query_company_size"
    assert args["include_trend"] is False  # 用户值优先


def test_heal_tool_name_keeps_valid_tools_unchanged():
    from src.agent.planner import _heal_tool_name

    new_name, args = _heal_tool_name("query_fund_size", {"top_n": 10})
    assert new_name == "query_fund_size"
    assert args == {"top_n": 10}


def test_sanitize_memory_drops_stale_tool_name():
    from src.agent.planner import _sanitize_memory_context

    dirty = {
        "last_query": "x",
        "last_tool_name": "compare_company_business_structure",  # legacy
        "last_args": {"companies": ["易方达"]},
        "last_intent": "company_structure_comparison",
        "last_companies": ["易方达"],
    }
    cleaned = _sanitize_memory_context(dirty)
    assert "last_tool_name" not in cleaned
    assert "last_args" not in cleaned
    assert "last_intent" not in cleaned
    # 实体上下文应保留
    assert cleaned["last_companies"] == ["易方达"]
    assert cleaned["last_query"] == "x"


def test_sanitize_memory_keeps_valid_tool_name():
    from src.agent.planner import _sanitize_memory_context

    clean_input = {"last_tool_name": "query_company_size", "last_args": {}, "last_intent": "x"}
    cleaned = _sanitize_memory_context(clean_input)
    assert cleaned["last_tool_name"] == "query_company_size"
    assert "last_args" in cleaned
