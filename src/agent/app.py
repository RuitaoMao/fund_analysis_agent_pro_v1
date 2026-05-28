"""Agent 应用封装。

main.py 不应该直接知道每个 node 如何创建。
这个文件负责组装依赖：Settings、SQLiteStore、ToolRegistry、LLMClient、Workflow。
"""

from __future__ import annotations

from src.config import Settings
from src.data.sqlite_store import SQLiteStore
from src.llm.client import LLMClient
from src.tools import build_default_registry
from src.agent.planner import PlannerAgent
from src.agent.plan_validator import PlanValidator
from src.agent.executor import ToolExecutorAgent
from src.agent.result_validator import ResultValidator
from src.agent.report_writer import ReportWriterAgent
from src.agent.self_check import SelfCheckAgent
from src.agent.memory import MemoryStore
from src.agent.workflow import FundAgentWorkflow
from src.agent.state import AgentState
from src.agent.run_artifacts import save_run_artifacts
from src.agent.sql_generation import GeneratedSQLAgent


def _fresh_state(
    *,
    query: str,
    session_id: str,
    mode: str,
    sql_mode: str,
    use_long_memory: bool,
    max_steps: int,
) -> AgentState:
    """返回一个全新的 AgentState，**显式把所有 per-run 字段重置为初始值**。

    LangGraph MemorySaver 同一 thread_id 下的 invoke() 会把上一轮 checkpoint
    里的字段合并进来——只有在 initial_state 里显式出现的键才会被覆盖。
    因此需要在这里把 draft_answer / final_answer / tool_result / plan 等全部
    置为 None / [] / 0，防止上一轮残留状态污染新一轮。
    """
    return {
        # ---- 输入 ----
        "query": query,
        "session_id": session_id,
        "mode": mode,
        "sql_mode": sql_mode,
        "use_long_memory": use_long_memory,
        "max_steps": max_steps,
        # ---- 计数 / 控制 ----
        "trace": [],
        "tool_history": [],
        "observations": [],
        "errors": [],
        "retry_count": 0,
        "sql_retry_count": 0,
        "revision_count": 0,
        "next_action": None,
        # ---- Planner 阶段 ----
        "plan": None,
        "tool_route": None,
        "plan_validation": None,
        # ---- Tool 执行阶段 ----
        "tool_result": None,
        "result_validation": None,
        # ---- Report / Self-check 阶段 ----
        "draft_answer": None,
        "self_check": None,
        "final_answer": None,
        # ---- Generated SQL 分支 ----
        "sql_plan": None,
        "generated_sql": None,
        "sql_validation_errors": [],
        # ---- ReAct 闭环 ----
        "hard_fallback_attempted": False,
        "auto_routed_mode": None,
        "complexity_signals": [],
        # ---- Clarify ----
        "pending_clarification": None,
        "clarification_question": None,
    }


class FundAnalysisAgent:
    """基金分析 Agent 总入口。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = SQLiteStore(settings.sqlite_db_path)
        self.registry = build_default_registry()
        self.llm_client = LLMClient(settings)
        self.sql_agent = GeneratedSQLAgent(self.store, self.llm_client)

        self.workflow = FundAgentWorkflow(
            planner=PlannerAgent(self.llm_client),
            plan_validator=PlanValidator(self.registry, self.store),
            executor=ToolExecutorAgent(self.registry, self.store),
            result_validator=ResultValidator(),
            report_writer=ReportWriterAgent(
                self.llm_client,
                store=self.store,
                outliner_enabled=settings.report_outliner_enabled,
            ),
            self_checker=SelfCheckAgent(),
            memory=MemoryStore(self.store),
            sql_agent=self.sql_agent,
        )
        self.checkpointer = None
        try:
            # 启用 in-memory checkpointer，让 clarify 能用 interrupt/resume
            from langgraph.checkpoint.memory import MemorySaver
            self.checkpointer = MemorySaver()
        except ImportError:
            self.checkpointer = None
        try:
            self.app = self.workflow.build_app(checkpointer=self.checkpointer)
            self.used_langgraph = True
        except ModuleNotFoundError:
            # 教学环境可能还没安装 langgraph。正式运行请安装 requirements.txt。
            self.app = None
            self.used_langgraph = False

    def rebuild_database(self) -> dict[str, int]:
        """从 Excel 重建 SQLite 数据库。"""
        self.settings.ensure_dirs()
        return self.store.rebuild_from_excel(self.settings.raw_data_dir)

    def run(
        self,
        query: str,
        *,
        mode: str = "mock",
        session_id: str = "default",
        max_steps: int = 3,
        use_long_memory: bool = True,
        sql_mode: str = "hard",
    ) -> AgentState:
        """执行一次 Agent 工作流。

        若执行中触发 LangGraph interrupt（clarify），返回的 state 中带
        is_interrupted=True 和 clarification_question，调用方应通过 resume() 提交补充。
        """
        self.store.ensure_ready()
        initial_state: AgentState = _fresh_state(
            query=query, session_id=session_id, mode=mode, sql_mode=sql_mode,
            use_long_memory=use_long_memory, max_steps=max_steps,
        )
        config = {"configurable": {"thread_id": session_id}} if self.checkpointer else {}
        state = self._invoke_with_interrupt(initial_state, config)
        state["used_langgraph"] = self.used_langgraph
        state["run_artifacts_dir"] = save_run_artifacts(self.settings.project_root, state)
        return state

    def stream(
        self,
        query: str,
        *,
        mode: str = "mock",
        session_id: str = "default",
        max_steps: int = 3,
        use_long_memory: bool = True,
        sql_mode: str = "hard",
    ):
        """流式生成器：逐节点 yield 事件，便于 UI 实时展示。

        每次 yield 形如 {"type": "node_update", "node": str, "trace": StepTrace, "state": partial}。
        最后一帧 yield {"type": "final", "state": full_state}。
        """
        self.store.ensure_ready()
        initial_state: AgentState = _fresh_state(
            query=query, session_id=session_id, mode=mode, sql_mode=sql_mode,
            use_long_memory=use_long_memory, max_steps=max_steps,
        )
        if self.app is None:
            # 退化：linear runner 不支持流式，整体执行后一次性 yield
            state = self.workflow.run_linear(initial_state)
            yield {"type": "final", "state": state}
            return

        config = {"configurable": {"thread_id": session_id}} if self.checkpointer else {}
        last_state: dict = dict(initial_state)
        try:
            for chunk in (self.app.stream(initial_state, config=config, stream_mode="updates") if config else self.app.stream(initial_state, stream_mode="updates")):
                # chunk: {node_name: updated_state}
                for node_name, partial in chunk.items():
                    if isinstance(partial, dict):
                        last_state.update(partial)
                    new_trace = partial.get("trace", []) if isinstance(partial, dict) else []
                    latest = new_trace[-1] if new_trace else None
                    yield {
                        "type": "node_update",
                        "node": node_name,
                        "trace_step": latest.model_dump() if latest is not None else None,
                        "next_action": last_state.get("next_action"),
                    }
        except Exception as exc:
            if type(exc).__name__ in {"GraphInterrupt", "Interrupt"}:
                interrupt_payload = self._extract_interrupt_payload(exc)
                last_state["is_interrupted"] = True
                last_state["clarification_question"] = interrupt_payload.get("question") or "请补充信息。"
                last_state["final_answer"] = last_state["clarification_question"]
                last_state["pending_clarification"] = interrupt_payload
                yield {"type": "interrupt", "state": last_state}
                return
            raise

        last_state["used_langgraph"] = self.used_langgraph
        last_state["run_artifacts_dir"] = save_run_artifacts(self.settings.project_root, last_state)
        yield {"type": "final", "state": last_state}

    def resume(self, user_response: str, *, session_id: str = "default") -> AgentState:
        """提交用户对 clarification 的补充，graph 从中断点继续执行。"""
        if self.app is None or self.checkpointer is None:
            raise RuntimeError("当前运行环境不支持 interrupt resume（缺少 LangGraph 或 checkpointer）。")
        from langgraph.types import Command
        config = {"configurable": {"thread_id": session_id}}
        state = self._invoke_with_interrupt(Command(resume=user_response), config)
        state["used_langgraph"] = self.used_langgraph
        state["run_artifacts_dir"] = save_run_artifacts(self.settings.project_root, state)
        return state

    def _invoke_with_interrupt(self, initial_input, config: dict) -> AgentState:
        """统一处理 graph.invoke 的 interrupt 出口。"""
        if self.app is None:
            return self.workflow.run_linear(initial_input if isinstance(initial_input, dict) else {})
        try:
            state = self.app.invoke(initial_input, config=config) if config else self.app.invoke(initial_input)
        except Exception as exc:
            # LangGraph 0.2+ 用 GraphInterrupt 异常表达 interrupt；不同版本类名略不同
            if type(exc).__name__ in {"GraphInterrupt", "Interrupt"}:
                interrupt_payload = self._extract_interrupt_payload(exc)
                snapshot = self.app.get_state(config) if config else None
                values = dict(snapshot.values) if snapshot and snapshot.values else {}
                values["is_interrupted"] = True
                values["clarification_question"] = interrupt_payload.get("question") or "请补充更多信息。"
                values["final_answer"] = values["clarification_question"]
                values["pending_clarification"] = interrupt_payload
                return values
            raise
        # 正常完成；如果 graph 仍处于中断状态（某些版本不抛异常而把中断写进 state）
        if config and self.checkpointer:
            snapshot = self.app.get_state(config)
            if snapshot and getattr(snapshot, "next", None):
                # 还有未完成节点 → 处于 interrupt 等待中
                pending = getattr(snapshot, "tasks", None) or []
                payload = self._extract_interrupt_from_snapshot(pending)
                if payload:
                    state["is_interrupted"] = True
                    state["clarification_question"] = payload.get("question") or "请补充更多信息。"
                    state["final_answer"] = state["clarification_question"]
                    state["pending_clarification"] = payload
        return state

    @staticmethod
    def _extract_interrupt_payload(exc: Exception) -> dict:
        # GraphInterrupt.args 通常携带 interrupt 时的 payload
        if exc.args:
            value = exc.args[0]
            if isinstance(value, dict):
                return value
            if isinstance(value, (list, tuple)) and value and isinstance(value[0], dict):
                return value[0]
        return {}

    @staticmethod
    def _extract_interrupt_from_snapshot(tasks) -> dict:
        for task in tasks:
            interrupts = getattr(task, "interrupts", None) or []
            for it in interrupts:
                value = getattr(it, "value", None)
                if isinstance(value, dict):
                    return value
        return {}
