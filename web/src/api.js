import { ElMessage } from "element-plus";

const BASE = "/api/v1";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, options);
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }
  if (!res.ok) {
    const detail = body && (body.detail || body.raw) ? body.detail || body.raw : res.statusText;
    throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return body;
}

function jsonBody(data) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

function formBody(file) {
  const fd = new FormData();
  fd.append("file", file);
  return { method: "POST", body: fd };
}

// blob 下载辅助：用于多策略脱敏文件导出
// - 解析 Content-Disposition（RFC 5987 双写）拿到文件名
// - 抓 X-FlowMask-Encrypt-Key（encrypt 策略时返回 AES-GCM 密钥 base64）
async function requestBlob(path, options = {}) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* keep raw */ }
    throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = disposition.match(/filename="([^"]+)"/i);
  const filename = decodeURIComponent(star ? star[1] : plain ? plain[1] : "flowmask-masked.txt");
  const encryptKey = res.headers.get("X-FlowMask-Encrypt-Key") || "";
  const blob = await res.blob();
  return { blob, filename, encryptKey };
}

// 触发浏览器下载；加密导出时提示密钥并复制到剪贴板
async function saveBlob(path, options, fallbackName = "flowmask-masked.txt") {
  const { blob, filename, encryptKey } = await requestBlob(path, options);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  if (encryptKey) {
    // 加密导出：密钥就是还原原文的唯一凭证，复制到剪贴板并提示
    try { await navigator.clipboard.writeText(encryptKey); } catch { /* 剪贴板被禁用，忽略 */ }
    ElMessage.success("加密密钥已复制到剪贴板，请妥善保管；丢失将无法解密");
  }
  return { filename, encryptKey };
}

export const api = {
  health: () => request("/health"),
  formats: () => request("/formats"),
  detectText: (payload) => request("/detect", jsonBody(payload)),
  detectFile: (file) => request("/detect/file", formBody(file)),

  createTaskText: (text) => request("/tasks/text", jsonBody({ text })),
  createTaskFile: (file) => request("/tasks", formBody(file)),
  tasks: (page = 1, pageSize = 20) => request(`/tasks?page=${page}&page_size=${pageSize}`),
  task: (id) => request(`/tasks/${id}`),
  taskFindings: (id, page = 1, pageSize = 50) =>
    request(`/tasks/${id}/findings?page=${page}&page_size=${pageSize}`),
  taskExportUrl: (id, fmt) => `${BASE}/tasks/${id}/export?format=${fmt}`,
  deleteTask: (id) =>
    request(`/tasks/${id}`, { method: "DELETE" }),
  deleteTasks: (taskIds) =>
    request(`/tasks`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_ids: taskIds }),
    }),
  privacyExport: (id, epsilon, l) =>
    request(`/tasks/${id}/export/privacy?format=json&epsilon=${epsilon}&l=${l}`),
  privacyExportUrl: (id, epsilon, l) =>
    `${BASE}/tasks/${id}/export/privacy?format=jsonl&epsilon=${epsilon}&l=${l}`,

  stats: (taskId) => request(`/stats${taskId ? `?task_id=${taskId}` : ""}`),
  audit: (page = 1, pageSize = 20) => request(`/audit?page=${page}&page_size=${pageSize}`),
  auditVerify: () => request("/audit/verify"),

  rules: () => request("/rules"),
  addRule: (rule) => request("/rules", jsonBody(rule)),
  toggleRule: (id, enabled) =>
    request(`/rules/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) }),
  deleteRule: (id) => request(`/rules/${id}`, { method: "DELETE" }),

  dynamicRender: (payload, operator) =>
    request("/dynamic/render", {
      ...jsonBody(payload),
      headers: { "Content-Type": "application/json", "X-Operator-Id": operator || "web-console" },
    }),
  dynamicPolicy: () => request("/dynamic/policy"),

  // 用户偏好：主题按操作人存服务端，换浏览器/换用户都能记住
  preferences: (operatorId) =>
    request("/preferences", { headers: operatorId ? { "X-Operator-Id": operatorId } : {} }),
  savePreference: (theme, operatorId) =>
    request("/preferences", {
      ...jsonBody({ theme }),
      headers: { "Content-Type": "application/json", "X-Operator-Id": operatorId || "local" },
    }),
  resetPreference: (operatorId) =>
    request("/preferences", {
      method: "DELETE",
      headers: { "X-Operator-Id": operatorId || "local" },
    }),
  preferenceUsers: () => request("/preferences/users?limit=20"),

  trafficFormats: () => request("/traffic/formats"),
  trafficSampleUrl: `${BASE}/traffic/sample`,
  importTraffic: (file, opts = {}) => {
    const fd = new FormData();
    fd.append("pcap", file);
    const qs = new URLSearchParams();
    if (opts.enableL2) qs.set("enable_l2", "true");
    if (opts.enableL3) qs.set("enable_l3", "true");
    if (opts.maskLevel) qs.set("mask_level", opts.maskLevel);
    if (opts.minLevel) qs.set("min_level", opts.minLevel);
    return request(`/traffic/import?${qs.toString()}`, { method: "POST", body: fd });
  },

  // 实时旁路：服务端跟随一个持续增长的 pcap 文件（由 dumpcap/Wireshark/自研脚本写）
  liveList: () => request("/traffic/live"),
  liveStart: (payload) => request("/traffic/live/start", jsonBody(payload)),
  liveStop: (liveId) => request(`/traffic/live/${liveId}/stop`, { method: "POST" }),
  liveClear: () => request("/traffic/live", { method: "DELETE" }),

  // 批量检测：一次上传多个文件，每个文件一个任务（坏文件不会拖垮整批）
  batchTasks: (files, opts = {}) => {
    const fd = new FormData();
    for (const file of files) fd.append("files", file);
    const qs = new URLSearchParams();
    if (opts.enableL2) qs.set("enable_l2", "true");
    if (opts.enableL3) qs.set("enable_l3", "true");
    if (opts.enableNer) qs.set("enable_ner", "true");
    if (opts.maskLevel && opts.maskLevel !== "auto") qs.set("mask_level", opts.maskLevel);
    if (opts.minLevel) qs.set("min_level", opts.minLevel);
    return request(`/tasks/batch?${qs.toString()}`, { method: "POST", body: fd });
  },

  // ---------- Day 2 新增：多策略脱敏文件导出 + 场景模式 + AI 助手 ----------

  // 文本入口导出：JSON 体（与 detectText 同款字段 + strategy/format）
  detectExportText: (payload) =>
    saveBlob("/detect/export", jsonBody(payload)),

  // 文本入口预览：返回 JSON {masked}，不下载文件，用于左栏实时预览导出策略效果
  detectPreview: (payload) =>
    request("/detect/export", jsonBody({ ...payload, preview: true })),

  // 文件入口导出：multipart form（strategy/format/l2/l3/ner 等都是 form 字段）
  detectExportFile: (file, opts = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("strategy", opts.strategy || "partial");
    if (opts.format) fd.append("format", opts.format);
    if (opts.enableL2) fd.append("enable_l2", "true");
    if (opts.enableL3) fd.append("enable_l3", "true");
    if (opts.enableNer !== undefined && opts.enableNer !== null)
      fd.append("enable_ner", opts.enableNer ? "true" : "false");
    if (opts.enableKeywords !== undefined && opts.enableKeywords !== null)
      fd.append("enable_keywords", opts.enableKeywords ? "true" : "false");
    if (opts.maskLevel) fd.append("mask_level", opts.maskLevel);
    return saveBlob("/detect/file/export", { method: "POST", body: fd }, file.name);
  },

  // 场景模式：列表与详情
  scenarios: () => request("/scenarios"),
  scenario: (id) => request(`/scenarios/${id}`),

  // AI 助手：统计解读 + 方案推荐
  assistantAdvise: (payload) => request("/assistant/advise", jsonBody(payload)),
  assistantRecommend: (payload) => request("/assistant/recommend", jsonBody(payload)),
};

export const LEVEL_COLORS = { L3: "#b5442c", L2: "#c87b2c", L1: "#c9a227", SAFE: "#5c7a4a" };
export const LEVEL_LABELS = { L3: "高危", L2: "中危", L1: "低危", SAFE: "安全" };
