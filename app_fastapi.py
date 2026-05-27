"""FastAPI 前端入口（替代 Streamlit）。

启动：
    uvicorn app_fastapi:app --host 127.0.0.1 --port 8000 --workers 1

特性：
    - 多 tab 真并发：FastAPI 基于 asyncio，多个浏览器窗口同时提问互不阻塞。
    - SSE 流式 trace：实时展示 planner / validator / executor 各节点的观察。
    - 区分"新问题"和"继续会话"：clarify 后用户提交补充走 /api/resume，原 query 自动合并。
    - 单进程内共享 Agent 实例，SQLite 通过 check_same_thread=False 跨线程读写。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent.app import FundAnalysisAgent
from src.agent.html_report import save_html_report
from src.config import Settings


logger = logging.getLogger("app_fastapi")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR = PROJECT_ROOT / "outputs" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _build_agent() -> FundAnalysisAgent:
    settings = Settings.load(PROJECT_ROOT)
    settings.ensure_dirs()
    return FundAnalysisAgent(settings)


# 单例 Agent — 所有请求共用，SQLite 已开启 check_same_thread=False
AGENT: FundAnalysisAgent = _build_agent()

# 跟踪每个 session 的 interrupt 状态，决定 ask 走 run 还是 resume
SESSION_PENDING_CLARIFICATION: dict[str, dict] = {}
SESSION_LAST_STATE: dict[str, dict] = {}


app = FastAPI(title="基金分析 Agent", version="1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")


class AskRequest(BaseModel):
    query: str
    session_id: str = Field(default_factory=lambda: f"web-{uuid4().hex[:8]}")
    mode: str = "llm"
    sql_mode: str = "generated"
    max_steps: int = 3
    use_long_memory: bool = True


class ResumeRequest(BaseModel):
    user_response: str
    session_id: str


def _state_to_payload(state: dict) -> dict:
    """剥离 state 里 LangChain/Pydantic 不可 JSON 化的部分，转成前端友好的 dict。

    先把 Pydantic 模型 model_dump()，再用 json.dumps(default=str) + json.loads
    做一次 JSON 往返，保证返回值里每个字段都是原生 Python 类型，后续
    json.dumps 不再需要 default=str。
    """
    out: dict = {}
    for k, v in state.items():
        if hasattr(v, "model_dump"):
            out[k] = v.model_dump()
        elif isinstance(v, list):
            out[k] = [item.model_dump() if hasattr(item, "model_dump") else item for item in v]
        else:
            out[k] = v
    # 往返序列化：default=str 兜底处理 Path / datetime / enum 等特殊类型
    payload = json.loads(json.dumps(out, default=str))
    # 把绝对路径转成浏览器可访问的相对 URL（/reports/<filename>）
    if payload.get("html_report_path"):
        fname = Path(payload["html_report_path"]).name
        payload["html_report_url"] = f"/reports/{fname}"
    return payload


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "used_langgraph": AGENT.used_langgraph}


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict[str, Any]:
    """非流式提问。Clarify 时返回 needs_clarification=True 和 question。"""
    is_resuming = req.session_id in SESSION_PENDING_CLARIFICATION

    def _run() -> dict:
        if is_resuming:
            state = AGENT.resume(req.user_response if hasattr(req, "user_response") else req.query, session_id=req.session_id)
        else:
            state = AGENT.run(
                req.query,
                mode=req.mode,
                sql_mode=req.sql_mode,
                session_id=req.session_id,
                use_long_memory=req.use_long_memory,
                max_steps=req.max_steps,
            )
        return state

    state = await asyncio.to_thread(_run)
    state["sql_mode"] = req.sql_mode

    if state.get("is_interrupted"):
        SESSION_PENDING_CLARIFICATION[req.session_id] = state.get("pending_clarification", {})
        payload = _state_to_payload(state)
        payload["needs_clarification"] = True
        payload["clarification_question"] = state.get("clarification_question")
        SESSION_LAST_STATE[req.session_id] = payload
        return payload

    SESSION_PENDING_CLARIFICATION.pop(req.session_id, None)
    # 保存 HTML 报告
    try:
        path = save_html_report(AGENT.settings.project_root, state)
        state["html_report_path"] = str(path)
    except Exception as exc:
        logger.warning("save_html_report failed: %s", exc)

    payload = _state_to_payload(state)
    SESSION_LAST_STATE[req.session_id] = payload
    return payload


@app.post("/api/resume")
async def resume(req: ResumeRequest) -> dict[str, Any]:
    """提交对 clarification 的补充答复。"""
    if req.session_id not in SESSION_PENDING_CLARIFICATION:
        raise HTTPException(status_code=400, detail="当前 session 没有待补充的 clarification。")

    def _run() -> dict:
        return AGENT.resume(req.user_response, session_id=req.session_id)

    state = await asyncio.to_thread(_run)

    if state.get("is_interrupted"):
        # 再次中断（用户回答仍不充分）
        SESSION_PENDING_CLARIFICATION[req.session_id] = state.get("pending_clarification", {})
        payload = _state_to_payload(state)
        payload["needs_clarification"] = True
        payload["clarification_question"] = state.get("clarification_question")
        return payload

    SESSION_PENDING_CLARIFICATION.pop(req.session_id, None)
    try:
        path = save_html_report(AGENT.settings.project_root, state)
        state["html_report_path"] = str(path)
    except Exception as exc:
        logger.warning("save_html_report failed: %s", exc)

    payload = _state_to_payload(state)
    SESSION_LAST_STATE[req.session_id] = payload
    return payload


@app.get("/api/stream")
async def stream(
    query: str,
    session_id: str,
    mode: str = "llm",
    sql_mode: str = "generated",
    max_steps: int = 3,
    use_long_memory: bool = True,
) -> StreamingResponse:
    """SSE 流式接口。每个节点完成就推一条 event，UI 实时展示思考过程。"""

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def producer():
            try:
                for chunk in AGENT.stream(
                    query,
                    mode=mode,
                    sql_mode=sql_mode,
                    session_id=session_id,
                    use_long_memory=use_long_memory,
                    max_steps=max_steps,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:  # pragma: no cover
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(exc)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "__end__"})

        task = asyncio.create_task(asyncio.to_thread(producer))

        try:
            while True:
                chunk = await queue.get()
                if chunk.get("type") == "__end__":
                    break
                if chunk.get("type") == "interrupt":
                    SESSION_PENDING_CLARIFICATION[session_id] = chunk["state"].get("pending_clarification", {})
                    SESSION_LAST_STATE[session_id] = _state_to_payload(chunk["state"])
                    yield f"data: {json.dumps({'type': 'interrupt', 'question': chunk['state'].get('clarification_question')}, ensure_ascii=False)}\n\n"
                    break
                if chunk.get("type") == "final":
                    final_state = chunk["state"]
                    SESSION_PENDING_CLARIFICATION.pop(session_id, None)
                    try:
                        path = save_html_report(AGENT.settings.project_root, final_state)
                        final_state["html_report_path"] = str(path)
                    except Exception:
                        pass
                    try:
                        payload = _state_to_payload(final_state)
                        SESSION_LAST_STATE[session_id] = payload
                        yield f"data: {json.dumps({'type': 'final', 'state': payload}, ensure_ascii=False)}\n\n"
                    except Exception as serial_exc:
                        logger.exception("final state 序列化失败: %s", serial_exc)
                        yield f"data: {json.dumps({'type': 'error', 'message': f'序列化失败: {serial_exc}'}, ensure_ascii=False)}\n\n"
                    break
                # node_update / error
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
        finally:
            await task

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "needs_clarification": session_id in SESSION_PENDING_CLARIFICATION,
        "pending": SESSION_PENDING_CLARIFICATION.get(session_id, {}),
        "last_state": SESSION_LAST_STATE.get(session_id),
    }


@app.delete("/api/session/{session_id}")
async def reset_session(session_id: str) -> dict[str, Any]:
    SESSION_PENDING_CLARIFICATION.pop(session_id, None)
    SESSION_LAST_STATE.pop(session_id, None)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app_fastapi:app", host="127.0.0.1", port=8000, reload=False)
