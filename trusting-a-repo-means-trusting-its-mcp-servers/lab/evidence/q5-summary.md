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
