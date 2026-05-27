"""多轮上下文 Memory。

这个模块保存会话级上下文，支持用户追问：
第一轮：1季度末规模最大的10只主动权益基金是谁？
第二轮：这些基金主要持有哪些股票？

Memory 不需要神秘技术，本质是把上一轮有用信息保存下来。
"""

from __future__ import annotations

import json
from typing import Any

from src.agent.schemas import ToolResult, AgentPlan
from src.data.sqlite_store import SQLiteStore


class MemoryStore:
    """基于 SQLite 的轻量 session memory。"""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.store.initialize_schema()

    def load(self, session_id: str, use_long_memory: bool = True) -> dict[str, Any]:
        df = self.store.query_df(
            "SELECT context_json FROM conversation_memory WHERE session_id = :session_id",
            {"session_id": session_id},
        )
        if df.empty:
            context: dict[str, Any] = {}
        else:
            try:
                context = json.loads(df.iloc[0]["context_json"])
            except Exception:
                context = {}
        context["recent_turns"] = self.load_recent_turns(session_id, limit=5)
        if use_long_memory:
            context["long_memory_summaries"] = self.load_recent_archives(limit=5)
        return context

    def load_recent_archives(self, limit: int = 5) -> list[dict[str, Any]]:
        """读取最近若干个已归档会话摘要。

        长期 memory 只暴露摘要，不把历史完整对话塞进 prompt，避免 token 膨胀。
        """
        df = self.store.query_df(
            """
            SELECT session_id, summary, created_at
            FROM conversation_archives
            ORDER BY id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        if df.empty:
            return []
        return [
            {"session_id": row["session_id"], "summary": row["summary"], "created_at": row["created_at"]}
            for _, row in df.iterrows()
        ]

    def load_recent_turns(self, session_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """读取最近 N 轮摘要。"""
        df = self.store.query_df(
            """
            SELECT query, plan_json, result_summary, context_json, created_at
            FROM conversation_turns
            WHERE session_id = :session_id
            ORDER BY id DESC
            LIMIT :limit
            """,
            {"session_id": session_id, "limit": limit},
        )
        if df.empty:
            return []
        turns: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                plan = json.loads(row["plan_json"]) if row["plan_json"] else None
                context = json.loads(row["context_json"]) if row["context_json"] else {}
            except Exception:
                plan = None
                context = {}
            turns.append(
                {
                    "query": row["query"],
                    "plan": plan,
                    "result_summary": row["result_summary"],
                    "context": context,
                    "created_at": row["created_at"],
                }
            )
        return turns

    def save_from_result(self, session_id: str, query: str, plan: AgentPlan, result: ToolResult | None) -> None:
        """从本轮结果中提取对下一轮有用的信息。"""
        context: dict[str, Any] = {
            "last_query": query,
            "last_intent": plan.intent,
            "last_tool_name": plan.tool_name,
            "last_args": plan.args,
            "last_date": plan.args.get("date") or plan.args.get("holding_date"),
            "last_asset_type": plan.args.get("asset_type"),
            "last_fund_codes": [],
            "last_stock_codes": [],
            "last_companies": plan.args.get("companies") or ([plan.args["fund_company"]] if plan.args.get("fund_company") else []),
            "last_result_summary": "",
        }

        if result:
            context["last_metadata"] = result.metadata
            context["last_date"] = result.metadata.get("date") or result.metadata.get("holding_date") or context["last_date"]
            context["last_asset_type"] = result.metadata.get("asset_type") or context["last_asset_type"]
            context["last_companies"] = result.metadata.get("companies") or context["last_companies"]
            # 如果工具返回基金列表，就保存 fund_codes，支持“这些基金”的追问。
            fund_codes: list[str] = []
            stock_codes: list[str] = []
            for rows in result.tables.values():
                for row in rows:
                    if "基金代码" in row:
                        fund_codes.append(str(row["基金代码"]).zfill(6))
                    if "股票代码" in row:
                        stock_codes.append(str(row["股票代码"]))
            if fund_codes:
                context["last_fund_codes"] = fund_codes[:50]
            if stock_codes:
                context["last_stock_codes"] = stock_codes[:50]
            context["last_result_summary"] = self._summarize_result(result)

        context_json = json.dumps(context, ensure_ascii=False)
        self.store.execute(
            """
            INSERT INTO conversation_memory(session_id, context_json, updated_at)
            VALUES(:session_id, :context_json, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
              context_json = excluded.context_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            {"session_id": session_id, "context_json": context_json},
        )
        self.store.execute(
            """
            INSERT INTO conversation_turns(session_id, query, plan_json, result_summary, context_json)
            VALUES(:session_id, :query, :plan_json, :result_summary, :context_json)
            """,
            {
                "session_id": session_id,
                "query": query,
                "plan_json": plan.model_dump_json(),
                "result_summary": context.get("last_result_summary", ""),
                "context_json": context_json,
            },
        )

    def archive_session(self, session_id: str) -> str:
        """把一次交互 session 压缩成长期 memory 摘要。"""
        turns = self.load_recent_turns(session_id, limit=20)
        if not turns:
            return ""
        lines = []
        for turn in reversed(turns[-10:]):
            plan = turn.get("plan") or {}
            lines.append(
                f"问题={turn.get('query')}; intent={plan.get('intent')}; "
                f"tool={plan.get('tool_name')}; 摘要={turn.get('result_summary')}"
            )
        summary = "\n".join(lines)[:3000]
        self.store.execute(
            """
            INSERT INTO conversation_archives(session_id, summary)
            VALUES(:session_id, :summary)
            """,
            {"session_id": session_id, "summary": summary},
        )
        return summary

    @staticmethod
    def _summarize_result(result: ToolResult) -> str:
        """生成短摘要，给下一轮 planner 做上下文提示。"""
        parts: list[str] = []
        for table_name, rows in result.tables.items():
            if not rows:
                parts.append(f"{table_name}: 空")
                continue
            first = rows[0]
            label = first.get("基金名称") or first.get("股票名称") or first.get("基金公司") or "有结果"
            parts.append(f"{table_name}: {len(rows)}行，首项={label}")
        return "；".join(parts)
