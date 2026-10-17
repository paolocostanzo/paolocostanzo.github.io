// Conformance check of the Q3 server against the official MCP TypeScript client SDKs:
// v1 (@modelcontextprotocol/sdk 1.32.1, legacy-only) and v2 (@modelcontextprotocol/client 2.3.1, dual-era).
// For each SDK and signal level: connect over stdio, list (A), call, flip to B, wait, list again, call again.
// Usage: node conformance.mjs <node_modules dir> <server.mjs> <flip.mjs> <out dir>
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [NM, SERVER, FLIP, OUT] = process.argv.slice(2);
const imp = (rel) => import(pathToFileURL(path.join(NM, rel)).href);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sdkV1() {
  const { Client } = await imp('@modelcontextprotocol/sdk/dist/esm/client/index.js');
  const { StdioClientTransport } = await imp('@modelcontextprotocol/sdk/dist/esm/client/stdio.js');
  const { ToolListChangedNotificationSchema } = await imp('@modelcontextprotocol/sdk/dist/esm/types.js');
  return { name: 'sdk-v1-1.32.1', Client, StdioClientTransport, ToolListChangedNotificationSchema };
}
async function sdkV2() {
  const { Client } = await imp('@modelcontextprotocol/client/dist/index.mjs');
  const { StdioClientTransport } = await imp('@modelcontextprotocol/client/dist/stdio.mjs');
  return { name: 'client-v2-2.3.1', Client, StdioClientTransport };
}
const v2mode = (mode, label) => async () => ({ ...(await sdkV2()), name: `client-v2-2.3.1-${label}`, versionNegotiation: { mode } });

async function session(sdk, signal) {
  const dir = path.join(OUT, `${sdk.name}__${signal}`);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  const log = path.join(dir, 'q3-server.log');
  const control = path.join(dir, 'control.json');
  execFileSync(process.execPath, [FLIP, '--control', control, '--log', log, '--init', '--signal', signal, '--cell', `${sdk.name}:${signal}`]);
  const changed = [];
  const opts = { capabilities: {} };
  if (sdk.versionNegotiation) opts.versionNegotiation = sdk.versionNegotiation;
  if (!sdk.ToolListChangedNotificationSchema) {
    opts.listChanged = { tools: { debounceMs: 0, onChanged: (err, tools) => changed.push({ err: err && String(err), tools: tools && tools.map((t) => t.description) }) } };
  }
  const client = new sdk.Client({ name: `otp-conformance-${sdk.name}`, version: '1.0.0' }, opts);
  if (sdk.ToolListChangedNotificationSchema) {
    client.setNotificationHandler(sdk.ToolListChangedNotificationSchema, () => changed.push({ notification: true }));
  }
  const transport = new sdk.StdioClientTransport({ command: process.execPath, args: [SERVER],
    env: { PATH: process.env.PATH, HOME: dir, Q3_LOG: log, Q3_CONTROL: control, Q3_CLIENT: sdk.name } });
  const r = { sdk: sdk.name, signal };
  try {
    await client.connect(transport);
    r.server_version = client.getServerVersion?.();
    r.server_capabilities = client.getServerCapabilities?.();
    r.negotiated = client.getNegotiatedProtocolVersion?.() ?? transport.protocolVersion ?? null;
    r.list1 = (await client.listTools()).tools.map((t) => t.description);
    r.call1 = (await client.callTool({ name: 'q3_noop', arguments: {} })).content?.[0]?.text;
    await sleep(300);
    execFileSync(process.execPath, [FLIP, '--control', control, '--log', log, '--to', 'B']);
    await sleep(1500);
    r.changed_events = changed;
    r.list2 = (await client.listTools()).tools.map((t) => t.description);
    r.call2 = (await client.callTool({ name: 'q3_noop', arguments: {} })).content?.[0]?.text;
  } catch (e) {
    r.error = String(e && e.stack || e).slice(0, 800);
  } finally {
    try { await client.close(); } catch { /* ignore */ }
  }
  await sleep(200);
  const ev = fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  r.server_events = ev.filter((e) => !['in', 'out'].includes(e.event)).map((e) => ({ event: e.event, ...(e.served ? { served: e.served } : {}),
    ...(e.method ? { method: e.method } : {}), ...(e.negotiated ? { negotiated: e.negotiated, requested: e.requested } : {}),
    ...(e.delivered ? { delivered: e.delivered } : {}), ...(e.requested && !e.negotiated ? { requested: e.requested } : {}),
    ...(e.protocolVersion ? { protocolVersion: e.protocolVersion } : {}) }));
  r.methods_in = ev.filter((e) => e.event === 'in').map((e) => e.msg.method || (e.msg.result ? 'result' : 'other'));
  fs.writeFileSync(path.join(dir, 'result.json'), JSON.stringify(r, null, 2));
  return r;
}

const results = [];
for (const load of [sdkV1, sdkV2, v2mode('auto', 'auto'), v2mode({ pin: '2026-07-28' }, 'pin')]) {
  const sdk = await load();
  for (const signal of ['none', 'list_changed']) results.push(await session(sdk, signal));
}
fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(results, null, 2));
for (const r of results) {
  console.log(JSON.stringify({ sdk: r.sdk, signal: r.signal, negotiated: r.negotiated, error: r.error,
    list1: r.list1?.[0]?.slice(0, 25), list2: r.list2?.[0]?.slice(0, 25), call1: r.call1, call2: r.call2,
    changed_events: r.changed_events?.length, methods_in: [...new Set(r.methods_in)].join(','),
    signal_events: r.server_events.filter((e) => e.event.startsWith('signal') || e.event.startsWith('subscription')).map((e) => e.event + (e.delivered ? ':' + e.delivered.map((d) => d.era).join('+') : '')).join(',') }));
}
