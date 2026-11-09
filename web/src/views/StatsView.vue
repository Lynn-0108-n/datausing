<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS } from "../api";
import { cssVar, watchTheme } from "../theme";

const stats = ref(null);
const pieEl = ref(null);
const barEl = ref(null);
const histEl = ref(null);
const charts = [];
let resizeHandler = null;
let stopWatchTheme = null;

/* ---------- 模块聚焦：双击放大到左半屏，右侧给通俗解读 ---------- */
const focused = ref(null);
const MODULES = {
  level: { title: "分级分布" },
  type: { title: "敏感类别 TopN" },
  score: { title: "敏感度分数直方图" },
};
let resizeTimers = [];

// 找到某个模块对应的卡片元素（图表容器的父级 .pane）
function paneFor(key) {
  const el = key === "level" ? pieEl.value : key === "type" ? barEl.value : histEl.value;
  return el ? el.closest(".pane") : null;
}

// FLIP：从切换前的位置/尺寸平滑过渡到切换后的位置/尺寸，关闭时沿原路收回
function flipPane(pane, before) {
  if (!pane || !before) return;
  const after = pane.getBoundingClientRect();
  if (!after.width || !after.height) return;
  const dx = before.left - after.left;
  const dy = before.top - after.top;
  const sx = before.width / after.width;
  const sy = before.height / after.height;
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return;
  pane.style.transition = "none";
  pane.style.transformOrigin = "top left";
  pane.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
  requestAnimationFrame(() => {
    pane.style.transition = "transform 0.38s cubic-bezier(0.22, 1, 0.36, 1)";
    pane.style.transform = "";
    window.setTimeout(() => {
      pane.style.transition = "";
      pane.style.transformOrigin = "";
    }, 420);
  });
}

const MODULE_KEYS = ["level", "type", "score"];

function captureRects() {
  const rects = {};
  for (const key of MODULE_KEYS) {
    const pane = paneFor(key);
    rects[key] = pane ? pane.getBoundingClientRect() : null;
  }
  return rects;
}

function flipAll(rects) {
  for (const key of MODULE_KEYS) flipPane(paneFor(key), rects[key]);
}

function focusModule(key) {
  if (focused.value === key) return;
  const rects = captureRects();
  focused.value = key;
  nextTick(() => {
    flipAll(rects);
    resizeChartsSoon();
  });
}

// 缩略条点击：切换主视角；点当前放大模块本身不处理
function onModuleClick(key) {
  if (focused.value && focused.value !== key) focusModule(key);
}

function closeFocus() {
  if (!focused.value) return;
  const rects = captureRects();
  focused.value = null;
  nextTick(() => {
    flipAll(rects);
    resizeChartsSoon();
  });
}

// 布局切换时图表容器尺寸变化，动画期间多次 resize 保证画布铺满
function resizeChartsSoon() {
  resizeTimers.forEach((timer) => clearTimeout(timer));
  resizeTimers = [140, 280, 420].map((delay) =>
    setTimeout(() => charts.forEach((chart) => chart.resize()), delay)
  );
}

watch(focused, resizeChartsSoon);

// 面向非专业读者的图表解读：结论在前，再给看图方法
const analysis = computed(() => {
  const data = stats.value;
  if (!data) return ["正在加载统计数据…"];
  if (focused.value === "level") {
    const levels = data.by_level || {};
    const total = Object.values(levels).reduce((sum, count) => sum + (Number(count) || 0), 0);
    const l3 = Number(levels.L3) || 0;
    const percent = total ? Math.round((l3 / total) * 100) : 0;
    return [
      total
        ? `当前共识别出 ${total} 条敏感项，其中 L3 高危 ${l3} 条，占 ${percent}%。`
        : "暂时还没有识别结果。",
      "L3 占比越高，说明流量里的个人信息越敏感，越需要优先处理；L3 通常对应身份证、银行卡这类强标识数据。",
      "L1 是规则直接命中，L2、L3 是语义与大模型复核后升级的结果，级别越高脱敏越彻底。",
      "看图要点：先看 L3、L2 的比例，再看 SAFE 是否偏多——SAFE 偏多通常说明规则覆盖偏松。",
    ];
  }
  if (focused.value === "type") {
    const rows = (data.by_type || [])
      .map((item) => ({ name: item.key || item.type || item.name, count: item.count ?? item.value ?? 0 }))
      .sort((a, b) => b.count - a.count);
    if (!rows.length) {
      return ["还没有类别统计。", "先跑一批流量或文本检测，这里会按敏感类别汇总命中数量。"];
    }
    const top = rows
      .slice(0, 3)
      .map((row) => `${row.name}（${row.count} 条）`)
      .join("、");
    return [
      `命中最多的是 ${top}。`,
      "柱子越高，说明这类敏感信息出现越频繁，应该优先检查对应规则和脱敏策略。",
      "如果某个类别长期为空，可能是规则没覆盖到，也可能这批流量确实不含这类数据。",
    ];
  }
  if (focused.value === "score") {
    const entries = Object.entries(data.score_histogram || {});
    const total = entries.reduce((sum, [, count]) => sum + (Number(count) || 0), 0);
    const peak = entries.reduce((best, item) => (item[1] > best[1] ? item : best), entries[0] || ["", 0]);
    const lines = [`当前共 ${total} 条命中，分数分布见左图。`];
    if (peak[0]) lines.push(`最集中的分数段是 ${peak[0]}，共 ${peak[1]} 条。`);
    lines.push("分数越高越敏感：高分集中在右侧，说明这批数据整体风险偏高；集中在左侧，说明大多是低风险命中。");
    lines.push("分数只用于辅助判断，最终脱敏级别以识别规则和策略表为准。");
    return lines;
  }
  return [];
});

function ensureChart(el) {
  if (!el) return null;
  let chart = echarts.getInstanceByDom(el);
  if (!chart) {
    chart = echarts.init(el);
    charts.push(chart);
  }
  return chart;
}

// ECharts 是 canvas 渲染，颜色取自当前主题变量；切换主题时重建图表
function axisTheme() {
  return {
    text: cssVar("--ink", "#2f2a26"),
    muted: cssVar("--muted", "#7b6f67"),
    grid: "rgba(47, 42, 38, 0.08)",
    surface: "#f3f1f1",
    copper: cssVar("--copper", "#b66d44"),
    font: cssVar("--font-hand", "sans-serif"),
  };
}

// 细柱 + 圆头 + 竖向渐变（顶 #E7A471 → 底 #C5A0AB）
function barGradient() {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: "#e7a471" },
    { offset: 1, color: "#c5a0ab" },
  ]);
}

function disposeCharts() {
  charts.forEach((chart) => chart.dispose());
  charts.length = 0;
}

function render() {
  const data = stats.value;
  if (!data) return;
  const palette = axisTheme();

  const pie = ensureChart(pieEl.value);
  if (pie) {
    const rows = Object.entries(data.by_level || {})
      .filter(([, count]) => count > 0)
      .map(([level, count]) => ({
        name: `${level}`,
        value: count,
        itemStyle: { color: LEVEL_COLORS[level] || palette.muted },
      }));
    pie.setOption(
      {
        textStyle: { color: palette.text, fontFamily: palette.font },
        tooltip: { trigger: "item" },
        legend: { bottom: 0, textStyle: { color: palette.muted } },
        series: [
          {
            type: "pie",
            radius: ["40%", "66%"],
            center: ["50%", "44%"],
            itemStyle: {
              borderColor: palette.surface,
              borderWidth: 3,
            },
            label: { formatter: "{b}: {c} ({d}%)", color: palette.text },
            data: rows.length ? rows : [{ name: "暂无数据", value: 1, itemStyle: { color: palette.grid } }],
          },
        ],
      },
      true
    );
  }

  const bar = ensureChart(barEl.value);
  if (bar) {
    const rows = (data.by_type || []).map((item) => item.key || item.type || item.name);
    const counts = (data.by_type || []).map((item) => item.count ?? item.value ?? 0);
    bar.setOption(
      {
        textStyle: { color: palette.text, fontFamily: palette.font },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(47, 42, 38, 0.04)" } },
        },
        grid: { left: 38, right: 16, top: 24, bottom: 48 },
        xAxis: {
          type: "category",
          data: rows,
          axisLabel: { rotate: 30, color: palette.muted },
          axisLine: { lineStyle: { color: palette.grid } },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: palette.muted },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: palette.grid } },
        },
        series: [
          {
            type: "bar",
            data: counts,
            barMaxWidth: 18,
            showBackground: true,
            backgroundStyle: { color: "rgba(47, 42, 38, 0.05)", borderRadius: [9, 9, 9, 9] },
            itemStyle: {
              borderRadius: [9, 9, 9, 9],
              color: barGradient(),
            },
          },
        ],
      },
      true
    );
  }

  const hist = ensureChart(histEl.value);
  if (hist) {
    const entries = Object.entries(data.score_histogram || {});
    hist.setOption(
      {
        textStyle: { color: palette.text, fontFamily: palette.font },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(47, 42, 38, 0.04)" } },
        },
        grid: { left: 38, right: 16, top: 24, bottom: 40 },
        xAxis: {
          type: "category",
          data: entries.map(([key]) => key),
          axisLabel: { color: palette.muted },
          axisLine: { lineStyle: { color: palette.grid } },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: palette.muted },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: palette.grid } },
        },
        series: [
          {
            type: "bar",
            data: entries.map(([, count]) => count),
            barMaxWidth: 24,
            showBackground: true,
            backgroundStyle: { color: "rgba(47, 42, 38, 0.05)", borderRadius: [9, 9, 9, 9] },
            itemStyle: {
              borderRadius: [9, 9, 9, 9],
              color: barGradient(),
            },
          },
        ],
      },
      true
    );
  }
}

async function load() {
  try {
    stats.value = await api.stats();
    render();
  } catch (err) {
    ElMessage.error(`加载统计失败：${err}`);
  }
}

// ---------- Day 3：AI 解读 ----------
const adviseResult = ref(null);
const adviseLoading = ref(false);

async function loadAdvise() {
  if (!stats.value) {
    ElMessage.warning("统计未加载");
    return;
  }
  adviseLoading.value = true;
  adviseResult.value = null;
  try {
    // 只发聚合计数，不发任何原文 / masked / path
    const byType = (stats.value.by_type || []).slice(0, 10).map((item) => ({
      type: item.type || item.key || "unknown",
      key: item.key || "",
      count: item.count ?? item.value ?? 0,
    }));
    adviseResult.value = await api.assistantAdvise({
      page: "stats",
      scope: stats.value.task_id ? "task" : "all",
      total: stats.value.total_findings || 0,
      by_level: stats.value.by_level || {},
      types: byType,
    });
  } catch (err) {
    ElMessage.error(`AI 解读失败：${err}`);
  } finally {
    adviseLoading.value = false;
  }
}

function adviseSourceLabel(source, degraded) {
  if (source === "local-model") return "由本机模型生成";
  if (degraded) return "模型不可用，已回退规则引擎";
  return "规则引擎";
}

onMounted(() => {
  load();
  resizeHandler = () => charts.forEach((chart) => chart.resize());
  window.addEventListener("resize", resizeHandler);
  // 换主题时旧 canvas 颜色不会自动更新，干脆重建
  stopWatchTheme = watchTheme(() => {
    disposeCharts();
    render();
  });
});

onBeforeUnmount(() => {
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  if (stopWatchTheme) stopWatchTheme();
  resizeTimers.forEach((timer) => clearTimeout(timer));
  disposeCharts();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      统计看板
      <span class="panel-title-actions">
        <el-button size="small" @click="load">刷新</el-button>
        <el-button size="small" type="primary" :loading="adviseLoading" @click="loadAdvise">AI 解读</el-button>
      </span>
    </div>
    <div class="chart-row" :class="{ focused: focused !== null }" style="margin-top: 14px">
      <div
        class="pane glass-card-solid stats-module"
        :class="{ 'is-focus': focused === 'level', 'is-thumb': focused !== null && focused !== 'level' }"
        @dblclick="focusModule('level')"
        @click="onModuleClick('level')"
      >
        <div class="pane-head">
          <span>分级分布（饼图）</span>
          <button
            v-if="focused === 'level'"
            class="pane-close"
            type="button"
            title="收起"
            @click.stop="closeFocus"
          >×</button>
        </div>
        <div ref="pieEl" class="chart"></div>
      </div>
      <div
        class="pane glass-card-solid stats-module"
        :class="{ 'is-focus': focused === 'type', 'is-thumb': focused !== null && focused !== 'type' }"
        @dblclick="focusModule('type')"
        @click="onModuleClick('type')"
      >
        <div class="pane-head">
          <span>敏感类别 TopN（柱状图）</span>
          <button
            v-if="focused === 'type'"
            class="pane-close"
            type="button"
            title="收起"
            @click.stop="closeFocus"
          >×</button>
        </div>
        <div ref="barEl" class="chart"></div>
      </div>
      <div
        class="pane glass-card-solid stats-module"
        :class="{ 'is-focus': focused === 'score', 'is-thumb': focused !== null && focused !== 'score' }"
        @dblclick="focusModule('score')"
        @click="onModuleClick('score')"
      >
        <div class="pane-head">
          <span>敏感度分数直方图</span>
          <button
            v-if="focused === 'score'"
            class="pane-close"
            type="button"
            title="收起"
            @click.stop="closeFocus"
          >×</button>
        </div>
        <div ref="histEl" class="chart"></div>
      </div>
      <div v-if="focused" class="stats-analysis glass-card-solid">
        <div class="stats-analysis-head">{{ MODULES[focused].title }}</div>
        <p v-for="(line, index) in analysis" :key="index">{{ line }}</p>
      </div>
    </div>

    <!-- Day 3.6：AI 解读结果 -->
    <div v-if="adviseResult" class="advise-panel glass-card-solid">
      <div class="advise-head">
        <span class="advise-title">AI 解读</span>
        <span class="advise-source" :class="`sev-${adviseResult.severity}`">
          {{ adviseSourceLabel(adviseResult.source, adviseResult.degraded) }}
        </span>
      </div>
      <p class="advise-summary">{{ adviseResult.summary }}</p>
      <ul v-if="adviseResult.tips && adviseResult.tips.length" class="advise-tips">
        <li v-for="(tip, idx) in adviseResult.tips" :key="idx">
          <div class="tip-title">{{ tip.title }}</div>
          <div class="tip-detail">{{ tip.detail }}</div>
        </li>
      </ul>
      <div class="advise-foot">
        <span v-if="adviseResult.model">{{ adviseResult.model }}</span>
        <span>{{ adviseResult.generated_at }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pane-close {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.7);
  color: #6b5f57;
  font-size: 17px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.pane-close:hover {
  background: rgba(182, 109, 68, 0.14);
  color: var(--copper);
}

/* 放大：左侧半屏主图 + 右侧解读，其余模块缩到底部缩略条
   位置/尺寸变化交给 FLIP 的 transform 动画，这里不加 grid 过渡，避免测到中间态 */
.chart-row.focused {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: minmax(0, 1fr) 118px;
  height: 470px;
}
.chart-row.focused .is-focus {
  grid-column: 1;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.chart-row.focused .is-focus .chart {
  flex: 1;
  height: auto;
  min-height: 0;
}
.chart-row.focused .is-thumb {
  grid-row: 2;
  display: flex;
  flex-direction: column;
  min-height: 0;
  cursor: pointer;
  transition: background 0.18s ease, border-color 0.18s ease;
}
.chart-row.focused .is-thumb:hover {
  border-color: rgba(182, 109, 68, 0.35);
  background: rgba(255, 255, 255, 0.75);
}
.chart-row.focused .is-thumb .pane-head {
  padding: 6px 12px;
  font-size: 12px;
  border-bottom: none;
}
.chart-row.focused .is-thumb .chart {
  flex: 1;
  height: auto;
  min-height: 0;
}

.stats-analysis {
  grid-column: 2;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 22px 24px;
  overflow: auto;
}
.stats-analysis-head {
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(47, 42, 38, 0.08);
  font-size: 22px;
  font-weight: 800;
  color: var(--ink);
}
.stats-analysis p {
  margin: 0;
  font-size: 19px;
  line-height: 1.8;
  color: #4a423c;
}

/* Day 3.6：AI 解读结果面板 */
.advise-panel {
  margin-top: 14px;
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.advise-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(47, 42, 38, 0.08);
}
.advise-title {
  font-size: 16px;
  font-weight: 800;
  color: var(--ink);
}
.advise-source {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 10px;
  background: rgba(47, 42, 38, 0.06);
  color: var(--muted);
}
.advise-source.sev-warn {
  background: rgba(182, 109, 68, 0.14);
  color: var(--copper);
}
.advise-source.sev-high {
  background: rgba(192, 57, 43, 0.12);
  color: #c0392b;
}
.advise-summary {
  margin: 0;
  font-size: 15px;
  line-height: 1.7;
  color: #4a423c;
}
.advise-tips {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.advise-tips li {
  padding: 10px 14px;
  background: rgba(47, 42, 38, 0.03);
  border-left: 3px solid var(--copper);
  border-radius: 4px;
}
.tip-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 4px;
}
.tip-detail {
  font-size: 13px;
  line-height: 1.6;
  color: #5a524c;
}
.advise-foot {
  display: flex;
  justify-content: flex-end;
  gap: 14px;
  font-size: 12px;
  color: var(--muted);
}
</style>
