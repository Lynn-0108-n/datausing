<script setup>
import { safeError } from "../workspace";
import PageHeader from "../components/PageHeader.vue";
import { onBeforeUnmount, onMounted, ref } from "vue";
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
let resizeObserver;

// AI 风险研判：只发聚合数字（各级别/类别命中数、任务数），不发原文
const adviseLoading = ref(false);
const adviseError = ref("");
const adviseResult = ref(null);
const SOURCE_LABELS = {
  rules: "本地规则",
  local_llm: "本机模型",
  degraded: "模型不可用已回退",
};
const adviseSource = ref("");

async function runAdvise() {
  if (!stats.value) {
    adviseError.value = "统计还没加载出来，先点「刷新」。";
    return;
  }
  adviseLoading.value = true;
  adviseError.value = "";
  try {
    const body = await api.adviseRisk({
      by_level: stats.value.by_level || {},
      by_type: stats.value.by_type || [],
      total_findings: stats.value.total_findings || 0,
      tasks: stats.value.tasks || 0,
    });
    adviseResult.value = body;
    adviseSource.value =
      SOURCE_LABELS[body.source] || body.source || "未知来源";
  } catch (err) {
    adviseResult.value = null;
    adviseError.value = `研判失败：${safeError(err)}`;
  } finally {
    adviseLoading.value = false;
  }
}

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
    border: cssVar("--border", "#dce7f5"),
  };
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
        animationDuration: 240,
        animationDurationUpdate: 160,
        animation: !window.matchMedia("(prefers-reduced-motion: reduce)")
          .matches,
        textStyle: { color: palette.text },
        tooltip: { trigger: "item" },
        legend: { bottom: 0, textStyle: { color: palette.muted } },
        series: [
          {
            type: "pie",
            radius: ["42%", "68%"],
            center: ["50%", "45%"],
            label: { formatter: "{b}: {c} ({d}%)", color: palette.text },
            data: rows.length
              ? rows
              : [
                  {
                    name: "暂无数据",
                    value: 1,
                    itemStyle: { color: palette.border },
                  },
                ],
          },
        ],
      },
      true,
    );
  }

  const bar = ensureChart(barEl.value);
  if (bar) {
    const rows = (data.by_type || []).map(
      (item) => item.key || item.type || item.name,
    );
    const counts = (data.by_type || []).map(
      (item) => item.count ?? item.value ?? 0,
    );
    bar.setOption(
      {
        animationDuration: 240,
        animationDurationUpdate: 160,
        animation: !window.matchMedia("(prefers-reduced-motion: reduce)")
          .matches,
        textStyle: { color: palette.text },
        tooltip: { trigger: "axis" },
        grid: { left: 42, right: 16, top: 24, bottom: 48 },
        xAxis: {
          type: "category",
          data: rows,
          axisLabel: { rotate: 30, color: palette.muted },
          axisLine: { lineStyle: { color: palette.border } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: palette.muted },
          splitLine: { lineStyle: { color: palette.border } },
        },
        series: [
          {
            type: "bar",
            data: counts,
            barMaxWidth: 34,
            itemStyle: { color: cssVar("--accent", "#5b96ed") },
          },
        ],
      },
      true,
    );
  }

  const hist = ensureChart(histEl.value);
  if (hist) {
    const entries = Object.entries(data.score_histogram || {});
    hist.setOption(
      {
        animationDuration: 240,
        animationDurationUpdate: 160,
        animation: !window.matchMedia("(prefers-reduced-motion: reduce)")
          .matches,
        textStyle: { color: palette.text },
        tooltip: { trigger: "axis" },
        grid: { left: 42, right: 16, top: 24, bottom: 40 },
        xAxis: {
          type: "category",
          data: entries.map(([key]) => key),
          axisLabel: { color: palette.muted },
          axisLine: { lineStyle: { color: palette.border } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: palette.muted },
          splitLine: { lineStyle: { color: palette.border } },
        },
        series: [
          {
            type: "bar",
            data: entries.map(([, count]) => count),
            barMaxWidth: 46,
            itemStyle: { color: cssVar("--warning") },
          },
        ],
      },
      true,
    );
  }
}

async function load() {
  try {
    stats.value = await api.stats();
    render();
  } catch (err) {
    ElMessage.error(`加载统计失败：${safeError(err)}`);
  }
}

onMounted(() => {
  load();
  resizeObserver = new ResizeObserver(() =>
    charts.forEach((chart) => chart.resize()),
  );
  if (pieEl.value?.parentElement)
    resizeObserver.observe(pieEl.value.parentElement);
  resizeHandler = () => charts.forEach((chart) => chart.resize());
  window.addEventListener("resize", resizeHandler);
  // 换主题时旧 canvas 颜色不会自动更新，干脆重建
  stopWatchTheme = watchTheme(() => {
    disposeCharts();
    render();
  });
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  if (stopWatchTheme) stopWatchTheme();
  disposeCharts();
});
</script>

<template>
  <PageHeader
    title="统计看板"
    description="从真实任务与检测数据中，观察风险分布与处理情况。"
    index="RISK ANALYTICS"
  />
  <div class="metric-strip">
    <div class="metric">
      <span>检测任务</span><strong>{{ stats?.tasks ?? "—" }}</strong>
    </div>
    <div class="metric">
      <span>检测片段</span><strong>{{ stats?.total_findings ?? "—" }}</strong>
    </div>
    <div class="metric">
      <span>L3 高风险片段</span
      ><strong style="color: var(--danger)">{{
        stats?.by_level?.L3 ?? "—"
      }}</strong>
    </div>
    <div class="metric">
      <span>SAFE 安全片段</span
      ><strong style="color: var(--success)">{{
        stats?.by_level?.SAFE ?? "—"
      }}</strong>
    </div>
  </div>
  <div class="panel">
    <div class="panel-title">
      统计看板
      <span class="hint" v-if="stats">
        命中 {{ stats.total_findings }} 条 · 任务
        {{ stats.tasks }} 个（全部任务聚合）
      </span>
    </div>
    <el-button size="small" @click="load">刷新</el-button>
    <div class="chart-row" style="margin-top: 14px">
      <div class="pane">
        <div class="pane-head">分级分布（饼图）</div>
        <div ref="pieEl" class="chart"></div>
      </div>
      <div class="pane">
        <div class="pane-head">敏感类别 TopN（柱状图）</div>
        <div ref="barEl" class="chart"></div>
      </div>
      <div class="pane">
        <div class="pane-head">敏感度分数直方图</div>
        <div ref="histEl" class="chart"></div>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">
      AI 风险研判
      <span class="hint">基于当前聚合统计生成结论与建议，不上传原文</span>
    </div>
    <div class="toolbar" style="margin-top: 0">
      <el-button
        type="primary"
        size="small"
        :loading="adviseLoading"
        :disabled="!stats"
        @click="runAdvise"
      >
        生成研判
      </el-button>
      <span v-if="adviseResult" class="hint">
        来源：{{ adviseSource }} · 发现 {{ adviseResult.findings.length }} 条 ·
        建议 {{ adviseResult.recommendations.length }} 条
      </span>
    </div>
    <div v-if="adviseError" class="hint-line" style="color: var(--danger)">
      {{ adviseError }}
    </div>
    <div v-if="adviseLoading" class="empty">正在研判…</div>
    <template v-else-if="adviseResult">
      <div class="pane" style="margin-top: 12px">
        <div class="pane-head">结论</div>
        <div class="pane-body">{{ adviseResult.summary }}</div>
      </div>
      <div class="compare" style="margin-top: 14px">
        <div class="pane">
          <div class="pane-head">发现</div>
          <ul v-if="adviseResult.findings.length" class="list-num">
            <li v-for="(item, index) in adviseResult.findings" :key="index">
              {{ item }}
            </li>
          </ul>
          <div v-else class="empty">暂无发现</div>
        </div>
        <div class="pane">
          <div class="pane-head">建议</div>
          <ul v-if="adviseResult.recommendations.length" class="list-num">
            <li
              v-for="(item, index) in adviseResult.recommendations"
              :key="index"
            >
              {{ item }}
            </li>
          </ul>
          <div v-else class="empty">暂无建议</div>
        </div>
      </div>
    </template>
    <div v-else class="empty">
      还没有研判结果，点「生成研判」基于当前统计生成
    </div>
  </div>
</template>
