/* LLM Gateway 管理控制台前端逻辑 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let ADMIN_TOKEN = localStorage.getItem("lgw_admin_token") || "";
let logPage = 0, logLimit = 50, logTotal = 0;

async function api(path, opts = {}) {
  opts.headers = Object.assign({ "X-Admin-Token": ADMIN_TOKEN }, opts.headers || {});
  if (opts.json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    showTokenGate();
    throw new Error("管理令牌无效");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, ms = 2200) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), ms);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTs(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/* ---------------- token gate ---------------- */
function showTokenGate() {
  $("#tokenGate").classList.remove("hidden");
  $("#app").classList.add("hidden");
}

$("#tokenBtn").onclick = async () => {
  ADMIN_TOKEN = $("#tokenInput").value.trim();
  try {
    await api("/api/status");
    localStorage.setItem("lgw_admin_token", ADMIN_TOKEN);
    boot();
  } catch (e) {
    $("#tokenErr").textContent = e.message;
  }
};
$("#tokenInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#tokenBtn").click(); });

/* ---------------- navigation ---------------- */
$$(".sidebar nav a").forEach((a) => {
  a.onclick = () => {
    $$(".sidebar nav a").forEach((x) => x.classList.remove("active"));
    a.classList.add("active");
    $$(".page").forEach((p) => p.classList.remove("active"));
    $("#page-" + a.dataset.page).classList.add("active");
    loadPage(a.dataset.page);
  };
});

function loadPage(page) {
  if (page === "dashboard") loadDashboard();
  if (page === "providers") loadProviders();
  if (page === "test") loadTest();
  if (page === "keys") loadKeys();
  if (page === "logs") loadLogs();
  if (page === "settings") loadSettings();
}

/* ---------------- model test ---------------- */
let testProviders = [];

async function loadTest() {
  testProviders = await api("/api/providers");
  if (!testProviders.length) {
    $("#tProv").innerHTML = '<option value="">（请先添加上游）</option>';
    $("#tModelList").innerHTML = "";
    return;
  }
  const cur = $("#tProv").value;
  $("#tProv").innerHTML = testProviders.map((p) =>
    `<option value="${p.id}">${esc(p.name)}${p.enabled ? "" : "（已停用）"}</option>`).join("");
  if (cur) $("#tProv").value = cur;
  fillTestModels();
}

function fillTestModels(models) {
  const p = testProviders.find((x) => x.id == $("#tProv").value);
  let list = models || (p ? (p.models || []) : []);
  list = list.filter((m) => m !== "*");
  $("#tModelList").innerHTML = list.map((m) => `<option value="${esc(m)}">`).join("");
  if (list.length && !list.includes($("#tModel").value)) $("#tModel").value = list[0];
}

$("#tProv").addEventListener("change", () => fillTestModels());

$("#tRefreshModels").onclick = async () => {
  const pid = +$("#tProv").value;
  if (!pid) return;
  toast("正在从上游拉取模型列表...");
  try {
    const r = await api(`/api/providers/${pid}/check`, { method: "POST" });
    if (r.ok && r.models && r.models.length) {
      fillTestModels(r.models);
      toast(`已获取 ${r.models.length} 个模型`);
    } else {
      toast("未能获取模型列表: " + (r.error || "上游未返回模型"));
    }
  } catch (e) { toast(e.message); }
};

$("#tSend").onclick = async () => {
  const pid = +$("#tProv").value;
  const model = $("#tModel").value.trim();
  if (!pid) { toast("请先添加并选择上游"); return; }
  if (!model) { toast("请填写模型名"); return; }
  const out = $("#tOut"), meta = $("#tMeta");
  out.textContent = "";
  out.classList.remove("err-text");
  meta.textContent = "请求中...";
  const payload = {
    provider_id: pid, model,
    message: $("#tMsg").value,
    system: $("#tSystem").value,
    stream: $("#tStream").checked,
  };
  if (!payload.stream) {
    try {
      const r = await api("/api/test", { json: payload });
      out.textContent = r.reply || "（空回复）";
      meta.textContent = `延迟 ${r.latency_ms ?? "-"}ms · prompt ${r.prompt_tokens ?? "-"} · completion ${r.completion_tokens ?? "-"} · total ${r.total_tokens ?? "-"}`;
    } catch (e) {
      out.textContent = e.message;
      out.classList.add("err-text");
      meta.textContent = "";
    }
    return;
  }
  // 流式：fetch 读取 SSE 并逐段渲染
  try {
    const resp = await fetch("/api/test", {
      method: "POST",
      headers: { "X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      let msg = resp.statusText;
      try { msg = (await resp.json()).error.message || msg; } catch (e) {}
      throw new Error(msg);
    }
    meta.textContent = "";
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const data = line.slice(5).trim();
        if (!data || data === "[DONE]") continue;
        try {
          const obj = JSON.parse(data);
          if (obj.error) {
            out.textContent += `\n[错误] ${obj.error.message || JSON.stringify(obj.error)}`;
            continue;
          }
          const delta = obj.choices && obj.choices[0] &&
            obj.choices[0].delta && obj.choices[0].delta.content;
          if (delta) out.textContent += delta;
          if (obj.usage) {
            meta.textContent = `prompt ${obj.usage.prompt_tokens} · completion ${obj.usage.completion_tokens} · total ${obj.usage.total_tokens}`;
          }
        } catch (e) { /* 忽略不完整 JSON */ }
      }
    }
    if (!out.textContent) out.textContent = "（空回复）";
  } catch (e) {
    out.textContent = e.message;
    out.classList.add("err-text");
    meta.textContent = "";
  }
};

/* ---------------- dashboard ---------------- */
async function loadDashboard() {
  const [st, ver] = await Promise.all([api("/api/stats"), api("/api/status")]);
  $("#ver").textContent = "v" + ver.version;
  $("#stToday").textContent = st.today.total;
  $("#stSucc").textContent = st.today.total ? Math.round((st.today.success / st.today.total) * 100) + "%" : "-";
  $("#stLat").textContent = st.today.avg_latency_ms || "-";
  $("#stTok").textContent = st.today.avg_tokens || "-";
  $("#stTotal").textContent = st.all.total;

  const rows = st.per_provider.map((r) =>
    `<tr><td>${esc(r.provider_name || "-")}</td><td>${r.c}</td><td>${r.ok}</td><td>${Math.round(r.avg_lat || 0)}ms</td></tr>`);
  $("#tblProvStats").innerHTML = `<tr><th>上游</th><th>请求</th><th>成功</th><th>均延迟</th></tr>` +
    (rows.join("") || `<tr><td colspan="4" class="dim">暂无数据</td></tr>`);
  const mrows = st.per_model.map((r) => `<tr><td class="mono">${esc(r.model)}</td><td>${r.c}</td></tr>`);
  $("#tblModelStats").innerHTML = `<tr><th>模型</th><th>请求</th></tr>` +
    (mrows.join("") || `<tr><td colspan="2" class="dim">暂无数据</td></tr>`);
  drawChart(st.hourly);
}

function drawChart(hourly) {
  const c = $("#chart");
  const ctx = c.getContext("2d");
  const W = (c.width = c.offsetWidth * 2), H = (c.height = 180);
  ctx.clearRect(0, 0, W, H);
  const buckets = new Array(24).fill(0);
  hourly.forEach((h) => { if (h.hour >= 0 && h.hour < 24) buckets[h.hour] = h.count; });
  const max = Math.max(...buckets, 1);
  const bw = W / 24;
  for (let i = 0; i < 24; i++) {
    const bh = (buckets[i] / max) * (H - 30);
    ctx.fillStyle = "#4f8cff";
    ctx.fillRect(i * bw + 4, H - 20 - bh, bw - 8, bh);
    ctx.fillStyle = "#8b95ad";
    ctx.font = "18px sans-serif";
    if (i % 4 === 0) ctx.fillText(`${23 - i}h`, i * bw + 4, H - 2);
  }
}

/* ---------------- providers ---------------- */
const STATUS_BADGE = {
  healthy: '<span class="dot ok"></span>正常',
  unhealthy: '<span class="dot err"></span>异常',
  unknown: '<span class="dot off"></span>未检测',
};

function healthCell(p) {
  const c = p.circuit || {};
  if (c.open) {
    return `<span class="dot err"></span>熔断 ${c.remaining}s` +
      `<button class="small" style="margin-left:6px" onclick="resetCircuit(${p.id})">重置</button>`;
  }
  const base = STATUS_BADGE[p.status] || STATUS_BADGE.unknown;
  return c.fails ? `${base} <span class="dim" style="font-size:12px">(连续失败${c.fails})</span>` : base;
}

async function loadProviders() {
  const list = await api("/api/providers");
  const rows = list.map((p) => `
    <tr>
      <td><b>${esc(p.name)}</b><br><span class="dim" style="font-size:12px">${esc(p.base_url)}</span></td>
      <td class="mono" style="max-width:220px;word-break:break-all">${esc((p.models || []).join(", ") || "-")}</td>
      <td>${p.priority} / ${p.weight || 1}</td>
      <td>${p.latency_ms != null ? Math.round(p.latency_ms) + "ms" : "-"}</td>
      <td>${healthCell(p)}</td>
      <td>${p.enabled ? '<span class="badge ok">已启用</span>' : '<span class="badge off">已停用</span>'}</td>
      <td><div class="btn-group">
        <button class="small" onclick="checkProv(${p.id})">检测</button>
        <button class="small" onclick="toggleProv(${p.id})">${p.enabled ? "停用" : "启用"}</button>
        <button class="small" onclick="editProv(${p.id})">编辑</button>
        <button class="small danger" onclick="delProv(${p.id})">删除</button>
      </div></td>
    </tr>`);
  $("#tblProviders").innerHTML =
    `<tr><th>上游</th><th>支持模型</th><th>优先级/权重</th><th>延迟</th><th>健康</th><th>状态</th><th>操作</th></tr>` +
    (rows.join("") || `<tr><td colspan="7" class="dim">暂无上游，点击右上角添加</td></tr>`);
}
window.resetCircuit = async (id) => {
  await api(`/api/providers/${id}/circuit/reset`, { method: "POST" });
  toast("熔断已重置");
  loadProviders();
};
window.checkProv = async (id) => {
  toast("正在检测上游...");
  try {
    const r = await api(`/api/providers/${id}/check`, { method: "POST" });
    toast(r.ok ? `上游正常 (${r.latency_ms}ms)` : `上游异常: ${r.error}`);
  } catch (e) { toast(e.message); }
  loadProviders();
};
window.toggleProv = async (id) => { await api(`/api/providers/${id}/toggle`, { method: "POST" }); loadProviders(); };
window.delProv = async (id) => {
  if (!confirm("确认删除该上游？")) return;
  await api(`/api/providers/${id}`, { method: "DELETE" });
  loadProviders();
};
window.editProv = (id) => {
  api("/api/providers").then((list) => {
    const p = list.find((x) => x.id === id);
    if (!p) return;
    $("#provModalTitle").textContent = "编辑上游";
    $("#pId").value = p.id;
    $("#pName").value = p.name;
    $("#pBase").value = p.base_url;
    $("#pKey").value = p.api_key;
    $("#pModels").value = (p.models || []).join(", ");
    $("#pPriority").value = p.priority;
    $("#pWeight").value = p.weight || 1;
    $("#pTimeout").value = p.timeout;
    $("#pRetries").value = p.max_retries;
    $("#pHeaders").value = JSON.stringify(p.headers || {});
    $("#provModal").classList.remove("hidden");
  });
};
$("#btnAddProv").onclick = () => {
  $("#provModalTitle").textContent = "添加上游";
  ["pId", "pName", "pBase", "pKey", "pModels"].forEach((i) => ($("#" + i).value = ""));
  $("#pPriority").value = 0; $("#pWeight").value = 1;
  $("#pTimeout").value = 60; $("#pRetries").value = 1;
  $("#pHeaders").value = "";
  $("#provModal").classList.remove("hidden");
};
$("#btnCancelProv").onclick = () => $("#provModal").classList.add("hidden");
$("#btnSaveProv").onclick = async () => {
  const models = $("#pModels").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  let headers = {};
  const htxt = $("#pHeaders").value.trim();
  if (htxt) { try { headers = JSON.parse(htxt); } catch (e) { toast("请求头 JSON 格式错误"); return; } }
  const data = {
    name: $("#pName").value.trim(), base_url: $("#pBase").value.trim(),
    api_key: $("#pKey").value, models, headers,
    enabled: true, priority: +$("#pPriority").value || 0,
    weight: Math.max(1, Math.min(100, +$("#pWeight").value || 1)),
    timeout: +$("#pTimeout").value || 60, max_retries: +$("#pRetries").value || 0,
  };
  try {
    if ($("#pId").value) await api(`/api/providers/${$("#pId").value}`, { method: "PUT", json: data });
    else await api("/api/providers", { json: data });
    $("#provModal").classList.add("hidden");
    toast("已保存");
    loadProviders();
  } catch (e) { toast(e.message); }
};

/* ---------------- api keys ---------------- */
async function loadKeys() {
  const s = await api("/api/settings");
  $("#gwEndpoint").textContent = `http://${s.listen_host}:${s.listen_port}/v1`;
  const list = await api("/api/keys");
  const rows = list.map((k) => `
    <tr>
      <td>${esc(k.name || "-")}</td>
      <td class="mono">${esc(k.key_masked)}</td>
      <td>${k.rpm ? k.rpm + "/min" : "不限"}</td>
      <td>${k.enabled ? '<span class="badge ok">启用</span>' : '<span class="badge off">停用</span>'}</td>
      <td>${fmtTs(k.last_used_at)}</td>
      <td><div class="btn-group">
        <button class="small" onclick="toggleKey(${k.id})">${k.enabled ? "停用" : "启用"}</button>
        <button class="small danger" onclick="delKey(${k.id})">删除</button>
      </div></td>
    </tr>`);
  $("#tblKeys").innerHTML =
    `<tr><th>备注</th><th>密钥</th><th>限速</th><th>状态</th><th>最近使用</th><th>操作</th></tr>` +
    (rows.join("") || `<tr><td colspan="6" class="dim">暂无密钥</td></tr>`);
}
window.toggleKey = async (id) => { await api(`/api/keys/${id}/toggle`, { method: "POST" }); loadKeys(); };
window.delKey = async (id) => {
  if (!confirm("删除后使用该密钥的客户端将立即失效，确认？")) return;
  await api(`/api/keys/${id}`, { method: "DELETE" });
  loadKeys();
};
$("#btnAddKey").onclick = () => { $("#kName").value = ""; $("#kRpm").value = 0; $("#keyModal").classList.remove("hidden"); };
$("#btnCancelKey").onclick = () => $("#keyModal").classList.add("hidden");
$("#btnSaveKey").onclick = async () => {
  try {
    const r = await api("/api/keys", { json: { name: $("#kName").value.trim(), rpm: +$("#kRpm").value || 0 } });
    $("#keyModal").classList.add("hidden");
    $("#newKeyValue").value = r.key;
    $("#keyResultModal").classList.remove("hidden");
    loadKeys();
  } catch (e) { toast(e.message); }
};
$("#btnCloseKeyResult").onclick = () => $("#keyResultModal").classList.add("hidden");
$("#btnCopyNewKey").onclick = () => { navigator.clipboard.writeText($("#newKeyValue").value); toast("已复制"); };

/* ---------------- logs ---------------- */
async function loadLogs() {
  const q = new URLSearchParams({ limit: logLimit, offset: logPage * logLimit });
  const m = $("#logModel").value.trim();
  if (m) q.set("model", m);
  const s = $("#logStatus").value;
  if (s !== "0") q.set("status", s === "4" ? 400 : s === "5" ? 500 : s);
  const r = await api("/api/logs?" + q);
  logTotal = r.total;
  const codeBadge = (st) => !st ? '<span class="badge err">失败</span>'
    : st < 300 ? '<span class="badge ok">' + st + "</span>"
    : st < 500 ? '<span class="badge warn">' + st + "</span>"
    : '<span class="badge err">' + st + "</span>";
  const rows = r.items.map((l) => `
    <tr>
      <td>${fmtTs(l.ts)}</td>
      <td>${esc(l.provider_name || "-")}</td>
      <td class="mono">${esc(l.model || "-")}</td>
      <td class="mono" style="font-size:12px">${esc(l.endpoint || "")}</td>
      <td>${codeBadge(l.status)}</td>
      <td>${l.latency_ms != null ? Math.round(l.latency_ms) : "-"}</td>
      <td>${l.total_tokens ?? "-"}</td>
      <td>${l.stream ? '<span class="badge warn">流式</span>' : '<span class="badge off">普通</span>'}</td>
      <td style="max-width:180px;word-break:break-all;color:var(--err);font-size:12px">${esc(l.error || "")}</td>
    </tr>`);
  $("#tblLogs").innerHTML =
    `<tr><th>时间</th><th>上游</th><th>模型</th><th>端点</th><th>状态</th><th>延迟ms</th><th>Tokens</th><th>类型</th><th>错误</th></tr>` +
    (rows.join("") || `<tr><td colspan="9" class="dim">暂无日志</td></tr>`);
  const pages = Math.max(1, Math.ceil(logTotal / logLimit));
  $("#logPage").textContent = `第 ${logPage + 1}/${pages} 页 · 共 ${logTotal} 条`;
}
$("#btnSearchLog").onclick = () => { logPage = 0; loadLogs(); };
$("#logPrev").onclick = () => { if (logPage > 0) { logPage--; loadLogs(); } };
$("#logNext").onclick = () => { if ((logPage + 1) * logLimit < logTotal) { logPage++; loadLogs(); } };
$("#btnClearLog").onclick = async () => {
  if (!confirm("确认清空全部请求日志？")) return;
  await api("/api/logs/clear", { method: "POST" });
  logPage = 0; loadLogs();
};

/* ---------------- settings ---------------- */
async function loadSettings() {
  const [s, v] = await Promise.all([api("/api/settings"), api("/api/status")]);
  $("#setHost").value = s.listen_host;
  $("#setPort").value = s.listen_port;
  $("#setRetention").value = s.log_retention_days;
  $("#setCircuitThreshold").value = s.circuit_threshold;
  $("#setCircuitCooldown").value = s.circuit_cooldown;
  $("#setBrowser").checked = s.open_browser_on_start;
  $("#adminToken").value = s.admin_token;
  $("#dataDir").textContent = v.data_dir;
}
$("#btnSaveSettings").onclick = async () => {
  try {
    await api("/api/settings", {
      method: "PUT",
      json: {
        listen_host: $("#setHost").value.trim() || "127.0.0.1",
        listen_port: +$("#setPort").value || 8688,
        log_retention_days: +$("#setRetention").value || 30,
        circuit_threshold: Math.max(1, +$("#setCircuitThreshold").value || 3),
        circuit_cooldown: Math.max(1, +$("#setCircuitCooldown").value || 60),
        open_browser_on_start: $("#setBrowser").checked,
      },
    });
    toast("设置已保存");
  } catch (e) { toast(e.message); }
};
$("#btnCopyToken").onclick = () => { navigator.clipboard.writeText($("#adminToken").value); toast("已复制"); };

$("#btnShutdown").onclick = async () => {
  if (!confirm("确认停止网关服务？停止后需要重新运行 LLMGateway 才能继续使用。")) return;
  try { await api("/api/shutdown", { method: "POST" }); } catch (e) { /* 服务关闭后连接中断属正常 */ }
  setTimeout(() => {
    document.body.innerHTML =
      '<div class="token-gate"><div class="token-box"><h1>网关服务已停止</h1>' +
      '<p>请重新运行 LLMGateway 后刷新本页。</p></div></div>';
  }, 800);
};

/* ---------------- boot ---------------- */
async function boot() {
  $("#tokenGate").classList.add("hidden");
  $("#app").classList.remove("hidden");
  await loadDashboard();
}

(async () => {
  if (!ADMIN_TOKEN) { showTokenGate(); return; }
  try { await boot(); } catch (e) { showTokenGate(); }
})();
