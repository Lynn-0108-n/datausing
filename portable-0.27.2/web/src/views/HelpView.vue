<script setup>
import { ref } from "vue";
import { ElMessage } from "element-plus";

const faqs = [
  {
    q: "FlowMask 适合谁用？",
    a: "面向个人开发者、算法工程师、师生与独立外包，用于本地数据脱敏与敏感数据自查。不是企业级 DLP，没有多租户、集中策略下发等重型能力，因此轻量、易部署、全链路本地。",
  },
  {
    q: "内容检测支持哪些文件格式？",
    a: "支持 .txt、.log、.md、.json、.csv、.tsv、.sql、.docx、.pdf，以及图片 .png、.jpg、.jpeg（图片经 OCR 解析为文本后识别，需服务端启用 OCR），单次最多 20 个文件。也可以直接粘贴文本检测。",
  },
  {
    q: "L1 / L2 / L3 分别代表什么？",
    a: "L1 为规则识别，覆盖手机号、身份证号等结构化敏感数据；L2 为 BERT 语义识别；L3 为本地大模型复核。级别越高，脱敏力度越大。",
  },
  {
    q: "自由文本 NER 为什么是灰色的？",
    a: "NER 需要本地模型权重，服务端未启用时会禁用该开关。启动采集端时设置 FLOWMASK_ENABLE_NER=1 并准备权重即可。",
  },
  {
    q: "检测数据会离开本机吗？",
    a: "不会。识别、分级与脱敏全部在本地闭环完成，数据不出机。AI 助手也只允许连接本机回环地址上的 Ollama，不会把数据发送到外部服务。",
  },
  {
    q: "检测结果可以导出吗？",
    a: "可以。检测完成后，在「左右对照」面板右上角选择脱敏策略（partial 部分遮掩 / block 全屏蔽 / hash 哈希替换 / generalize 数值泛化 / pseudonym 假名替换 / encrypt AES-GCM 加密），点「下载」即可得到按该策略重建的脱敏文件；批量结果也可在对应文件行点「脱敏文件」单独导出。",
  },
  {
    q: "场景模式是什么？",
    a: "场景模式是一套预设：选「医疗病历」「政务公开」「电商订单」「金融流水」「HR 简历」「日志审计」任一项，会自动套用对应的示例文本、推荐脱敏策略、识别开关与最低级别。也可以点「自定义模式」自己命名并保存（仅存浏览器 localStorage，不进后端库）。",
  },
  {
    q: "「AI 帮我选方案」是干嘛的？",
    a: "在内容检测页点「AI 帮我选方案」，告诉系统你的用途（开发测试 / 教学演示 / 外包交付 / 数据分析 / 公开共享）和输出要求（保留统计特征 / 可逆解密 / 格式保持 / 彻底不可逆等），系统会推荐合适的脱敏策略与识别开关。默认走规则引擎秒级返回；配置了本机 Ollama 后可给出更细的解读。",
  },
  {
    q: "导出策略里的 encrypt 加密，密钥在哪？",
    a: "选 encrypt 导出时，密钥随下载文件一起返回，浏览器会自动把密钥复制到剪贴板并弹窗提示；同时后端不会持久化保存密钥，请妥善保管，丢了就无法解密。",
  },
  {
    q: "图片 OCR 是怎么做的？会调大模型吗？",
    a: "OCR 使用轻量专用视觉模型 rapidocr-onnxruntime（ONNX 格式），不调大模型，速度快、本地运行。默认关闭，需在服务端设置 FLOWMASK_OCR_ENABLED=1 并安装 rapidocr-onnxruntime 后生效。",
  },
  {
    q: "AI 助手会联网吗？数据安全吗？",
    a: "AI 助手有两档：关闭（默认，仅规则引擎，纯本地）/ 本机 Ollama（回环 127.0.0.1，数据不出机）。不支持外部 API，AI 解读只接收聚合计数与用途标签，绝不发送原文、masked 值或文件路径。",
  },
  {
    q: "「检测记录」页的作用",
    a: "「检测记录」页集中展示所有检测的历史与审计链。内容检测页每次检测（文本或文件）都会自动创建一条记录，包含输入哈希、命中的规则、操作人、生成时间，并通过 HMAC-SHA256 哈希链串起来；任何一条被篡改都会在校验时被发现并定位断点。该页支持分级/类别/操作人筛选、JSONL/CSV/JSON/SQL 多格式导出，以及隐私增强导出（差分隐私 ε + l-多样性）；不再需要手动提交检测。",
  },
];

// 场景模式介绍：6 个内置场景
const SCENARIOS = [
  { name: "医疗病历", strategy: "partial 部分遮掩", level: "L2", desc: "主诉、初步诊断保留字段名、值用星号，姓名与证件同步遮掩，适合临床数据共享与教学。" },
  { name: "政务公开", strategy: "hash 哈希替换", level: "L2", desc: "彻底去标识化，对外发布前用哈希替代敏感字段。" },
  { name: "电商订单", strategy: "pseudonym 假名替换", level: "L2", desc: "用假名替代真实姓名，保留订单结构便于风控分析。" },
  { name: "金融流水", strategy: "encrypt AES-GCM", level: "L3", desc: "强加密可逆，敏感字段加密保存，需要时用密钥解密。" },
  { name: "HR 简历", strategy: "pseudonym 假名替换", level: "L2", desc: "人名用稳定假名替换，身份证、电话、邮箱同步遮掩，用于内部筛选前的脱敏。" },
  { name: "日志审计", strategy: "block 全屏蔽", level: "L3", desc: "敏感字段整体替换为「已屏蔽:类别」，日志归档前彻底脱敏。" },
];

// 脱敏策略介绍：6 种导出策略
const STRATEGIES = [
  { name: "partial 部分遮掩", tag: "可读 / 不可逆", desc: "手机号、邮箱保留首字符；地址保留省/市/区，街道起用星号；医疗保留字段名、值用星号；姓名与证件整体遮掩。适合内部沟通、教学演示。" },
  { name: "block 全屏蔽", tag: "不可读 / 不可逆", desc: "整体替换为类别名（如「手机号」「身份证号」）。彻底隐藏，适合日志归档、对外公开。" },
  { name: "hash 哈希替换", tag: "去标识 / 不可逆", desc: "敏感值替换为 SHA-256 哈希值。同一原文得到同一哈希，可用于跨表关联分析。" },
  { name: "generalize 数值泛化", tag: "可统计 / 不可逆", desc: "数值、日期、地址做区间泛化（如「35 岁」→「30-40 岁」）。保留统计特征，适合数据发布。" },
  { name: "pseudonym 假名替换", tag: "可读 / 不可逆", desc: "用假名替代真实姓名，身份证保留首尾。适合风控、订单分析等需要字段形态的场景。" },
  { name: "encrypt AES-GCM", tag: "可逆 / 强加密", desc: "AES-GCM 对称加密，密钥随文件返回不持久化。适合需要还原原始数据的场景，密钥务必妥善保管。" },
];

const activeFaq = ref([]);
const feedback = ref("");
const contact = ref("");

function submitFeedback() {
  if (!feedback.value.trim()) {
    ElMessage.warning("请先填写反馈内容");
    return;
  }
  // 占位：后续接入反馈提交接口
  ElMessage.success("反馈已提交，感谢你的建议");
  feedback.value = "";
  contact.value = "";
}
</script>

<template>
  <div class="panel">
    <div class="panel-title">帮助与反馈</div>
    <div class="help-subtitle">常见问题</div>
    <el-collapse v-model="activeFaq" class="help-faq">
      <el-collapse-item v-for="(item, index) in faqs" :key="index" :title="item.q" :name="String(index)">
        <div class="help-answer">{{ item.a }}</div>
      </el-collapse-item>
    </el-collapse>
  </div>

  <div class="panel">
    <div class="panel-title">场景模式介绍</div>
    <p class="scenario-intro">在内容检测页选择对应场景，会自动套用推荐的脱敏策略、识别开关与最低级别。</p>
    <div class="scenario-grid">
      <div v-for="(s, idx) in SCENARIOS" :key="idx" class="scenario-card">
        <div class="scenario-name">{{ s.name }}</div>
        <div class="scenario-meta">
          <span class="scenario-strategy">{{ s.strategy }}</span>
          <span class="scenario-level">最低 {{ s.level }}</span>
        </div>
        <div class="scenario-desc">{{ s.desc }}</div>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">脱敏策略介绍</div>
    <p class="scenario-intro">内容检测页导出时可选择以下 6 种策略，按需切换可逆性、可读性与统计特性。</p>
    <div class="scenario-grid">
      <div v-for="(s, idx) in STRATEGIES" :key="idx" class="scenario-card">
        <div class="scenario-name">{{ s.name }}</div>
        <div class="scenario-meta">
          <span class="scenario-strategy">{{ s.tag }}</span>
        </div>
        <div class="scenario-desc">{{ s.desc }}</div>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">提交反馈</div>
    <el-input
      v-model="feedback"
      type="textarea"
      :rows="6"
      placeholder="描述你遇到的问题或改进建议…"
    />
    <div class="toolbar">
      <el-input v-model="contact" placeholder="联系方式（选填）" style="width: 240px" />
      <el-button type="primary" @click="submitFeedback">提交反馈</el-button>
    </div>
  </div>
</template>

<style scoped>
.help-subtitle {
  margin: 4px 0 10px;
  font-size: 14px;
  font-weight: 700;
  color: var(--muted);
  letter-spacing: 0.04em;
}
.help-faq {
  --el-collapse-border-color: rgba(36, 52, 77, 0.08);
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
}
.help-faq :deep(.el-collapse-item__header) {
  height: 52px;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  background: transparent;
  border-bottom-color: rgba(36, 52, 77, 0.08);
}
.help-faq :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom-color: rgba(36, 52, 77, 0.08);
}
.help-answer {
  padding: 2px 0 14px;
  font-size: 14px;
  line-height: 1.8;
  color: var(--muted);
}

/* 场景模式介绍 */
.scenario-intro {
  margin: 0 0 14px;
  font-size: 14px;
  color: var(--muted);
}
.scenario-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
}
.scenario-card {
  padding: 14px 16px;
  background: rgba(36, 52, 77, 0.03);
  border-left: 3px solid var(--copper);
  border-radius: 4px;
}
.scenario-name {
  font-size: 15px;
  font-weight: 800;
  color: var(--ink);
  margin-bottom: 8px;
}
.scenario-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  font-size: 12px;
}
.scenario-strategy {
  padding: 2px 8px;
  background: rgba(74, 135, 228, 0.12);
  color: var(--copper);
  border-radius: 10px;
}
.scenario-level {
  padding: 2px 8px;
  background: rgba(36, 52, 77, 0.06);
  color: var(--muted);
  border-radius: 10px;
}
.scenario-desc {
  font-size: 13px;
  line-height: 1.6;
  color: #4a5f7c;
}
</style>
