<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS, LEVEL_LABELS } from "../api";
import { maskedDocumentText, renderHighlight } from "../highlight";
import DocumentCompareDialog from "../components/DocumentCompareDialog.vue";

const MAX_FILES = 20;
const SAMPLE = `客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号。`;
// 自由文本样例：没有字段名/关键词，L1 规则抓不到人名和地名，只有 NER 能补
const NER_SAMPLE = "张三昨天从福州赶过来，和李四在厦门大学碰了面，商量下个季度的采购方案。";
// Day 3.5：支持上传图片（OCR 解析）
const ACCEPT = ".txt,.log,.md,.json,.csv,.tsv,.sql,.docx,.xlsx,.pdf,.png,.jpg,.jpeg,.bmp,.webp";

// ---------- Day 3 场景模式 ----------
// 内置场景从 /scenarios 拉，自定义场景从 localStorage 读
const scenarioList = ref([]); // 内置 6 个
const customScenarios = ref([]); // 用户自定义
const activeScenario = ref("");
const CUSTOM_KEY = "flowmask-custom-scenarios";

function loadCustomScenarios() {
  try {
    const raw = window.localStorage.getItem(CUSTOM_KEY);
    if (raw) customScenarios.value = JSON.parse(raw);
  } catch { /* ignore */ }
}

async function loadScenarios() {
  try {
    const body = await api.scenarios();
    scenarioList.value = body.items || [];
  } catch (err) {
    ElMessage.error(`加载场景列表失败：${err}`);
  }
}

// 内置 + 自定义 + 「自定义模式」（不套预设）
const allScenarios = computed(() => [
  ...scenarioList.value.map((s) => ({
    id: s.id,
    name: s.name,
    description: s.description,
    sample: s.sample,
    strategy: s.recommended_strategy,
    minLevel: s.recommended_min_level,
    enableL2: s.recommended_enable_l2,
    enableL3: s.recommended_enable_l3,
    enableNer: s.recommended_enable_ner,
    audit: s.audit_recommended,
    custom: false,
  })),
  ...customScenarios.value.map((s) => ({ ...s, custom: true })),
]);

function applyScenario(s) {
  activeScenario.value = s.id;
  if (s.sample) text.value = s.sample;
  if (s.minLevel) options.minLevel = s.minLevel;
  options.enableL2 = !!s.enableL2;
  options.enableL3 = !!s.enableL3;
  options.enableNer = !!s.enableNer && nerAvailable.value;
  if (s.strategy) exportStrategy.value = s.strategy;
  ElMessage.success(`已套用场景：${s.name}`);
}

function clearScenario() {
  activeScenario.value = "";
}

// ---------- Day 3 多策略导出 + AI 推荐 + 授权开关 ----------
const exportStrategy = ref("block"); // 默认全屏蔽：隐私优先，partial 需用户显式选择
const STRATEGIES = [
  { label: "partial 部分遮掩", value: "partial" },
  { label: "block 全屏蔽", value: "block" },
  { label: "hash 哈希替换", value: "hash" },
  { label: "generalize 数值泛化", value: "generalize" },
  { label: "pseudonym 假名替换", value: "pseudonym" },
  { label: "encrypt AES-GCM", value: "encrypt" },
];

// 检测时的快照：导出以检测时的原文与选项为准，用户检测后继续编辑不影响
const detectedText = ref("");
const detectedOptions = reactive({
  enableL2: true,
  enableL3: true,
  enableNer: false,
  maskLevel: "auto",
  minLevel: "SAFE",
});

// 授权查看原文：true 显示原文（原行为），false 左栏也显示脱敏副本（动态脱敏核心演示）
const authorizedView = ref(true);

// OCR 图片识别开关：服务端启用 OCR 且用户勾选时，上传图片走 OCR 解析
const ocrAvailable = ref(false);
const ocrEnabled = ref(false);

// 策略预览：选中导出策略后，左栏脱敏副本实时按策略重新生成（不下载文件）
const previewByStrategy = ref("");
const previewLoading = ref(false);

// AI 推荐弹窗
const showRecommend = ref(false);
const recommendForm = reactive({
  purpose: "dev-test",
  requirements: [],
  note: "",
});
const recommendResult = ref(null);
const recommendLoading = ref(false);

const PURPOSE_OPTIONS = [
  { label: "开发测试", value: "dev-test" },
  { label: "教学演示", value: "teaching" },
  { label: "外包交付", value: "outsourcing" },
  { label: "数据分析", value: "analysis" },
  { label: "公开共享", value: "public-sharing" },
  { label: "其他", value: "other" },
];
const REQUIREMENT_OPTIONS = [
  { label: "保留统计特征", value: "keep-statistics" },
  { label: "可逆解密", value: "reversible" },
  { label: "格式保持", value: "format-preserving" },
  { label: "彻底不可逆", value: "irreversible" },
  { label: "需要审计", value: "audit" },
];

async function callRecommend() {
  recommendLoading.value = true;
  recommendResult.value = null;
  try {
    recommendResult.value = await api.assistantRecommend({
      purpose: recommendForm.purpose,
      requirements: recommendForm.requirements,
      note: recommendForm.note,
    });
  } catch (err) {
    ElMessage.error(`推荐失败：${err}`);
  } finally {
    recommendLoading.value = false;
  }
}

function applyRecommend() {
  if (!recommendResult.value) return;
  const r = recommendResult.value;
  exportStrategy.value = r.strategy;
  options.minLevel = r.min_level;
  options.enableL2 = r.enable_l2;
  options.enableL3 = r.enable_l3;
  if (r.enable_ner && nerAvailable.value) options.enableNer = true;
  ElMessage.success(`已应用推荐方案：${r.strategy}`);
  showRecommend.value = false;
}

// 策略预览：选导出策略后，左栏脱敏副本实时按策略重新生成（不下载文件）
async function refreshPreview() {
  if (!detectedText.value) {
    previewByStrategy.value = "";
    return;
  }
  previewLoading.value = true;
  try {
    const body = await api.detectPreview({
      text: detectedText.value,
      strategy: exportStrategy.value,
      enable_l2: detectedOptions.enableL2,
      enable_l3: detectedOptions.enableL3,
      enable_ner: detectedOptions.enableNer,
      mask_level: detectedOptions.maskLevel === "auto" ? null : detectedOptions.maskLevel,
    });
    previewByStrategy.value = body.masked || "";
  } catch (err) {
    // 预览失败不影响主流程，静默清空（错误已由 request 弹窗提示）
    previewByStrategy.value = "";
  } finally {
    previewLoading.value = false;
  }
}

// 选导出策略或授权开关变化时，自动刷新预览
watch([exportStrategy, authorizedView], refreshPreview);

// 导出脱敏文件（文本入口）
async function exportText() {
  if (!detectedText.value) {
    ElMessage.warning("请先执行检测");
    return;
  }
  try {
    await api.detectExportText({
      text: detectedText.value,
      strategy: exportStrategy.value,
      enable_l2: detectedOptions.enableL2,
      enable_l3: detectedOptions.enableL3,
      enable_ner: detectedOptions.enableNer,
      mask_level: detectedOptions.maskLevel === "auto" ? null : detectedOptions.maskLevel,
    });
    ElMessage.success("脱敏文件已开始下载");
  } catch (err) {
    ElMessage.error(`导出失败：${err}`);
  }
}

// 导出脱敏文件（文件入口，批量里逐个导出）
async function exportFile(row) {
  if (!row.fileObj) {
    ElMessage.warning("该任务无原文件对象，无法重导出");
    return;
  }
  try {
    await api.detectExportFile(row.fileObj, {
      strategy: exportStrategy.value,
      enable_l2: detectedOptions.enableL2,
      enable_l3: detectedOptions.enableL3,
      enable_ner: detectedOptions.enableNer,
      mask_level: detectedOptions.maskLevel === "auto" ? null : detectedOptions.maskLevel,
    });
    ElMessage.success("脱敏文件已开始下载");
  } catch (err) {
    ElMessage.error(`导出失败：${err}`);
  }
}

// 输入区：文本框 + 拖拽文件
const text = ref(SAMPLE);
const files = ref([]);
const dragging = ref(false);
const fileInput = ref(null);
let dragDepth = 0;

const options = reactive({
  maskLevel: "auto",
  minLevel: "SAFE",
  enableL2: true,
  enableL3: true,
  enableNer: false,
});

// 文本检测状态
const loading = ref(false);
const result = ref(null);

// 批量检测状态
const running = ref(false);
const rows = ref([]);
const activeTask = ref("");
const detailFindings = ref([]);
const findingsTotal = ref(0);
let pollTimer = null;

// 文档对比工作台：原文与脱敏结果来自同一检测任务的服务端临时快照
const compareOpen = ref(false);
const compareTaskId = ref("");
const compareFilename = ref("");

const nerAvailable = ref(false);
const nerBackend = ref("");
const busy = computed(() => loading.value || running.value);
const doneCount = computed(
  () => rows.value.filter((row) => ["done", "failed", "解析失败"].includes(row.status)).length
);
// 检测结果明细：合并所有已完成任务的 findings，按文件分组顺序排列
const mergedFindings = computed(() => {
  const out = [];
  for (const row of rows.value) {
    if (row.status !== "done" || !row.findingsList) continue;
    for (const item of row.findingsList) {
      out.push({ ...item, _filename: row.filename });
    }
  }
  return out;
});
const nerTitle = computed(() =>
  nerAvailable.value
    ? `NER 后端：${nerBackend.value}（可选识别，仅补类别不抬级别）`
    : "服务端未启用 NER：启动时设置 FLOWMASK_ENABLE_NER=1 并准备 NER 权重"
);

// 文本检测结果
const findings = computed(() => (result.value ? result.value.findings : []));
const byLevel = computed(() => (result.value ? result.value.by_level : {}));
const originalHtml = computed(() => (result.value ? renderHighlight(text.value, flattenSpans()) : ""));
const maskedHtml = computed(() => escape(maskedDocumentText(result.value?.masked_document)));

function escape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function flattenSpans() {
  const spans = [];
  for (const item of findings.value) {
    for (const span of item.spans || []) spans.push(span);
  }
  return spans;
}

function levelChip(level) {
  return { background: LEVEL_COLORS[level] || "#71839d" };
}

/* ---------- 输入框聚焦 / 失焦 ---------- */
function onFocus() {
  // 内容恰好是示例文字：聚焦即清空，方便直接输入
  if (text.value === SAMPLE) text.value = "";
}

function onBlur() {
  // 什么都没填：恢复示例文字
  if (!text.value.trim()) text.value = SAMPLE;
}

/* ---------- 文件选择 / 拖拽 ---------- */
function pickFiles() {
  fileInput.value?.click();
}

function onPick(event) {
  addFiles(event.target.files);
  // 允许再次选择同一个文件
  event.target.value = "";
}

// 拖拽不走 accept 过滤，这里按扩展名再校验一次
const SUPPORTED_EXT = new Set(ACCEPT.split(","));

function isSupportedFile(file) {
  const name = String(file.name || "").toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 && SUPPORTED_EXT.has(name.slice(dot));
}

function addFiles(list) {
  const incoming = Array.from(list || []);
  if (!incoming.length) return;
  const seen = new Set(files.value.map((item) => `${item.name}:${item.size}`));
  const merged = [...files.value];
  let rejected = 0;
  for (const item of incoming) {
    if (!isSupportedFile(item)) {
      rejected += 1;
      continue;
    }
    const key = `${item.name}:${item.size}`;
    if (!seen.has(key)) {
      merged.push(item);
      seen.add(key);
    }
  }
  if (rejected) {
    ElMessage.warning("文件仅支持 txt、log、json、csv、docx、pdf 等格式。");
  }
  if (!merged.length) return;
  if (merged.length > MAX_FILES) {
    files.value = merged.slice(0, MAX_FILES);
    ElMessage.warning(`一次最多 ${MAX_FILES} 个文件，已截断`);
  } else {
    files.value = merged;
  }
}

function removeFile(index) {
  files.value.splice(index, 1);
}

function hasFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function onDragEnter(event) {
  if (!hasFiles(event)) return;
  dragDepth += 1;
  dragging.value = true;
}

function onDragOver(event) {
  if (!hasFiles(event)) return;
  dragging.value = true;
}

function onDragLeave() {
  // 子元素会成对触发 enter/leave，用计数避免遮罩闪烁
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dragging.value = false;
}

function onDrop(event) {
  dragDepth = 0;
  dragging.value = false;
  addFiles(event.dataTransfer?.files);
}

/* ---------- 提交：文件优先，其次文本 ---------- */
function submit() {
  if (files.value.length) {
    runBatch();
    return;
  }
  if (!text.value.trim() || text.value === SAMPLE) {
    ElMessage.warning("请输入内容或上传文件");
    return;
  }
  runDetect();
}

async function runDetect() {
  loading.value = true;
  // 文本与批量互斥：清掉上一次的批量结果
  stopPolling();
  running.value = false;
  rows.value = [];
  activeTask.value = "";
  detailFindings.value = [];
  findingsTotal.value = 0;
  // 快照：导出以检测时的原文与选项为准
  detectedText.value = text.value;
  Object.assign(detectedOptions, {
    enableL2: options.enableL2,
    enableL3: options.enableL3,
    enableNer: options.enableNer,
    maskLevel: options.maskLevel,
    minLevel: options.minLevel,
  });
  // 检测后清空策略预览，等 watch exportStrategy 触发或用户切策略时重新生成
  previewByStrategy.value = "";
  try {
    result.value = await api.detectText({
      text: text.value,
      enable_l2: options.enableL2,
      enable_l3: options.enableL3,
      enable_ner: options.enableNer,
      mask_level: options.maskLevel === "auto" ? null : options.maskLevel,
      min_level: options.minLevel,
    });
    // 检测成功后立即按当前导出策略生成预览
    refreshPreview();
  } catch (err) {
    ElMessage.error(`检测失败：${err}`);
  } finally {
    loading.value = false;
  }
}

async function runBatch() {
  running.value = true;
  // 文本与批量互斥：清掉上一次的文本结果
  result.value = null;
  rows.value = [];
  activeTask.value = "";
  detailFindings.value = [];
  findingsTotal.value = 0;
  // 快照：批量导出以检测时的选项为准
  Object.assign(detectedOptions, {
    enableL2: options.enableL2,
    enableL3: options.enableL3,
    enableNer: options.enableNer,
    maskLevel: options.maskLevel,
    minLevel: options.minLevel,
  });
  // 记录文件对象到行，便于 exportFile(row) 重导出
  const fileMap = new Map(files.value.map((f) => [f.name, f]));
  try {
    const body = await api.batchTasks(files.value, options);
    rows.value = body.items.map((item) =>
      rowFor(item.filename, item, fileMap.get(item.filename))
    );
    const accepted = body.items.filter((item) => item.task_id).length;
    ElMessage.success(`已提交 ${accepted}/${body.total} 个文件，正在后台检测…`);
    startPolling();
  } catch (err) {
    ElMessage.error(`批量提交失败：${err}`);
    running.value = false;
  }
}

function rowFor(filename, item, fileObj = null) {
  return {
    filename,
    status: item.error ? "解析失败" : "pending",
    taskId: item.task_id || "",
    segments: item.segments || 0,
    findings: 0,
    l3: 0,
    l2: 0,
    error: item.error || "",
    fileObj, // 原始 File 对象，exportFile 用它走 /detect/file/export
  };
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refresh, 800);
  refresh();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refresh() {
  const pending = rows.value.filter((row) => row.taskId && ["pending", "running"].includes(row.status));
  if (!pending.length) {
    stopPolling();
    running.value = false;
    return;
  }
  await Promise.all(
    pending.map(async (row) => {
      try {
        const task = await api.task(row.taskId);
        row.status = task.status;
        row.segments = task.total_segments ?? row.segments;
        row.findings = task.total_findings ?? 0;
        if (task.status === "failed") row.error = task.error || "检测失败";
        if (task.status === "done") {
          const page = await api.taskFindings(row.taskId, 1, 100);
          row.l3 = page.items.filter((item) => item.level === "L3").length;
          row.l2 = page.items.filter((item) => item.level === "L2").length;
          row.findingsList = page.items;
          row.findingsTotal = page.total;
        }
      } catch (err) {
        row.error = String(err);
      }
    })
  );
}

async function openTask(row) {
  if (!row.taskId) return;
  activeTask.value = row.taskId;
  try {
    const page = await api.taskFindings(row.taskId, 1, 50);
    detailFindings.value = page.items;
    findingsTotal.value = page.total;
  } catch (err) {
    ElMessage.error(`加载结果失败：${err}`);
  }
}

function openCompare(row) {
  if (!row || !row.taskId || row.status !== "done") return;
  compareTaskId.value = row.taskId;
  compareFilename.value = row.filename || "";
  compareOpen.value = true;
}

function statusType(status) {
  if (status === "done") return "success";
  if (status === "failed" || status === "解析失败") return "danger";
  if (status === "running") return "warning";
  return "info";
}

function levelStyle(level) {
  return { backgroundColor: LEVEL_COLORS[level] || "#71839d" };
}

function preview(value, limit = 60) {
  const content = String(value || "").replace(/\s+/g, " ");
  return content.length > limit ? `${content.slice(0, limit)}…` : content;
}

// 转义预览文本，避免 v-html 解析 HTML 标签
function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br/>");
}

/* ---------- 清空 ---------- */
function clearAll() {
  files.value = [];
  text.value = SAMPLE;
  result.value = null;
  rows.value = [];
  activeTask.value = "";
  detailFindings.value = [];
  findingsTotal.value = 0;
  detectedText.value = "";
  previewByStrategy.value = "";
  activeScenario.value = "";
  if (fileInput.value) fileInput.value.value = "";
  stopPolling();
  running.value = false;
}

onMounted(async () => {
  // NER 需要本地模型：服务端没启用时把开关禁用，避免用户开了却没效果
  try {
    const health = await api.health();
    nerAvailable.value = Boolean(health.ner);
    nerBackend.value = health.ner_backend || "";
    options.enableNer = nerAvailable.value;
    // OCR：服务端启用且安装了引擎才允许开
    ocrAvailable.value = Boolean(health.ocr);
    ocrEnabled.value = ocrAvailable.value;
  } catch {
    nerAvailable.value = false;
    ocrAvailable.value = false;
    ocrEnabled.value = false;
  }
  // ?ner=1：直接演示自由文本模式（NER 补出人名/地名）
  const wantNer = new URLSearchParams(window.location.search).get("ner") === "1";
  if (wantNer && nerAvailable.value) {
    options.enableNer = true;
    text.value = NER_SAMPLE;
  }
  // Day 3：加载场景列表（内置）与自定义场景（localStorage）
  loadCustomScenarios();
  loadScenarios();
});

onBeforeUnmount(stopPolling);
</script>

<template>
  <div class="panel">
    <div class="panel-title">内容检测</div>

    <!-- Day 3：场景模式条（内置 + 自定义 + 自定义模式） -->
    <div class="scenario-bar">
      <button
        v-for="s in allScenarios"
        :key="s.id"
        :class="['scenario-card', { active: activeScenario === s.id }]"
        @click="applyScenario(s)"
      >
        {{ s.name }}
      </button>
      <button
        :class="['scenario-card', { active: !activeScenario }]"
        @click="clearScenario"
      >
        自定义模式
      </button>
    </div>

    <!-- 输入 + 拖拽复合容器；默认无虚线，拖拽悬浮时才出现遮罩 -->
    <div
      class="detect-input"
      @dragenter.prevent="onDragEnter"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <textarea
        v-model="text"
        class="detect-textarea"
        placeholder="粘贴或输入待检测文本…"
        @focus="onFocus"
        @blur="onBlur"
      ></textarea>
      <div v-if="dragging" class="drop-overlay">
        <span>松手即可上传</span>
      </div>
    </div>

    <div v-if="files.length" class="file-list">
      <el-tag
        v-for="(item, index) in files"
        :key="`${item.name}-${item.size}-${index}`"
        type="info"
        closable
        @close="removeFile(index)"
      >
        {{ item.name }}
      </el-tag>
    </div>

    <div class="toolbar">
      <!-- 识别模式：单独成组，供新手引导整块高亮 -->
      <div class="detect-options">
        <el-select v-model="options.maskLevel" style="width: 170px">
          <el-option label="按实际级别" value="auto" />
          <el-option label="强制 L3" value="L3" />
          <el-option label="强制 L2" value="L2" />
          <el-option label="强制 L1" value="L1" />
        </el-select>
        <el-select v-model="options.minLevel" style="width: 150px">
          <el-option label="只显示 ≥ SAFE" value="SAFE" />
          <el-option label="只显示 ≥ L1" value="L1" />
          <el-option label="只显示 ≥ L2" value="L2" />
          <el-option label="只显示 ≥ L3" value="L3" />
        </el-select>
        <el-checkbox v-model="options.enableL2">L2 语义</el-checkbox>
        <el-checkbox v-model="options.enableL3">L3 大模型</el-checkbox>
        <el-checkbox v-model="options.enableNer" :disabled="!nerAvailable" :title="nerTitle">
          自由文本 NER{{ nerAvailable ? "（" + (nerBackend || "已启用") + "）" : "（未启用）" }}
        </el-checkbox>
        <el-checkbox v-model="ocrEnabled" :disabled="!ocrAvailable" :title="ocrAvailable ? '上传图片时走 OCR 解析' : '服务端未启用 OCR（需配置 FLOWMASK_OCR_ENABLED=1 并安装 rapidocr-onnxruntime）'">
          图片识别{{ ocrAvailable ? "（已启用）" : "（未启用）" }}
        </el-checkbox>
      </div>
      <el-button class="detect-submit" type="primary" :loading="busy" @click="submit">检测</el-button>
      <el-button :disabled="busy" @click="pickFiles">上传文件</el-button>
      <el-button class="detect-clear" :disabled="busy" @click="clearAll">清空</el-button>
      <input
        ref="fileInput"
        class="file-input"
        type="file"
        multiple
        :accept="ACCEPT"
        @change="onPick"
      />
    </div>
  </div>

  <!-- 文本检测结果 -->
  <template v-if="result">
    <div class="panel">
      <div class="panel-title">
        左右对照
        <!-- Day 3：导出栏 + AI 推荐 + 授权查看原文 -->
        <div class="export-bar">
          <el-select v-model="exportStrategy" style="width: 200px" size="small">
            <el-option v-for="s in STRATEGIES" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
          <el-button size="small" type="primary" @click="exportText">下载脱敏文件</el-button>
          <el-button size="small" @click="showRecommend = true">AI 帮我选方案</el-button>
          <el-switch v-model="authorizedView" active-text="授权查看原文" inline-prompt size="small" />
        </div>
      </div>
      <div class="compare">
        <div class="pane">
          <div class="pane-head">{{ authorizedView ? "原文" : "脱敏副本（未授权）" }}</div>
          <div class="pane-body" v-html="authorizedView ? originalHtml : (previewByStrategy ? escapeHtml(previewByStrategy) : maskedHtml)"></div>
        </div>
        <div class="pane">
          <div class="pane-head">
            脱敏副本（按导出策略）
            <span v-if="previewLoading" class="hint">生成中…</span>
          </div>
          <div class="pane-body" v-html="previewByStrategy ? escapeHtml(previewByStrategy) : maskedHtml"></div>
        </div>
      </div>
      <div class="legend">
        <span
          v-for="level in ['L3', 'L2', 'L1', 'SAFE']"
          :key="level"
          class="chip"
          :style="levelChip(level)"
        >
          {{ level }} {{ LEVEL_LABELS[level] }} {{ byLevel[level] || 0 }}
        </span>
      </div>
      <div v-if="result.task_id" class="audit-hint">
        检测记录已自动创建：<span class="mono">{{ result.task_id.slice(0, 12) }}</span>（可在「检测记录」查看）
      </div>
    </div>

    <div class="panel">
      <div class="panel-title">检测结果明细</div>
      <el-table v-if="findings.length" :data="findings" stripe>
        <el-table-column prop="path" label="位置" width="160" />
        <el-table-column label="级别" width="90">
          <template #default="scope">
            <span class="chip" :style="levelChip(scope.row.level)">{{ scope.row.level }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="score" label="分数" width="80" />
        <el-table-column label="类别" width="220">
          <template #default="scope">{{ (scope.row.types || []).join(", ") }}</template>
        </el-table-column>
        <el-table-column prop="hits" label="命中" width="70" />
        <el-table-column prop="model_chain" label="模型链" width="110" />
        <el-table-column prop="masked" label="脱敏副本" show-overflow-tooltip />
      </el-table>
      <div v-else class="empty">还没有检测结果</div>
    </div>
  </template>

  <!-- 批量检测结果 -->
  <template v-if="rows.length">
    <div class="panel">
      <div class="panel-title">
        批量结果
        <span class="hint">已处理 {{ doneCount }}/{{ rows.length }} 个文件</span>
        <!-- 文件上传也提供脱敏策略选择 + AI推荐 -->
        <div class="export-bar">
          <el-select v-model="exportStrategy" style="width: 200px" size="small">
            <el-option v-for="s in STRATEGIES" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
          <el-button size="small" @click="showRecommend = true">AI 帮我选方案</el-button>
        </div>
      </div>
      <el-table :data="rows" size="small" style="width: 100%">
        <el-table-column prop="filename" label="文件" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="segments" label="片段" width="70" />
        <el-table-column prop="findings" label="命中" width="70" />
        <el-table-column label="高危/中危" width="100">
          <template #default="{ row }">{{ row.l3 }} / {{ row.l2 }}</template>
        </el-table-column>
        <el-table-column label="任务" min-width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.taskId ? row.taskId.slice(0, 8) : "-" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="!row.taskId" @click="openTask(row)">
              查看
            </el-button>
            <el-button
              link
              type="primary"
              size="small"
              class="row-action"
              :disabled="!row.taskId || row.status !== 'done'"
              :title="row.taskId && row.status === 'done' ? '查看原文与脱敏结果的逐字符对比' : '任务完成检测后可对比'"
              @click="openCompare(row)"
            >对比</el-button>
            <a
              v-if="row.taskId"
              class="export-link"
              :href="api.taskExportUrl(row.taskId, 'jsonl')"
              target="_blank"
              rel="noreferrer"
            >明细</a>
            <el-button
              v-if="row.fileObj"
              link
              type="success"
              size="small"
              class="row-action"
              @click="exportFile(row)"
            >脱敏文件</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
      </el-table>
    </div>

    <!-- 检测结果明细：一个卡片内并列呈现所有已完成任务的所有命中 -->
    <div v-if="mergedFindings.length" class="panel">
      <div class="panel-title">
        检测结果明细
        <span class="hint">共 {{ mergedFindings.length }} 条命中 · {{ doneCount }} 个文件</span>
      </div>
      <el-table :data="mergedFindings" size="small" style="width: 100%">
        <el-table-column prop="_filename" label="文件" width="160" show-overflow-tooltip />
        <el-table-column prop="path" label="位置" width="150" show-overflow-tooltip />
        <el-table-column label="级别" width="76">
          <template #default="{ row }">
            <span class="chip" :style="levelStyle(row.level)">{{ row.level }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="score" label="分数" width="80" />
        <el-table-column label="类别" width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.types || []).join(", ") }}</template>
        </el-table-column>
        <el-table-column prop="hits" label="命中" width="60" />
        <el-table-column label="脱敏副本" min-width="220">
          <template #default="{ row }">
            <span class="mono">{{ preview(row.masked, 80) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </template>

  <!-- AI 帮我选方案弹窗 -->
  <el-dialog v-model="showRecommend" title="AI 帮我选方案" width="540px">
    <el-form :model="recommendForm" label-width="100px">
      <el-form-item label="数据用途">
        <el-radio-group v-model="recommendForm.purpose">
          <el-radio v-for="p in PURPOSE_OPTIONS" :key="p.value" :value="p.value">{{ p.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="输出要求">
        <el-checkbox-group v-model="recommendForm.requirements">
          <el-checkbox v-for="r in REQUIREMENT_OPTIONS" :key="r.value" :value="r.value">
            {{ r.label }}
          </el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-form-item label="补充说明">
        <el-input
          v-model="recommendForm.note"
          type="textarea"
          :rows="3"
          placeholder="可选：补充说明你的具体场景或顾虑"
        />
      </el-form-item>
    </el-form>
    <div class="toolbar" style="justify-content: flex-end">
      <el-button @click="showRecommend = false">取消</el-button>
      <el-button type="primary" :loading="recommendLoading" @click="callRecommend">推荐方案</el-button>
    </div>
    <div v-if="recommendResult" class="recommend-card">
      <div class="recommend-row">
        <span class="recommend-label">推荐策略</span>
        <el-tag type="success">{{ recommendResult.strategy }}</el-tag>
        <span class="recommend-label">建议级别</span>
        <el-tag>{{ recommendResult.min_level }}</el-tag>
      </div>
      <div class="recommend-row">
        <span class="recommend-label">识别开关</span>
        <span>L2: {{ recommendResult.enable_l2 ? "开" : "关" }} / L3: {{ recommendResult.enable_l3 ? "开" : "关" }} / NER: {{ recommendResult.enable_ner ? "开" : "关" }}</span>
        <span class="recommend-label">审计</span>
        <el-tag :type="recommendResult.audit_recommended ? 'warning' : 'info'" size="small">
          {{ recommendResult.audit_recommended ? "建议启用" : "非必要" }}
        </el-tag>
      </div>
      <div class="recommend-reason">{{ recommendResult.reason }}</div>
      <div class="recommend-source">
        来源：{{ recommendResult.source === "rules" ? "规则引擎" : recommendResult.source }}
      </div>
      <div class="toolbar" style="justify-content: flex-end; margin-top: 8px">
        <el-button type="primary" @click="applyRecommend">一键应用</el-button>
      </div>
    </div>
  </el-dialog>

  <DocumentCompareDialog
    v-model="compareOpen"
    :task-id="compareTaskId"
    :filename="compareFilename"
  />
</template>

<style scoped>
/* 输入 + 拖拽复合容器：保持玻璃态，默认无虚线 */
.detect-input {
  position: relative;
  min-height: 190px;
  padding: 14px 18px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.7);
  overflow: hidden;
  transition: background 0.18s ease, border-color 0.18s ease;
}
.detect-textarea {
  display: block;
  width: 100%;
  min-height: 160px;
  padding: 0;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: inherit;
  font-size: 16px;
  line-height: 1.8;
  color: var(--ink);
}
.detect-textarea::placeholder {
  color: #a0b1c6;
}
/* 仅拖拽时出现：半透明遮罩 + 虚线高亮 */
.drop-overlay {
  position: absolute;
  inset: 6px;
  display: grid;
  place-items: center;
  border: 2px dashed rgba(74, 135, 228, 0.75);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.55);
  -webkit-backdrop-filter: blur(6px);
  backdrop-filter: blur(6px);
  color: var(--copper);
  font-size: 16px;
  font-weight: 700;
  pointer-events: none;
}
/* 识别模式分组：视觉与 toolbar 一致，只作为一个整体参与换行 */
.detect-options {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.file-input {
  display: none;
}
.mono {
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  color: var(--muted);
}
.export-link {
  margin-left: 10px;
  font-size: 12px;
}
/* 操作列：明细与脱敏文件间距与"查看-明细"间距一致 */
.row-action {
  margin-left: 10px;
}
/* Day 3：场景模式条 */
.scenario-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.scenario-card {
  padding: 6px 14px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.7);
  background: rgba(255, 255, 255, 0.45);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.18s ease;
}
.scenario-card:hover {
  background: rgba(255, 255, 255, 0.7);
  border-color: var(--copper, #4a87e4);
}
.scenario-card.active {
  background: var(--copper, #4a87e4);
  color: #fff;
  border-color: var(--copper, #4a87e4);
}
/* Day 3：导出栏 + 授权开关 */
.export-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex-wrap: wrap;
}
.audit-hint {
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.4);
  font-size: 12px;
  color: var(--muted, #888);
}
.hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--muted, #888);
}
/* AI 推荐弹窗里的结果卡片 */
.recommend-card {
  margin-top: 14px;
  padding: 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.7);
}
.recommend-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 6px 0;
  flex-wrap: wrap;
}
.recommend-label {
  font-weight: 600;
  font-size: 13px;
}
.recommend-reason {
  margin: 10px 0;
  padding: 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.5);
  font-size: 13px;
  color: var(--ink);
}
.recommend-source {
  font-size: 12px;
  color: var(--muted, #888);
}
</style>
