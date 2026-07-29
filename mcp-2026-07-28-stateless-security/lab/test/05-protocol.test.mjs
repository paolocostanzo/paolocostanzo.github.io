import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  createLabHarness,
  LAB_URL,
  modernEnvelope,
  modernHeaders,
  MODERN_REVISION
} from '../src/harness.mjs';

async function post(lab, body, headers) {
  const response = await lab.fetch(LAB_URL, { method: 'POST', headers, body: JSON.stringify(body) });
  return { response, payload: await response.json() };
}

test('T10 protocol: header/body mismatch returns -32020 before dispatch', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());
  const before = { ...lab.dispatch };
  const body = modernEnvelope({ id: 'mismatch-1', name: 'complete_scan', arguments: { handle: 'scan-x' } });
  const { response, payload } = await post(lab, body, modernHeaders({ name: 'read_scan' }));

  assert.equal(response.status, 400);
  assert.equal(payload.id, 'mismatch-1');
  assert.equal(payload.error.code, -32020);
  assert.deepEqual(lab.dispatch, before);
  t.diagnostic(JSON.stringify({ status: 400, code: -32020, requestIdPreserved: true, dispatched: false }));
});

test('T11 protocol: a modern request without _meta returns -32602 before dispatch', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());
  const before = { ...lab.dispatch };
  const body = modernEnvelope({ id: 'missing-meta-1', name: 'read_scan', arguments: { handle: 'scan-x' }, includeMeta: false });
  const { response, payload } = await post(lab, body, modernHeaders({ name: 'read_scan' }));

  assert.equal(response.status, 400);
  assert.equal(payload.id, 'missing-meta-1');
  assert.equal(payload.error.code, -32602);
  assert.deepEqual(lab.dispatch, before);
  t.diagnostic(JSON.stringify({ status: 400, code: -32602, requestIdPreserved: true, dispatched: false }));
});

test('T12 protocol: an unsupported future revision returns -32022 and the supported list', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());
  const before = { ...lab.dispatch };
  const future = '2030-01-01';
  const body = modernEnvelope({ id: 'future-1', name: 'read_scan', arguments: { handle: 'scan-x' }, revision: future });
  const { response, payload } = await post(lab, body, modernHeaders({ name: 'read_scan', revision: future }));

  assert.equal(response.status, 400);
  assert.equal(payload.id, 'future-1');
  assert.equal(payload.error.code, -32022);
  assert.ok(payload.error.data.supported.includes(MODERN_REVISION));
  assert.deepEqual(lab.dispatch, before);
  t.diagnostic(
    JSON.stringify({ status: 400, code: -32022, supported: [MODERN_REVISION], requestIdPreserved: true, dispatched: false })
  );
});
