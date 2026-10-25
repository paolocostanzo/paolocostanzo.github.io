# Notes of the second rule author (D27 point 7)

Material used: only this folder (README.md, tools/, material/). `sha256sum -c SHA256SUMS` passes for every file.
Rules: `rules-second-author.json` (15 pairs plus one auxiliary rule, see claude-code/q3-silent).

## How the rules were evaluated (read this first)

`tools/normalize.py` cannot run as shipped: it loads `../scripts/vm_logs.py` (TS_KEYS, TS_COLS, to_epoch) and
`../inventory/candidates.json` (the clients' frozen log locations), and neither file is in the package. I did not look for them
outside the folder. To evaluate I used a temporary harness (deleted at the end) that ran `normalize.py` and `apply.py` with their
logic unchanged, but with:

- the client log locations taken from each archive's own `manifest.json` (`patterns`; same `candidates_sha256` 040eab3b… in
  all 27 archives, and the same pattern lists for the five clients);
- a stand-in for `vm_logs`: TS_KEYS = timestamp, ts, time, created_at, createdAt, updated_at, updatedAt, date, datetime,
  startTime, start_time, t; TS_COLS = ts|timestamp|time|created_at|createdAt|created|updated_at|date|datetime|start_time;
  to_epoch = numbers as seconds (ms/µs/ns rescaled), ISO 8601 with Z or offset (UTC if no zone).

So the figures below are mine, not the official pipeline's. Record times may differ slightly from the real `vm_logs`
(for example if it picks a different time key). **The rules should be run again with the real tools** before anyone relies
on these figures.

Measures: for each run of the pair, "fired" = first hit at or after `event_time` and no later than `t1`, with the delay after
`event_time`; early hits (before `event_time`) are listed when there are any. For each normal-use session of the client: windows
with at least one hit / one-minute windows. "Coverage" = windows that hold at least one usable record of the rule's source
(`apply.coverage`). A window with no record cannot give a false alarm, so a low coverage makes a 0 a weak result.

## Results, rule by rule

### claude-code/q3-announced — `match`
- Looks at: the per-server MCP debug logs under `~/.cache/claude-cli-nodejs/<project>/mcp-logs-<server>/*.jsonl`, the record
  `Received tools/list_changed notification, refreshing tools`.
- Why: Claude Code logs the standard MCP notification by name, when it arrives.
- Runs: events-dev q3-announced r1, fired, delay 0.0 s. Not firing in the q3-silent and q5 runs.
- Sessions: 0/60 (r2, coverage 48), 0/60 (r4, coverage 46).

### claude-code/q3-silent — `unannounced` (with auxiliary `claude-code/q3-silent--changed`, type `changed`)
- Looks at: MCP tool definitions (description and input schema) that Claude Code writes into its transcripts
  (`~/.claude/projects/<project>/*.jsonl`: `api-request-shape` and `deferred_tools_record` entries, extractor `claude-tools`).
  It fires when a tool's definition differs from its last earlier definition in the same project, and no
  `claude-code/q3-announced` hit lies between the two observations.
- Why: Claude Code does not re-read the tools without a notification, but the next session records the new definition; the
  "unannounced" condition keeps the announced case out.
- The auxiliary rule is needed because type `unannounced` takes the id of a `changed` rule. It has id
  `claude-code/q3-silent--changed` and event `q3-silent`; count it as part of the claude-code/q3-silent rule, not as a 16th
  pair. On its own it also fires in the announced run (2 hits, 52-53 s after t0).
- Runs: events-dev q3-silent r1, fired, delay 32.6 s (first transcript record of the next session holding the new definition).
  0 hits in the q3-announced run (the notification lies between the two observations) and the q5 run.
- Sessions: 0/60 (r2), 0/60 (r4); coverage of the transcripts 60/60 in both.

### claude-code/q5-start — `new-name`
- Looks at: the path of the per-server MCP log directories `.cache/claude-cli-nodejs/*/mcp-logs-<server>/`. It fires when a
  server name gets a log directory for the first time in the archive (first-ever start of that server name).
- Why: a project-declared server that the client starts gets its own log directory. A name never seen before is the general
  sign of "a new server was started".
- Runs: events-dev q5-start r1 has `event_happened: false` (Claude Code did not start the project's server). The rule does not
  fire there, which is correct, but there is **no positive run to test it on**. History in the same archive: the rule hits when
  the project's server was started for the first time, on 28 Sep 2026 (outside every run window). That is evidence that the
  signal exists, not a measured result.
- Limit: the key is the server name only, so a project server whose name was seen before (in any project) does not fire again.
  Keying on project + server would fire when a known user-level server first runs in a new project folder (it happens in this
  very run).
- Sessions: 0/60 (r2, coverage 48), 0/60 (r4, coverage 46).

### codex/q3-announced — `match`
- Looks at: `~/.codex/logs_*.sqlite` (table `logs`), the records `MCP server tool list changed` (codex_rmcp_client) and
  `ToolListChangedNotification` (rmcp::service).
- Runs: events-dev q3-announced r1, fired, delay 0.0 s (two records, same time). 0 hits in the q3-silent and q5 runs.
- Sessions: 0/60 (r2), 0/48 (r3), 0/60 (r4); coverage 60, 48 and 60.

### codex/q3-silent — `none`
- Codex's default logs never hold an MCP tool's description or schema. Searching every record of every codex archive for the
  definition text found nothing, in logs_2.sqlite, the session rollouts, history.jsonl, session_index.jsonl or the state
  databases. A silent change sends no notification. I compared the record kinds after the event (target plus message template,
  digits and ids masked) with the kinds seen in the three normal-use sessions: the silent run has **no** kind that the sessions
  lack.

### codex/q5-start — `new-name`
- Looks at: `logs_*.sqlite`, span `start_server_task{server_name=<name>}`. It fires on a server name never started before in
  the database.
- Runs: events-dev q5-start r1, fired, delay 0.27 s (the project's server is started 0.25 s after `event_time`). No early hit.
  0 hits in the q3 runs.
- Sessions: 0/60, 0/48, 0/60 (coverage 60, 48, 60).
- Note: right before starting the server, Codex logs (ERROR) that project-local config is "disabled ... until the project is
  trusted", but the project's server is started anyway (seen in this run; I did not investigate further).

### gemini/q3-announced — `match`
- Looks at: chat transcripts `~/.gemini/tmp/<project>/chats/*`, a record of type `info` with text `Tools updated for server`.
- Runs: events-dev q3-announced r1, fired, delay 0.53 s. pipeline-check q3-announced r1, fired, delay 0.53 s. 0 hits in the
  four q3-silent and q5 runs.
- Sessions: 0/61 (coverage 61).

### gemini/q3-silent — `none`
- Gemini CLI's default logs record no tool definitions, and nothing that refreshes them without a notification. In both
  silent runs (events-dev and pipeline-check) the records after the event are only ordinary user, model and info records
  ("Request cancelled", "update available"), the same kinds as in announced runs and normal use. No candidate rule.

### gemini/q5-start — `none`
- In both runs where the event happened (events-dev and pipeline-check, q5-start), Gemini CLI wrote **no timed record at all**
  in [t0, t1]: no chat, no logs.json entry, no log line. Nothing can mark the start.

### goose/q3-announced and goose/q3-silent — both `changed` (same rule body)
- Looks at: `~/.local/state/goose/logs/llm_request.*.jsonl`, the tool declarations sent to the model
  (`input.tools.functionDeclarations`, extractor `goose-request-tools`). It fires when a declaration differs from the earlier
  declaration of the same tool. These files carry no record time, so they are dated by file modification time
  (`allow_mtime: true`).
- Why the same rule for both: Goose logs no list-changed notification (no record in the announced run marks it). In both
  variants the change shows only when a later request carries the new declaration. The rule recognises "a tool's definition
  changed" and cannot tell announced from silent.
- Runs: q3-announced r1, fired, delay 51.4 s (first request of the next session). q3-silent r1, fired, delay 39.6 s. Each rule
  also fires in the other variant's run. 0 hits in the q5 run.
- Sessions: 0/60, but **coverage is only 1/60**. Goose keeps only its last ten llm_request files, and in the session archive
  they are all dated at the end of the hour. The 0 says little about false alarms during an hour of use; it holds only because
  normal use does not change declarations.

### goose/q5-start — `new-name`
- Looks at: `~/.local/share/goose/sessions/sessions.db`, table `sessions`, column `extension_data`. It fires when a session's
  enabled extensions include a `stdio` extension name never enabled in an earlier session.
- Runs: events-dev q5-start r1 has `event_happened: false`. No hit, which is correct, but there is **no positive run**. History
  in the archive: the rule hits once, when the user-level server was first enabled (15 Oct 2026). That shows the regex works,
  nothing about the event.
- Limit: `new-name` takes the first match per record, so it watches only the first stdio extension in the list.
- Sessions: 0/60 (coverage 60).

### qwen/q3-announced and qwen/q3-silent — both `changed` (same rule body)
- Looks at: Qwen Code chats `~/.qwen/projects/<project>/chats/*.jsonl`, the MCP tool definitions returned by its `tool_search`
  tool (extractor `qwen-tool-search`). It fires when a definition differs from the earlier one of the same tool in the same
  project.
- Why the same rule: Qwen Code logs no notification. The change is visible only when the model fetches the definition again
  with tool_search, in the next session.
- Runs: q3-announced r1, fired, delay 47.5 s. q3-silent r1, fired, delay 49.3 s. Each also fires in the other variant's run.
  0 hits in the q5 run.
- Sessions: 0/63 (r2, coverage 33), 0/60 (r4, coverage 60).

### qwen/q5-start — `none`
- In the run where Qwen Code started the project's server, the only record in [t0, t1] is the session's `runtime.json` (pid,
  work_dir, start time, dated by file mtime, 1.7 s before `event_time`). No default log names an MCP server or reports its
  start (`~/.qwen/debug`, `~/.qwen/logs` empty).

## Summary table

| pair | type | runs (event happened) | sessions FP windows |
|---|---|---|---|
| claude-code/q3-announced | match | 1/1 fired, +0.0 s | 0/60, 0/60 |
| claude-code/q3-silent | unannounced (+aux changed) | 1/1 fired, +32.6 s | 0/60, 0/60 |
| claude-code/q5-start | new-name | no positive run (event did not happen; silent, correct) | 0/60, 0/60 |
| codex/q3-announced | match | 1/1 fired, +0.0 s | 0/60, 0/48, 0/60 |
| codex/q3-silent | none | 0/1 | 0 |
| codex/q5-start | new-name | 1/1 fired, +0.27 s | 0/60, 0/48, 0/60 |
| gemini/q3-announced | match | 2/2 fired, +0.53 s and +0.53 s | 0/61 |
| gemini/q3-silent | none | 0/2 | 0 |
| gemini/q5-start | none | 0/2 | 0 |
| goose/q3-announced | changed (mtime) | 1/1 fired, +51.4 s | 0/60 (coverage 1/60) |
| goose/q3-silent | changed (mtime) | 1/1 fired, +39.6 s | 0/60 (coverage 1/60) |
| goose/q5-start | new-name | no positive run (event did not happen; silent, correct) | 0/60 |
| qwen/q3-announced | changed | 1/1 fired, +47.5 s | 0/63, 0/60 |
| qwen/q3-silent | changed | 1/1 fired, +49.3 s | 0/63, 0/60 |
| qwen/q5-start | none | 0/1 | 0 |

## Rules tried and dropped

- **claude-code/q3-silent as plain `changed`**: fires in the silent run (+32.6 s) and 0/60 in the sessions, but also fires in
  the announced run. Replaced by `unannounced`, which keeps the same hit and drops the announced case.
- **claude-code/q5-start as `match` on `Starting connection`** in the MCP logs: fires at every server start, 26/60 and 25/60
  session windows (43%) and in the q5 run where the event did not happen. Dropped.
- **codex/q5-start as `new-name` on any `server_name=`**: also captures the remote-control websocket's `server_name=<host
  name>`. It fires before the event (6.9 s after t0 in the q5 run) and in 1/48 windows of session r3. Narrowed to the
  `start_server_task{server_name=…}` span, which removes both.
- **qwen/q5-start as `new-name` on `work_dir` of the session `runtime.json`**: it marks "a project folder opened for the
  first time", not a server start. It fires 1.7 s *before* the event in the q5 run (so never at or after it) and in 1/63 and
  1/60 session windows. Dropped; the pair is `none`.
- Searches with no candidate (they led to the `none` entries): codex records after the event compared with session record
  kinds (silent run: nothing new; announced run: only the two notification records used above); gemini silent and q5 runs
  checked the same way.

## Forbidden literals

No rule names the lab's servers, tools, folders, repositories, log files, addresses or description text. Sources are generic
globs of the clients' default locations (`*` for project and server parts). Patterns are the clients' own log phrases
(`Received tools/list_changed notification`, `MCP server tool list changed`, `ToolListChangedNotification`, `Tools updated for
server`, `start_server_task{server_name=…}`), generic JSON shapes, or built-in extractors. `apply.check_literals` accepts the
file.
