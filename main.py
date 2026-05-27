"""CLI 入口。

这个文件应该尽量薄：解析参数、初始化 Agent、打印结果。
真正的业务逻辑不写在 main.py。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.config import Settings
from src.agent.app import FundAnalysisAgent


def print_trace(state: dict) -> None:
    """打印 ReAct 风格 trace。"""
    print("\n[TRACE]")
    for step in state.get("trace", []):
        label = ""
        text = f"{step.node} {step.action} {step.observation}"
        if "[REACT-CORRECTION]" in text or "replan" in step.observation or "error=" in step.observation:
            label = " [REACT-CORRECTION]"
        elif "[TOOL]" in text or "ToolExecutor.execute" in step.action:
            label = " [TOOL-CALL]"
        elif "[SQL]" in text or step.node.startswith("sql_"):
            label = " [GENERATED-SQL]"
        print(f"- node: {step.node}{label}")
        print(f"  thought: {step.thought}")
        print(f"  action: {step.action}")
        print(f"  observation: {step.observation}")
    if state.get("tool_history"):
        print("[TOOLS]")
        for item in state["tool_history"]:
            print(f"- {item.get('tool_name')}: {item.get('args')}")
    if state.get("errors"):
        print("[ERRORS]")
        for err in state["errors"]:
            print(f"- {err}")
    print("[/TRACE]\n")


def run_once(
    agent: FundAnalysisAgent,
    query: str,
    mode: str,
    session_id: str,
    trace: bool,
    max_steps: int,
    use_long_memory: bool,
    sql_mode: str,
) -> None:
    state = agent.run(
        query,
        mode=mode,
        session_id=session_id,
        max_steps=max_steps,
        use_long_memory=use_long_memory,
        sql_mode=sql_mode,
    )
    if trace:
        print_trace(state)
    print(state.get("final_answer", "当前没有生成结果。"))


def choose(prompt: str, default: str, allowed: set[str]) -> str:
    value = input(f"{prompt} [{default}] > ").strip() or default
    return value if value in allowed else default


def interactive_loop(
    agent: FundAnalysisAgent,
    mode: str,
    session_id: str,
    trace: bool,
    max_steps: int,
    use_long_memory: bool,
    sql_mode: str,
) -> None:
    print("\n基金数据分析 Agent")
    print(f"- 本次短期 memory session：{session_id}")
    print(f"- LLM/mock 模式：{mode}")
    _engine_labels = {"hard": "硬 SQL 专家工具", "generated": "LLM 生成 SQL", "auto": "自动按复杂度选择"}
    print(f"- 查询引擎：{_engine_labels.get(sql_mode, sql_mode)}")
    print(f"- 是否读取长期 memory 摘要：{'是' if use_long_memory else '否'}")
    print("\n支持的问题类别：")
    print("1. 基金规模：规模排名、公司总规模、规模趋势、资产类型分布、Wind 一级/二级/三级分类。")
    print("2. 基金业绩：收益率排名、回撤、超额收益、业绩分布、公司平均收益。")
    print("3. 股票持仓：全市场重仓股、基金持仓明细、公司整体重仓股、共同持仓、持仓变化。")
    print("4. 基金公司：业务结构、主动权益画像、产品数量、公司增长对比。")
    print("5. 跨表分析：规模+业绩、业绩+持仓、多条件筛选、复杂报告证据包。")
    print("\n示例：")
    print("- 1季度末规模最大的10只主动权益基金是谁")
    print("- 偏股混合型基金规模最大的10只是哪些")
    print("- 易方达整体重仓股前10是什么")
    print("- 在规模top100基金中，业绩最好的10个")
    print("- 这些基金主要持有哪些股票？（需要先问基金列表类问题）")
    print("\n输入 exit 退出；输入 /engine 可切换硬 SQL/LLM 生成 SQL；输入 /trace 可开关 trace。\n")

    while True:
        query = input("请输入问题 > ").strip()
        if query.lower() in {"exit", "quit", "q"}:
            break
        if query == "/engine":
            cycle = {"hard": "generated", "generated": "auto", "auto": "hard"}
            sql_mode = cycle.get(sql_mode, "hard")
            labels = {"hard": "硬 SQL 专家工具", "generated": "LLM 生成 SQL", "auto": "自动按复杂度选择"}
            print(f"已切换查询引擎：{labels[sql_mode]}\n")
            continue
        if query == "/trace":
            trace = not trace
            print(f"trace 已{'开启' if trace else '关闭'}。\n")
            continue
        if not query:
            continue
        run_once(agent, query, mode=mode, session_id=session_id, trace=trace, max_steps=max_steps, use_long_memory=use_long_memory, sql_mode=sql_mode)
        print()
    summary = agent.workflow.memory.archive_session(session_id)
    if summary:
        print(f"本次 session 已归档为长期 memory 摘要：{session_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="*", help="用户问题")
    parser.add_argument("--mode", choices=["mock", "llm"], default=None, help="运行模式")
    parser.add_argument("--session-id", default="default", help="会话 ID，用于多轮上下文")
    parser.add_argument("--max-steps", type=int, default=3, help="ReAct 重新规划最大步数，默认 3")
    parser.add_argument("--rebuild-db", action="store_true", help="从 Excel 重建 SQLite 数据库")
    parser.add_argument("--trace", action="store_true", help="打印运行轨迹")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    parser.add_argument("--sql-mode", choices=["hard", "generated", "auto"], default="hard", help="hard=专家工具固定 SQL；generated=LLM 生成受控 SQL；auto=按复杂度自动选择")
    parser.add_argument("--long-memory", action=argparse.BooleanOptionalAction, default=True, help="是否读取已归档的长期 memory 摘要")
    args = parser.parse_args()

    settings = Settings.load(Path(__file__).resolve().parent)
    settings.ensure_dirs()
    mode = args.mode or settings.agent_mode
    agent = FundAnalysisAgent(settings)

    if args.rebuild_db:
        print("正在从 Excel 重建 SQLite 数据库...")
        counts = agent.rebuild_database()
        print(f"数据库重建完成：{counts}")

    if args.interactive or not args.query:
        print("请选择本次运行方式。直接回车使用推荐值。")
        chosen_mode = choose("LLM/mock 模式：mock 或 llm", mode, {"mock", "llm"})
        chosen_sql_mode = choose("查询引擎：hard / generated / auto", args.sql_mode, {"hard", "generated", "auto"})
        long_choice = choose("是否读取长期 memory 摘要：y 或 n", "y" if args.long_memory else "n", {"y", "n"})
        session_id = f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        interactive_loop(
            agent,
            mode=chosen_mode,
            session_id=session_id,
            trace=args.trace,
            max_steps=args.max_steps,
            use_long_memory=(long_choice == "y"),
            sql_mode=chosen_sql_mode,
        )
        return

    query = " ".join(args.query)
    run_once(
        agent,
        query,
        mode=mode,
        session_id=args.session_id,
        trace=args.trace,
        max_steps=args.max_steps,
        use_long_memory=args.long_memory,
        sql_mode=args.sql_mode,
    )


if __name__ == "__main__":
    main()
