#!/usr/bin/env node
// Q3 lab server: does an MCP client notice when an approved tool's definition changes?
//
// A harmless stdio MCP server with one no-op tool whose definition switches from revision A to
// revision B when a local control file says so. Both revisions describe the same no-op behaviour
// in different words; neither contains instructions to the model, data access or network access.
// The tool's only effect is one line in this server's own log.
//
// Dual-era (MCP 2026-07-28, basic/versioning): a request carrying modern `_meta` is served
// statelessly under 2026-07-28 (server/discover, subscriptions/listen with acknowledgement,
// ttlMs on tools/list); an `initialize` request selects legacy semantics for the life of this
// process (tools.listChanged declared by the server, notifications on the initialized connection).
//
// Everything the server receives and sends is logged with a timestamp, together with which
// definition (A or B) each tools/list served, so that "B was served after the change" is a
// recorded fact and not an inference from the tool call.
//
// Environment:
//   Q3_CONTROL  JSON control file {state:"A"|"B", signal:"none"|"list_changed",
//               change:"description"|"schema"|"name", declareListChanged:bool, ttlMs:int, cell:str}
//   Q3_LOG      JSONL log (default ~/q3-server.log)
//   Q3_CLIENT   label of the client under test (written into every log line)
// It opens no network connection and writes no file other than Q3_LOG.
import fs from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';

export const SERVER_INFO = { name: 'q3-lab', version: '1.0.0' };
export const MODERN = '2026-07-28';
export const LEGACY = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'];
const META = 'io.modelcontextprotocol/';

const NO_ARGS = { type: 'object', properties: {}, additionalProperties: false };
export const DEFINITIONS = {
  A: {
    name: 'q3_noop',
    description: 'Lab tool, revision A. Returns the word ok and records the call in its own log. It has no other effect.',
    inputSchema: NO_ARGS,
  },
  B: {
    description: {
      name: 'q3_noop',
      description: 'Lab tool, revision B. Records the call in its own log and returns the word ok. Nothing else happens.',
      inputSchema: NO_ARGS,
    },
    schema: {
      name: 'q3_noop',
      description: 'Lab tool, revision A. Returns the word ok and records the call in its own log. It has no other effect.',
      inputSchema: {
        type: 'object',
        properties: { note: { type: 'string', description: 'Optional free text. It is ignored.' } },
        additionalProperties: false,
      },
    },
    name: {
      name: 'q3_noop_renamed',
      description: 'Lab tool, revision A. Returns the word ok and records the call in its own log. It has no other effect.',
      inputSchema: NO_ARGS,
    },
  },
};

export const DEFAULT_CONTROL = {
  state: 'A', signal: 'none', change: 'description', declareListChanged: true, ttlMs: 0, cell: '',
};

export function toolsFor(control) {
  const t = control.state === 'B' ? DEFINITIONS.B[control.change] || DEFINITIONS.B.description : DEFINITIONS.A;
  return [structuredClone(t)];
}

export function defHash(tools) {
  return crypto.createHash('sha256').update(JSON.stringify(tools)).digest('hex');
}

// ------------------------------------------------------------------ runtime
const LOG = process.env.Q3_LOG || `${os.homedir()}/q3-server.log`;
const CONTROL = process.env.Q3_CONTROL || '';
const CLIENT = process.env.Q3_CLIENT || 'unknown';
let seq = 0;

function record(event, extra = {}) {
  const line = JSON.stringify({
    ts: new Date().toISOString(), seq: seq++, pid: process.pid, ppid: process.ppid, client: CLIENT,
    cell: control.cell, state: control.state, event, ...extra,
  }) + '\n';
  try { fs.appendFileSync(LOG, line); } catch { /* never fail loudly */ }
}

function readControl() {
  if (!CONTROL) return { ...DEFAULT_CONTROL };
  try {
    const c = JSON.parse(fs.readFileSync(CONTROL, 'utf8'));
    return { ...DEFAULT_CONTROL, ...c };
  } catch (e) {
    return null;
  }
}

let control = readControl() || { ...DEFAULT_CONTROL };
const session = {
  legacy: null,          // { protocolVersion, requested, clientInfo, capabilities, initializedAt }
  modernSeen: false,
  subscriptions: new Map(), // id -> { toolsListChanged }
};

function send(obj, why) {
  const text = JSON.stringify(obj);
  process.stdout.write(text + '\n');
  record('out', { why, msg: obj });
}

const ok = (id, result) => ({ jsonrpc: '2.0', id, result });
const err = (id, code, message, data) => ({ jsonrpc: '2.0', id, error: data ? { code, message, data } : { code, message } });

function modernVersion(params) {
  const m = params && params._meta;
  return m && typeof m === 'object' ? m[`${META}protocolVersion`] : undefined;
}

function listResult(modern) {
  const tools = toolsFor(control);
  const result = { tools };
  if (modern) Object.assign(result, { resultType: 'complete', ttlMs: control.ttlMs, cacheScope: 'public' });
  return { result, tools, hash: defHash(tools) };
}

function callTool(id, params, modern) {
  const name = params && params.name;
  const tools = toolsFor(control);
  const known = tools.some((t) => t.name === name);
  record('tools/call', { called: name, known, served_state: control.state, arguments: params && params.arguments });
  if (!known) {
    send(err(id, -32602, `Unknown tool: ${name}`), 'tools/call unknown');
    return;
  }
  const result = { content: [{ type: 'text', text: 'ok' }], isError: false };
  if (modern) result.resultType = 'complete';
  send(ok(id, result), 'tools/call');
}

export function handle(msg) {
  if (Array.isArray(msg)) {
    record('batch-rejected', { size: msg.length });
    send(err(null, -32600, 'Batches are not supported'), 'batch');
    return;
  }
  const { id, method, params } = msg || {};
  const isRequest = id !== undefined && typeof method === 'string';
  const isNotification = id === undefined && typeof method === 'string';
  const mv = modernVersion(params);

  if (isNotification) {
    if (method === 'notifications/initialized' && session.legacy) {
      session.legacy.initializedAt = new Date().toISOString();
      record('legacy-initialized');
    } else if (method === 'notifications/cancelled') {
      const rid = params && (params.requestId ?? params.id);
      if (session.subscriptions.has(rid)) {
        session.subscriptions.delete(rid);
        record('subscription-cancelled', { subscriptionId: rid });
      }
    }
    return;
  }
  if (!isRequest) return; // responses from the client are not expected; already logged as 'in'

  // ---- modern request (per-request _meta)
  if (mv !== undefined) {
    session.modernSeen = true;
    const meta = params._meta;
    record('modern-request', {
      method, protocolVersion: mv,
      clientInfo: meta[`${META}clientInfo`], clientCapabilities: meta[`${META}clientCapabilities`],
    });
    if (mv !== MODERN) {
      send(err(id, -32022, 'Unsupported protocol version', { supported: [MODERN], requested: mv }), 'unsupported version');
      return;
    }
    if (method === 'server/discover') {
      send(ok(id, {
        resultType: 'complete',
        supportedVersions: [MODERN],
        capabilities: { tools: control.declareListChanged ? { listChanged: true } : {} },
        _meta: { [`${META}serverInfo`]: SERVER_INFO },
        ttlMs: 0,
        cacheScope: 'public',
      }), 'server/discover');
    } else if (method === 'tools/list') {
      const { result, tools, hash } = listResult(true);
      record('served-list', { era: 'modern', served: control.state, names: tools.map((t) => t.name), def_sha256: hash, ttlMs: control.ttlMs });
      send(ok(id, result), 'tools/list');
    } else if (method === 'tools/call') {
      callTool(id, params, true);
    } else if (method === 'subscriptions/listen') {
      const wants = !!(params.notifications && params.notifications.toolsListChanged);
      const agreed = wants && control.declareListChanged ? { toolsListChanged: true } : {};
      session.subscriptions.set(id, { toolsListChanged: !!agreed.toolsListChanged });
      record('subscription-opened', { subscriptionId: id, requested: params.notifications || {}, agreed });
      send({ jsonrpc: '2.0', method: 'notifications/subscriptions/acknowledged',
        params: { _meta: { [`${META}subscriptionId`]: id }, notifications: agreed } }, 'subscription ack');
      // The request stays open: no response until the stream ends.
    } else if (method === 'ping') {
      send(ok(id, { resultType: 'complete' }), 'ping');
    } else {
      send(err(id, -32601, `Method not found: ${method}`), 'unknown modern method');
    }
    return;
  }

  // ---- legacy handshake
  if (method === 'initialize') {
    const requested = params && params.protocolVersion;
    const negotiated = LEGACY.includes(requested) ? requested : LEGACY[0];
    session.legacy = {
      requested, protocolVersion: negotiated,
      clientInfo: params && params.clientInfo, capabilities: params && params.capabilities, initializedAt: null,
    };
    record('legacy-initialize', { requested, negotiated, clientInfo: session.legacy.clientInfo, clientCapabilities: session.legacy.capabilities });
    send(ok(id, {
      protocolVersion: negotiated,
      capabilities: { tools: control.declareListChanged ? { listChanged: true } : {} },
      serverInfo: SERVER_INFO,
    }), 'initialize');
    return;
  }
  if (!session.legacy) {
    record('pre-initialize-request', { method });
    send(err(id, -32600, 'Send initialize (legacy) or include _meta protocolVersion 2026-07-28'), 'pre-initialize');
    return;
  }
  if (method === 'tools/list') {
    const { result, tools, hash } = listResult(false);
    record('served-list', { era: 'legacy', served: control.state, names: tools.map((t) => t.name), def_sha256: hash });
    send(ok(id, result), 'tools/list');
  } else if (method === 'tools/call') {
    callTool(id, params, false);
  } else if (method === 'ping') {
    send(ok(id, {}), 'ping');
  } else {
    send(err(id, -32601, `Method not found: ${method}`), 'unknown legacy method');
  }
}

// ------------------------------------------------------------------ change signal
function onControlChange() {
  const next = readControl();
  if (!next) { record('control-unreadable'); return; }
  const before = control.state;
  control = next;
  if (before === control.state) return;
  const tools = toolsFor(control);
  record('definition-changed', { from: before, to: control.state, change: control.change, signal: control.signal,
    names: tools.map((t) => t.name), def_sha256: defHash(tools) });
  if (control.signal !== 'list_changed') return;
  const delivered = [];
  if (session.legacy && control.declareListChanged) {
    send({ jsonrpc: '2.0', method: 'notifications/tools/list_changed' }, 'list_changed legacy');
    delivered.push({ era: 'legacy', initialized: !!session.legacy.initializedAt });
  }
  for (const [sid, sub] of session.subscriptions) {
    if (!sub.toolsListChanged) continue;
    send({ jsonrpc: '2.0', method: 'notifications/tools/list_changed',
      params: { _meta: { [`${META}subscriptionId`]: sid } } }, 'list_changed modern');
    delivered.push({ era: 'modern', subscriptionId: sid });
  }
  record(delivered.length ? 'signal-delivered' : 'signal-not-deliverable', {
    delivered, legacy: !!session.legacy, modernSeen: session.modernSeen,
    subscriptions: [...session.subscriptions.keys()], declareListChanged: control.declareListChanged,
  });
}

// ------------------------------------------------------------------ main
if (import.meta.url === `file://${process.argv[1]}`) {
  record('start', { control_file: CONTROL, control, argv: process.argv.slice(2), node: process.version });
  if (CONTROL) fs.watchFile(CONTROL, { interval: 200 }, onControlChange);
  let buf = '';
  process.stdin.on('data', (chunk) => {
    buf += chunk.toString('utf8');
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { record('in-unparseable', { raw: line.slice(0, 500) }); continue; }
      record('in', { msg });
      try { handle(msg); } catch (e) { record('handler-error', { error: String(e && e.stack || e) }); }
    }
  });
  const stop = (why) => { record(why, { subscriptions_open: [...session.subscriptions.keys()] }); process.exit(0); };
  process.stdin.on('end', () => stop('stdin-closed'));
  process.on('SIGTERM', () => stop('sigterm'));
  process.on('SIGINT', () => stop('sigint'));
}
