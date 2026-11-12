<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import * as echarts from "echarts";
import { ElMessage } from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { api, LEVEL_COLORS, LEVEL_LABELS } from "../api";
import { cssVar, watchTheme } from "../theme";
import { TYPE_FILTER_OPTIONS, formatCount, formatShare, shortTypeLabel, typeLabel } from "../statsMeta";

const stats = ref(null);
const pieEl = ref(null);
const barEl = ref(null);
const histEl = ref(null);
const charts = [];
let resizeHandler = null;
let stopWatchTheme = null;
let resizeTimers = [];

/* ---------- 筛选条：统计、KPI、记录抽屉共用同一份筛选条件 ---------- */
// days: 0 表示全部时间；level: "all" 表示全部等级；types: [] 表示全部类别
const LEVEL_ALL = "all";
const filters = reactive({ days: 0, level: LEVEL_ALL, types: [] });
const statsLoading = ref(false);
// 告警汇总单独拉取，失败不影响统计与图表
const alerts = ref(null);
const alertsLoading = ref(false);
const alertError = ref("");
let statsSeq = 0;
let alertsSeq = 0;

const DAY_OPTIONS = [
  { value: 0, label: "全部" },
  { value: 7, label: "近 7 天" },
  { value: 30, label: "近 30 天" },
];
const LEVEL_OPTIONS = [
  { value: LEVEL_ALL, label: "全部" },
  { value: "L3", label: "L3 高危" },
  { value: "L2", label: "L2 中危" },
  { value: "L1", label: "L1 低危" },
  { value: "SAFE", label: "SAFE 安全" },
];

// 筛选 → 请求参数：选「全部」时不下发该参数，交给后端按不筛选处理
function filterParams() {
  const params = {};
  if (filters.days) params.days = filters.days;
  if (filters.level && filters.level !== LEVEL_ALL) params.level = filters.level;
  if (filters.types.length) params.types = filters.types.join(",");
  return params;
}

// 当前生效筛选 chips：点 × 逐个移除，全部清掉时不显示「重置」
const filterChips = computed(() => {
  const chips = [];
  if (filters.days) {
    const option = DAY_OPTIONS.find((item) => item.value === filters.days);
    chips.push({ key: "days", text: option ? option.label : `近 ${filters.days} 天` });
  }
  if (filters.level && filters.level !== LEVEL_ALL) {
    chips.push({ key: "level", text: `等级 ${LEVEL_LABELS[filters.level] || filters.level}` });
  }
  for (const type of filters.types) {
    chips.push({ key: `type:${type}`, text: typeLabel(type) });
  }
  return chips;
});
const hasFilters = computed(() => filterChips.value.length > 0);

function removeChip(chip) {
  if (chip.key === "days") filters.days = 0;
  else if (chip.key === "level") filters.level = LEVEL_ALL;
  else if (chip.key.startsWith("type:")) {
    filters.types = filters.types.filter((type) => type !== chip.key.slice(5));
  }
}

function resetFilters() {
  filters.days = 0;
  filters.level = LEVEL_ALL;
  filters.types = [];
}

// KPI 卡片点击 L3：把风险等级筛选设为 L3；已经是 L3 时直接看记录，避免点了没反应
function applyLevelFilter(level) {
  if (filters.level === level) {
    openRecords({ level });
    return;
  }
  filters.level = level;
}

/* KPI 口径：统计与告警分开算，字段缺失时退化为「—」 */
const kpi = computed(() => {
  const total = Number(stats.value && stats.value.total_findings) || 0;
  const levels = (stats.value && stats.value.by_level) || {};
  const l3 = Number(levels.L3) || 0;
  const status = (alerts.value && alerts.value.by_status) || {};
  const alertTotal = Number(alerts.value && alerts.value.total) || 0;
  const open = Number(status.open) || 0;
  const handled = (Number(status.ack) || 0) + (Number(status.ignored) || 0);
  return {
    total,
    l3,
    l3Share: formatShare(l3, total),
    open,
    alertTotal,
    // 无告警时按需求显示「—」，不显示 0%，避免误解为「处理过但都处理了」
    handledShare: alertTotal ? formatShare(handled, alertTotal) : "—",
  };
});

const kpiTotalText = computed(() =>
  stats.value ? formatCount(kpi.value.total) : statsLoading.value ? "…" : "—"
);
const kpiL3Text = computed(() =>
  stats.value ? formatCount(kpi.value.l3) : statsLoading.value ? "…" : "—"
);
const kpiOpenText = computed(() =>
  alerts.value ? formatCount(kpi.value.open) : alertsLoading.value ? "…" : "—"
);
const kpiHandledText = computed(() =>
  alerts.value ? kpi.value.handledShare : alertsLoading.value ? "…" : "—"
);
// SAFE 计数为 0 时也要说明口径：0 不是没统计，而是没有疑似的低分候选
const safeLevelCount = computed(() => Number(((stats.value && stats.value.by_level) || {}).SAFE) || 0);

const openCardTip = computed(() =>
  alertError.value
    ? `告警汇总加载失败：${alertError.value}`
    : "统计口径：/alerts/summary 的待处理（open）告警数，按当前时间范围统计；告警不含 SAFE 记录，与检测记录不是一对一。"
);
const handledCardTip = computed(() =>
  alertError.value
    ? `告警汇总加载失败：${alertError.value}`
    : "统计口径：(已确认 ack + 已忽略 ignored) ÷ 告警总数；没有告警时显示「—」。"
);

/* ---------- 模块聚焦：双击放大到左半屏，右侧给通俗解读 ---------- */
const focused = ref(null);
const MODULES = {
  level: { title: "分级分布" },
  type: { title: "敏感类别 TopN" },
  score: { title: "敏感度分数直方图" },
};

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

// 双击放大：先取消待执行的单击下钻，两种手势不打架
function onPaneDblclick(key) {
  cancelChartDrill();
  focusModule(key);
}

// 缩略条点击：切换主视角；点当前放大模块本身不处理。
// 注意：普通态下不要在这里取消单击下钻——图表 canvas 的 click 会冒泡到 pane，
// 早先在这里无条件 cancelChartDrill() 会把刚排上的下钻任务清掉，导致点图表没反应。
function onModuleClick(key) {
  if (!focused.value || focused.value === key) return;
  cancelChartDrill();
  focusModule(key);
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
// 饼图外侧标签能放下「名称 数量 · 占比」的最小卡片宽度（实测 1600 宽时容器约 410px）
const PIE_LABEL_WIDE = 372;
let pieLabelWide = true;

function resizeChartsSoon() {
  resizeTimers.forEach((timer) => clearTimeout(timer));
  resizeTimers = [140, 280, 420].map((delay) =>
    setTimeout(() => {
      charts.forEach((chart) => chart.resize());
      syncPieLabelMode();
    }, delay)
  );
}

// 卡片宽度跨过饼图标签断点时按新宽度重绘，避免标签被裁或该显示占比时不显示
function syncPieLabelMode() {
  const wide = pieEl.value ? pieEl.value.clientWidth >= PIE_LABEL_WIDE : true;
  if (wide !== pieLabelWide) render();
}

watch(focused, () => {
  resizeChartsSoon();
  // 缩略图与放大图需要不同的坐标轴/标签配置，聚焦切换时按当前形态重绘
  render();
});

// 面向非专业读者的图表解读：结论在前，再给看图方法
const analysis = computed(() => {
  const data = stats.value;
  if (!data) return ["正在加载统计数据…"];
  if (focused.value === "level") {
    const levels = data.by_level || {};
    const total = Object.values(levels).reduce((sum, count) => sum + (Number(count) || 0), 0);
    const l3 = Number(levels.L3) || 0;
    // 与 KPI 卡口径统一：占比保留 1 位小数，避免同一页出现 30% 与 29.7% 两种说法
    const percent = formatShare(l3, total);
    return [
      total
        ? `当前筛选内共 ${total} 条敏感项，其中 L3 高危 ${l3} 条，占 ${percent}。`
        : "当前筛选下暂时没有识别结果。",
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
      return ["当前筛选下还没有类别统计。", "先跑一批流量或文本检测，这里会按敏感类别汇总命中数量。"];
    }
    const top = rows
      .slice(0, 3)
      .map((row) => `${typeLabel(row.name)}（${row.count} 条）`)
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
    text: cssVar("--ink", "#24344d"),
    muted: cssVar("--muted", "#71839d"),
    grid: "rgba(36, 52, 77, 0.08)",
    surface: "#f7faff",
    copper: cssVar("--copper", "#4a87e4"),
    font: cssVar("--font-hand", "sans-serif"),
  };
}

// 细柱 + 圆头 + 竖向渐变（顶 #a9cbf8 → 底 #aec5e6）
function barGradient() {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: "#a9cbf8" },
    { offset: 1, color: "#aec5e6" },
  ]);
}

function disposeCharts() {
  charts.forEach((chart) => chart.dispose());
  charts.length = 0;
}

/* ---------- 单击图表下钻：与双击放大共用画布，延时 240ms 让双击先取消 ---------- */
let drillTimer = null;

function scheduleChartDrill(action) {
  clearTimeout(drillTimer);
  drillTimer = setTimeout(() => {
    drillTimer = null;
    action();
  }, 240);
}

function cancelChartDrill() {
  if (!drillTimer) return;
  clearTimeout(drillTimer);
  drillTimer = null;
}

// 每次重绘都先解绑再绑定，避免筛选/换主题后 handler 越堆越多
function bindChartClick(chart, handler) {
  chart.off("click");
  chart.on("click", (params) => {
    if (!params || params.componentType !== "series") return;
    handler(params);
  });
}

// 柱状图的类别行，点击时按 dataIndex 反查类别键
let barRows = [];

// 放大态下未被选中的模块会缩成底部缩略条（约 110px 高），
// 缺省坐标轴/图例在这个高度里会互相压字，所以按形态给两套配置。
function isThumb(key) {
  return focused.value !== null && focused.value !== key;
}

function render() {
  const data = stats.value;
  if (!data) return;
  const palette = axisTheme();

  const pie = ensureChart(pieEl.value);
  if (pie) {
    const levels = data.by_level || {};
    const pieThumb = isThumb("level");
    // SAFE 即使为 0 也保留：图例里显示 0，说明「疑似候选」口径
    const levelRows = ["L3", "L2", "L1", "SAFE"].map((level) => ({
      name: level,
      value: Number(levels[level]) || 0,
      itemStyle: { color: LEVEL_COLORS[level] || palette.muted },
    }));
    const levelTotal = levelRows.reduce((sum, row) => sum + row.value, 0);
    // 窄卡片（如 1366 宽的三图并排）放不下占比，只留「名称 数量」，占比交给 tooltip
    const pieWide = pieEl.value ? pieEl.value.clientWidth >= PIE_LABEL_WIDE : true;
    pieLabelWide = pieWide;
    const pieData = levelTotal
      ? levelRows
      : [{ name: "暂无数据", value: 1, itemStyle: { color: palette.grid } }];
    pie.setOption(
      {
        textStyle: { color: palette.text, fontFamily: palette.font },
        tooltip: {
          trigger: "item",
          formatter: (params) => {
            if (!LEVEL_LABELS[params.name]) return "暂无数据";
            return `${LEVEL_LABELS[params.name]}：${params.value} 条 · 占 ${formatShare(params.value, levelTotal)}`;
          },
        },
        legend: {
          show: !pieThumb,
          bottom: 0,
          textStyle: { color: palette.muted },
          formatter: (name) =>
            LEVEL_LABELS[name] ? `${LEVEL_LABELS[name]} ${Number(levels[name]) || 0}` : name,
        },
        series: [
          {
            type: "pie",
            // 卡片只有 ~300px 高，饼图收一点半径，给外侧标签留出横向空间
            radius: pieThumb ? ["42%", "68%"] : ["36%", "60%"],
            center: pieThumb ? ["50%", "50%"] : ["50%", "45%"],
            itemStyle: {
              borderColor: palette.surface,
              borderWidth: 3,
            },
            label: {
              show: !pieThumb,
              color: palette.text,
              fontSize: 11,
              // 短标签避免窄卡片里被截断：宽卡「高危 33 · 29.7%」，窄卡「高危 33」
              formatter: (params) => {
                const name = LEVEL_LABELS[params.name] || params.name;
                const value = Number(params.value) || 0;
                return pieWide ? `${name} ${value} · ${formatShare(value, levelTotal)}` : `${name} ${value}`;
              },
            },
            labelLine: { length: 8, length2: 8 },
            labelLayout: { hideOverlap: true },
            data: pieData,
          },
        ],
      },
      true
    );
    bindChartClick(pie, (params) => {
      // 占位数据「暂无数据」不可下钻
      if (!LEVEL_LABELS[params.name]) return;
      if (!stats.value || !stats.value.total_findings) return;
      scheduleChartDrill(() => openRecords({ level: params.name }));
    });
  }

  const bar = ensureChart(barEl.value);
  if (bar) {
    const barThumb = isThumb("type");
    const typeTotal = Number(data.total_findings) || 0;
    barRows = (data.by_type || []).map((item) => {
      const key = item.type || item.key || item.name || "unknown";
      return {
        type: key,
        label: typeLabel(key),
        short: shortTypeLabel(key),
        count: Number(item.count ?? item.value ?? 0) || 0,
      };
    });
    bar.setOption(
      {
        textStyle: { color: palette.text, fontFamily: palette.font },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(36, 52, 77, 0.04)" } },
          formatter: (items) => {
            const item = Array.isArray(items) ? items[0] : items;
            const row = item ? barRows[item.dataIndex] : null;
            if (!row) return "";
            return `${row.label}<br/>${row.count} 条 · 占 ${formatShare(row.count, typeTotal)}（占筛选内检测记录）`;
          },
        },
        grid: barThumb
          ? { left: 8, right: 8, top: 10, bottom: 10 }
          : { left: 38, right: 16, top: 24, bottom: 36 },
        xAxis: {
          type: "category",
          data: barRows.map((row) => row.short),
          axisLabel: { show: !barThumb, rotate: 0, interval: 0, fontSize: 11, color: palette.muted },
          axisLine: { show: !barThumb, lineStyle: { color: palette.grid } },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          show: !barThumb,
          axisLabel: { color: palette.muted },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: palette.grid } },
        },
        series: [
          {
            type: "bar",
            data: barRows.map((row) => row.count),
            barMaxWidth: barThumb ? 10 : 18,
            showBackground: true,
            backgroundStyle: { color: "rgba(36, 52, 77, 0.05)", borderRadius: [9, 9, 9, 9] },
            itemStyle: {
              borderRadius: [9, 9, 9, 9],
              color: barGradient(),
            },
          },
        ],
      },
      true
    );
    bindChartClick(bar, (params) => {
      const row = barRows[params.dataIndex];
      if (!row || !row.count) return;
      // 等级筛选若已选则叠加，类别按点击的柱子覆盖
      scheduleChartDrill(() => openRecords({ types: [row.type] }));
    });
  }

  const hist = ensureChart(histEl.value);
  if (hist) {
    const histThumb = isThumb("score");
    const entries = Object.entries(data.score_histogram || {});
    hist.setOption(
      {
        textStyle: { color: palette.text, fontFamily: palette.font },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(36, 52, 77, 0.04)" } },
        },
        grid: histThumb
          ? { left: 8, right: 8, top: 10, bottom: 10 }
          : { left: 38, right: 16, top: 24, bottom: 40 },
        xAxis: {
          type: "category",
          data: entries.map(([key]) => key),
          axisLabel: { show: !histThumb, color: palette.muted },
          axisLine: { show: !histThumb, lineStyle: { color: palette.grid } },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          show: !histThumb,
          axisLabel: { color: palette.muted },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: palette.grid } },
        },
        series: [
          {
            type: "bar",
            data: entries.map(([, count]) => count),
            barMaxWidth: histThumb ? 14 : 24,
            showBackground: true,
            backgroundStyle: { color: "rgba(36, 52, 77, 0.05)", borderRadius: [9, 9, 9, 9] },
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

/* ---------- 统计与告警加载：请求序号防止慢响应覆盖新筛选 ---------- */
async function loadStats() {
  const seq = ++statsSeq;
  statsLoading.value = true;
  try {
    const body = await api.stats(filterParams());
    if (seq !== statsSeq) return;
    stats.value = body;
    render();
  } catch (err) {
    if (seq !== statsSeq) return;
    ElMessage.error(`加载统计失败：${err}`);
  } finally {
    if (seq === statsSeq) statsLoading.value = false;
  }
}

async function loadAlerts() {
  const seq = ++alertsSeq;
  alertsLoading.value = true;
  try {
    const body = await api.alertSummary(filters.days || undefined);
    if (seq !== alertsSeq) return;
    alerts.value = body;
    alertError.value = "";
  } catch (err) {
    if (seq !== alertsSeq) return;
    alerts.value = null;
    alertError.value = String((err && err.message) || err);
  } finally {
    if (seq === alertsSeq) alertsLoading.value = false;
  }
}

// 任意筛选变化：统计、KPI（告警 summary 带 days）一起刷新
watch(
  filters,
  () => {
    loadStats();
    loadAlerts();
  },
  { deep: true }
);

function refresh() {
  loadStats();
  loadAlerts();
  if (drawer.visible) loadRecords();
}

/* ---------- 检测记录抽屉：图表下钻与 KPI 卡片共用，服务端分页 ---------- */
const drawer = reactive({
  visible: false,
  level: "",
  types: [],
  page: 1,
  pageSize: 20,
  total: 0,
  items: [],
  loading: false,
  error: "",
});
let recordsSeq = 0;

// override.level / override.types 传了就覆盖全局筛选，不传则沿用筛选条
function openRecords(override = {}) {
  cancelChartDrill();
  // 「全部」在请求里不下发 level 参数
  const nextLevel = override.level !== undefined ? override.level : filters.level;
  drawer.level = nextLevel && nextLevel !== LEVEL_ALL ? nextLevel : "";
  drawer.types = override.types !== undefined ? [...override.types] : [...filters.types];
  drawer.page = 1;
  drawer.visible = true;
  loadRecords();
}

const drawerScopeText = computed(() => {
  const parts = [filters.days ? `近 ${filters.days} 天` : "全部时间"];
  parts.push(drawer.level ? `等级 ${LEVEL_LABELS[drawer.level] || drawer.level}` : "全部等级");
  parts.push(drawer.types.length ? `类别 ${drawer.types.map(typeLabel).join("、")}` : "全部类别");
  return `筛选：${parts.join(" · ")}`;
});

async function loadRecords() {
  const seq = ++recordsSeq;
  drawer.loading = true;
  drawer.error = "";
  try {
    // 位置/原文不在请求里，也不展示；只取脱敏后的 masked
    const params = { page: drawer.page, page_size: drawer.pageSize };
    if (filters.days) params.days = filters.days;
    if (drawer.level) params.level = drawer.level;
    if (drawer.types.length) params.types = drawer.types.join(",");
    const body = await api.findings(params);
    if (seq !== recordsSeq) return;
    drawer.items = Array.isArray(body && body.items) ? body.items : [];
    drawer.total = Number(body && body.total) || 0;
    // 筛选后总数变少时，页码可能越界，回退到最后一页重查
    const maxPage = Math.max(1, Math.ceil(drawer.total / drawer.pageSize));
    if (drawer.page > maxPage) {
      drawer.page = maxPage;
      loadRecords();
    }
  } catch (err) {
    if (seq !== recordsSeq) return;
    drawer.items = [];
    drawer.total = 0;
    drawer.error = String((err && err.message) || err);
    ElMessage.error(`加载检测记录失败：${drawer.error}`);
  } finally {
    if (seq === recordsSeq) drawer.loading = false;
  }
}

function handlePageChange(page) {
  drawer.page = page;
  loadRecords();
}

function handleSizeChange(size) {
  drawer.pageSize = size;
  drawer.page = 1;
  loadRecords();
}

// created_at 是 UTC ISO 串（…Z），这里转成本机时区显示，避免误读成 UTC 时间
function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
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
  loadStats();
  loadAlerts();
  // 窗口缩放：图表重新铺满；跨过饼图标签断点时按新宽度重绘标签
  resizeHandler = () => {
    charts.forEach((chart) => chart.resize());
    syncPieLabelMode();
  };
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
  cancelChartDrill();
  disposeCharts();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      统计看板
      <span class="panel-title-actions">
        <el-button size="small" @click="refresh">刷新</el-button>
        <el-button size="small" type="primary" :loading="adviseLoading" @click="loadAdvise">AI 解读</el-button>
      </span>
    </div>
    <div class="hint stats-hint">
      统计单位：检测记录（findings 片段）；筛选同时作用于统计、KPI 与记录抽屉。单击饼图扇区或柱状图类别可下钻记录，双击图表可放大。
    </div>

    <!-- KPI 条：可点的两张卡都对应真实动作，告警两卡只展示口径 -->
    <div class="kpi-row">
      <el-tooltip
        placement="top"
        content="统计口径：检测记录（findings 片段）总数，来自 /stats 的 total_findings；一条记录可能命中多个类别。点击查看全部检测记录。"
      >
        <button class="kpi-card glass-card-solid is-clickable" type="button" @click="openRecords()">
          <span class="kpi-label">检测记录总量</span>
          <span class="kpi-value">{{ kpiTotalText }}</span>
          <span class="kpi-sub">条检测记录</span>
        </button>
      </el-tooltip>
      <el-tooltip
        placement="top"
        content="统计口径：当前筛选内 L3（高危）检测记录数，以及它占筛选内检测记录总量的比例；L3 通常是身份证、银行卡等强标识数据。点击把风险等级筛选设为 L3；已经是 L3 时再次点击查看 L3 记录。"
      >
        <button class="kpi-card glass-card-solid is-clickable" type="button" @click="applyLevelFilter('L3')">
          <span class="kpi-label">L3 高危命中</span>
          <span class="kpi-value" :style="{ color: LEVEL_COLORS.L3 }">{{ kpiL3Text }}</span>
          <span class="kpi-sub">占筛选内 {{ kpi.l3Share }}</span>
        </button>
      </el-tooltip>
      <el-tooltip placement="top" :content="openCardTip">
        <div class="kpi-card glass-card-solid">
          <span class="kpi-label">待处理风险</span>
          <span class="kpi-value" :class="{ 'is-muted': kpiOpenText === '—' }">{{ kpiOpenText }}</span>
          <span class="kpi-sub">条待处理告警</span>
        </div>
      </el-tooltip>
      <el-tooltip placement="top" :content="handledCardTip">
        <div class="kpi-card glass-card-solid">
          <span class="kpi-label">已处理比例</span>
          <span class="kpi-value" :class="{ 'is-muted': kpiHandledText === '—' }">{{ kpiHandledText }}</span>
          <span class="kpi-sub">已确认 + 已忽略 ÷ 告警总数</span>
        </div>
      </el-tooltip>
    </div>

    <!-- 筛选条：任一变化都会刷新统计、KPI 与后续打开的记录抽屉 -->
    <div class="stats-filters glass-card-solid">
      <div class="filter-line">
        <div class="filter-item">
          <span class="filter-label">时间范围</span>
          <span class="seg">
            <button
              v-for="option in DAY_OPTIONS"
              :key="option.value"
              type="button"
              class="seg-btn"
              :class="{ active: filters.days === option.value }"
              @click="filters.days = option.value"
            >
              {{ option.label }}
            </button>
          </span>
        </div>
        <div class="filter-item">
          <span class="filter-label">风险等级</span>
          <!-- 不选任何等级即「全部」；clearable 提供一键清空，避免下拉里出现空的「全部」项 -->
          <el-select
            v-model="filters.level"
            size="small"
            clearable
            placeholder="全部"
            style="width: 128px"
          >
            <el-option
              v-for="option in LEVEL_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">敏感类型</span>
          <el-select
            v-model="filters.types"
            multiple
            collapse-tags
            collapse-tags-tooltip
            size="small"
            placeholder="全部类别"
            style="width: 230px"
          >
            <el-option
              v-for="option in TYPE_FILTER_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
        <div class="filter-chips">
          <span v-if="!hasFilters" class="filter-none">当前筛选：全部</span>
          <template v-else>
            <span class="filter-label">当前筛选</span>
            <button
              v-for="chip in filterChips"
              :key="chip.key"
              class="chip pale filter-chip"
              type="button"
              :title="`点击移除：${chip.text}`"
              @click="removeChip(chip)"
            >
              {{ chip.text }}<span class="filter-chip-x">×</span>
            </button>
            <el-button size="small" link @click="resetFilters">重置</el-button>
          </template>
        </div>
      </div>
    </div>

    <div class="chart-row" :class="{ focused: focused !== null }" style="margin-top: 14px">
      <div
        class="pane glass-card-solid stats-module"
        :class="{ 'is-focus': focused === 'level', 'is-thumb': focused !== null && focused !== 'level' }"
        @dblclick="onPaneDblclick('level')"
        @click="onModuleClick('level')"
      >
        <div class="pane-head">
          <span>分级分布（饼图）</span>
          <span class="pane-head-side">
            <el-tooltip
              v-if="stats && safeLevelCount === 0 && filters.level === LEVEL_ALL"
              placement="top"
              content="SAFE 计数为 0 也保留在图例里。SAFE 指 score<0.25 的疑似候选；完全未命中的片段不落库，所以不会出现在统计里。"
            >
              <span class="pane-note">SAFE 0 条</span>
            </el-tooltip>
            <button
              v-if="focused === 'level'"
              class="pane-close"
              type="button"
              title="收起"
              @click.stop="closeFocus"
            >×</button>
          </span>
        </div>
        <div ref="pieEl" class="chart"></div>
      </div>
      <div
        class="pane glass-card-solid stats-module"
        :class="{ 'is-focus': focused === 'type', 'is-thumb': focused !== null && focused !== 'type' }"
        @dblclick="onPaneDblclick('type')"
        @click="onModuleClick('type')"
      >
        <div class="pane-head">
          <span>敏感类别 TopN（柱状图）</span>
          <span class="pane-head-side">
            <button
              v-if="focused === 'type'"
              class="pane-close"
              type="button"
              title="收起"
              @click.stop="closeFocus"
            >×</button>
          </span>
        </div>
        <div ref="barEl" class="chart"></div>
      </div>
      <div
        class="pane glass-card-solid stats-module"
        :class="{ 'is-focus': focused === 'score', 'is-thumb': focused !== null && focused !== 'score' }"
        @dblclick="onPaneDblclick('score')"
        @click="onModuleClick('score')"
      >
        <div class="pane-head">
          <span>敏感度分数直方图</span>
          <span class="pane-head-side">
            <button
              v-if="focused === 'score'"
              class="pane-close"
              type="button"
              title="收起"
              @click.stop="closeFocus"
            >×</button>
          </span>
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

    <!-- 检测记录抽屉：只展示 masked 脱敏预览，不展示任何原文。
         drawer/overlay 会被 teleport 到 body，scoped 样式命中不了，所以用专属类名 + 文末的非 scoped 样式块 -->
    <el-drawer
      v-model="drawer.visible"
      class="records-drawer"
      modal-class="records-drawer-modal"
      title="检测记录"
      size="min(820px, 94vw)"
    >
      <!-- 旧版外壳没启用 Element Plus 中文语言包，分页会显示 “Total / 20/page”；
           这里只在抽屉内用 el-config-provider 局部切中文，不影响其它页面 -->
      <el-config-provider :locale="zhCn">
        <div class="records-body">
          <div class="records-meta">
            <span class="records-scope" :title="drawerScopeText">{{ drawerScopeText }}</span>
            <span>共 {{ formatCount(drawer.total) }} 条</span>
          </div>
          <div v-if="drawer.error" class="records-error">
            <span>加载失败：{{ drawer.error }}</span>
            <el-button size="small" @click="loadRecords">重试</el-button>
          </div>
          <div v-loading="drawer.loading" class="records-table-wrap">
            <el-table
              :data="drawer.items"
              size="small"
              height="100%"
              empty-text="暂无符合条件的检测记录"
              style="width: 100%"
            >
              <el-table-column label="等级" width="86">
                <template #default="{ row }">
                  <span class="lvl-dot" :style="{ background: LEVEL_COLORS[row.level] || '#8a9bb3' }"></span>
                  <span :style="{ color: LEVEL_COLORS[row.level] || 'inherit', fontWeight: 600 }">
                    {{ LEVEL_LABELS[row.level] || row.level || "—" }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="敏感类型" min-width="130">
                <template #default="{ row }">
                  <span v-if="row.types && row.types.length">{{ row.types.map(typeLabel).join("、") }}</span>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column prop="path" label="位置" min-width="110" show-overflow-tooltip />
              <el-table-column label="任务" width="150">
                <template #default="{ row }">
                  <span class="task-cell">{{ row.task_id ? `#${row.task_id}` : "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="检测时间" width="152">
                <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="脱敏预览" min-width="150" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="masked-cell">{{ row.masked || "—" }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div class="records-foot">
            <el-pagination
              :current-page="drawer.page"
              :page-size="drawer.pageSize"
              :page-sizes="[10, 20, 50]"
              :total="drawer.total"
              background
              layout="total, sizes, prev, pager, next"
              @current-change="handlePageChange"
              @size-change="handleSizeChange"
            />
          </div>
        </div>
      </el-config-provider>
    </el-drawer>
  </div>
</template>

<style scoped>
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pane-head-side {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pane-note {
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
  white-space: nowrap;
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
  color: #5a6c85;
  font-size: 17px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.pane-close:hover {
  background: rgba(74, 135, 228, 0.14);
  color: var(--copper);
}

/* 口径说明：跟在标题下方，不挤压标题行的按钮 */
.stats-hint {
  margin: -6px 0 12px;
  line-height: 1.6;
}

/* ---------- KPI 条 ---------- */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.kpi-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 12px 16px;
  border-radius: 16px;
  text-align: left;
  font: inherit;
  color: var(--ink);
}
button.kpi-card {
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
button.kpi-card:hover {
  border-color: var(--copper-line);
  background: #fff;
}
button.kpi-card:active {
  background: #f2f7ff;
}
.kpi-label {
  font-size: 13px;
  color: var(--muted);
}
.kpi-value {
  font-size: 24px;
  font-weight: 800;
  line-height: 1.25;
  letter-spacing: 0.5px;
}
.kpi-value.is-muted {
  color: var(--muted);
}
.kpi-sub {
  font-size: 12px;
  color: var(--muted);
}

/* ---------- 筛选条 ---------- */
.stats-filters {
  padding: 10px 14px;
  border-radius: 16px;
}
.filter-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 18px;
}
.filter-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
  white-space: nowrap;
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 10px;
  background: var(--copper-soft);
}
.seg-btn {
  padding: 4px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #4a5f7c;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.seg-btn:hover {
  color: var(--copper);
}
.seg-btn.active {
  background: #fff;
  color: var(--copper);
  box-shadow: 0 2px 6px rgba(74, 135, 228, 0.16);
}
.filter-chips {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-left: auto;
}
.filter-none {
  font-size: 12px;
  color: var(--muted);
}
.filter-chips .filter-chip {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 10px;
  cursor: pointer;
}
.filter-chips .filter-chip:hover {
  border-color: var(--copper-line);
  color: var(--copper);
}
.filter-chip-x {
  margin-left: 4px;
  font-size: 13px;
  line-height: 1;
}

/* ---------- 检测记录抽屉 ---------- */
/* drawer body 有确定高度，这里用 flex 把剩余高度全给表格：
   表格内部滚动、分页固定在最底部，避免表格溢出压到分页上 */
.records-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.records-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex: none;
  font-size: 12px;
  color: var(--muted);
}
.records-scope {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.records-table-wrap {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.records-error {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(214, 69, 69, 0.08);
  color: #b43c3c;
  font-size: 13px;
}
.records-error .el-button {
  margin-left: auto;
}
.records-foot {
  display: flex;
  justify-content: flex-end;
  flex: none;
  padding-top: 4px;
}
.lvl-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
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
  border-color: rgba(74, 135, 228, 0.35);
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
  gap: 10px;
  padding: 20px 22px;
  overflow: auto;
}
.stats-analysis-head {
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(36, 52, 77, 0.08);
  font-size: 20px;
  font-weight: 800;
  color: var(--ink);
}
.stats-analysis p {
  margin: 0;
  font-size: 15px;
  line-height: 1.75;
  color: #4a5f7c;
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
  border-bottom: 1px solid rgba(36, 52, 77, 0.08);
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
  background: rgba(36, 52, 77, 0.06);
  color: var(--muted);
}
.advise-source.sev-warn {
  background: rgba(74, 135, 228, 0.14);
  color: var(--copper);
}
.advise-source.sev-high {
  background: rgba(214, 69, 69, 0.12);
  color: #d64545;
}
.advise-summary {
  margin: 0;
  font-size: 15px;
  line-height: 1.7;
  color: #4a5f7c;
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
  background: rgba(36, 52, 77, 0.03);
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
  color: #4a5f7c;
}
.advise-foot {
  display: flex;
  justify-content: flex-end;
  gap: 14px;
  font-size: 12px;
  color: var(--muted);
}

/* 窄屏：KPI 两列，筛选条自然换行 */
@media (max-width: 1080px) {
  .kpi-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
/* 更窄（旧版外壳在小屏会把内容区压得很窄）：KPI 单列，放大态退回上下两段 */
@media (max-width: 720px) {
  .kpi-row {
    grid-template-columns: minmax(0, 1fr);
  }
  .filter-chips {
    margin-left: 0;
  }
  .chart-row.focused {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) 96px auto;
    height: auto;
  }
  .chart-row.focused .is-focus {
    min-height: 300px;
  }
  .stats-analysis {
    grid-column: 1;
    grid-row: 3;
  }
}
</style>

<style>
/* el-drawer / el-overlay 会被 teleport 到 body，scoped 选择器命中不到，
   所以这里用 records-drawer / records-drawer-modal 专属类名限定作用范围。
   全局 --el-bg-color 是 50% 透明玻璃，抽屉里放数据表格会透出下面的页面，这里补成接近不透明。 */
.records-drawer {
  background: rgba(255, 255, 255, 0.94);
  -webkit-backdrop-filter: blur(28px) saturate(140%);
  backdrop-filter: blur(28px) saturate(140%);
  border-left: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: -24px 0 60px rgba(36, 52, 77, 0.12);
}
.records-drawer .el-drawer__header {
  margin-bottom: 10px;
  padding: 16px 20px 10px;
  border-bottom: 1px solid rgba(36, 52, 77, 0.08);
}
.records-drawer .el-drawer__title {
  font-family: var(--font-hand);
  font-weight: 700;
  color: #24344d;
}
.records-drawer .el-drawer__body {
  padding: 0 20px 14px;
}
.records-drawer .el-table th.el-table__cell {
  border-bottom: 1px solid rgba(36, 52, 77, 0.1);
}
.records-drawer .el-table td.el-table__cell {
  border-bottom: 1px solid rgba(36, 52, 77, 0.05);
}
.records-drawer .task-cell {
  font-family: var(--font-num);
  font-size: 12px;
}
.records-drawer-modal {
  background: rgba(36, 52, 77, 0.18);
}
</style>
