"""SQLite 表结构定义。

生产项目里，数据 schema 应该集中维护，而不是散落在各个工具里。
"""

CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS fund_size (
        date TEXT NOT NULL,
        fund_code TEXT NOT NULL,
        fund_name TEXT,
        fund_company TEXT,
        wind_level1 TEXT,
        wind_level2 TEXT,
        wind_level3 TEXT,
        asset_type TEXT,
        fund_size REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_holding (
        date TEXT NOT NULL,
        fund_code TEXT NOT NULL,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        holding_quantity REAL,
        holding_value REAL,
        nav_ratio REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_performance (
        fund_code TEXT NOT NULL,
        fund_name TEXT,
        period TEXT,
        portfolio_return REAL,
        benchmark_return REAL,
        excess_return REAL,
        max_drawdown REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_memory (
        session_id TEXT PRIMARY KEY,
        context_json TEXT NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        query TEXT NOT NULL,
        plan_json TEXT,
        result_summary TEXT,
        context_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_archives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
]

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_size_date_asset ON fund_size(date, asset_type);",
    "CREATE INDEX IF NOT EXISTS idx_size_company ON fund_size(fund_company);",
    "CREATE INDEX IF NOT EXISTS idx_size_code_date ON fund_size(fund_code, date);",
    "CREATE INDEX IF NOT EXISTS idx_holding_date_code ON fund_holding(date, fund_code);",
    "CREATE INDEX IF NOT EXISTS idx_holding_stock ON fund_holding(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_perf_period_code ON fund_performance(period, fund_code);",
    "CREATE INDEX IF NOT EXISTS idx_turns_session_time ON conversation_turns(session_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_archives_time ON conversation_archives(created_at);",
]
