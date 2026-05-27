"""数据清洗逻辑。

这个文件只做一件事：把原始 Excel 的中文列名、混乱代码、日期格式等，统一成更适合 SQL 查询的标准表结构。

注意：生产级项目中，清洗逻辑必须和工具查询逻辑分离。
否则每个 tool 都自己清洗一次，会导致口径不一致。
"""

from __future__ import annotations

from typing import Any
import pandas as pd


DROP_INDEX_COL_NAMES = {"", "   ", "Unnamed: 0"}


def _drop_excel_index_columns(df: pd.DataFrame) -> pd.DataFrame:
    """删除 Excel 中多出来的索引列。

    当前附件的持仓表和业绩表第一列是空白列，读取后列名类似 '   '。
    这类列不是业务字段，应该删掉。
    """
    cols_to_drop = [c for c in df.columns if str(c).strip() in DROP_INDEX_COL_NAMES]
    return df.drop(columns=cols_to_drop, errors="ignore")


def standardize_fund_code(value: Any) -> str | None:
    """统一基金代码。

    例子：
    - '001623.OF' -> '001623'
    - 162411 -> '162411'
    - 44 -> '000044'

    这样规模、持仓、业绩三张表才能稳定 join。
    """
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    # 去掉 .OF / .SH / .SZ 等后缀。
    text = text.split(".")[0]
    # 如果是纯数字，补足 6 位。
    if text.isdigit():
        text = text.zfill(6)
    return text


def standardize_security_code(value: Any) -> str | None:
    """统一股票代码/证券代码。

    股票代码既可能是 A 股 6 位数字，也可能是海外证券代码，如 U20825C104。
    因此这里不强制补零，只做字符串清洗。
    """
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def standardize_date(value: Any) -> str | None:
    """统一日期为 YYYY-MM-DD 字符串，方便 SQLite 查询。"""
    if pd.isna(value):
        return None
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.strftime("%Y-%m-%d")


def to_float(value: Any) -> float | None:
    """安全转浮点数。"""
    if pd.isna(value):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _standardize_fund_code_series(series: pd.Series) -> pd.Series:
    """向量化统一基金代码，比逐行 map 快很多。"""
    s = series.astype("string").str.strip().str.split(".").str[0]
    s = s.where(~s.isin(["", "<NA>", "nan", "None"]))
    numeric_mask = s.str.fullmatch(r"\d+", na=False)
    s = s.where(~numeric_mask, s.str.zfill(6))
    return s


def _standardize_date_series(series: pd.Series) -> pd.Series:
    """向量化统一日期。"""
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


def clean_size_df(raw: pd.DataFrame) -> pd.DataFrame:
    """清洗规模表。"""
    df = _drop_excel_index_columns(raw).copy()
    rename_map = {
        "日期": "date",
        "基金代码": "fund_code",
        "基金名称": "fund_name",
        "基金公司": "fund_company",
        "wind一级分类": "wind_level1",
        "wind二级分类": "wind_level2",
        "wind三级分类": "wind_level3",
        "资产类型": "asset_type",
        "基金规模": "fund_size",
    }
    df = df.rename(columns=rename_map)
    df["date"] = _standardize_date_series(df["date"])
    df["fund_code"] = _standardize_fund_code_series(df["fund_code"])
    df["fund_size"] = pd.to_numeric(df["fund_size"], errors="coerce")
    return df[list(rename_map.values())].dropna(subset=["date", "fund_code"])


def clean_holding_df(raw: pd.DataFrame) -> pd.DataFrame:
    """清洗持仓表。"""
    df = _drop_excel_index_columns(raw).copy()
    rename_map = {
        "日期": "date",
        "基金代码": "fund_code",
        "股票代码": "stock_code",
        "股票名称": "stock_name",
        "持仓数量": "holding_quantity",
        "持仓规模": "holding_value",
        "占基金净值比例": "nav_ratio",
    }
    df = df.rename(columns=rename_map)
    df["date"] = _standardize_date_series(df["date"])
    df["fund_code"] = _standardize_fund_code_series(df["fund_code"])
    df["stock_code"] = df["stock_code"].astype("string").str.strip()
    df["holding_quantity"] = pd.to_numeric(df["holding_quantity"], errors="coerce")
    df["holding_value"] = pd.to_numeric(df["holding_value"], errors="coerce")
    df["nav_ratio"] = pd.to_numeric(df["nav_ratio"], errors="coerce")
    return df[list(rename_map.values())].dropna(subset=["date", "fund_code", "stock_code"])


def clean_performance_df(raw: pd.DataFrame) -> pd.DataFrame:
    """清洗业绩表。"""
    df = _drop_excel_index_columns(raw).copy()
    rename_map = {
        "基金代码": "fund_code",
        "基金名称": "fund_name",
        "区间": "period",
        "组合收益率": "portfolio_return",
        "基准收益率": "benchmark_return",
        "超额收益": "excess_return",
        "最大回撤": "max_drawdown",
    }
    df = df.rename(columns=rename_map)
    df["fund_code"] = _standardize_fund_code_series(df["fund_code"])
    for col in ["portfolio_return", "benchmark_return", "excess_return", "max_drawdown"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[list(rename_map.values())].dropna(subset=["fund_code"])
