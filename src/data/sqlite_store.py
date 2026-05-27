"""SQLite 数据层。

这个文件把 Excel 数据转成 SQLite 表，并提供统一 query 接口。
Tools 不应该自己连接数据库，而是通过 SQLiteStore 查询。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any
import pandas as pd

# 全局写锁：保证同一进程内所有 SQLiteStore 实例的写操作串行执行。
# 防止 LangGraph 多线程并发写时 SQLite 出现 "database is locked"。
_GLOBAL_WRITE_LOCK = threading.Lock()

from src.data.loader import ExcelDataLoader
from src.data.schema import CREATE_TABLES_SQL, CREATE_INDEXES_SQL


class SQLiteStore:
    """本地 SQLite 数据库封装。

    支持：
    - check_same_thread=False，多线程并发只读安全。
    - 简单 LRU 查询缓存，避免同一 session 重复查 SQL。
    """

    QUERY_CACHE_SIZE = 256

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._query_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._cache_lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        """创建连接。

        check_same_thread=False：允许跨线程使用同一连接，便于 FastAPI 多 tab 并发。
        timeout=30：等待锁释放最多 30 秒，避免 LangGraph 多线程调度时的"database is locked"。
        WAL 模式（尽力设置）：允许多读单写并发，大幅降低锁冲突；若 OneDrive 等持有文件锁则跳过。
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            # 数据库已被锁定（如 OneDrive 同步），跳过 PRAGMA 设置；
            # timeout=30 保证后续操作有足够等待时间。
            pass
        return conn

    def _cache_key(self, sql: str, params: Any) -> str:
        try:
            params_repr = json.dumps(params, sort_keys=True, default=str)
        except Exception:
            params_repr = repr(params)
        return f"{sql}||{params_repr}"

    def _cache_get(self, key: str) -> pd.DataFrame | None:
        with self._cache_lock:
            if key in self._query_cache:
                self._query_cache.move_to_end(key)
                return self._query_cache[key].copy()
        return None

    def _cache_put(self, key: str, df: pd.DataFrame) -> None:
        with self._cache_lock:
            self._query_cache[key] = df.copy()
            if len(self._query_cache) > self.QUERY_CACHE_SIZE:
                self._query_cache.popitem(last=False)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._query_cache.clear()

    def initialize_schema(self) -> None:
        """创建表和索引。"""
        with _GLOBAL_WRITE_LOCK:
            conn = self.connect()
            try:
                for sql in CREATE_TABLES_SQL:
                    conn.execute(sql)
                for sql in CREATE_INDEXES_SQL:
                    conn.execute(sql)
                conn.commit()
            finally:
                conn.close()

    def rebuild_from_excel(self, raw_data_dir: Path) -> dict[str, int]:
        """从 Excel 重建 SQLite 数据库。

        这一步相当于一个轻量 ETL：Excel -> clean DataFrame -> SQLite。
        """
        loader = ExcelDataLoader(raw_data_dir)
        clean_tables = loader.load_clean_data()

        with _GLOBAL_WRITE_LOCK:
            conn = self.connect()
            try:
                for table in ["fund_size", "fund_holding", "fund_performance"]:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.commit()
            finally:
                conn.close()

        self.initialize_schema()
        row_counts: dict[str, int] = {}
        with _GLOBAL_WRITE_LOCK:
            conn = self.connect()
            try:
                for table_name, df in clean_tables.items():
                    df.to_sql(table_name, conn, if_exists="append", index=False)
                    row_counts[table_name] = len(df)
                conn.commit()
            finally:
                conn.close()

        self.clear_cache()

        # 在主表写入后重建市场快照（free context for analytical reports）。
        # 局部 import 避免数据层和 market_snapshot 形成循环依赖。
        try:
            from src.data.market_snapshot import rebuild_market_snapshot
            snapshot_rows = rebuild_market_snapshot(self)
            row_counts["market_snapshot"] = snapshot_rows
        except Exception as exc:
            # 快照失败不影响主流程，记入 row_counts 以便诊断。
            row_counts["market_snapshot"] = -1
            row_counts["market_snapshot_error"] = str(exc)[:120]  # type: ignore[assignment]

        return row_counts

    def query_df(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> pd.DataFrame:
        """执行 SQL 并返回 DataFrame，带 LRU 缓存。

        缓存只对 SELECT 类查询有效；表数据更新（rebuild_from_excel）会重置缓存。
        显式关闭连接（SQLite context manager 只做 commit/rollback，不关闭），
        防止连接积累导致写锁超时。
        """
        cache_key = self._cache_key(sql, params)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        conn = self.connect()
        try:
            df = pd.read_sql_query(sql, conn, params=params)
        finally:
            conn.close()
        self._cache_put(cache_key, df)
        return df

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> None:
        """执行不返回结果的 SQL。写操作会清缓存。

        使用全局写锁保证同进程内写操作串行执行，
        配合 timeout=30 应对跨进程（OneDrive 等）的短暂锁定。
        显式关闭连接防止锁泄漏。
        """
        with _GLOBAL_WRITE_LOCK:
            conn = self.connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()
        self.clear_cache()

    def table_row_count(self, table_name: str) -> int:
        """获取某张表行数。"""
        df = self.query_df(f"SELECT COUNT(*) AS cnt FROM {table_name}")
        return int(df.iloc[0]["cnt"])

    def ensure_ready(self) -> None:
        """检查数据库是否已经可用。"""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"SQLite 数据库不存在：{self.db_path}\n"
                "请先运行 main.py --rebuild-db。"
            )
        # 简单检查核心表是否有数据。
        try:
            count = self.table_row_count("fund_size")
        except Exception as exc:
            raise RuntimeError("数据库结构不完整，请重新运行 --rebuild-db。") from exc
        if count == 0:
            raise RuntimeError("fund_size 表为空，请重新运行 --rebuild-db。")

    def max_date(self, table_name: str) -> str | None:
        """获取某张表的最新日期。"""
        df = self.query_df(f"SELECT MAX(date) AS max_date FROM {table_name}")
        value = df.iloc[0]["max_date"]
        return None if pd.isna(value) else str(value)

    def distinct_values(self, table_name: str, column_name: str) -> list[str]:
        """读取某列的去重值，供 validator 做参数校验。"""
        df = self.query_df(
            f"SELECT DISTINCT {column_name} AS value FROM {table_name} WHERE {column_name} IS NOT NULL"
        )
        return [str(v) for v in df["value"].dropna().tolist()]

    def date_exists(self, table_name: str, date: str) -> bool:
        """检查某个日期是否存在于指定业务表。"""
        df = self.query_df(
            f"SELECT 1 AS hit FROM {table_name} WHERE date = :date LIMIT 1",
            {"date": date},
        )
        return not df.empty

    def resolve_company_names(self, companies: list[str]) -> tuple[list[str], list[str]]:
        """把用户输入的公司简称匹配到数据库中的基金公司名称。

        返回值为 (matched, missing)。matched 保留用户输入简称，工具内部仍用 LIKE 查询。
        """
        existing = self.distinct_values("fund_size", "fund_company")
        matched: list[str] = []
        missing: list[str] = []
        for company in companies:
            text = str(company).strip()
            if any(text in item or item in text for item in existing):
                matched.append(text)
            else:
                missing.append(text)
        return matched, missing
