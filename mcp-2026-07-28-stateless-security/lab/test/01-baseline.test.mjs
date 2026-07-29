import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createLabHarness, MODERN_REVISION, TOKENS } from '../src/harness.mjs';

test('T01 baseline: the official client uses the modern revision without a protocol session', async t => {
  const lab = createLabHarness({ mode: 'hardened' });
  t.after(() => lab.close());
  const systemFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error('network access is forbidden in this lab');
  };
  t.after(() => {
    globalThis.fetch = systemFetch;
  });

  const alice = await lab.connectClient('alice');
  await alice.callTool({ name: 'create_scan', arguments: { target: 'demo.local', note: 'alice-private' } });

  assert.equal(alice.getProtocolEra(), 'modern');
  assert.equal(alice.getNegotiatedProtocolVersion(), MODERN_REVISION);

  const methods = lab.records.map(record => record.requestBody?.method).filter(Boolean);
  assert.ok(methods.includes('server/discover'));
  assert.ok(methods.includes('tools/call'));
  assert.ok(!methods.includes('initialize'));
  assert.ok(lab.records.every(record => !('mcp-session-id' in record.requestHeaders)));
  assert.ok(lab.records.every(record => !('mcp-session-id' in record.responseHeaders)));

  const toolCall = lab.records.find(record => record.requestBody?.method === 'tools/call');
  assert.equal(toolCall.requestHeaders['mcp-protocol-version'], MODERN_REVISION);
  assert.equal(toolCall.requestHeaders['mcp-method'], 'tools/call');
  assert.equal(toolCall.requestHeaders['mcp-name'], 'create_scan');
  assert.equal(toolCall.requestBody.params._meta['io.modelcontextprotocol/protocolVersion'], MODERN_REVISION);
  assert.deepEqual(toolCall.requestBody.params._meta['io.modelcontextprotocol/clientCapabilities'], {});
  assert.deepEqual(toolCall.requestBody.params._meta['io.modelcontextprotocol/clientInfo'], {
    name: 'alice-lab-client',
    version: '1.0.0'
  });
  assert.equal(toolCall.requestHeaders.authorization, '[REDACTED]');
  assert.ok(!JSON.stringify(lab.records).includes(TOKENS.alice));

  t.diagnostic(
    JSON.stringify({
      era: 'modern',
      revision: MODERN_REVISION,
      methods,
      toolCall: {
        headerVersion: toolCall.requestHeaders['mcp-protocol-version'],
        headerMethod: toolCall.requestHeaders['mcp-method'],
        headerName: toolCall.requestHeaders['mcp-name'],
        envelopeMethod: toolCall.requestBody.method,
        envelopeName: toolCall.requestBody.params.name,
        envelopeVersion: toolCall.requestBody.params._meta['io.modelcontextprotocol/protocolVersion']
      },
      initializeSeen: false,
      sessionIdSeen: false,
      externalFetchBlocked: true
    })
  );
});
