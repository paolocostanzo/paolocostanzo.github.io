# Tool change — MCP lab (Q3)

Companion laboratory for part 3 of *Outside the Prompt* (20 October 2026), preregistered research question RQ3.

The question: **after a user approves an MCP tool, the server changes the tool's definition. Does the client notice — does it tell
the user, ask for approval again, accept the change silently, or refuse the server?** The lab runs one harmless server whose single
no-op tool switches from definition A to definition B on a local control file, and records, message by message, what the client
asked for and what it was served.

**Status (17 October 2026): primary matrix done, 60/60 valid repetitions; preregistered coding done (author per D21, local model on
blind packets; agreement and its cause under Results). The two graphical clients (VS Code with Copilot, Cursor IDE) are run by hand and
added separately.** The procedure follows the frozen protocol as amended by deviations D5, D12, D16, D18 and D19, and the coding by D20
and D21 (`../preregistration/DEVIATIONS.md`), all written before the outcomes they govern were read.

## Safety boundary

- The server (`server/q3-server.mjs`) exposes one tool, `q3_noop`, that returns the word `ok` and appends one line to the server's
  own log. Definitions A and B describe that same behaviour in different words; neither contains instructions to the model, URLs,
  paths, data access or network access (test 15). The schema change adds one optional field that is ignored; the name change renames
  the tool. The server opens no network connection and writes no file other than its log (test 14).
- No third-party server, account or data is involved.
- `prepare_vm.py` and `run_q3_cell.py` edit the clients' MCP configuration and approvals in `$HOME`; `q3_host.py` restores and
  destroys a virtual machine. **Run them only in a disposable VM.**

## Contents

| Path | What it is |
|---|---|
| `server/q3-server.mjs` | Dual-era MCP server (stdio): modern 2026-07-28 and legacy (`initialize`) behaviour, logs every message in and out |
| `scripts/flip.mjs` | Writes the control file atomically (state A/B, signal level, kind of change) and logs each change |
| `scripts/prepare_vm.py` | Adds `q3-lab` to each client's user-level MCP configuration (with backups) and creates the test folder |
| `scripts/run_q3_cell.py` | Runs one repetition for one terminal client in tmux: call with A, change to B, call again, reopen, call again |
| `scripts/q3-clients.json` | Per-client screen patterns and keys, fixed in the set-up runs and frozen before the primary runs |
| `scripts/code_q3.py` | Rule-based provisional coder: outcome per phase from the server log and the runner's observations |
| `scripts/q3_host.py` | Host-side orchestration: clean snapshot per repetition, randomised blocked order, raw telemetry export. It copies its own folder into the VM under that folder's name and runs `~/lab/tool-change/...`: run it from a folder named `tool-change`, as in the repository (the copy published with the post is named `lab/`: rename it first). Kept as it ran, so that its SHA-256 matches the harness hashes recorded for every repetition |
| `scripts/isolated_codex.py`, `scripts/code_q3_codex.py` | Additional post hoc coder (D22): Codex CLI in a bubblewrap container that cannot see the project, with an isolation probe and an initial-context check against a reference session |
| `scripts/agreement_q3_three.py` | Agreement with the additional coder: preregistered κ first, then Codex vs author and vs local model, Fleiss' κ, confusion tables |
| `scripts/check-approvals.mjs` | **For readers:** lists, read-only, the MCP tools you approved permanently in each client (see below) |
| `test/` | `node:test` suite (server, coder with the real server, configuration writer, run plan, analysis, agreement, check-approvals) |

## Check your own setup

```
node scripts/check-approvals.mjs                 # stores in your home directory
node scripts/check-approvals.mjs --project DIR   # also the project-level stores inside DIR
node scripts/check-approvals.mjs --json
```

It reads, and never writes, the stores where the six terminal clients keep permanent MCP tool approvals (with `--project`, also the
project-level stores of Claude Code, Codex CLI, Cursor CLI, Gemini CLI and Qwen Code): Claude Code
(`~/.claude.json`, `~/.claude/settings*.json`), Codex CLI (`~/.codex/config.toml` and the project's `.codex/config.toml`,
`approval_mode = "approve"`), Cursor CLI (`~/.cursor/cli-config.json` and the project's `.cursor/cli.json`), Gemini CLI (`~/.gemini/policies/auto-saved.toml`, plus servers marked `trust: true`), Goose
(`~/.config/goose/permission.yaml`, and the `GOOSE_MODE` in `config.yaml`) and Qwen Code (`~/.qwen/settings.json`). These are the
locations the lab observed, and cleaned between repetitions (`scripts/cleanup_setup.py`), with the versions measured on 15 October
2026. In those versions every approval is keyed by server and tool name only: a server that changes the tool keeps it. Session
approvals live in memory and are not listed. An empty list is not proof that nothing is approved: modes that approve every call
need no entry, server-wide entries cover tools added later, and VS Code and Cursor IDE keep approvals in their own storage.
Gemini CLI's `trust: true` is read from `~/.gemini/settings.json` and, with `--project`, from the project's `.gemini/settings.json`.
A store that exists but cannot be read, or a Codex `approval_mode` line in a form the reader does not understand (for example an
inline table), is reported and the output is marked INCOMPLETE: check it by hand.

## How the server behaves

- **Modern (2026-07-28)**: any request carrying `_meta["io.modelcontextprotocol/protocolVersion"]` is served statelessly.
  `server/discover` returns the supported version, capabilities (`tools.listChanged: true`) and server info; an unsupported version
  gets `UnsupportedProtocolVersionError` (`-32022`) with the supported list; `tools/list` carries `resultType`, `ttlMs: 0` and
  `cacheScope`; `subscriptions/listen` is acknowledged first (`notifications/subscriptions/acknowledged`, subscription id in `_meta`),
  and list-changed notifications are sent only on subscriptions that asked for `toolsListChanged`.
- **Legacy**: `initialize` selects legacy semantics for the life of the process; the server negotiates the requested legacy revision
  (or 2025-11-25) and declares `tools.listChanged: true` — a server capability. After the change, at the `list_changed` level, it sends
  `notifications/tools/list_changed` on the initialized connection.
- Every `tools/list` is logged with the definition served (A or B) and its SHA-256; every `tools/call` with the name called, whether it
  exists, and the definition in force. At the `list_changed` level the server logs whether the signal could be delivered and on which
  channel (`signal-delivered`) or not (`signal-not-deliverable`, D16 point 1).

## Outcomes (D12, with precedence) and how they are coded

1 refused or disconnected · 2 re-approval required · 3 notified without re-approval · 4 silent accept (B served by a `tools/list` after
the change, call succeeded, nothing shown) · 5 stale (no `tools/list` after the change, A still in use) · 6 error, no call or timeout.
A repetition whose first call did not succeed with A is invalid. `code_q3.py` gives a provisional outcome; the two preregistered coders
classify the captures independently (D16 point 7). "Silent accept" is a protocol-level outcome: that B reached the model is claimed
only where a recording proxy shows it (local-engine cells).

## Requirements and tests

Node.js ≥ 22 and Python ≥ 3.11, no dependencies. `npm test` runs the suite (60 tests on 17 October 2026).

## Interpretation limits

- Measures client behaviour on a definition change, not what a model does with a changed description.
- Results are per client version; clients update themselves (Cursor CLI, Codex's background component).
- One OS (Debian 13 in a VM); graphical clients by hand, one repetition per signal.
- The server is configured at user level and the test folder is trusted beforehand (D16 point 2): repository trust is RQ2's question.

## Results (measured 15 October 2026; labels are the rule-based coder's, confirmed by the author per D21)

Six terminal clients, current releases, user-level server, folder trusted, first call approved with the client's own option; then the
description changed (A to B) with and without the protocol's notification; five repetitions per cell from a clean snapshot; random order.

| Client | Protocol spoken | First call (default) | Change, no signal | Change + notification | After restart (exploratory) |
|---|---|---|---|---|---|
| Claude Code 2.1.300 | 2026-07-28 (subscribes) | no question ("auto mode") | stale 5/5 | silent accept 5/5 (fetched B) | silent accept |
| Codex CLI 0.162.1 | 2025-06-18 | asked; "Always allow" | stale 5/5 | stale 5/5 (delivered, ignored) | silent accept |
| Cursor CLI 2026.10.14 | 2025-11-25 | asked; "Allowlist MCP Tool" | stale 5/5 | stale 5/5 (delivered, ignored) | silent accept |
| Gemini CLI 0.64.0 | 2025-06-18 | asked; "Allow tool for this session" | stale 5/5 | notified 5/5 ("Tools updated for server") | asked again (session approval) |
| Goose 1.54.0 | 2026-07-28 (no subscription) | no question (`auto`) | stale 5/5 | stale 5/5 (not deliverable) | silent accept |
| Qwen Code 0.25.1 | 2025-11-25 | no question ("Auto mode") | stale 5/5 | stale 5/5 (delivered, ignored) | silent accept |

H3, first clause (at most one client requires re-approval): holds — none does. Second clause (no client acts on the notification):
does not hold — Gemini CLI shows a notice in 5/5 delivered repetitions (it does not ask again); Goose cannot be evaluated (no subscription).
Coders (§5, D21): the author (labels recorded from his declaration of agreement on every repetition) and a local model coding blind
packets (`qwen3.8:27b-q8_0`, temperature 0). Preregistered Cohen's κ author vs local model = **0.115** (14/60 agreement). 45 of the 46
disagreements share one cause, a wording error in the packets: the line "definition in force at the call: B" gives the server's state, and the
model read it as "B was served" even where the packet also said that no tool list was fetched after the change. All 46 were resolved by
re-reading the server log and the screens, in favour of the author's label (`evidence/primary-20261015/coding/disagreements-resolved.csv`).

**Additional coder (D22, post hoc, published before it saw any packet).** Codex CLI 0.162.0 (`gpt-6.1-sol`, effort medium) coded the
same 60 packets, one fresh session per packet in a bubblewrap container without the project or the real home directory. A reference
session ran first and all 60 sessions passed the isolation probe and the initial-context check. Cohen's κ Codex vs author = **1.00**
(60/60); Codex vs local model = 0.115; Fleiss' κ over the three = 0.066; all 46 earlier disagreements coded like the author. The
instruction text is the local model's, but it sits inside the single `codex exec` prompt instead of a separate system message (D22).
Results: `evidence/primary-20261015/coding/codex/` (`codex-coding.csv`, `codex-coding.json`, `agreement-three.json`). The session
rollouts are not published; their SHA-256 hashes are in `sessions-SHA256SUMS`.

Evidence (terminal clients): `evidence/` (captures, server logs, plan, analysis, blind coding packets, set-up runs, conformance check;
`SHA256SUMS`).
Raw host telemetry is not included here (1.4 GB; kept for Q4 and the archived release).

## Results — graphical clients (17 October 2026, by hand, one repetition per signal)

Driven with `xdotool` inside the VM while the author watched the console; same server, sentence and phases; clean snapshot and VM clock
set from the host before each run. Fewer repetitions than the terminal cells, as D5 allows.

| Client | Protocol | First call (default) | Change, no signal | Change + notification | After restart (exploratory) |
|---|---|---|---|---|---|
| VS Code 1.139.1 + Copilot Chat | legacy | asked ("Run q3_noop", six scopes); "Always Allow" | stale | silent accept, 2 repetitions of phases 1–2 (B fetched 4–5 ms after the notification) | silent accept after the no-signal run; not observed after the notification runs (VM display froze, `[TTM] Buffer eviction failed`) |
| Cursor IDE 3.22.7 | legacy | no question (settings in the snapshot) | stale | silent accept (B fetched 6 ms after) | silent accept |

Evidence: `evidence/gui-20261017/` (one folder per run with `LOG.md`, screenshots, server logs and `SHA256SUMS`; `SUMMARY.md`). Host
telemetry and editor logs of these runs (audit, journal, client logs) are kept for Q4 and listed in each `SHA256SUMS`; verify the
published files with `sha256sum -c --ignore-missing SHA256SUMS`.
