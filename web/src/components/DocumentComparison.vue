<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { ElMessage } from "element-plus";
import { api, downloadWithAuth } from "../api";
import { prepareComparison, mapLine } from "../comparison";
import DocumentPane from "./DocumentPane.vue";
const props = defineProps({ taskId: String, filename: String });
const emit = defineEmits(["close"]);
const open = ref(true), loading = ref(true), error = ref(""), model = shallowRef(null);
const view = ref("split"), sync = ref(true), wrap = ref(false), selected = ref(-1), details = ref(false);
const original = ref(null), masked = ref(null), narrow = ref(false);
const media = window.matchMedia("(max-width: 760px)");
const effectiveView = computed(() => narrow.value && view.value === "split" ? "original" : view.value);
const active = computed(() => model.value?.changes[selected.value]);
const controller = new AbortController();
const returnFocus = document.activeElement;
let closed = false;
function escape(event) {
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
}
function resize() { narrow.value = media.matches; }
async function load() {
  try {
    const payload = await api.taskComparison(props.taskId, controller.signal);
    if (!controller.signal.aborted) model.value = prepareComparison(payload);
  } catch (err) {
    if (controller.signal.aborted) return;
    error.value = ({ 401: "无权读取原始文档，请检查登录凭据。", 403: "当前会话无权读取该任务原文。", 410: "对比快照已过期、重启失效或未保留，请重新提交文件。", 413: "文件过大，无法打开对比，请缩小文件后重试。", 409: "任务未完成、脱敏失败或策略已变化，请重新检测。" })[err.status] || "无法获取对比文档，请稍后重试。";
  } finally { loading.value = false; }
}
function select(id, showDetails = true) {
  selected.value = id;
  details.value = showDetails;
  const change = model.value.changes[id];
  original.value?.locate(change.originalLine);
  masked.value?.locate(change.maskedLine);
}
function navigate(delta) {
  const count = model.value.changes.length;
  if (count) select(selected.value < 0 ? (delta > 0 ? 0 : count - 1) : (selected.value + delta + count) % count, false);
}
function position(side, value) {
  if (!sync.value || effectiveView.value !== "split") return;
  const target = side === "original" ? masked : original;
  target.value?.locate(mapLine(model.value.anchors, side, value));
}
async function download() {
  try { await downloadWithAuth(api.taskComparisonDownloadUrl(props.taskId), "flowmask-masked.txt"); }
  catch { ElMessage.error("下载失败，快照可能已过期或当前会话无权访问。"); }
}
function cleanup() { controller.abort(); model.value = null; details.value = false; selected.value = -1; }
function close() {
  if (closed) return;
  closed = true;
  cleanup();
  emit("close");
  nextTick(() => { if (returnFocus?.isConnected) returnFocus.focus(); });
}
onMounted(() => { document.addEventListener("keydown", escape, true); resize(); media.addEventListener("change", resize); load(); });
onBeforeUnmount(() => { document.removeEventListener("keydown", escape, true); cleanup(); media.removeEventListener("change", resize); });
</script>
<template>
  <el-dialog v-model="open" class="document-comparison" width="90vw" top="6vh" destroy-on-close :close-on-click-modal="false" :before-close="close" @closed="close">
    <template #header><div class="compare-title">文档对比 · {{ filename }}</div></template>
    <div v-if="loading" class="compare-state" role="status">正在加载受权限保护的文档快照…</div>
    <el-alert v-else-if="error" :title="error" type="warning" :closable="false" show-icon />
    <template v-else-if="model">
      <div class="compare-toolbar">
        <el-tag type="success" size="small">检测完成 · 已脱敏</el-tag>
        <span>{{ model.hits }} 次敏感命中 · {{ model.changes.length }} 处差异</span>
        <el-tag v-if="model.coverage !== 'complete'" type="warning" size="small">检测覆盖不完整</el-tag>
        <el-select v-model="view" aria-label="文档对比视图" style="width: 155px" size="small">
          <el-option v-if="!narrow" label="左右对比" value="split" />
          <el-option label="仅查看原文" value="original" />
          <el-option label="仅查看脱敏文档" value="masked" />
        </el-select>
        <el-checkbox v-model="sync">同步滚动</el-checkbox>
        <el-checkbox v-model="wrap">自动换行</el-checkbox>
        <el-button size="small" :disabled="!model.changes.length" @click="navigate(-1)">上一处</el-button>
        <span aria-live="polite">{{ selected + 1 }} / {{ model.changes.length }}</span>
        <el-button size="small" :disabled="!model.changes.length" @click="navigate(1)">下一处</el-button>
        <el-button size="small" type="primary" plain @click="download">下载脱敏文档</el-button>
      </div>
      <p class="compare-note">按解析片段展示，保留片段内换行；结构化及 Office/PDF 文档不还原原文件排版。下载为与右侧内容一致的 UTF-8 文本。</p>
      <div v-if="!model.changes.length" class="compare-note" role="status">未发现需要替换的敏感信息，两侧内容一致。</div>
      <div class="compare-body" :class="{ single: effectiveView !== 'split' }">
        <DocumentPane v-if="effectiveView !== 'masked'" ref="original" :lines="model.original" side="original" :selected="selected" :wrap="wrap" @select="select" @position="position('original', $event)" />
        <DocumentPane v-if="effectiveView !== 'original'" ref="masked" :lines="model.masked" side="masked" :selected="selected" :wrap="wrap" @select="select" @position="position('masked', $event)" />
        <aside v-if="details && active" class="field-details" role="status" aria-label="字段信息">
          <el-button class="detail-close" link aria-label="关闭字段信息" @click="details = false">关闭</el-button>
          <div>类别：{{ active.type || '敏感信息' }}</div>
          <div>实际检测级别：{{ active.level }}</div>
          <div>策略：{{ active.strategy === 'block' ? '全屏蔽' : active.strategy === 'partial' ? '部分遮蔽' : active.strategy }}</div>
          <div>状态：{{ active.status }}</div>
          <div v-if="active.full_segment">无精确位置证据，按安全策略屏蔽整个片段。</div>
        </aside>
      </div>
    </template>
  </el-dialog>
</template>
<style scoped>
.compare-title { font-size: 17px; font-weight: 600; overflow-wrap: anywhere; padding-right: 24px; }
.compare-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.compare-toolbar .el-button { margin-left: 0; }
.compare-note { font-size: 12px; color: var(--muted); margin: 10px 0; }
.compare-state { padding: 32px; color: var(--muted); }
.compare-body { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; flex: 1; min-height: 0; }
.compare-body.single { grid-template-columns: minmax(0, 1fr); }
.field-details { position: absolute; right: 16px; bottom: 16px; width: min(260px, calc(100% - 32px)); padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--panel); color: var(--ink); font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.detail-close { float: right; }
</style>
<style>
.el-dialog.document-comparison { height: 86vh; margin-bottom: 0; display: flex; flex-direction: column; border-radius: var(--radius-lg); }
.document-comparison .el-dialog__body { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: auto; }
</style>
