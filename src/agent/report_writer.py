"""分析报告写作器（Branch 2 全量重构）。

设计思路：
1. 技能选择（report_skills.py）：规则匹配，无需 LLM，生成结构化大纲（ReportOutline）。
2. Drafter LLM（llm 模式）：拿到大纲 + 数据 → 一次 LLM 调用，生成完整中文分析报告。
3. mock 模式：技能大纲 + 数据表格，不调用 LLM，用于测试和离线演示。

ReportWriterAgent 保留原类名，以兼容 app.py / workflow.py 中的构造器。
"""

from __future__ import annotations

from src.agent.report_skills import select_skill
from src.agent.schemas import ReportOutline, ToolResult
from src.llm.client import LLMClient
from src.llm.prompts import DRAFTER_SYSTEM_PROMPT
from src.utils.table_utils import records_to_markdown


class ReportWriterAgent:
    """分析报告写作子智能体（技能驱动 + Drafter LLM）。"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

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
        # 兼容旧调用方（workflow.py 旧版本传入 plan/plan_validation/result_validation）
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
        plan / plan_validation / result_validation
                        旧接口兼容参数，新版不使用。
        """
        if tool_result is None:
            return "未能得到可用结果，请尝试换一种提问方式或放宽查询条件。"

        skill = select_skill(query, tool_result)
        outline = skill.outline(query, tool_result)

        if mode == "mock":
            return self._mock_report(query, tool_result, outline, generated_sql, result_validation)
        return self._llm_report(query, tool_result, outline, generated_sql)

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
        """结构化 Mock 报告：大纲框架 + 原始数据表格。"""
        parts: list[str] = []

        # 直接回答（简单问题置顶）
        if outline.direct_answer:
            parts.append(f"**直接回答：** {outline.direct_answer}")

        # 报告框架（章节标题 + 分析要点）
        if outline.sections:
            parts.append("\n---\n")
            parts.append(f"**报告框架（技能：{outline.skill_type}）**")
            for i, section in enumerate(outline.sections, 1):
                parts.append(f"\n## {i}. {section.title}")
                if section.analytical_angles:
                    for angle in section.analytical_angles:
                        parts.append(f"- {angle}")

        # 原始数据表格
        if tool_result.tables:
            parts.append("\n---\n")
            parts.append("## 查询数据")
            for table_name, rows in tool_result.tables.items():
                parts.append(f"\n### {table_name}")
                parts.append(records_to_markdown(rows, max_rows=50))

        # SQL 口径说明（generated SQL 路径）
        if generated_sql:
            parts.append(f"\n**SQL 口径**（已通过只读校验和 dry run）：\n```sql\n{generated_sql}\n```")

        # 数据口径说明
        if tool_result.notes:
            parts.append("\n---\n**数据口径**")
            parts.extend(f"- {note}" for note in tool_result.notes)

        # 数据质量警告
        warnings: list[str] = list(tool_result.warnings)
        if result_validation and result_validation.warnings:
            warnings.extend(result_validation.warnings)
        warnings = list(dict.fromkeys(warnings))
        if warnings:
            parts.append("\n**注意事项**")
            parts.extend(f"- {w}" for w in warnings)

        return "\n".join(parts)

    # ──────────────────────────────────────────────────────────────────
    # LLM 模式（Drafter）
    # ──────────────────────────────────────────────────────────────────

    def _llm_report(
        self,
        query: str,
        tool_result: ToolResult,
        outline: ReportOutline,
        generated_sql: str,
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

        user_prompt = (
            f"用户问题：{query}\n\n"
            f"查询结果数据：\n{tables_text}"
            f"{sql_note}\n\n"
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
    # 内部工具方法
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _render_tables(tool_result: ToolResult) -> str:
        """把所有数据表渲染成 Markdown，附上数据口径说明。"""
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
        """把 ReportOutline 渲染成 Drafter prompt 内的文本指令。"""
        parts: list[str] = []
        if outline.direct_answer:
            parts.append(f"0. **直接回答**（必须放报告第一行粗体）：{outline.direct_answer}")
        for i, section in enumerate(outline.sections, 1):
            parts.append(f"{i}. ## {section.title}")
            for angle in section.analytical_angles:
                parts.append(f"   - {angle}")
        return "\n".join(parts)
