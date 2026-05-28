"""分析报告写作器测试。

覆盖技能选择、Outliner LLM 调用、Drafter LLM 调用、失败回退、市场快照上下文。
不依赖 tmp_path_factory（避免 Windows 临时目录权限问题）。
"""

from __future__ import annotations

import json

import pytest

from src.agent.report_skills import (
    CompanyComparisonSkill,
    CompanyProfileSkill,
    CompetitiveLandscapeSkill,
    GenericRankingSkill,
    HoldingsAnalysisSkill,
    PerformanceAnalysisSkill,
    ScreeningResultSkill,
    SimpleLookupSkill,
    StockHolderSkill,
    select_skill,
    _SKILLS,
)
from src.agent.report_writer import ReportWriterAgent
from src.agent.schemas import QueryProfile, ReportOutline, ReportSection, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# 技能选择
# ──────────────────────────────────────────────────────────────────────────

def _tr(tool_name: str, tables: dict, metadata: dict | None = None) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        intent=tool_name,
        tables=tables,
        notes=[],
        metadata=metadata or {},
    )


@pytest.mark.parametrize(
    "query, tool_name, tables, metadata, expected_skill",
    [
        ("一季度公募基金规模竞争格局", "query_market_overview",
            {"market_total": [{"全市场总规模_亿": 30000}]}, None, "competitive_landscape"),
        ("对比易方达和华夏", "query_company_size",
            {"company_total": [{"基金公司": "易方达"}, {"基金公司": "华夏"}], "company_breakdown": []},
            {"companies": ["易方达", "华夏"]}, "company_comparison"),
        ("分析易方达", "query_company_size",
            {"company_total": [{"基金公司": "易方达"}], "company_breakdown": []},
            {"companies": ["易方达"]}, "company_profile"),
        ("今年收益最高的基金", "query_fund_performance",
            {"performance_ranking": [{"基金名称": "A", "组合收益率_pct": 20.5}]}, None, "performance_analysis"),
        ("易方达重仓股", "query_fund_holdings",
            {"fund_holdings_detail": [{"股票名称": "贵州茅台"}]}, None, "holdings_analysis"),
        ("公募共识股", "query_stock_holders",
            {"stock_concentration": []}, {"group_by": "concentration"}, "stock_holder_analysis"),
        ("规模>50亿且收益>10%的基金", "screen_funds",
            {"screened_funds": [{"基金名称": "A"}]}, None, "screening_result"),
        ("005827是什么基金", "lookup_fund",
            {"lookup_result": [{"基金代码": "005827", "基金名称": "X"}]}, None, "simple_lookup"),
    ],
)
def test_select_skill_picks_correct_type(query, tool_name, tables, metadata, expected_skill):
    skill = select_skill(query, _tr(tool_name, tables, metadata))
    assert skill.skill_type == expected_skill


def test_select_skill_generic_fallback():
    # 不匹配任何专门技能的工具结果
    result = _tr("unknown_tool", {"some_table": [{"col": 1}]})
    skill = select_skill("某个奇怪的问题", result)
    assert skill.skill_type == "generic"


def test_all_skills_provide_query_profile():
    result = _tr("any", {"t": [{"k": "v"}]}, {"companies": ["A"], "stock_keyword": "贵州茅台"})
    for skill in _SKILLS:
        profile = skill.query_profile("q", result)
        assert isinstance(profile, QueryProfile)
        assert profile.question_type == skill.skill_type
        assert profile.complexity in {"simple", "moderate", "complex"}


# ──────────────────────────────────────────────────────────────────────────
# Mock 模式（不调用 LLM）
# ──────────────────────────────────────────────────────────────────────────

def test_mock_report_renders_outline_and_tables():
    writer = ReportWriterAgent()
    result = _tr("query_market_overview",
        {"market_total": [{"全市场总规模_亿": 30000}]}, None)
    report = writer.write(query="竞争格局", tool_result=result, mode="mock")
    assert "市场总体规模" in report  # competitive_landscape 技能的第一节
    assert "30000" in report or "30,000" in report
    assert writer.last_skill_type == "competitive_landscape"
    assert writer.last_outline_source == "skill"


def test_mock_report_handles_none_tool_result():
    writer = ReportWriterAgent()
    report = writer.write(query="x", tool_result=None, mode="mock")
    assert "未能" in report or "无法" in report
    assert writer.last_skill_type is None


def test_simple_lookup_skill_produces_direct_answer():
    writer = ReportWriterAgent()
    result = _tr("lookup_fund",
        {"lookup_result": [{"基金代码": "005827", "基金名称": "易方达蓝筹精选",
                            "基金公司": "易方达", "最新规模_亿": 500}]})
    report = writer.write(query="005827是什么基金", tool_result=result, mode="mock")
    assert "**直接回答" in report or "直接回答" in report
    assert "005827" in report


# ──────────────────────────────────────────────────────────────────────────
# LLM 两阶段流水线
# ──────────────────────────────────────────────────────────────────────────

class _FakeLLM:
    """记录调用并返回预设响应的假 LLM。"""

    def __init__(self, outliner_response: str, drafter_response: str = "# 测试报告\n\n内容"):
        self.outliner_response = outliner_response
        self.drafter_response = drafter_response
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if "大纲设计师" in kwargs["system_prompt"]:
            return self.outliner_response
        return self.drafter_response


def test_llm_pipeline_invokes_outliner_then_drafter():
    outliner_json = json.dumps({
        "skill_type": "competitive_landscape",
        "direct_answer": None,
        "sections": [
            {"title": "市场总览", "analytical_angles": ["总规模", "公司数"]},
            {"title": "头部集中度", "analytical_angles": ["前5份额"]},
        ],
    }, ensure_ascii=False)
    fake = _FakeLLM(outliner_response=outliner_json)
    # outliner_enabled=True 显式开启第一阶段；默认 False 是为了 demo 速度
    writer = ReportWriterAgent(llm_client=fake, outliner_enabled=True)
    result = _tr("query_market_overview", {"market_total": [{"全市场总规模_亿": 30000}]})

    report = writer.write(query="竞争格局", tool_result=result, mode="llm")

    assert len(fake.calls) == 2
    assert "大纲设计师" in fake.calls[0]["system_prompt"]
    assert "撰写专家" in fake.calls[1]["system_prompt"]
    assert writer.last_outline_source == "llm_outliner"
    assert "测试报告" in report


def test_llm_pipeline_falls_back_when_outliner_returns_bad_json():
    fake = _FakeLLM(outliner_response="not valid json {{{{")
    writer = ReportWriterAgent(llm_client=fake, outliner_enabled=True)
    result = _tr("query_market_overview", {"market_total": [{"全市场总规模_亿": 30000}]})

    writer.write(query="竞争格局", tool_result=result, mode="llm")

    # 应回退到技能大纲，但 Drafter 仍正常调用
    assert writer.last_outline_source == "skill_fallback"
    assert len(fake.calls) == 2  # 一次 outliner 失败 + 一次 drafter 仍然调用


def test_llm_pipeline_falls_back_when_outliner_returns_empty_sections():
    outliner_json = json.dumps({"skill_type": "x", "direct_answer": None, "sections": []})
    fake = _FakeLLM(outliner_response=outliner_json)
    writer = ReportWriterAgent(llm_client=fake, outliner_enabled=True)
    result = _tr("query_fund_performance", {"performance_ranking": [{"基金名称": "A"}]})

    writer.write(query="收益最高的基金", tool_result=result, mode="llm")
    assert writer.last_outline_source == "skill_fallback"


def test_outliner_disabled_by_default_only_one_llm_call():
    """默认 outliner_enabled=False：只调用 1 次 Drafter，省一半时间。"""
    fake = _FakeLLM(outliner_response="should not be called")
    writer = ReportWriterAgent(llm_client=fake)  # 默认 outliner_enabled=False
    result = _tr("query_market_overview", {"market_total": [{"全市场总规模_亿": 30000}]})

    writer.write(query="竞争格局", tool_result=result, mode="llm")

    assert len(fake.calls) == 1, f"Expected 1 LLM call (Drafter only), got {len(fake.calls)}"
    assert "撰写专家" in fake.calls[0]["system_prompt"]
    assert writer.last_outline_source == "skill"


def test_mock_mode_does_not_call_llm():
    fake = _FakeLLM(outliner_response="never called")
    writer = ReportWriterAgent(llm_client=fake)
    result = _tr("lookup_fund", {"lookup_result": [{"基金代码": "005827"}]})

    writer.write(query="x", tool_result=result, mode="mock")
    assert fake.calls == []
