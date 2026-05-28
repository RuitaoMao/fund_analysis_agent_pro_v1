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
from src.llm.prompts import (
    DRAFTER_SYSTEM_PROMPT,
    OUTLINER_SYSTEM_PROMPT,
    SIMPLE_ANSWER_SYSTEM_PROMPT,
)
from src.utils.json_utils import extract_json_object
from src.utils.table_utils import records_to_markdown


class ReportWriterAgent:
    """分析报告写作子智能体（技能 + Outliner LLM + Drafter LLM 两阶段）。"""

    def __init__(self, llm_client: LLMClient | None = None, store=None, outliner_enabled: bool = False):
        self.llm_client = llm_client
        self.store = store
        # False（默认）：跳过 Stage 1 LLM，直接用 skill 规则大纲 → 报告写作只剩 1 次 Drafter LLM，wall-time 近似减半。
        # True：保留两阶段流水线（OUTLINER + DRAFTER），适合追求大纲灵活度的场景。
        self.outliner_enabled = outliner_enabled
        # 暴露给 workflow 节点用于 trace / 可观测性
        self.last_skill_type: str | None = None
        self.last_outline_source: str | None = None  # "skill" | "llm_outliner" | "skill_fallback"
        self.last_answer_type: str | None = None     # "simple" | "report"，最近一次 write() 实际走的路径
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
        answer_type: str | None = None,  # "simple" | "report"；不传时按规则推断
    ) -> str:
        """生成回答（简单查询走 simple，分析问题走 report）。

        Parameters
        ----------
        query           用户原始问题。
        tool_result     工具结果（ToolResult）；None 时返回友好错误提示。
        generated_sql   生成 SQL 路径时的 SQL 字符串，用于口径说明。
        mode            "mock" | "llm"；mock 不调用 LLM。
        answer_type     "simple" → 简短结论 + 表格 + 追问，不分章节
                        "report" → 完整分析报告
                        None     → 由 _infer_answer_type 按规则推断
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

        # 决定走 simple 还是 report：
        # 规则推断（基于 query 关键词 + 数据规模 + 技能类型）是确定性的，
        # 比 planner 的 LLM 标签更可靠（planner LLM JSON 失败时会回退到规则版误标，
        # generated SQL 路径还把 answer_type 固定为 "report"）。
        # 所以让规则推断作为主信号，传入的 answer_type 仅用于可观测性，不参与决策。
        resolved_type = self._infer_answer_type(query, tool_result, skill.skill_type)
        self.last_answer_type = resolved_type

        if mode == "mock":
            self.last_outline_source = "skill"
            t0 = time.perf_counter()
            answer = self._mock_report(query, tool_result, skill_outline, generated_sql, result_validation)
            self.last_stage_ms["mock_render"] = (time.perf_counter() - t0) * 1000
            return answer

        # Simple 路径：一次轻量 LLM 调用，跳过 outliner + market_context
        if resolved_type == "simple":
            self.last_outline_source = "skill"
            t0 = time.perf_counter()
            answer = self._draft_simple_with_llm(query, tool_result, generated_sql)
            self.last_stage_ms["drafter_llm"] = (time.perf_counter() - t0) * 1000
            return answer

        # Report 路径：加载市场快照 → outliner（可选）→ drafter
        t0 = time.perf_counter()
        market_context = self._load_market_context()
        self.last_stage_ms["market_ctx"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        if self.outliner_enabled:
            outline = self._outline_with_llm(query, tool_result, skill_outline)
        else:
            # 跳过 Stage 1，直接用规则大纲，节省一次 LLM 往返
            outline = skill_outline
            self.last_outline_source = "skill"
        self.last_stage_ms["outliner_llm"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        answer = self._draft_with_llm(query, tool_result, outline, generated_sql, market_context)
        self.last_stage_ms["drafter_llm"] = (time.perf_counter() - t0) * 1000
        return answer

    # ──────────────────────────────────────────────────────────────────
    # 答案类型推断（planner 未提供 answer_type 时的兜底规则）
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _infer_answer_type(query: str, tool_result: ToolResult, skill_type: str) -> str:
        """根据 query 形态 + 工具结果规模推断 simple / report。

        规则（优先级从高到低）：
        1. lookup / simple_lookup 技能 → simple
        2. 单表 ≤ 25 行 + query 含查找/排名关键词（"是多少"/"前N"/"最高"/"最大"/"哪些"/"基本信息"）→ simple
        3. 多表 或 单表 > 25 行 或 查询含分析关键词（"对比"/"分析"/"格局"/"特征"/"分化"/"差异"/"路径"）→ report
        4. 默认 → report
        """
        # 1. 技能直接标记
        if skill_type in {"simple_lookup", "lookup"}:
            return "simple"

        # 3. 强分析关键词 → report
        analytical_kw = ("对比", "分析", "格局", "特征", "分化", "差异", "路径",
                         "全面", "深度", "联动", "结构", "画像", "归因")
        if any(kw in query for kw in analytical_kw):
            return "report"

        # 2. 简单查找/排名 + 数据量不大 → simple
        # 注：放宽 is_small 到"总行数 ≤ 30"，不强制单表（很多 hard 工具会返回 2 张辅助表）。
        tables = tool_result.tables or {}
        total_rows = sum(len(rows) for rows in tables.values())
        is_small = total_rows <= 30
        simple_kw = ("是多少", "是哪", "哪些", "前", "最高", "最低", "最大", "最小",
                     "基本信息", "介绍", "代码", "规模是", "收益是", "收益率最",
                     "排名", "总规模", "管理了多少", "持有", "持仓最")
        if is_small and any(kw in query for kw in simple_kw):
            return "simple"

        return "report"

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

        # Drafter 容错：DeepSeek-v4-pro 偶发返回空 content（多见于 thinking 模式占满 token、
        # 长 prompt 截断、provider 临时异常等）。
        # 策略：
        #   1) 第一次正常调用 (max_tokens=8000)
        #   2) 若返回空：用更大 token 预算 + 精简后的 prompt 重试（去掉 market_context 等冗余）
        #   3) 仍为空：返回 ""，由 workflow 节点统一兜底为"友好澄清"而非展示原始数据表
        draft = self._safe_chat_draft(user_prompt, max_tokens=8000)
        if draft and draft.strip():
            return draft

        # 重试用精简版 prompt：去掉 market_context、只展示 20 行数据，降低 token 压力
        compact_tables = self._render_tables(tool_result, max_rows=20)
        compact_prompt = (
            f"用户问题：{query}\n\n"
            f"查询结果数据：\n{compact_tables}{sql_note}\n\n"
            f"报告框架（请严格按此结构撰写）：\n{outline_text}\n\n"
            "请撰写完整的中文分析报告。注意：上次调用未返回内容，请确保本次回答完整、有结论。"
        )
        t_retry = time.perf_counter()
        draft = self._safe_chat_draft(compact_prompt, max_tokens=12000)
        self.last_stage_ms["drafter_llm_retry"] = (time.perf_counter() - t_retry) * 1000
        if draft and draft.strip():
            return draft

        # 两次都为空：返回空串，由上层（analytical_report_writer_node）改为澄清流程，
        # 不暴露原始数据表（用户更愿意看到"请换种问法"而不是 mock 风格的裸表）。
        self.last_outline_source = (self.last_outline_source or "skill") + "+drafter_empty"
        return ""

    def _safe_chat_draft(self, user_prompt: str, max_tokens: int = 8000) -> str:
        """调用 Drafter LLM 并把异常/空返回都归一成 ""，由调用方决定下一步。"""
        try:
            return self.llm_client.chat(
                role="report",
                system_prompt=DRAFTER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_mode=False,
                temperature=0.3,
                max_tokens=max_tokens,
            ) or ""
        except Exception:
            return ""

    # ──────────────────────────────────────────────────────────────────
    # Simple 答案路径（短结论 + 单表 + 追问）
    # ──────────────────────────────────────────────────────────────────

    def _draft_simple_with_llm(
        self,
        query: str,
        tool_result: ToolResult,
        generated_sql: str,
    ) -> str:
        """Simple 模式：一次 LLM 调用，目标是 1-3 句结论 + 1 张表 + 2-3 个追问。

        与 _draft_with_llm 的差异：
        - 系统提示用 SIMPLE_ANSWER_SYSTEM_PROMPT，明确禁止分章节
        - 不注入 market_context、不传 outline
        - 单表只展示 ≤ 15 行，token 控制在 ≤ 2000
        - 仍保留两次重试 + 空兜底，行为与 _draft_with_llm 对称
        """
        if self.llm_client is None:
            raise RuntimeError("LLMClient 未初始化，无法调用 Drafter LLM。")

        tables_text = self._render_tables(tool_result, max_rows=15)
        sql_note = (
            f"\n\n（数据来自一次只读 SQL 查询，已通过白名单校验。）"
            if generated_sql
            else ""
        )
        user_prompt = (
            f"用户问题：{query}\n\n"
            f"查询结果数据：\n{tables_text}{sql_note}\n\n"
            "请按【简洁回答】规范输出：第一行 **直接回答：**，接 1 张表（如果有数据），"
            "结尾给 2-3 个 `💡 您可能还想了解` 的具体追问。**不要写分析报告，不要 ## 章节。**"
        )

        draft = self._safe_simple_chat(user_prompt, max_tokens=2000)
        if draft and draft.strip():
            return draft

        # 一次重试，给更多 token 余量
        t_retry = time.perf_counter()
        draft = self._safe_simple_chat(user_prompt, max_tokens=4000)
        self.last_stage_ms["drafter_llm_retry"] = (time.perf_counter() - t_retry) * 1000
        if draft and draft.strip():
            return draft

        # 两次都空：返回空串，让上层走澄清（与 report 路径同语义）
        self.last_outline_source = (self.last_outline_source or "skill") + "+simple_drafter_empty"
        return ""

    def _safe_simple_chat(self, user_prompt: str, max_tokens: int = 2000) -> str:
        """Simple 路径的 LLM 调用，使用 SIMPLE_ANSWER_SYSTEM_PROMPT。"""
        try:
            return self.llm_client.chat(
                role="report",
                system_prompt=SIMPLE_ANSWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_mode=False,
                temperature=0.2,
                max_tokens=max_tokens,
            ) or ""
        except Exception:
            return ""

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
    def _render_tables(tool_result: ToolResult, max_rows: int = 50) -> str:
        """把所有数据表渲染成 Markdown，附数据口径。max_rows 可下调用于重试 prompt。"""
        parts: list[str] = []
        for table_name, rows in tool_result.tables.items():
            parts.append(f"【{table_name}】")
            parts.append(records_to_markdown(rows, max_rows=max_rows))
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
