<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";

// ---------- 1. 场景与默认值 ----------
// 默认场景/策略/识别模式/级别，存 localStorage，内容检测页与隐私导出读这里作为初始值
const DEFAULTS_KEY = "flowmask-defaults";
const defaults = ref({
  strategy: "partial",
  minLevel: "SAFE",
  enableL2: false,
  enableL3: false,
  enableNer: false,
});

const STRATEGIES = [
  { label: "partial 部分遮掩", value: "partial" },
  { label: "block 全屏蔽", value: "block" },
  { label: "hash 哈希替换", value: "hash" },
  { label: "generalize 数值泛化", value: "generalize" },
  { label: "pseudonym 假名替换", value: "pseudonym" },
  { label: "encrypt AES-GCM", value: "encrypt" },
];
const LEVELS = [
  { label: "SAFE 全部", value: "SAFE" },
  { label: "L1 低危及以上", value: "L1" },
  { label: "L2 中危及以上", value: "L2" },
  { label: "L3 高危", value: "L3" },
];

function loadDefaults() {
  try {
    const raw = window.localStorage.getItem(DEFAULTS_KEY);
    if (raw) defaults.value = { ...defaults.value, ...JSON.parse(raw) };
  } catch { /* ignore */ }
}
function saveDefaults() {
  try {
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify(defaults.value));
    ElMessage.success("默认值已保存");
  } catch {
    ElMessage.error("写入本地存储失败（可能处于隐私模式）");
  }
}

// ---------- 2. 自定义场景（localStorage） ----------
const CUSTOM_KEY = "flowmask-custom-scenarios";
const customScenarios = ref([]);
const scenarioDialog = ref(false);
const scenarioForm = ref({
  name: "",
  strategy: "partial",
  minLevel: "L2",
  enableL2: true,
  enableL3: false,
  enableNer: false,
  audit: true,
});

function loadCustomScenarios() {
  try {
    const raw = window.localStorage.getItem(CUSTOM_KEY);
    if (raw) customScenarios.value = JSON.parse(raw);
  } catch { /* ignore */ }
}
function persistCustomScenarios() {
  try {
    window.localStorage.setItem(CUSTOM_KEY, JSON.stringify(customScenarios.value));
  } catch {
    ElMessage.error("写入本地存储失败");
  }
}
function addCustomScenario() {
  const name = (scenarioForm.value.name || "").trim();
  if (!name) {
    ElMessage.warning("请填写场景名称");
    return;
  }
  const id = `custom-${Date.now()}`;
  customScenarios.value.push({
    id,
    name,
    strategy: scenarioForm.value.strategy,
    minLevel: scenarioForm.value.minLevel,
    enableL2: scenarioForm.value.enableL2,
    enableL3: scenarioForm.value.enableL3,
    enableNer: scenarioForm.value.enableNer,
    audit: scenarioForm.value.audit,
  });
  persistCustomScenarios();
  scenarioDialog.value = false;
  scenarioForm.value = {
    name: "",
    strategy: "partial",
    minLevel: "L2",
    enableL2: true,
    enableL3: false,
    enableNer: false,
    audit: true,
  };
  ElMessage.success("自定义场景已保存");
}
function removeCustomScenario(id) {
  ElMessageBox.confirm("删除该自定义场景？", "确认", { type: "warning" })
    .then(() => {
      customScenarios.value = customScenarios.value.filter((s) => s.id !== id);
      persistCustomScenarios();
      ElMessage.success("已删除");
    })
    .catch(() => {});
}

// ---------- 3. 隐私增强（保留） ----------
// ε（差分隐私噪声强度）/ l（l-多样性）滑块，TasksView 隐私导出读这里作为初始值
const PRIVACY_KEY = "flowmask-privacy";
const privacy = ref({ epsilon: 1.0, l: 2 });

function loadPrivacy() {
  try {
    const raw = window.localStorage.getItem(PRIVACY_KEY);
    if (raw) privacy.value = { ...privacy.value, ...JSON.parse(raw) };
  } catch { /* ignore */ }
}
function savePrivacy() {
  try {
    window.localStorage.setItem(PRIVACY_KEY, JSON.stringify(privacy.value));
    ElMessage.success("隐私参数已保存");
  } catch {
    ElMessage.error("写入本地存储失败");
  }
}

// ---------- 4. 模型与降级 ----------
// 读 api.health() 展示 L2/L3/NER/OCR/助手状态
const health = ref(null);
const healthLoading = ref(false);
const ollamaDialog = ref(false);
const ollamaForm = ref({
  mode: "off",
  endpoint: "http://127.0.0.1:11434/v1/chat/completions",
  model: "qwen2.5:1.5b",
});
const OLLAMA_KEY = "flowmask-assistant-config";

function loadOllamaConfig() {
  try {
    const raw = window.localStorage.getItem(OLLAMA_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    ollamaForm.value = {
      mode: saved.mode === "ollama" ? "ollama" : "off",
      endpoint: saved.endpoint || ollamaForm.value.endpoint,
      model: saved.model || ollamaForm.value.model,
    };
  } catch { /* ignore */ }
}
function saveOllamaConfig() {
  try {
    window.localStorage.setItem(OLLAMA_KEY, JSON.stringify(ollamaForm.value));
    ElMessage.warning("前端配置已保存。修改需在服务端 .env 配置 FLOWMASK_ASSISTANT_* 后重启生效");
    ollamaDialog.value = false;
  } catch {
    ElMessage.error("写入本地存储失败");
  }
}

async function loadHealth() {
  healthLoading.value = true;
  try {
    health.value = await api.health();
  } catch (err) {
    ElMessage.error(`加载健康检查失败：${err}`);
  } finally {
    healthLoading.value = false;
  }
}

function healthBadge(value) {
  return value ? { type: "success", text: "已启用" } : { type: "info", text: "未启用" };
}

const assistantModeLabel = computed(() => {
  const m = ollamaForm.value.mode;
  if (m === "ollama") return "本机 Ollama";
  return "规则引擎（不联网）";
});

// ---------- 5. 关于 ----------
const about = {
  version: "FlowMask 服务端 0.1.0 / 客户端 0.14.0",
};

onMounted(() => {
  loadDefaults();
  loadCustomScenarios();
  loadPrivacy();
  loadOllamaConfig();
  loadHealth();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">场景与默认值</div>
    <div class="settings-row">
      <span class="settings-label">默认脱敏策略</span>
      <el-select v-model="defaults.strategy" style="width: 220px">
        <el-option v-for="s in STRATEGIES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
    </div>
    <div class="settings-row">
      <span class="settings-label">默认最小返回级别</span>
      <el-select v-model="defaults.minLevel" style="width: 220px">
        <el-option v-for="l in LEVELS" :key="l.value" :label="l.label" :value="l.value" />
      </el-select>
    </div>
    <div class="settings-row">
      <span class="settings-label">识别模式</span>
      <el-checkbox v-model="defaults.enableL2">L2 语义</el-checkbox>
      <el-checkbox v-model="defaults.enableL3">L3 大模型</el-checkbox>
      <el-checkbox v-model="defaults.enableNer">自由文本 NER</el-checkbox>
    </div>
    <div class="toolbar">
      <el-button type="primary" @click="saveDefaults">保存默认值</el-button>
    </div>

    <div class="settings-subtitle">自定义场景</div>
    <el-table :data="customScenarios" stripe style="margin-top: 8px" empty-text="尚未添加自定义场景">
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="strategy" label="策略" width="110" />
      <el-table-column prop="minLevel" label="级别" width="90" />
      <el-table-column label="L2/L3/NER" width="120">
        <template #default="scope">
          {{ scope.row.enableL2 ? "L2 " : "" }}{{ scope.row.enableL3 ? "L3 " : "" }}{{ scope.row.enableNer ? "NER" : "" }}
        </template>
      </el-table-column>
      <el-table-column label="审计" width="70">
        <template #default="scope">
          <el-tag :type="scope.row.audit ? 'success' : 'info'" size="small">
            {{ scope.row.audit ? "建议" : "不必要" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="scope">
          <el-button size="small" type="danger" @click="removeCustomScenario(scope.row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="toolbar" style="margin-top: 8px">
      <el-button @click="scenarioDialog = true">新增自定义场景</el-button>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">隐私增强</div>
    <div class="settings-row">
      <span class="settings-label">差分隐私 ε（{{ privacy.epsilon.toFixed(2) }}）</span>
      <el-slider v-model="privacy.epsilon" :min="0.1" :max="5" :step="0.1" style="flex: 1; max-width: 320px" />
      <span class="settings-hint">数值越大噪声越强，保护更彻底但可用性下降</span>
    </div>
    <div class="settings-row">
      <span class="settings-label">l-多样性（{{ privacy.l }}）</span>
      <el-slider v-model="privacy.l" :min="1" :max="5" :step="1" style="flex: 1; max-width: 320px" />
      <span class="settings-hint">等价类内至少有 l 个不同值，抵御同质攻击</span>
    </div>
    <div class="toolbar">
      <el-button type="primary" @click="savePrivacy">保存隐私参数</el-button>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">模型与降级</div>
    <div class="toolbar">
      <el-button :loading="healthLoading" @click="loadHealth">刷新状态</el-button>
      <el-button @click="ollamaDialog = true">配置 AI 助手连接</el-button>
    </div>
    <el-descriptions v-if="health" :column="2" border style="margin-top: 12px">
      <el-descriptions-item label="L2 语义">
        <el-tag :type="healthBadge(health.enable_l2).type" size="small">{{ healthBadge(health.enable_l2).text }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="L3 大模型">
        <el-tag :type="healthBadge(health.enable_l3).type" size="small">{{ healthBadge(health.enable_l3).text }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="NER 实体识别">
        <el-tag :type="healthBadge(health.ner).type" size="small">{{ healthBadge(health.ner).text }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="OCR 图片识别">
        <el-tag :type="healthBadge(health.ocr).type" size="small">{{ healthBadge(health.ocr).text }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="AI 助手" :span="2">
        {{ assistantModeLabel }}
      </el-descriptions-item>
    </el-descriptions>
    <div v-else class="settings-hint" style="margin-top: 12px">点击「刷新状态」读取服务端配置。</div>
  </div>

  <div class="panel">
    <div class="panel-title">关于</div>
    <div class="settings-row"><span class="settings-label">版本</span>{{ about.version }}</div>
  </div>

  <!-- 自定义场景弹窗 -->
  <el-dialog v-model="scenarioDialog" title="新增自定义场景" width="520px">
    <el-form :model="scenarioForm" label-width="120px">
      <el-form-item label="场景名称">
        <el-input v-model="scenarioForm.name" placeholder="例如 外包交付-甲方A" />
      </el-form-item>
      <el-form-item label="脱敏策略">
        <el-select v-model="scenarioForm.strategy" style="width: 100%">
          <el-option v-for="s in STRATEGIES" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="最小返回级别">
        <el-select v-model="scenarioForm.minLevel" style="width: 100%">
          <el-option v-for="l in LEVELS" :key="l.value" :label="l.label" :value="l.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="识别模式">
        <el-checkbox v-model="scenarioForm.enableL2">L2 语义</el-checkbox>
        <el-checkbox v-model="scenarioForm.enableL3">L3 大模型</el-checkbox>
        <el-checkbox v-model="scenarioForm.enableNer">自由文本 NER</el-checkbox>
      </el-form-item>
      <el-form-item label="建议启用审计">
        <el-switch v-model="scenarioForm.audit" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="scenarioDialog = false">取消</el-button>
      <el-button type="primary" @click="addCustomScenario">保存场景</el-button>
    </template>
  </el-dialog>

  <!-- AI 助手连接配置弹窗 -->
  <el-dialog v-model="ollamaDialog" title="AI 助手连接（仅展示）" width="520px">
    <el-form :model="ollamaForm" label-width="120px">
      <el-form-item label="模式">
        <el-radio-group v-model="ollamaForm.mode">
          <el-radio value="off">规则引擎</el-radio>
          <el-radio value="ollama">本机 Ollama</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="Endpoint">
        <el-input v-model="ollamaForm.endpoint" placeholder="http://127.0.0.1:11434/v1/chat/completions" />
      </el-form-item>
      <el-form-item label="模型名">
        <el-input v-model="ollamaForm.model" placeholder="qwen2.5:1.5b" />
      </el-form-item>
    </el-form>
    <div class="settings-hint" style="margin-top: 8px">
      修改需在服务端 .env 配置 FLOWMASK_ASSISTANT_* 环境变量并重启服务后才生效；此处仅作展示。
    </div>
    <template #footer>
      <el-button @click="ollamaDialog = false">取消</el-button>
      <el-button type="primary" @click="saveOllamaConfig">保存配置</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.settings-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0;
  flex-wrap: wrap;
}
.settings-item {
  display: flex;
  align-items: center;
  gap: 12px;
}
.settings-label {
  font-weight: 600;
  min-width: 120px;
}
.settings-hint {
  color: var(--text-muted, #888);
  font-size: 12px;
}
.settings-subtitle {
  font-weight: 600;
  margin-top: 16px;
  margin-bottom: 4px;
}
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
