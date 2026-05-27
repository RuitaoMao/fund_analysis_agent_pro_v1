from src.agent.planner import PlannerAgent


def test_mock_planner_size_query():
    plan = PlannerAgent().plan("1季度末规模最大的10只主动权益基金是谁", mode="mock")
    assert plan.tool_name == "get_top_funds_by_size"
    assert plan.intent == "fund_size_ranking"
    assert plan.args["asset_type"] == "主动权益"
    assert plan.args["top_n"] == 10


def test_mock_planner_resolves_follow_up_funds():
    plan = PlannerAgent().plan(
        "这些基金主要持有哪些股票",
        mode="mock",
        memory_context={"last_fund_codes": ["005827", "003095"]},
    )
    assert plan.tool_name == "get_top_stocks_by_holding"
    assert plan.args["fund_codes"] == ["005827", "003095"]


def test_mock_planner_unknown_query_clarifies():
    plan = PlannerAgent().plan("帮我看看这个怎么样", mode="mock")
    assert plan.need_clarification
    assert plan.answer_type == "clarification"


def test_mock_planner_company_total_size_query():
    plan = PlannerAgent().plan("易方达基金总规模是多少，给出你的计算过程，用到了哪几个文件", mode="mock")
    assert plan.tool_name == "get_company_total_size"
    assert plan.intent == "company_total_size"
    assert plan.args["companies"] == ["易方达"]


def test_mock_planner_company_fund_list_query():
    plan = PlannerAgent().plan("列出易方达旗下规模最大的5只基金", mode="mock")
    assert plan.tool_name == "list_company_funds_by_size"
    assert plan.intent == "company_fund_list"
    assert plan.args["fund_company"] == "易方达"
    assert plan.args["top_n"] == 5


def test_mock_planner_company_size_trend_query():
    plan = PlannerAgent().plan("易方达近几个季度总规模怎么变化", mode="mock")
    assert plan.tool_name == "get_company_size_trend"
    assert plan.intent == "company_size_trend"


def test_mock_planner_fund_holding_detail_query():
    plan = PlannerAgent().plan("005827具体持有哪些股票", mode="mock")
    assert plan.tool_name == "get_fund_holdings_detail"
    assert plan.intent == "fund_holding_detail"
    assert plan.args["fund_codes"] == ["005827"]


def test_mock_planner_stock_holder_query():
    plan = PlannerAgent().plan("哪些基金持有宁德时代", mode="mock")
    assert plan.tool_name == "find_funds_holding_stock"
    assert plan.intent == "stock_holder_funds"
    assert plan.args["stock_keyword"] == "宁德时代"
