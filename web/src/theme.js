import { ref, watch } from "vue";

import { api } from "./api";

const THEME_KEY = "flowmask-theme";
const OPERATOR_KEY = "flowmask-operator";
const DEFAULT_OPERATOR = "local";

// 拟物化版本只保留暖色浅色主题；不再提供深色切换
export const theme = ref("light");
export const operator = ref(DEFAULT_OPERATOR);

function readLocal(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 隐私模式下写不进去不影响使用 */
  }
}

// 每个操作人一份本地缓存，切用户时不会互相覆盖
function cacheKey() {
  return `${THEME_KEY}:${operator.value}`;
}

// 始终把页面固定为浅色（拟物暖色），移除所有深色分支
function paint() {
  const root = document.documentElement;
  root.classList.remove("dark");
  root.dataset.theme = "light";
  theme.value = "light";
  return "light";
}

export function applyTheme() {
  writeLocal(cacheKey(), "light");
  return paint();
}

// 兼容旧调用：拟物化已取消深色，切换恒为浅色
export function toggleTheme() {
  return "light";
}

export function setOperator(next) {
  const value = (next || "").trim().slice(0, 64) || DEFAULT_OPERATOR;
  operator.value = value;
  writeLocal(OPERATOR_KEY, value);
  return syncThemeFromServer();
}

// 保留服务端偏好同步（仍记录偏好），但界面恒为浅色
export async function syncThemeFromServer() {
  try {
    const body = await api.preferences(operator.value);
    return { theme: "light", source: body && body.theme ? "user" : "cache" };
  } catch {
    return { theme: "light", source: "offline" };
  }
}

export function initTheme() {
  operator.value = readLocal(OPERATOR_KEY) || DEFAULT_OPERATOR;
  // 演示/截图：/app/?operator=alice 可直接以某个用户身份打开（不写本地存储）
  try {
    const fromQuery = new URLSearchParams(window.location.search).get("operator");
    if (fromQuery) operator.value = fromQuery.trim().slice(0, 64) || DEFAULT_OPERATOR;
  } catch {
    /* 老浏览器没有 URLSearchParams 也无妨 */
  }
  paint();
  return "light";
}

// ECharts 这类命令式渲染的组件用它订阅主题变化（拟物化后主题不变，保留兼容）
export function watchTheme(handler) {
  return watch(theme, () => handler(theme.value), { flush: "post" });
}

// 读 style.css 里的主题变量，避免 JS 里再写一份颜色
export function cssVar(name, fallback = "") {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}
