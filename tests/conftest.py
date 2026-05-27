from pathlib import Path

import pytest

from src.config import Settings
from src.data.sqlite_store import SQLiteStore


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def temp_store(tmp_path_factory, project_root: Path) -> SQLiteStore:
    """使用临时 SQLite，避免测试污染项目默认数据库。"""
    db_path = tmp_path_factory.mktemp("db") / "fund_agent_test.db"
    store = SQLiteStore(db_path)
    store.rebuild_from_excel(project_root / "data" / "raw")
    return store


@pytest.fixture()
def temp_settings(temp_store: SQLiteStore, project_root: Path) -> Settings:
    settings = Settings.load(project_root)
    settings.sqlite_db_path = temp_store.db_path
    return settings
