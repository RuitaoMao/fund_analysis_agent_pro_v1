// 基金分析 Agent 前端逻辑
// 关键点：
// - sessionId 每个 tab 独立，多窗口并发互不干扰
// - 优先用 SSE 流式接口，失败回退到 /api/ask
// - clarify 通过 /api/resume 提交补充答复

// marked.js 配置：开启 GFM 表格、代码块
if (typeof marked !== "undefined") {
  marked.setOptions({ breaks: true, gfm: true });
}
function renderMd(text) {
  if (typeof marked !== "undefined") {
    return marked.parse(String(text));
  }
  // fallback：只做换行
  return escapeHtml(text).replace(/\n/g, "<br>");
}

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const traceList = $("trace_list");

let sessionId = `web-${Math.random().toString(36).slice(2, 10)}`;
$("session_id").textContent = sessionId;

let isLoading = false;
let pendingClarification = false;

function appendMsg(role, content, opts = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (opts.html) {
    div.innerHTML = content;
  } else {
    div.textContent = content;
  }
  if (opts.meta) {
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = opts.meta;
    div.appendChild(meta);
  }
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function clearTrace() {
  traceList.innerHTML = "";
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function appendTrace(step, nextAction) {
  if (!step) return;
  const obs = step.observation || "";
  let cls = "";
  if (obs.includes("[REACT-FALLBACK]")) cls = "react-fallback";
  else if (obs.includes("[REACT-CORRECTION]")) cls = "react-correction";
  else if (obs.includes("[REACT-REPLAN]")) cls = "react-replan";
  else if (obs.includes("[TOOL]")) cls = "tool";
  else if (obs.includes("[SQL]")) cls = "sql";

  // 每个节点都带 duration_ms（workflow._timed_node 装饰器写入）
  // 报告写作节点的 observation 里还有内部分段（skill/outliner/drafter），渲染为 tooltip
  let timingBadge = "";
  if (typeof step.duration_ms === "number") {
    const ms = step.duration_ms;
    const text = ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : Math.round(ms) + "ms";
    const breakdown = obs.match(/total=\d+ms\s*\[([^\]]+)\]/);
    const tooltip = breakdown ? escapeHtml(breakdown[1]) : "本节点墙钟耗时";
    timingBadge = ` <span class="timing-badge" title="${tooltip}">⏱ ${text}</span>`;
  }

  const div = document.createElement("div");
  div.className = `trace-step ${cls}`;
  div.innerHTML = `
    <div class="node">${escapeHtml(step.node)}${timingBadge}</div>
    <div class="action">${escapeHtml(step.action || "")}</div>
    <div class="obs">${escapeHtml(obs)}</div>
  `;
  traceList.appendChild(div);
  traceList.scrollTop = traceList.scrollHeight;
}

function renderTablesFromState(state) {
  const result = state.tool_result;
  if (!result || !result.tables) return "";
  let html = "";
  for (const [name, rows] of Object.entries(result.tables)) {
    if (!Array.isArray(rows) || rows.length === 0) continue;
    html += `<div><b>${name}</b></div><table><thead><tr>`;
    const cols = Object.keys(rows[0]);
    cols.forEach((c) => (html += `<th>${c}</th>`));
    html += "</tr></thead><tbody>";
    rows.slice(0, 20).forEach((row) => {
      html += "<tr>";
      cols.forEach((c) => (html += `<td>${row[c] ?? ""}</td>`));
      html += "</tr>";
    });
    html += "</tbody></table>";
  }
  return html;
}

async function sendNonStream(query) {
  const body = {
    query,
    session_id: sessionId,
    mode: $("mode").value,
    sql_mode: $("sql_mode").value,
    max_steps: parseInt($("max_steps").value, 10) || 3,
    use_long_memory: $("use_long_memory").checked,
  };
  const endpoint = pendingClarification ? "/api/resume" : "/api/ask";
  const payload = pendingClarification
    ? { user_response: query, session_id: sessionId }
    : body;
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

function sendStream(query) {
  return new Promise((resolve, reject) => {
    if (pendingClarification) {
      // resume 走非流式
      sendNonStream(query).then(resolve).catch(reject);
      return;
    }
    const params = new URLSearchParams({
      query,
      session_id: sessionId,
      mode: $("mode").value,
      sql_mode: $("sql_mode").value,
      max_steps: $("max_steps").value || "3",
      use_long_memory: $("use_long_memory").checked ? "true" : "false",
    });
    const url = `/api/stream?${params.toString()}`;
    const source = new EventSource(url);
    let finalState = null;

    source.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "node_update") {
          appendTrace(data.trace_step, data.next_action);
        } else if (data.type === "interrupt") {
          source.close();
          resolve({ needs_clarification: true, clarification_question: data.question });
        } else if (data.type === "final") {
          finalState = data.state;
          source.close();
          resolve(finalState);
        } else if (data.type === "error") {
          source.close();
          reject(new Error(data.message));
        }
      } catch (err) {
        console.error("SSE parse error", err, e.data);
      }
    };
    source.onerror = (_evt) => {
      source.close();
      if (finalState) {
        resolve(finalState);
      } else {
        reject(new Error("SSE 连接中断（后端序列化异常或网络断开），请查看服务端日志。"));
      }
    };
  });
}

async function send() {
  if (isLoading) return;
  const queryEl = $("query");
  const q = queryEl.value.trim();
  if (!q) return;
  isLoading = true;
  $("send_btn").disabled = true;
  $("send_btn").textContent = "思考中…";

  if (pendingClarification) {
    appendMsg("user", q, { meta: "补充答复" });
  } else {
    appendMsg("user", q);
    clearTrace();
  }
  queryEl.value = "";

  const thinking = appendMsg("assistant", "正在思考", { html: true });
  thinking.innerHTML = '<span class="thinking">正在思考</span>';

  try {
    const useStream = $("use_stream").checked;
    const state = useStream ? await sendStream(q) : await sendNonStream(q);

    if (state.needs_clarification) {
      pendingClarification = true;
      thinking.classList.remove("assistant");
      thinking.classList.add("clarification");
      thinking.innerHTML = `<div class="clarify-icon">❓</div><div class="clarify-text">${escapeHtml(state.clarification_question)}</div><div class="meta">请在下方输入框补充信息后再次提交。</div>`;
    } else {
      pendingClarification = false;
      const answer = state.final_answer || state.draft_answer || "（无回答）";
      // 用 marked 渲染 Markdown
      thinking.classList.add("md-content");
      thinking.innerHTML = renderMd(answer);
      // 报告按钮（可直接在浏览器里打开）
      if (state.html_report_url) {
        const btn = document.createElement("div");
        btn.className = "report-bar";
        btn.innerHTML = `<a href="${state.html_report_url}" target="_blank" class="report-btn">📄 查看完整 HTML 报告</a>`;
        thinking.appendChild(btn);
      }
    }
  } catch (err) {
    thinking.innerHTML = `❌ 出错：${err.message || err}`;
    thinking.classList.add("clarification");
  } finally {
    isLoading = false;
    $("send_btn").disabled = false;
    $("send_btn").textContent = "提问";
  }
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

$("send_btn").addEventListener("click", send);
$("query").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    send();
  }
});

$("new_session_btn").addEventListener("click", async () => {
  await fetch(`/api/session/${sessionId}`, { method: "DELETE" });
  sessionId = `web-${Math.random().toString(36).slice(2, 10)}`;
  $("session_id").textContent = sessionId;
  chat.innerHTML = "";
  clearTrace();
  pendingClarification = false;
  appendMsg("system", `已开启新 session: ${sessionId}`);
});

document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => {
    $("query").value = btn.textContent.trim();
    $("query").focus();
  });
});

appendMsg("system", `当前 session: ${sessionId}。可以同时打开多个 tab 并发提问。`);
