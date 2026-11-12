<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";

const tasks = ref([]);
const findings = ref([]);
const audit = ref([]);
const verify = ref(null);
const current = ref(null);
const loading = ref(false);
const file = ref(null);
const textInput = ref("");
// 隐私导出参数：epsilon 越小噪声越大；l 是 l-多样性下限
const epsilon = ref(1.0);
const diversityL = ref(3);
// 多选删除
const selectedTasks = ref([]);
const deleting = ref(false);

async function loadTasks() {
  loading.value = true;
  try {
    const body = await api.tasks(1, 20);
    tasks.value = body.items || [];
    // 默认展开最新任务：结果与「隐私导出」都在详情面板里，省掉一次点击
    if (!current.value && tasks.value.length) {
      await openTask(tasks.value[0]);
    }
  } catch (err) {
    ElMessage.error(`加载任务失败：${err}`);
  } finally {
    loading.value = false;
  }
}

async function loadAudit() {
  try {
    const body = await api.audit(1, 20);
    audit.value = body.items || [];
    verify.value = await api.auditVerify();
  } catch (err) {
    ElMessage.error(`加载审计链失败：${err}`);
  }
}

async function openTask(row) {
  current.value = row;
  try {
    const body = await api.taskFindings(row.id, 1, 100);
    findings.value = body.items || [];
  } catch (err) {
    ElMessage.error(`加载任务详情失败：${err}`);
  }
}

async function createFromText() {
  if (!textInput.value.trim()) {
    ElMessage.warning("先输入文本");
    return;
  }
  try {
    const created = await api.createTaskText(textInput.value);
    ElMessage.success(`任务已创建：${created.task_id}`);
    textInput.value = "";
    await loadTasks();
  } catch (err) {
    ElMessage.error(`创建任务失败：${err}`);
  }
}

async function createFromFile(uploadFile) {
  file.value = uploadFile.raw;
  try {
    const created = await api.createTaskFile(uploadFile.raw);
    ElMessage.success(`任务已创建：${created.task_id}`);
    await loadTasks();
  } catch (err) {
    ElMessage.error(`上传失败：${err}`);
  }
}

function exportUrl(fmt) {
  return current.value ? api.taskExportUrl(current.value.id, fmt) : "#";
}

async function copyExport(fmt) {
  if (!current.value) return;
  try {
    const res = await fetch(api.taskExportUrl(current.value.id, fmt));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    window.open(api.taskExportUrl(current.value.id, fmt), "_blank");
  } catch (err) {
    ElMessage.error(`导出失败：${err}`);
  }
}

async function privacyExport() {
  if (!current.value) return;
  try {
    const body = await api.privacyExport(current.value.id, epsilon.value, diversityL.value);
    const privacy = body.meta.privacy;
    const noisy = Object.entries(privacy.dp_by_level)
      .map(([level, count]) => `${level}:${count}`)
      .join(" / ");
    const diversity = privacy.l_diversity.ok
      ? "通过"
      : `有 ${privacy.l_diversity.violation_count} 组不满足`;
    ElMessage({
      type: privacy.l_diversity.ok ? "success" : "warning",
      duration: 8000,
      message: `隐私导出：ε=${privacy.epsilon}，l=${privacy.l}；加噪分级计数 ${noisy}；l-多样性${diversity}`,
    });
    window.open(api.privacyExportUrl(current.value.id, epsilon.value, diversityL.value), "_blank");
  } catch (err) {
    ElMessage.error(`隐私导出失败：${err.message || err}`);
  }
}

function handleSelectionChange(rows) {
  selectedTasks.value = rows;
}

async function deleteSelected() {
  if (!selectedTasks.value.length) {
    ElMessage.warning("先勾选要删除的记录");
    return;
  }
  const ids = selectedTasks.value.map((row) => row.id);
  try {
    await ElMessageBox.confirm(
      `将删除选中的 ${ids.length} 条检测记录及其 findings、审计链记录。删除后审计哈希链会断裂（前端校验会标出断点），此操作不可恢复。`,
      "确认删除",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消", customClass: "fm-msgbox-opaque", modalClass: "fm-modal-opaque" }
    );
  } catch {
    return;
  }
  deleting.value = true;
  try {
    const result = await api.deleteTasks(ids);
    ElMessage.success(`已删除 ${result.deleted} 条记录`);
    selectedTasks.value = [];
    if (current.value && ids.includes(current.value.id)) {
      current.value = null;
      findings.value = [];
    }
    await loadTasks();
    await loadAudit();
  } catch (err) {
    ElMessage.error(`删除失败：${err.message || err}`);
  } finally {
    deleting.value = false;
  }
}

async function deleteOne(row) {
  try {
    await ElMessageBox.confirm(
      `将删除检测记录 ${row.id} 及其 findings、审计链记录。删除后审计哈希链会断裂（此操作不可恢复。`,
      "确认删除",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消", customClass: "fm-msgbox-opaque", modalClass: "fm-modal-opaque" }
    );
  } catch {
    return;
  }
  deleting.value = true;
  try {
    await api.deleteTask(row.id);
    ElMessage.success(`已删除记录 ${row.id}`);
    if (current.value && current.value.id === row.id) {
      current.value = null;
      findings.value = [];
    }
    await loadTasks();
    await loadAudit();
  } catch (err) {
    ElMessage.error(`删除失败：${err.message || err}`);
  } finally {
    deleting.value = false;
  }
}

onMounted(async () => {
  await loadTasks();
  await loadAudit();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      检测记录
      <span class="hint">内容检测页每次检测会自动建记录，下方也可手动补充</span>
    </div>
    <el-input v-model="textInput" type="textarea" :rows="2" placeholder="粘贴文本创建检测任务" />
    <div class="toolbar">
      <el-button type="primary" class="task-create-text" @click="createFromText">提交文本建任务</el-button>
      <el-upload :auto-upload="false" :show-file-list="false" :on-change="createFromFile">
        <el-button class="task-create-file">上传文件建任务</el-button>
      </el-upload>
      <el-button :loading="loading" @click="loadTasks">刷新</el-button>
      <el-button
        type="danger"
        class="task-delete-batch"
        :disabled="!selectedTasks.length"
        :loading="deleting"
        @click="deleteSelected"
      >
        删除选中 ({{ selectedTasks.length }})
      </el-button>
    </div>
    <el-table
      class="task-table"
      :data="tasks"
      stripe
      style="margin-top: 12px"
      max-height="360"
      @row-click="openTask"
      @selection-change="handleSelectionChange"
    >
      <el-table-column type="selection" width="42" />
      <el-table-column prop="id" label="任务" width="200" />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column prop="content_type" label="类型" width="90" />
      <el-table-column prop="created_at" label="创建时间" width="220" class-name="task-time" />
      <el-table-column prop="total_findings" label="命中" width="80" />
      <el-table-column prop="error" label="错误" show-overflow-tooltip />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="scope">
          <el-button size="small" type="danger" link :loading="deleting" @click.stop="deleteOne(scope.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>

  <div class="panel" v-if="current">
    <div class="panel-title">
      任务 {{ current.id }} 的检测结果
      <span class="hint">
        <el-button size="small" class="export-jsonl" @click="copyExport('jsonl')">导出 JSONL</el-button>
        <el-button size="small" class="export-csv" @click="copyExport('csv')">导出 CSV</el-button>
        <el-button size="small" class="export-json" @click="copyExport('json')">导出 JSON</el-button>
        <el-button size="small" class="export-sql" @click="copyExport('sql')">导出 SQL</el-button>
        <el-divider direction="vertical" />
        <span class="hint-inline">ε</span>
        <el-input-number v-model="epsilon" :min="0.05" :max="10" :step="0.5" size="small" style="width: 96px" />
        <span class="hint-inline">l</span>
        <el-input-number v-model="diversityL" :min="1" :max="20" size="small" style="width: 80px" />
        <el-button size="small" type="warning" class="privacy-export" @click="privacyExport">隐私导出</el-button>
      </span>
    </div>
    <el-table :data="findings" stripe>
      <el-table-column prop="path" label="位置" width="170" />
      <el-table-column prop="level" label="级别" width="80" />
      <el-table-column prop="score" label="分数" width="80" />
      <el-table-column label="类别" width="200">
        <template #default="scope">{{ (scope.row.types || []).join(", ") }}</template>
      </el-table-column>
      <el-table-column prop="masked" label="脱敏副本" show-overflow-tooltip />
    </el-table>
    <div v-if="!findings.length" class="empty">该任务暂无可展示结果（可能仍在运行）</div>
  </div>

  <div class="panel">
    <div class="panel-title">
      审计链（哈希链 + HMAC 签名）
      <span class="hint" v-if="verify">
        校验：<b :style="{ color: verify.ok ? '#5b96ed' : '#d64545' }">{{ verify.ok ? "通过" : "失败" }}</b>
        · 共 {{ verify.total }} 条，已校验 {{ verify.checked }} 条
        <template v-if="verify.broken_at != null"> · 断点 {{ verify.broken_at }}</template>
      </span>
    </div>
    <div class="toolbar">
      <el-button size="small" @click="loadAudit">刷新并校验</el-button>
    </div>
    <el-table class="audit-table" :data="audit" stripe style="margin-top: 12px" max-height="360">
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="ts" label="时间" width="200" />
      <el-table-column prop="task_id" label="任务" width="180" />
      <el-table-column prop="rule_id" label="规则" width="140" />
      <el-table-column prop="operator_id" label="操作人" width="120" />
      <el-table-column prop="input_hash" label="输入哈希" width="150" show-overflow-tooltip />
      <el-table-column prop="prev_hash" label="前序哈希" show-overflow-tooltip />
    </el-table>
  </div>
</template>

<style scoped>
/* 任务创建时间固定一行展示，避免换行挤压其它列 */
:deep(.task-time .cell) {
  white-space: nowrap;
}
</style>
