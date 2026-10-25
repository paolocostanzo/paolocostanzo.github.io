// The Q3 server must: answer both eras correctly, serve definition A then B after the control file
// changes, record which definition each tools/list served, send list_changed only where it can be
// delivered (legacy: declared by the server on an initialized connection; modern: acknowledged
// subscription), and write nothing but its own log.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.resolve(HERE, '../server/q3-server.mjs');
const FLIP = path.resolve(HERE, '../scripts/flip.mjs');
const M = 'io.modelcontextprotocol/';
const meta = (v = '2026-07-28') => ({ _meta: { [`${M}protocolVersion`]: v, [`${M}clientInfo`]: { name: 't', version: '0' }, [`${M}clientCapabilities`]: {} } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Starts the server, sends `before`, optionally flips to B, sends `after`, closes stdin.
async function session({ before = [], after = [], init = [], flip = true, waitMs = 700 }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'q3-'));
  const log = path.join(dir, 'q3.log');
  const control = path.join(dir, 'control.json');
  execFileSync(process.execPath, [FLIP, '--control', control, '--log', log, '--init', ...init]);
  const child = spawn(process.execPath, [SERVER], {
    cwd: dir, env: { PATH: process.env.PATH, HOME: dir, Q3_LOG: log, Q3_CONTROL: control, Q3_CLIENT: 'test' },
  });
  let out = '';
  child.stdout.on('data', (d) => { out += d; });
  const write = (m) => child.stdin.write(JSON.stringify(m) + '\n');
  await sleep(150);
  for (const m of before) write(m);
  await sleep(300);
  if (flip) execFileSync(process.execPath, [FLIP, '--control', control, '--log', log, '--to', 'B']);
  await sleep(waitMs);
  for (const m of after) write(m);
  await sleep(300);
  const exited = new Promise((r) => child.on('exit', r));
  child.stdin.end();
  await exited;
  const lines = out.split('\n').filter(Boolean);
  const replies = lines.map((l) => JSON.parse(l)); // throws if stdout carries anything but JSON lines
  const events = fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  return { dir, replies, events, files: fs.readdirSync(dir).sort(), byId: (id) => replies.find((r) => r.id === id) };
}

const legacyInit = (v = '2025-06-18') => [
  { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: v, capabilities: {}, clientInfo: { name: 'legacy-client', version: '1' } } },
  { jsonrpc: '2.0', method: 'notifications/initialized' },
];

test('legacy: negotiates the requested legacy revision and declares tools.listChanged (server capability)', async () => {
  const r = await session({ before: legacyInit('2025-06-18'), flip: false });
  const res = r.byId(1).result;
  assert.equal(res.protocolVersion, '2025-06-18');
  assert.deepEqual(res.capabilities, { tools: { listChanged: true } });
  assert.equal(res.serverInfo.name, 'q3-lab');
  assert.ok(r.events.some((e) => e.event === 'legacy-initialized'));
});

test('legacy: unknown requested revision falls back to the latest legacy revision', async () => {
  const r = await session({ before: legacyInit('2099-01-01'), flip: false });
  assert.equal(r.byId(1).result.protocolVersion, '2025-11-25');
});

test('legacy: serves A before the change and B after it, and logs which one with its hash', async () => {
  const r = await session({
    before: [...legacyInit(), { jsonrpc: '2.0', id: 2, method: 'tools/list' }],
    after: [{ jsonrpc: '2.0', id: 3, method: 'tools/list' }, { jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'q3_noop', arguments: {} } }],
  });
  assert.match(r.byId(2).result.tools[0].description, /revision A/);
  assert.match(r.byId(3).result.tools[0].description, /revision B/);
  assert.equal(r.byId(2).result.ttlMs, undefined, 'legacy results carry no modern cache fields');
  const served = r.events.filter((e) => e.event === 'served-list');
  assert.deepEqual(served.map((e) => e.served), ['A', 'B']);
  assert.notEqual(served[0].def_sha256, served[1].def_sha256);
  assert.equal(r.byId(4).result.content[0].text, 'ok');
  const call = r.events.find((e) => e.event === 'tools/call');
  assert.equal(call.served_state, 'B');
});

test('legacy: list_changed is sent after the change only at the list_changed level', async () => {
  const withSignal = await session({ init: ['--signal', 'list_changed'], before: legacyInit() });
  const notes = withSignal.replies.filter((m) => m.method === 'notifications/tools/list_changed');
  assert.equal(notes.length, 1);
  const d = withSignal.events.find((e) => e.event === 'signal-delivered');
  assert.deepEqual(d.delivered, [{ era: 'legacy', initialized: true }]);

  const none = await session({ init: ['--signal', 'none'], before: legacyInit() });
  assert.equal(none.replies.filter((m) => m.method === 'notifications/tools/list_changed').length, 0);
  assert.ok(none.events.some((e) => e.event === 'definition-changed'));
});

test('legacy: with listChanged not declared, the signal is recorded as not deliverable', async () => {
  const r = await session({ init: ['--signal', 'list_changed', '--no-list-changed'], before: legacyInit() });
  assert.deepEqual(r.byId(1).result.capabilities, { tools: {} });
  assert.equal(r.replies.filter((m) => m.method === 'notifications/tools/list_changed').length, 0);
  assert.ok(r.events.some((e) => e.event === 'signal-not-deliverable'));
});

test('a request before initialize and without modern _meta is refused and logged', async () => {
  const r = await session({ before: [{ jsonrpc: '2.0', id: 9, method: 'tools/list' }], flip: false });
  assert.equal(r.byId(9).error.code, -32600);
  assert.ok(r.events.some((e) => e.event === 'pre-initialize-request'));
});

test('modern: server/discover answers with versions, capabilities, serverInfo and cache hints', async () => {
  const r = await session({ before: [{ jsonrpc: '2.0', id: 'd1', method: 'server/discover', params: meta() }], flip: false });
  const res = r.byId('d1').result;
  assert.equal(res.resultType, 'complete');
  assert.deepEqual(res.supportedVersions, ['2026-07-28']);
  assert.deepEqual(res.capabilities, { tools: { listChanged: true } });
  assert.equal(res._meta[`${M}serverInfo`].name, 'q3-lab');
  assert.equal(res.ttlMs, 0);
});

test('modern: an unsupported version gets UnsupportedProtocolVersionError (-32022) with the supported list', async () => {
  const r = await session({ before: [{ jsonrpc: '2.0', id: 5, method: 'tools/list', params: meta('1900-01-01') }], flip: false });
  assert.equal(r.byId(5).error.code, -32022);
  assert.deepEqual(r.byId(5).error.data, { supported: ['2026-07-28'], requested: '1900-01-01' });
});

test('modern: tools/list carries resultType, ttlMs 0 and cacheScope, and switches A -> B', async () => {
  const r = await session({
    before: [{ jsonrpc: '2.0', id: 1, method: 'tools/list', params: meta() }],
    after: [{ jsonrpc: '2.0', id: 2, method: 'tools/list', params: meta() }],
  });
  const a = r.byId(1).result;
  assert.equal(a.resultType, 'complete');
  assert.equal(a.ttlMs, 0);
  assert.equal(a.cacheScope, 'public');
  assert.match(a.tools[0].description, /revision A/);
  assert.match(r.byId(2).result.tools[0].description, /revision B/);
});

test('modern: subscription is acknowledged first, then list_changed carries the subscription id', async () => {
  const r = await session({
    init: ['--signal', 'list_changed'],
    before: [{ jsonrpc: '2.0', id: 7, method: 'subscriptions/listen', params: { ...meta(), notifications: { toolsListChanged: true } } }],
  });
  const msgs = r.replies.filter((m) => m.method);
  assert.equal(msgs[0].method, 'notifications/subscriptions/acknowledged');
  assert.equal(msgs[0].params._meta[`${M}subscriptionId`], 7);
  assert.deepEqual(msgs[0].params.notifications, { toolsListChanged: true });
  assert.equal(msgs[1].method, 'notifications/tools/list_changed');
  assert.equal(msgs[1].params._meta[`${M}subscriptionId`], 7);
  assert.equal(r.byId(7), undefined, 'the listen request stays open while the stream is active');
});

test('modern: without a toolsListChanged subscription the signal is not sent and is logged as not deliverable', async () => {
  const r = await session({
    init: ['--signal', 'list_changed'],
    before: [{ jsonrpc: '2.0', id: 7, method: 'subscriptions/listen', params: { ...meta(), notifications: { resourcesListChanged: true } } }],
  });
  const ack = r.replies.find((m) => m.method === 'notifications/subscriptions/acknowledged');
  assert.deepEqual(ack.params.notifications, {}, 'only the agreed subset is acknowledged');
  assert.equal(r.replies.filter((m) => m.method === 'notifications/tools/list_changed').length, 0);
  assert.ok(r.events.some((e) => e.event === 'signal-not-deliverable'));
});

test('modern: a cancelled subscription receives nothing after cancellation', async () => {
  const r = await session({
    init: ['--signal', 'list_changed'],
    before: [
      { jsonrpc: '2.0', id: 7, method: 'subscriptions/listen', params: { ...meta(), notifications: { toolsListChanged: true } } },
      { jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: 7 } },
    ],
  });
  assert.equal(r.replies.filter((m) => m.method === 'notifications/tools/list_changed').length, 0);
  assert.ok(r.events.some((e) => e.event === 'subscription-cancelled'));
});

test('schema and name changes: B adds an optional field, or renames the tool and refuses the old name', async () => {
  const s = await session({ init: ['--change', 'schema'], before: legacyInit(), after: [{ jsonrpc: '2.0', id: 3, method: 'tools/list' }] });
  assert.deepEqual(Object.keys(s.byId(3).result.tools[0].inputSchema.properties), ['note']);
  const n = await session({
    init: ['--change', 'name'], before: legacyInit(),
    after: [{ jsonrpc: '2.0', id: 3, method: 'tools/list' }, { jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'q3_noop' } }],
  });
  assert.equal(n.byId(3).result.tools[0].name, 'q3_noop_renamed');
  assert.equal(n.byId(4).error.code, -32602);
  assert.equal(n.events.find((e) => e.event === 'tools/call').known, false);
});

test('writes nothing but its own log and the control file, and logs every message in and out', async () => {
  const r = await session({ before: [...legacyInit(), { jsonrpc: '2.0', id: 2, method: 'tools/list' }] });
  assert.deepEqual(r.files, ['control.json', 'q3.log']);
  const ins = r.events.filter((e) => e.event === 'in').length;
  const outs = r.events.filter((e) => e.event === 'out').length;
  assert.equal(ins, 3);
  assert.equal(outs, 2);
  assert.ok(r.events.some((e) => e.event === 'control-flip'));
  assert.ok(r.events.some((e) => e.event === 'stdin-closed'));
});

test('safety boundary: both revisions are plain descriptions with no URL, path or instruction to the model', async () => {
  const { DEFINITIONS } = await import(SERVER);
  const all = [DEFINITIONS.A, ...Object.values(DEFINITIONS.B)];
  for (const t of all) {
    assert.ok(t.description.length < 140);
    assert.doesNotMatch(t.description, /https?:|\/|ignore|instruction|must|always|secret|password|token|file|read|send/i);
  }
});
