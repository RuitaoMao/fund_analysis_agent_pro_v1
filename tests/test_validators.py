from src.agent.plan_validator import PlanValidator
from src.agent.result_validator import ResultValidator
from src.agent.schemas import AgentPlan, ToolResult
from src.tools import build_default_registry


def test_plan_validator_repairs_date_and_checks_company(temp_store):
    validator = PlanValidator(build_default_registry(), temp_store)
    plan = AgentPlan(
        intent="company_structure_comparison",
        tool_name="compare_company_business_structure",
        args={"companies": ["易方达", "华夏"], "date": None, "asset_type": None},
        answer_type="report",
    )

    result = validator.validate("对比分析易方达和华夏基金的业务结构", plan)

    assert result.passed
    assert result.repaired_args["date"] == "2026-03-31"
    assert result.next_action == "execute"


def test_plan_validator_detects_semantic_conflict(temp_store):
    validator = PlanValidator(build_default_registry(), temp_store)
    plan = AgentPlan(
        intent="fund_size_ranking",
        tool_name="get_top_funds_by_size",
        args={"date": None, "asset_type": None, "fund_company": None, "top_n": 10},
    )

    result = validator.validate("这些基金主要持有哪些股票", plan)

    assert not result.passed
    assert result.next_action == "replan"
    assert result.correction_hint


def test_result_validator_empty_and_missing_codes():
    plan = AgentPlan(
        intent="performance_holding_analysis",
        tool_name="analyze_top_performance_holdings",
        args={"period": "本年以来", "top_n": 10},
        answer_type="report",
    )
    result = ToolResult(
        tool_name="analyze_top_performance_holdings",
        intent="performance_holding_analysis",
        tables={"top_performance_funds": []},
        metadata={"missing_fund_codes": ["000001"]},
    )

    validation = ResultValidator().validate("筛选1季度收益率前10基金并分析其持仓情况", plan, result)

    assert not validation.passed
    assert validation.next_action in {"replan", "clarify"}
    assert any("未匹配" in warning or "部分基金" in warning for warning in validation.warnings)
