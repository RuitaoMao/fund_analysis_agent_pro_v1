"""工具包入口（9 个通用工具）。"""

from src.tools.registry import ToolRegistry
from src.tools.tools import (
    lookup_fund,
    query_company_size,
    query_fund_holdings,
    query_fund_performance,
    query_fund_size,
    query_market_overview,
    query_performance_holdings,
    query_stock_holders,
    screen_funds,
)


def build_default_registry() -> ToolRegistry:
    """注册全部通用工具。"""
    registry = ToolRegistry()
    registry.register("query_fund_size", query_fund_size)
    registry.register("query_company_size", query_company_size)
    registry.register("query_fund_performance", query_fund_performance)
    registry.register("query_fund_holdings", query_fund_holdings)
    registry.register("query_stock_holders", query_stock_holders)
    registry.register("screen_funds", screen_funds)
    registry.register("query_performance_holdings", query_performance_holdings)
    registry.register("query_market_overview", query_market_overview)
    registry.register("lookup_fund", lookup_fund)
    return registry
