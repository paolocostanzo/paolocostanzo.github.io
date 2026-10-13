# Trusting a repo — MCP server lab

Companion laboratory for the article “Trusting a repo now means trusting its MCP servers” (*Outside the Prompt*, part 2).

The question: **when a repository declares an MCP server in its project configuration, does an AI coding agent start it — and under which trust decision?** The lab declares one harmless canary server in every project-level location the tested clients read, opens the repository in each client, and records what the client asks, which answer is pre-selected, and whether the canary started.

## Safety boundary

- The canary (`canary/marker-server.mjs`) writes one JSON line to its own log file when it starts and answers a minimal MCP handshake with one tool that does nothing. It opens no network connection and writes no other file (test T04).
- The test repository contains only configuration that points at the canary.
- The matrix runner edits the clients' trust stores in `$HOME`. **Run it only in a disposable virtual machine or throwaway user**, never on your workstation.
- `check-repo` and `check-trust` are read-only.

## Contents

| Path | What it is |
|---|---|
| `canary/marker-server.mjs` | The canary MCP server (stdio) |
| `scripts/make-test-repo.mjs` | Builds the test repository with the six project-level declarations |
| `scripts/check-repo.mjs` | **For readers:** lists the MCP servers a repository declares and the exact command each would run |
| `scripts/check-trust.mjs` | **For readers:** lists the projects you have already trusted, client by client |
| `scripts/run_q5_matrix.py` | Unattended runner for the terminal clients (tmux), disposable VM only |
| `scripts/summarize_matrix.py` | Turns one or more `matrix.csv` into the results table, flagging disagreements |
| `scripts/generate-evidence.mjs` | Runs the tests, enforces the publication gate, writes `evidence/` |
| `test/` | `node:test` suite for the components above |
| `evidence/` | Test output, environment manifest with the SHA-256 of every source and evidence file, the matrix runs (`q5-run/`, including the 9 October re-check) with one screen capture per prompt |

## Requirements

- Node.js `>=22`, no dependencies.
- For the matrix: Python 3, tmux, and the clients under test, logged in, inside a disposable VM.

## Check your own setup (read-only)

```bash
node scripts/check-repo.mjs path/to/a/repository     # what would this repository ask my agent to run?
node scripts/check-trust.mjs                          # which projects have I already trusted?
```

`check-repo` can gate a script or CI step through its exit code (`--help` prints it):

| Exit | Meaning |
|---|---|
| 0 | No MCP server declared |
| 1 | At least one server declared, listed with the command it would run |
| 2 | Usage error |
| 3 | A configuration file could not be read or parsed: the list may be incomplete. Wins over 0 and 1; check that file by hand |

JSON files are parsed strictly, except `.vscode/mcp.json`, which is read as JSONC (comments and trailing commas) like VS Code does.
`.codex/config.toml` is read on a best-effort basis: basic and literal strings, inline and multiline arrays, inline tables,
dotted keys and `[mcp_servers.<name>]` / `[mcp_servers.<name>.env]` tables. Anything else under `mcp_servers` is reported as an
error (exit 3) rather than guessed.

## Reproduce

```bash
npm test                 # 18 tests
npm run evidence         # writes evidence/, exits non-zero unless 18/18 pass
```

Matrix (inside the VM, clients installed and logged in):

```bash
node scripts/make-test-repo.mjs --dir ~/q5-repo --canary "$PWD/canary/marker-server.mjs" --log ~/canary-start.log
python3 scripts/run_q5_matrix.py --lab "$PWD" --out ~/q5-run --reps 2
```

The runner writes `matrix.csv`, `run.json` (client versions before and after) and one screen capture per prompt.

## Test matrix (components)

| ID | Control |
|---|---|
| T01 | Canary logs `start` and `initialize`, tagged with client and source |
| T02 | Canary answers initialize, tools/list, tools/call; one no-op tool; no reply to notifications |
| T03 | Unknown methods get method-not-found |
| T04 | Canary writes nothing except its own log |
| T05 | Test repository has README plus six declarations |
| T06 | JSON declarations use each client's format and the absolute canary path |
| T07 | Codex TOML declaration is readable and points at the same canary |
| T08 | Relative canary path is refused |
| T09 | `check-repo` finds all six declarations and the command they run |
| T10 | `check-repo` exit code: 1 when declared, 0 when clean |
| T11 | `check-repo` reports unparsable files instead of crashing |
| T12 | TOML reader handles quoted table names and comments |
| T13 | `check-trust` reads every client's store format |
| T14 | `check-trust` is quiet when no store exists |
| T15 | `check-repo` exits with 3 on an unparsable file, also when other servers are declared |
| T16 | `.vscode/mcp.json` is read as JSONC; strings are left untouched; `.mcp.json` stays strict |
| T17 | TOML reader: literal strings, multiline arrays, env tables and inline tables, exact args |
| T18 | A TOML value the reader cannot read is an error (exit 3), not empty args; exit codes 2 and `--help` |

## Client matrix (what the runner measures)

| Client | Cells |
|---|---|
| Claude Code | untrusted · after-decision (default answers) · trusted-reopen · positive control (explicit approval) |
| Gemini CLI | untrusted · after-decision · trusted-reopen |
| Codex CLI | untrusted · after-decision · trusted-reopen |
| Cursor CLI | untrusted · after-decision · trusted-reopen · trust-without-mcp (option [w], then the approval screen) |
| Qwen Code | untrusted · reopen-after-reject · after-decision · trusted-reopen · config-changed |
| Goose | open-in-repo |

VS Code and Cursor IDE are graphical; they were measured by hand with screenshots: VS Code without Copilot on 28 September (article), and on 9 October VS Code with Copilot signed in and Cursor IDE signed in, one run each, driven with xdotool — step tables, canary logs and screenshots in `evidence/gui-20261009/`.

## Results (9 October 2026)

Two unattended runs from the same VM snapshot; details and exclusions in `evidence/q5-run/NOTES.md`.
"Canary after answer" counts the repetitions in which the declared server started.

| Client | Cell | Reps | Prompt seen | Pre-selected | Canary before answer | Canary after answer | Versions | Agreement |
|---|---|---|---|---|---|---|---|---|
| claude-code | untrusted | 2 | trust | ❯ No, exit | 0/2 | 0/2 | 2.1.283 (Claude Code) | consistent |
| claude-code | after-decision | 2 | trust+mcp | ❯ Continue without using this MCP server | 0/2 | 0/2 | 2.1.283 (Claude Code) | consistent |
| claude-code | trusted-reopen | 2 | — | — | — | 0/2 | 2.1.283 (Claude Code) | consistent |
| claude-code | positive-control | 2 | trust+mcp | ❯ Continue without using this MCP server | 0/2 | 2/2 | 2.1.283 (Claude Code) | consistent |
| gemini | untrusted | 2 | trust | ● 1. Trust folder (q5-repo) | 0/2 | 0/2 | 0.61.0 | consistent |
| gemini | after-decision | 5 | trust | ● 1. Trust folder (q5-repo) | 0/5 | 4/5 | 0.61.0 | **check** |
| gemini | trusted-reopen | 2 | — | — | — | 2/2 | 0.61.0 | consistent |
| cursor-agent | untrusted | 2 | trust | ▶ [a] Trust this workspace | 0/2 | 0/2 | 2026.09.26-dd393fe | consistent |
| cursor-agent | after-decision | 2 | trust | ▶ [a] Trust this workspace | 0/2 | 2/2 | 2026.09.26-dd393fe | consistent |
| cursor-agent | trusted-reopen | 2 | — | — | — | 2/2 | 2026.09.26-dd393fe | consistent |
| cursor-agent | trust-without-mcp | 2 | trust+mcp | ▶ [a] Approve all servers | 0/2 | 0/2 | 2026.09.26-dd393fe | consistent |
| qwen | untrusted | 2 | mcp | › 1. Approve this server | 0/2 | 0/2 | 0.24.6 | consistent |
| qwen | reopen-after-reject | 2 | — | — | — | 0/2 | 0.24.6 | consistent |
| qwen | after-decision | 2 | mcp | › 1. Approve this server | 0/2 | 2/2 | 0.24.6 | consistent |
| qwen | trusted-reopen | 2 | — | — | — | 2/2 | 0.24.6 | consistent |
| qwen | config-changed | 2 | mcp | › 1. Approve this server | 0/2 | 0/2 | 0.24.6 | consistent |
| goose | open-in-repo | 2 | ready | — | — | 0/2 | 1.52.0 | consistent |
| codex | untrusted | 3 | trust | › 1. Trust and continue | 0/3 | 0/3 | codex-cli 0.157.1 | consistent |
| codex | after-decision | 3 | trust | › 1. Trust and continue | 0/3 | 3/3 | codex-cli 0.157.1 | consistent |
| codex | trusted-reopen | 3 | — | — | — | 3/3 | codex-cli 0.157.1 | consistent |

Reading the table:

- No client started the canary before the user answered (column "before": 0 everywhere).
- The pre-selected answer is the permissive one in Gemini, Codex, Cursor CLI and Qwen; only Claude Code pre-selects "No".
- After trusting the folder, Gemini, Codex and Cursor CLI ([a]) start the server with no further question.
  Claude Code asks a second, server-specific question whose default declines it. Qwen asks per server, binds the
  approval to the exact configuration and asks again after a change. Goose does not read servers from the repository.
- Gemini `after-decision`: 4/5. In one repetition of the first run the canary was not observed within 15 s; the
  rerun added a later probe and recorded 3/3.


## Re-check on the releases of 9 October 2026

Before publication the five terminal clients with new releases were updated in place (Claude Code 2.1.295, Codex CLI 0.162.0,
Gemini CLI 0.63.0, Qwen Code 0.25.0, Cursor CLI 2026.10.01) and the matrix was run once more, plus three repetitions of the
Gemini cells (`evidence/q5-run/recheck-20261009/`). Every prompt, pre-selected answer and "before" cell was identical to the
table above. One difference: on Gemini 0.63.0 the canary started in the same session after "Trust folder" in 1 of 4 runs
(0.61.0: 4 of 5); in the other three the screen read `Skipping project agents due to untrusted folder`, the decision was saved,
and the server started at every reopening (4 of 4). On Claude Code 2.1.295, `claude --bare -p` and `claude -p --setting-sources user`
did not start the canary (0 of 3 each). A later clean run of `claude -p` on 2.1.295, with the project's trust entry removed before each of three runs, started the canary
3 of 3 (`recheck-20261009/claude-p-clean/`; not independently verifiable, see the changes of 13 October 2026 below). The earlier `gemini -p` runs in that folder are not valid as "untrusted"
measurements: the trust store still held the folder from the previous matrix cell (see the README there).

## Interpretation limits

- Product behaviour of specific versions (see each `run.json` and the `version_cell` column); defaults change with updates.
- Two clients changed version on their own during the runs, with updates disabled: Cursor CLI (2026.09.26 → 2026.10.01) and Codex's background component (→ 0.162.0, which then blocked the 0.157.1 CLI until it was told to run without it).
- Measures whether the declared program starts and under which decision — not what a malicious server could do once running, and not model behaviour.
- The runner answers with fixed keys; a change in a client's prompt layout shows up as `no-trust` / `no-mcp` in `prompt_seen` and must be checked against the captures.
- Store formats for `check-trust` are the ones observed on Linux in September–October 2026.

## Changes on 13 October 2026

Fixes to the lab's tools after publication. The measurements and the raw evidence (matrix CSVs, captures, canary logs,
screenshots, the canary itself) are unchanged; only the scripts, the tests, this README and `evidence/environment.json` /
`evidence/test-output.tap` (regenerated) changed.

- **`check-repo` failed open.** A configuration file it could not parse (broken JSON, or a `.vscode/mcp.json` with comments)
  produced an empty list and exit 0, so a CI gate passed on a file it had not understood. It now exits with **3** whenever a file
  cannot be read or parsed, and an unreadable file is reported instead of crashing the script.
- **JSONC.** `.vscode/mcp.json` is now read as VS Code reads it, with comments and trailing commas.
- **TOML.** The Codex reader showed `'sh'` with its quotes, dropped single-quoted array items and returned empty `args` for a
  multiline array, so "the exact command it would run" was not exact. It now reads literal strings, multiline arrays,
  `[mcp_servers.<name>.env]` tables and inline tables, and records an error for values it cannot read.
- **Manifest coverage.** `environment.json` hashed only `evidence/q5-run/`; it omitted the 14 files under `evidence/gui-20261009/`,
  `q5-summary.md` and `test-output.tap`. It now hashes every file under `evidence/` except itself, after the TAP is written.
- **Summarizer.** `summarize_matrix.py` called a cell consistent even when repetitions showed different pre-selected answers; it now
  also requires a single pre-selected answer. No row of the published table changes.
- Tests: 14 → 18 (T15–T18).

Caveats on the published evidence, stated here because the files themselves are left as they ran:

- Run 1 (`20261009-194314`) was produced by an earlier revision of the runner: its `matrix.csv` has no `version_cell` or
  `starts_late` columns, so its rows carry the version read at the start of the run.
- The 8 Cursor CLI cells of run 1 ran on 2026.10.01, after the client updated itself during the run (see its `run.json`), although
  `q5-summary.md` and the results table above label them 2026.09.26-dd393fe.
- The clean `claude -p` re-check (`recheck-20261009/claude-p-clean/`) is **not independently verifiable**: the precheck on line 10 of
  `claude-p-clean.sh` has a syntax error (unquoted strings in the inline Python), so the removal of the trust entry was not confirmed by
  the script, and the folder has no per-run canary logs (the three `.out` files only show that the session failed to
  authenticate). The claim that `claude -p` starts project servers rests on the September
  measurement and on Anthropic's documentation, not on this folder.
