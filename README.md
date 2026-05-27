# 基金数据分析 AI Agent：Production-oriented Teaching Project

这个项目是一个**供学习的生产级架构示例**，用于完成“基金数据分析 AI Agent”案例。
它不是最终提交版，但代码结构按真实工程项目拆分，方便你学习 LangGraph Agent 项目的模块化设计。

## 支持的示例问题

- 1季度末规模最大的10只主动权益基金是谁
- 1季度末全市场持仓规模最大的股票是哪只
- 对比分析易方达和华夏基金的业务结构
- 筛选1季度收益率前10基金并分析其持仓情况
- 这些基金主要持有哪些股票？（依赖上一轮 memory）

## 技术栈

- Python
- LangGraph：Agent 工作流编排
- SQLite：本地结构化数据层
- pandas + openpyxl：Excel 读取与清洗
- Pydantic：Planner 输出、校验结果、自检结果的 schema
- SQL-backed tools：工具内部使用 SQL 查询
- Tool Registry：工具统一注册与说明
- Plan Validator / Result Validator / Self-check：质量控制
- Memory Store：轻量多轮上下文
- pytest / evaluation：自动化测试与评估

## 快速开始

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

项目包里已经包含一个预构建的 SQLite 数据库 `data/processed/fund_agent.db`，所以通常可以直接运行。
如果你替换了 Excel 数据，才需要重建 SQLite 数据库：

```bash
.\.venv\Scripts\python.exe main.py --rebuild-db --mode mock --trace "1季度末规模最大的10只主动权益基金是谁"
```

之后可以直接运行：

```bash
.\.venv\Scripts\python.exe main.py --mode mock --trace "对比分析易方达和华夏基金的业务结构"
```

交互模式：

```bash
.\.venv\Scripts\python.exe main.py --mode mock --interactive
```

## 推荐阅读顺序

1. `main.py`：程序入口
2. `src/config.py`：配置管理
3. `src/agent/state.py`：LangGraph state
4. `src/agent/workflow.py`：LangGraph nodes / edges
5. `src/agent/schemas.py`：Pydantic schema
6. `src/tools/specs.py` + `src/tools/registry.py`：tool specs 和工具注册
7. `src/agent/planner.py`：自然语言到 plan
8. `src/agent/plan_validator.py`：plan 校验
9. `src/agent/executor.py`：工具执行
10. `src/agent/result_validator.py`：工具结果校验
11. `src/agent/report_writer.py`：报告生成
12. `src/agent/self_check.py`：最终回答自检
13. `src/agent/memory.py`：多轮上下文
14. `src/data/sqlite_store.py`：SQLite 数据层

## 重要设计原则

LLM 不直接计算基金数据，也不直接自由写 SQL。LLM 只负责：

1. Planner：理解用户问题，选择工具和参数。
2. Report Writer：基于工具结果写报告。

所有真实数值计算都由 SQL-backed tools 完成。
