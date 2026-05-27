"""Pydantic 参数 schema（对应 8 个通用工具）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


def _normalize_code(value: Any) -> str:
    text = str(value).strip().split(".")[0]
    return text.zfill(6) if text.isdigit() else text


class TopNMixin(BaseModel):
    top_n: int = 10

    @field_validator("top_n", mode="before")
    @classmethod
    def coerce_top_n(cls, value: object) -> int:
        if value is None:
            return 10
        try:
            v = int(value)
        except (TypeError, ValueError):
            return 10
        if v <= 0:
            return 10
        return min(v, 50)


class QueryFundSizeArgs(TopNMixin):
    date: str | None = None
    asset_type: str | None = None
    fund_company: str | None = None
    fund_codes: list[str] | None = None
    wind_category: str | None = None
    group_by: str | None = None
    include_history: bool = False

    @field_validator("fund_codes", mode="before")
    @classmethod
    def normalize_fund_codes(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            v = [v]
        return [_normalize_code(c) for c in v]


class QueryCompanySizeArgs(TopNMixin):
    companies: list[str] | None = None
    date: str | None = None
    asset_type: str | None = None
    include_trend: bool = False

    @field_validator("companies", mode="before")
    @classmethod
    def normalize_companies(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return [v]
        return list(v)


class QueryFundPerformanceArgs(TopNMixin):
    period: str = "本年以来"
    ascending: bool = False
    sort_by: str = "portfolio_return"   # portfolio_return | excess_return | max_drawdown
    fund_codes: list[str] | None = None
    fund_company: str | None = None
    asset_type: str | None = None
    rank_by_company: bool = False

    @field_validator("fund_codes", mode="before")
    @classmethod
    def normalize_fund_codes(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            v = [v]
        return [_normalize_code(c) for c in v]


class QueryFundHoldingsArgs(TopNMixin):
    fund_codes: list[str] | None = None
    companies: list[str] | None = None
    date: str | None = None
    include_concentration: bool = False

    @field_validator("fund_codes", mode="before")
    @classmethod
    def normalize_fund_codes(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            v = [v]
        return [_normalize_code(c) for c in v]

    @field_validator("companies", mode="before")
    @classmethod
    def normalize_companies(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return [v]
        return list(v)


class QueryStockHoldersArgs(TopNMixin):
    stock_keyword: str | None = None
    date: str | None = None
    group_by: str = "fund"
    companies: list[str] | None = None
    fund_company: str | None = None
    asset_type: str | None = None
    min_companies: int = 2

    @field_validator("companies", mode="before")
    @classmethod
    def normalize_companies(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return [v]
        return list(v)


class ScreenFundsArgs(TopNMixin):
    date: str | None = None
    holding_date: str | None = None
    period: str = "本年以来"
    asset_type: str | None = None
    fund_company: str | None = None
    min_size: float | None = None
    min_return: float | None = None          # 总收益率下限（小数，0.10=10%）
    min_excess_return: float | None = None   # 超额收益下限（小数，0.05=5%）
    max_drawdown: float | None = None        # 最大回撤上限（正小数，0.10=10%；DB存负数，自动处理）
    stock_keyword: str | None = None
    min_nav_ratio: float | None = None


class QueryPerformanceHoldingsArgs(TopNMixin):
    period: str = "本年以来"
    holding_date: str | None = None
    asset_type: str | None = None


class LookupFundArgs(TopNMixin):
    keyword: str


TOOL_ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    "query_fund_size": QueryFundSizeArgs,
    "query_company_size": QueryCompanySizeArgs,
    "query_fund_performance": QueryFundPerformanceArgs,
    "query_fund_holdings": QueryFundHoldingsArgs,
    "query_stock_holders": QueryStockHoldersArgs,
    "screen_funds": ScreenFundsArgs,
    "query_performance_holdings": QueryPerformanceHoldingsArgs,
    "lookup_fund": LookupFundArgs,
}
