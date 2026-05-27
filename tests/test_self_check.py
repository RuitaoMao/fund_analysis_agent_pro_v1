from src.agent.schemas import AgentPlan, ToolResult
from src.agent.self_check import SelfCheckAgent


def test_self_check_rejects_fabricated_fund_code():
    plan = AgentPlan(
        intent="fund_size_ranking",
        tool_name="get_top_funds_by_size",
        args={"date": "2026-03-31", "asset_type": "主动权益", "top_n": 1},
    )
    result = ToolResult(
        tool_name="get_top_funds_by_size",
        intent="fund_size_ranking",
        tables={"fund_size_ranking": [{"日期": "2026-03-31", "基金代码": "005827", "基金规模": 267.93}]},
        notes=["日期：2026-03-31", "规模口径来自规模表。"],
    )

    check = SelfCheckAgent().check(
        "1季度末规模最大的主动权益基金是谁",
        plan,
        result,
        "以下为结果：999999 是最大基金。\n\n### 数据口径说明\n- 日期：2026-03-31",
    )

    assert not check.passed
    assert any("工具结果之外" in issue for issue in check.issues)


def test_self_check_accepts_basic_tool_grounded_answer():
    plan = AgentPlan(
        intent="stock_holding_ranking",
        tool_name="get_top_stocks_by_holding",
        args={"date": "2026-03-31", "top_n": 1},
    )
    result = ToolResult(
        tool_name="get_top_stocks_by_holding",
        intent="stock_holding_ranking",
        tables={"stock_holding_ranking": [{"日期": "2026-03-31", "股票代码": "600519", "股票名称": "贵州茅台"}]},
        notes=["日期：2026-03-31", "股票持仓按代码聚合。"],
    )

    check = SelfCheckAgent().check(
        "1季度末全市场持仓规模最大的股票是哪只",
        plan,
        result,
        "以下为股票持仓规模排名：\n\n| 日期 | 股票代码 | 股票名称 |\n| --- | --- | --- |\n| 2026-03-31 | 600519 | 贵州茅台 |\n\n### 数据口径说明\n- 日期：2026-03-31",
    )

    assert check.passed
