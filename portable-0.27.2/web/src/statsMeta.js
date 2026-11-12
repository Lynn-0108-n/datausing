// 统计看板的类别中文名与展示辅助（纯函数，无副作用）。
// 后端 0.26.1 的 /stats 不返回 type_labels，中文名统一在前端映射，
// 与 server/app/identify/types.py 里的 ENTITY_LABELS 保持一致。

export const TYPE_LABELS = {
  phone: "手机号",
  id_card: "身份证号",
  bank_card: "银行卡号",
  email: "邮箱",
  ip: "IP 地址",
  secret: "密钥凭据",
  password: "密码",
  name: "姓名",
  address: "地址",
  medical: "医疗信息",
};

// 柱状图 x 轴短名：rotate 0 时也要放得下，完整名称放在 tooltip 与记录抽屉里。
// 关键词都是 2–3 个汉字，10 个类别在 320px 宽的卡片里也不会互相遮挡。
export const TYPE_SHORT_LABELS = {
  phone: "手机号",
  id_card: "证件",
  bank_card: "银行卡",
  email: "邮箱",
  ip: "IP",
  secret: "密钥",
  password: "密码",
  name: "姓名",
  address: "地址",
  medical: "医疗",
};

// 常见 10 类，用于筛选下拉与顺序稳定
export const TYPE_KEYS = Object.keys(TYPE_LABELS);

export const TYPE_FILTER_OPTIONS = TYPE_KEYS.map((key) => ({ value: key, label: TYPE_LABELS[key] }));

// 类别英文键 → 中文名；未登记的类别回退成原键，避免出现空白
export function typeLabel(key) {
  const text = String(key || "").trim();
  if (!text) return "未知类别";
  return TYPE_LABELS[text] || text;
}

export function shortTypeLabel(key) {
  const text = String(key || "").trim();
  if (!text) return "未知";
  return TYPE_SHORT_LABELS[text] || text;
}

// 计数展示：千分位，纯数字；非法值按 0
export function formatCount(value) {
  const count = Number(value) || 0;
  return count.toLocaleString("zh-CN");
}

// 占比展示：total 为 0 时返回破折号，避免出现 NaN%；保留 1 位小数，整数不带 ".0"
export function formatShare(count, total) {
  const numerator = Number(count) || 0;
  const denominator = Number(total) || 0;
  if (!denominator) return "—";
  const percent = Math.round((numerator / denominator) * 1000) / 10;
  return `${percent}%`;
}
