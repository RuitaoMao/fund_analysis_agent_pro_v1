import pytest

from src.config import Settings, _env_optional_url
from src.llm.client import LLMClient


def test_llm_client_provider_config_supports_split_models(project_root):
    settings = Settings.load(project_root)
    settings.planner_provider = "openai"
    settings.planner_model = "gpt-4.1-nano"
    settings.openai_api_key = "openai-key"
    settings.openai_base_url = None
    settings.report_provider = "deepseek"
    settings.report_model = "deepseek-v4-flash"
    settings.deepseek_api_key = "deepseek-key"
    settings.sql_provider = "deepseek"
    settings.sql_model = "deepseek-v4-pro"

    client = LLMClient(settings)

    assert client._provider_config("planner") == ("openai", "gpt-4.1-nano", "openai-key", None)
    assert client._provider_config("sql") == (
        "deepseek",
        "deepseek-v4-pro",
        "deepseek-key",
        "https://api.deepseek.com",
    )
    assert client._provider_config("report") == (
        "deepseek",
        "deepseek-v4-flash",
        "deepseek-key",
        "https://api.deepseek.com",
    )


def test_llm_client_provider_config_supports_compatible(project_root):
    settings = Settings.load(project_root)
    settings.report_provider = "compatible"
    settings.report_model = "qwen-plus"
    settings.compatible_api_key = "compatible-key"
    settings.compatible_base_url = "https://example.test/v1"

    client = LLMClient(settings)

    assert client._provider_config("report") == (
        "compatible",
        "qwen-plus",
        "compatible-key",
        "https://example.test/v1",
    )


def test_llm_client_json_mode_kwargs():
    assert LLMClient._build_response_format_kwargs(True) == {"response_format": {"type": "json_object"}}
    assert LLMClient._build_response_format_kwargs(False) == {}


def test_deepseek_thinking_kwargs(project_root):
    settings = Settings.load(project_root)
    settings.deepseek_reasoning_effort = "high"
    settings.sql_thinking_enabled = True
    settings.planner_thinking_enabled = False
    client = LLMClient(settings)

    assert client._build_thinking_kwargs("deepseek", "deepseek-v4-pro", "sql") == {
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": "high",
    }
    assert client._build_thinking_kwargs("deepseek", "deepseek-v4-pro", "planner") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    assert client._build_thinking_kwargs("openai", "gpt-5.4", "sql") == {}


def test_optional_url_treats_blank_as_none(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "   ")
    assert _env_optional_url("OPENAI_BASE_URL") is None


def test_optional_url_rejects_missing_protocol(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "api.openai.com/v1")
    with pytest.raises(ValueError, match="http:// 或 https://"):
        _env_optional_url("OPENAI_BASE_URL")
