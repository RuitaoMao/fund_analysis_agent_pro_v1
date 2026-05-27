"""表格处理工具。

Agent 的 tools 通常返回 DataFrame，但 LLM / JSON / trace 更适合接收普通 dict/list。
这个文件负责 DataFrame 与 markdown / JSON-friendly rows 之间的转换。
"""

from __future__ import annotations

from typing import Any
import pandas as pd


def df_to_records(df: pd.DataFrame, max_rows: int | None = None) -> list[dict[str, Any]]:
    """把 DataFrame 转成 list[dict]，方便进入 ToolResult。"""
    if max_rows is not None:
        df = df.head(max_rows)
    # NaN 不能很好地 JSON 序列化，所以统一转成 None。
    safe_df = df.where(pd.notnull(df), None)
    return safe_df.to_dict(orient="records")


def records_to_markdown(records: list[dict[str, Any]], max_rows: int = 20) -> str:
    """把 list[dict] 转成 markdown 表格。

    这里额外做了展示格式优化：
    - 基金代码保留前导 0；
    - 持仓规模/基金规模用千分位；
    - 收益率/回撤/占比保留两位小数。
    """
    if not records:
        return "（无数据）"
    df = pd.DataFrame(records).head(max_rows).copy()

    for col in df.columns:
        if col == "基金代码":
            df[col] = df[col].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        elif "股票代码" in col:
            df[col] = df[col].astype(str).str.replace(r"\.0$", "", regex=True)
        elif "规模" in col:
            df[col] = df[col].map(lambda x: format_number(x, 2))
        elif any(key in col for key in ["收益率", "超额收益", "最大回撤", "规模占比"]):
            df[col] = df[col].map(lambda x: format_number(x, 2))
        elif "数量" in col:
            df[col] = df[col].map(lambda x: format_number(x, 0))

    return df.to_markdown(index=False, disable_numparse=True)


def format_percent(value: Any) -> str:
    """把小数收益率格式化成百分比。"""
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return str(value)


def format_number(value: Any, digits: int = 2) -> str:
    """格式化普通数字。"""
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return str(value)
