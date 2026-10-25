# Default client log sources: format, retention and how they are read (D24 point 1)

Observed on the clean snapshot `q3-base-20261015` on 17 October 2026 (`inventory-20261017.json`, counts only), with the client
versions of the Q3 runs, and checked against the vendors' documentation where it exists (the project's research note of the same
day). "Undocumented" means we found the location on disk but not in the vendor's documentation. Retention is what the vendor
documents; we did not wait to observe it. The lab sets no logging or verbosity option.

| Client | Location (under the home directory) | Format | How the exporter reads record times | Retention |
|---|---|---|---|---|
| Claude Code | `.claude/projects/<project>/*.jsonl` (session transcripts) | JSON Lines | `timestamp` (ISO 8601) | 30 days by default (documented) |
| Claude Code | `.cache/claude-cli-nodejs/<project>/mcp-logs-<server>/<time>.jsonl` (one file per MCP server and session) | JSON Lines | `timestamp` | undocumented |
| Codex CLI | `.codex/logs_*.sqlite` (log database), `state_*.sqlite`, `thread_history_*.sqlite` | SQLite in WAL mode: copied with `-wal`/`-shm`, opened from a private copy, consolidated | columns named `ts`, `timestamp`, `created_at`… | bounded local store (documented, size not stated) |
| Codex CLI | `.codex/history.jsonl` (prompt history), `.codex/session_index.jsonl` | JSON Lines | `ts` (epoch seconds) | documented as on by default |
| Codex CLI | `.codex/app-server-daemon/*.log*` | plain text | not extracted (coverage "unknown") | undocumented |
| Codex CLI | `.codex/sessions/**/*.jsonl` (session rollouts) | JSON Lines | `timestamp` | absent on the snapshot (removed before it, D18); undocumented |
| Cursor CLI | `.cursor/chats/<hash>/<id>/store.db` and `meta.json` | SQLite, JSON | SQLite columns as above | undocumented |
| Cursor CLI | `.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl`, `worker.log` | JSON Lines, plain text | `timestamp` where present | undocumented |
| Gemini CLI | `.gemini/tmp/<project hash>/logs.json`, `chats/` | JSON (list of records) | `timestamp` | `chats/`: 30 days by default, configurable (`sessionRetention`, documented at v0.64.0); `logs.json`: no documented retention |
| Goose | `.local/state/goose/logs/cli/<date>/<time>.log` | plain text | not extracted | two weeks, fixed (documented; hard-coded in `logging.rs` at v1.54.0) |
| Goose | `.local/share/goose/sessions/sessions.db` | SQLite in WAL mode (as Codex) | SQLite columns as above | documented |
| Qwen Code | `.qwen/tmp/<hash>/logs.json`, `.qwen/projects/<project>/chats/*.jsonl`, `.qwen/usage/*.jsonl`, `usage_record.jsonl` | JSON, JSON Lines | `timestamp` | not swept at v0.25.1: `general.cleanupPeriodDays` (default 30) covers file-history backups, debug logs and subagent transcripts, not `chats/` (settings.md:101, housekeeping/scheduler.ts) |

Where record times cannot be extracted, `collection_check.py` reports the file's coverage as "unknown" and falls back on the
file's modification time; the analysis reads those files with their own parser. A file kept for a limited time can be cut by
retention between sessions; every session starts from the clean snapshot, so retention does not act within the lab.
