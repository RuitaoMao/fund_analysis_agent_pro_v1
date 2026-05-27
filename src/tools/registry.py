"""Tool Registry。

生产级 Agent 不应该在 executor 里写一堆 if/else 到处找工具。
Registry 集中维护：工具名 -> 工具函数、工具说明。
"""

from __future__ import annotations

from typing import Callable

from src.agent.schemas import ToolResult
from src.data.sqlite_store import SQLiteStore
from src.tools.arg_schemas import TOOL_ARG_SCHEMAS
from src.tools.specs import TOOL_SPECS, ToolSpec

ToolFunction = Callable[[SQLiteStore, dict], ToolResult]


class ToolRegistry:
    """工具注册表。"""

    def __init__(self):
        self._tools: dict[str, ToolFunction] = {}
        self._specs: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}

    def register(self, name: str, func: ToolFunction) -> None:
        if name not in self._specs:
            raise ValueError(f"工具 {name} 没有对应 ToolSpec，请先在 specs.py 中定义。")
        self._tools[name] = func

    def exists(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolFunction:
        if name not in self._tools:
            raise KeyError(f"工具未注册：{name}")
        return self._tools[name]

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def validate_args(self, name: str, args: dict) -> dict:
        """用 Pydantic schema 校验并规范化工具参数。"""
        schema = TOOL_ARG_SCHEMAS.get(name)
        if schema is None:
            return dict(args)
        return schema.model_validate(args).model_dump()

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def execute(self, name: str, store: SQLiteStore, args: dict) -> ToolResult:
        """执行白名单工具。"""
        func = self.get(name)
        return func(store, args)
