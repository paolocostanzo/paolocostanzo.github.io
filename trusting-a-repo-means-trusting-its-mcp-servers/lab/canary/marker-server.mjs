#!/usr/bin/env node
// Canary MCP server for the "trusting a repo" lab.
// It records that it was started (one JSON line in its own log), answers a
// minimal MCP stdio handshake and exposes one tool that does nothing.
// It opens no network connection and writes no other file.
import fs from 'node:fs';
import os from 'node:os';

const LOG = process.env.CANARY_LOG || `${os.homedir()}/canary-start.log`;
const CLIENT = process.env.CANARY_CLIENT || 'unknown';
const SOURCE = process.env.CANARY_SOURCE || 'unknown';

function record(event) {
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    event,
    client: CLIENT,
    source: SOURCE,
    pid: process.pid,
    ppid: process.ppid,
  }) + '\n';
  try { fs.appendFileSync(LOG, line); } catch { /* never fail loudly */ }
}

record('start');

const send = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

function handle({ id, method }) {
  if (method === 'initialize') {
    record('initialize');
    send({ jsonrpc: '2.0', id, result: {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'q5-canary', version: '1.1.0' },
    } });
  } else if (method === 'tools/list') {
    send({ jsonrpc: '2.0', id, result: { tools: [{
      name: 'canary_noop',
      description: 'Records that it was called and returns ok. Does nothing else.',
      inputSchema: { type: 'object', properties: {} },
    }] } });
  } else if (method === 'tools/call') {
    record('tools/call');
    send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: 'ok' }] } });
  } else if (method === 'ping') {
    send({ jsonrpc: '2.0', id, result: {} });
  } else if (id !== undefined) {
    send({ jsonrpc: '2.0', id, error: { code: -32601, message: 'method not found' } });
  }
}

let buf = '';
process.stdin.on('data', (chunk) => {
  buf += chunk.toString('utf8');
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    handle(msg);
  }
});
process.stdin.on('end', () => { record('stdin-closed'); process.exit(0); });
process.on('SIGTERM', () => { record('sigterm'); process.exit(0); });
process.on('SIGINT', () => { record('sigint'); process.exit(0); });
