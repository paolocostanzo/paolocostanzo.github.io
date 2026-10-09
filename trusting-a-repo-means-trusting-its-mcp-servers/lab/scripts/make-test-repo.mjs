#!/usr/bin/env node
// Builds the test repository: one ordinary-looking project that declares the
// same canary MCP server in every project-level location the tested clients read.
//
//   node scripts/make-test-repo.mjs --dir ~/q5-repo --canary /abs/path/marker-server.mjs --log ~/canary-start.log
//
// The canary path is written as an absolute path on purpose: the lab measures
// whether a client *starts* a declared server, and relative-path resolution
// differs between clients.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export const DECLARATIONS = [
  { file: '.mcp.json', client: 'claude-code', format: 'mcpServers' },
  { file: '.vscode/mcp.json', client: 'vscode', format: 'servers' },
  { file: '.cursor/mcp.json', client: 'cursor', format: 'mcpServers' },
  { file: '.gemini/settings.json', client: 'gemini', format: 'mcpServers' },
  { file: '.qwen/settings.json', client: 'qwen', format: 'mcpServers' },
  { file: '.codex/config.toml', client: 'codex', format: 'toml' },
];

export const SERVER_NAME = 'q5-canary';

function entry({ canary, log, client, source }) {
  return {
    command: 'node',
    args: [canary],
    env: { CANARY_CLIENT: client, CANARY_SOURCE: source, CANARY_LOG: log },
  };
}

export function render(decl, { canary, log }) {
  const e = entry({ canary, log, client: decl.client, source: decl.file });
  if (decl.format === 'mcpServers') {
    return JSON.stringify({ mcpServers: { [SERVER_NAME]: e } }, null, 2) + '\n';
  }
  if (decl.format === 'servers') {
    return JSON.stringify({ servers: { [SERVER_NAME]: { type: 'stdio', ...e } } }, null, 2) + '\n';
  }
  // TOML: Codex reads [mcp_servers.<name>] from a trusted project's .codex/config.toml
  const q = (s) => JSON.stringify(s);
  const env = Object.entries(e.env).map(([k, v]) => `${k} = ${q(v)}`).join(', ');
  return [
    `[mcp_servers.${SERVER_NAME}]`,
    `command = ${q(e.command)}`,
    `args = [${e.args.map(q).join(', ')}]`,
    `env = { ${env} }`,
    '',
  ].join('\n');
}

export function makeTestRepo({ dir, canary, log }) {
  if (!path.isAbsolute(canary)) throw new Error('--canary must be an absolute path');
  if (!path.isAbsolute(log)) throw new Error('--log must be an absolute path');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'README.md'),
    '# q5-demo\n\nOrdinary-looking demo project. It declares one harmless canary MCP server\n' +
    'for the "trusting a repo" lab. The canary only logs that it started.\n');
  const written = [];
  for (const decl of DECLARATIONS) {
    const target = path.join(dir, decl.file);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, render(decl, { canary, log }));
    written.push(decl.file);
  }
  return written;
}

function parseArgs(argv) {
  const out = { dir: null, canary: null, log: path.join(os.homedir(), 'canary-start.log') };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--dir') out.dir = path.resolve(argv[++i]);
    else if (k === '--canary') out.canary = path.resolve(argv[++i]);
    else if (k === '--log') out.log = path.resolve(argv[++i]);
    else if (k === '-h' || k === '--help') out.help = true;
  }
  return out;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const a = parseArgs(process.argv.slice(2));
  if (a.help || !a.dir || !a.canary) {
    console.log('usage: make-test-repo.mjs --dir <repo> --canary <abs path to marker-server.mjs> [--log <abs log path>]');
    process.exit(a.help ? 0 : 2);
  }
  const files = makeTestRepo(a);
  console.log(`test repository written to ${a.dir}`);
  for (const f of files) console.log(`  ${f}`);
}
