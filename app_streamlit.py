"""Streamlit 前端入口。

运行：
    streamlit run app_streamlit.py
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src.agent.app import FundAnalysisAgent
from src.agent.html_report import build_sql_repair_suggestions, save_html_report
from src.config import Settings


st.set_page_config(
    page_title="基金数据分析 Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


CSS = """
<style>
  .stApp {
    background: #f7fbff;
  }
  [data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 1px solid #dbeafe;
  }
  .hero {
    background: linear-gradient(135deg, #1d4ed8, #38bdf8);
    color: white;
    padding: 24px 28px;
    border-radius: 8px;
    margin-bottom: 18px;
    box-shadow: 0 12px 30px rgba(37, 99, 235, .18);
  }
  .hero h1 {
    margin: 0;
    font-size: 28px;
    letter-spacing: 0;
  }
  .hero p {
    margin: 8px 0 0;
    color: #e0f2fe;
  }
  .metric-card {
    background: white;
    border: 1px solid #dbeafe;
    border-radius: 8px;
    padding: 14px 16px;
    box-shadow: 0 8px 20px rgba(37, 99, 235, .06);
  }
  .trace-box {
    background: #ffffff;
    border: 1px solid #dbeafe;
    border-left: 4px solid #2563eb;
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 10px;
  }
  .small-muted {
    color: #64748b;
    font-size: 13px;
  }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_resource
def get_agent() -> FundAnalysisAgent:
    settings = Settings.load(Path(__file__).resolve().parent)
    settings.ensure_dirs()
    return FundAnalysisAgent(settings)


def init_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"ui-{uuid4().hex[:8]}"
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_state" not in st.session_state:
        st.session_state.last_state = None
    if "last_report_path" not in st.session_state:
        st.session_state.last_report_path = None


def apply_runtime_llm_settings(agent: FundAnalysisAgent, planner_report_thinking: bool) -> None:
    """UI 层临时覆盖 thinking，不写回 .env。"""
    agent.settings.planner_thinking_enabled = planner_report_thinking
    agent.settings.report_thinking_enabled = planner_report_thinking
    # Generated SQL 的 thinking 固定开启；如果 SQL_MODEL 不是 deepseek-v4-pro，这个开关不会产生副作用。
    agent.settings.sql_thinking_enabled = True


def render_trace(state: dict) -> None:
    for step in state.get("trace", []):
        label = ""
        text = f"{step.node} {step.action} {step.observation}"
        if "[REACT-CORRECTION]" in text or "replan" in step.observation or "error=" in step.observation:
            label = " · ReAct纠错"
        elif "[TOOL]" in text or "ToolExecutor.execute" in step.action:
            label = " · Tool调用"
        elif "[SQL]" in text or step.node.startswith("sql_"):
            label = " · Generated SQL"
        st.markdown(
            f"""
            <div class="trace-box">
              <b>{step.node}{label}</b><br/>
              <span class="small-muted">Action:</span> {step.action}<br/>
              <span class="small-muted">Observation:</span> {step.observation}
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_tables(state: dict) -> None:
    result = state.get("tool_result")
    if not result or not result.tables:
        st.info("本轮没有结构化表格结果。")
        return
    for name, rows in result.tables.items():
        st.subheader(name)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("空结果")


def render_tool_history(state: dict) -> None:
    history = state.get("tool_history", [])
    if not history:
        st.caption("暂无工具调用记录。")
        return
    for item in history:
        st.code(f"{item.get('tool_name')}\n{item.get('args')}", language="text")


def render_sql_repair_suggestions(state: dict) -> None:
    suggestions = build_sql_repair_suggestions(state)
    if not suggestions:
        st.info("当前不是 Generated SQL 路径，或本轮没有可展示的 SQL 修复建议。")
        return
    for suggestion in suggestions:
        st.markdown(f"- {suggestion}")


def main() -> None:
    init_state()
    agent = get_agent()

    st.markdown(
        """
        <div class="hero">
          <h1>基金数据分析 AI Agent</h1>
          <p>基于规模、持仓、业绩三张表，支持专家工具模式与 LLM 生成 SQL 模式。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("运行设置")
        mode = st.segmented_control("LLM 模式", ["llm", "mock"], default="llm")
        sql_mode_label = st.segmented_control("分析引擎", ["Hard tools", "Generated SQL"], default="Hard tools")
        sql_mode = "generated" if sql_mode_label == "Generated SQL" else "hard"

        planner_report_thinking = st.toggle(
            "Planner/Report 开启 thinking",
            value=agent.settings.planner_thinking_enabled or agent.settings.report_thinking_enabled,
            help="只影响 hard planner 和 report writer；Generated SQL 的 thinking 固定开启，不能在这里关闭。",
        )
        st.info("Generated SQL 使用 SQL LLM，thinking 固定开启。")

        use_long_memory = st.toggle("读取长期 memory 摘要", value=True)
        show_trace = st.toggle("显示 Trace", value=True)
        auto_open_html = st.toggle("运行后自动弹出 HTML 报告", value=True)
        max_steps = st.slider("最大纠错/重试步数", 1, 5, 3)

        st.divider()
        st.caption("当前短期 session")
        st.code(st.session_state.session_id)
        if st.button("开启新 session", use_container_width=True):
            st.session_state.session_id = f"ui-{uuid4().hex[:8]}"
            st.session_state.messages = []
            st.session_state.last_state = None
            st.rerun()

        st.divider()
        st.caption("示例问题")
        examples = [
            "1季度末规模最大的10只主动权益基金是谁",
            "偏股混合型基金规模最大的10只是哪些",
            "易方达整体重仓股前10是什么",
            "在规模top100基金中，业绩最好的10个",
            "对比分析易方达和华夏基金的业务结构",
        ]
        for example in examples:
            if st.button(example, use_container_width=True):
                st.session_state.pending_query = example

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown('<div class="metric-card"><b>数据源</b><br/>规模.xlsx / 持仓.xlsx / 业绩.xlsx</div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-card"><b>当前引擎</b><br/>{sql_mode_label}</div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-card"><b>Thinking</b><br/>SQL: 固定开启；Planner/Report: {"开启" if planner_report_thinking else "关闭"}</div>', unsafe_allow_html=True)

    st.divider()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    query = st.session_state.pop("pending_query", None) if "pending_query" in st.session_state else None
    chat_query = st.chat_input("输入你的基金分析问题，例如：在规模top100基金中，业绩最好的10个")
    query = query or chat_query

    if query:
        apply_runtime_llm_settings(agent, planner_report_thinking)
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)
        with st.chat_message("assistant"):
            with st.spinner("Agent 正在分析数据..."):
                state = agent.run(
                    query,
                    mode=mode,
                    sql_mode=sql_mode,
                    session_id=st.session_state.session_id,
                    use_long_memory=use_long_memory,
                    max_steps=max_steps,
                )
                state["sql_mode"] = sql_mode
                report_path = save_html_report(agent.settings.project_root, state)
                st.session_state.last_report_path = str(report_path)
                st.session_state.last_state = state
                answer = state.get("final_answer", "当前没有生成结果。")
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
                if auto_open_html:
                    webbrowser.open(report_path.resolve().as_uri())

    state = st.session_state.last_state
    if state:
        st.divider()
        tabs = st.tabs(["结果表", "Trace", "工具 / SQL", "SQL 修复建议", "HTML 报告"])
        with tabs[0]:
            render_tables(state)
        with tabs[1]:
            if show_trace:
                render_trace(state)
            else:
                st.caption("Trace 已隐藏，可在左侧开启。")
        with tabs[2]:
            render_tool_history(state)
        with tabs[3]:
            render_sql_repair_suggestions(state)
        with tabs[4]:
            path = st.session_state.last_report_path
            if path:
                st.success(f"HTML 报告已生成：{path}")
                st.link_button("在浏览器打开 HTML 报告", Path(path).resolve().as_uri())
            else:
                st.caption("还没有生成报告。")


if __name__ == "__main__":
    main()
