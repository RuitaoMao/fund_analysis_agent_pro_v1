"""市场快照：预计算的全市场聚合数据，作为分析报告的免费上下文。

设计目标：
- 每次 SQLiteStore.rebuild_from_excel() 末尾自动重建。
- 每个 snapshot_date 一行 payload_json，包含 market_total / asset_type_breakdown / top_companies。
- 报告写作器调用 load_market_snapshot() 注入到 prompt 上下文，
  这样竞争格局/公司对比类报告即使 planner 没显式调用 query_market_overview，
  也能拿到市场参照数据。
"""

from __future__ import annotations

import json
from typing import Any

from src.data.sqlite_store import SQLiteStore


MARKET_SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_snapshot (
    snapshot_date TEXT NOT NULL PRIMARY KEY,
    payload_json TEXT NOT NULL,
    refreshed_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""".strip()


def _df_records(df) -> list[dict[str, Any]]:
    return [{str(k): _coerce(v) for k, v in row.items()} for row in df.to_dict("records")]


def _coerce(v):
    if v is None:
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _compute_snapshot_for_date(store: SQLiteStore, date: str, top_n: int) -> dict[str, Any]:
    """计算单个 snapshot_date 的市场聚合数据。"""
    params = {"_date": date, "_top_n": top_n}

    df_total = store.query_df(
        """
        SELECT :_date AS 数据日期,
               COUNT(DISTINCT fund_code) AS 基金总数,
               COUNT(DISTINCT fund_company) AS 基金公司数,
               ROUND(SUM(fund_size), 2) AS 全市场总规模_亿
        FROM fund_size WHERE date = :_date
        """,
        params,
    )

    df_asset = store.query_df(
        """
        SELECT asset_type AS 资产类型,
               COUNT(DISTINCT fund_code) AS 基金数量,
               ROUND(SUM(fund_size), 2) AS 规模_亿,
               ROUND(
                   SUM(fund_size) / NULLIF(
                       (SELECT SUM(fund_size) FROM fund_size WHERE date = :_date), 0
                   ) * 100, 1
               ) AS 市场占比_pct
        FROM fund_size WHERE date = :_date
        GROUP BY asset_type
        ORDER BY SUM(fund_size) DESC
        """,
        params,
    )

    df_top = store.query_df(
        """
        SELECT fund_company AS 基金公司,
               COUNT(DISTINCT fund_code) AS 基金数量,
               ROUND(SUM(fund_size), 2) AS 规模_亿,
               ROUND(
                   SUM(fund_size) / NULLIF(
                       (SELECT SUM(fund_size) FROM fund_size WHERE date = :_date), 0
                   ) * 100, 2
               ) AS 市场份额_pct
        FROM fund_size WHERE date = :_date
        GROUP BY fund_company
        ORDER BY SUM(fund_size) DESC LIMIT :_top_n
        """,
        params,
    )

    return {
        "snapshot_date": date,
        "market_total": _df_records(df_total),
        "size_by_asset_type": _df_records(df_asset),
        "top_companies": _df_records(df_top),
    }


def rebuild_market_snapshot(store: SQLiteStore, top_n: int = 15) -> int:
    """重建全部 snapshot_date 的市场快照。

    返回写入的快照行数。安全可重复调用：会先 DROP+CREATE 表。
    """
    # 先创建表（rebuild_from_excel 调用时 schema 还没建到这里）
    store.execute("DROP TABLE IF EXISTS market_snapshot")
    store.execute(MARKET_SNAPSHOT_TABLE_SQL)

    dates_df = store.query_df("SELECT DISTINCT date FROM fund_size ORDER BY date")
    dates = [str(d) for d in dates_df["date"].tolist() if d]

    n = 0
    for date in dates:
        try:
            payload = _compute_snapshot_for_date(store, date, top_n)
        except Exception:
            continue
        store.execute(
            "INSERT INTO market_snapshot (snapshot_date, payload_json) VALUES (:d, :p)",
            {"d": date, "p": json.dumps(payload, ensure_ascii=False, default=str)},
        )
        n += 1
    return n


def load_market_snapshot(store: SQLiteStore, date: str | None = None) -> dict[str, Any] | None:
    """读取市场快照。date=None 时取最新一期。

    返回 None 表示快照表不存在或为空（例如旧数据库未重建）。
    """
    try:
        if date:
            df = store.query_df(
                "SELECT payload_json FROM market_snapshot WHERE snapshot_date = :d",
                {"d": date},
            )
        else:
            df = store.query_df(
                "SELECT payload_json FROM market_snapshot ORDER BY snapshot_date DESC LIMIT 1"
            )
    except Exception:
        return None
    if df.empty:
        return None
    try:
        return json.loads(df.iloc[0]["payload_json"])
    except (json.JSONDecodeError, ValueError):
        return None
