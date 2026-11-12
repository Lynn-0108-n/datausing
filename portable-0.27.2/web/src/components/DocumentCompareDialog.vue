<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS, LEVEL_LABELS } from "../api";
import { escapeHtml } from "../highlight";
import { formatCount, typeLabel } from "../statsMeta";

// 文档脱敏前后对比工作台：数据只来自 /tasks/{id}/comparison 的服务端临时快照，
// 前端不做任何“模拟脱敏”，也不把原文写入 localStorage / sessionStorage / 日志。
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  taskId: { type: String, default: "" },
  filename: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue"]);

const LINE_HEIGHT = 22; // 与 .dc-text 行高保持一致
const WRAP_CHAR_LIMIT = 200_000;
const STRATEGY_LABELS = {
  block: "全屏蔽",
  partial: "部分遮掩",
  hash: "哈希替换",
  generalize: "数值泛化",
  pseudonym: "假名替换",
  encrypt: "加密替换",
};

const dialogOpen = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

const loading = ref(false);
const loadError = ref(null);
const model = ref(null);
const activeDiff = ref(-1);
const view = ref("split");
const syncScroll = ref(true);
const wrap = ref(false);
const isNarrow = ref(false);
const downloading = ref(false);

const scrollTops = reactive({ orig: 0, masked: 0 });
const paneHeights = reactive({ orig: 360, masked: 360 });
const scrollEls = { orig: null, masked: null };
const wrapMeasure = {
  orig: { tops: [], dirty: true },
  masked: { tops: [], dirty: true },
};
const syncingUntil = { orig: 0, masked: 0 };
const card = reactive({ open: false, x: 0, y: 0, change: null });
const cardEl = ref(null);

let mediaQuery = null;
let resizeTimer = 0;
let syncRaf = 0;
let syncSourceKey = "orig";
let activeMarksRaf = 0;
let pointerHandler = null;

const wrapDisabled = computed(() => (model.value ? model.value.original.length > WRAP_CHAR_LIMIT : false));
const totalDiffs = computed(() => (model.value ? model.value.changes.length : 0));
const diffPosition = computed(() => {
  if (!model.value) return "";
  if (!totalDiffs.value) return "无差异";
  if (activeDiff.value < 0) return `共 ${totalDiffs.value} 处差异`;
  return `第 ${activeDiff.value + 1} 处 / 共 ${totalDiffs.value} 处`;
});
const viewOptions = computed(() =>
  isNarrow.value
    ? [
        { value: "original", label: "原文" },
        { value: "masked", label: "脱敏文档" },
      ]
    : [
        { value: "split", label: "左右对比" },
        { value: "original", label: "仅原文" },
        { value: "masked", label: "仅脱敏文档" },
      ]
);
const visibleSides = computed(() => {
  if (view.value === "split") return ["orig", "masked"];
  return view.value === "masked" ? ["masked"] : ["orig"];
});
// 后端 offset 以 unicode codepoint 计数，展示字符数时也按 codepoint 统计（emoji 不重复计数）。
function codePointLength(text) {
  let count = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) i += 1;
    }
    count += 1;
  }
  return count;
}

const metaText = computed(() => {
  const current = model.value;
  if (!current) return "";
  return `${current.segments.length} 个片段 · ${formatCount(codePointLength(current.original))} 字符`;
});
const displayFilename = computed(
  () => model.value?.payload?.filename || props.filename || "文档对比"
);

function closeDialog() {
  dialogOpen.value = false;
}

// ---------- 错误状态 ----------
function describeError(err) {
  const status = err?.status;
  const code = err?.code || "";
  const errorId = err?.errorId || "";
  if (status === 404) {
    return { title: "任务不存在", desc: "该检测记录已被删除，请刷新批量结果后重试。", errorId };
  }
  if (status === 410 || code === "SNAPSHOT_EXPIRED") {
    return {
      title: "对比快照已过期",
      desc: "原文对比只在检测完成后短时间内可用（服务端临时快照，约 5 分钟）。请重新检测该文件后再对比。",
      errorId,
    };
  }
  if (status === 403 || code === "SNAPSHOT_FORBIDDEN") {
    return {
      title: "无权限查看原文",
      desc: "检测任务与当前浏览器会话不匹配。原文只允许发起检测的会话读取，请在同一会话中重新检测。",
      errorId,
    };
  }
  if (status === 413 || code === "COMPARISON_TOO_LARGE") {
    return { title: "文档过大", desc: "文档超过 100 万字符，无法生成对比视图。请拆分文件后重新检测。", errorId };
  }
  if (status === 409 || code === "SNAPSHOT_POLICY_CHANGED" || code === "MASK_LOCATION_INVALID") {
    return { title: "脱敏副本已失效", desc: "任务脱敏副本与当前策略不一致，请重新检测后查看对比。", errorId };
  }
  return {
    title: "对比加载失败",
    desc: err?.message ? String(err.message) : "网络异常或服务暂时不可用，请稍后重试。",
    errorId,
  };
}

async function load() {
  if (!props.taskId || loading.value) return;
  loading.value = true;
  loadError.value = null;
  model.value = null;
  activeDiff.value = -1;
  closeFieldCard();
  try {
    const payload = await api.taskComparison(props.taskId);
    model.value = buildModel(payload);
    await nextTick();
    measurePanes();
    markWrapDirty();
  } catch (err) {
    loadError.value = describeError(err);
  } finally {
    loading.value = false;
    await nextTick();
    measurePanes();
    markWrapDirty();
  }
}

// ---------- 差异数据：unicode codepoint → UTF-16 偏移 ----------
// 后端 offset_unit=unicode-codepoint，JS 字符串下标是 UTF-16 code unit；
// 仅当文本含增补平面字符（emoji 等）时才需要逐码点换算，避免高亮错位。
function codepointOffsetsToUtf16(text, offsets) {
  const wanted = [...new Set(offsets.filter((value) => Number.isFinite(value) && value >= 0))].sort(
    (a, b) => a - b
  );
  const result = new Map();
  if (!wanted.length) return result;
  let u16 = 0;
  let cp = 0;
  let index = 0;
  while (index < wanted.length && u16 <= text.length) {
    if (cp === wanted[index]) {
      result.set(cp, u16);
      index += 1;
      continue;
    }
    if (u16 >= text.length) break;
    const code = text.codePointAt(u16);
    u16 += code > 0xffff ? 2 : 1;
    cp += 1;
  }
  // 越界偏移按文本末尾处理（正常情况下后端已校验，此处只做兜底）
  for (; index < wanted.length; index += 1) result.set(wanted[index], text.length);
  return result;
}

function buildModel(payload) {
  const segments = Array.isArray(payload?.segments) ? payload.segments : [];
  let original = "";
  let masked = "";
  const changes = [];
  const segmentMeta = [];

  segments.forEach((segment, segIndex) => {
    const segOriginal = String(segment.original ?? "");
    const segMasked = String(segment.masked ?? "");
    const origOffset = original.length;
    const maskedOffset = masked.length;
    if (segIndex > 0) {
      // 与服务端 comparison/download 一致：片段之间用换行连接
      original += "\n";
      masked += "\n";
    }
    segmentMeta.push({ index: segIndex, path: segment.path ?? "" });

    const rawChanges = Array.isArray(segment.changes) ? segment.changes : [];
    if (rawChanges.length) {
      const origMap = codepointOffsetsToUtf16(
        segOriginal,
        rawChanges.flatMap((item) => [Number(item.start), Number(item.end)])
      );
      const maskedMap = codepointOffsetsToUtf16(
        segMasked,
        rawChanges.flatMap((item) => [Number(item.masked_start), Number(item.masked_end)])
      );
      for (const raw of rawChanges) {
        const start = origMap.get(Number(raw.start));
        const end = origMap.get(Number(raw.end));
        const maskedStart = maskedMap.get(Number(raw.masked_start));
        const maskedEnd = maskedMap.get(Number(raw.masked_end));
        if (![start, end, maskedStart, maskedEnd].every((value) => Number.isFinite(value))) continue;
        if (end <= start || maskedEnd <= maskedStart) continue;
        changes.push({
          start: origOffset + start,
          end: origOffset + end,
          maskedStart: maskedOffset + maskedStart,
          maskedEnd: maskedOffset + maskedEnd,
          type: raw.type ?? "",
          strategy: raw.strategy ?? "",
          level: raw.level ?? "",
          status: raw.status ?? "已脱敏",
          segIndex,
        });
      }
    }

    original += segOriginal;
    masked += segMasked;
  });

  changes.sort((a, b) => a.start - b.start || a.maskedStart - b.maskedStart);
  changes.forEach((change, index) => {
    change.i = index;
  });

  const originalSide = buildSide(original, changes, "orig");
  const maskedSide = buildSide(masked, changes, "masked");
  return {
    payload,
    original,
    masked,
    changes,
    segments: segmentMeta,
    sides: { orig: originalSide, masked: maskedSide },
    anchors: buildAnchors(originalSide, maskedSide, changes),
  };
}

function buildSide(text, changes, key) {
  const starts = [0];
  for (let i = 0; i < text.length; i += 1) {
    if (text.charCodeAt(i) === 10) starts.push(i + 1);
  }
  const lineCount = starts.length;
  const lineOf = (offset) => {
    let lo = 0;
    let hi = lineCount - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= offset) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  };

  const buckets = new Array(lineCount);
  for (const change of changes) {
    const from0 = key === "orig" ? change.start : change.maskedStart;
    const to0 = key === "orig" ? change.end : change.maskedEnd;
    if (to0 <= from0) continue;
    let line = lineOf(from0);
    while (line < lineCount) {
      const lineStart = starts[line];
      if (lineStart >= to0) break;
      const lineEnd = line + 1 < lineCount ? starts[line + 1] - 1 : text.length;
      const from = Math.max(from0, lineStart);
      const to = Math.min(to0, lineEnd);
      if (to > from) {
        if (!buckets[line]) buckets[line] = [];
        buckets[line].push({ from: from - lineStart, to: to - lineStart, i: change.i });
      }
      if (to0 <= lineEnd) break;
      line += 1;
    }
  }

  const lines = new Array(lineCount);
  for (let line = 0; line < lineCount; line += 1) {
    const lineStart = starts[line];
    const lineEnd = line + 1 < lineCount ? starts[line + 1] - 1 : text.length;
    const fragments = (buckets[line] || []).sort((a, b) => a.from - b.from);
    let html = "";
    let cursor = 0;
    for (const fragment of fragments) {
      if (fragment.to <= cursor) continue;
      const from = Math.max(fragment.from, cursor);
      html += escapeHtml(text.slice(lineStart + cursor, lineStart + from));
      html += `<mark class="dc-mark dc-mark-${key === "orig" ? "orig" : "masked"}" data-i="${
        fragment.i
      }" title="点击查看该字段信息">${escapeHtml(text.slice(lineStart + from, lineStart + fragment.to))}</mark>`;
      cursor = fragment.to;
    }
    html += escapeHtml(text.slice(lineStart + cursor, lineEnd));
    lines[line] = { no: line + 1, html };
  }
  return { key, text, starts, lineCount, lineOf, lines };
}

// 行号对齐：两侧行数相同且所有差异所在行一致时直接按行同步；
// 否则用每条差异的 (原文行, 脱敏行) 作为锚点做分段线性映射。
function buildAnchors(originalSide, maskedSide, changes) {
  const sameLines =
    originalSide.lineCount === maskedSide.lineCount &&
    changes.every((change) => originalSide.lineOf(change.start) === maskedSide.lineOf(change.maskedStart));
  if (sameLines) return null;

  const anchors = [{ o: 0, m: 0 }];
  for (const change of changes) {
    const o = originalSide.lineOf(change.start);
    const m = maskedSide.lineOf(change.maskedStart);
    const last = anchors[anchors.length - 1];
    if (o <= last.o) continue;
    anchors.push({ o, m });
  }
  const tailO = originalSide.lineCount - 1;
  const tailM = maskedSide.lineCount - 1;
  const last = anchors[anchors.length - 1];
  if (last.o === tailO) last.m = tailM;
  else anchors.push({ o: tailO, m: tailM });
  for (let i = 1; i < anchors.length; i += 1) {
    if (anchors[i].m < anchors[i - 1].m) anchors[i].m = anchors[i - 1].m;
  }
  return anchors;
}

// ---------- 滚动位置（以“行 + 行内比例”表示，换行模式下也成立） ----------
function measurePanes() {
  for (const key of ["orig", "masked"]) {
    const el = scrollEls[key];
    if (el) paneHeights[key] = Math.max(120, el.clientHeight);
  }
}

function markWrapDirty() {
  wrapMeasure.orig.dirty = true;
  wrapMeasure.masked.dirty = true;
}

function ensureWrapTops(key) {
  const cache = wrapMeasure[key];
  if (!cache.dirty && cache.tops.length) return cache.tops;
  const el = scrollEls[key];
  if (!el) return cache.tops;
  const nodes = el.querySelectorAll(".dc-content .dc-line");
  const tops = new Array(nodes.length);
  for (let i = 0; i < nodes.length; i += 1) tops[i] = nodes[i].offsetTop;
  cache.tops = tops;
  cache.dirty = false;
  return tops;
}

function readScrollPosition(key) {
  const el = scrollEls[key];
  const side = model.value?.sides[key];
  if (!el || !side) return 0;
  const maxPos = Math.max(0, side.lineCount - 1);
  if (!wrap.value) {
    return Math.min(Math.max(el.scrollTop / LINE_HEIGHT, 0), maxPos);
  }
  const tops = ensureWrapTops(key);
  if (!tops.length) return 0;
  const top = el.scrollTop;
  let lo = 0;
  let hi = tops.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (tops[mid] <= top) lo = mid;
    else hi = mid - 1;
  }
  const startTop = tops[lo];
  const endTop = lo + 1 < tops.length ? tops[lo + 1] : startTop + LINE_HEIGHT;
  const height = Math.max(1, endTop - startTop);
  const ratio = Math.min(Math.max((top - startTop) / height, 0), 1);
  return Math.min(lo + ratio, maxPos);
}

function scrollPaneToPosition(key, position) {
  const el = scrollEls[key];
  const side = model.value?.sides[key];
  if (!el || !side) return;
  const clamped = Math.min(Math.max(position, 0), Math.max(0, side.lineCount - 1));
  let top = 0;
  if (wrap.value) {
    const tops = ensureWrapTops(key);
    if (tops.length) {
      const line = Math.min(Math.floor(clamped), tops.length - 1);
      const startTop = tops[line];
      const endTop = line + 1 < tops.length ? tops[line + 1] : startTop + LINE_HEIGHT;
      top = startTop + (clamped - line) * Math.max(1, endTop - startTop);
    }
  } else {
    top = clamped * LINE_HEIGHT;
  }
  el.scrollTop = Math.max(0, top);
  scrollTops[key] = el.scrollTop;
  syncingUntil[key] = performance.now() + 120;
}

function mapPosition(position, fromKey) {
  const current = model.value;
  const anchors = current?.anchors;
  if (!current || !anchors) return position;
  const srcCount = fromKey === "orig" ? current.sides.orig.lineCount : current.sides.masked.lineCount;
  const maxPos = Math.max(0, srcCount - 1);
  const value = Math.min(Math.max(position, 0), maxPos);
  const ordered = fromKey === "orig" ? anchors : anchors.map((item) => ({ o: item.m, m: item.o }));
  if (!ordered.length) return value;
  for (let i = 1; i < ordered.length; i += 1) {
    const previous = ordered[i - 1];
    const next = ordered[i];
    if (value <= next.o) {
      if (next.o === previous.o) return next.m;
      return previous.m + ((next.m - previous.m) * (value - previous.o)) / (next.o - previous.o);
    }
  }
  return ordered[ordered.length - 1].m;
}

// ---------- 同步滚动 ----------
function applySync() {
  syncRaf = 0;
  if (!syncScroll.value || !model.value) return;
  if (visibleSides.value.length < 2) return;
  const sourceKey = syncSourceKey;
  const targetKey = sourceKey === "orig" ? "masked" : "orig";
  if (!scrollEls[sourceKey] || !scrollEls[targetKey]) return;
  scrollPaneToPosition(targetKey, mapPosition(readScrollPosition(sourceKey), sourceKey));
}

function handleScroll(key, event) {
  const el = event.target;
  if (!el) return;
  scrollTops[key] = el.scrollTop;
  if (card.open) closeFieldCard();
  const now = performance.now();
  if (syncingUntil[key] > now) {
    scheduleActiveMarks();
    return;
  }
  if (syncScroll.value && visibleSides.value.length === 2) {
    syncSourceKey = key;
    if (!syncRaf) syncRaf = requestAnimationFrame(applySync);
  }
  scheduleActiveMarks();
}

// ---------- 高亮 ----------
function scheduleActiveMarks() {
  if (activeMarksRaf) return;
  activeMarksRaf = requestAnimationFrame(() => {
    activeMarksRaf = 0;
    updateActiveMarks();
  });
}

function updateActiveMarks() {
  const root = document.querySelector(".dc-workbench");
  if (!root) return;
  root.querySelectorAll("mark.dc-mark.is-active").forEach((node) => node.classList.remove("is-active"));
  if (activeDiff.value < 0) return;
  root
    .querySelectorAll(`mark.dc-mark[data-i="${activeDiff.value}"]`)
    .forEach((node) => node.classList.add("is-active"));
}

function scrollToChange(change) {
  const current = model.value;
  if (!current) return;
  for (const key of visibleSides.value) {
    const side = current.sides[key];
    const offset = key === "orig" ? change.start : change.maskedStart;
    const line = side.lineOf(offset);
    const visibleLines = Math.max(1, (paneHeights[key] || 360) / LINE_HEIGHT);
    const lead = Math.min(6, Math.floor(visibleLines * 0.3));
    scrollPaneToPosition(key, Math.max(0, line - lead));
  }
  syncingUntil.orig = performance.now() + 160;
  syncingUntil.masked = performance.now() + 160;
}

function gotoDiff(step) {
  const total = totalDiffs.value;
  if (!total) return;
  const current = activeDiff.value;
  const next = current < 0 ? (step > 0 ? 0 : total - 1) : (current + step + total) % total;
  const change = model.value?.changes[next];
  if (!change) return;
  activeDiff.value = next;
  closeFieldCard();
  scrollToChange(change);
  nextTick(() => {
    updateActiveMarks();
    requestAnimationFrame(updateActiveMarks);
  });
}

function handlePaneClick(key, event) {
  const mark = event.target.closest ? event.target.closest("mark.dc-mark[data-i]") : null;
  if (!mark) {
    closeFieldCard();
    return;
  }
  const index = Number(mark.getAttribute("data-i"));
  const change = model.value?.changes[index];
  if (!change) return;
  activeDiff.value = index;
  updateActiveMarks();

  // 双向定位：点击一侧高亮时，把另一侧对应的位置滚入视野
  if (visibleSides.value.length === 2) {
    const otherKey = key === "orig" ? "masked" : "orig";
    const otherSide = model.value?.sides[otherKey];
    if (otherSide) {
      const offset = otherKey === "orig" ? change.start : change.maskedStart;
      const line = otherSide.lineOf(offset);
      const visibleLines = Math.max(1, (paneHeights[otherKey] || 360) / LINE_HEIGHT);
      const lead = Math.min(6, Math.floor(visibleLines * 0.3));
      scrollPaneToPosition(otherKey, Math.max(0, line - lead));
    }
  }
  showFieldCard(change, mark);
  scheduleActiveMarks();
}

// ---------- 字段信息浮层 ----------
function showFieldCard(change, markEl) {
  const rect = markEl.getBoundingClientRect();
  const width = 288;
  const height = 224;
  const margin = 12;
  let x = rect.right + margin;
  if (x + width > window.innerWidth - margin) x = Math.max(margin, rect.left - width - margin);
  let y = Math.min(Math.max(rect.top - 10, margin), window.innerHeight - height - margin);
  card.x = x;
  card.y = y;
  card.change = change;
  card.open = true;
  if (!pointerHandler) {
    pointerHandler = (event) => {
      if (cardEl.value && cardEl.value.contains(event.target)) return;
      if (event.target.closest && event.target.closest("mark.dc-mark")) return;
      closeFieldCard();
    };
    document.addEventListener("pointerdown", pointerHandler, true);
  }
}

function closeFieldCard() {
  card.open = false;
  card.change = null;
  if (pointerHandler) {
    document.removeEventListener("pointerdown", pointerHandler, true);
    pointerHandler = null;
  }
}

function levelColor(level) {
  return LEVEL_COLORS[level] || "#71839d";
}
function levelLabel(level) {
  return LEVEL_LABELS[level] || level || "未知";
}
function strategyLabel(strategy) {
  return STRATEGY_LABELS[strategy] || strategy || "未知策略";
}
function changePath(change) {
  return model.value?.segments?.[change.segIndex]?.path || "";
}

// ---------- 视图切换 / 换行 ----------
async function setView(nextView) {
  if (!nextView || nextView === view.value) return;
  const positions = { orig: readScrollPosition("orig"), masked: readScrollPosition("masked") };
  const had = { orig: Boolean(scrollEls.orig), masked: Boolean(scrollEls.masked) };
  view.value = nextView;
  markWrapDirty();
  await nextTick();
  measurePanes();
  if (had.orig) scrollPaneToPosition("orig", positions.orig);
  if (had.masked) scrollPaneToPosition("masked", positions.masked);
  updateActiveMarks();
}

async function toggleWrap(next) {
  const nextValue = Boolean(next);
  if (nextValue === wrap.value) return;
  if (nextValue && wrapDisabled.value) return;
  const positions = { orig: readScrollPosition("orig"), masked: readScrollPosition("masked") };
  wrap.value = nextValue;
  markWrapDirty();
  await nextTick();
  measurePanes();
  scrollPaneToPosition("orig", positions.orig);
  scrollPaneToPosition("masked", positions.masked);
  updateActiveMarks();
}

// ---------- 下载 ----------
async function downloadMasked() {
  if (!props.taskId || downloading.value) return;
  downloading.value = true;
  try {
    await api.comparisonDownload(props.taskId);
    ElMessage.success("脱敏文档已下载");
  } catch (err) {
    ElMessage.error(`下载失败：${describeError(err).title}`);
  } finally {
    downloading.value = false;
  }
}

// ---------- 生命周期 ----------
function setPaneEl(key, el) {
  scrollEls[key] = el;
  if (el) paneHeights[key] = Math.max(120, el.clientHeight);
}

function syncMedia() {
  if (!mediaQuery) {
    isNarrow.value = window.innerWidth <= 980;
    return;
  }
  isNarrow.value = mediaQuery.matches;
  if (isNarrow.value && view.value === "split") setView("original");
}

function handleResize() {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    syncMedia();
    measurePanes();
    markWrapDirty();
    if (card.open) closeFieldCard();
  }, 120);
}

function openAndLoad() {
  view.value = isNarrow.value ? "original" : "split";
  syncScroll.value = true;
  wrap.value = false;
  activeDiff.value = -1;
  model.value = null;
  loadError.value = null;
  closeFieldCard();
  load();
}

function handleClosed() {
  model.value = null;
  loadError.value = null;
  loading.value = false;
  activeDiff.value = -1;
  closeFieldCard();
  scrollTops.orig = 0;
  scrollTops.masked = 0;
  if (syncRaf) cancelAnimationFrame(syncRaf);
  syncRaf = 0;
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) openAndLoad();
    else closeFieldCard();
  }
);

watch(
  () => props.taskId,
  () => {
    if (props.modelValue) load();
  }
);

watch(syncScroll, (enabled) => {
  if (!enabled || !model.value || visibleSides.value.length < 2) return;
  scrollPaneToPosition("masked", mapPosition(readScrollPosition("orig"), "orig"));
});

onMounted(() => {
  mediaQuery = window.matchMedia ? window.matchMedia("(max-width: 980px)") : null;
  syncMedia();
  if (mediaQuery) {
    if (mediaQuery.addEventListener) mediaQuery.addEventListener("change", syncMedia);
    else if (mediaQuery.addListener) mediaQuery.addListener(syncMedia);
  }
  window.addEventListener("resize", handleResize);
  if (props.modelValue) openAndLoad();
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", handleResize);
  if (mediaQuery) {
    if (mediaQuery.removeEventListener) mediaQuery.removeEventListener("change", syncMedia);
    else if (mediaQuery.removeListener) mediaQuery.removeListener(syncMedia);
  }
  window.clearTimeout(resizeTimer);
  if (syncRaf) cancelAnimationFrame(syncRaf);
  if (activeMarksRaf) cancelAnimationFrame(activeMarksRaf);
  closeFieldCard();
});

// 虚拟滚动：不换行时只渲染视口附近的行；换行时渲染全部行（超长文档已禁用换行）
function renderedLines(key) {
  const current = model.value;
  if (!current) return [];
  const side = current.sides[key];
  if (!side) return [];
  let start = 0;
  let end = side.lineCount;
  if (!wrap.value) {
    const top = scrollTops[key] || 0;
    const height = paneHeights[key] || 360;
    start = Math.max(0, Math.floor(top / LINE_HEIGHT) - 10);
    end = Math.min(side.lineCount, Math.ceil((top + height) / LINE_HEIGHT) + 10);
  }
  const result = new Array(end - start);
  for (let i = start; i < end; i += 1) {
    const line = side.lines[i];
    result[i - start] = { no: line.no, html: line.html, top: i * LINE_HEIGHT };
  }
  return result;
}
</script>

<template>
  <el-dialog
    v-model="dialogOpen"
    class="dc-dialog"
    :width="isNarrow ? '96%' : '88%'"
    top="4vh"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="true"
    append-to-body
    destroy-on-close
    aria-label="文档脱敏前后对比"
    @closed="handleClosed"
  >
    <div class="dc-workbench">
      <h2 class="dc-sr-only">文档脱敏前后对比</h2>

      <header class="dc-head">
        <div class="dc-title-group">
          <div class="dc-title" :title="displayFilename">{{ displayFilename }}</div>
          <div class="dc-meta">
            <el-tag v-if="model" size="small" type="success" effect="plain">已脱敏</el-tag>
            <span v-if="model" class="dc-meta-text">
              <span title="规则命中次数，含敏感词命中；可能与下方差异处数不同">规则命中 {{ formatCount(model.payload.hits ?? 0) }} 次</span>
              · {{ metaText }}
            </span>
            <span v-else class="dc-meta-text">真实原文与脱敏结果对比</span>
          </div>
        </div>
        <div class="dc-head-actions">
          <el-radio-group :model-value="view" size="small" @change="setView">
            <el-radio-button v-for="option in viewOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </el-radio-button>
          </el-radio-group>
          <button type="button" class="dc-close" aria-label="关闭对比" @click="closeDialog">×</button>
        </div>
      </header>

      <div class="dc-toolbar">
        <div class="dc-toolbar-left">
          <el-switch
            v-model="syncScroll"
            size="small"
            active-text="同步滚动"
            :disabled="visibleSides.length < 2"
          />
          <el-switch
            :model-value="wrap"
            size="small"
            active-text="自动换行"
            :disabled="wrapDisabled"
            :title="wrapDisabled ? '文档超过 20 万字符，自动换行可能影响性能，已禁用' : '长行自动换行 / 关闭后横向滚动'"
            @change="toggleWrap"
          />
        </div>
        <div class="dc-toolbar-right">
          <div class="dc-nav">
            <el-button size="small" :disabled="!totalDiffs" @click="gotoDiff(-1)">上一处</el-button>
            <span class="dc-diff-label" aria-live="polite">{{ diffPosition }}</span>
            <el-button size="small" :disabled="!totalDiffs" @click="gotoDiff(1)">下一处</el-button>
          </div>
          <el-button size="small" type="primary" :loading="downloading" :disabled="!model" @click="downloadMasked">
            下载脱敏文档
          </el-button>
        </div>
      </div>

      <div class="dc-body">
        <div v-if="loading" class="dc-state">
          <span class="dc-spinner" aria-hidden="true"></span>
          <p class="dc-state-text">正在读取原文与脱敏结果…</p>
          <p class="dc-state-sub">原文仅从服务端临时快照读取，不写入浏览器存储</p>
        </div>

        <div v-else-if="loadError" class="dc-state">
          <span class="dc-state-icon" aria-hidden="true">!</span>
          <p class="dc-state-text">{{ loadError.title }}</p>
          <p class="dc-state-sub">{{ loadError.desc }}</p>
          <p v-if="loadError.errorId" class="dc-state-code">错误编号 {{ loadError.errorId }}</p>
          <div class="dc-state-actions">
            <el-button size="small" @click="closeDialog">关闭</el-button>
            <el-button size="small" type="primary" @click="load">重试</el-button>
          </div>
        </div>

        <div v-else-if="!model" class="dc-state">
          <p class="dc-state-text">暂无可对比内容</p>
          <p class="dc-state-sub">该任务没有可展示的文本片段，请确认文件解析是否成功。</p>
        </div>

        <template v-else>
          <div v-if="!totalDiffs" class="dc-notice">未发现敏感信息，两侧内容一致（无差异）</div>
          <div class="dc-panes" :class="{ 'is-single': visibleSides.length === 1 }">
            <div
              v-for="key in visibleSides"
              :key="key"
              class="dc-pane"
              :class="['dc-pane-' + key, { 'is-wrap': wrap }]"
            >
              <div class="dc-pane-head">
                <span class="dc-pane-title">{{ key === "orig" ? "原始文档" : "脱敏文档" }}</span>
                <span class="dc-pane-hint">
                  <i class="dc-swatch" :class="'dc-swatch-' + key"></i>
                  {{ key === "orig" ? "浅红 = 敏感命中" : "浅蓝 = 脱敏替换" }}
                </span>
              </div>
              <div
                class="dc-scroll"
                :ref="(el) => setPaneEl(key, el)"
                role="region"
                :aria-label="key === 'orig' ? '原始文档内容' : '脱敏文档内容'"
                @scroll.passive="handleScroll(key, $event)"
                @click="handlePaneClick(key, $event)"
              >
                <div
                  class="dc-content"
                  :style="wrap ? null : { height: model.sides[key].lineCount * 22 + 'px' }"
                >
                  <template v-if="wrap">
                    <div v-for="line in model.sides[key].lines" :key="line.no" class="dc-line dc-line-wrap">
                      <span class="dc-ln" aria-hidden="true">{{ line.no }}</span><span
                        class="dc-text"
                        v-html="line.html"
                      ></span>
                    </div>
                  </template>
                  <template v-else>
                    <div
                      v-for="line in renderedLines(key)"
                      :key="line.no"
                      class="dc-line"
                      :style="{ top: line.top + 'px' }"
                    >
                      <span class="dc-ln" aria-hidden="true">{{ line.no }}</span><span
                        class="dc-text"
                        v-html="line.html"
                      ></span>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>

      <footer v-if="model && !loadError" class="dc-foot">
        <span>内容来自本次检测任务的服务端临时快照 · 仅当前会话可读 · 不写入浏览器存储</span>
        <span class="dc-foot-right">对比视图与实际下载的脱敏文件同源</span>
      </footer>
    </div>
  </el-dialog>

  <Teleport to="body">
    <div
      v-if="card.open && card.change"
      ref="cardEl"
      class="dc-field-card"
      :style="{ left: card.x + 'px', top: card.y + 'px' }"
      role="dialog"
      aria-label="敏感字段信息"
    >
      <div class="dc-field-head">
        <span class="dc-field-title">{{ typeLabel(card.change.type) }}</span>
        <button type="button" class="dc-field-close" aria-label="关闭字段信息" @click="closeFieldCard">×</button>
      </div>
      <dl class="dc-field-list">
        <dt>敏感类别</dt>
        <dd>{{ typeLabel(card.change.type) }}</dd>
        <dt>检测级别</dt>
        <dd>
          <span class="dc-level" :style="{ backgroundColor: levelColor(card.change.level) }">
            {{ levelLabel(card.change.level) }}
          </span>
        </dd>
        <dt>替换策略</dt>
        <dd>{{ strategyLabel(card.change.strategy) }}</dd>
        <dt>处理状态</dt>
        <dd>{{ card.change.status || "已脱敏" }}</dd>
        <dt v-if="changePath(card.change)">文档片段</dt>
        <dd v-if="changePath(card.change)" class="dc-field-path">{{ changePath(card.change) }}</dd>
      </dl>
      <p class="dc-field-foot">对应位置已同步高亮，可用「上一处 / 下一处」继续浏览</p>
    </div>
  </Teleport>
</template>

<style>
.dc-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* —— 弹窗外壳：沿用现有玻璃拟态弹窗风格，只把正文区改为无内边距工作台 —— */
.dc-dialog {
  max-width: 1760px;
}
.dc-dialog .el-dialog__header {
  display: none;
}
.dc-dialog .el-dialog__body {
  padding: 0;
}

.dc-workbench {
  display: flex;
  flex-direction: column;
  height: 86vh;
  max-height: 1080px;
  box-sizing: border-box;
  padding: 16px 20px 10px;
  color: var(--ink, #24344d);
  font-family: var(--font-hand, "Microsoft YaHei UI", "Microsoft YaHei", sans-serif);
}

/* —— 头部 —— */
.dc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(36, 52, 77, 0.08);
}
.dc-title-group {
  min-width: 0;
}
.dc-title {
  max-width: 56vw;
  font-size: 17px;
  font-weight: 800;
  color: #24344d;
  letter-spacing: 0.2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dc-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 5px;
  color: var(--muted, #71839d);
  font-size: 12.5px;
  flex-wrap: wrap;
}
.dc-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.dc-close {
  width: 28px;
  height: 28px;
  border: 1px solid rgba(36, 52, 77, 0.1);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.72);
  color: #5b6f8c;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.dc-close:hover {
  background: rgba(74, 135, 228, 0.12);
  color: #2f5f9e;
}

/* —— 工具栏 —— */
.dc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 0;
}
.dc-toolbar-left,
.dc-toolbar-right {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.dc-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dc-nav .el-button + .el-button {
  margin-left: 0;
}
.dc-diff-label {
  min-width: 128px;
  text-align: center;
  font-size: 12.5px;
  color: var(--muted, #71839d);
  white-space: nowrap;
}

/* —— 内容区 —— */
.dc-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.dc-notice {
  padding: 7px 12px;
  border-radius: 10px;
  background: rgba(63, 162, 107, 0.1);
  color: #2f7a52;
  font-size: 12.5px;
}
.dc-panes {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
}
.dc-pane {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(36, 52, 77, 0.09);
  border-radius: 14px;
  background: #f8fbff;
  overflow: hidden;
}
.dc-pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 12px;
  background: rgba(255, 255, 255, 0.78);
  border-bottom: 1px solid rgba(36, 52, 77, 0.07);
  font-size: 12.5px;
  color: #4a5f7c;
  flex-shrink: 0;
}
.dc-pane-title {
  font-weight: 700;
  color: #24344d;
}
.dc-pane-hint {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--muted, #71839d);
  font-size: 12px;
}
.dc-swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.dc-swatch-orig {
  background: rgba(214, 69, 69, 0.26);
}
.dc-swatch-masked {
  background: rgba(74, 135, 228, 0.28);
}
.dc-pane-masked {
  background: #f7fbff;
}

/* —— 文本区：轻量“代码阅读器”，浅色底、等宽字、行号 —— */
.dc-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(120, 143, 171, 0.45) transparent;
}
.dc-scroll::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}
.dc-scroll::-webkit-scrollbar-thumb {
  background: rgba(120, 143, 171, 0.38);
  border-radius: 6px;
  border: 2px solid transparent;
  background-clip: content-box;
}
.dc-content {
  position: relative;
  min-width: 100%;
}
.dc-line {
  position: absolute;
  left: 0;
  top: 0;
  display: flex;
  align-items: flex-start;
  min-width: 100%;
  width: max-content;
  white-space: pre;
}
.dc-line-wrap {
  position: static;
  display: block;
  width: auto;
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding-right: 18px;
}
.dc-ln {
  position: sticky;
  left: 0;
  z-index: 2;
  flex: 0 0 52px;
  width: 52px;
  box-sizing: border-box;
  padding-right: 10px;
  text-align: right;
  font: 12px / 22px Consolas, "Cascadia Mono", "Courier New", monospace;
  color: #a0b1c6;
  background: #eef4fd;
  user-select: none;
}
.dc-line-wrap .dc-ln {
  position: static;
  display: inline-block;
  vertical-align: top;
}
.dc-text {
  font: 13.5px / 22px Consolas, "Cascadia Mono", "Courier New", monospace;
  color: #24344d;
  letter-spacing: 0.1px;
}
.dc-line-wrap .dc-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* —— 字符级差异高亮：原文浅红 / 脱敏浅蓝，未变化文本保持普通色 —— */
mark.dc-mark {
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 3px;
  font: inherit;
}
mark.dc-mark-orig {
  background: rgba(214, 69, 69, 0.15);
  box-shadow: inset 0 -1px 0 rgba(214, 69, 69, 0.3);
  cursor: pointer;
}
mark.dc-mark-masked {
  background: rgba(74, 135, 228, 0.18);
  box-shadow: inset 0 -1px 0 rgba(74, 135, 228, 0.32);
  cursor: pointer;
}
mark.dc-mark-orig:hover {
  background: rgba(214, 69, 69, 0.26);
}
mark.dc-mark-masked:hover {
  background: rgba(74, 135, 228, 0.3);
}
mark.dc-mark.is-active {
  background: rgba(214, 69, 69, 0.34);
  outline: 1px solid rgba(214, 69, 69, 0.45);
  outline-offset: 1px;
}
.dc-pane-masked mark.dc-mark.is-active {
  background: rgba(74, 135, 228, 0.4);
  outline-color: rgba(74, 135, 228, 0.5);
}

/* —— 加载 / 错误 / 空状态 —— */
.dc-state {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  text-align: center;
}
.dc-state-text {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: #24344d;
}
.dc-state-sub {
  margin: 0;
  max-width: 520px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--muted, #71839d);
}
.dc-state-code {
  margin: 0;
  font: 12px / 1.6 Consolas, "Courier New", monospace;
  color: #9aa8bb;
}
.dc-state-actions {
  display: flex;
  gap: 10px;
  margin-top: 6px;
}
.dc-state-icon {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: rgba(214, 69, 69, 0.1);
  color: #d64545;
  font-weight: 800;
  font-size: 18px;
  line-height: 34px;
}
.dc-spinner {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid rgba(74, 135, 228, 0.18);
  border-top-color: #4a87e4;
  animation: dc-spin 0.8s linear infinite;
}
@keyframes dc-spin {
  to {
    transform: rotate(360deg);
  }
}

/* —— 底部说明 —— */
.dc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 7px;
  border-top: 1px solid rgba(36, 52, 77, 0.06);
  margin-top: 8px;
  font-size: 11.5px;
  color: #9aa8bb;
  flex-wrap: wrap;
}
.dc-foot-right {
  white-space: nowrap;
}

/* —— 字段信息浮层（Teleport 到 body，避免撑开对比区） —— */
.dc-field-card {
  position: fixed;
  z-index: 3000;
  width: 288px;
  padding: 12px 14px 10px;
  box-sizing: border-box;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.97);
  border: 1px solid rgba(36, 52, 77, 0.1);
  box-shadow: 0 18px 40px rgba(20, 40, 70, 0.16);
  color: #24344d;
  font-family: var(--font-hand, "Microsoft YaHei UI", "Microsoft YaHei", sans-serif);
}
.dc-field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.dc-field-title {
  font-size: 14px;
  font-weight: 800;
}
.dc-field-close {
  border: none;
  background: transparent;
  color: #9aa8bb;
  font-size: 18px;
  line-height: 1;
  padding: 0 2px;
  cursor: pointer;
}
.dc-field-close:hover {
  color: #5b6f8c;
}
.dc-field-list {
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: 7px 10px;
  margin: 0;
  font-size: 12.5px;
  line-height: 1.5;
}
.dc-field-list dt {
  margin: 0;
  color: var(--muted, #71839d);
}
.dc-field-list dd {
  margin: 0;
  word-break: break-all;
}
.dc-field-path {
  font-family: Consolas, "Courier New", monospace;
  font-size: 11.5px;
  color: #5b6f8c;
}
.dc-level {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  border-radius: 999px;
  color: #fff;
  font-size: 11.5px;
  font-weight: 700;
  line-height: 1.6;
}
.dc-field-foot {
  margin: 9px 0 0;
  padding-top: 8px;
  border-top: 1px dashed rgba(36, 52, 77, 0.1);
  font-size: 11.5px;
  line-height: 1.5;
  color: #9aa8bb;
}

/* —— 窄屏：单栏优先 —— */
@media (max-width: 980px) {
  .dc-workbench {
    height: 82vh;
    padding: 12px 12px 8px;
  }
  .dc-head {
    flex-direction: column;
    gap: 8px;
  }
  .dc-title {
    max-width: 82vw;
  }
  .dc-head-actions {
    width: 100%;
    justify-content: space-between;
  }
  .dc-toolbar {
    gap: 8px;
    padding: 8px 0;
  }
  .dc-toolbar-left,
  .dc-toolbar-right {
    gap: 8px;
  }
  .dc-pane-hint {
    display: none;
  }
  .dc-panes {
    gap: 8px;
  }
  .dc-diff-label {
    min-width: 104px;
  }
  .dc-foot {
    font-size: 11px;
  }
  .dc-foot-right {
    display: none;
  }
}
</style>
