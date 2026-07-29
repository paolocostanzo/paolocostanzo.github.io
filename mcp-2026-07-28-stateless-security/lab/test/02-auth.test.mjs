import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createLabHarness, LAB_URL, TOKENS } from '../src/harness.mjs';

const pingBody = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping' });
const baseHeaders = { accept: 'application/json, text/event-stream', 'content-type': 'application/json' };

test('T02 auth: a missing bearer token is rejected before the MCP handler', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());

  const response = await lab.fetch(LAB_URL, { method: 'POST', headers: baseHeaders, body: pingBody });
  assert.equal(response.status, 401);
  assert.match(response.headers.get('www-authenticate') ?? '', /^Bearer/i);
  assert.equal(lab.metrics.handlerRequests, 0);
  assert.deepEqual(lab.dispatch, { create_scan: 0, read_scan: 0, complete_scan: 0 });
  t.diagnostic(JSON.stringify({ status: 401, challenge: 'Bearer', handlerRequests: 0, toolDispatches: 0 }));
});

test('T03 auth: an unknown bearer token is rejected before the MCP handler', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());

  const response = await lab.fetch(LAB_URL, {
    method: 'POST',
    headers: { ...baseHeaders, authorization: 'Bearer unknown-demo-token' },
    body: pingBody
  });
  assert.equal(response.status, 401);
  assert.match(response.headers.get('www-authenticate') ?? '', /invalid_token/i);
  assert.equal(lab.metrics.handlerRequests, 0);
  assert.deepEqual(lab.dispatch, { create_scan: 0, read_scan: 0, complete_scan: 0 });
  assert.ok(!JSON.stringify(lab.records).includes('unknown-demo-token'));
  assert.ok(!JSON.stringify(lab.records).includes(TOKENS.alice));
  t.diagnostic(JSON.stringify({ status: 401, error: 'invalid_token', handlerRequests: 0, toolDispatches: 0 }));
});
