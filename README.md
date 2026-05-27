# fund_analysis_agent_pro

基金数据分析 AI Agent 项目，基于本地基金数据构建面向中文问题的垂类数据分析系统。项目支持用户用自然语言查询基金规模、持仓、业绩、基金公司和多表组合问题，并生成结构化分析结果或 HTML 报告。

## 项目特点

- 数据源来自本地 Excel：`规模.xlsx`、`持仓.xlsx`、`业绩.xlsx`
- 使用 SQLite 作为本地结构化数据层，所有真实计算在本地完成
- 使用 LangGraph 编排 Agent workflow
- 支持 Hard Tools 与 LLM Generated SQL 两种分析路径
- 支持多轮对话上下文，能够处理“这些基金”“同样口径”等追问
- 支持 FastAPI / Streamlit / CLI 交互方式
- 支持 HTML 报告导出和批量评测

## 核心设计

本项目的基本原则是：LLM 负责理解问题、规划步骤和组织表达，本地工具与 SQL 负责查询、计算和校验。

Agent workflow 采用 ReAct-style 设计，主要节点包括：

```text
plan -> validate -> act -> observe -> reflect -> report -> self-check
```

其中：

- `plan`：将中文问题转换为结构化分析计划
- `validate`：校验工具名、参数、SQL 安全性和结果可靠性
- `act`：调用 SQL-backed tools 或执行受控 SQL
- `observe`：读取工具或 SQL 返回的结构化结果
- `reflect`：根据校验结果决定继续执行、重试、追问或安全退出
- `report`：基于结构化结果生成中文分析报告
- `self-check`：检查回答是否忠实于数据、是否遗漏口径或存在不当表达

## 两种分析模式

### Hard Tools

LLM 负责理解用户问题并选择工具，实际计算由本地 SQL-backed tools 完成。该模式适合高频、标准化、可控性要求高的问题。

### Generated SQL

LLM 根据受控 schema 生成只读 SQL，本地系统执行白名单校验、dry run、执行和结果校验。该模式适合长尾、多表组合和探索式分析问题。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

项目默认使用本地 SQLite 数据库：

```text
data/processed/fund_agent.db
```

如需从 Excel 重建数据库：

```powershell
.\.venv\Scripts\python.exe main.py --rebuild-db --mode llm --trace "易方达目前旗下所有基金的总规模是多少"
```

## 运行方式

CLI：

```powershell
.\.venv\Scripts\python.exe main.py --mode llm --sql-mode generated --trace "规模前50的主动权益基金中，同时持有贵州茅台和宁德时代的有哪些"
```

FastAPI：

```powershell
.\.venv\Scripts\python.exe app_fastapi.py
```

Streamlit：

```powershell
.\.venv\Scripts\streamlit.exe run app_streamlit.py
```

## 批量评测

评测脚本会读取 `evaluation/batch_questions.yaml`，通过 FastAPI TestClient 批量调用接口，并生成 HTML 汇总报告。

```powershell
.\.venv\Scripts\python.exe evaluation\run_batch_html_reports.py
```

只跑 Generated SQL：

```powershell
.\.venv\Scripts\python.exe evaluation\run_batch_html_reports.py --sql-modes generated
```

只跑指定题目：

```powershell
.\.venv\Scripts\python.exe evaluation\run_batch_html_reports.py --ids q001,q016 --sql-modes generated
```

输出目录：

```text
outputs/evaluations/html_batch_<timestamp>/
```

其中 `index.html` 是评测总览，`states/` 保存每题完整 Agent state，便于调试 planner、SQL、trace 和 errors。

## 技术栈

- Python
- LangGraph
- FastAPI / Streamlit
- SQLite
- pandas / openpyxl
- Pydantic
- LLM API
- HTML report generation
- pytest / batch evaluation
