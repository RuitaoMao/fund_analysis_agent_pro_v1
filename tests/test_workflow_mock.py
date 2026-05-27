from src.agent.app import FundAnalysisAgent


def test_workflow_mock_smoke(temp_settings):
    agent = FundAnalysisAgent(temp_settings)
    state = agent.run("1季度末规模最大的10只主动权益基金是谁", mode="mock", session_id="pytest")
    assert "final_answer" in state
    assert "基金规模" in state["final_answer"]
    assert state["tool_history"]
    assert any(step.node == "tool_router_node" for step in state["trace"])
    assert state["tool_route"]["categories"]
    assert any(step.node == "reflect_node" for step in state["trace"])


def test_workflow_mock_multi_turn(temp_settings):
    agent = FundAnalysisAgent(temp_settings)
    session_id = "pytest-multi-turn"

    first = agent.run("1季度末规模最大的10只主动权益基金是谁", mode="mock", session_id=session_id)
    assert first["final_answer"]

    second = agent.run("这些基金主要持有哪些股票", mode="mock", session_id=session_id)
    assert "股票持仓规模排名" in second["final_answer"]
    assert second["tool_result"].metadata["fund_codes"]


def test_workflow_mock_ambiguous_query(temp_settings):
    agent = FundAnalysisAgent(temp_settings)
    state = agent.run("帮我看看这个怎么样", mode="mock", session_id="pytest-ambiguous")
    assert "补充" in state["final_answer"] or "不能确定" in state["final_answer"]


def test_workflow_mock_multi_tool(temp_settings):
    agent = FundAnalysisAgent(temp_settings)
    state = agent.run(
        "分析易方达近几个季度规模变化，并列出最新规模最大的5只基金",
        mode="mock",
        session_id="pytest-multi-tool",
    )
    assert state["tool_result"].tool_name == "multi_tool"
    assert len(state["tool_history"]) == 2
    assert "company_size_trend" in state["tool_result"].tables
    assert "company_funds" in state["tool_result"].tables
    assert state["run_artifacts_dir"]


def test_workflow_mock_chained_multi_tool(temp_settings):
    agent = FundAnalysisAgent(temp_settings)
    state = agent.run(
        "筛选1季度收益率前5基金并分析持仓集中度",
        mode="mock",
        session_id="pytest-chained-tool",
    )
    assert state["tool_result"].tool_name == "multi_tool"
    assert "top_performance_funds" in state["tool_result"].tables
    assert "fund_holding_concentration" in state["tool_result"].tables
    assert state["tool_result"].tables["fund_holding_concentration"]


def test_workflow_memory_turns(temp_settings):
    agent = FundAnalysisAgent(temp_settings)
    session_id = "pytest-turns"
    agent.run("易方达基金总规模是多少", mode="mock", session_id=session_id)
    agent.run("易方达近几个季度总规模怎么变化", mode="mock", session_id=session_id)
    context = agent.workflow.memory.load(session_id)
    assert len(context["recent_turns"]) >= 2
