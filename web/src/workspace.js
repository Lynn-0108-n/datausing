// 只在内存中建立原文片段索引；路径和后端解析器一致，无法可靠映射时不套用区间。
export function sourceSegments(text, format) {
  const segments = new Map();
  if (format === "text") return new Map([["$", text]]);
  if (format === "json") {
    try {
      const walk = (value, path) => {
        if (typeof value === "string") segments.set(path, value);
        else if (Array.isArray(value))
          value.forEach((item, i) => walk(item, `${path}[${i}]`));
        else if (value && typeof value === "object")
          Object.entries(value).forEach(([key, item]) =>
            walk(item, `${path}.${key}`),
          );
      };
      walk(JSON.parse(text), "$");
    } catch {
      /* 服务端格式与输入不一致时不猜偏移 */
    }
  }
  if (format === "csv") {
    const first = text.split(/\r?\n/, 1)[0];
    const separator = ["\t", ",", ";", "|"].sort(
      (a, b) => first.split(b).length - first.split(a).length,
    )[0];
    const rows = [];
    let row = [];
    let cell = "";
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === '"') {
        if (quoted && text[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = !quoted;
      } else if (!quoted && ch === separator) {
        row.push(cell);
        cell = "";
      } else if (!quoted && (ch === "\n" || ch === "\r")) {
        if (ch === "\r" && text[i + 1] === "\n") i++;
        row.push(cell);
        if (row.some((item) => item.trim())) rows.push(row);
        row = [];
        cell = "";
      } else cell += ch;
    }
    row.push(cell);
    if (row.some((item) => item.trim())) rows.push(row);
    const header = rows.shift() || [];
    rows.forEach((cells, r) =>
      cells.forEach((value, c) =>
        segments.set(
          `row${r + 1}.${header[c]?.trim() || `col${c + 1}`}`,
          value,
        ),
      ),
    );
  }
  if (format === "sql") {
    const statements = [];
    let start = 0;
    for (let i = 0; i < text.length; i++) {
      if (text[i] === "'" || text[i] === '"') {
        const quote = text[i];
        i++;
        while (i < text.length) {
          if (text[i] === quote) {
            if (text[i + 1] === quote) {
              i += 2;
              continue;
            }
            break;
          }
          i++;
        }
      } else if (text.startsWith("--", i)) {
        const end = text.indexOf("\n", i);
        i = end < 0 ? text.length : end;
      } else if (text.startsWith("/*", i)) {
        const end = text.indexOf("*/", i + 2);
        i = end < 0 ? text.length : end + 1;
      } else if (text[i] === ";") {
        statements.push(text.slice(start, i));
        start = i + 1;
      }
    }
    statements.push(text.slice(start));
    let dml = 0;
    let literal = 0;
    for (const statement of statements.filter((item) => item.trim())) {
      if (
        /^\s*(create|alter|drop|comment|truncate|grant|revoke|use|set|delimiter)\b/i.test(
          statement,
        )
      )
        continue;
      dml++;
      for (const match of statement.matchAll(/'((?:[^']|'')*)'/g)) {
        if (!match[1].trim()) continue;
        segments.set(`stmt${dml}.literal${++literal}`, match[1]);
      }
    }
  }
  return segments;
}

export function countHits(findings = []) {
  return findings.reduce((sum, row) => sum + (Number(row.hits) || 0), 0);
}

export function safeError(
  error,
  fallback = "操作失败，请检查服务状态后重试。",
) {
  // 校验错误体可能带回原始 input，界面只保留状态码与可操作提示。
  const status = /\b(400|401|403|413|415|422|429|500|502|503)\b/.exec(
    String(error?.message || ""),
  )?.[1];
  const messages = {
    401: "访问令牌缺失或无效，请检查本机 API Token 配置。",
    403: "当前操作未获授权。",
    413: "文件或文本超出服务端限制，请缩小输入后重试。",
    415: "不支持的文件格式，请选择服务端支持的格式。",
    422: "输入或参数无法解析，请检查文件内容和选项。",
    429: "请求过于频繁，请稍后重试。",
  };
  return status
    ? messages[status] || `服务暂时无法完成操作（${status}），请稍后重试。`
    : fallback;
}
