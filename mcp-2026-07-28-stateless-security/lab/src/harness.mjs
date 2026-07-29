import { createHash, randomUUID } from 'node:crypto';

import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import {
  createMcpHandler,
  McpServer,
  OAuthError,
  OAuthErrorCode,
  requireBearerAuth
} from '@modelcontextprotocol/server';
import * as z from 'zod/v4';

export const MODERN_REVISION = '2026-07-28';
export const LAB_URL = new URL('http://in-process/mcp');
export const TOKENS = Object.freeze({
  alice: 'alice-demo-token',
  mallory: 'mallory-demo-token',
  // Structured principal ids, as produced by multi-tenant issuers and by the
  // `${iss}#${sub}` canonical form. They exist to exercise delimiter safety.
  'tenant-a': 'tenant-a-demo-token',
  'tenant-a:svc': 'tenant-a-svc-demo-token'
});

// Three ways to key application state, from no binding to unambiguous binding.
//   vulnerable → possession of the handle is the only requirement.
//   naive      → the composite key the spec shows as an illustration. Correct in
//                intent, ambiguous in form: string concatenation cannot tell
//                ("a", "b:c") apart from ("a:b", "c"), so a principal whose id
//                shares a prefix with another can smuggle the delimiter inside
//                the handle argument and land on someone else's key.
//   hardened   → the pair encoded as a tuple. No delimiter left to smuggle.
export const KEY_STRATEGIES = Object.freeze({
  vulnerable: (owner, handle) => handle,
  naive: (owner, handle) => `${owner}:${handle}`,
  hardened: (owner, handle) => JSON.stringify([owner, handle])
});
export const LAB_MODES = Object.freeze(Object.keys(KEY_STRATEGIES));

const PROTOCOL_VERSION_META_KEY = 'io.modelcontextprotocol/protocolVersion';
const CLIENT_CAPABILITIES_META_KEY = 'io.modelcontextprotocol/clientCapabilities';
const CLIENT_INFO_META_KEY = 'io.modelcontextprotocol/clientInfo';

function hashHandle(handle) {
  return createHash('sha256').update(handle).digest('hex');
}

function resultJson(value, isError = false) {
  return {
    content: [{ type: 'text', text: JSON.stringify(value) }],
    ...(isError ? { isError: true } : {})
  };
}

function deniedResult() {
  return {
    content: [{ type: 'text', text: 'scan not found' }],
    isError: true
  };
}

function headersToRecord(headers) {
  const output = {};
  for (const [name, value] of headers.entries()) {
    output[name.toLowerCase()] = name.toLowerCase() === 'authorization' ? '[REDACTED]' : value;
  }
  return output;
}

function sanitizeValue(value, parentKey = '') {
  const sensitiveKeys = new Set(['handle', 'note', 'token', 'access_token', 'authorization']);
  if (sensitiveKeys.has(parentKey.toLowerCase())) return `[${parentKey.toUpperCase()}_REDACTED]`;
  if (Array.isArray(value)) return value.map(item => sanitizeValue(item));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sanitizeValue(item, key)]));
  }
  return value;
}

async function sanitizedBody(request) {
  const text = await request.clone().text();
  if (!text) return undefined;
  try {
    return sanitizeValue(JSON.parse(text));
  } catch {
    return '[NON_JSON_BODY_REDACTED]';
  }
}

export function modernEnvelope({ id, name, arguments: args = {}, revision = MODERN_REVISION, includeMeta = true }) {
  return {
    jsonrpc: '2.0',
    id,
    method: 'tools/call',
    params: {
      name,
      arguments: args,
      ...(includeMeta
        ? {
            _meta: {
              [PROTOCOL_VERSION_META_KEY]: revision,
              [CLIENT_INFO_META_KEY]: { name: 'lab-raw-client', version: '1.0.0' },
              [CLIENT_CAPABILITIES_META_KEY]: {}
            }
          }
        : {})
    }
  };
}

export function modernHeaders({ token = TOKENS.alice, name, revision = MODERN_REVISION } = {}) {
  return {
    accept: 'application/json, text/event-stream',
    authorization: `Bearer ${token}`,
    'content-type': 'application/json',
    'mcp-method': 'tools/call',
    'mcp-name': name,
    'mcp-protocol-version': revision
  };
}

// Two time domains, deliberately not merged:
//   `clock`   → application state. Injectable, so record expiry is testable
//               without sleeping (T08).
//   real time → credential lifetime. It belongs to the verifier and to the
//               bearer-auth middleware, which validate against the wall clock.
//               Pinning a credential to an injected clock would make the suite
//               pass or fail depending on the day it runs.
export function createLabHarness({
  mode,
  clock = () => Date.now(),
  ttlMs = 60_000,
  handleGenerator,
  traceGenerator
}) {
  if (!Object.hasOwn(KEY_STRATEGIES, mode)) {
    throw new TypeError(`mode must be one of: ${LAB_MODES.join(', ')}`);
  }

  const store = new Map();
  const audit = [];
  const records = [];
  const clients = new Set();
  const dispatch = { create_scan: 0, read_scan: 0, complete_scan: 0 };
  const metrics = { handlerRequests: 0 };
  let traceSequence = 0;

  const nextHandle = handleGenerator ?? (() => randomUUID());
  const nextTrace =
    traceGenerator ??
    (() => {
      traceSequence += 1;
      return `trace-${String(traceSequence).padStart(4, '0')}`;
    });

  const keyFor = KEY_STRATEGIES[mode];

  function writeAudit({ actor, action, outcome, reason, handle, traceId }) {
    audit.push({
      actor,
      action,
      outcome,
      ...(reason ? { reason } : {}),
      handleHash: hashHandle(handle),
      traceId
    });
  }

  function findRecord(actor, handle, action) {
    const key = keyFor(actor, handle);
    const scan = store.get(key);
    const traceId = nextTrace();

    if (!scan) {
      writeAudit({ actor, action, outcome: 'denied', reason: 'not_found_or_not_owner', handle, traceId });
      return { denied: true, result: deniedResult() };
    }

    if (scan.expiresAt <= clock()) {
      store.delete(key);
      writeAudit({ actor, action, outcome: 'denied', reason: 'expired', handle, traceId });
      return { denied: true, result: deniedResult() };
    }

    writeAudit({ actor, action, outcome: 'allowed', handle, traceId });
    return { denied: false, scan };
  }

  const factory = context => {
    const actor = context.authInfo?.extra?.sub;
    if (typeof actor !== 'string' || actor.length === 0) {
      throw new Error('verified subject claim is required');
    }
    const server = new McpServer({ name: `state-handle-${mode}`, version: '1.0.0' });

    server.registerTool(
      'create_scan',
      {
        description: 'Creates a fictitious in-memory record. It performs no scan.',
        inputSchema: z.object({ target: z.literal('demo.local'), note: z.string().min(1).max(128) })
      },
      ({ target, note }) => {
        dispatch.create_scan += 1;
        const handle = nextHandle();
        const scan = { handle, owner: actor, target, note, status: 'pending', expiresAt: clock() + ttlMs };
        store.set(keyFor(actor, handle), scan);
        return resultJson(scan);
      }
    );

    server.registerTool(
      'read_scan',
      { description: 'Reads a fictitious record.', inputSchema: z.object({ handle: z.string().min(1) }) },
      ({ handle }) => {
        dispatch.read_scan += 1;
        const found = findRecord(actor, handle, 'read_scan');
        return found.denied ? found.result : resultJson(found.scan);
      }
    );

    server.registerTool(
      'complete_scan',
      { description: 'Marks a fictitious record as completed.', inputSchema: z.object({ handle: z.string().min(1) }) },
      ({ handle }) => {
        dispatch.complete_scan += 1;
        const found = findRecord(actor, handle, 'complete_scan');
        if (found.denied) return found.result;
        found.scan.status = 'completed';
        return resultJson(found.scan);
      }
    );

    return server;
  };

  const verifier = {
    async verifyAccessToken(token) {
      const actor = Object.entries(TOKENS).find(([, knownToken]) => knownToken === token)?.[0];
      if (!actor) throw new OAuthError(OAuthErrorCode.InvalidToken, 'unknown token');
      return {
        token,
        clientId: 'state-handle-lab',
        scopes: ['mcp'],
        expiresAt: Math.floor(Date.now() / 1000) + 3600, // real time on purpose
        extra: { sub: actor }
      };
    }
  };

  const gate = requireBearerAuth({ verifier, requiredScopes: ['mcp'] });
  const handler = createMcpHandler(factory, { legacy: 'reject' });

  const inProcessFetch = async (url, init) => {
    const request = new Request(url, init);
    const requestBody = await sanitizedBody(request);
    const auth = await gate(request);
    let response;
    if (auth instanceof Response) {
      response = auth;
    } else {
      metrics.handlerRequests += 1;
      response = await handler.fetch(request, { authInfo: auth });
    }

    records.push({
      method: request.method,
      url: new URL(request.url).pathname,
      requestHeaders: headersToRecord(request.headers),
      ...(requestBody === undefined ? {} : { requestBody }),
      status: response.status,
      responseHeaders: headersToRecord(response.headers)
    });
    return response;
  };

  async function connectClient(actor) {
    const token = TOKENS[actor];
    if (!token) throw new TypeError(`unknown actor: ${actor}`);
    const client = new Client(
      { name: `${actor}-lab-client`, version: '1.0.0' },
      { versionNegotiation: { mode: { pin: MODERN_REVISION } } }
    );
    const transport = new StreamableHTTPClientTransport(LAB_URL, {
      fetch: inProcessFetch,
      requestInit: { headers: { authorization: `Bearer ${token}` } }
    });
    await client.connect(transport);
    clients.add(client);
    return client;
  }

  async function close() {
    await Promise.all([...clients].map(client => client.close().catch(() => {})));
    clients.clear();
    await handler.close();
  }

  return {
    mode,
    store,
    audit,
    records,
    dispatch,
    metrics,
    fetch: inProcessFetch,
    connectClient,
    close
  };
}

export function toolText(result) {
  const first = result.content?.[0];
  return first?.type === 'text' ? first.text : '';
}

export function toolJson(result) {
  return JSON.parse(toolText(result));
}
