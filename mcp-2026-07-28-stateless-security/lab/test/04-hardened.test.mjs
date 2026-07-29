import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createLabHarness, TOKENS, toolJson, toolText } from '../src/harness.mjs';

test('T06 hardened: principal binding blocks Mallory cross-read', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());
  const alice = await lab.connectClient('alice');
  const mallory = await lab.connectClient('mallory');

  const created = toolJson(
    await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } })
  );
  const denied = await mallory.callTool({ name: 'read_scan', arguments: { handle: created.handle } });

  assert.match(created.handle, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.equal(denied.isError, true);
  assert.equal(toolText(denied), 'scan not found');
  assert.ok(!toolText(denied).includes('alice-private'));
  t.diagnostic(
    JSON.stringify({ actor: 'mallory', action: 'read_scan', handleFormat: 'uuid-v4', denied: true, error: 'generic' })
  );
});

test('T07 hardened: principal binding blocks Mallory cross-write and preserves owner access', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());
  const alice = await lab.connectClient('alice');
  const mallory = await lab.connectClient('mallory');

  const created = toolJson(
    await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } })
  );
  const denied = await mallory.callTool({ name: 'complete_scan', arguments: { handle: created.handle } });
  const beforeOwnerWrite = toolJson(await alice.callTool({ name: 'read_scan', arguments: { handle: created.handle } }));
  const ownerWrite = toolJson(await alice.callTool({ name: 'complete_scan', arguments: { handle: created.handle } }));

  assert.equal(denied.isError, true);
  assert.equal(beforeOwnerWrite.status, 'pending');
  assert.equal(ownerWrite.status, 'completed');
  t.diagnostic(JSON.stringify({ malloryWriteDenied: true, ownerStateBefore: 'pending', ownerWriteAfter: 'completed' }));
});

test('T08 hardened: expired state is rejected and removed without sleeping', async t => {
  let now = Date.UTC(2026, 6, 29, 12, 0, 0);
  const lab = createLabHarness({ mode: 'hardened', clock: () => now, ttlMs: 1_000 });
  t.after(() => lab.close());
  const alice = await lab.connectClient('alice');

  const created = toolJson(
    await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } })
  );
  now += 1_001;
  const denied = await alice.callTool({ name: 'read_scan', arguments: { handle: created.handle } });

  assert.equal(denied.isError, true);
  assert.equal(toolText(denied), 'scan not found');
  assert.equal(lab.store.size, 0);
  assert.equal(lab.audit.at(-1)?.reason, 'expired');
  t.diagnostic(JSON.stringify({ expired: true, genericError: true, storeEntriesAfterCleanup: 0 }));
});

test('T09 hardened: denied audit events correlate attempts without controlled secrets', async t => {
  const lab = createLabHarness({ mode: 'hardened', traceGenerator: () => 'trace-test-001' });
  t.after(() => lab.close());
  const alice = await lab.connectClient('alice');
  const mallory = await lab.connectClient('mallory');

  const created = toolJson(
    await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } })
  );
  await mallory.callTool({ name: 'read_scan', arguments: { handle: created.handle } });

  const event = lab.audit.find(item => item.actor === 'mallory' && item.outcome === 'denied');
  assert.deepEqual(
    { actor: event.actor, action: event.action, outcome: event.outcome, reason: event.reason, traceId: event.traceId },
    {
      actor: 'mallory',
      action: 'read_scan',
      outcome: 'denied',
      reason: 'not_found_or_not_owner',
      traceId: 'trace-test-001'
    }
  );
  assert.match(event.handleHash, /^[a-f0-9]{64}$/);
  const serialized = JSON.stringify(lab.audit);
  for (const secret of [created.handle, 'alice-private', TOKENS.alice, TOKENS.mallory]) {
    assert.ok(!serialized.includes(secret));
  }
  t.diagnostic(
    JSON.stringify({ actor: event.actor, outcome: event.outcome, reason: event.reason, traceId: event.traceId, hashLength: 64 })
  );
});
