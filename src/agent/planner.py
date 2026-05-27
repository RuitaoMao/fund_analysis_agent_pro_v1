"""Planner Agent。

Planner 的职责：把用户自然语言问题转换成 AgentPlan。
它不执行工具，也不生成最终答案。
"""

from __future__ import annotations

import re
from json import JSONDecodeError

from src.agent.schemas import AgentPlan, ToolCall
from src.llm.client import LLMClient
from src.llm.prompts import PLANNER_SYSTEM_PROMPT
from src.utils.json_utils import extract_json_object


class PlannerAgent:
    """自然语言 -> 结构化计划。"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    def plan(
        self,
        query: str,
        mode: str = "mock",
        memory_context: dict | None = None,
        failure_context: dict | None = None,
    ) -> AgentPlan:
        """生成 AgentPlan。

        mock 模式使用规则，方便本地学习和测试。
        llm 模式调用真实 LLM。
        failure_context 为上一轮失败信息，用于 ReAct 闭环重规划。
        """
        if mode == "mock":
            return self._mock_plan(query, memory_context or {})
        return self._llm_plan(query, memory_context or {}, failure_context or {})

    @staticmethod
    def _format_failure_context(failure_context: dict) -> str:
        """把上一轮 plan/result validation 的 issues 和 hint 渲染成文本，注入 prompt。"""
        if not failure_context:
            return ""
        lines = ["", "=== 上轮失败反馈（请基于以下反馈重新规划，避免相同错误）==="]
        tool_history = failure_context.get("tool_history") or []
        if tool_history:
            lines.append("已尝试过的工具调用：")
            for item in tool_history[-3:]:
                lines.append(f"  - {item.get('tool_name')} args={item.get('args')}")
        plan_validation = failure_context.get("plan_validation")
        if plan_validation:
            issues = plan_validation.get("issues") or []
            hint = plan_validation.get("correction_hint")
            if issues:
                lines.append(f"Plan 校验问题：{'; '.join(issues)}")
            if hint:
                lines.append(f"Plan 修正建议：{hint}")
        result_validation = failure_context.get("result_validation")
        if result_validation:
            issues = result_validation.get("issues") or []
            hint = result_validation.get("correction_hint")
            if issues:
                lines.append(f"Result 校验问题：{'; '.join(issues)}")
            if hint:
                lines.append(f"Result 修正建议：{hint}")
        errors = failure_context.get("errors") or []
        if errors:
            lines.append(f"运行错误：{'; '.join(errors[-3:])}")
        lines.append("=== 结束反馈 ===")
        return "\n".join(lines)

    def _llm_plan(self, query: str, memory_context: dict, failure_context: dict) -> AgentPlan:
        if self.llm_client is None:
            raise RuntimeError("LLMClient 未初始化。")
        failure_text = self._format_failure_context(failure_context)
        user_prompt = (
            f"用户问题：{query}\n\n"
            f"可用多轮上下文：{memory_context}\n"
            f"{failure_text}\n\n"
            "请输出 JSON。"
        )
        raw = self.llm_client.chat(
            role="planner",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=True,
            temperature=0.0,
            max_tokens=800,
        )
        try:
            data = extract_json_object(raw)
        except (JSONDecodeError, ValueError) as exc:
            # LLM planner 没有返回结构化 JSON 时，回退到规则 planner。
            # 规则 planner 若也无法匹配，会产出 clarification plan。
            fallback = self._mock_plan(query, memory_context)
            fallback.rationale = f"LLM planner JSON 解析失败，已回退到规则 planner：{exc}"
            return fallback
        data["intent"] = self._normalize_intent(data.get("intent"), data.get("tool_name"))
        if str(data.get("tool_name")) in {"generate_sql_query", "generated_sql_query"}:
            # hard tools 模式必须选择注册工具；LLM 若漂移到生成 SQL，回退到规则 planner。
            return self._mock_plan(query, memory_context)
        return AgentPlan.model_validate(data)

    def _mock_plan(self, query: str, memory_context: dict) -> AgentPlan:
        """规则版 planner。

        这是教学和测试用的 fallback，不代表生产中只靠规则。
        """
        q = query.strip()
        top_n = self._extract_top_n(q) or 10
        company = self._extract_company(q)
        companies = self._extract_companies(q)
        fund_codes_in_query = self._extract_fund_codes(q)
        wind_category, wind_level = self._extract_wind_category(q)

        # 多轮上下文示例：用户说“这些基金”，优先使用上一轮基金代码。
        if "这些基金" in q and ("持仓" in q or "股票" in q):
            fund_codes = memory_context.get("last_fund_codes")
            if not fund_codes:
                return AgentPlan(
                    intent="unknown",
                    tool_name="none",
                    args={},
                    answer_type="clarification",
                    need_clarification=True,
                    clarification_question="请问您说的“这些基金”具体指哪些基金？可以提供基金代码或先查询基金列表。",
                    rationale="用户使用了指代，但 memory 中没有上一轮基金代码。",
                )
            return AgentPlan(
                intent="stock_holding_ranking",
                tool_name="get_top_stocks_by_holding",
                args={"date": None, "fund_codes": fund_codes, "top_n": top_n},
                answer_type="simple",
                rationale="用户追问上一轮基金的持仓，应调用股票持仓排名工具。",
            )

        if ("这些基金" in q or "这几只基金" in q) and any(word in q for word in ["集中度", "前十大", "前10大", "分散"]):
            fund_codes = memory_context.get("last_fund_codes")
            if fund_codes:
                return AgentPlan(
                    intent="fund_holding_concentration",
                    tool_name="analyze_fund_holding_concentration",
                    args={"date": None, "fund_codes": fund_codes, "top_n": top_n},
                    answer_type="report",
                    rationale="用户追问上一轮基金的持仓集中度，应复用 memory 中的基金代码。",
                )

        if companies and any(word in q for word in ["趋势", "变化", "增长", "下降", "近几个季度"]):
            if any(word in q for word in ["列出", "贡献", "最大的", "明细", "前"]):
                return AgentPlan(
                    intent="company_size_trend",
                    tool_name="get_company_size_trend",
                    args={"companies": companies, "asset_type": self._extract_asset_type(q)},
                    tool_calls=[
                        ToolCall(
                            tool_name="get_company_size_trend",
                            args={"companies": companies, "asset_type": self._extract_asset_type(q)},
                        ),
                        ToolCall(
                            tool_name="list_company_funds_by_size",
                            args={
                                "companies": companies,
                                "fund_company": companies[0],
                                "date": None,
                                "asset_type": self._extract_asset_type(q),
                                "top_n": top_n,
                            },
                        ),
                    ],
                    answer_type="report",
                    rationale="用户同时询问公司规模变化和最新规模贡献基金，应一次调用趋势和明细两个工具。",
                )
            return AgentPlan(
                intent="company_size_trend",
                tool_name="get_company_size_trend",
                args={"companies": companies, "asset_type": self._extract_asset_type(q)},
                answer_type="report",
                rationale="用户询问基金公司规模趋势，应按日期汇总公司规模变化。",
            )

        if wind_category and any(word in q for word in ["规模", "排名", "最大", "前"]):
            if any(word in q for word in ["公司", "哪家", "基金公司"]):
                return AgentPlan(
                    intent="company_wind_category_ranking",
                    tool_name="rank_companies_by_wind_category_size",
                    args={"date": None, "wind_level": wind_level, "wind_category": wind_category, "top_n": top_n},
                    answer_type="simple",
                    rationale="用户按 Wind 分类询问基金公司规模排名，应使用 Wind 分类公司排名工具。",
                )
            return AgentPlan(
                intent="wind_category_fund_ranking",
                tool_name="get_top_funds_by_wind_category",
                args={"date": None, "wind_level": wind_level, "wind_category": wind_category, "fund_company": company, "top_n": top_n},
                answer_type="simple",
                rationale="用户按 Wind 分类询问基金规模排名，应使用 Wind 分类基金排名工具。",
            )

        if any(word in q for word in ["wind", "Wind", "分类结构", "分类分布", "一级分类", "二级分类", "三级分类"]):
            return AgentPlan(
                intent="wind_category_distribution",
                tool_name="get_wind_category_size_distribution",
                args={"date": None, "wind_level": wind_level, "fund_company": company},
                answer_type="report",
                rationale="用户询问 Wind 分类结构，应按 wind_level1/2/3 汇总规模。",
            )

        if any(word in q for word in ["资产类型分布", "资产类型结构", "全市场结构"]):
            return AgentPlan(
                intent="asset_type_distribution",
                tool_name="get_asset_type_size_distribution",
                args={"date": None, "fund_company": company},
                answer_type="report",
                rationale="用户询问资产类型分布，应按 asset_type 汇总规模。",
            )

        if "收益率" in q and "持仓" in q:
            if any(word in q for word in ["集中度", "前十大", "前10大", "分散"]):
                return AgentPlan(
                    intent="performance_holding_analysis",
                    tool_name="analyze_top_performance_holdings",
                    args={"period": "本年以来", "top_n": top_n, "holding_date": None, "asset_type": self._extract_asset_type(q)},
                    tool_calls=[
                        ToolCall(
                            step_id="top_perf",
                            tool_name="analyze_top_performance_holdings",
                            args={
                                "period": "本年以来",
                                "top_n": top_n,
                                "holding_date": None,
                                "asset_type": self._extract_asset_type(q),
                            },
                        ),
                        ToolCall(
                            step_id="concentration",
                            tool_name="analyze_fund_holding_concentration",
                            args={
                                "date": None,
                                "fund_codes": {
                                    "$from_step": "top_perf",
                                    "table": "top_performance_funds",
                                    "column": "基金代码",
                                    "limit": top_n,
                                },
                                "top_n": 10,
                            },
                        ),
                    ],
                    answer_type="report",
                    rationale="用户询问收益率前列基金并分析集中度，应先筛选基金再把基金代码传给集中度工具。",
                )
            return AgentPlan(
                intent="performance_holding_analysis",
                tool_name="analyze_top_performance_holdings",
                args={"period": "本年以来", "top_n": top_n, "holding_date": None, "asset_type": self._extract_asset_type(q)},
                answer_type="report",
                rationale="用户询问收益率前列基金并分析持仓，应调用业绩持仓联动工具。",
            )

        if "收益率" in q or "业绩" in q or "回撤" in q or "超额" in q:
            if companies and any(word in q for word in ["公司", "平均", "排名"]):
                return AgentPlan(
                    intent="company_average_return_ranking",
                    tool_name="rank_companies_by_average_return",
                    args={"period": "本年以来", "asset_type": self._extract_asset_type(q), "top_n": top_n},
                    answer_type="report",
                    rationale="用户询问公司维度平均业绩，应按基金公司聚合收益率。",
                )
            if any(word in q for word in ["最低", "最差", "后"]):
                return AgentPlan(
                    intent="performance_bottom_ranking",
                    tool_name="get_bottom_funds_by_performance",
                    args={"period": "本年以来", "top_n": top_n},
                    answer_type="simple",
                    rationale="用户询问业绩靠后基金，应调用收益率后N工具。",
                )
            if any(word in q for word in ["分布", "平均", "整体"]):
                return AgentPlan(
                    intent="performance_distribution",
                    tool_name="analyze_performance_distribution",
                    args={"period": "本年以来", "asset_type": self._extract_asset_type(q)},
                    answer_type="report",
                    rationale="用户询问收益率整体分布，应调用业绩分布工具。",
                )
            return AgentPlan(
                intent="performance_ranking",
                tool_name="get_top_funds_by_performance",
                args={"period": "本年以来", "top_n": top_n},
                answer_type="simple",
                rationale="用户询问基金业绩排名，应调用收益率排名工具。",
            )

        if ("持仓" in q or "重仓" in q) and "股票" in q:
            return AgentPlan(
                intent="stock_holding_ranking",
                tool_name="get_top_stocks_by_holding",
                args={"date": None, "fund_codes": None, "top_n": top_n if "哪只" not in q else 1},
                answer_type="simple",
                rationale="用户询问股票持仓规模排名，应调用股票持仓工具。",
            )

        if fund_codes_in_query and any(word in q for word in ["规模历史", "规模变化", "历史规模", "怎么变", "变化"]):
            return AgentPlan(
                intent="fund_size_history",
                tool_name="get_fund_size_history",
                args={"fund_code": fund_codes_in_query[0], "keyword": None, "top_n": top_n},
                answer_type="report",
                rationale="用户给出基金代码并询问规模变化，应查询该基金规模历史。",
            )

        if fund_codes_in_query and any(word in q for word in ["集中度", "前十大", "前10大", "分散"]):
            return AgentPlan(
                intent="fund_holding_concentration",
                tool_name="analyze_fund_holding_concentration",
                args={"date": None, "fund_codes": fund_codes_in_query, "top_n": top_n},
                answer_type="report",
                rationale="用户询问指定基金前N大持仓集中度，应调用集中度分析工具。",
            )

        if fund_codes_in_query and any(word in q for word in ["持仓明细", "具体持有", "持有哪些股票", "重仓股", "持仓"]):
            return AgentPlan(
                intent="fund_holding_detail",
                tool_name="get_fund_holdings_detail",
                args={"date": None, "fund_codes": fund_codes_in_query, "top_n": top_n},
                answer_type="report",
                rationale="用户给出基金代码并询问持仓明细，应查询该基金持仓表。",
            )

        stock_keyword = self._extract_stock_keyword(q)
        # 公司维度的对比（最高优先级）：A 和 B 谁更看好 X / 对比 A 和 B 对 X 的持仓
        if stock_keyword and len(companies) >= 2 and any(p in q for p in ["谁更", "对比", "比较", "哪家更"]):
            return AgentPlan(
                intent="company_stock_comparison",
                tool_name="compare_companies_stock_holding",
                args={"stock_keyword": stock_keyword, "companies": companies, "date": None},
                answer_type="report",
                rationale="用户对比多家公司对同一股票的持仓，应使用公司持仓对比工具。",
            )
        # 公司明细：单公司 + 股票 + (明细|旗下|具体)
        if stock_keyword and company and any(p in q for p in ["明细", "旗下", "具体", "拆解", "贡献"]):
            return AgentPlan(
                intent="company_stock_breakdown",
                tool_name="get_company_stock_holding_breakdown",
                args={"stock_keyword": stock_keyword, "fund_company": company, "date": None, "top_n": top_n},
                answer_type="report",
                rationale="用户询问某公司持有某股票的明细，应拆解到旗下基金。",
            )
        # 公司持仓某股票的分布/占比
        if stock_keyword and any(p in q for p in ["公司持仓分布", "公司分布", "公司占比", "公司持有结构"]):
            return AgentPlan(
                intent="stock_company_distribution",
                tool_name="get_stock_company_distribution",
                args={"stock_keyword": stock_keyword, "date": None, "top_n": top_n},
                answer_type="report",
                rationale="用户询问某股票在公司间的分布或占比，应汇总公司持仓占比。",
            )
        # 公司维度排名：哪家公司持仓 X 最多
        if stock_keyword and any(c in q for c in ["基金公司", "哪家公司", "哪个公司", "公司最看好", "哪家基金公司"]):
            return AgentPlan(
                intent="company_stock_holding_ranking",
                tool_name="rank_companies_by_stock_holding",
                args={"stock_keyword": stock_keyword, "date": None, "asset_type": self._extract_asset_type(q), "top_n": top_n},
                answer_type="simple",
                rationale="用户询问哪家基金公司持仓某股票最多，应按公司聚合持仓规模。",
            )
        # 共识股识别
        if any(p in q for p in ["共识股", "最一致", "公募共识", "最多基金公司"]) and any(s in q for s in ["持有", "持仓", "重仓"]):
            return AgentPlan(
                intent="stock_company_concentration",
                tool_name="rank_stocks_by_company_concentration",
                args={"date": None, "asset_type": self._extract_asset_type(q), "top_n": top_n},
                answer_type="report",
                rationale="用户询问公募共识股，应按持仓公司数排序股票。",
            )
        # 增强 find_funds_holding_stock：用户问"哪些基金持仓X最多/前N"需要严格排序
        if stock_keyword and any(s in q for s in ["哪些基金", "哪个基金", "基金持仓", "基金持有"]) and any(p in q for p in ["最多", "前", "最大", "规模最高"]):
            return AgentPlan(
                intent="stock_holder_funds_ranked",
                tool_name="rank_funds_holding_stock_by_value",
                args={"stock_keyword": stock_keyword, "date": None, "fund_company": company, "top_n": top_n},
                answer_type="simple",
                rationale="用户询问持仓某股票最多的基金，应按持仓规模严格排序。",
            )
        if stock_keyword and any(word in q for word in ["哪些基金", "被哪些基金", "持有", "重仓"]):
            return AgentPlan(
                intent="stock_holder_funds",
                tool_name="find_funds_holding_stock",
                args={"date": None, "stock_keyword": stock_keyword, "top_n": top_n},
                answer_type="report",
                rationale="用户询问某股票被哪些基金持有，应反查持仓表。",
            )

        if stock_keyword and any(word in q for word in ["趋势", "变化", "增长", "下降"]):
            return AgentPlan(
                intent="stock_holding_trend",
                tool_name="get_stock_holding_trend",
                args={"stock_keyword": stock_keyword},
                answer_type="report",
                rationale="用户询问单只股票的基金持仓趋势，应按日期聚合持仓表。",
            )

        if stock_keyword and any(word in q for word in ["资产类型", "主动权益", "被动权益"]):
            return AgentPlan(
                intent="stock_holding_by_asset_type",
                tool_name="get_stock_holding_by_asset_type",
                args={"date": None, "stock_keyword": stock_keyword},
                answer_type="report",
                rationale="用户询问股票被不同资产类型基金持有情况，应按资产类型拆分。",
            )

        if ("刚才" in q or "同样口径" in q) and (
            "对比" in q
            or "业务结构" in q
            or "主动权益" in q
            or any(name in q for name in ["易方达", "华夏", "广发", "富国", "中欧", "嘉实", "南方", "博时"])
        ):
            companies = []
            for name in ["易方达", "华夏", "广发", "富国", "中欧", "嘉实", "南方", "博时"]:
                if name in q:
                    companies.append(name)
            if not companies:
                companies = memory_context.get("last_companies") or []
            if "华夏" in q and "华夏" not in companies:
                companies.append("华夏")
            if "刚才那两家公司" in q and memory_context.get("last_companies"):
                companies = memory_context["last_companies"]
            if companies and ("主动权益" in q or memory_context.get("last_asset_type") == "主动权益"):
                if len(companies) == 1:
                    args = {
                        "date": memory_context.get("last_date"),
                        "asset_type": "主动权益",
                        "fund_company": companies[0],
                        "top_n": top_n,
                    }
                else:
                    args = {
                        "companies": companies,
                        "date": memory_context.get("last_date"),
                        "asset_type": "主动权益",
                    }
                return AgentPlan(
                    intent="fund_size_ranking" if len(companies) == 1 else "company_structure_comparison",
                    tool_name="get_top_funds_by_size" if len(companies) == 1 else "compare_company_business_structure",
                    args=args,
                    answer_type="report" if len(companies) > 1 else "simple",
                    rationale="用户使用多轮指代，沿用上一轮公司/日期并补充主动权益口径。",
                )

        if companies and any(word in q for word in ["总规模", "管理规模", "合计规模", "规模是多少", "总资产"]):
            return AgentPlan(
                intent="company_total_size",
                tool_name="get_company_total_size",
                args={"companies": companies, "date": None, "asset_type": self._extract_asset_type(q)},
                answer_type="report" if ("计算过程" in q or "文件" in q or len(companies) > 1) else "simple",
                rationale="用户询问基金公司总规模，应汇总该公司同一日期下所有基金代码/份额的规模。",
            )

        if companies and any(word in q for word in ["整体重仓", "重仓股", "持仓股票", "持仓风格"]):
            return AgentPlan(
                intent="company_top_holdings" if len(companies) == 1 else "company_holding_comparison",
                tool_name="get_company_top_holdings" if len(companies) == 1 else "compare_holdings_between_companies",
                args={"companies": companies, "date": None, "asset_type": self._extract_asset_type(q), "top_n": top_n},
                answer_type="report",
                rationale="用户询问基金公司维度持仓，应聚合该公司旗下基金持仓。",
            )

        if companies and any(word in q for word in ["综合报告", "综合对比", "证据包"]):
            return AgentPlan(
                intent="report_evidence_pack",
                tool_name="build_report_evidence_pack",
                args={"companies": companies, "date": None, "asset_type": self._extract_asset_type(q), "period": "本年以来", "top_n": top_n},
                answer_type="report",
                rationale="用户要求复杂综合报告，应准备公司结构、持仓和业绩证据包。",
            )

        if company and any(word in q for word in ["旗下", "明细", "具体情况", "有哪些基金", "基金列表", "构成"]):
            return AgentPlan(
                intent="company_fund_list",
                tool_name="list_company_funds_by_size",
                args={
                    "companies": [company],
                    "fund_company": company,
                    "date": None,
                    "asset_type": self._extract_asset_type(q),
                    "top_n": top_n,
                },
                answer_type="report",
                rationale="用户询问某基金公司旗下基金明细，应返回该公司同一日期截面的基金规模列表。",
            )

        if "业务结构" in q or "结构对比" in q or ("对比" in q and ("易方达" in q or "华夏" in q)):
            if len(companies) < 2:
                companies = ["易方达", "华夏"]
            return AgentPlan(
                intent="company_structure_comparison",
                tool_name="compare_company_business_structure",
                args={"companies": companies, "date": None},
                answer_type="report",
                rationale="用户询问基金公司业务结构对比，应调用公司结构分析工具。",
            )

        if ("持仓" in q or "重仓" in q) and "股票" in q:
            return AgentPlan(
                intent="stock_holding_ranking",
                tool_name="get_top_stocks_by_holding",
                args={"date": None, "fund_codes": None, "top_n": top_n if "哪只" not in q else 1},
                answer_type="simple",
                rationale="用户询问股票持仓规模排名，应调用股票持仓工具。",
            )

        if "规模" in q or "最大" in q or "排名" in q:
            if any(word in q for word in ["增长最快", "增长排名", "变化最大"]):
                return AgentPlan(
                    intent="size_growth_ranking",
                    tool_name="get_size_growth_ranking",
                    args={"entity": "company" if "公司" in q else "fund", "asset_type": self._extract_asset_type(q), "top_n": top_n},
                    answer_type="report",
                    rationale="用户询问规模增长排名，应比较最新两个日期截面。",
                )
            if any(word in q for word in ["基金公司", "哪家公司", "公司排名"]) and self._extract_asset_type(q):
                return AgentPlan(
                    intent="company_asset_type_ranking",
                    tool_name="rank_companies_by_asset_type_size",
                    args={"date": None, "asset_type": self._extract_asset_type(q), "top_n": top_n},
                    answer_type="simple",
                    rationale="用户询问某资产类型下基金公司规模排名，应使用公司资产类型排名工具。",
                )
            return AgentPlan(
                intent="fund_size_ranking",
                tool_name="get_top_funds_by_size",
                args={
                    "date": None,
                    "asset_type": self._extract_asset_type(q),
                    "fund_company": company,
                    "top_n": top_n,
                },
                answer_type="simple",
                rationale="用户询问基金规模排名，应调用基金规模工具。",
            )

        return AgentPlan(
            intent="unknown",
            tool_name="none",
            args={},
            answer_type="clarification",
            need_clarification=True,
            clarification_question="这个问题当前系统还不能确定应使用哪个分析工具，请补充您想看规模、持仓、业绩还是公司结构。",
            rationale="无法匹配当前工具能力。",
        )

    @staticmethod
    def _extract_top_n(query: str) -> int | None:
        m = re.search(r"前\s*(\d+)|top\s*(\d+)|最大(?:的)?(\d+)", query, flags=re.IGNORECASE)
        if not m:
            return None
        for group in m.groups():
            if group:
                return int(group)
        return None

    @staticmethod
    def _extract_asset_type(query: str) -> str | None:
        for asset_type in ["主动权益", "被动权益", "纯债", "现金管理", "量化", "FOF", "多资产投资", "REITs"]:
            if asset_type in query:
                return asset_type
        return None

    @staticmethod
    def _extract_company(query: str) -> str | None:
        for company in ["易方达", "华夏", "广发", "富国", "中欧", "嘉实", "南方", "博时"]:
            if company in query:
                return company
        return None

    @staticmethod
    def _extract_companies(query: str) -> list[str]:
        return [company for company in ["易方达", "华夏", "广发", "富国", "中欧", "嘉实", "南方", "博时"] if company in query]

    @staticmethod
    def _extract_fund_codes(query: str) -> list[str]:
        # 只识别 6 位基金代码，避免把“前10”“1季度”等普通数字误当成基金代码。
        return [code.zfill(6) for code in re.findall(r"(?<!\d)(\d{6})(?:\.OF)?(?!\d)", query)]

    @staticmethod
    def _extract_stock_keyword(query: str) -> str | None:
        stock_code = re.search(r"(?<!\d)(\d{6})(?!\d)", query)
        if stock_code:
            return stock_code.group(1)
        for stock_name in ["宁德时代", "贵州茅台", "腾讯控股", "阿里巴巴", "中际旭创", "五粮液"]:
            if stock_name in query:
                return stock_name
        return None

    @staticmethod
    def _normalize_intent(intent: object, tool_name: object) -> str:
        """LLM 有时会把 intent 写成中文短句，这里按 tool_name 收敛到枚举值。"""
        valid_intents = {
            "company_stock_holding_ranking",
            "stock_company_distribution",
            "stock_holder_funds_ranked",
            "company_stock_breakdown",
            "company_stock_comparison",
            "stock_company_concentration",
            "fund_size_ranking",
            "stock_holding_ranking",
            "company_structure_comparison",
            "company_total_size",
            "company_fund_list",
            "company_size_trend",
            "fund_size_history",
            "fund_holding_detail",
            "fund_holding_concentration",
            "stock_holder_funds",
            "performance_holding_analysis",
            "fund_lookup",
            "asset_type_distribution",
            "wind_category_distribution",
            "wind_category_fund_ranking",
            "company_asset_type_ranking",
            "company_wind_category_ranking",
            "size_growth_ranking",
            "fund_size_date_comparison",
            "performance_ranking",
            "performance_bottom_ranking",
            "fund_performance_detail",
            "fund_performance_comparison",
            "company_average_return_ranking",
            "performance_distribution",
            "company_top_holdings",
            "company_holding_comparison",
            "common_holdings",
            "stock_holding_trend",
            "stock_holding_by_asset_type",
            "fund_holding_change",
            "company_product_count",
            "company_active_equity_profile",
            "company_growth_comparison",
            "fund_screening",
            "size_return_analysis",
            "report_evidence_pack",
            "generated_sql_query",
            "unknown",
        }
        if isinstance(intent, str) and intent in valid_intents:
            return intent
        tool_to_intent = {
            "get_top_funds_by_size": "fund_size_ranking",
            "get_top_stocks_by_holding": "stock_holding_ranking",
            "compare_company_business_structure": "company_structure_comparison",
            "get_company_total_size": "company_total_size",
            "list_company_funds_by_size": "company_fund_list",
            "get_company_size_trend": "company_size_trend",
            "get_fund_size_history": "fund_size_history",
            "get_fund_holdings_detail": "fund_holding_detail",
            "analyze_fund_holding_concentration": "fund_holding_concentration",
            "find_funds_holding_stock": "stock_holder_funds",
            "analyze_top_performance_holdings": "performance_holding_analysis",
            "lookup_fund": "fund_lookup",
            "get_asset_type_size_distribution": "asset_type_distribution",
            "get_wind_category_size_distribution": "wind_category_distribution",
            "get_top_funds_by_wind_category": "wind_category_fund_ranking",
            "rank_companies_by_asset_type_size": "company_asset_type_ranking",
            "rank_companies_by_wind_category_size": "company_wind_category_ranking",
            "get_size_growth_ranking": "size_growth_ranking",
            "compare_fund_size_across_dates": "fund_size_date_comparison",
            "get_top_funds_by_performance": "performance_ranking",
            "get_bottom_funds_by_performance": "performance_bottom_ranking",
            "get_fund_performance_detail": "fund_performance_detail",
            "compare_fund_performance": "fund_performance_comparison",
            "rank_companies_by_average_return": "company_average_return_ranking",
            "analyze_performance_distribution": "performance_distribution",
            "get_company_top_holdings": "company_top_holdings",
            "compare_holdings_between_companies": "company_holding_comparison",
            "get_common_holdings_between_funds": "common_holdings",
            "get_stock_holding_trend": "stock_holding_trend",
            "get_stock_holding_by_asset_type": "stock_holding_by_asset_type",
            "get_fund_holding_change": "fund_holding_change",
            "get_company_product_count_by_asset_type": "company_product_count",
            "get_company_active_equity_profile": "company_active_equity_profile",
            "compare_company_growth": "company_growth_comparison",
            "screen_funds_by_conditions": "fund_screening",
            "analyze_size_and_return": "size_return_analysis",
            "build_report_evidence_pack": "report_evidence_pack",
            "rank_companies_by_stock_holding": "company_stock_holding_ranking",
            "get_stock_company_distribution": "stock_company_distribution",
            "rank_funds_holding_stock_by_value": "stock_holder_funds_ranked",
            "get_company_stock_holding_breakdown": "company_stock_breakdown",
            "compare_companies_stock_holding": "company_stock_comparison",
            "rank_stocks_by_company_concentration": "stock_company_concentration",
            "generated_sql_query": "generated_sql_query",
        }
        return tool_to_intent.get(str(tool_name), "unknown")

    @staticmethod
    def _extract_wind_category(query: str) -> tuple[str | None, int]:
        level = 1
        if "二级" in query:
            level = 2
        if "三级" in query:
            level = 3
        categories = [
            ("普通股票型", 3),
            ("偏股混合型", 3),
            ("灵活配置型", 3),
            ("被动指数型", 3),
            ("增强指数型", 3),
            ("中长期纯债", 3),
            ("短期纯债", 3),
            ("可转换债券", 3),
            ("混合债券型一级", 3),
            ("混合债券型二级", 3),
            ("股票型基金", 1),
            ("混合型基金", 1),
            ("债券型基金", 1),
            ("货币市场型基金", 1),
            ("国际(QDII)基金", 1),
            ("FOF基金", 1),
            ("REITs", 1),
        ]
        for name, default_level in categories:
            if name in query:
                return name, level if any(word in query for word in ["一级", "二级", "三级"]) else default_level
        return None, level
