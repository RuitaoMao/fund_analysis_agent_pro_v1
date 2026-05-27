"""工具说明中心（8 个通用工具）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    name: str
    description: str
    when_to_use: list[str]
    args_schema: dict[str, Any]
    output_description: str
    examples: list[str] = field(default_factory=list)


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="query_fund_size",
        description="通用基金规模查询：基金级别排名、历史变化、按资产类型/Wind分类/公司汇总。",
        when_to_use=[
            "用户询问规模最大的基金、某类基金的规模排名、基金规模前 N。",
            "用户询问某资产类型或 Wind 分类的规模分布（group_by=asset_type/wind_level1/wind_level2）。",
            "用户询问各基金公司的规模汇总排名（group_by=company）。",
            "用户询问某只基金的历史规模变化（fund_codes + include_history=true）。",
            "用户询问某基金公司旗下基金规模排名（fund_company 参数）。",
        ],
        args_schema={
            "date": "string|null，快照日期，如 2026-03-31；null 使用最新日期。",
            "asset_type": "string|null，如 主动权益、被动权益、纯债、现金管理。",
            "fund_company": "string|null，公司简称，如 易方达、华夏。",
            "fund_codes": "list[string]|null，指定基金代码；历史模式时必填。",
            "wind_category": "string|null，Wind 分类关键词，如 偏股混合型。",
            "group_by": "string|null，汇总维度：asset_type | wind_level1 | wind_level2 | company | null（基金级别）。",
            "include_history": "boolean，true=展示历史规模变化（需配合 fund_codes），false=单日快照。",
            "top_n": "integer，返回条数，默认 20。",
        },
        output_description="基金规模排名或汇总表，包含代码、名称、公司、资产类型、规模（亿元）。",
        examples=[
            "1季度末规模最大的20只主动权益基金",
            "易方达旗下规模前10的基金",
            "全市场各资产类型规模分布",
            "005827的历史规模变化",
        ],
    ),
    ToolSpec(
        name="query_company_size",
        description="基金公司维度规模分析：公司排名、业务结构拆分、历史规模趋势。",
        when_to_use=[
            "用户询问某基金公司的总规模、当前规模、规模排名。",
            "用户询问某公司旗下各资产类型的规模拆分或业务结构。",
            "用户询问公司规模历史趋势、较上期变化（include_trend=true）。",
            "用户对比多家基金公司的规模（companies 填多家）。",
            "用户询问主动权益/纯债等类别规模最大的基金公司（companies 为空，asset_type 过滤）。",
        ],
        args_schema={
            "companies": "list[string]，基金公司列表，如 ['易方达'] 或 ['易方达','华夏']；null=全市场排名。",
            "date": "string|null，快照日期；null 使用最新日期。",
            "asset_type": "string|null，可选过滤。",
            "include_trend": "boolean，true=展示历史趋势（含 LAG 变化），false=单日快照。",
            "top_n": "integer，全市场排名时返回条数，默认 20。",
        },
        output_description="公司总规模表（含资产类型拆分）或历史趋势表，规模单位亿元。",
        examples=[
            "易方达基金总规模是多少",
            "对比易方达和华夏的业务结构",
            "易方达近几个季度规模趋势",
            "主动权益规模最大的基金公司TOP10",
        ],
    ),
    ToolSpec(
        name="query_fund_performance",
        description="通用基金业绩查询：排名、单基金明细、公司均值排名。支持按收益率、超额收益或最大回撤排序。",
        when_to_use=[
            "用户询问收益率最高/最低的基金 → sort_by=portfolio_return, ascending=false/true。",
            "用户询问最大回撤最小的基金（风险最低）→ sort_by=max_drawdown, ascending=false。",
            "用户询问超额收益最高的基金 → sort_by=excess_return, ascending=false。",
            "用户给出基金代码，询问其各区间业绩（fund_codes 参数）。",
            "用户对比多只基金收益（fund_codes 填多只）。",
            "用户询问哪家基金公司平均收益最高（rank_by_company=true）。",
            "用户询问某公司或某资产类型的基金业绩。",
        ],
        args_schema={
            "period": "string，业绩区间，可选 本年以来|最近一月|最近一年|最近三年|最近五年；默认 本年以来。",
            "top_n": "integer，返回条数，默认 10。",
            "sort_by": "string，排序字段：portfolio_return（总收益，默认）| excess_return（超额收益）| max_drawdown（最大回撤）。",
            "ascending": "boolean，false=排名靠前优先（默认）；对max_drawdown=false表示回撤最小（最优）优先。",
            "fund_codes": "list[string]|null，指定基金代码；填写时返回这些基金的全区间明细。",
            "fund_company": "string|null，过滤基金公司。",
            "asset_type": "string|null，过滤资产类型。",
            "rank_by_company": "boolean，true=按公司旗下基金平均收益率排名。",
        },
        output_description="基金业绩排名表（含代码、名称、组合收益率%、基准%、超额%、最大回撤%）。最大回撤为正值百分比，值越小表示回撤越轻。",
        examples=[
            "本年以来收益率前10基金",
            "本年以来最大回撤最小的主动权益基金 → sort_by=max_drawdown, ascending=false, asset_type=主动权益",
            "005827的历史业绩",
            "主动权益平均收益最高的基金公司",
            "华夏基金最近一年超额收益最好的基金 → sort_by=excess_return",
        ],
    ),
    ToolSpec(
        name="query_fund_holdings",
        description="基金或公司的持仓查询：重仓股列表、持仓明细、持仓集中度、全市场股票排名。",
        when_to_use=[
            "用户给出基金代码，询问该基金持有哪些股票（fund_codes 参数）。",
            "用户询问某基金公司整体重仓哪些股票（companies 参数）。",
            "用户询问全市场持仓规模最大的股票（fund_codes 和 companies 均不填）。",
            "用户询问基金持仓集中度、前 N 大持仓占比（include_concentration=true）。",
            "用户询问几只基金共同持有的股票（fund_codes 填多只）。",
        ],
        args_schema={
            "fund_codes": "list[string]|null，基金代码列表；与 companies 二选一，均不填则全市场排名。",
            "companies": "list[string]|null，公司名列表，如 ['易方达']；查询公司整体持仓时使用。",
            "date": "string|null，持仓日期；null 使用最新日期。",
            "top_n": "integer，返回持仓条数，默认 10。",
            "include_concentration": "boolean，true=附加集中度统计，false=不附加（默认）。",
        },
        output_description="持仓明细表（股票代码、名称、持仓规模亿元、净值占比%）；include_concentration 时附集中度表。",
        examples=[
            "005827具体持有哪些股票",
            "易方达整体重仓股前20",
            "全市场持仓规模最大的股票",
            "这些基金的前10大持仓及集中度",
        ],
    ),
    ToolSpec(
        name="query_stock_holders",
        description="个股持有情况查询：持有某股票的基金/公司排名、共识股识别。",
        when_to_use=[
            "用户询问哪些基金持有某只股票，或某股票净值占比/持仓最高的基金（stock_keyword + group_by=fund）。注意：'净值占比最高的基金'用此工具，不用 screen_funds。",
            "用户询问哪家基金公司持有某股票最多（stock_keyword + group_by=company）。",
            "用户对比几家公司对同一股票的持仓（stock_keyword + companies + group_by=company）。",
            "用户询问被最多公司同时持有的股票、公募共识股（group_by=concentration，无需 stock_keyword）。",
            "用户询问全市场持仓规模最大的股票（不填 stock_keyword，不填 group_by）。",
        ],
        args_schema={
            "stock_keyword": "string|null，股票代码（6位数字）或名称关键词，如 300750 或 宁德时代；共识股模式不填。",
            "date": "string|null，持仓日期；null 使用最新日期。",
            "group_by": "string，维度：fund（基金级别，默认，按净值占比排序）| company（公司级别）| concentration（共识股排名）。",
            "companies": "list[string]|null，对比公司时填写，如 ['易方达','华夏']。",
            "fund_company": "string|null，过滤特定公司。",
            "asset_type": "string|null，过滤资产类型。",
            "top_n": "integer，返回条数，默认 20。",
            "min_companies": "integer，共识股模式时最少持有公司数，默认 2。",
        },
        output_description="fund 模式：持有该股票的基金列表，按净值占比降序排列，列：基金代码、基金名称、股票名称、净值占比_pct（核心指标）、持仓规模_亿、基金公司、资产类型、基金规模_亿。company 模式：公司汇总持仓。concentration：共识股排名。",
        examples=[
            "持仓宁德时代净值占比最高的5只基金 → stock_keyword=宁德时代, group_by=fund, top_n=5",
            "持仓贵州茅台最多的基金公司前5 → stock_keyword=贵州茅台, group_by=company, top_n=5",
            "易方达和华夏谁更看好宁德时代 → companies=[易方达,华夏], group_by=company",
            "公募基金共识股是什么 → group_by=concentration",
        ],
    ),
    ToolSpec(
        name="screen_funds",
        description="多条件筛选基金，联合规模、业绩、持仓三张表。",
        when_to_use=[
            "用户同时给出多个条件，如规模下限、收益率、回撤、持仓股票等。",
            "用户问：规模超过X亿 且 收益率超过Y% 的基金有哪些。",
            "用户问：持仓某股票且净值占比超过Z% 且 业绩排名靠前的基金。",
            "用户要求筛选符合条件的基金列表。",
        ],
        args_schema={
            "date": "string|null，规模日期；null 使用最新日期。",
            "holding_date": "string|null，持仓日期；null 使用最新日期。",
            "period": "string，业绩区间，默认 本年以来。",
            "asset_type": "string|null，资产类型过滤。",
            "fund_company": "string|null，公司过滤。",
            "min_size": "number|null，规模下限（亿元）。",
            "min_return": "number|null，总收益率下限（小数，0.10=10%）。用于'收益为正/超过X%'。",
            "min_excess_return": "number|null，超额收益下限（小数，0.05=5%）。用于'超额收益超过X%'。",
            "max_drawdown": "number|null，最大回撤上限（正小数，0.10=10%）。用于'回撤低于/小于X%'。",
            "stock_keyword": "string|null，持仓股票关键词（选填）。",
            "min_nav_ratio": "number|null，持仓净值占比下限（百分点，5=5%）。",
            "top_n": "integer，返回条数，默认 20。",
        },
        output_description="满足条件的基金列表（代码、名称、公司、规模亿元、收益率%、超额%、回撤%）。",
        examples=[
            "规模超过100亿且本年以来收益为正的主动权益基金 → min_return=0",
            "超额收益超过5%且最大回撤低于10%的基金 → min_excess_return=0.05, max_drawdown=0.10",
            "持仓贵州茅台净值占比超过5%且本年收益>10%的基金 → min_return=0.10, stock_keyword=贵州茅台, min_nav_ratio=5",
        ],
    ),
    ToolSpec(
        name="query_performance_holdings",
        description="业绩前 N 基金的持仓分析：先筛出收益率最高的基金，再汇总其持仓股票。",
        when_to_use=[
            "用户询问表现最好的基金都持有哪些股票。",
            "用户要求业绩与持仓联动分析。",
            "用户问：收益率前10基金共同重仓了什么。",
        ],
        args_schema={
            "period": "string，业绩区间，默认 本年以来。",
            "top_n": "integer，取收益率前几名，默认 10。",
            "holding_date": "string|null，持仓日期；null 使用最新日期。",
            "asset_type": "string|null，过滤资产类型。",
        },
        output_description="返回两张表：top_performance_funds（业绩前N基金）和 top_fund_holdings（这些基金持仓汇总）。",
        examples=[
            "本年以来收益前10基金持有哪些股票",
            "主动权益业绩最好的基金都重仓什么",
        ],
    ),
    ToolSpec(
        name="lookup_fund",
        description="按基金代码或名称关键词检索基金基础信息。",
        when_to_use=[
            "用户想确认某只基金是否存在。",
            "用户输入基金简称或代码，需要定位基金。",
            '用户说"帮我查一下 XXX 基金"。',
        ],
        args_schema={
            "keyword": "string，基金代码（如 005827）或名称关键词（如 蓝筹精选）。",
            "top_n": "integer，返回数量，默认 10。",
        },
        output_description="匹配到的基金代码、名称、公司、资产类型、最新规模（亿元）。",
        examples=["帮我查一下易方达蓝筹精选的代码", "005827是什么基金"],
    ),
]


def render_tool_specs_for_prompt() -> str:
    """把工具说明渲染成 Planner prompt 可读文本。"""
    blocks: list[str] = []
    for spec in TOOL_SPECS:
        blocks.append(
            f"工具名：{spec.name}\n"
            f"用途：{spec.description}\n"
            f"适用场景：{'；'.join(spec.when_to_use)}\n"
            f"参数：{spec.args_schema}\n"
            f"输出：{spec.output_description}\n"
            f"示例：{'；'.join(spec.examples)}"
        )
    return "\n\n".join(blocks)
