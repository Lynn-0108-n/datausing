// 服务端偏移为 Unicode 码点；使用 Array.from 避免中文/emoji 的 UTF-16 偏移错误。
export function prepareComparison(payload) {
  const sides = { original: [], masked: [] };
  const changes = [];
  const anchors = [];
  for (const segment of payload.segments) {
    const base = { original: sides.original.length, masked: sides.masked.length };
    anchors.push({ originalLine: base.original, maskedLine: base.masked });
    const local = {};
    for (const side of Object.keys(sides)) {
      let offset = 0;
      local[side] = segment[side].split("\n").map(text => {
        const line = { text, start: offset, end: offset + Array.from(text).length, marks: [], path: segment.path };
        offset = line.end + 1;
        return line;
      });
      for (const line of local[side]) sides[side].push(line);
    }
    for (const change of segment.changes) {
      const id = changes.length;
      const entry = { ...change, id, path: segment.path };
      for (const side of Object.keys(sides)) {
        const start = side === "original" ? change.start : change.masked_start;
        const end = side === "original" ? change.end : change.masked_end;
        let first = null;
        let lo = 0, hi = local[side].length;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (local[side][mid].end < start) lo = mid + 1; else hi = mid; }
        for (let index = lo; index < local[side].length && local[side][index].start <= end; index++) {
          const line = local[side][index];
          if ((start < line.end && end > line.start) || (start === end && start >= line.start && start <= line.end)) {
            line.marks.push({ id, start: Math.max(0, start - line.start), end: Math.min(line.end, end) - line.start });
            if (first === null) first = base[side] + index;
          }
        }
        entry[side + "Line"] = first ?? base[side];
      }
      changes.push(entry);
      anchors.push(entry);
    }
    anchors.push({ originalLine: sides.original.length - 1, maskedLine: sides.masked.length - 1 });
  }
  for (const lines of Object.values(sides)) {
    for (const line of lines) {
      const chars = Array.from(line.text);
      let cursor = 0;
      line.parts = [];
      for (const mark of line.marks) {
        line.parts.push({ text: chars.slice(cursor, mark.start).join("") });
        line.parts.push({ text: chars.slice(mark.start, mark.end).join(""), id: mark.id });
        cursor = mark.end;
      }
      line.parts.push({ text: chars.slice(cursor).join("") });
    }
  }
  return { ...sides, changes, anchors, hits: payload.hits, coverage: payload.coverage };
}

export function mapLine(anchors, from, value) {
  const a = from + "Line", b = (from === "original" ? "masked" : "original") + "Line";
  if (!anchors.length) return 0;
  let lo = 0, hi = anchors.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (anchors[mid][a] <= value) lo = mid + 1; else hi = mid; }
  const left = anchors[Math.max(0, lo - 1)], right = anchors[lo];
  if (!right) return left[b];
  return left[b] + (value - left[a]) / (right[a] - left[a]) * (right[b] - left[b]);
}
