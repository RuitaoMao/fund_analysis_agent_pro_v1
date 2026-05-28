"""HTML 报告导出。

Streamlit 和 CLI 都可以复用这个函数，把一次 Agent 运行结果保存成更适合阅读的
本地 HTML 文件。
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import markdown as markdown_lib
except Exception:  # pragma: no cover - 本地未安装 markdown 时使用轻量 fallback
    markdown_lib = None


def save_html_report(project_root: Path, state: dict[str, Any]) -> Path:
    reports_dir = project_root / "outputs" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = reports_dir / f"report_{stamp}.html"
    latest_path = reports_dir / "latest_report.html"
    content = render_html_report(state)
    report_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")
    return report_path


def render_html_report(state: dict[str, Any]) -> str:
    result = state.get("tool_result")
    tables = result.tables if result else {}
    tool_history = state.get("tool_history", [])
    trace = state.get("trace", [])
    query = html.escape(str(state.get("query", "")))
    final_answer = _markdown_to_html(str(state.get("final_answer", "")))
    mode = html.escape(str(state.get("mode", "")))
    sql_mode = html.escape(str(state.get("sql_mode", "")))
    session_id = html.escape(str(state.get("session_id", "")))

    table_blocks = "\n".join(_render_table(name, rows) for name, rows in tables.items())
    repair_block = _render_sql_repair_suggestions(build_sql_repair_suggestions(state))
    trace_blocks = "\n".join(
        f"""
        <div class="trace-item">
          <div class="trace-node">{html.escape(step.node)}</div>
          <div><b>Action:</b> {html.escape(step.action)}</div>
          <div><b>Observation:</b> {html.escape(step.observation)}</div>
        </div>
        """
        for step in trace
    )
    tools = html.escape(json.dumps(tool_history, ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>基金分析 Agent · 报告</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@600;700&display=swap" rel="stylesheet">
  <style>
    /* efund-inspired theme · 与前端 static/style.css 共享色板
       主色从 https://www.efunds.com.cn/css/index.css 提取：
       #005096 深邃海军蓝（主色，91 次复用）· #24bbe1 亮青强调
       #ffc819 金黄 CTA · #333 主文 · #f5f7fa 页底 · #ffffff 卡片 */
    :root {{
      --efund-navy:      #005096;
      --efund-navy-dark: #00345f;
      --efund-navy-soft: #eff5fb;
      --efund-cyan:      #24bbe1;
      --efund-gold:      #ffc819;
      --efund-gold-dark: #c99700;
      --efund-up:        #009b6d;
      --efund-down:      #ee1533;
      --ink:             #333333;
      --ink-soft:        #555555;
      --muted:           #999999;
      --line:            #dcdfe6;
      --line-strong:    #c1c6c8;
      --bg-page:         #f5f7fa;
      --bg-card:         #ffffff;
      --bg-soft:         #eff2f6;
      --bg-table-head:   #eff5fb;
      --bg-table-zebra:  #f8fafd;
      --font-sans: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", -apple-system, sans-serif;
      --font-serif: "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif;
    }}
    body {{
      margin: 0;
      font-family: var(--font-sans);
      background: var(--bg-page);
      color: var(--ink);
      font-size: 14px;
      line-height: 1.7;
      -webkit-font-smoothing: antialiased;
    }}
    header {{
      background: #fff;
      color: var(--ink);
      padding: 28px 40px 22px;
      border-bottom: 3px solid var(--efund-navy);
    }}
    header h1 {{
      margin: 0 0 8px;
      font-family: var(--font-serif);
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 1px;
      color: var(--efund-navy);
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
      font-family: Consolas, monospace;
    }}
    main {{
      max-width: 1120px;
      margin: 24px auto;
      padding: 0 20px 48px;
    }}
    section {{
      background: var(--bg-card);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 22px 26px;
      margin-bottom: 18px;
      box-shadow: 0 1px 2px rgba(26,26,26,.04), 0 4px 12px rgba(26,26,26,.04);
    }}
    h2 {{
      color: var(--efund-navy);
      margin: 0 0 14px;
      font-family: var(--font-serif);
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.5px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--line);
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--bg-soft);
      border: 1px solid var(--line);
      border-left: 3px solid var(--efund-navy);
      border-radius: 3px;
      padding: 14px 16px;
      line-height: 1.6;
      font-family: Consolas, "JetBrains Mono", monospace;
      font-size: 13px;
      color: var(--ink-soft);
    }}
    .answer-markdown {{
      background: #fff;
      border: 1px solid var(--line);
      border-top: 2px solid var(--efund-navy);
      border-radius: 3px;
      padding: 20px 24px;
      line-height: 1.8;
      overflow-x: auto;
    }}
    .answer-markdown h1,
    .answer-markdown h2,
    .answer-markdown h3,
    .answer-markdown h4 {{
      font-family: var(--font-serif);
      font-weight: 700;
      color: var(--ink);
      margin: 20px 0 10px;
      line-height: 1.4;
    }}
    .answer-markdown h1 {{
      font-size: 22px;
      color: var(--efund-navy);
      border-bottom: 2px solid var(--efund-navy);
      padding-bottom: 6px;
    }}
    .answer-markdown h2 {{
      font-size: 19px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 5px;
    }}
    .answer-markdown h3 {{ font-size: 16px; color: var(--ink-soft); }}
    .answer-markdown h4 {{ font-size: 15px; color: var(--ink-soft); }}
    .answer-markdown p {{ margin: 10px 0; }}
    .answer-markdown ul,
    .answer-markdown ol {{ margin: 8px 0 12px 24px; padding: 0; }}
    .answer-markdown li {{ margin: 5px 0; }}
    .answer-markdown strong {{
      color: var(--efund-navy);
      font-weight: 700;
    }}
    .answer-markdown code {{
      background: var(--bg-soft);
      color: var(--efund-navy);
      border: 1px solid var(--line);
      border-radius: 2px;
      padding: 1px 6px;
      font-family: Consolas, "SFMono-Regular", monospace;
      font-size: 12.5px;
    }}
    .answer-markdown table {{
      min-width: 760px;
      margin: 14px 0;
      background: white;
    }}
    .answer-markdown th, .answer-markdown td {{ white-space: nowrap; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 13.5px;
      background: white;
    }}
    th {{
      text-align: left;
      background: var(--bg-table-head);
      color: var(--ink);
      font-family: var(--font-serif);
      font-weight: 700;
      border: 1px solid var(--line-strong);
      padding: 9px 11px;
    }}
    td {{
      border: 1px solid var(--line);
      padding: 8px 11px;
      vertical-align: top;
      color: var(--ink);
    }}
    table tr:nth-child(even) td {{ background: var(--bg-table-zebra); }}
    .trace-item {{
      border: 1px solid var(--line);
      border-left: 3px solid var(--efund-navy);
      background: #fff;
      padding: 10px 13px;
      margin: 8px 0;
      border-radius: 3px;
      font-size: 13px;
    }}
    .trace-node {{
      font-weight: 700;
      font-family: var(--font-serif);
      color: var(--efund-navy);
      margin-bottom: 4px;
      letter-spacing: 0.3px;
    }}
    .repair-list {{
      margin: 0;
      padding-left: 20px;
      line-height: 1.7;
    }}
    .repair-list li {{ margin: 6px 0; }}
  </style>
</head>
<body>
  <header>
    <h1>基金数据分析 Agent 报告</h1>
    <div class="meta">mode={mode} · sql_mode={sql_mode} · session={session_id}</div>
  </header>
  <main>
    <section>
      <h2>用户问题</h2>
      <pre>{query}</pre>
    </section>
    <section>
      <h2>最终回答</h2>
      <div class="answer-markdown">{final_answer}</div>
    </section>
    <section>
      <h2>结果表</h2>
      {table_blocks or "<p>本轮没有结构化表格结果。</p>"}
    </section>
    <section>
      <h2>调用工具 / SQL</h2>
      <pre>{tools}</pre>
    </section>
    {repair_block}
    <section>
      <h2>Trace</h2>
      {trace_blocks}
    </section>
  </main>
</body>
</html>"""


def build_sql_repair_suggestions(state: dict[str, Any]) -> list[str]:
    """根据 generated SQL 的错误与 trace 给出可执行的修复建议。

    这里不重新生成 SQL，只把常见失败模式翻译成工程上能直接处理的方向。
    """

    result = state.get("tool_result")
    metadata = getattr(result, "metadata", {}) if result else {}
    sql = str(state.get("generated_sql") or metadata.get("sql") or "")
    sql_mode = str(state.get("sql_mode", ""))
    errors = [str(item) for item in state.get("errors", []) + state.get("sql_validation_errors", [])]
    trace = state.get("trace", [])
    for step in trace:
        observation = getattr(step, "observation", "")
        if "[REACT-CORRECTION]" in observation or "error=" in observation:
            errors.append(str(observation))

    if not sql and "generated" not in sql_mode and not errors:
        return []

    suggestions: list[str] = []
    if sql and not errors:
        suggestions.append("本轮 Generated SQL 已通过只读白名单校验、dry run 和结果校验；如果结果不符合预期，优先检查日期、基金公司简称、业绩区间和 LIMIT。")

    lowered_errors = "\n".join(errors).lower()
    if "select *" in lowered_errors:
        suggestions.append("不要使用 SELECT *，改为显式列出需要展示的字段，例如 date、fund_code、fund_name、fund_company、fund_size。")
    if "非白名单表" in lowered_errors or "unknown_tables" in lowered_errors:
        suggestions.append("FROM/JOIN 只能访问 fund_size、fund_holding、fund_performance 三张物理表；CTE 可以使用，但最终来源表仍需在白名单内。")
    if "limit" in lowered_errors:
        suggestions.append("SQL 必须包含 LIMIT，且 LIMIT 不超过 200；报告型问题建议先聚合再 LIMIT，避免截断原始明细后再统计。")
    if "no such column" in lowered_errors or "没有这个字段" in lowered_errors:
        suggestions.append("字段名需要以 SQLite 表结构为准；不确定字段时先查看项目 schema，不要凭 Excel 原始中文列名直接拼 SQL。")
    if "syntax" in lowered_errors or "near" in lowered_errors:
        suggestions.append("检查 SQL 语法、别名和括号；中文别名或带括号的别名建议用英文下划线命名，减少 SQLite 解析问题。")
    if "空" in lowered_errors or "empty" in lowered_errors or "结果为空" in lowered_errors:
        suggestions.append("结果为空时优先放宽过滤条件：日期改用最新日期、公司名用简称匹配、基金代码统一去掉 .OF/.SH/.SZ 后缀。")
    if "replan" in lowered_errors or "[react-correction]" in lowered_errors:
        suggestions.append("本轮触发过 ReAct 纠错；下一次生成 SQL 时会把这些错误作为 previous_errors 传回 SQL Planner，让它重新规划。")

    if sql:
        suggestions.append(f"当前 SQL 摘要：{_compact_sql(sql)}")
    return _dedupe(suggestions)


def _render_sql_repair_suggestions(suggestions: list[str]) -> str:
    if not suggestions:
        return ""
    items = "".join(f"<li>{html.escape(item)}</li>" for item in suggestions)
    return f"""
    <section>
      <h2>SQL 修复建议</h2>
      <ul class="repair-list">{items}</ul>
    </section>
    """


def _render_table(name: str, rows: list[dict[str, Any]], max_rows: int = 50) -> str:
    if not rows:
        return f"<h3>{html.escape(name)}</h3><p>空结果。</p>"
    columns = list(rows[0].keys())
    head = "".join(f"<th>{html.escape(str(col))}</th>" for col in columns)
    body = []
    for row in rows[:max_rows]:
        body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>")
    more = f"<p>仅展示前 {max_rows} 行，共 {len(rows)} 行。</p>" if len(rows) > max_rows else ""
    return f"<h3>{html.escape(name)}</h3><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{more}"


def _markdown_to_html(text: str) -> str:
    if not text.strip():
        return "<p>当前没有生成最终回答。</p>"
    if markdown_lib is not None:
        return markdown_lib.markdown(
            text,
            extensions=["tables", "fenced_code", "sane_lists"],
            output_format="html5",
        )
    return _fallback_markdown_to_html(text)


def _fallback_markdown_to_html(text: str) -> str:
    """轻量 Markdown fallback，保证无第三方包时标题、加粗、表格也能正常显示。"""

    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            level = min(len(stripped) - len(stripped.lstrip("#")), 4)
            title = stripped[level:].strip()
            blocks.append(f"<h{level}>{_format_inline(title)}</h{level}>")
            i += 1
            continue
        if _looks_like_table(lines, i):
            table_html, i = _parse_markdown_table(lines, i)
            blocks.append(table_html)
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append("<ol>" + "".join(f"<li>{_format_inline(item)}</li>" for item in items) + "</ol>")
            continue
        if stripped.startswith(("- ", "* ")):
            items = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            blocks.append("<ul>" + "".join(f"<li>{_format_inline(item)}</li>" for item in items) + "</ul>")
            continue

        paragraph = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not _starts_new_block(lines, i):
            paragraph.append(lines[i].strip())
            i += 1
        blocks.append("<p>" + "<br/>".join(_format_inline(item) for item in paragraph) + "</p>")
    return "\n".join(blocks)


def _starts_new_block(lines: list[str], index: int) -> bool:
    stripped = lines[index].strip()
    return bool(
        re.match(r"^#{1,6}\s+", stripped)
        or _looks_like_table(lines, index)
        or re.match(r"^\d+\.\s+", stripped)
        or stripped.startswith(("- ", "* "))
    )


def _looks_like_table(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    header = lines[index].strip()
    separator = lines[index + 1].strip()
    return header.startswith("|") and header.endswith("|") and bool(re.match(r"^\|?[\s:\-|]+\|[\s:\-|]*$", separator))


def _parse_markdown_table(lines: list[str], index: int) -> tuple[str, int]:
    rows: list[list[str]] = []
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
        index += 1
    if len(rows) < 2:
        return "<p>" + _format_inline(" | ".join(rows[0])) + "</p>", index
    header = rows[0]
    body_rows = rows[2:]
    head = "".join(f"<th>{_format_inline(cell)}</th>" for cell in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{_format_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in body_rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>", index


def _format_inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)


def _compact_sql(sql: str, max_len: int = 260) -> str:
    compact = re.sub(r"\s+", " ", sql).strip()
    return compact if len(compact) <= max_len else compact[: max_len - 3] + "..."


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output
