import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.js';

const originalFetch = globalThis.fetch;
test.afterEach(() => {
  globalThis.fetch = originalFetch;
  delete globalThis.window;
  delete globalThis.document;
});

test('strategy preview sends snapshot identity and policy without original input', async () => {
  let sent;
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.fetch = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return new Response(JSON.stringify({ result_id: 'snapshot' }), { status: 200 });
  };
  await api.previewSnapshot({ result_id: 'snapshot', policy_version: 'v1', text: '13800138000' }, 'encrypt');
  assert.equal(sent.url, '/api/v1/detect/results/snapshot/preview');
  assert.deepEqual(sent.body, { strategy: 'encrypt', policy_version: 'v1' });
});

test('snapshot export does not resubmit input or detection options', async () => {
  let sent;
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.document = {
    createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} },
  };
  globalThis.fetch = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return new Response('masked', { status: 200 });
  };
  await api.exportSnapshot({ result_id: 'snapshot', policy_version: 'v1', text: '13800138000', enable_l3: true }, 'block', 'txt');
  assert.equal(sent.url, '/api/v1/detect/export');
  assert.deepEqual(sent.body, { result_id: 'snapshot', policy_version: 'v1', strategy: 'block', fmt: 'txt' });
});
