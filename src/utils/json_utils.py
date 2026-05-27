"""JSON 辅助函数。"""

from __future__ import annotations

import json
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 输出里提取 JSON object。

    生产中更推荐使用 structured output / tool calling。
    这里保留该函数是为了容错：如果模型把 JSON 外面包了文字，也尽量解析。
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def to_pretty_json(obj: Any) -> str:
    """美化 JSON，方便 trace / debug。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)
