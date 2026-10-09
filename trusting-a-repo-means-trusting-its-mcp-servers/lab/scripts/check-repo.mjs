#!/usr/bin/env node
// Lists the MCP servers a repository declares for AI coding agents, with the
// exact command each one would run. Read-only: it opens files, never runs them.
//
//   node scripts/check-repo.mjs path/to/repo          # human-readable
//   node scripts/check-repo.mjs path/to/repo --json   # machine-readable
//
// Exit code: 0 = nothing declared, 1 = at least one server declared, 2 = usage error.
import fs from 'node:fs';
import path from 'node:path';

export const LOCATIONS = [
  { file: '.mcp.json', readBy: 'Claude Code (also detected by VS Code)', key: 'mcpServers' },
  { file: '.vscode/mcp.json', readBy: 'VS Code', key: 'servers' },
  { file: '.cursor/mcp.json', readBy: 'Cursor (CLI and IDE)', key: 'mcpServers' },
  { file: '.gemini/settings.json', readBy: 'Gemini CLI', key: 'mcpServers' },
  { file: '.qwen/settings.json', readBy: 'Qwen Code', key: 'mcpServers' },
  { file: '.codex/config.toml', readBy: 'Codex CLI', key: 'toml' },
];

// Minimal reader for the [mcp_servers.<name>] tables used by Codex.
// Handles quoted strings, string arrays and one-line inline tables; enough to
// show what would run. It is not a general TOML parser.
export function parseCodexToml(text) {
  const servers = {};
  let current = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/\s+#.*$/, '').trim();
    if (!line || line.startsWith('#')) continue;
    const header = line.match(/^\[\s*mcp_servers\.("?)([^"\]]+)\1\s*\]$/);
    if (header) { current = servers[header[2]] = { raw: {} }; continue; }
    if (line.startsWith('[')) { current = null; continue; }
    if (!current) continue;
    const kv = line.match(/^([A-Za-z0-9_-]+)\s*=\s*(.+)$/);
    if (!kv) continue;
    const [, key, value] = kv;
    current.raw[key] = value;
    if (key === 'command') current.command = value.replace(/^"(.*)"$/, '$1');
    if (key === 'args') {
      current.args = [...value.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => JSON.parse(`"${m[1]}"`));
    }
    if (key === 'env') {
      current.env = [...value.matchAll(/([A-Za-z0-9_]+)\s*=/g)].map((m) => m[1]);
    }
  }
  return servers;
}

export function checkRepo(dir) {
  const found = [];
  const errors = [];
  for (const loc of LOCATIONS) {
    const target = path.join(dir, loc.file);
    if (!fs.existsSync(target)) continue;
    const text = fs.readFileSync(target, 'utf8');
    try {
      if (loc.key === 'toml') {
        for (const [name, s] of Object.entries(parseCodexToml(text))) {
          found.push({ file: loc.file, readBy: loc.readBy, name, command: s.command ?? null, args: s.args ?? [], env: s.env ?? [] });
        }
      } else {
        const servers = JSON.parse(text)[loc.key] ?? {};
        for (const [name, s] of Object.entries(servers)) {
          found.push({
            file: loc.file, readBy: loc.readBy, name,
            command: s.command ?? s.url ?? null,
            args: Array.isArray(s.args) ? s.args : [],
            env: s.env ? Object.keys(s.env) : [],
          });
        }
      }
    } catch (e) {
      errors.push({ file: loc.file, error: e.message });
    }
  }
  return { dir: path.resolve(dir), found, errors };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const dir = args.find((a) => !a.startsWith('--'));
  if (!dir) { console.error('usage: check-repo.mjs <repo> [--json]'); process.exit(2); }
  const r = checkRepo(dir);
  if (args.includes('--json')) {
    console.log(JSON.stringify(r, null, 2));
  } else {
    console.log(`MCP servers declared in ${r.dir}: ${r.found.length}`);
    for (const s of r.found) {
      console.log(`\n  ${s.name}  (${s.file} — read by ${s.readBy})`);
      console.log(`    runs: ${[s.command, ...s.args].join(' ')}`);
      if (s.env.length) console.log(`    env:  ${s.env.join(', ')}`);
    }
    for (const e of r.errors) console.log(`\n  could not parse ${e.file}: ${e.error}`);
    if (r.found.length) console.log('\nOpening this repository in an agent and trusting it may start the commands above.');
  }
  process.exit(r.found.length ? 1 : 0);
}
