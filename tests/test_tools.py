from pathlib import Path

from src.config import Settings
from src.data.sqlite_store import SQLiteStore
from src.tools import build_default_registry


def test_registry_contains_core_tools():
    registry = build_default_registry()
    assert len(registry.names()) >= 25
    assert registry.exists("get_top_funds_by_size")
    assert registry.exists("get_top_stocks_by_holding")
    assert registry.exists("get_company_total_size")
    assert registry.exists("list_company_funds_by_size")
    assert registry.exists("compare_company_business_structure")
    assert registry.exists("analyze_top_performance_holdings")
    assert registry.exists("get_company_size_trend")
    assert registry.exists("get_fund_size_history")
    assert registry.exists("get_fund_holdings_detail")
    assert registry.exists("analyze_fund_holding_concentration")
    assert registry.exists("find_funds_holding_stock")
    assert registry.exists("get_wind_category_size_distribution")
    assert registry.exists("get_top_funds_by_wind_category")
    assert registry.exists("rank_companies_by_asset_type_size")
    assert registry.exists("get_company_top_holdings")
    assert registry.exists("screen_funds_by_conditions")
    assert registry.exists("build_report_evidence_pack")


def test_core_tools_return_expected_tables(temp_store):
    registry = build_default_registry()

    size_result = registry.execute(
        "get_top_funds_by_size",
        temp_store,
        {"date": None, "asset_type": "主动权益", "fund_company": None, "top_n": 3},
    )
    assert len(size_result.tables["fund_size_ranking"]) == 3
    assert "基金代码" in size_result.tables["fund_size_ranking"][0]

    holding_result = registry.execute(
        "get_top_stocks_by_holding",
        temp_store,
        {"date": None, "fund_codes": None, "top_n": 1},
    )
    assert len(holding_result.tables["stock_holding_ranking"]) == 1
    assert "股票代码" in holding_result.tables["stock_holding_ranking"][0]

    company_result = registry.execute(
        "compare_company_business_structure",
        temp_store,
        {"date": None, "companies": ["易方达", "华夏"], "asset_type": "主动权益"},
    )
    assert company_result.tables["company_summary"]
    assert company_result.metadata["asset_type"] == "主动权益"

    total_result = registry.execute(
        "get_company_total_size",
        temp_store,
        {"date": None, "companies": ["易方达"], "asset_type": None},
    )
    total_rows = total_result.tables["company_total_size"]
    assert total_rows[0]["基金公司"] == "易方达"
    assert round(total_rows[0]["公司总规模"], 2) == 24954.48
    assert total_rows[0]["基金代码数量"] == 503
    assert total_result.metadata["source_files"] == ["data/raw/规模.xlsx"]

    funds_result = registry.execute(
        "list_company_funds_by_size",
        temp_store,
        {"date": None, "companies": ["易方达"], "fund_company": "易方达", "asset_type": None, "top_n": 5},
    )
    assert len(funds_result.tables["company_funds"]) == 5
    assert funds_result.tables["company_summary"][0]["基金公司"] == "易方达"

    perf_result = registry.execute(
        "analyze_top_performance_holdings",
        temp_store,
        {"period": "本年以来", "top_n": 5, "holding_date": None, "asset_type": None},
    )
    assert perf_result.tables["top_performance_funds"]
    assert "missing_fund_codes" in perf_result.metadata

    trend_result = registry.execute(
        "get_company_size_trend",
        temp_store,
        {"companies": ["易方达"], "asset_type": None},
    )
    assert trend_result.tables["company_size_trend"]
    assert "较上期变化" in trend_result.tables["company_size_trend"][0]

    history_result = registry.execute(
        "get_fund_size_history",
        temp_store,
        {"fund_code": "005827", "top_n": 10},
    )
    assert history_result.tables["fund_size_history"]
    assert history_result.tables["fund_size_history"][0]["基金代码"] == "005827"

    detail_result = registry.execute(
        "get_fund_holdings_detail",
        temp_store,
        {"fund_codes": ["005827"], "date": None, "top_n": 5},
    )
    assert len(detail_result.tables["fund_holdings_detail"]) <= 5
    assert detail_result.tables["fund_holdings_detail"]

    concentration_result = registry.execute(
        "analyze_fund_holding_concentration",
        temp_store,
        {"fund_codes": ["005827"], "date": None, "top_n": 10},
    )
    assert concentration_result.tables["fund_holding_concentration"]
    assert "前N大持仓占比" in concentration_result.tables["fund_holding_concentration"][0]

    holders_result = registry.execute(
        "find_funds_holding_stock",
        temp_store,
        {"stock_keyword": "宁德时代", "date": None, "top_n": 5},
    )
    assert holders_result.tables["stock_holder_funds"]
    assert holders_result.tables["stock_holder_funds"][0]["股票名称"] == "宁德时代"

    wind_result = registry.execute(
        "get_top_funds_by_wind_category",
        temp_store,
        {"date": None, "wind_level": 3, "wind_category": "偏股混合型", "top_n": 3},
    )
    assert wind_result.tables["wind_category_fund_ranking"]
    assert "Wind分类" in wind_result.tables["wind_category_fund_ranking"][0]

    company_holding_result = registry.execute(
        "get_company_top_holdings",
        temp_store,
        {"companies": ["易方达"], "date": None, "asset_type": None, "top_n": 3},
    )
    assert company_holding_result.tables["company_top_holdings"]

    screening_result = registry.execute(
        "screen_funds_by_conditions",
        temp_store,
        {"asset_type": "主动权益", "min_size": 100, "period": "本年以来", "top_n": 3},
    )
    assert screening_result.tables["fund_screening"]
