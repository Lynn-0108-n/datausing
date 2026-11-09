<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import DetectView from "./views/DetectView.vue";
import TasksView from "./views/TasksView.vue";
import StatsView from "./views/StatsView.vue";
import RulesView from "./views/RulesView.vue";
import SettingsView from "./views/SettingsView.vue";
import TrafficView from "./views/TrafficView.vue";
import HelpView from "./views/HelpView.vue";
import { api } from "./api";
import { operator, setOperator, syncThemeFromServer, theme } from "./theme";

const tabs = [
  {
    key: "detect",
    label: "内容检测",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M16.5 16.5 21 21"/></svg>',
    comp: DetectView,
  },
  {
    key: "traffic",
    label: "流量导入",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/></svg>',
    comp: TrafficView,
  },
  {
    key: "tasks",
    label: "检测记录",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.3" fill="currentColor" stroke="none"/></svg>',
    comp: TasksView,
  },
  {
    key: "stats",
    label: "统计看板",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20V13M2 20h20"/></svg>',
    comp: StatsView,
  },
  {
    key: "rules",
    label: "识别规则",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z"/><path d="M8 3v18"/></svg>',
    comp: RulesView,
  },
  {
    key: "settings",
    label: "设置",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    comp: SettingsView,
  },
];

// 帮助页固定在侧栏底部，不参与主导航胶囊
const HELP_KEY = "help";
const helpIcon =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 1 1 4.7 1.2c-.4.9-2.3 1.3-2.3 2.9"/><circle cx="12" cy="16.8" r="1" fill="currentColor" stroke="none"/></svg>';

const hashTab = (location.hash || '').replace('#', '');
const active = ref(hashTab === HELP_KEY || tabs.some((tab) => tab.key === hashTab) ? hashTab : 'detect');
const current = computed(() => {
  if (active.value === HELP_KEY) return HelpView;
  return (tabs.find((t) => t.key === active.value) || tabs[0]).comp;
});
/* ---------- 顶栏：时间与情绪日签 ---------- */
const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const pad2 = (value) => String(value).padStart(2, "0");
const now = ref(new Date());
let clockTimer = null;

// 等宽数字由 tabular-nums 保证，时间每秒刷新
const clockText = computed(() => {
  const d = now.value;
  return `${pad2(d.getMonth() + 1)}月${pad2(d.getDate())}日 ${WEEKDAYS[d.getDay()]} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
});

// 文案按页面 key 分组；切页与点击都从当前分组里抽，尽量不和上一句重复
const QUOTES = {
  detect: [
    "发现隐患，消灭隐患。",
    "刚刚那个手机号，我帮你藏好了。",
    "让数据安全，从脱敏开始。",
    "每一段文本，都值得被温柔对待。",
  ],
  traffic: [
    "流量会说话，敏感字段要闭嘴。",
    "看得见的包，看不见的隐私。",
    "从一段 pcap 里，看清数据的去向。",
  ],
  tasks: [
    "每一次检测，都留下可追溯的脚印。",
    "审计链不会说谎，改一个字都能看出来。",
    "任务跑完了，证据也留好了。",
  ],
  stats: [
    "那些你忽略的隐私数据，都在图表里现原形了。",
    "今天的数据很干净，请放心。",
    "数据会说谎，但图表不会。",
    "看清全局，才能掌控细节。",
  ],
  rules: [
    "没有规矩，不成方圆。",
    "正则虐我千百遍，我待正则如初恋。",
    "一条好规则，抵十次人工翻查。",
  ],
  settings: [
    "调一调，让 FlowMask 更顺手。",
    "默认值写在这里，导出时还能临时改。",
    "AI 助手、OCR、隐私增强，都在设置里。",
  ],
  help: [
    "不会用就点这里，别硬撑。",
    "问题问出来，答案就有一半了。",
  ],
  default: [
    "今天也是守护数据安全的一天。",
    "FlowMask 已就绪，随时为您服务。",
    "保持专注，代码无 Bug。",
  ],
};

function pickQuote(key) {
  const pool = QUOTES[key] || QUOTES.default;
  if (pool.length < 2) return pool[0];
  const candidates = pool.filter((item) => item !== quote.value);
  return candidates[Math.floor(Math.random() * candidates.length)];
}

const quote = ref("");
const quoteTick = ref(0);
const quoteBreathing = ref(false);
let quoteTimer = null;

function refreshQuote() {
  quote.value = pickQuote(active.value);
  quoteTick.value += 1;
}

// 首次加载即随机抽一句
refreshQuote();

// 点击换一句：淡入由内层 span 的 key 触发，呼吸动效只作用在按钮上
function nextQuote() {
  refreshQuote();
  quoteBreathing.value = false;
  clearTimeout(quoteTimer);
  requestAnimationFrame(() => {
    quoteBreathing.value = true;
    quoteTimer = setTimeout(() => {
      quoteBreathing.value = false;
    }, 700);
  });
}

/* ---------- 顶栏：消息未读红点 ---------- */
const NOTIFY_SEEN_KEY = "flowmask-notify-seen";
// 更新通知内容时递增此版本号，让红点重新出现
const NOTIFY_VERSION = "2026-11-02";

function hasSeenNotifications() {
  try {
    return localStorage.getItem(NOTIFY_SEEN_KEY) === NOTIFY_VERSION;
  } catch {
    return false; // 隐私模式下 localStorage 不可用，退化为当前会话已读
  }
}

const notifyUnread = ref(!hasSeenNotifications());

function markNotificationsRead() {
  notifyUnread.value = false;
  try {
    localStorage.setItem(NOTIFY_SEEN_KEY, NOTIFY_VERSION);
  } catch {
    /* 隐私模式：写入失败也不影响本次会话已读 */
  }
}

/* ---------- 顶栏：新手引导提示气泡 ---------- */
const GUIDE_TIP_KEY = "guide-tip-closed";

function isGuideTipClosed() {
  try {
    return localStorage.getItem(GUIDE_TIP_KEY) === "true";
  } catch {
    return false; // 隐私模式：读不到就按未关闭处理，仅本次会话可见
  }
}

const guideTipVisible = ref(!isGuideTipClosed());

function closeGuideTip() {
  guideTipVisible.value = false;
  try {
    localStorage.setItem(GUIDE_TIP_KEY, "true");
  } catch {
    /* 隐私模式：写入失败也不影响本次关闭 */
  }
}
const health = ref(null);
const healthError = ref("");

// 当前项玻璃胶囊：绝对定位在菜单容器内，随选中项滑动
const menuWrap = ref(null);
const pill = reactive({ top: 0, height: 0, stretch: 1, origin: "center", ready: false });
let pillPrevIndex = tabs.findIndex((t) => t.key === active.value);
let stretchTimer = null;

// 读取当前选中项的位置，写进胶囊；方向不同则沿移动方向先拉长再回弹
async function syncPill() {
  await nextTick();
  const wrap = menuWrap.value;
  if (!wrap) return;
  const items = wrap.querySelectorAll(".el-menu-item");
  const index = tabs.findIndex((t) => t.key === active.value);
  if (index < 0) {
    // 帮助页不在主导航内：收起胶囊
    pill.ready = false;
    return;
  }
  const item = items[index];
  if (!item) return;
  const wrapRect = wrap.getBoundingClientRect();
  const itemRect = item.getBoundingClientRect();
  pill.top = itemRect.top - wrapRect.top;
  pill.height = itemRect.height;
  if (!pill.ready) {
    pill.ready = true;
    return;
  }
  if (index === pillPrevIndex) return;
  pill.origin = index > pillPrevIndex ? "top center" : "bottom center";
  pill.stretch = 1.16;
  clearTimeout(stretchTimer);
  stretchTimer = setTimeout(() => {
    pill.stretch = 1;
  }, 210);
  pillPrevIndex = index;
}

watch(active, () => {
  resetCardGlow();
  syncPill();
  refreshQuote();
});
// 多用户演示：操作人可切换，主题按操作人存在服务端
const knownOperators = ref(["local"]);

// 拟物化版本只保留暖色浅色主题；左侧导航默认展开，可收起为浮动玻璃停靠栏
const collapsed = ref(false);
// 收起态悬停放大：当前图标最大，上下相邻图标跟随放大
const hoverIndex = ref(-1);
const hoverLabel = computed(() => (hoverIndex.value >= 0 ? tabs[hoverIndex.value].label : ""));

function onIconEnter(index) {
  if (collapsed.value) hoverIndex.value = index;
}
function onIconLeave() {
  hoverIndex.value = -1;
}
function iconScale(index) {
  if (!collapsed.value || hoverIndex.value < 0) return 1;
  const distance = Math.abs(index - hoverIndex.value);
  if (distance === 0) return 1.45;
  if (distance === 1) return 1.18;
  return 1;
}
// 展开/收起时清空悬停状态；重新展开后补测品牌区高度
watch(collapsed, () => {
  hoverIndex.value = -1;
  if (!collapsed.value) measureBrand();
});
// public 下的 logo 经 Vite base 前缀访问；用动态绑定避免静态 src 被 Rollup 当模块解析
const logoUrl = import.meta.env.BASE_URL + "logo-brown.png";
const logoMonoUrl = import.meta.env.BASE_URL + "logo-mono.png";
// 展开态品牌区实测高度：收起后沿用同一高度，图标上下位置保持不变
const brandRef = ref(null);
const brandHeight = ref(0);

function measureBrand() {
  if (collapsed.value || !brandRef.value) return;
  brandHeight.value = brandRef.value.offsetHeight;
}

function selectTab(key) {
  active.value = key;
  location.hash = key;
}

function selectHelp() {
  active.value = HELP_KEY;
  location.hash = HELP_KEY;
}

/* ---------- 卡片：跟随光标的柔光、局部提亮与 3D 倾斜 ---------- */
const CARD_SELECTOR = ".panel, .el-card, .side";
// 单轴最大角度：两轴同时倾斜时合成角度约 3.96°，保持在 4° 以内
const MAX_TILT = 2.8;
let litCard = null;

function onCardMove(event) {
  const target = event.target instanceof Element ? event.target : null;
  const card = target ? target.closest(CARD_SELECTOR) : null;
  if (!card) {
    resetCardGlow();
    return;
  }
  const inMain = Boolean(card.closest(".main"));
  const isSide = card.classList.contains("side");
  if (!inMain && !isSide) {
    resetCardGlow();
    return;
  }
  const rect = card.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  if (card !== litCard) {
    resetCardGlow();
    litCard = card;
    card.classList.add("is-lit");
  }
  // 光标落点：换算成卡片内坐标与 -1 ~ 1 的归一化偏移
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  card.style.setProperty("--spot-x", `${x.toFixed(1)}px`);
  card.style.setProperty("--spot-y", `${y.toFixed(1)}px`);
  // 侧栏只做光斑，不做倾斜；主区卡片才跟随光标轻微倾斜
  if (!inMain) {
    card.style.removeProperty("--tilt-x");
    card.style.removeProperty("--tilt-y");
    return;
  }
  const nx = Math.min(1, Math.max(0, x / rect.width)) * 2 - 1;
  const ny = Math.min(1, Math.max(0, y / rect.height)) * 2 - 1;
  card.style.setProperty("--tilt-x", `${(-ny * MAX_TILT).toFixed(2)}deg`);
  card.style.setProperty("--tilt-y", `${(nx * MAX_TILT).toFixed(2)}deg`);
}

// 移出卡片：去掉倾斜，交由 CSS 过渡平滑复原
function resetCardGlow() {
  if (!litCard) return;
  litCard.classList.remove("is-lit");
  litCard.style.removeProperty("--tilt-x");
  litCard.style.removeProperty("--tilt-y");
  litCard = null;
}

/* ---------- 顶栏：新手引导 ---------- */
let tour = null;

// 按钮文案 → 引导说明；页面切换后按当前 DOM 动态取按钮
const GUIDE_TEXT = {
  "检测": "提交当前内容：有文件时优先按文件批量检测，否则检测文本。",
  "上传文件": "选择待检测文件，支持 txt、log、json、csv、docx、pdf 等。",
  "清空": "清空输入、已选文件与当前检测结果。",
  "查看": "查看该文件任务的命中明细。",
  "导出": "下载该任务的命中明细（JSONL）。",
  "导入并检测": "解析所选 pcap，执行识别、分级与脱敏。",
  "导入自带示例": "直接导入仓库自带示例 pcap，无需手动选择文件。",
  "导出 JSONL": "按 JSONL 导出命中明细，一行一条。",
  "导出 CSV": "按 CSV 导出命中明细，便于表格查看。",
  "导出 JSON": "按 JSON 导出完整命中明细。",
  "导出 SQL": "生成 INSERT 语句，便于写入业务库。",
  "隐私导出": "按 ε 差分隐私与 l-多样性导出统计，不含原文。",
  "开始跟随": "持续读取不断增长的 pcap，按窗口落库并检测。",
  "停止": "停止该实时会话，已写入的窗口保留。",
  "刷新": "重新拉取最新数据。",
  "清理已停止": "移除已停止的实时会话记录。",
  "刷新并校验": "重新拉取审计链并校验哈希链与签名。",
  "提交文本建任务": "用输入框里的文本创建一个检测任务。",
  "上传文件建任务": "选择文件并直接创建检测任务。",
  "新增规则":
    "打开表单添加自定义规则。类别（下拉里是英文名）：phone 手机号、id_card 身份证号、bank_card 银行卡号、email 邮箱、ip IP 地址、secret 密钥、password 密码、name 姓名、address 地址、medical 医疗信息。正则：描述要匹配的内容，例如手机号 ^1[3-9]\\d{9}$，写得越精确误报越少。权重：这条规则对最终评分的影响，0–1，越大越敏感，常用 0.3–0.6。置信度：规则本身的可信程度，0–1，越确定越高，常用 0.8–0.95。",
  "删除": "删除该自定义规则；内置规则不可删除。",
  "取消": "放弃本次编辑并关闭弹窗。",
  "保存并生效": "保存规则，立即用于后续识别。",
  "渲染": "按当前授权与级别设置渲染脱敏视图。",
  "恢复示例": "把输入恢复为内置示例文本。",
};

// 内容检测页：固定四步（场景 → 输入 → 模式 → 检测 → 清空）
const GUIDE_PAGES = {
  detect: [
    {
      selector: ".scenario-bar",
      title: "选择场景模式",
      description: "点上方场景卡片一键套用对应的检测与脱敏参数；详细说明见「帮助与反馈」里的场景模式介绍。",
    },
    {
      selector: ".detect-input",
      title: "输入检测内容",
      description: "输入/粘贴文本，或把文件拖进来/点击「上传文件」。",
    },
    {
      selector: ".detect-options",
      title: "选择识别模式",
      description:
        "按实际级别：脱敏强度跟随识别结果，也可强制 L1/L2/L3。只显示 ≥：过滤掉低于该级别的命中。L2 语义：启用 BERT 语义复核，补规则抓不到的敏感表达。L3 大模型：启用本地大模型复核高风险内容。自由文本 NER：识别人名、地名等，需服务端启用模型。图片识别：上传图片时自动走 OCR 解析，识别图片中的文字。",
    },
    {
      selector: ".detect-submit",
      title: "检测",
      description: "提交当前内容：单次最多上传20个文件。",
    },
    {
      selector: ".detect-clear",
      title: "清空",
      description: "清空输入、已选文件与当前检测结果。",
    },
  ],
  stats: [
    {
      selector: ".stats-module",
      title: "双击放大图表",
      description:
        "双击任意模块，它会放大到统计看板卡片的左半边，右半边给出通俗的图表解读；其余模块缩到底部玻璃缩略条，点缩略条可切换主视角，点放大模块右上角的 × 会沿原路收回。",
    },
  ],
  traffic: [
    {
      selector: ".el-upload-dragger",
      title: "选择抓包文件",
      description:
        "点击这里选择 pcap 文件，或把文件直接拖进来。pcap 是一次抓包保存下来的网络流量；没有现成文件时，可以点下面的「导入自带示例」直接体验。",
    },
    {
      selector: ".import-submit",
      title: "导入并检测",
      description: "对选中的 pcap 执行解析 → 识别 → 分级 → 脱敏，结果显示在下方表格。",
    },
    {
      selector: ".import-sample",
      title: "导入自带示例",
      description: "一键载入仓库自带的示例 pcap 并检测，不需要自己准备文件，适合快速体验。",
    },
    {
      selector: ".export-jsonl",
      title: "导出 JSONL",
      description: "检测完成后出现：把命中明细按一行一条下载，适合程序处理。",
    },
    {
      selector: ".export-csv",
      title: "导出 CSV",
      description: "检测完成后出现：把命中明细导出为表格文件，适合用 Excel 查看。",
    },
    {
      selector: ".live-pcap",
      title: "抓包文件",
      description:
        "要持续跟随的文件路径：采集端一边往里写，这里一边读。默认 data/live.pcap，日常不用改；只有抓包文件放在别的位置时，才填相对仓库根目录的路径。",
    },
    {
      selector: ".live-interval",
      title: "轮询间隔",
      description:
        "每隔多少毫秒检查一次文件有没有新内容。数值越小越及时、越占用 CPU；默认 500，日常不用改。电脑卡就调到 1000–2000，想更实时可调到 200–300。",
    },
    {
      selector: ".live-window",
      title: "落库窗口",
      description:
        "每积累多少秒的流量生成一次检测任务。默认 2 秒：窗口越短结果越细但任务越多，越长越省资源但出结果越慢。一般保持 2，批量回放可调到 5–10。",
    },
    {
      selector: ".live-start",
      title: "开始跟随",
      description:
        "按上面的文件路径、检查间隔和落库窗口开始持续检测。开始后右侧会话表会新增一条记录，状态变为「跟随中」。",
    },
    {
      selector: ".live-refresh",
      title: "刷新",
      description: "立即重新拉取会话列表；列表本身每 2 秒自动刷新，日常不用手动点。",
    },
    {
      selector: ".live-clear",
      title: "清理已停止",
      description: "清掉已停止的会话记录，只清列表，不会删除已经生成的检测任务。",
    },
    {
      selector: ".live-stop",
      title: "停止",
      description: "结束这条会话的跟随，已写入的流量会先落库完成检测；需要时可重新开始跟随。",
    },
  ],
  tasks: [
    {
      selector: ".task-create-text",
      title: "提交文本建任务",
      description: "把上方输入框里的文本创建成一个检测任务，创建后任务会出现在下面的任务栏。",
    },
    {
      selector: ".task-create-file",
      title: "上传文件建任务",
      description: "选择一个文件并直接创建检测任务，适合文件较大或需要留档的场景。",
    },
    {
      selector: ".task-table",
      title: "任务栏",
      description:
        "列出最近 20 个检测任务，点任意一行查看该任务的命中明细。状态：排队（pending）/ 运行中（running）/ 完成（done）/ 失败（failed）；命中：识别到的敏感项数量；错误：失败原因。",
    },
    {
      selector: ".export-jsonl",
      title: "导出 JSONL",
      description: "按 JSONL 导出当前任务的命中明细，一行一条，适合程序处理。",
    },
    {
      selector: ".export-csv",
      title: "导出 CSV",
      description: "按 CSV 导出当前任务的命中明细，适合用表格软件查看。",
    },
    {
      selector: ".export-json",
      title: "导出 JSON",
      description: "按 JSON 导出当前任务的完整命中明细，保留嵌套结构。",
    },
    {
      selector: ".export-sql",
      title: "导出 SQL",
      description: "生成 INSERT 语句，便于把命中明细写入业务数据库。",
    },
    {
      selector: ".privacy-export",
      title: "隐私导出",
      description:
        "不导出原始内容，只导出统计结果。ε（差分隐私）控制加噪强度：数值越小噪声越大、越难反推出个体，但统计越模糊；数值越大越接近真实数据。一般从 1 开始，需要更强保护调到 0.5 以下，需要更准的统计调到 2 以上。l（多样性）要求每组至少有 l 条不同记录才输出：l 越大越难反推个体，但可能因分组不满足而告警，常用 2–5。",
    },
    {
      selector: ".audit-table",
      title: "审计链表",
      description:
        "每次识别操作都留一条记录。规则：命中的规则 ID；操作人：执行检测的账号；输入哈希：输入内容的摘要，用来核对内容是否被改动；前序哈希：指向上一条记录，把所有记录串成链，任何一条被篡改都会让上方校验失败。",
    },
  ],
};

function isGuideVisible(el) {
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== "hidden";
}

// 采集当前页面所有可见按钮，逐个生成引导步骤
function collectGuideSteps() {
  const root = document.querySelector(".main");
  if (!root) return [];
  const steps = [];
  const seen = new Set();
  // 有固定编排的页面按定义走；其余页面自动采集可见按钮
  const fixed = GUIDE_PAGES[active.value];
  if (fixed) {
    for (const item of fixed) {
      const el = root.querySelector(item.selector);
      if (!el) continue;
      steps.push({
        element: el,
        popover: { title: item.title, description: item.description },
      });
    }
    return steps;
  }
  for (const el of root.querySelectorAll("button, .export-link")) {
    const label = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!label || label.length > 14) continue;
    if (el.disabled || !isGuideVisible(el)) continue;
    if (seen.has(label)) continue;
    seen.add(label);
    steps.push({
      element: el,
      popover: {
        title: label,
        description: GUIDE_TEXT[label] || `点击「${label}」执行该操作。`,
      },
    });
  }
  return steps;
}

async function startGuide() {
  resetCardGlow();
  tour?.destroy();
  tour = null;
  if (active.value === HELP_KEY) {
    ElMessage.info("帮助与反馈页面无需引导");
    return;
  }
  await nextTick();
  const steps = collectGuideSteps();
  if (!steps.length) {
    ElMessage.info("当前页面没有可引导的按钮");
    return;
  }
  tour = driver({
    showProgress: true,
    progressText: "{{current}} / {{total}}",
    nextBtnText: "下一步",
    prevBtnText: "上一步",
    doneBtnText: "完成",
    steps,
  });
  tour.drive();
}

/* ---------- 顶栏：消息通知 ---------- */
const notifyTab = ref("feedback");
let notifySwipeX = null;

function openNotifications() {
  // 打开即视为已读；刷新后由 localStorage 记忆
  markNotificationsRead();
}

function onNotifySwipeStart(event) {
  notifySwipeX = event.clientX;
}

function onNotifySwipeEnd(event) {
  if (notifySwipeX === null) return;
  const delta = event.clientX - notifySwipeX;
  notifySwipeX = null;
  if (Math.abs(delta) < 40) return;
  notifyTab.value = delta < 0 ? "changelog" : "feedback";
}

/* ---------- 顶栏：操作人切换（无账号体系，仅标识符） ---------- */
async function onChangeOperator(value) {
  const result = await setOperator(value);
  ElMessage.success(`已切换用户「${operator.value}」：主题 ${result.theme}`);
}

async function loadOperators() {
  try {
    const body = await api.preferenceUsers();
    const names = body.users.map((item) => item.operator_id);
    knownOperators.value = Array.from(new Set(["local", ...names, operator.value]));
  } catch {
    /* 拿不到列表不影响本机使用 */
  }
}

onMounted(() => {
  clockTimer = setInterval(() => {
    now.value = new Date();
  }, 1000);
});

onBeforeUnmount(() => {
  clearInterval(clockTimer);
  clearTimeout(quoteTimer);
  clearTimeout(stretchTimer);
});

onMounted(async () => {
  try {
    health.value = await api.health();
  } catch (err) {
    healthError.value = String(err);
  }
  await syncThemeFromServer();
  await loadOperators();
  await syncPill();
  measureBrand();
});
</script>

<template>
  <el-container class="layout">
      <el-aside
        :width="collapsed ? '84px' : '220px'"
        class="side glass-card"
        :class="{ collapsed }"
        @pointermove="onCardMove"
        @pointerleave="resetCardGlow"
      >
        <div
          ref="brandRef"
          class="side-brand"
          :style="collapsed && brandHeight ? { height: `${brandHeight}px` } : null"
        >
          <img v-if="!collapsed" class="side-logo-img" :src="logoUrl" alt="FlowMask" @load="measureBrand" />
          <img v-else class="side-logo-img mono" :src="logoMonoUrl" alt="FlowMask" />
          <div v-if="!collapsed" class="side-tagline">——脱敏工具</div>
        </div>
        <div ref="menuWrap" class="menu-wrap">
          <!-- 当前项的半透明玻璃胶囊：滑动 + 方向性拉长 + 边缘细高光 -->
          <div
            class="nav-pill"
            :class="{ ready: pill.ready }"
            :style="{ transform: `translateY(${pill.top}px)`, height: `${pill.height}px` }"
          >
            <span
              class="nav-pill-glass"
              :style="{ transform: `scaleY(${pill.stretch})`, transformOrigin: pill.origin }"
            ></span>
          </div>
          <el-menu :default-active="active" class="side-menu" @select="selectTab">
            <el-menu-item
              v-for="(tab, i) in tabs"
              :key="tab.key"
              :index="tab.key"
              :title="tab.label"
              @mouseenter="onIconEnter(i)"
              @mouseleave="onIconLeave"
            >
              <span
                class="menu-icon"
                :style="collapsed ? { transform: `scale(${iconScale(i)})` } : null"
                v-html="tab.icon"
              ></span>
              <span class="menu-label">{{ tab.label }}</span>
              <span v-if="collapsed && tab.key === active" class="dock-dot"></span>
            </el-menu-item>
          </el-menu>
          <!-- 悬停时在停靠栏右下角显示名称 -->
          <div v-if="collapsed && hoverIndex >= 0" class="dock-name">
            {{ hoverLabel }}
            <span class="dock-name-dot"></span>
          </div>
        </div>
        <button
          class="side-help"
          :class="{ active: active === 'help' }"
          type="button"
          title="帮助与反馈"
          @click="selectHelp"
        >
          <span class="menu-icon" v-html="helpIcon"></span>
          <span class="menu-label">帮助与反馈</span>
        </button>
        <button
          class="collapse-btn"
          type="button"
          :title="collapsed ? '展开导航' : '收起导航'"
          @click="collapsed = !collapsed"
        >
          {{ collapsed ? "»" : "«" }}
        </button>
      </el-aside>
      <div class="content">
        <header class="top-header">
          <div class="top-actions">
            <!-- 新手引导提示气泡：紧贴问号左侧，箭头指向问号按钮 -->
            <div v-if="guideTipVisible && active !== HELP_KEY" class="guide-tip">
              <button class="guide-tip-text" type="button" title="开始新手引导" @click="startGuide">
                初次使用？点击问号，获取快速指引 ✨
              </button>
              <button
                class="guide-tip-close"
                type="button"
                title="关闭提示"
                aria-label="关闭新手引导提示"
                @click="closeGuideTip"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <!-- 新手引导 -->
            <button class="header-btn" type="button" title="新手引导" @click="startGuide">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M9.6 9.2a2.5 2.5 0 1 1 4.7 1.2c-.4.9-2.3 1.3-2.3 2.9" />
                <circle cx="12" cy="16.8" r="1" fill="currentColor" stroke="none" />
              </svg>
            </button>

            <!-- 消息通知 -->
            <el-popover placement="bottom-end" :width="320" trigger="click" popper-class="notify-popper">
              <template #reference>
                <button class="header-btn" type="button" title="消息通知" @click="openNotifications">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8" />
                    <path d="M13.7 20a2 2 0 0 1-3.4 0" />
                  </svg>
                  <span v-if="notifyUnread" class="header-dot"></span>
                </button>
              </template>
              <div class="notify-panel">
                <div class="notify-tabs">
                  <button
                    type="button"
                    :class="{ active: notifyTab === 'feedback' }"
                    @click="notifyTab = 'feedback'"
                  >反馈结果</button>
                  <button
                    type="button"
                    :class="{ active: notifyTab === 'changelog' }"
                    @click="notifyTab = 'changelog'"
                  >更新日志</button>
                </div>
                <div
                  class="notify-body"
                  @pointerdown="onNotifySwipeStart"
                  @pointerup="onNotifySwipeEnd"
                >
                  <div v-show="notifyTab === 'feedback'" class="notify-list">
                    <div class="notify-item">
                      <b>暂无反馈结果</b>
                      <span>提交反馈后，可在这里查看处理进度。</span>
                    </div>
                  </div>
                  <div v-show="notifyTab === 'changelog'" class="notify-list">
                    <div class="notify-item">
                      <b>内容检测合并</b>
                      <span>即时检测与批量检测合并为「内容检测」。</span>
                    </div>
                    <div class="notify-item">
                      <b>液态玻璃主题</b>
                      <span>全局切换为暖色磨砂玻璃风格。</span>
                    </div>
                    <div class="notify-item">
                      <b>识别与脱敏</b>
                      <span>L1 规则、四级评分与四级脱敏保持可用。</span>
                    </div>
                  </div>
                </div>
              </div>
            </el-popover>
          </div>

          <!-- 时间与情绪日签：落在图标群左下方，第一行图标左侧保持为空 -->
          <div class="top-signature">
            <time class="signature-clock" :datetime="now.toISOString()">{{ clockText }}</time>
            <span class="signature-divider" aria-hidden="true"></span>
            <button
              class="signature-quote"
              :class="{ 'is-breathing': quoteBreathing }"
              type="button"
              title="点击换一句"
              aria-label="点击换一句"
              @click="nextQuote"
            >
              <span :key="quoteTick" class="signature-quote-text">{{ quote }}</span>
            </button>
          </div>
        </header>

        <el-main
          class="main"
          :class="{ 'no-tilt': active === 'help' }"
          @pointermove="onCardMove"
          @pointerleave="resetCardGlow"
        >
          <!-- keep-alive：切菜单不重挂，内容检测页等状态保留；主题用 CSS 变量切换，无需重建组件 -->
          <KeepAlive>
            <component :is="current" />
          </KeepAlive>
        </el-main>
      </div>
  </el-container>
</template>
