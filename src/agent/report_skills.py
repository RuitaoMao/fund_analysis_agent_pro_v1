"""分析报告技能注册表。

每个技能负责一类问题的报告结构设计，通过规则匹配（无需额外 LLM 调用）：
- matches(query, tool_result) → bool     判断是否适用
- outline(query, tool_result) → ReportOutline  生成报告大纲

Drafter LLM 拿到大纲后撰写完整报告。技能按优先级排列，第一个匹配生效。
"""

from __future__ import annotations

import re

from src.agent.schemas import ReportOutline, ReportSection, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# 基类
# ──────────────────────────────────────────────────────────────────────────

class BaseReportSkill:
    """报告技能基类。"""

    skill_type: str = "generic"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        raise NotImplementedError

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        raise NotImplementedError

    @staticmethod
    def _has_table(tool_result: ToolResult, *keys: str) -> bool:
        """检查 tool_result 是否含有指定 table key（非空）。"""
        for k in keys:
            if tool_result.tables.get(k):
                return True
        return False

    @staticmethod
    def _kw(*words: str) -> re.Pattern:
        """把关键词列表编译成 OR 正则（忽略大小写）。"""
        return re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)


def _kw(*words: str) -> re.Pattern:
    """模块级快捷方式（给模块级常量使用）。"""
    return re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────
# Skill 1: 竞争格局 / 市场分析
# ──────────────────────────────────────────────────────────────────────────

_LANDSCAPE_KW = _kw(
    "竞争格局", "格局", "全市场", "整体", "行业", "市场概览", "市场分析",
    "市场规模", "公募市场",
)


class CompetitiveLandscapeSkill(BaseReportSkill):
    """全市场竞争格局分析。

    适用：'一季度公募基金规模竞争格局分析'、'全市场基金规模结构' 等。
    """

    skill_type = "competitive_landscape"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        if re.search(_LANDSCAPE_KW, query):
            return True
        # query_market_overview 工具结果
        if tool_result.tool_name == "query_market_overview":
            return True
        # query_fund_size group_by=company / asset_type + 无指定公司 → 市场分析
        if tool_result.tool_name == "query_fund_size" and (
            self._has_table(tool_result, "size_by_company", "size_by_asset_type", "size_by_wind")
        ):
            return True
        if tool_result.tool_name == "query_company_size" and not tool_result.metadata.get("companies"):
            return True
        return False

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        return ReportOutline(
            skill_type=self.skill_type,
            sections=[
                ReportSection(
                    title="市场总体规模",
                    analytical_angles=[
                        "说明全市场基金总规模（亿元）和基金总数",
                        "与上期对比（如有历史数据）：规模增减幅度",
                        "数据日期说明",
                    ],
                ),
                ReportSection(
                    title="各类型基金规模格局",
                    analytical_angles=[
                        "按资产类型列出规模和占比（主动权益/被动权益/纯债/现金管理等）",
                        "指出占比最高和增速最快的类型",
                        "分析结构特点（如权益类占比变化趋势、被动化趋势等）",
                    ],
                ),
                ReportSection(
                    title="头部基金公司竞争态势",
                    analytical_angles=[
                        "列出规模前 N 的基金公司及其市场份额",
                        "分析头部集中度：前5/前10公司合计份额",
                        "指出规模最大的公司及其领先优势",
                        "如有多期数据，识别排名变动的公司",
                    ],
                ),
                ReportSection(
                    title="市场结构判断",
                    analytical_angles=[
                        "总结本期市场的主要结构特征",
                        "指出值得关注的趋势或异常点",
                        "提示数据局限性（不做投资建议）",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 2: 多公司对比
# ──────────────────────────────────────────────────────────────────────────

_COMPARE_KW = _kw("对比", "比较", "vs", "VS", "和", "与", "pk", "PK")


class CompanyComparisonSkill(BaseReportSkill):
    """多家基金公司对比分析。

    适用：'对比分析易方达和华夏'、'易方达 vs 广发 业绩和规模'。
    """

    skill_type = "company_comparison"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        companies = tool_result.metadata.get("companies") or []
        if len(companies) >= 2:
            return True
        # query_company_size 返回多公司快照
        if tool_result.tool_name == "query_company_size" and (
            self._has_table(tool_result, "company_total", "company_breakdown")
        ):
            meta_companies = tool_result.metadata.get("companies") or []
            if len(meta_companies) >= 2:
                return True
        # 名称中含对比关键词 + 多公司
        if re.search(_COMPARE_KW, query) and re.search(
            r"[一-龥]{2,}(?:基金|资产|投资)?.*[一-龥]{2,}(?:基金|资产|投资)?", query
        ):
            return True
        return False

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        companies = tool_result.metadata.get("companies") or []
        company_str = "、".join(companies) if companies else "各公司"
        return ReportOutline(
            skill_type=self.skill_type,
            sections=[
                ReportSection(
                    title=f"{company_str} 总规模对比",
                    analytical_angles=[
                        f"列出 {company_str} 的总规模和基金数量",
                        "规模差距量化：领先公司超出多少亿/多少倍",
                        "数据日期说明",
                    ],
                ),
                ReportSection(
                    title="业务结构对比",
                    analytical_angles=[
                        "各公司在主动权益/被动权益/纯债/现金管理等类型的规模和占比",
                        "对比各公司的业务侧重点差异",
                        "识别哪家公司在哪个类型有明显优势",
                    ],
                ),
                ReportSection(
                    title="业绩表现对比（如有数据）",
                    analytical_angles=[
                        "若有业绩数据，比较各公司旗下基金平均收益率、超额收益",
                        "对比风险指标（最大回撤）",
                        "无业绩数据时跳过此节，说明'业绩数据未在本次查询范围内'",
                    ],
                ),
                ReportSection(
                    title="持仓风格对比（如有数据）",
                    analytical_angles=[
                        "若有持仓数据，比较各公司的重仓股偏好",
                        "识别共同重仓股和差异化持仓",
                        "无持仓数据时跳过此节",
                    ],
                ),
                ReportSection(
                    title="综合评价",
                    analytical_angles=[
                        "从规模体量、业务多元化、业绩稳定性等角度做综合对比",
                        "指出各公司的相对优势",
                        "客观陈述，不做投资建议",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 3: 单公司深度分析
# ──────────────────────────────────────────────────────────────────────────

class CompanyProfileSkill(BaseReportSkill):
    """单家基金公司深度分析。

    适用：'易方达基金总览'、'分析华夏基金的业务结构和业绩'。
    """

    skill_type = "company_profile"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        if tool_result.tool_name in ("query_company_size", "query_fund_size"):
            companies = tool_result.metadata.get("companies") or []
            fund_company = tool_result.metadata.get("fund_company")
            if (len(companies) == 1) or fund_company:
                return True
        return False

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        companies = tool_result.metadata.get("companies") or []
        company = companies[0] if companies else tool_result.metadata.get("fund_company") or "该公司"
        return ReportOutline(
            skill_type=self.skill_type,
            sections=[
                ReportSection(
                    title=f"{company} 规模总览",
                    analytical_angles=[
                        f"{company} 当前总规模、基金数量、数据日期",
                        "在全市场中的大致排位（如有全市场参照）",
                        "与前期对比（如有历史数据）",
                    ],
                ),
                ReportSection(
                    title="业务结构分析",
                    analytical_angles=[
                        "各资产类型的规模和占比（主动权益/被动权益/纯债等）",
                        "业务重心：哪类产品是核心业务",
                        "产品线多元化程度",
                    ],
                ),
                ReportSection(
                    title="规模趋势（如有历史数据）",
                    analytical_angles=[
                        "近几个季度规模变化趋势",
                        "增速加快或减缓的拐点",
                        "无历史数据时跳过此节",
                    ],
                ),
                ReportSection(
                    title="小结",
                    analytical_angles=[
                        f"总结 {company} 的核心竞争力和业务特点",
                        "客观描述，不做投资建议",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 4: 业绩分析
# ──────────────────────────────────────────────────────────────────────────

_PERF_KW = _kw("业绩", "收益", "盈利", "涨幅", "回报", "超额", "回撤", "最大回撤", "夏普", "胜率")


class PerformanceAnalysisSkill(BaseReportSkill):
    """业绩排名与分析。

    适用：'本年以来收益率最高的基金'、'主动权益超额收益分析'。
    """

    skill_type = "performance_analysis"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        if tool_result.tool_name == "query_fund_performance":
            return True
        if re.search(_PERF_KW, query) and tool_result.tool_name in (
            "screen_funds", "query_performance_holdings"
        ):
            return True
        return False

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        period = tool_result.metadata.get("period") or "指定区间"
        return ReportOutline(
            skill_type=self.skill_type,
            sections=[
                ReportSection(
                    title=f"{period}业绩概览",
                    analytical_angles=[
                        f"说明{period}业绩分析的数据范围（基金数量、筛选条件）",
                        "最高/最低收益率及其对应的基金",
                        "数据口径：组合收益率 vs 超额收益 vs 最大回撤",
                    ],
                ),
                ReportSection(
                    title="头部基金分析",
                    analytical_angles=[
                        "列出收益率前列基金（名称、公司、收益率%）",
                        "分析共同特征：属于哪家公司、哪种资产类型",
                        "超额收益最突出的基金及其表现",
                    ],
                ),
                ReportSection(
                    title="风险调整后表现",
                    analytical_angles=[
                        "对比最大回撤：高收益基金是否伴随高回撤",
                        "指出'高收益低回撤'的基金（如有）",
                        "回撤最小的基金及其收益率",
                    ],
                ),
                ReportSection(
                    title="持仓关联（如有数据）",
                    analytical_angles=[
                        "若有持仓数据，分析头部基金的共同重仓股",
                        "无持仓数据时跳过",
                    ],
                ),
                ReportSection(
                    title="分析小结",
                    analytical_angles=[
                        "总结本期业绩格局的主要特征",
                        "指出值得关注的基金或趋势",
                        "客观描述历史业绩，不预测未来，不做投资建议",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 5: 持仓分析
# ──────────────────────────────────────────────────────────────────────────

class HoldingsAnalysisSkill(BaseReportSkill):
    """持仓结构分析。

    适用：'易方达重仓股分析'、'业绩前10基金的持仓'。
    """

    skill_type = "holdings_analysis"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        if tool_result.tool_name in ("query_fund_holdings", "query_performance_holdings"):
            return True
        return False

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        return ReportOutline(
            skill_type=self.skill_type,
            sections=[
                ReportSection(
                    title="持仓概览",
                    analytical_angles=[
                        "说明持仓数据的范围（基金数量、持仓日期）",
                        "持仓股票总数、总持仓规模",
                    ],
                ),
                ReportSection(
                    title="重仓股分析",
                    analytical_angles=[
                        "列出持仓规模最大的前 N 只股票",
                        "分析行业集中情况（从股票名称推断，如科技/消费/金融）",
                        "指出持仓最广泛的共识股（被最多基金持有）",
                    ],
                ),
                ReportSection(
                    title="集中度分析",
                    analytical_angles=[
                        "若有集中度数据：前5/前10持仓占基金净值的比例",
                        "对比不同基金的持仓集中程度",
                        "无集中度数据时跳过",
                    ],
                ),
                ReportSection(
                    title="业绩与持仓关联（如有业绩数据）",
                    analytical_angles=[
                        "若有业绩前N基金的持仓数据，分析其重仓股与业绩的关联",
                        "指出高收益基金与普通基金在持仓上的差异",
                        "无业绩数据时跳过",
                    ],
                ),
                ReportSection(
                    title="持仓小结",
                    analytical_angles=[
                        "总结持仓特征和规律",
                        "客观描述，不做投资建议",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 6: 个股持有人分析
# ──────────────────────────────────────────────────────────────────────────

class StockHolderSkill(BaseReportSkill):
    """个股持有人分析。

    适用：'哪些基金持有宁德时代'、'持仓贵州茅台最多的基金公司'、'公募共识股'。
    """

    skill_type = "stock_holder_analysis"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        return tool_result.tool_name == "query_stock_holders"

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        stock = tool_result.metadata.get("stock_keyword") or "目标股票"
        group_by = tool_result.metadata.get("group_by") or "fund"

        if group_by == "concentration":
            # 共识股模式
            return ReportOutline(
                skill_type=self.skill_type,
                sections=[
                    ReportSection(
                        title="公募共识股排名",
                        analytical_angles=[
                            "列出被最多基金公司同时持有的股票",
                            "说明每只共识股的持仓公司数、基金数、总规模",
                            "分析共识股的特征（通常是行业龙头）",
                        ],
                    ),
                    ReportSection(
                        title="共识度分析",
                        analytical_angles=[
                            "TOP5 共识股与其余股票的持有机构数差距",
                            "共识股在全市场持仓中的规模占比",
                        ],
                    ),
                    ReportSection(
                        title="小结",
                        analytical_angles=[
                            "总结公募基金的整体持仓偏好",
                            "客观描述，不做投资建议",
                        ],
                    ),
                ],
            )
        elif group_by == "company":
            return ReportOutline(
                skill_type=self.skill_type,
                sections=[
                    ReportSection(
                        title=f"基金公司持有 {stock} 排名",
                        analytical_angles=[
                            f"列出持有 {stock} 规模最大的基金公司",
                            "各公司持仓规模、持仓基金数、平均净值占比",
                            "指出持仓最重的公司及其规模领先幅度",
                        ],
                    ),
                    ReportSection(
                        title="持仓结构分析",
                        analytical_angles=[
                            f"分析机构对 {stock} 的持仓集中度",
                            "持仓最重的公司占全市场该股票持仓的比例",
                            "各公司的配置策略差异（重仓 vs 轻仓）",
                        ],
                    ),
                    ReportSection(
                        title="小结",
                        analytical_angles=[
                            f"总结机构持有 {stock} 的整体格局",
                            "客观描述，不做投资建议",
                        ],
                    ),
                ],
            )
        else:
            # 基金维度
            return ReportOutline(
                skill_type=self.skill_type,
                sections=[
                    ReportSection(
                        title=f"持有 {stock} 的基金排名",
                        analytical_angles=[
                            f"列出持有 {stock} 净值占比最高的基金",
                            "说明每只基金的持仓规模、净值占比、所属公司",
                            "指出持仓最重的基金（净值占比最高）",
                        ],
                    ),
                    ReportSection(
                        title="持仓机构分布",
                        analytical_angles=[
                            f"分析哪些基金公司旗下基金持有 {stock}",
                            "持有基金总数和总持仓规模",
                            "不同资产类型基金的持仓差异",
                        ],
                    ),
                    ReportSection(
                        title="小结",
                        analytical_angles=[
                            f"总结市场对 {stock} 的持仓格局",
                            "客观描述，不做投资建议",
                        ],
                    ),
                ],
            )


# ──────────────────────────────────────────────────────────────────────────
# Skill 7: 筛选结果分析
# ──────────────────────────────────────────────────────────────────────────

class ScreeningResultSkill(BaseReportSkill):
    """多条件筛选结果分析。

    适用：'规模>50亿且收益>10%的主动权益基金'。
    """

    skill_type = "screening_result"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        return tool_result.tool_name == "screen_funds"

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        rows = tool_result.tables.get("screened_funds", [])
        count = len(rows)
        return ReportOutline(
            skill_type=self.skill_type,
            direct_answer=f"共找到 {count} 只符合条件的基金。" if count > 0 else "当前条件下未找到符合的基金。",
            sections=[
                ReportSection(
                    title="筛选结果概览",
                    analytical_angles=[
                        f"说明筛选条件（从数据口径中提取）",
                        f"结果数量：{count} 只基金",
                        "0 结果时建议放宽条件并说明可能原因",
                    ],
                ),
                ReportSection(
                    title="结果特征分析",
                    analytical_angles=[
                        "收益率最高和最低的基金各是哪只",
                        "规模分布：大型/中型/小型基金各有几只",
                        "来自哪些基金公司（是否集中在头部公司）",
                        "资产类型分布",
                    ],
                ),
                ReportSection(
                    title="关键指标对比",
                    analytical_angles=[
                        "列出关键指标表格（收益率、超额收益、最大回撤、规模）",
                        "找出收益率与回撤比最优的基金",
                        "指出高收益伴随高回撤的基金（注意风险）",
                    ],
                ),
                ReportSection(
                    title="小结",
                    analytical_angles=[
                        "总结筛选结果的共同特征",
                        "客观描述，不预测未来，不做投资建议",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 8: 简单查找
# ──────────────────────────────────────────────────────────────────────────

class SimpleLookupSkill(BaseReportSkill):
    """简单查找/基础信息类问题。

    适用：'005827是什么基金'、'易方达蓝筹精选的代码'。
    """

    skill_type = "simple_lookup"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        return tool_result.tool_name == "lookup_fund"

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        rows = tool_result.tables.get("lookup_result", [])
        direct = None
        if rows:
            r = rows[0]
            name = r.get("基金名称", "")
            code = r.get("基金代码", "")
            company = r.get("基金公司", "")
            size = r.get("最新规模_亿", "")
            direct = f"{name}（{code}），{company}，最新规模 {size} 亿元。"
        return ReportOutline(
            skill_type=self.skill_type,
            direct_answer=direct,
            sections=[
                ReportSection(
                    title="基金基础信息",
                    analytical_angles=[
                        "基金代码、名称、管理公司、资产类型、最新规模",
                        "若返回多只基金，列表展示并说明检索方式（代码/名称模糊匹配）",
                    ],
                ),
                ReportSection(
                    title="说明",
                    analytical_angles=[
                        "说明数据日期",
                        "若未找到，建议用户确认代码/名称拼写，或换关键词搜索",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# Skill 9: 通用排名（兜底）
# ──────────────────────────────────────────────────────────────────────────

class GenericRankingSkill(BaseReportSkill):
    """通用排名/查询结果（兜底技能，匹配所有未被前8个技能拦截的查询）。"""

    skill_type = "generic"

    def matches(self, query: str, tool_result: ToolResult) -> bool:
        return True  # 永远兜底

    def outline(self, query: str, tool_result: ToolResult) -> ReportOutline:
        # 判断是否是简单直接回答
        total_rows = sum(len(v) for v in tool_result.tables.values())
        is_simple = total_rows <= 5

        direct = None
        if is_simple and total_rows == 1:
            # 单行结果：可能是简单数值查询
            for rows in tool_result.tables.values():
                if rows:
                    vals = list(rows[0].values())
                    direct = "、".join(str(v) for v in vals[:3] if v is not None)

        return ReportOutline(
            skill_type=self.skill_type,
            direct_answer=direct,
            sections=[
                ReportSection(
                    title="查询结果",
                    analytical_angles=[
                        "展示完整查询结果（表格形式）",
                        "说明数据口径（日期、范围、排序方式）",
                        f"共 {total_rows} 条记录" + ("，已达到上限" if total_rows >= 20 else ""),
                    ],
                ),
                ReportSection(
                    title="结果分析",
                    analytical_angles=[
                        "找出结果中的最大值、最小值和均值（如适用）",
                        "指出排名靠前和靠后的典型案例",
                        "是否有异常值或值得关注的规律",
                        "如数据为空，说明数据库中没有满足条件的记录，建议放宽条件",
                    ],
                ),
            ],
        )


# ──────────────────────────────────────────────────────────────────────────
# 技能注册表 & 选择器
# ──────────────────────────────────────────────────────────────────────────

_SKILLS: list[BaseReportSkill] = [
    CompetitiveLandscapeSkill(),
    CompanyComparisonSkill(),
    CompanyProfileSkill(),
    PerformanceAnalysisSkill(),
    HoldingsAnalysisSkill(),
    StockHolderSkill(),
    ScreeningResultSkill(),
    SimpleLookupSkill(),
    GenericRankingSkill(),   # 兜底，必须放最后
]


def select_skill(query: str, tool_result: ToolResult) -> BaseReportSkill:
    """根据查询和工具结果，选择最合适的报告技能（第一个匹配优先）。"""
    for skill in _SKILLS:
        if skill.matches(query, tool_result):
            return skill
    return _SKILLS[-1]   # GenericRankingSkill 永远兜底
