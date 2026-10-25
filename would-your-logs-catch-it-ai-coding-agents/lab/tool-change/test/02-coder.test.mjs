// The rule-based coder (scripts/code_q3.py) must give the D12 outcome, with its precedence, for each
// client behaviour. Each case drives the real server with a scripted client and writes a repetition
// directory exactly as run_q3_cell.py does (row.json + q3-server.log).
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
const CODER = path.resolve(HERE, '../scripts/code_q3.py');
const M = 'io.modelcontextprotocol/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const iso = () => new Date().toISOString();

const init = [
  { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'scripted', version: '1' } } },
  { jsonrpc: '2.0', method: 'notifications/initialized' },
  { jsonrpc: '2.0', id: 2, method: 'tools/list' },
  { jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'q3_noop', arguments: {} } },
];
const list = (id) => ({ jsonrpc: '2.0', id, method: 'tools/list' });
const call = (id, name = 'q3_noop') => ({ jsonrpc: '2.0', id, method: 'tools/call', params: { name, arguments: {} } });

// after: messages sent after the change; close: close stdin right after the change;
// row: what the runner observed on screen (approvals, notices)
async function repetition({ signal = 'none', change = 'description', after = [], close = false, row = {}, phase1 = init, modern = false }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'q3c-'));
  const log = path.join(dir, 'q3-server.log');
  const control = path.join(dir, 'q3-control.json');
  const t_start = iso();
  execFileSync(process.execPath, [FLIP, '--control', control, '--log', log, '--init', '--signal', signal, '--change', change, '--cell', 'test']);
  const child = spawn(process.execPath, [SERVER], { env: { PATH: process.env.PATH, HOME: dir, Q3_LOG: log, Q3_CONTROL: control, Q3_CLIENT: 'scripted' } });
  const exited = new Promise((r) => child.on('exit', r));
  const write = (m) => child.stdin.write(JSON.stringify(m) + '\n');
  await sleep(150);
  for (const m of phase1) write(m);
  await sleep(300);
  const flip_at = iso();
  execFileSync(process.execPath, [FLIP, '--control', control, '--log', log, '--to', 'B']);
  await sleep(600);
  let p2_end_at;
  if (close) { child.stdin.end(); await exited; await sleep(50); p2_end_at = iso(); }
  else {
    for (const m of after) write(m);
    await sleep(300);
    p2_end_at = iso(); // the runner records the end of phase 2 before it closes the client
    child.stdin.end();
  }
  await exited;
  await sleep(50);
  const t_end = iso();
  fs.writeFileSync(path.join(dir, 'row.json'), JSON.stringify({
    client: 'scripted', signal, change, rep: 1, t_start, flip_at, p2_end_at, t_end,
    p1_approvals: 1, p2_approvals: 0, p2_notice_seen: false, ...row,
  }));
  const out = execFileSync('python3', ['-I', CODER, dir], { encoding: 'utf8' });
  return JSON.parse(out.trim().split('\n')[0]);
}

test('re-lists after the change and calls: silent accept, B served', async () => {
  const r = await repetition({ after: [list(10), call(11)] });
  assert.equal(r.p1_ok, true);
  assert.equal(r.p2_outcome, 'silent-accept');
  assert.equal(r.p2_lists_served, 'B');
  assert.equal(r.p2_signal_delivered, 'n-a');
});

test('calls without listing again: stale', async () => {
  const r = await repetition({ after: [call(11)] });
  assert.equal(r.p2_outcome, 'stale');
  assert.equal(r.p2_lists_served, '');
});

test('asks for permission again: re-approval required, even if it then calls', async () => {
  const r = await repetition({ after: [list(10), call(11)], row: { p2_approvals: 1 } });
  assert.equal(r.p2_outcome, 're-approval-required');
});

test('shows a change message without asking: notified', async () => {
  const r = await repetition({ after: [list(10), call(11)], row: { p2_notice_seen: true } });
  assert.equal(r.p2_outcome, 'notified');
});

test('disconnects the server after the change without calling: refused or disconnected (takes precedence)', async () => {
  const r = await repetition({ close: true, row: { p2_approvals: 1 } });
  assert.equal(r.p2_outcome, 'refused-or-disconnected');
});

test('never calls after the change: error, no call or timeout', async () => {
  const r = await repetition({ after: [list(10)] });
  assert.equal(r.p2_outcome, 'error-no-call-timeout');
});

test('name change with a stale list: the old name is unknown, coded as error', async () => {
  const r = await repetition({ change: 'name', after: [call(11, 'q3_noop')] });
  assert.equal(r.p2_outcome, 'error-no-call-timeout');
});

test('list_changed level: delivery on the legacy connection is recorded', async () => {
  const r = await repetition({ signal: 'list_changed', after: [list(10), call(11)] });
  assert.equal(r.p2_signal_delivered, 'yes');
  assert.equal(r.p2_channels, 'legacy');
});

test('modern client without a subscription: signal coded as not deliverable', async () => {
  const meta = { _meta: { [`${M}protocolVersion`]: '2026-07-28', [`${M}clientInfo`]: { name: 'm', version: '1' }, [`${M}clientCapabilities`]: {} } };
  const r = await repetition({
    signal: 'list_changed', modern: true,
    phase1: [{ jsonrpc: '2.0', id: 1, method: 'tools/list', params: meta }, { jsonrpc: '2.0', id: 2, method: 'tools/call', params: { ...meta, name: 'q3_noop', arguments: {} } }],
    after: [{ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { ...meta, name: 'q3_noop', arguments: {} } }],
  });
  assert.equal(r.eras, 'modern');
  assert.equal(r.p2_signal_delivered, 'no');
  assert.equal(r.p2_outcome, 'stale');
});

test('phase 1 without a successful call: the repetition is invalid', async () => {
  const r = await repetition({ phase1: init.slice(0, 3), after: [list(10), call(11)] });
  assert.equal(r.p1_ok, false);
  assert.equal(r.p2_outcome, 'invalid-setup');
});

test('D19: a phase-2 question caused by the client classifier being unavailable is invalid-external, not re-approval', async () => {
  const r = await repetition({ after: [list(10), call(11)], row: { p2_approvals: 1, p2_classifier_unavailable: true } });
  assert.equal(r.p2_outcome, 'invalid-external');
});
