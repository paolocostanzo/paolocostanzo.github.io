# Rule-development log (D27 point 5)

Every candidate rule considered by the author, the material it was tried on, and why it was kept or dropped. Material: only the
exploratory runs (`events-dev/`, 15 runs; `pipeline-check/`, 3 Gemini CLI runs) and the exploratory set-up sessions
(`setup-r1` to `setup-r4`), later also the later set-up sessions and the development part of the baseline (rounds 3–4); the client
logs of the confirmatory event runs and of the holdout sessions were not opened. Other reads of confirmatory material (ground truth
and observer records, not client logs) are listed in `holdout-access.md`. Explorations (private notes, by two assistant sessions
under the author's direction): ESPLORAZIONE-claude-gemini-qwen.md, ESPLORAZIONE-codex-goose.md. Candidate file:
`author-candidates.json`, frozen unchanged as `rules-author.json` (see `FROZEN.md`).

## Round 1 (22/10 21:00)

Tooling found during exploration and fixed in `normalize.py` before any candidate was scored: Goose's .log lines are JSON objects
(times now read); Codex's `ts_nanos` is added to `ts`; ISO times without a zone are read as UTC (Goose's sessions.db); files
without timed records are dated by the archive's file time, line by line, marked `time_from: mtime` (Goose's llm_request files,
Qwen Code's runtime.json).

| Candidate | Event | Kind | Development runs | Set-up sessions (false-positive windows) | Status |
|---|---|---|---|---|---|
| CC-A1 | Claude Code q3-announced | match: "Received tools/list_changed notification" in the per-server MCP log | announced hit (+0.0 s); silent, q5 miss | 0 | kept as claude-code/q3-announced |
| CC-D1 | Claude Code q3 (both) | changed: MCP tool definition in transcripts (api-request-shape, deferred_tools_record) | silent hit (+32.6 s); announced hit too | 0 | kept as claude-code/q3-silent (detects the change at the next session) |
| CC-S1 | Claude Code q3-silent | unannounced(CC-D1, CC-A1) | silent hit; announced miss | 0 | dropped: D27 does not require a rule to separate the two q3 types, and CC-D1 is simpler |
| CC-Q1 | Claude Code q5-start | new-name: MCP log directory for a server never seen before | no event in the development run: untested | 0 | kept as claude-code/q5-start, marked untested |
| C-Q5a | Codex q5-start | new-name: server first initialised | q5 hit; q3 miss | 0 | dropped for C-Q5b (a new user-level server would fire) |
| C-Q5b | Codex q5-start | new-name after a project-trust write within 10 s | q5 hit (+0.0 s); q3 miss | 0 | kept as codex/q5-start |
| C-Q5c | Codex q5-start | precursor error "Project-local config … disabled" | fires before the start | 0 | dropped: does not show that a server started |
| C-A1 | Codex q3-announced | match: tool-list-changed notification in logs_*.sqlite | announced hit (+0.0 s); silent, q5 miss | 0 | kept as codex/q3-announced |
| — | Codex q3-silent | none: no record names a definition, description or catalog hash | — | — | none |
| GE-A1 | Gemini CLI q3-announced | match: chat info "Tools updated for server: " | 2/2 hit (+0.5 s) | 0 | kept as gemini/q3-announced |
| — | Gemini CLI q3-silent, q5-start | none: definitions never stored; nothing written at a server start | — | — | none |
| QW-D1 | Qwen Code q3 (both) | changed: definitions returned by tool_search | announced +47.5 s, silent +49.3 s | 0 | kept for both q3 types; depends on the model calling tool_search (1 of 2 set-up sessions did) |
| — | Qwen Code q5-start | none: only runtime.json, before the start | — | — | none |
| G-Q3a | Goose q3 (both) | changed: tool declarations in llm_request files (file-time dated) | announced +51.5 s, silent +39.6 s | 0 | kept for both q3 types; the notification never reaches Goose |
| — | Goose q5-start | none: Goose does not start repository-declared servers | — | — | none (no event) |

Full output: reports/piano-p4-20261022/regole/VALUTAZIONE-SVILUPPO-v1.txt.
Known limit (D27 "Effect"): normal use has no benign server starts or definition changes, so 0 false-positive windows is expected
and does not show specificity against look-alikes.

## Round 2 (22/10 21:10) — after the adversarial review of round 1

Accepted: R1 claude-code/q5-start becomes "none": the logs do not record a server's scope; "never seen before" cannot be tested
because the snapshot's history already holds earlier starts of the same server, and a session-relative version would fire at every
session start for any server (a rule that passes the threshold without meaning). R2: `coverage()` reports, per rule, the holdout
windows that hold any record of its source (Goose keeps only its last ten model requests); the threshold is unchanged. R3:
"changed" compares a tool only with earlier definitions in the same project. R5: a database with a write-ahead log and no
consolidated copy is refused; the file-time fallback uses the member actually read. R6: each class states its limits (Codex: TUI
trust path only; Qwen Code: needs tool searches; Goose: last ten requests). R7: the whole rule is scanned for lab literals, with the
lab tool's definition texts added; a name hidden in regular-expression syntax is left to the reviews. R11: tests added (precursor
within/beyond, per-project comparison, consolidated copy, lone WAL, coverage). Noted, no change: R4 (final scoring uses score.py's
exact [event, run end]; the ±5 s of evaldev.py is for exploration only), R8 (UTC for naive times; "*" crosses "/"), R9 (look-alikes
absent from normal use: the D27 limitation), R10 (the "none" rules are confirmed).
Development results unchanged except claude-code/q5-start (now none): VALUTAZIONE-SVILUPPO-v2.txt.

## Round 3 (23/10 12:52) — development part of the baseline (baseline-r2), first two clients
Codex CLI development hour: 0 false-positive windows out of 60 for every candidate; source coverage 60/60. Claude Code
development hour: 0/60; coverage 48/60 (MCP logs) and 60/60 (transcripts). No candidate changed.

## Round 4 (24/10 08:21) — development part of the baseline (baseline-r4, five clients), with baseline-r3 and baseline-r2
Material: the five development hours of `baseline-r4`, Codex CLI's development attempt of `baseline-r3` (48 windows) and the
development hours of `baseline-r2` (Codex CLI, Claude Code, Qwen Code's attempt), reported side by side (D33 point 4, D37).
Every candidate: 0 false-positive windows in every session. Coverage of the rule's source (windows with any record): Codex CLI
60/60 (q5-start, q3-announced); Claude Code 46/60 (q3-announced, MCP logs) and 60/60 (q3-silent, transcripts); Gemini CLI 61/61
(q3-announced); Qwen Code 60/60; Goose 1/60 for both q3 rules (it keeps only its last ten model requests, round 2 R2). The rules
of type none or unannounced have no own source. No candidate changed: the author's rules are frozen as they are.
Output: reports/piano-p4-20261022/regole/VALUTAZIONE-SVILUPPO-v4.txt (script evaldev_r4.py).
Host rule (D36): learning set computed from the 27 development and set-up sessions inside their windows: 5 commands
(`telemetry/rules/host-known-commands.json`).
