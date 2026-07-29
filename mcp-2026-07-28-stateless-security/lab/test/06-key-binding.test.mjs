import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createLabHarness, toolJson, toolText } from '../src/harness.mjs';

// The victim's principal id already contains the delimiter that the naive
// composite key uses to join owner and handle. That lets the attacker move the
// boundary by one segment: the record is written as `tenant-a:svc` + handle and
// read back as `tenant-a` + a handle that has swallowed the `svc` segment.
// Structured principal ids are not exotic — multi-tenant issuers produce them,
// and so does the `${iss}#${sub}` canonical form used in the AWS sketch.
const VICTIM = 'tenant-a:svc';
const ATTACKER = 'tenant-a';

async function attemptSmuggledLookup(lab) {
  const victim = await lab.connectClient(VICTIM);
  const attacker = await lab.connectClient(ATTACKER);

  const created = toolJson(
    await victim.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'svc-private' } })
  );
  // Identical bytes on the wire in both variants. Only the server-side key differs.
  const smuggled = `svc:${created.handle}`;
  return { created, attempt: await attacker.callTool({ name: 'read_scan', arguments: { handle: smuggled } }) };
}

test('T13 key binding: the concatenated composite key is ambiguous, the tuple key is not', async t => {
  const naive = createLabHarness({ mode: 'naive' });
  const hardened = createLabHarness({ mode: 'hardened' });
  t.after(() => Promise.all([naive.close(), hardened.close()]));

  const naiveRun = await attemptSmuggledLookup(naive);
  const hardenedRun = await attemptSmuggledLookup(hardened);

  // `tenant-a:svc` + H and `tenant-a` + `svc:H` concatenate to the same string:
  // ownership was checked, and the check still let the wrong principal through.
  assert.notEqual(naiveRun.attempt.isError, true);
  assert.equal(toolJson(naiveRun.attempt).owner, VICTIM);
  assert.equal(toolJson(naiveRun.attempt).note, 'svc-private');

  // ["tenant-a:svc", H] and ["tenant-a", "svc:H"] stay distinct keys.
  assert.equal(hardenedRun.attempt.isError, true);
  assert.equal(toolText(hardenedRun.attempt), 'scan not found');
  assert.ok(!toolText(hardenedRun.attempt).includes('svc-private'));

  t.diagnostic(
    JSON.stringify({
      victim: VICTIM,
      attacker: ATTACKER,
      naiveKeyCollision: true,
      tupleKeyCollision: false
    })
  );
});
