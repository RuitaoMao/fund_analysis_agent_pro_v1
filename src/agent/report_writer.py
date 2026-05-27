"""分析报告写作器（Branch 2 全量重构 + 真正的两阶段 LLM 流水线）。

设计：
- mock 模式：技能 → 大纲框架 + 数据表格，不调用 LLM。
- llm 模式（两阶段）：
    Stage 1 (Outliner LLM): 技能模板作为建议 → LLM 输出 JSON 大纲（ReportOutline）
                            outliner 失败时回退到技能模板大纲
    Stage 2 (Drafter LLM):  大纲 + 工具数据 + 市场快照上下文 → 完整中文分析报告

- 市场快照（market_snapshot）作为免费上下文：
  即使 planner 没显式调用 query_market_overview，写作器也会从预计算表里捞总规模、
  资产类型分布、头部公司，注入到 Drafter prompt，让竞争格局类报告有市场参照。

ReportWriterAgent 保留原类名，与 app.py / workflow.py 现有构造器兼容。
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.agent.report_skills import select_skill
from src.agent.schemas import ReportOutline, ReportSection, ToolResult
from src.llm.client import LLMClient
from src.llm.prompts import DRAFTER_SYSTEM_PROMPT, OUTLINER_SYSTEM_PROMPT
from src.utils.json_utils import extract_json_object
from src.utils.table_utils import records_to_markdown


class ReportWriterAgent:
    """分析报告写作子智能体（技能 + Outliner LLM + Drafter LLM 两阶段）。"""

    def __init__(self, llm_client: LLMClient | None = None, store=None):
        self.llm_client = llm_client
        self.store = store
        # 暴露给 workflow 节点用于 trace / 可观测性
        self.last_skill_type: str | None = None
        self.last_outline_source: str | None = None  # "skill" | "llm_outliner"
        # 每个阶段的墙钟时间（毫秒），便于排查"写报告慢"的瓶颈
        self.last_stage_ms: dict[str, float] = {}

    # ──────────────────────────────────────────────────────────────────
    # 对外接口
    # ──────────────────────────────────────────────────────────────────

    def write(
        self,
        *,
        query: str,
        tool_result: ToolResult | None,
        generated_sql: str = "",
        mode: str = "mock",
        # 兼容旧调用方
        plan=None,
        plan_validation=None,
        result_validation=None,
    ) -> str:
        """生成分析报告。

        Parameters
        ----------
        query           用户原始问题。
        tool_result     工具结果（ToolResult）；None 时返回友好错误提示。
        generated_sql   生成 SQL 路径时的 SQL 字符串，用于口径说明。
        mode            "mock" | "llm"；mock 不调用 LLM。
        """
        # 重置时间统计；本轮 write() 不到的阶段不会污染上一轮的读数
        self.last_stage_ms = {}

        if tool_result is None:
            self.last_skill_type = None
            self.last_outline_source = None
            return "未能得到可用结果，请尝试换一种提问方式或放宽查询条件。"

        t0 = time.perf_counter()
        skill = select_skill(query, tool_result)
        skill_outline = skill.outline(query, tool_result)
        self.last_skill_type = skill.skill_type
        self.last_stage_ms["skill"] = (time.perf_counter() - t0) * 1000

        # 加载市场快照作为免费上下文（写报告时可用）
        t0 = time.perf_counter()
        market_context = self._load_market_context()
        self.last_stage_ms["market_ctx"] = (time.perf_counter() - t0) * 1000

        if mode == "mock":
            self.last_outline_source = "skill"
            t0 = time.perf_counter()
            answer = self._mock_report(query, tool_result, skill_outline, generated_sql, result_validation)
            self.last_stage_ms["mock_render"] = (time.perf_counter() - t0) * 1000
            return answer

        # LLM 模式：先 Outliner → 再 Drafter
        t0 = time.perf_counter()
        outline = self._outline_with_llm(query, tool_result, skill_outline)
        self.last_stage_ms["outliner_llm"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        answer = self._draft_with_llm(query, tool_result, outline, generated_sql, market_context)
        self.last_stage_ms["drafter_llm"] = (time.perf_counter() - t0) * 1000
        return answer

    # ──────────────────────────────────────────────────────────────────
    # 市场快照上下文
    # ──────────────────────────────────────────────────────────────────

    def _load_market_context(self) -> dict[str, Any] | None:
        if self.store is None:
            return None
        try:
            from src.data.market_snapshot import load_market_snapshot
            return load_market_snapshot(self.store)
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────────
    # Stage 1: Outliner
    # ──────────────────────────────────────────────────────────────────

    def _outline_with_llm(
        self,
        query: str,
        tool_result: ToolResult,
        skill_outline: ReportOutline,
    ) -> ReportOutline:
        """LLM Outliner：以技能大纲为建议，输出最终 ReportOutline。

        失败时回退到 skill_outline，保证 Drafter 一定有大纲可用。
        """
        if self.llm_client is None:
            self.last_outline_source = "skill"
            return skill_outline

        # 给 LLM 简洁的数据摘要（避免大表撑爆 outliner 的 token 预算）
        data_summary = self._summarize_tables_for_outliner(tool_result)
        skill_template_json = json.dumps(skill_outline.model_dump(), ensure_ascii=False, indent=2)

        user_prompt = (
            f"用户问题：{query}\n\n"
            f"工具结果摘要（每张表只展示前 3 行 + 列名 + 行数）：\n{data_summary}\n\n"
            f"默认技能模板（建议保留 70% 以上结构）：\n{skill_template_json}\n\n"
            "请输出最终大纲 JSON。"
        )

        try:
            raw = self.llm_client.chat(
                role="report",
                system_prompt=OUTLINER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_mode=True,
                temperature=0.1,
                max_tokens=1200,
            )
            parsed = extract_json_object(raw) if raw else None
            if not parsed:
                raise ValueError("outliner returned empty / unparseable JSON")

            sections = [
                ReportSection(
                    title=str(s.get("title", "")).strip() or "未命名章节",
                    analytical_angles=[str(a) for a in (s.get("analytical_angles") or [])],
                )
                for s in (parsed.get("sections") or [])
            ]
            if not sections:
                raise ValueError("outliner returned no sections")

            outline = ReportOutline(
                skill_type=str(parsed.get("skill_type") or skill_outline.skill_type),
                direct_answer=parsed.get("direct_answer") or None,
                sections=sections,
            )
            self.last_outline_source = "llm_outliner"
            return outline
        except Exception:
            # Outliner 任何失败都回退到技能大纲
            self.last_outline_source = "skill_fallback"
            return skill_outline

    @staticmethod
    def _summarize_tables_for_outliner(tool_result: ToolResult) -> str:
        parts: list[str] = []
        for table_name, rows in tool_result.tables.items():
            n = len(rows)
            cols = list(rows[0].keys()) if rows else []
            preview = rows[:3]
            parts.append(
                f"- 表【{table_name}】 行数={n} 列={cols}\n"
                f"  前3行：{json.dumps(preview, ensure_ascii=False, default=str)}"
            )
        if tool_result.notes:
            parts.append("- notes：" + "；".join(tool_result.notes))
        return "\n".join(parts) if parts else "（工具结果为空）"

    # ──────────────────────────────────────────────────────────────────
    # Stage 2: Drafter
    # ──────────────────────────────────────────────────────────────────

    def _draft_with_llm(
        self,
        query: str,
        tool_result: ToolResult,
        outline: ReportOutline,
        generated_sql: str,
        market_context: dict[str, Any] | None,
    ) -> str:
        if self.llm_client is None:
            raise RuntimeError("LLMClient 未初始化，无法调用 Drafter LLM。")

        tables_text = self._render_tables(tool_result)
        outline_text = self._render_outline(outline)
        sql_note = (
            f"\n\n**SQL 查询（已通过只读校验和 dry run）：**\n```sql\n{generated_sql}\n```"
            if generated_sql
            else ""
        )
        market_text = self._render_market_context(market_context)

        user_prompt = (
            f"用户问题：{query}\n\n"
            f"查询结果数据：\n{tables_text}"
            f"{sql_note}\n\n"
            f"{market_text}"
            f"报告框架（请严格按此结构撰写）：\n{outline_text}\n\n"
            "请撰写完整的中文分析报告。要有实质性分析洞察，不要只是数据的机械复述。"
        )

        return self.llm_client.chat(
            role="report",
            system_prompt=DRAFTER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=False,
            temperature=0.3,
            max_tokens=3500,
        )

    # ──────────────────────────────────────────────────────────────────
    # Mock 模式（不调用 LLM）
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _mock_report(
        query: str,
        tool_result: ToolResult,
        outline: ReportOutline,
        generated_sql: str,
        result_validation=None,
    ) -> str:
        """Mock 报告：大纲框架 + 原始数据表格，不调用 LLM。"""
        parts: list[str] = []

        if outline.direct_answer:
            parts.append(f"**直接回答：** {outline.direct_answer}")

        if outline.sections:
            parts.append("\n---\n")
            parts.append(f"**报告框架（技能：{outline.skill_type}）**")
            for i, section in enumerate(outline.sections, 1):
                parts.append(f"\n## {i}. {section.title}")
                if section.analytical_angles:
                    for angle in section.analytical_angles:
                        parts.append(f"- {angle}")

        if tool_result.tables:
            parts.append("\n---\n")
            parts.append("## 查询数据")
            for table_name, rows in tool_result.tables.items():
                parts.append(f"\n### {table_name}")
                parts.append(records_to_markdown(rows, max_rows=50))

        if generated_sql:
            parts.append(f"\n**SQL 口径**（已通过只读校验和 dry run）：\n```sql\n{generated_sql}\n```")

        if tool_result.notes:
            parts.append("\n---\n**数据口径**")
            parts.extend(f"- {note}" for note in tool_result.notes)

        warnings: list[str] = list(tool_result.warnings)
        if result_validation and result_validation.warnings:
            warnings.extend(result_validation.warnings)
        warnings = list(dict.fromkeys(warnings))
        if warnings:
            parts.append("\n**注意事项**")
            parts.extend(f"- {w}" for w in warnings)

        return "\n".join(parts)

    # ──────────────────────────────────────────────────────────────────
    # 渲染辅助
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _render_tables(tool_result: ToolResult) -> str:
        """把所有数据表渲染成 Markdown，附数据口径。"""
        parts: list[str] = []
        for table_name, rows in tool_result.tables.items():
            parts.append(f"【{table_name}】")
            parts.append(records_to_markdown(rows, max_rows=50))
        if tool_result.notes:
            parts.append("（数据口径：" + "；".join(tool_result.notes) + "）")
        if tool_result.warnings:
            parts.append("（数据警告：" + "；".join(tool_result.warnings) + "）")
        return "\n".join(parts)

    @staticmethod
    def _render_outline(outline: ReportOutline) -> str:
        parts: list[str] = []
        if outline.direct_answer:
            parts.append(f"0. **直接回答**（必须放报告第一行粗体）：{outline.direct_answer}")
        for i, section in enumerate(outline.sections, 1):
            parts.append(f"{i}. ## {section.title}")
            for angle in section.analytical_angles:
                parts.append(f"   - {angle}")
        return "\n".join(parts)

    @staticmethod
    def _render_market_context(market_context: dict[str, Any] | None) -> str:
        """把市场快照渲染成 Drafter 可读的上下文段落（仅作参照，不替代主表）。"""
        if not market_context:
            return ""
        parts: list[str] = ["**【市场参照（预计算快照，仅供横向对比，不替代主查询结果）】**"]
        if total := market_context.get("market_total"):
            parts.append("市场总览：" + json.dumps(total, ensure_ascii=False, default=str))
        if asset_dist := market_context.get("size_by_asset_type"):
            parts.append("资产类型分布：" + json.dumps(asset_dist[:6], ensure_ascii=False, default=str))
        if top := market_context.get("top_companies"):
            parts.append("头部公司参照：" + json.dumps(top[:10], ensure_ascii=False, default=str))
        parts.append("")  # 末尾空行
        return "\n".join(parts) + "\n"
