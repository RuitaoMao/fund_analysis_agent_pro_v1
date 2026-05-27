"""LLM 客户端封装。

这个文件把不同 provider 的调用细节隔离起来。
Agent 其他模块不应该直接 import OpenAI SDK。
"""

from __future__ import annotations

from typing import Literal

from src.config import Settings


class LLMClient:
    """OpenAI-compatible LLM client。

    DeepSeek 等服务通常也兼容 OpenAI chat completions 格式。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _provider_config(self, role: Literal["planner", "sql", "report", "self_check"]) -> tuple[str, str, str | None, str | None]:
        """根据 role 返回 provider/model/api_key/base_url。"""
        if role == "planner":
            provider = self.settings.planner_provider
            model = self.settings.planner_model
        elif role == "sql":
            provider = self.settings.sql_provider or self.settings.planner_provider
            model = self.settings.sql_model or self.settings.planner_model
        else:
            provider = self.settings.report_provider
            model = self.settings.report_model

        if provider == "deepseek":
            return provider, model, self.settings.deepseek_api_key, self.settings.deepseek_base_url
        if provider == "compatible":
            return provider, model, self.settings.compatible_api_key, self.settings.compatible_base_url
        return provider, model, self.settings.openai_api_key, self.settings.openai_base_url

    def _thinking_enabled(self, role: Literal["planner", "sql", "report", "self_check"]) -> bool:
        if role == "planner":
            return self.settings.planner_thinking_enabled
        if role == "sql":
            return self.settings.sql_thinking_enabled
        if role == "report":
            return self.settings.report_thinking_enabled
        return False

    def chat(
        self,
        *,
        role: Literal["planner", "sql", "report", "self_check"],
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> str:
        """调用 LLM 并返回文本。

        注意：教学项目默认用 mock，不需要调用这里。
        """
        provider, model, api_key, base_url = self._provider_config(role)
        if not api_key:
            raise RuntimeError(f"缺少 {provider} API key，请检查 .env。")

        from openai import OpenAI

        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        kwargs = self._build_response_format_kwargs(json_mode)
        thinking_kwargs = self._build_thinking_kwargs(provider, model, role)
        kwargs.update(thinking_kwargs)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                **({} if thinking_kwargs else {"temperature": temperature}),
                **kwargs,
            )
        except Exception:
            # 有些 OpenAI-compatible 服务声明兼容，但不支持 response_format。
            # Planner 后续仍会用 extract_json_object 做兜底解析。
            if not json_mode:
                raise
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                **({} if thinking_kwargs else {"temperature": temperature}),
                **thinking_kwargs,
            )
        return response.choices[0].message.content or ""

    @staticmethod
    def _build_response_format_kwargs(json_mode: bool) -> dict:
        """构建 JSON 输出参数，便于测试和兼容服务降级。"""
        if not json_mode:
            return {}
        return {"response_format": {"type": "json_object"}}

    def _build_thinking_kwargs(
        self,
        provider: str,
        model: str,
        role: Literal["planner", "sql", "report", "self_check"],
    ) -> dict:
        """DeepSeek V4 Pro thinking 参数。

        .env 只负责开关；这里把开关转换成 API 请求参数。
        """
        if provider != "deepseek" or "v4-pro" not in model:
            return {}
        enabled = self._thinking_enabled(role)
        kwargs = {"extra_body": {"thinking": {"type": "enabled" if enabled else "disabled"}}}
        if enabled:
            kwargs["reasoning_effort"] = self.settings.deepseek_reasoning_effort
        return kwargs
