"""运行产物保存。

保存 trace/tool_result/answer，便于调试、复盘和演示。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def save_run_artifacts(project_root: Path, state: dict[str, Any]) -> str:
    """把一次运行的关键产物保存到 outputs/runs。"""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = project_root / "outputs" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    serializable = {
        "query": state.get("query"),
        "session_id": state.get("session_id"),
        "mode": state.get("mode"),
        "plan": _dump_model(state.get("plan")),
        "plan_validation": _dump_model(state.get("plan_validation")),
        "tool_result": _dump_model(state.get("tool_result")),
        "result_validation": _dump_model(state.get("result_validation")),
        "self_check": _dump_model(state.get("self_check")),
        "tool_history": state.get("tool_history", []),
        "observations": state.get("observations", []),
        "trace": [_dump_model(item) for item in state.get("trace", [])],
        "errors": state.get("errors", []),
    }
    (run_dir / "run.json").write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "answer.md").write_text(state.get("final_answer", ""), encoding="utf-8")
    return str(run_dir)


def _dump_model(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value
