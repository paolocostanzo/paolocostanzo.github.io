import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createLabHarness, toolJson } from '../src/harness.mjs';

test('T04 vulnerable: Mallory can read Alice data with the returned handle', async t => {
  const lab = createLabHarness({ mode: 'vulnerable' });
  t.after(() => lab.close());
  const alice = await lab.connectClient('alice');
  const mallory = await lab.connectClient('mallory');

  const created = toolJson(
    await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } })
  );
  const stolen = toolJson(await mallory.callTool({ name: 'read_scan', arguments: { handle: created.handle } }));

  assert.match(created.handle, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.equal(created.owner, 'alice');
  assert.equal(stolen.owner, 'alice');
  assert.equal(stolen.note, 'alice-private');
  t.diagnostic(
    JSON.stringify({ owner: 'alice', reader: 'mallory', handleFormat: 'uuid-v4', noteMatched: true, crossPrincipalRead: true })
  );
});

test('T05 vulnerable: Mallory can modify Alice data with the returned handle', async t => {
  const lab = createLabHarness({ mode: 'vulnerable' });
  t.after(() => lab.close());
  const alice = await lab.connectClient('alice');
  const mallory = await lab.connectClient('mallory');

  const created = toolJson(
    await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } })
  );
  const changed = toolJson(await mallory.callTool({ name: 'complete_scan', arguments: { handle: created.handle } }));
  const observedByAlice = toolJson(await alice.callTool({ name: 'read_scan', arguments: { handle: created.handle } }));

  assert.equal(changed.owner, 'alice');
  assert.equal(changed.status, 'completed');
  assert.equal(observedByAlice.status, 'completed');
  t.diagnostic(JSON.stringify({ owner: 'alice', writer: 'mallory', statusObservedByOwner: 'completed' }));
});
