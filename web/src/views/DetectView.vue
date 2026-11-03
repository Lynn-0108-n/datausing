<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS, LEVEL_LABELS } from "../api";
import { maskedDocumentText, renderHighlight } from "../highlight";

const SAMPLE = `客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号。`;
// 自由文本样例：没有字段名/关键词，L1 规则抓不到人名和地名，只有 NER 能补
const NER_SAMPLE = "张三昨天从福州赶过来，和李四在厦门大学碰了面，商量下个季度的采购方案。";

const text = ref(SAMPLE);
// 本次结果对应的原文快照：上传文件检测时没有原文，不能拿输入框内容去套文件的高亮区间
const detectSource = ref(SAMPLE);
const fileLabel = ref("");
const maskLevel = ref("");
const minLevel = ref("SAFE");
const enableL2 = ref(false);
const enableL3 = ref(false);
const enableNer = ref(false);
const nerAvailable = ref(false);
const nerBackend = ref("");
const loading = ref(false);
const result = ref(null);
const file = ref(null);

const nerTitle = computed(() =>
  nerAvailable.value
    ? `NER 后端：${nerBackend.value}（可选识别，仅补类别不抬级别）`
    : "服务端未启用 NER：启动时设置 FLOWMASK_ENABLE_NER=1 并准备 NER 权重",
);

const findings = computed(() => (result.value ? result.value.findings : []));
const byLevel = computed(() => (result.value ? result.value.by_level : {}));
const originalHtml = computed(() => {
  if (!result.value) return "";
  if (!detectSource.value) return "（文件检测不展示原文：服务端不落原文，右侧为脱敏副本）";
  return renderHighlight(detectSource.value, flattenSpans());
});
const maskedText = computed(() => maskedDocumentText(result.value?.masked_document));
const maskedHtml = computed(() => escape(maskedText.value));

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

async function runDetect() {
  if (!text.value.trim()) {
    ElMessage.warning("先输入要检测的文本");
    return;
  }
  loading.value = true;
  try {
    const snapshot = text.value;
    result.value = await api.detectText({
      text: snapshot,
      enable_l2: enableL2.value,
      enable_l3: enableL3.value,
      enable_ner: enableNer.value,
      mask_level: maskLevel.value || null,
      min_level: minLevel.value,
    });
    detectSource.value = snapshot;
    fileLabel.value = "";
  } catch (err) {
    ElMessage.error(`检测失败：${err}`);
  } finally {
    loading.value = false;
  }
}

async function upload(fileEntry) {
  if (!fileEntry) return;
  loading.value = true;
  try {
    result.value = await api.detectFile(fileEntry, {
      enableL2: enableL2.value,
      enableL3: enableL3.value,
      enableNer: enableNer.value,
      maskLevel: maskLevel.value || "",
      minLevel: minLevel.value,
    });
    detectSource.value = "";
    fileLabel.value = fileEntry.name;
    ElMessage.success("文件解析并检测完成");
  } catch (err) {
    ElMessage.error(`上传失败：${err}`);
  } finally {
    loading.value = false;
  }
}

function onFileChange(uploadFile) {
  file.value = uploadFile.raw;
  upload(uploadFile.raw);
}

function levelChip(level) {
  return { background: LEVEL_COLORS[level] || "#64748b" };
}

// ---------------------------------------------------------------------------
// 场景模板：选中即套用推荐策略 + 级别 + 可选识别开关，并展示数据保留提示
// ---------------------------------------------------------------------------
const scenarios = ref([]);
const scenarioKey = ref("");

const activeScenario = computed(
  () => scenarios.value.find((item) => item.key === scenarioKey.value) || null,
);

function applyScenario(key) {
  const scenario = scenarios.value.find((item) => item.key === key);
  if (!scenario) return;
  strategy.value = scenario.strategy || strategy.value;
  maskLevel.value = scenario.mask_level || "";
  minLevel.value = scenario.min_level || "SAFE";
  enableL2.value = Boolean(scenario.enable_l2);
  enableL3.value = Boolean(scenario.enable_l3);
  // NER 只有服务端启用时才真正生效，这里不为不可用的能力伪造开启
  enableNer.value = Boolean(scenario.enable_ner) && nerAvailable.value;
}

function clearScenario() {
  scenarioKey.value = "";
}

// ---------------------------------------------------------------------------
// 脱敏策略：导出与「还原加密内容」共用，服务端不可用时退化为内置常用策略
// ---------------------------------------------------------------------------
const STRATEGY_FALLBACK = [
  { key: "block", label: "整段屏蔽", description: "敏感片段整体替换为占位符，信息全丢，不可逆。" },
  { key: "partial", label: "部分遮蔽", description: "只保留首尾少量字符，中间打码，不可逆。" },
  { key: "hash", label: "哈希", description: "替换为加盐摘要，可关联比对，不可逆。" },
  { key: "generalize", label: "泛化", description: "按类别泛化（如精确值变区间），不可逆。" },
  { key: "pseudonym", label: "假名化", description: "映射为稳定的假名/假值，不可逆。" },
  { key: "encrypt", label: "加密", description: "替换为本机密钥加密的 enc: 密文，可在本机还原。" },
];
const strategies = ref([]);
const strategy = ref("partial");

const strategyOptions = computed(() => (strategies.value.length ? strategies.value : STRATEGY_FALLBACK));
const strategyHint = computed(() => {
  const found = strategyOptions.value.find((item) => item.key === strategy.value);
  if (!found) return "";
  const reversible = found.reversible ? "（可还原）" : "（不可逆）";
  return `${found.description || ""}${reversible}`;
});

const FMT_LABELS = {
  auto: "跟随原格式",
  txt: "纯文本 txt",
  json: "JSON",
  csv: "CSV",
  sql: "SQL",
  docx: "Word docx",
  xlsx: "Excel xlsx",
};
const TEXT_FMTS = ["auto", "txt", "json", "csv", "sql"];
const FILE_FMTS = ["auto", "txt", "docx", "xlsx", "json", "csv", "sql"];
const exportFmt = ref("auto");
const exportLoading = ref(false);
const exportError = ref("");

const fmtOptions = computed(() => (fileLabel.value ? FILE_FMTS : TEXT_FMTS));
const exportFallbackName = computed(() => {
  const fmt = exportFmt.value === "auto" ? (fileLabel.value ? "bin" : "txt") : exportFmt.value;
  return `flowmask-masked.${fmt}`;
});

async function runExport() {
  if (!file && !detectSource.value.trim()) {
    exportError.value = "先检测一段文本或上传一个文件，再导出脱敏结果。";
    return;
  }
  // 文件/文本两种入口的格式列表不同，切入口后残留的格式回退到 auto
  if (!fmtOptions.value.includes(exportFmt.value)) exportFmt.value = "auto";
  exportLoading.value = true;
  exportError.value = "";
  try {
    let name = "";
    if (fileLabel.value && file.value) {
      name = await api.exportDetectFile(
        file.value,
        {
          strategy: strategy.value,
          fmt: exportFmt.value,
          enableL2: enableL2.value,
          enableL3: enableL3.value,
          enableNer: enableNer.value,
        },
        exportFallbackName.value,
      );
    } else {
      name = await api.exportDetectText(
        {
          text: detectSource.value,
          content_type: (result.value && result.value.content_type) || "text",
          strategy: strategy.value,
          fmt: exportFmt.value,
          enable_l2: enableL2.value,
          enable_l3: enableL3.value,
          enable_ner: enableNer.value,
          min_level: minLevel.value,
        },
        exportFallbackName.value,
      );
    }
    ElMessage.success(`已导出：${name}`);
  } catch (err) {
    exportError.value = `导出失败：${err}`;
  } finally {
    exportLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 还原加密内容：把当前掩码副本当 ciphertext 交给本机服务端还原
// ---------------------------------------------------------------------------
const decryptOpen = ref(false);
const decryptInput = ref("");
const decryptLoading = ref(false);
const decryptError = ref("");
const decryptResult = ref(null);

function openDecrypt() {
  decryptInput.value = maskedText.value;
  decryptResult.value = null;
  decryptError.value = "";
  decryptOpen.value = true;
}

async function runDecrypt() {
  if (!decryptInput.value.trim()) {
    decryptError.value = "先粘贴含 enc: 密文的脱敏文本。";
    return;
  }
  decryptLoading.value = true;
  decryptError.value = "";
  try {
    decryptResult.value = await api.decryptText(decryptInput.value);
  } catch (err) {
    decryptResult.value = null;
    decryptError.value = `还原失败：${err}`;
  } finally {
    decryptLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// AI 推荐方案：只发用途描述与统计信息，不发原文
// ---------------------------------------------------------------------------
const SOURCE_LABELS = {
  rules: "本地规则",
  local_llm: "本机模型",
  degraded: "模型不可用已回退",
};

const suggestOpen = ref(false);
const suggestLoading = ref(false);
const suggestError = ref("");
const suggestResult = ref(null);
const suggestForm = ref({ prompt: "", scenario: "" });

const sourceLabel = (key) => SOURCE_LABELS[key] || key || "未知来源";
const strategyLabelFor = (key) => {
  const found = strategyOptions.value.find((item) => item.key === key);
  return found ? `${found.label}（${found.key}）` : key || "—";
};

function openSuggest() {
  suggestForm.value = { prompt: "", scenario: scenarioKey.value };
  suggestResult.value = null;
  suggestError.value = "";
  suggestOpen.value = true;
}

async function runSuggest() {
  const prompt = suggestForm.value.prompt.trim();
  if (prompt.length > 200) {
    suggestError.value = "用途描述最多 200 字。";
    return;
  }
  suggestLoading.value = true;
  suggestError.value = "";
  try {
    // fields 只传类别名，不传真实值；原文一律不上送
    const fields = [...new Set(findings.value.flatMap((item) => item.types || []))].slice(0, 32);
    suggestResult.value = await api.suggestMasking({
      scenario: suggestForm.value.scenario || null,
      content_type: (result.value && result.value.content_type) || "text",
      fields,
      level_counts: byLevel.value,
      text_length: (detectSource.value || maskedText.value || "").length,
      prompt,
    });
  } catch (err) {
    suggestResult.value = null;
    suggestError.value = `推荐失败：${err}`;
  } finally {
    suggestLoading.value = false;
  }
}

function applySuggestion() {
  const plan = suggestResult.value;
  if (!plan) return;
  if (plan.strategy) strategy.value = plan.strategy;
  if (plan.mask_level) maskLevel.value = plan.mask_level;
  if (plan.min_level) minLevel.value = plan.min_level;
  enableL2.value = Boolean(plan.enable_l2);
  enableL3.value = Boolean(plan.enable_l3);
  enableNer.value = Boolean(plan.enable_ner) && nerAvailable.value;
  suggestOpen.value = false;
  ElMessage.success("已应用到当前设置（导出时按这个策略生成脱敏文件）");
}

onMounted(async () => {
  // NER 需要本地模型：服务端没启用时把开关禁用，避免用户开了却没效果
  try {
    const health = await api.health();
    nerAvailable.value = Boolean(health.ner);
    nerBackend.value = health.ner_backend || "";
    // 服务端默认开 NER 时开关跟着打开；用户仍可手动关掉本次请求的可选识别
    enableNer.value = nerAvailable.value;
  } catch {
    nerAvailable.value = false;
  }
  // 策略/场景加载失败不影响检测主流程，只是下拉退化为内置常用选项
  const [strategyBody, scenarioBody] = await Promise.allSettled([
    api.fetchMaskStrategies(),
    api.fetchScenarios(),
  ]);
  if (strategyBody.status === "fulfilled") {
    strategies.value = strategyBody.value.strategies || [];
    if (strategyBody.value.default) strategy.value = strategyBody.value.default;
  }
  if (scenarioBody.status === "fulfilled") {
    scenarios.value = scenarioBody.value.scenarios || [];
  }
  // 打开即跑一遍示例：答辩/演示时页面一落地就有内容
  // ?ner=1：直接演示自由文本模式（NER 补出人名/地名）
  const wantNer = new URLSearchParams(window.location.search).get("ner") === "1";
  if (wantNer && nerAvailable.value) {
    enableNer.value = true;
    text.value = NER_SAMPLE;
  }
  runDetect();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      内容检测（文本 / JSON / CSV / SQL 自动识别）
      <span class="hint">命中处黄色高亮，右侧是可直接写库/导出的脱敏副本</span>
    </div>
    <div class="toolbar" style="margin-top: 0">
      <el-select
        v-model="scenarioKey"
        placeholder="场景模板（可选，选中即套用推荐配置）"
        clearable
        style="width: 260px"
        @change="applyScenario"
        @clear="clearScenario"
      >
        <el-option
          v-for="item in scenarios"
          :key="item.key"
          :label="item.label"
          :value="item.key"
        />
      </el-select>
      <el-tag v-if="activeScenario" size="small" type="info">
        {{ activeScenario.description }}
      </el-tag>
      <span v-if="activeScenario && activeScenario.retention" class="hint">
        数据保留：{{ activeScenario.retention }}
      </span>
      <span v-else-if="!scenarios.length" class="hint">未加载到场景模板时按页面默认配置检测</span>
    </div>
    <el-input v-model="text" type="textarea" :rows="5" placeholder="粘贴文本，或点击下方上传文件" />
    <div class="toolbar">
      <el-select v-model="maskLevel" placeholder="脱敏级别（按实际）" style="width: 170px">
        <el-option label="按实际级别" value="" />
        <el-option label="强制 L3" value="L3" />
        <el-option label="强制 L2" value="L2" />
        <el-option label="强制 L1" value="L1" />
      </el-select>
      <el-select v-model="minLevel" style="width: 150px">
        <el-option label="只显示 ≥ SAFE" value="SAFE" />
        <el-option label="只显示 ≥ L1" value="L1" />
        <el-option label="只显示 ≥ L2" value="L2" />
        <el-option label="只显示 ≥ L3" value="L3" />
      </el-select>
      <el-checkbox v-model="enableL2">L2 语义</el-checkbox>
      <el-checkbox v-model="enableL3">L3 大模型</el-checkbox>
      <el-checkbox v-model="enableNer" :disabled="!nerAvailable" :title="nerTitle">
        自由文本 NER{{ nerAvailable ? "（" + (nerBackend || "已启用") + "）" : "（未启用）" }}
      </el-checkbox>
      <el-tag v-if="nerAvailable" size="small" :type="enableNer ? 'success' : 'info'">
        {{ enableNer ? "NER 已开启" : "NER 已关闭" }}
      </el-tag>
      <el-button type="primary" :loading="loading" @click="runDetect">检测</el-button>
      <el-upload
        :auto-upload="false"
        :show-file-list="false"
        :on-change="onFileChange"
        accept=".txt,.log,.md,.json,.csv,.tsv,.sql,.docx,.pdf,.xlsx"
      >
        <el-button>上传文件</el-button>
      </el-upload>
      <el-button @click="text = SAMPLE">恢复示例</el-button>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">
      左右对照
      <span class="hint" v-if="result">
        {{ result.content_type }} · {{ result.segments }} 个片段 · 耗时 {{ result.duration_ms }}ms
        <template v-if="fileLabel"> · {{ fileLabel }}</template>
        <template v-if="result.degraded"> · 已降级</template>
      </span>
    </div>
    <div class="compare">
      <div class="pane">
        <div class="pane-head">原文（命中处高亮；悬停看类别与规则）</div>
        <div class="pane-body" v-html="originalHtml"></div>
      </div>
      <div class="pane">
        <div class="pane-head">脱敏副本（写库/导出用的就是它）</div>
        <div class="pane-body" v-html="maskedHtml"></div>
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

    <!-- 导出 / 还原 / AI 推荐：都作用于当前这次检测结果 -->
    <div v-if="result" class="toolbar">
      <el-select v-model="strategy" placeholder="脱敏策略" style="width: 170px">
        <el-option
          v-for="item in strategyOptions"
          :key="item.key"
          :label="item.label"
          :value="item.key"
        />
      </el-select>
      <el-select v-model="exportFmt" style="width: 140px">
        <el-option
          v-for="fmt in fmtOptions"
          :key="fmt"
          :label="FMT_LABELS[fmt] || fmt"
          :value="fmt"
        />
      </el-select>
      <el-button type="primary" :loading="exportLoading" @click="runExport">导出脱敏文件</el-button>
      <el-button v-if="strategy === 'encrypt'" @click="openDecrypt">还原加密内容</el-button>
      <el-button @click="openSuggest">AI 推荐方案</el-button>
    </div>
    <div v-if="strategyHint" class="hint-line">当前策略：{{ strategyHint }}</div>
    <div v-if="exportError" class="hint-line" style="color: #dc2626">{{ exportError }}</div>
    <div v-if="fileLabel" class="hint-line">
      当前是文件检测：导出的格式按所选处理，选「跟随原格式」时尽量还原为原文件类型。
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

  <!-- AI 推荐方案弹窗 -->
  <el-dialog v-model="suggestOpen" title="AI 推荐脱敏方案" width="620px">
    <el-form label-width="88px">
      <el-form-item label="用途描述">
        <el-input
          v-model="suggestForm.prompt"
          type="textarea"
          :rows="3"
          maxlength="200"
          show-word-limit
          placeholder="例如：给外部审计导出近一个月的工单明细。请勿输入真实数据，这里只用来推断用途。"
        />
      </el-form-item>
      <el-form-item label="场景模板">
        <el-select v-model="suggestForm.scenario" clearable placeholder="可选" style="width: 240px">
          <el-option
            v-for="item in scenarios"
            :key="item.key"
            :label="item.label"
            :value="item.key"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <div v-if="suggestError" class="hint-line" style="color: #dc2626">{{ suggestError }}</div>
    <div v-if="suggestResult" style="margin-top: 12px">
      <dl class="kv">
        <dt>推荐策略</dt>
        <dd>{{ strategyLabelFor(suggestResult.strategy) }}</dd>
        <dt>脱敏级别</dt>
        <dd>{{ suggestResult.mask_level }}（最低 {{ suggestResult.min_level }}）</dd>
        <dt>可选识别</dt>
        <dd>
          L2 {{ suggestResult.enable_l2 ? "开" : "关" }} · L3
          {{ suggestResult.enable_l3 ? "开" : "关" }} · NER
          {{ suggestResult.enable_ner ? "开" : "关" }}
        </dd>
        <dt>数据保留</dt>
        <dd>{{ suggestResult.retention || "—" }}</dd>
        <dt>推荐理由</dt>
        <dd>{{ suggestResult.rationale }}</dd>
        <dt>来源</dt>
        <dd>{{ sourceLabel(suggestResult.source) }}</dd>
      </dl>
    </div>
    <div v-else class="hint-line">
      只上报用途描述、字符数、各级别命中数与类别名，不上报原文和真实值。
    </div>
    <template #footer>
      <el-button @click="suggestOpen = false">关闭</el-button>
      <el-button :loading="suggestLoading" @click="runSuggest">获取推荐</el-button>
      <el-button type="primary" :disabled="!suggestResult" @click="applySuggestion">
        应用到当前设置
      </el-button>
    </template>
  </el-dialog>

  <!-- 还原加密内容弹窗 -->
  <el-dialog v-model="decryptOpen" title="还原加密内容" width="640px">
    <div class="hint-line" style="margin-top: 0">
      把含 <code>enc:</code> 密文的脱敏文本交给本机服务端还原。仅本机还原，请勿外发。
    </div>
    <el-input
      v-model="decryptInput"
      type="textarea"
      :rows="5"
      placeholder="粘贴含 enc: 密文的脱敏文本"
      style="margin-top: 10px"
    />
    <div v-if="decryptError" class="hint-line" style="color: #dc2626">{{ decryptError }}</div>
    <div v-if="decryptResult" style="margin-top: 12px">
      <div class="pane">
        <div class="pane-head">
          还原结果（{{ decryptResult.replaced }} 个密文片段 · 策略 {{ decryptResult.strategy }}）
        </div>
        <div class="pane-body">{{ decryptResult.text }}</div>
      </div>
      <el-alert type="warning" :closable="false" show-icon style="margin-top: 10px">
        还原结果含明文敏感信息，只在本机查看，请勿复制外发或写入工单。
      </el-alert>
    </div>
    <template #footer>
      <el-button @click="decryptOpen = false">关闭</el-button>
      <el-button type="primary" :loading="decryptLoading" @click="runDecrypt">还原</el-button>
    </template>
  </el-dialog>
</template>
