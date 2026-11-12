<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
const props = defineProps({ lines: Array, side: String, selected: Number, wrap: Boolean });
const emit = defineEmits(["position", "select"]);
const viewport = ref(null), top = ref(0), height = ref(500), heights = ref({});
let observer, frame = 0, release = 0, programmatic = false;
const offsets = computed(() => {
  const values = [0];
  props.lines.forEach((_, i) => values.push(values[i] + (heights.value[i] || 26)));
  return values;
});
function indexAt(y) {
  let lo = 0, hi = props.lines.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (offsets.value[mid + 1] <= y) lo = mid + 1; else hi = mid; }
  return Math.min(lo, Math.max(0, props.lines.length - 1));
}
const visible = computed(() => {
  const start = Math.max(0, indexAt(top.value) - 6), end = Math.min(props.lines.length, indexAt(top.value + height.value) + 8);
  return props.lines.slice(start, end).map((line, i) => ({ line, index: start + i }));
});
function measure() {
  if (!viewport.value) return;
  height.value = viewport.value.clientHeight;
  const updates = { ...heights.value };
  let changed = false;
  viewport.value.querySelectorAll("[data-line]").forEach(node => {
    const size = node.getBoundingClientRect().height, key = node.dataset.line;
    if (updates[key] !== size) { updates[key] = size; changed = true; }
  });
  if (changed) heights.value = updates;
}
function scroll() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    top.value = viewport.value.scrollTop;
    if (!programmatic) {
      const index = indexAt(top.value);
      emit("position", index + (top.value - offsets.value[index]) / (heights.value[index] || 26));
    }
  });
}
async function locate(value) {
  programmatic = true;
  const index = Math.min(Math.floor(value), props.lines.length - 1);
  top.value = Math.max(0, offsets.value[index] + (value - index) * (heights.value[index] || 26));
  await nextTick();
  if (viewport.value) viewport.value.scrollTop = top.value;
  cancelAnimationFrame(release);
  release = requestAnimationFrame(() => { release = requestAnimationFrame(() => { programmatic = false; }); });
}
watch(() => props.wrap, () => { heights.value = {}; nextTick(measure); });
watch(visible, () => nextTick(measure));
onMounted(() => { observer = new ResizeObserver(() => { heights.value = {}; nextTick(measure); }); observer.observe(viewport.value); measure(); });
onBeforeUnmount(() => { observer?.disconnect(); cancelAnimationFrame(frame); cancelAnimationFrame(release); });
defineExpose({ locate });
</script>
<template>
  <section class="document-pane" :class="side">
    <div class="pane-heading">{{ side === 'original' ? '原始文档' : '脱敏后的文档' }}</div>
    <div ref="viewport" class="document-scroll" tabindex="0" :aria-label="side === 'original' ? '原始文档内容' : '脱敏文档内容'" @scroll="scroll">
      <div class="line-space" :style="{ height: offsets.at(-1) + 'px' }">
        <div v-for="{ line, index } in visible" :key="index" :data-line="index" class="document-line" :class="{ wrap }" :style="{ top: offsets[index] + 'px' }">
          <span class="line-number" aria-hidden="true">{{ index + 1 }}</span><span class="line-text"><template v-for="(part, i) in line.parts" :key="i"><button v-if="part.id !== undefined" class="change" :class="{ selected: selected === part.id }" :aria-label="'查看差异 ' + (part.id + 1)" @click="emit('select', part.id)">{{ part.text || '∅' }}</button><span v-else>{{ part.text }}</span></template></span>
        </div>
      </div>
    </div>
  </section>
</template>
<style scoped>
.document-pane { min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; background: var(--panel); }
.pane-heading { padding: 10px 16px; background: var(--pane-head); border-bottom: 1px solid var(--border); font-weight: 600; }
.document-scroll { flex: 1; overflow: auto; position: relative; }
.line-space { position: relative; min-width: 100%; }
.document-line { position: absolute; min-width: 100%; display: flex; align-items: flex-start; font: 13px/26px var(--font-mono); white-space: pre; }
.line-number { position: sticky; left: 0; flex: 0 0 55px; text-align: right; padding-right: 12px; color: var(--muted); background: var(--panel); user-select: none; }
.line-text { padding-right: 16px; }
.wrap { width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
.wrap .line-text { min-width: 0; flex: 1; }
.change { display: inline; border: 0; border-radius: 3px; padding: 0; font: inherit; color: inherit; white-space: inherit; overflow-wrap: inherit; cursor: pointer; background: var(--danger-soft); }
.masked .change { background: var(--accent-soft); }
.change.selected, .change:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
</style>
