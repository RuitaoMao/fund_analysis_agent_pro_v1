"""批量调用 FastAPI 后端并收集 HTML 报告。

读取一个 YAML 问题文件，逐条调用 /api/ask（TestClient，无需启动真实服务器）。
对每题做 "关键词命中" 初步质量检查，生成彩色汇总 index.html 和 summary.json。

运行方式：
    # 跑所有题目（默认 YAML）
    python evaluation/run_batch_html_reports.py

    # 指定问题文件
    python evaluation/run_batch_html_reports.py --questions evaluation/batch_questions.yaml

    # 只跑 generated SQL 路径
    python evaluation/run_batch_html_reports.py --sql-modes generated

    # 只跑某几题（按 id 过滤）
    python evaluation/run_batch_html_reports.py --ids q001,q005,q010
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ──────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────

def _safe_filename(text: str) -> str:
    import re
    text = re.sub(r"[^\w一-鿿-]+", "_", text, flags=re.UNICODE).strip("_")
    return text[:80] or "question"


def _dump_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _dump_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dump_jsonable(v) for v in value]
    return value


def check_keywords(answer: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    """返回 (命中关键词列表, 未命中关键词列表)。"""
    hit, miss = [], []
    for kw in keywords:
        if kw in answer:
            hit.append(kw)
        else:
            miss.append(kw)
    return hit, miss


def judge_row(row: dict[str, Any]) -> str:
    """返回 'PASS' / 'WARN' / 'FAIL'。"""
    if row.get("exception"):
        return "FAIL"
    state = row.get("state") or {}
    answer = str(state.get("final_answer") or "")
    errors = state.get("errors") or []
    keywords = row.get("expected_keywords") or []
    expect_empty = row.get("expect_empty", False)
    not_contain = row.get("expected_not_contain") or []

    if state.get("needs_clarification"):
        return "WARN"
    if not answer:
        return "FAIL"
    if errors:
        return "WARN"
    if keywords:
        _, miss = check_keywords(answer, keywords)
        if miss:
            return "WARN"
    # 负向关键词：答案里不该出现的内容（如错误方向的数值）
    for bad in not_contain:
        if bad in answer:
            row.setdefault("not_contain_violations", []).append(bad)
    if row.get("not_contain_violations"):
        return "WARN"
    if expect_empty:
        empty_indicators = ["没有", "无", "空", "0 行", "未找到", "不存在", "无满足", "查询结果为空", "0只", "没有符合"]
        if not any(ind in answer for ind in empty_indicators):
            return "WARN"
    return "PASS"


# ──────────────────────────────────────────────────────────────────
# YAML 读取
# ──────────────────────────────────────────────────────────────────

def load_questions(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    questions = data.get("questions") or []
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"问题文件没有 questions 列表：{path}")
    return defaults, questions


# ──────────────────────────────────────────────────────────────────
# index.html 生成
# ──────────────────────────────────────────────────────────────────

_STATUS_COLOR = {"PASS": "#dcfce7", "WARN": "#fef9c3", "FAIL": "#fee2e2"}
_STATUS_BADGE = {
    "PASS": '<span style="background:#16a34a;color:white;padding:2px 8px;border-radius:4px;font-size:12px">PASS</span>',
    "WARN": '<span style="background:#d97706;color:white;padding:2px 8px;border-radius:4px;font-size:12px">WARN</span>',
    "FAIL": '<span style="background:#dc2626;color:white;padding:2px 8px;border-radius:4px;font-size:12px">FAIL</span>',
}


def write_index(out_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    items = []
    for row in rows:
        verdict = row.get("verdict", "FAIL")
        bg = _STATUS_COLOR.get(verdict, "#fff")
        badge = _STATUS_BADGE.get(verdict, verdict)
        state = row.get("state") or {}
        answer = str(state.get("final_answer") or state.get("clarification_question") or "")[:220]
        answer_html = html.escape(answer).replace("\n", " ")
        errors = ", ".join(str(e) for e in (state.get("errors") or []))
        kw_miss = row.get("keywords_missing") or []
        bad_kw = row.get("not_contain_violations") or []
        cross_note = row.get("cross_mode_note", "")
        kw_hint = "; ".join(filter(None, [
            f'缺少关键词: {", ".join(kw_miss)}' if kw_miss else "",
            f'禁止词命中: {", ".join(bad_kw)}' if bad_kw else "",
            cross_note,
        ]))
        report_link = f'<a href="{html.escape(row["html_file"])}" target="_blank">📄 报告</a>' if row.get("html_file") else "-"
        items.append(f"""
          <tr style="background:{bg}">
            <td style="font-weight:600">{html.escape(row["id"])}</td>
            <td>{badge}</td>
            <td><code>{html.escape(row["sql_mode"])}</code></td>
            <td style="color:#6b7280">{row["duration_sec"]}s</td>
            <td>{html.escape(row["query"])}</td>
            <td>{report_link}</td>
            <td style="font-size:13px;color:#374151">{answer_html}</td>
            <td style="font-size:12px;color:#b91c1c">{html.escape(errors or kw_hint)}</td>
          </tr>""")

    pass_count = summary["pass"]
    warn_count = summary["warn"]
    fail_count = summary["fail"]
    total = summary["total_jobs"]
    score_pct = round(pass_count / total * 100) if total else 0

    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>基金 Agent — Batch Evaluation</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
            background: #f0f9ff; color: #0f172a; margin: 0; }}
    header {{ background: #1d4ed8; color: white; padding: 24px 40px;
              display: flex; justify-content: space-between; align-items: center; }}
    header h1 {{ margin: 0; font-size: 22px; }}
    .scoreboard {{ display: flex; gap: 20px; }}
    .score-chip {{ background: rgba(255,255,255,0.15); border-radius: 8px;
                  padding: 8px 16px; text-align: center; }}
    .score-chip .num {{ font-size: 28px; font-weight: 700; }}
    .score-chip .lbl {{ font-size: 12px; opacity: 0.85; }}
    main {{ max-width: 1400px; margin: 24px auto; padding: 0 20px 40px; }}
    table {{ width: 100%; border-collapse: collapse; background: white;
             border: 1px solid #bfdbfe; border-radius: 8px; overflow: hidden;
             box-shadow: 0 1px 6px rgba(0,0,0,.06); }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #e5e7eb;
              text-align: left; vertical-align: top; }}
    th {{ background: #eff6ff; color: #1e40af; font-size: 13px; }}
    tr:last-child td {{ border-bottom: none; }}
    a {{ color: #1d4ed8; font-weight: 600; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .meta {{ color: #64748b; font-size: 13px; margin-bottom: 16px; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>基金分析 Agent — Batch Evaluation</h1>
      <div style="font-size:13px;opacity:.8;margin-top:4px">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &nbsp;·&nbsp; {total} 道题 / {summary["total_jobs"]} 个子任务</div>
    </div>
    <div class="scoreboard">
      <div class="score-chip"><div class="num">{score_pct}%</div><div class="lbl">通过率</div></div>
      <div class="score-chip" style="background:#16a34a"><div class="num">{pass_count}</div><div class="lbl">PASS</div></div>
      <div class="score-chip" style="background:#d97706"><div class="num">{warn_count}</div><div class="lbl">WARN</div></div>
      <div class="score-chip" style="background:#dc2626"><div class="num">{fail_count}</div><div class="lbl">FAIL</div></div>
    </div>
  </header>
  <main>
    <p class="meta">
      问题文件: {html.escape(summary.get("question_file",""))} &nbsp;·&nbsp;
      耗时合计: {summary.get("elapsed_total_sec","?")}s &nbsp;·&nbsp;
      输出目录: {html.escape(summary.get("output_dir",""))}
    </p>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>结果</th><th>SQL 路径</th><th>耗时</th>
          <th>问题</th><th>报告</th><th>回答摘要</th><th>问题说明</th>
        </tr>
      </thead>
      <tbody>{''.join(items)}</tbody>
    </table>
  </main>
</body>
</html>"""
    (out_dir / "index.html").write_text(content, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────

def _check_server_conflict(port: int = 8000) -> None:
    """如果 uvicorn 服务器正在运行，评测脚本与其共用同一个 SQLite 文件会产生跨进程写锁冲突
    （Python threading.Lock 只在同一个 OS 进程内有效）。
    检测到端口占用时给出警告，让用户决定是否继续。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(
                f"\n⚠️  警告：检测到端口 {port} 正在被占用（很可能是 uvicorn 服务器）。\n"
                "   评测脚本会直接嵌入加载 app，与运行中的 uvicorn 共用 SQLite DB，\n"
                "   可能导致跨进程写锁冲突（HTTP 500）。\n"
                "   建议先停止 uvicorn，再运行评测。\n"
                "   继续运行请按 Enter，退出请按 Ctrl+C ...",
                flush=True,
            )
            try:
                input()
            except KeyboardInterrupt:
                print("已取消。")
                sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="批量运行基金 Agent 评测，输出 HTML 报告")
    parser.add_argument("--questions", default="evaluation/batch_questions.yaml", help="YAML 问题文件路径")
    parser.add_argument("--out-dir", default=None, help="输出目录；默认 outputs/evaluations/html_batch_<timestamp>")
    parser.add_argument("--sql-modes", default=None,
                        help="强制覆盖所有题目的 SQL 路径（hard/generated/hard,generated）")
    parser.add_argument("--ids", default=None, help="只跑指定 id，逗号分隔，例如 q001,q005")
    args = parser.parse_args()

    _check_server_conflict()  # 检测是否与 uvicorn 冲突

    question_path = Path(args.questions)
    if not question_path.is_absolute():
        question_path = PROJECT_ROOT / question_path
    defaults, questions = load_questions(question_path)

    # 全局 sql_mode 覆盖（命令行优先）
    global_modes_override: list[str] | None = None
    if args.sql_modes:
        global_modes_override = [m.strip() for m in args.sql_modes.split(",") if m.strip()]

    # ID 过滤
    id_filter: set[str] | None = None
    if args.ids:
        id_filter = {s.strip() for s in args.ids.split(",") if s.strip()}

    # 输出目录
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "outputs" / "evaluations" / f"html_batch_{stamp}"
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # FastAPI TestClient（不需要启动服务器）
    # DB 路径由 Settings.load() 从 .env 读取，无需手动覆盖。
    from app_fastapi import app  # noqa: PLC0415
    from starlette.testclient import TestClient
    client = TestClient(app, raise_server_exceptions=False)

    # 展开 job 列表
    default_mode = str(defaults.get("mode", "llm"))
    default_max_steps = int(defaults.get("max_steps", 3))
    default_long_memory = bool(defaults.get("use_long_memory", False))
    default_sql_modes_str = str(defaults.get("sql_modes") or defaults.get("sql_mode") or "generated")

    jobs: list[dict[str, Any]] = []
    for i, item in enumerate(questions, start=1):
        qid = str(item.get("id") or f"q{i:03d}")
        if id_filter and qid not in id_filter:
            continue

        # 确定本题跑哪些 sql_mode
        if global_modes_override:
            item_sql_modes = global_modes_override
        elif item.get("sql_modes"):
            item_sql_modes = [m.strip() for m in str(item["sql_modes"]).split(",") if m.strip()]
        elif item.get("sql_mode"):
            item_sql_modes = [str(item["sql_mode"]).strip()]
        else:
            item_sql_modes = [m.strip() for m in default_sql_modes_str.split(",") if m.strip()]

        for sql_mode in item_sql_modes:
            jobs.append({
                "id": qid,
                "query": str(item["query"]),
                "category": str(item.get("category") or ""),
                "mode": str(item.get("mode") or default_mode),
                "sql_mode": sql_mode,
                "max_steps": int(item.get("max_steps") or default_max_steps),
                "use_long_memory": bool(item.get("use_long_memory", default_long_memory)),
                "session_id": f"eval-{stamp}-{qid}-{sql_mode}",
                "expected_keywords": list(item.get("expected_keywords") or []),
                "expected_not_contain": list(item.get("expected_not_contain") or []),
                "expect_empty": bool(item.get("expect_empty", False)),
                "cross_mode_check": bool(item.get("cross_mode_check", False)),
            })

    print(f"评测开始：{len(jobs)} 个子任务，输出目录 {out_dir}\n")
    eval_start = time.perf_counter()
    rows: list[dict[str, Any]] = []

    for idx, job in enumerate(jobs, start=1):
        qid = job["id"]
        sql_mode = job["sql_mode"]
        query = job["query"]
        print(f"[{idx:>2}/{len(jobs)}] {qid} [{sql_mode}]  {query[:60]}", flush=True)

        t0 = time.perf_counter()
        row: dict[str, Any] = {**job, "ok": False, "duration_sec": 0.0, "html_file": "", "exception": ""}

        try:
            resp = client.post(
                "/api/ask",
                json={
                    "query": query,
                    "session_id": job["session_id"],
                    "mode": job["mode"],
                    "sql_mode": sql_mode,
                    "max_steps": job["max_steps"],
                    "use_long_memory": job["use_long_memory"],
                },
                timeout=300,
            )
            resp.raise_for_status()
            state = resp.json()
            row["state"] = _dump_jsonable(state)

            # 关键词命中检查
            answer = str(state.get("final_answer") or "")
            hit, miss = check_keywords(answer, job["expected_keywords"])
            row["keywords_hit"] = hit
            row["keywords_missing"] = miss

            # 复制 HTML 报告
            filename = f"{qid}_{sql_mode}_{_safe_filename(query)}.html"
            report_path = state.get("html_report_path")
            if report_path and Path(report_path).exists():
                import shutil
                shutil.copy2(report_path, out_dir / filename)
            else:
                (out_dir / filename).write_text(
                    f"<html><body><h1>{html.escape(qid)} / {html.escape(sql_mode)}</h1>"
                    f"<p>问题：{html.escape(query)}</p>"
                    f"<pre>{html.escape(answer[:3000])}</pre></body></html>",
                    encoding="utf-8",
                )
            row["html_file"] = filename
            row["ok"] = bool(answer) and not state.get("needs_clarification")

        except Exception as exc:  # noqa: BLE001
            row["exception"] = f"{type(exc).__name__}: {exc}"
            filename = f"{qid}_{sql_mode}_ERROR.html"
            (out_dir / filename).write_text(
                f"<html><body><h1>{html.escape(qid)} 运行失败</h1>"
                f"<pre>{html.escape(row['exception'])}</pre></body></html>",
                encoding="utf-8",
            )
            row["html_file"] = filename
            print(f"         ✗ EXCEPTION: {row['exception']}", flush=True)

        finally:
            row["duration_sec"] = round(time.perf_counter() - t0, 2)

        # 最终判定
        row["verdict"] = judge_row(row)
        verdict_icon = {"PASS": "✓", "WARN": "△", "FAIL": "✗"}.get(row["verdict"], "?")
        miss_info = f"  缺: {row.get('keywords_missing')}" if row.get("keywords_missing") else ""
        bad_info = f"  禁: {row.get('not_contain_violations')}" if row.get("not_contain_violations") else ""
        print(f"         {verdict_icon} {row['verdict']}  {row['duration_sec']}s{miss_info}{bad_info}", flush=True)
        rows.append(row)

    # ── 跨模式一致性检查（cross_mode_check=true 的题目）──────────────────
    import re as _re
    cross_checked: set[str] = set()
    for row in rows:
        qid = row["id"]
        if not row.get("cross_mode_check") or qid in cross_checked:
            continue
        cross_checked.add(qid)
        pair = [r for r in rows if r["id"] == qid and not r.get("exception")]
        if len(pair) < 2:
            continue
        # 从 final_answer 中提取核心财务数值用于比对。
        # 策略：优先找"数字+亿"（最可靠）；退而忽略年份范围（1990-2030），取第一个有意义数值。
        def _key_number(text: str) -> float | None:
            t = text or ""
            m = _re.search(r"([\d,，]+(?:\.\d+)?)\s*亿", t)
            if m:
                try:
                    return float(m.group(1).replace(",", "").replace("，", ""))
                except ValueError:
                    pass
            for c in _re.findall(r"-?\d+(?:\.\d+)?", t):
                v = float(c)
                if not (1990 <= v <= 2030):   # 排除年份数字
                    return v
            return None
        nums = [_key_number(str((r.get("state") or {}).get("final_answer", ""))) for r in pair]
        valid = [n for n in nums if n is not None]
        if len(valid) >= 2:
            lo, hi = min(valid), max(valid)
            ref = abs(hi) or 1
            if (hi - lo) / ref > 0.20:   # 差距超过 20% → WARN
                for r in pair:
                    if r["verdict"] == "PASS":
                        r["verdict"] = "WARN"
                        r.setdefault("cross_mode_note", f"hard/generated首个数值差距>{20}%: {nums}")
                        print(f"   ⚠ cross_mode_check {qid}: 数值差距 {nums}", flush=True)

    elapsed = round(time.perf_counter() - eval_start, 1)

    # 统计
    verdict_counts: dict[str, int] = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for row in rows:
        verdict_counts[row.get("verdict", "FAIL")] += 1

    summary = {
        "question_file": str(question_path),
        "output_dir": str(out_dir),
        "timestamp": stamp,
        "total": len(questions),
        "total_jobs": len(rows),
        "pass": verdict_counts["PASS"],
        "warn": verdict_counts["WARN"],
        "fail": verdict_counts["FAIL"],
        "elapsed_total_sec": elapsed,
        "rows": [
            {k: v for k, v in row.items() if k != "state"}  # state 太大，单独存
            for row in rows
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # 每题完整 state 存到独立文件
    states_dir = out_dir / "states"
    states_dir.mkdir(exist_ok=True)
    for row in rows:
        if row.get("state"):
            fname = f"{row['id']}_{row['sql_mode']}.json"
            (states_dir / fname).write_text(
                json.dumps(row["state"], ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )

    write_index(out_dir, rows, summary)

    print("\n" + "═" * 55)
    print(f"  评测完成  耗时 {elapsed}s  通过率 {verdict_counts['PASS']}/{len(rows)}")
    print(f"  PASS {verdict_counts['PASS']}  WARN {verdict_counts['WARN']}  FAIL {verdict_counts['FAIL']}")
    print(f"  Index:  {out_dir / 'index.html'}")
    print("═" * 55)


if __name__ == "__main__":
    main()
