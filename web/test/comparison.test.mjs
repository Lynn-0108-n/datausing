import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareComparison, mapLine } from '../src/comparison.js';

test('码点定位、重复值、跨行与删除映射不使用字符串搜索', () => {
  const model = prepareComparison({ segments: [{ path: '$', original: '😀张三\n张三', masked: '😀NAME\n', changes: [
    { start: 1, end: 3, masked_start: 1, masked_end: 5 },
    { start: 4, end: 6, masked_start: 6, masked_end: 6 },
  ] }] });
  assert.equal(model.original[0].parts[0].text, '😀');
  assert.equal(model.original[0].parts[1].text, '张三');
  assert.equal(model.masked[0].parts[1].text, 'NAME');
  assert.equal(model.masked[1].parts[1].id, 1);
  assert.equal(model.changes[1].originalLine, 1);
});
test('根据差异锚点映射不同文档行数', () => {
  const anchors = [{ originalLine: 0, maskedLine: 0 }, { originalLine: 10, maskedLine: 5 }, { originalLine: 20, maskedLine: 30 }];
  assert.equal(mapLine(anchors, 'original', 15), 17.5);
  assert.equal(mapLine(anchors, 'masked', 5), 10);
});
test('长文档行构建不超过 JS 函数参数上限', () => {
  const text = '\n'.repeat(140000);
  const model = prepareComparison({ segments: [{ path: '$', original: text, masked: text, changes: [] }] });
  assert.equal(model.original.length, 140001);
  assert.equal(model.changes.length, 0);
});
