"""项目配置。

生产级项目里，路径、模型、数据库地址、运行模式都不应该散落在代码各处。
这个文件负责集中读取和管理配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


def _env_optional(name: str) -> str | None:
    """读取可选环境变量，自动去掉空白和占位值。"""
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    if not value or value.lower() in {"none", "null", "your_openai_api_key", "your_deepseek_api_key"}:
        return None
    return value


def _env_optional_url(name: str, default: str | None = None) -> str | None:
    """读取可选 URL，并提前拦截缺少协议的配置。"""
    raw_value = os.getenv(name)
    value = _env_optional(name)
    if value is None and raw_value is not None:
        # OpenAI SDK 会自己读取 OPENAI_BASE_URL 等环境变量。
        # 如果 .env 写了 OPENAI_BASE_URL=，dotenv 会注入空字符串，SDK 会把它当成非法 base_url。
        os.environ.pop(name, None)
    value = value or default
    if value is None:
        return None
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"{name} 必须以 http:// 或 https:// 开头；官方 OpenAI 可留空。当前值：{value}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


@dataclass
class Settings:
    """全局配置对象。

    注意：这里用 dataclass 是为了教学更直观。
    更复杂的项目也可以使用 pydantic-settings。
    """

    project_root: Path
    raw_data_dir: Path
    processed_data_dir: Path
    sqlite_db_path: Path

    agent_mode: str = "mock"

    planner_provider: str = "openai"
    planner_model: str = "gpt-4.1-nano"
    sql_provider: str | None = None
    sql_model: str | None = None
    report_provider: str = "deepseek"
    report_model: str = "deepseek-chat"

    planner_thinking_enabled: bool = False
    sql_thinking_enabled: bool = False
    report_thinking_enabled: bool = False
    deepseek_reasoning_effort: str = "high"

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    compatible_api_key: str | None = None
    compatible_base_url: str | None = None

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Settings":
        """从 .env 和默认路径加载配置。"""
        root = project_root or Path(__file__).resolve().parents[1]
        root = root.resolve()
        load_dotenv(root / ".env")

        raw_dir = root / "data" / "raw"
        processed_dir = root / "data" / "processed"
        # 支持环境变量展开，如 %LOCALAPPDATA%、$HOME 等，便于把 DB 放到 OneDrive 以外的位置。
        db_path = Path(os.path.expandvars(os.getenv("SQLITE_DB_PATH", "data/processed/fund_agent.db")))
        if not db_path.is_absolute():
            db_path = root / db_path

        return cls(
            project_root=root,
            raw_data_dir=raw_dir,
            processed_data_dir=processed_dir,
            sqlite_db_path=db_path,
            agent_mode=os.getenv("AGENT_MODE", "mock"),
            planner_provider=os.getenv("PLANNER_PROVIDER", "openai"),
            planner_model=os.getenv("PLANNER_MODEL", "gpt-4.1-nano"),
            sql_provider=os.getenv("SQL_PROVIDER") or None,
            sql_model=os.getenv("SQL_MODEL") or None,
            report_provider=os.getenv("REPORT_PROVIDER", "deepseek"),
            report_model=os.getenv("REPORT_MODEL", "deepseek-chat"),
            planner_thinking_enabled=_env_bool("PLANNER_THINKING_ENABLED", False),
            sql_thinking_enabled=_env_bool("SQL_THINKING_ENABLED", False),
            report_thinking_enabled=_env_bool("REPORT_THINKING_ENABLED", False),
            deepseek_reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip() or "high",
            openai_api_key=_env_optional("OPENAI_API_KEY"),
            openai_base_url=_env_optional_url("OPENAI_BASE_URL"),
            deepseek_api_key=_env_optional("DEEPSEEK_API_KEY"),
            deepseek_base_url=_env_optional_url("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            compatible_api_key=_env_optional("COMPATIBLE_API_KEY"),
            compatible_base_url=_env_optional_url("COMPATIBLE_BASE_URL"),
        )

    def ensure_dirs(self) -> None:
        """确保必要目录存在。"""
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)
        self.processed_data_dir.mkdir(parents=True, exist_ok=True)
