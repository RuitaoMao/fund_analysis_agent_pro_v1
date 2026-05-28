"""LangGraph workflow。

这个模块把 Agent 拆成清晰的 ReAct-style 节点：
plan -> act -> observe -> reflect -> 必要时 replan/retry。
"""

from __future__ import annotations

import functools
import re
import time
from typing import Any

from src.agent.state import AgentState
from src.agent.schemas import AgentPlan, StepTrace, ToolResult
from src.agent.planner import PlannerAgent
from src.agent.plan_validator import PlanValidator
from src.agent.executor import ToolExecutorAgent
from src.agent.result_validator import ResultValidator
from src.agent.report_writer import ReportWriterAgent
from src.agent.self_check import SelfCheckAgent
from src.agent.memory import MemoryStore
from src.agent.tool_router import ToolRouter
from src.agent.sql_generation import GeneratedSQLAgent, sql_plan_to_agent_plan
from src.utils.table_utils import df_to_records


def _append_trace(state: AgentState, node: str, thought: str, action: str, observation: str) -> AgentState:
    """向 state 追加工程可观测 trace 和 observation。"""
    trace = list(state.get("trace", []))
    trace.append(StepTrace(node=node, thought=thought, action=action, observation=observation))
    observations = list(state.get("observations", []))
    observations.append(f"{node}: {observation}")
    return {**state, "trace": trace, "observations": observations}


def _timed_node(method):
    """装饰器：测量节点墙钟耗时，写入该节点 _append_trace 出来的最后一条 StepTrace.duration_ms。

    所有 *_node 方法都会在 FundAgentWorkflow 类定义后被自动包裹（见文件末尾），
    不需要在每个方法上手动加 @_timed_node。
    """
    @functools.wraps(method)
    def wrapper(self, state):
        t0 = time.perf_counter()
        new_state = method(self, state)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if isinstance(new_state, dict):
            trace = new_state.get("trace") or []
            if trace:
                last = trace[-1]
                try:
                    last.duration_ms = round(elapsed_ms, 1)
                except Exception:
                    pass
        return new_state
    return wrapper


class FundAgentWorkflow:
    """封装 LangGraph workflow 依赖。"""

    def __init__(
        self,
        planner: PlannerAgent,
        plan_validator: PlanValidator,
        executor: ToolExecutorAgent,
        result_validator: ResultValidator,
        report_writer: ReportWriterAgent,
        self_checker: SelfCheckAgent,
        memory: MemoryStore,
        sql_agent: GeneratedSQLAgent | None = None,
    ):
        self.planner = planner
        self.plan_validator = plan_validator
        self.executor = executor
        self.result_validator = result_validator
        self.report_writer = report_writer
        self.self_checker = self_checker
        self.memory = memory
        self.tool_router = ToolRouter()
        self.sql_agent = sql_agent

    def load_context_node(self, state: AgentState) -> AgentState:
        session_id = state.get("session_id", "default")
        memory_context = self.memory.load(session_id, use_long_memory=state.get("use_long_memory", True))
        state = {**state, "memory_context": memory_context, "next_action": "execute"}
        return _append_trace(
            state,
            node="load_context_node",
            thought="读取会话级上下文，用于解析这些基金、刚才、同样口径等指代。",
            action=f"load_memory(session_id={session_id})",
            observation=f"memory keys={list(memory_context.keys())}",
        )

    def mode_router_node(self, state: AgentState) -> AgentState:
        sql_mode = state.get("sql_mode", "hard")
        state = {**state, "next_action": "generated" if sql_mode == "generated" else "hard"}
        return _append_trace(
            state,
            node="mode_router_node",
            thought="根据用户选择的查询引擎，把任务路由到专家工具分支或 LLM 生成 SQL 分支。",
            action="route_by_sql_mode",
            observation=f"sql_mode={sql_mode}, branch={state['next_action']}",
        )

    def planner_node(self, state: AgentState) -> AgentState:
        # C：把上一轮失败反馈拼成 failure_context 给 planner，构成 ReAct 闭环。
        failure_context = self._build_failure_context(state)
        plan = self.planner.plan(
            query=state["query"],
            mode=state.get("mode", "mock"),
            memory_context=state.get("memory_context", {}),
            failure_context=failure_context,
        )
        state = {**state, "plan": plan, "next_action": "execute"}
        marker = "[REACT-REPLAN]" if failure_context else ""
        return _append_trace(
            state,
            node="planner_node",
            thought="把用户问题转换成结构化工具调用计划，重规划时会读取上轮失败反馈。",
            action="PlannerAgent.plan()",
            observation=f"{marker} intent={plan.intent}, tool={plan.tool_name}, args={plan.args}, tool_calls={[c.model_dump() for c in plan.tool_calls]}",
        )

    @staticmethod
    def _build_failure_context(state: AgentState) -> dict:
        """构造给 planner 的失败反馈包。第一次调用 planner 时返回空。"""
        if state.get("retry_count", 0) == 0 and not state.get("errors"):
            return {}
        ctx: dict = {}
        plan_validation = state.get("plan_validation")
        if plan_validation and not plan_validation.passed:
            ctx["plan_validation"] = plan_validation.model_dump()
        result_validation = state.get("result_validation")
        if result_validation and not result_validation.passed:
            ctx["result_validation"] = result_validation.model_dump()
        if state.get("errors"):
            ctx["errors"] = state["errors"][-3:]
        if state.get("tool_history"):
            ctx["tool_history"] = state["tool_history"][-3:]
        return ctx

    def complexity_classifier_node(self, state: AgentState) -> AgentState:
        """E：自动复杂度路由器。

        策略：
        - 用户显式选 hard → 尊重，走 hard 工具路径（B fallback 仍可兜底）。
        - 用户显式选 generated → 尊重。
        - 用户未显式指定（auto）→ 默认走 generated SQL，避免 hard 工具边界问题导致 demo 翻车。
        - 仍然收集复杂度信号用于 trace 可观察性。
        """
        query = state.get("query", "")
        explicit_mode = state.get("sql_mode")  # 来自 UI 的用户选择
        signals: list[str] = []

        import re as _re
        # 复杂度信号（仅用于 trace 记录，不再影响路由决策）
        nested_patterns = ["规模top", "规模前", "top100", "top200", r"中.{1,10}前", r"中.{1,10}最"]
        for pat in nested_patterns:
            if _re.search(pat, query):
                signals.append(f"嵌套排名:{pat}")
                break
        if any(p in query for p in ["同时", "并且", "且.*又", "并满足"]):
            signals.append("多条件且")
        domain_hits = sum(1 for kw_set in [
            ["规模", "份额"],
            ["收益", "业绩", "回撤", "超额"],
            ["持仓", "重仓", "股票"],
        ] if any(kw in query for kw in kw_set))
        if domain_hits >= 2:
            signals.append(f"{domain_hits}域联合")
        if "%" in query and any(p in query for p in ["大于", "超过", "高于", "低于"]):
            signals.append("数值阈值过滤")

        # 路由决策：显式指定 hard/generated 时尊重；否则默认 generated（更稳定）
        if explicit_mode in {"hard", "generated"}:
            final_mode = explicit_mode
            auto_routed = "generated" if signals else "hard"
        else:
            # auto 模式：默认走 generated SQL
            final_mode = "generated"
            auto_routed = "generated"

        state = {
            **state,
            "sql_mode": final_mode,
            "auto_routed_mode": auto_routed,
            "complexity_signals": signals,
        }
        return _append_trace(
            state,
            node="complexity_classifier_node",
            thought="按规则识别查询复杂度；auto 模式默认走 generated SQL，显式指定则尊重用户选择。",
            action="classify_complexity",
            observation=f"explicit={explicit_mode}, signals={signals}, auto={auto_routed}, final={final_mode}",
        )

    def plan_validator_node(self, state: AgentState) -> AgentState:
        validation = self.plan_validator.validate(state["query"], state["plan"])
        next_action = validation.next_action

        # B fallback：hard 模式 plan 校验失败时，**先给 planner 1 次 replan 机会**再降级到 SQL。
        # 之前的早触发版会在第一次失败立刻切到 generated SQL（SQL planner 慢、复杂查询易超时）。
        # 新策略：
        #   - 第 1 次失败：next_action="replan" → 让 planner 看到 issues/hint 重规划
        #   - 第 2 次仍失败：B fallback → generated SQL（保留兜底语义）
        b_fallback = False
        plan_retry_count = int(state.get("plan_retry_count", 0))
        if (
            not validation.passed
            and validation.issues
            and state.get("sql_mode") == "hard"
        ):
            if plan_retry_count == 0:
                next_action = "replan"
                state = {**state, "plan_retry_count": 1}
            elif (
                not state.get("hard_fallback_attempted", False)
                and self.sql_agent is not None
            ):
                b_fallback = True
                next_action = "fallback_sql"
                state = {**state, "hard_fallback_attempted": True, "sql_mode": "generated"}

        state = {**state, "plan_validation": validation, "next_action": next_action}
        return _append_trace(
            state,
            node="plan_validator_node",
            thought="校验工具名、参数 schema、数据库日期/资产类型/公司名和明显语义冲突。",
            action="PlanValidator.validate()",
            observation=(
                f"passed={validation.passed}, next_action={next_action}"
                + (f" [REACT-FALLBACK] b_fallback_triggered=True" if b_fallback else "")
                + (f" [plan_retry={plan_retry_count}]" if plan_retry_count else "")
                + f", issues={validation.issues}, hint={validation.correction_hint}"
            ),
        )

    def tool_router_node(self, state: AgentState) -> AgentState:
        """给当前计划选择工具类别，形成可观测的工具路由结果。"""
        route = self.tool_router.route(state["query"], state["plan"])
        state = {**state, "tool_route": route}
        return _append_trace(
            state,
            node="tool_router_node",
            thought="根据用户问题和 planner 选择的工具，把请求路由到规模、业绩、持仓、公司或跨表分析能力簇。",
            action="ToolRouter.route()",
            observation=(
                f"categories={route['categories']}, planned_tools={route['planned_tools']}, "
                f"allowed_tools_count={len(route['allowed_tools'])}"
            ),
        )

    def executor_node(self, state: AgentState) -> AgentState:
        plan = state["plan"]
        validation = state.get("plan_validation")
        repaired_args = validation.repaired_args if validation else None
        repaired_tool_calls = validation.repaired_tool_calls if validation else None

        # Phase 4 改进：激活 ToolRouter — 校验 plan 用的工具在 allowed_tools 内。
        route = state.get("tool_route") or {}
        allowed = set(route.get("allowed_tools") or [])
        planned_tools = [c.tool_name for c in (repaired_tool_calls or plan.tool_calls or [])] or [plan.tool_name]
        if allowed and not any(t in allowed for t in planned_tools):
            # 越界：直接 replan，把信息塞进 errors 给 planner 反馈
            errors = list(state.get("errors", [])) + [
                f"ToolRouter 限制：工具 {planned_tools} 不在类别 {route.get('categories')} 的 allowed_tools 内。"
            ]
            empty_result = ToolResult(
                tool_name=plan.tool_name,
                intent=plan.intent,
                tables={},
                warnings=[errors[-1]],
                notes=["工具越界，已要求重新规划。"],
            )
            state = {**state, "errors": errors, "tool_result": empty_result, "next_action": "replan"}
            return _append_trace(
                state,
                node="executor_node",
                thought="ToolRouter 守门：planner 选择的工具未在路由白名单内，要求 replan。",
                action="reject_out_of_route_tool",
                observation=f"[REACT-CORRECTION] planned={planned_tools}, allowed={sorted(allowed)[:5]}..., next_action=replan",
            )

        try:
            result = self.executor.execute(plan, repaired_args=repaired_args, repaired_tool_calls=repaired_tool_calls)
            tool_history = list(state.get("tool_history", []))
            if repaired_tool_calls and len(repaired_tool_calls) > 1:
                for call in repaired_tool_calls:
                    tool_history.append({"tool_name": call.tool_name, "args": call.args})
            else:
                tool_history.append(
                    {
                        "tool_name": plan.tool_name,
                        "args": repaired_args or plan.args,
                        "metadata": result.metadata,
                        "warnings": result.warnings,
                    }
                )
            state = {**state, "tool_result": result, "tool_history": tool_history, "next_action": "report"}
            return _append_trace(
                state,
                node="executor_node",
                thought="执行白名单 SQL-backed tool，获得确定性观察结果。",
                action=f"ToolExecutor.execute({result.tool_name})",
                observation=f"[TOOL] tool={result.tool_name}, tables={list(result.tables.keys())}, notes={len(result.notes)}, warnings={len(result.warnings)}",
            )
        except Exception as exc:
            errors = list(state.get("errors", [])) + [str(exc)]
            empty_result = ToolResult(
                tool_name=plan.tool_name,
                intent=plan.intent,
                tables={},
                warnings=[str(exc)],
                notes=["工具执行失败。"],
            )
            state = {**state, "errors": errors, "tool_result": empty_result, "next_action": "replan"}
            return _append_trace(
                state,
                node="executor_node",
                thought="工具执行异常，记录错误并交给反思节点决定是否重试。",
                action=f"ToolExecutor.execute({plan.tool_name})",
                observation=f"[REACT-CORRECTION] tool_error={exc}; next_action=replan",
            )

    def sql_planner_node(self, state: AgentState) -> AgentState:
        if self.sql_agent is None:
            raise RuntimeError("GeneratedSQLAgent 未初始化。")
        if self._query_needs_clarification(state["query"]):
            plan = AgentPlan(
                intent="unknown",
                tool_name="none",
                args={},
                answer_type="clarification",
                need_clarification=True,
                clarification_question="这个问题当前还不是一个明确的数据分析请求。请补充您想看规模、持仓、业绩、基金公司、基金代码、股票或时间口径中的哪些信息。",
                rationale="用户问题缺少可映射到三张数据表的分析对象或指标。",
            )
            state = {**state, "plan": plan, "generated_sql": "", "next_action": "clarify"}
            return _append_trace(
                state,
                node="sql_planner_node",
                thought="识别到问题缺少可执行的数据分析对象，先追问而不是生成默认 SQL。",
                action="precheck_generated_sql_query",
                observation="[REACT-CORRECTION] insufficient_data_intent; next_action=clarify",
            )
        try:
            sql_plan = self.sql_agent._plan(
                state["query"],
                mode=state.get("mode", "mock"),
                previous_errors=state.get("errors", []),
                memory_context=state.get("memory_context"),
            )
        except Exception as exc:
            # Generated SQL 分支遇到非数据问题、LLM 非 JSON 输出或无法规划 SQL 时，
            # 不能把 traceback 暴露给用户，应转成追问/澄清。
            plan = AgentPlan(
                intent="unknown",
                tool_name="none",
                args={},
                answer_type="clarification",
                need_clarification=True,
                clarification_question="这个问题暂时无法转换成可执行的数据查询。请补充您想分析的对象，例如基金公司、基金、股票、时间、规模、持仓或业绩口径。",
                rationale=f"Generated SQL planner failed: {exc}",
            )
            errors = list(state.get("errors", [])) + [str(exc)]
            state = {
                **state,
                "plan": plan,
                "generated_sql": "",
                "errors": errors,
                "next_action": "clarify",
            }
            return _append_trace(
                state,
                node="sql_planner_node",
                thought="SQL planner 未能产出可解析计划，转为用户澄清而不是继续执行 SQL。",
                action="GeneratedSQLAgent.plan_sql()",
                observation=f"[REACT-CORRECTION] sql_plan_error={exc}; next_action=clarify",
            )
        plan = sql_plan_to_agent_plan(sql_plan)
        state = {
            **state,
            "sql_plan": sql_plan,
            "generated_sql": sql_plan.sql,
            "plan": plan,
            "next_action": "execute",
        }
        return _append_trace(
            state,
            node="sql_planner_node",
            thought="让 LLM/规则根据用户问题和 schema 生成受控只读 SQL。",
            action="GeneratedSQLAgent.plan_sql()",
            observation=f"[SQL] intent={sql_plan.query_intent}, tables={sql_plan.tables}, sql_preview={sql_plan.sql[:160]}",
        )

    def sql_validator_node(self, state: AgentState) -> AgentState:
        if self.sql_agent is None:
            raise RuntimeError("GeneratedSQLAgent 未初始化。")
        errors = self.sql_agent.validate_sql(state.get("generated_sql", ""))
        all_errors = list(state.get("errors", [])) + errors
        next_action = "execute" if not errors else "replan"
        state = {**state, "sql_validation_errors": errors, "errors": all_errors, "next_action": next_action}
        marker = "[SQL]" if not errors else "[REACT-CORRECTION]"
        return _append_trace(
            state,
            node="sql_validator_node",
            thought="检查 SQL 是否只读、是否只访问白名单表、是否有 LIMIT 且不会越权。",
            action="GeneratedSQLAgent.validate_sql()",
            observation=f"{marker} passed={not errors}, errors={errors}, next_action={next_action}",
        )

    def sql_dry_run_node(self, state: AgentState) -> AgentState:
        try:
            assert self.sql_agent is not None
            self.sql_agent.store.query_df(f"EXPLAIN QUERY PLAN {state['generated_sql']}")
            state = {**state, "next_action": "execute"}
            return _append_trace(
                state,
                node="sql_dry_run_node",
                thought="正式执行前先让 SQLite 解析查询计划，提前发现字段名、表名、语法错误。",
                action="EXPLAIN QUERY PLAN generated_sql",
                observation="[SQL] dry_run_passed=True",
            )
        except Exception as exc:
            errors = list(state.get("errors", [])) + [str(exc)]
            state = {**state, "errors": errors, "next_action": "replan"}
            return _append_trace(
                state,
                node="sql_dry_run_node",
                thought="dry run 失败，记录错误并回到 SQL planner 进行纠错重试。",
                action="EXPLAIN QUERY PLAN generated_sql",
                observation=f"[REACT-CORRECTION] dry_run_error={exc}; next_action=replan",
            )

    def sql_executor_node(self, state: AgentState) -> AgentState:
        try:
            assert self.sql_agent is not None
            sql_plan = state["sql_plan"]
            df = self.sql_agent.store.query_df(state["generated_sql"])
            result = ToolResult(
                tool_name="generated_sql_query",
                intent="generated_sql_query",
                tables={"generated_sql_result": df_to_records(df)},
                notes=sql_plan.assumptions + [sql_plan.explanation, "SQL 由 LLM/规则生成，并经过只读白名单校验和 dry run。"],
                metadata={"sql": state["generated_sql"], "tables": sql_plan.tables, "expected_columns": sql_plan.expected_columns},
            )
            tool_history = list(state.get("tool_history", []))
            tool_history.append({"tool_name": "generated_sql_query", "args": {"sql": state["generated_sql"]}, "metadata": result.metadata})
            state = {**state, "tool_result": result, "tool_history": tool_history, "next_action": "report"}
            return _append_trace(
                state,
                node="sql_executor_node",
                thought="执行已通过校验和 dry run 的只读 SQL。",
                action="SQLiteStore.query_df(generated_sql)",
                observation=f"[SQL][TOOL] rows={len(df)}, columns={list(df.columns)}",
            )
        except Exception as exc:
            errors = list(state.get("errors", [])) + [str(exc)]
            state = {**state, "errors": errors, "next_action": "replan"}
            return _append_trace(
                state,
                node="sql_executor_node",
                thought="SQL 执行失败，进入 ReAct 纠错路径。",
                action="SQLiteStore.query_df(generated_sql)",
                observation=f"[REACT-CORRECTION] sql_execute_error={exc}; next_action=replan",
            )

    def sql_result_validator_node(self, state: AgentState) -> AgentState:
        result = state.get("tool_result")
        rows = []
        if result and result.tables:
            rows = result.tables.get("generated_sql_result", [])

        if result is None or not result.tables:
            # SQL 执行本身失败（无表返回） → 触发重试
            issues = ["生成 SQL 未返回任何表格，可能存在执行错误。"]
            next_action = "replan"
            marker = "[REACT-CORRECTION]"
        else:
            # SQL 执行成功，结果可能为 0 行（数据不存在）→ 直接进报告，让报告模块告知用户
            issues = []
            next_action = "report"
            marker = "[SQL]" if rows else "[SQL-EMPTY]"

        state = {**state, "next_action": next_action}
        return _append_trace(
            state,
            node="sql_result_validator_node",
            thought="检查生成 SQL 的结果。执行失败→重试；结果为空→告知用户没有满足条件的数据；有结果→报告。",
            action="validate_generated_sql_result",
            observation=f"{marker} rows={len(rows)}, issues={issues}, next_action={next_action}",
        )

    def sql_reflect_node(self, state: AgentState) -> AgentState:
        retry_count = state.get("sql_retry_count", 0)
        max_steps = state.get("max_steps", 3)
        requested = state.get("next_action", "report")
        if requested == "replan":
            retry_count += 1
            requested = "replan" if retry_count < max_steps else "fail"

        # C fallback: generated SQL 重试用尽 → 切换到 hard tool 模式兜底（与 B fallback 对称）
        if (
            requested == "fail"
            and state.get("sql_mode") == "generated"
            and not state.get("generated_fallback_attempted", False)
        ):
            state = {
                **state,
                "generated_fallback_attempted": True,
                "sql_mode": "hard",
                "sql_retry_count": 0,
                "retry_count": 0,
                "next_action": "fallback_hard",
            }
            return _append_trace(
                state,
                node="sql_reflect_node",
                thought="generated SQL 重试用尽，C fallback：切换到 hard tool 模式重新规划。",
                action="generated_fallback_to_hard",
                observation="[C-FALLBACK] generated_fallback_attempted=True, next_action=fallback_hard",
            )

        state = {**state, "sql_retry_count": retry_count, "next_action": requested}
        marker = "[REACT-CORRECTION]" if requested in {"replan", "fail"} else "[SQL]"
        return _append_trace(
            state,
            node="sql_reflect_node",
            thought="根据 SQL 校验、dry run 或结果观察决定是否让 LLM 重新生成 SQL。",
            action="reflect_sql_branch",
            observation=f"{marker} next_action={requested}, sql_retry_count={retry_count}, max_steps={max_steps}",
        )

    def analytical_report_writer_node(self, state: AgentState) -> AgentState:
        """统一分析报告写作节点（Branch 2）。

        服务 hard tools 路径和 generated SQL 路径：
        - hard 路径：tool_result 来自工具执行器，generated_sql 为空。
        - sql 路径：tool_result 来自 SQL 执行器，generated_sql 含 SQL 字符串。

        三阶段流水线（llm 模式）：
        1. 技能选择 → 默认大纲（report_skills，规则驱动）
        2. Outliner LLM → 调整大纲（OUTLINER_SYSTEM_PROMPT，失败回退到技能大纲）
        3. Drafter LLM → 撰写完整中文分析报告（DRAFTER_SYSTEM_PROMPT）

        mock 模式：只用技能大纲 + 数据表格，不调用 LLM。
        """
        result = state.get("tool_result")
        generated_sql = state.get("generated_sql", "")

        if result is None:
            answer = "未能得到可用结果，请尝试换一种提问方式或放宽查询条件。"
        else:
            answer = self.report_writer.write(
                query=state["query"],
                tool_result=result,
                generated_sql=generated_sql,
                result_validation=state.get("result_validation"),
                mode=state.get("mode", "mock"),
            )

        state = {**state, "draft_answer": answer, "next_action": "final"}
        skill_type = getattr(self.report_writer, "last_skill_type", "?") or "?"
        outline_source = getattr(self.report_writer, "last_outline_source", "?") or "?"
        stage_ms = getattr(self.report_writer, "last_stage_ms", {}) or {}
        # 拼成 "outliner_llm=2351ms drafter_llm=8204ms" 紧凑串，让 --trace 直接显示瓶颈
        timing_str = " ".join(f"{k}={int(v)}ms" for k, v in stage_ms.items())
        total_ms = int(sum(stage_ms.values()))
        is_sql = bool(generated_sql)
        return _append_trace(
            state,
            node="analytical_report_writer_node",
            thought="技能 → Outliner LLM → Drafter LLM 三阶段（hard/SQL 双路径统一）。",
            action=f"AnalyticalReportWriter.write(sql={'yes' if is_sql else 'no'})",
            observation=(
                f"skill={skill_type}, outline_source={outline_source}, "
                f"sql={'yes' if is_sql else 'no'}, draft_length={len(answer)}, "
                f"total={total_ms}ms [{timing_str}]"
            ),
        )

    def result_validator_node(self, state: AgentState) -> AgentState:
        validation = self.result_validator.validate(
            query=state["query"],
            plan=state["plan"],
            result=state["tool_result"],
        )
        state = {**state, "result_validation": validation, "next_action": validation.next_action}
        return _append_trace(
            state,
            node="result_validator_node",
            thought="检查工具观察结果是否为空、字段是否匹配问题、是否存在异常值或缺失基金。",
            action="ResultValidator.validate()",
            observation=(
                f"passed={validation.passed}, next_action={validation.next_action}, "
                f"warnings={validation.warnings}, issues={validation.issues}, hint={validation.correction_hint}"
            ),
        )

    def reflect_node(self, state: AgentState) -> AgentState:
        """根据校验观察决定 replan、clarify、report 或 fail。

        B 增强：当 hard 模式重试用尽即将 fail 时，若尚未尝试过 generated SQL fallback，
        则改路由到 sql_planner_node，让 LLM 生成 SQL 兜底。
        """
        retry_count = state.get("retry_count", 0)
        max_steps = state.get("max_steps", 3)
        requested = state.get("next_action", "report")
        fallback_marker = ""
        if requested == "replan":
            retry_count += 1
            if retry_count >= max_steps:
                # 重试用尽：尝试 B fallback
                if (
                    state.get("sql_mode") == "hard"
                    and not state.get("hard_fallback_attempted", False)
                    and self.sql_agent is not None
                ):
                    fallback_marker = "[REACT-FALLBACK]"
                    state = {
                        **state,
                        "hard_fallback_attempted": True,
                        "sql_mode": "generated",
                        "retry_count": 0,
                        "next_action": "fallback_sql",
                    }
                    return _append_trace(
                        state,
                        node="reflect_node",
                        thought="hard 模式重试用尽，转入 generated SQL fallback。",
                        action="hard_fallback_to_generated_sql",
                        observation=f"{fallback_marker} hard_fallback_attempted=True, next_action=fallback_sql",
                    )
                requested = "fail"
            else:
                requested = "replan"
        state = {**state, "retry_count": retry_count, "next_action": requested}
        return _append_trace(
            state,
            node="reflect_node",
            thought="基于 plan/result validator 的观察结果决定下一步。",
            action="reflect_and_route",
            observation=f"next_action={requested}, retry_count={retry_count}, max_steps={max_steps}",
        )

    def report_writer_node(self, state: AgentState) -> AgentState:
        """Hard tools 路径出口（委托给 analytical_report_writer_node）。"""
        return self.analytical_report_writer_node(state)

    def self_check_node(self, state: AgentState) -> AgentState:
        check = self.self_checker.check(
            query=state["query"],
            plan=state["plan"],
            tool_result=state.get("tool_result"),
            answer=state.get("draft_answer", ""),
        )
        next_action = "final" if check.passed else "revise"
        state = {**state, "self_check": check, "next_action": next_action}
        return _append_trace(
            state,
            node="self_check_node",
            thought="检查最终回答是否回答原问题、忠实于工具结果、说明口径且避免投资建议。",
            action="SelfCheckAgent.check()",
            observation=f"passed={check.passed}, next_action={next_action}, issues={check.issues}",
        )

    def revise_report_node(self, state: AgentState) -> AgentState:
        """自检失败后的保守修订，最多 1 次。"""
        revision_count = state.get("revision_count", 0) + 1
        draft = state.get("draft_answer", "")
        check = state.get("self_check")
        issues = check.issues if check else []
        repaired = draft
        if issues:
            repaired += "\n\n### 自检修订说明\n" + "\n".join(f"- {issue}" for issue in issues)
        state = {**state, "draft_answer": repaired, "revision_count": revision_count, "next_action": "final"}
        return _append_trace(
            state,
            node="revise_report_node",
            thought="自检未通过，对回答进行一次保守修订。",
            action="revise_report_once",
            observation=f"revision_count={revision_count}",
        )

    def save_context_node(self, state: AgentState) -> AgentState:
        # final_answer 优先：clarification_node / fail_node 显式写 final_answer。
        # draft_answer 是 report_writer 在正常路径写的。
        # 翻转顺序，防止 checkpointer 上一轮残留的 draft_answer 覆盖本轮 clarification。
        final_answer = state.get("final_answer") or state.get("draft_answer") or self._fallback_answer(state)
        if "plan" in state:
            self.memory.save_from_result(
                session_id=state.get("session_id", "default"),
                query=state["query"],
                plan=state["plan"],
                result=state.get("tool_result"),
            )
        state = {**state, "final_answer": final_answer, "next_action": "final"}
        return _append_trace(
            state,
            node="save_context_node",
            thought="保存结构化上下文，支持下一轮追问。",
            action="save_memory_and_finalize",
            observation=f"final_answer_length={len(final_answer)}",
        )

    def clarification_node(self, state: AgentState) -> AgentState:
        """Clarify 节点：使用 LangGraph 原生 interrupt 暂停 graph，等待用户补充。

        恢复时通过 Command(resume=user_response) 把用户的回答注入。
        本节点会把原 query 和用户补充合并，再返回 planner_node 重新规划。
        """
        plan = state.get("plan")
        question = "请补充更多信息后再查询。"
        if plan and plan.clarification_question:
            question = plan.clarification_question
        elif state.get("plan_validation"):
            question = "当前问题还不能可靠执行：" + "；".join(state["plan_validation"].issues)

        # 优先使用 LangGraph 原生 interrupt；未安装时退化为旧行为
        user_response: str | None = None
        try:
            from langgraph.types import interrupt  # type: ignore
            payload = {
                "kind": "clarification",
                "question": question,
                "original_query": state.get("query", ""),
                "thread_id": state.get("session_id"),
            }
            user_response = interrupt(payload)  # 第一次调用：暂停；resume 时返回值
        except ImportError:
            user_response = None
        except Exception:
            # 没有 checkpointer 等异常 → 退化为旧行为
            user_response = None

        if not user_response:
            # 退化路径：保持原结束语义
            state = {
                **state,
                "final_answer": question,
                "pending_clarification": {"question": question, "original_query": state.get("query", "")},
                "next_action": "final",
            }
            return _append_trace(
                state,
                node="clarification_node",
                thought="LangGraph interrupt 不可用或被取消，回退到一次性返回追问。",
                action="return_clarification",
                observation=question,
            )

        # 恢复路径：合并查询，继续 planner
        merged_query = f"{state.get('query', '')} | 用户补充：{user_response}"
        state = {
            **state,
            "query": merged_query,
            "pending_clarification": {},
            "retry_count": 0,        # 复位重试计数器，作为新一轮规划
            "errors": [],
            "next_action": "replan_after_clarify",
        }
        return _append_trace(
            state,
            node="clarification_node",
            thought="收到用户补充信息，合并到 query 后回到 planner 重新规划。",
            action="resume_with_clarification",
            observation=f"merged_query={merged_query[:120]}",
        )

    @staticmethod
    def route_after_clarification(state: AgentState) -> str:
        """Clarify 节点的出口：恢复时回到 planner，否则进入 save 结束。"""
        return "planner" if state.get("next_action") == "replan_after_clarify" else "save"

    def fail_node(self, state: AgentState) -> AgentState:
        answer = self._fallback_answer(state)
        state = {**state, "final_answer": answer, "next_action": "fail"}
        return _append_trace(
            state,
            node="fail_node",
            thought="达到最大重试步数或无法安全修复，停止执行。",
            action="return_failure",
            observation=answer,
        )

    @staticmethod
    def _fallback_answer(state: AgentState) -> str:
        issues: list[str] = []
        for key in ["plan_validation", "result_validation"]:
            validation = state.get(key)
            if validation:
                issues.extend(validation.issues)
        if not issues:
            issues = state.get("errors", []) or ["当前没有生成可用结果。"]
        return "当前问题暂时无法可靠回答。\n\n" + "\n".join(f"- {issue}" for issue in issues)

    @staticmethod
    def _query_needs_clarification(query: str) -> bool:
        """Generated SQL 前置判断：明显不是数据分析请求时先追问。"""
        q = query.strip()
        data_terms = [
            "规模",
            "持仓",
            "重仓",
            "业绩",
            "收益",
            "回撤",
            "超额",
            "基金",
            "股票",
            "公司",
            "资产类型",
            "wind",
            "Wind",
            "排名",
            "趋势",
            "变化",
            "易方达",
            "华夏",
            "广发",
            "富国",
            "中欧",
            "嘉实",
            "南方",
            "博时",
            "宁德时代",
            "贵州茅台",
            "腾讯控股",
            "阿里巴巴",
        ]
        if any(term in q for term in data_terms):
            return False
        if re.search(r"(?<!\d)\d{6}(?:\.(?:OF|SH|SZ))?(?!\d)", q):
            return False
        return True

    @staticmethod
    def route_after_plan_validation(state: AgentState) -> str:
        action = state.get("next_action")
        if action == "execute":
            return "execute"
        if action == "fallback_sql":        # B fallback 早触发：直接转 generated SQL
            return "fallback_sql"
        if action == "clarify":
            return "clarify"
        if action == "replan":
            return "reflect"
        return "fail"

    @staticmethod
    def route_after_mode(state: AgentState) -> str:
        return "generated" if state.get("next_action") == "generated" else "hard"

    @staticmethod
    def route_after_sql_validation(state: AgentState) -> str:
        return "dry_run" if state.get("next_action") == "execute" else "reflect"

    @staticmethod
    def route_after_sql_planner(state: AgentState) -> str:
        return "clarify" if state.get("next_action") == "clarify" else "validate"

    @staticmethod
    def route_after_sql_dry_run(state: AgentState) -> str:
        return "execute" if state.get("next_action") == "execute" else "reflect"

    @staticmethod
    def route_after_sql_result_validation(state: AgentState) -> str:
        return "report" if state.get("next_action") == "report" else "reflect"

    @staticmethod
    def route_after_sql_reflect(state: AgentState) -> str:
        action = state.get("next_action")
        if action == "replan":
            return "sql_planner"
        if action == "report":
            return "sql_report"
        if action == "fallback_hard":   # C fallback: generated → hard
            return "fallback_hard"
        return "fail"

    @staticmethod
    def route_after_reflect(state: AgentState) -> str:
        action = state.get("next_action")
        if action == "replan":
            return "planner"
        if action == "fallback_sql":
            return "fallback_sql"
        if action == "clarify":
            return "clarify"
        if action == "report":
            return "report"
        if action == "final":
            return "save"
        return "fail"

    @staticmethod
    def route_after_result_validation(state: AgentState) -> str:
        action = state.get("next_action")
        if action == "report":
            return "reflect"
        if action in {"replan", "clarify", "fail"}:
            return "reflect"
        return "reflect"

    @staticmethod
    def route_after_self_check(state: AgentState) -> str:
        if state.get("next_action") == "revise" and state.get("revision_count", 0) < 1:
            return "revise"
        return "save"

    def run_linear(self, state: AgentState) -> AgentState:
        """没有安装 LangGraph 时的 fallback，保持与图执行一致的路由语义。"""
        state = self.load_context_node(state)
        state = self.complexity_classifier_node(state)
        state = self.mode_router_node(state)
        if state.get("next_action") == "generated":
            while True:
                state = self.sql_planner_node(state)
                if self.route_after_sql_planner(state) == "clarify":
                    state = self.clarification_node(state)
                    return self.save_context_node(state)
                state = self.sql_validator_node(state)
                if self.route_after_sql_validation(state) == "reflect":
                    state = self.sql_reflect_node(state)
                    _r = self.route_after_sql_reflect(state)
                    if _r == "sql_planner":
                        continue
                    if _r == "fallback_hard":   # C fallback
                        state = {**state, "sql_mode": "hard"}
                        return self.run_linear(state)
                    state = self.fail_node(state)
                    return self.save_context_node(state)
                state = self.sql_dry_run_node(state)
                if self.route_after_sql_dry_run(state) == "reflect":
                    state = self.sql_reflect_node(state)
                    _r = self.route_after_sql_reflect(state)
                    if _r == "sql_planner":
                        continue
                    if _r == "fallback_hard":   # C fallback
                        state = {**state, "sql_mode": "hard"}
                        return self.run_linear(state)
                    state = self.fail_node(state)
                    return self.save_context_node(state)
                state = self.sql_executor_node(state)
                state = self.sql_result_validator_node(state)
                if self.route_after_sql_result_validation(state) == "report":
                    state = self.analytical_report_writer_node(state)
                    state = self.self_check_node(state)
                    if self.route_after_self_check(state) == "revise":
                        state = self.revise_report_node(state)
                    return self.save_context_node(state)
                state = self.sql_reflect_node(state)
                _r = self.route_after_sql_reflect(state)
                if _r == "fallback_hard":           # C fallback
                    state = {**state, "sql_mode": "hard"}
                    return self.run_linear(state)
                if _r == "sql_report":              # reflect 决定直接报告
                    state = self.analytical_report_writer_node(state)
                    state = self.self_check_node(state)
                    if self.route_after_self_check(state) == "revise":
                        state = self.revise_report_node(state)
                    return self.save_context_node(state)
                if _r != "sql_planner":
                    state = self.fail_node(state)
                    return self.save_context_node(state)
        while True:
            state = self.planner_node(state)
            state = self.tool_router_node(state)
            state = self.plan_validator_node(state)
            route = self.route_after_plan_validation(state)
            if route == "execute":
                break
            if route == "clarify":
                state = self.clarification_node(state)
                return self.save_context_node(state)
            state = self.reflect_node(state)
            if self.route_after_reflect(state) != "planner":
                state = self.fail_node(state)
                return self.save_context_node(state)

        state = self.executor_node(state)
        state = self.result_validator_node(state)
        state = self.reflect_node(state)
        route = self.route_after_reflect(state)
        if route == "planner":
            return self.run_linear({**state, "trace": state.get("trace", []), "observations": state.get("observations", [])})
        if route == "fallback_sql":
            # B fallback：转入 generated SQL 分支
            state = {**state, "sql_mode": "generated"}
            return self.run_linear(state)
        if route == "clarify":
            state = self.clarification_node(state)
            return self.save_context_node(state)
        if route == "fail":
            state = self.fail_node(state)
            return self.save_context_node(state)

        state = self.report_writer_node(state)
        state = self.self_check_node(state)
        if self.route_after_self_check(state) == "revise":
            state = self.revise_report_node(state)
        return self.save_context_node(state)

    def build_app(self, checkpointer=None):
        """构建 LangGraph app。

        checkpointer 用于支持 interrupt-based clarify。传入 MemorySaver 即可。
        """
        from langgraph.graph import StateGraph, START, END

        graph = StateGraph(AgentState)

        graph.add_node("load_context_node", self.load_context_node)
        graph.add_node("complexity_classifier_node", self.complexity_classifier_node)
        graph.add_node("mode_router_node", self.mode_router_node)
        graph.add_node("planner_node", self.planner_node)
        graph.add_node("tool_router_node", self.tool_router_node)
        graph.add_node("plan_validator_node", self.plan_validator_node)
        graph.add_node("executor_node", self.executor_node)
        graph.add_node("result_validator_node", self.result_validator_node)
        graph.add_node("reflect_node", self.reflect_node)
        graph.add_node("report_writer_node", self.report_writer_node)
        graph.add_node("analytical_report_writer_node", self.analytical_report_writer_node)
        graph.add_node("self_check_node", self.self_check_node)
        graph.add_node("revise_report_node", self.revise_report_node)
        graph.add_node("save_context_node", self.save_context_node)
        graph.add_node("clarification_node", self.clarification_node)
        graph.add_node("fail_node", self.fail_node)
        graph.add_node("sql_planner_node", self.sql_planner_node)
        graph.add_node("sql_validator_node", self.sql_validator_node)
        graph.add_node("sql_dry_run_node", self.sql_dry_run_node)
        graph.add_node("sql_executor_node", self.sql_executor_node)
        graph.add_node("sql_result_validator_node", self.sql_result_validator_node)
        graph.add_node("sql_reflect_node", self.sql_reflect_node)

        graph.add_edge(START, "load_context_node")
        graph.add_edge("load_context_node", "complexity_classifier_node")
        graph.add_edge("complexity_classifier_node", "mode_router_node")
        graph.add_conditional_edges(
            "mode_router_node",
            self.route_after_mode,
            {"hard": "planner_node", "generated": "sql_planner_node"},
        )
        graph.add_edge("planner_node", "tool_router_node")
        graph.add_edge("tool_router_node", "plan_validator_node")
        graph.add_conditional_edges(
            "plan_validator_node",
            self.route_after_plan_validation,
            {
                "execute": "executor_node",
                "clarify": "clarification_node",
                "reflect": "reflect_node",
                "fail": "fail_node",
                "fallback_sql": "sql_planner_node",  # B fallback 早触发：hard 校验失败直转 generated SQL
            },
        )
        graph.add_edge("executor_node", "result_validator_node")
        graph.add_conditional_edges(
            "result_validator_node",
            self.route_after_result_validation,
            {"reflect": "reflect_node"},
        )
        graph.add_conditional_edges(
            "reflect_node",
            self.route_after_reflect,
            {
                "planner": "planner_node",
                "fallback_sql": "sql_planner_node",  # B：hard 失败转 generated
                "clarify": "clarification_node",
                "report": "report_writer_node",
                "save": "save_context_node",
                "fail": "fail_node",
            },
        )
        graph.add_edge("report_writer_node", "self_check_node")
        graph.add_conditional_edges(
            "self_check_node",
            self.route_after_self_check,
            {"revise": "revise_report_node", "save": "save_context_node"},
        )
        graph.add_edge("revise_report_node", "save_context_node")
        graph.add_conditional_edges(
            "clarification_node",
            self.route_after_clarification,
            {"planner": "planner_node", "save": "save_context_node"},
        )
        graph.add_edge("fail_node", "save_context_node")
        graph.add_edge("save_context_node", END)

        graph.add_conditional_edges(
            "sql_planner_node",
            self.route_after_sql_planner,
            {"validate": "sql_validator_node", "clarify": "clarification_node"},
        )
        graph.add_conditional_edges(
            "sql_validator_node",
            self.route_after_sql_validation,
            {"dry_run": "sql_dry_run_node", "reflect": "sql_reflect_node"},
        )
        graph.add_conditional_edges(
            "sql_dry_run_node",
            self.route_after_sql_dry_run,
            {"execute": "sql_executor_node", "reflect": "sql_reflect_node"},
        )
        graph.add_edge("sql_executor_node", "sql_result_validator_node")
        graph.add_conditional_edges(
            "sql_result_validator_node",
            self.route_after_sql_result_validation,
            {"report": "analytical_report_writer_node", "reflect": "sql_reflect_node"},
        )
        graph.add_conditional_edges(
            "sql_reflect_node",
            self.route_after_sql_reflect,
            {
                "sql_planner": "sql_planner_node",
                "sql_report": "analytical_report_writer_node",
                "fail": "fail_node",
                "fallback_hard": "planner_node",   # C fallback: generated SQL → hard tools
            },
        )
        graph.add_edge("analytical_report_writer_node", "self_check_node")

        if checkpointer is not None:
            return graph.compile(checkpointer=checkpointer)
        return graph.compile()


# 在类定义后自动包裹所有 *_node 方法以测量每个节点的墙钟耗时。
# 这样新增节点不需要记得手动加装饰器。
for _name in list(vars(FundAgentWorkflow)):
    _attr = vars(FundAgentWorkflow)[_name]
    if _name.endswith("_node") and callable(_attr) and not isinstance(_attr, (staticmethod, classmethod)):
        setattr(FundAgentWorkflow, _name, _timed_node(_attr))
