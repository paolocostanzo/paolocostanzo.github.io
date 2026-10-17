# Conformance check against the official MCP client SDKs

Optional, not part of `npm test` (which needs no dependencies). Checks the Q3 server against the official TypeScript client SDKs:
`@modelcontextprotocol/sdk` 1.32.1 (legacy only, `LATEST_PROTOCOL_VERSION = 2025-11-25`) and `@modelcontextprotocol/client` 2.3.1
(dual-era; `versionNegotiation.mode` is `'legacy'` by default, `'auto'` probes with `server/discover`, `{ pin: '2026-07-28' }` forces modern).
For each SDK/mode and signal level it connects over stdio, lists and calls the tool, switches A → B, waits, lists and calls again.

```bash
mkdir -p /tmp/sdk && cd /tmp/sdk && npm init -y && npm install --ignore-scripts @modelcontextprotocol/sdk@1.32.1 @modelcontextprotocol/client@2.3.1
node conformance.mjs /tmp/sdk/node_modules ../server/q3-server.mjs ../scripts/flip.mjs /tmp/sdk/out
```

Result on 15 October 2026 (8 combinations, no protocol error): legacy SDK and v2 in default mode → `initialize` 2025-11-25,
notification on the initialized connection only at the `list_changed` level; v2 `auto` and `pin` → `server/discover`,
`subscriptions/listen` (acknowledged), `tools/list` with `ttlMs: 0`, notification on the subscription at the `list_changed` level;
in every combination the list fetched after the change served definition B. Raw output: `lab/results/q3/conformance-20261015/`
in the project notes (published with the evidence).
