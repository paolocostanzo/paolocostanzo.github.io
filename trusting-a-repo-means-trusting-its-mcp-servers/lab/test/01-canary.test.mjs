// The canary must: log its start, answer the MCP handshake, expose one no-op tool,
// and write nothing except its own log.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const CANARY = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../canary/marker-server.mjs');

function runCanary(messages) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'canary-'));
  const log = path.join(dir, 'canary.log');
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CANARY], {
      cwd: dir,
      env: { PATH: process.env.PATH, HOME: dir, CANARY_LOG: log, CANARY_CLIENT: 'test-client', CANARY_SOURCE: 'test-source' },
    });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.on('error', reject);
    child.on('exit', () => {
      const replies = out.split('\n').filter(Boolean).map((l) => JSON.parse(l));
      const events = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
      resolve({ dir, log, replies, events, files: fs.readdirSync(dir) });
    });
    for (const m of messages) child.stdin.write(JSON.stringify(m) + '\n');
    child.stdin.end();
  });
}

test('logs its start and the handshake, tagged with client and source', async () => {
  const r = await runCanary([{ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }]);
  const names = r.events.map((e) => e.event);
  assert.deepEqual(names.slice(0, 2), ['start', 'initialize']);
  for (const e of r.events) {
    assert.equal(e.client, 'test-client');
    assert.equal(e.source, 'test-source');
    assert.ok(!Number.isNaN(Date.parse(e.ts)));
  }
});

test('answers initialize, tools/list and tools/call with a single no-op tool', async () => {
  const r = await runCanary([
    { jsonrpc: '2.0', id: 1, method: 'initialize', params: {} },
    { jsonrpc: '2.0', method: 'notifications/initialized' },
    { jsonrpc: '2.0', id: 2, method: 'tools/list' },
    { jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'canary_noop', arguments: {} } },
  ]);
  const byId = Object.fromEntries(r.replies.map((x) => [x.id, x]));
  assert.equal(byId[1].result.serverInfo.name, 'q5-canary');
  assert.equal(byId[2].result.tools.length, 1);
  assert.equal(byId[2].result.tools[0].name, 'canary_noop');
  assert.deepEqual(byId[3].result.content, [{ type: 'text', text: 'ok' }]);
  assert.ok(r.events.some((e) => e.event === 'tools/call'));
  assert.equal(r.replies.length, 3, 'notifications get no reply');
});

test('rejects unknown methods with method-not-found', async () => {
  const r = await runCanary([{ jsonrpc: '2.0', id: 9, method: 'resources/list' }]);
  assert.equal(r.replies[0].error.code, -32601);
});

test('writes nothing except its own log file', async () => {
  const r = await runCanary([{ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }]);
  assert.deepEqual(r.files, ['canary.log']);
});
