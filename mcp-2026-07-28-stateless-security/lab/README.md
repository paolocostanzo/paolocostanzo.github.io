# MCP 2026-07-28 State Handle Lab

Companion laboratory for the article “MCP 2026-07-28: stateless non significa senza stato”.

The lab is an executable conformance test for one normative requirement of the 2026-07-28 revision: *“MCP servers MUST NOT treat possession of a state handle as authentication”* ([Security Best Practices — State Handle Hijacking](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices#state-handle-hijacking)).

It compares three ways to key application state, holding tokens, tools, UUID generator and inputs constant:

| Mode | Key | Outcome |
|---|---|---|
| `vulnerable` | `handle` | No binding. Possession of the handle is the whole authorization check. |
| `naive` | `${owner}:${handle}` | The composite key the spec shows as an illustration. Bound, but ambiguous: a structured principal id can smuggle the delimiter through the handle argument. |
| `hardened` | `JSON.stringify([owner, handle])` | Unambiguous tuple. No delimiter left to move. |

It also exercises the modern protocol envelope and the authentication gate used by this specific SDK configuration.

## Safety boundary

- Everything runs in-process through a custom `fetch` function.
- No socket, DNS request, cloud account, external target or real credential is used.
- `create_scan` creates only an in-memory object and accepts the literal target `demo.local`.
- Alice, Mallory and the two `tenant-a` principals use local fixture tokens.
- Published HTTP records redact authorization, handles, notes and tokens.

## Requirements

- Node.js `>=22.19` for this lab baseline. The SDK itself supports Node.js `>=20`.
- npm

## Reproduce

```bash
npm ci
npm test
npm run evidence
```

The publication gate is exactly 13 passing tests, zero failures and exit code zero (`EXPECTED_TESTS` in `scripts/generate-evidence.mjs`). Evidence is written to:

- `evidence/test-output.tap`
- `evidence/environment.json`

The manifest records the source HEAD, source-only dirty state and SHA-256 hashes. Generated evidence files are excluded from the dirty-state check.

## Test matrix

| ID | Control |
|---|---|
| T01 | Modern revision, no `initialize`, no `Mcp-Session-Id` |
| T02–T03 | Missing and unknown bearer tokens rejected before the MCP handler |
| T04–T05 | Vulnerable cross-principal read and write |
| T06–T07 | Hardened ownership checks and preserved owner access |
| T08 | Record TTL and on-access cleanup with an injected clock |
| T09 | Audit correlation and controlled-secret redaction |
| T10 | Header/body mismatch |
| T11 | Missing modern `_meta` envelope |
| T12 | Unsupported protocol revision |
| T13 | Delimiter safety: concatenated composite key collides, tuple key does not |

## Interpretation limits

This is a deliberately constructed local implementation, not a finding against MCP itself. It evaluates one SDK and one pinned version.

Two time domains are deliberately kept apart: the injected `clock` governs application record TTL, while credential lifetime stays on the wall clock, because it is validated by the bearer-auth middleware. T08 therefore proves deterministic record expiry, not token expiry. Fixture authentication is not an OAuth conformance test. AWS, distributed consistency, replay, idempotency, concurrency and interoperability are outside this lab.
