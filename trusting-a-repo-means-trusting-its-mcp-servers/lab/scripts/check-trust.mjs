#!/usr/bin/env node
// Shows which projects you have already trusted (or whose MCP servers you
// approved) in each AI coding agent. Read-only.
//
//   node scripts/check-trust.mjs            # your home directory
//   node scripts/check-trust.mjs --json
//   node scripts/check-trust.mjs --home DIR # another home (used by the tests)
//
// Store locations observed on Linux with the versions tested in September 2026.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const readJson = (p) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return null; } };

export function claude(home) {
  const d = readJson(path.join(home, '.claude.json'));
  if (!d) return { store: '~/.claude.json', present: false, projects: [] };
  const projects = Object.entries(d.projects ?? {})
    .filter(([, v]) => v.hasTrustDialogAccepted || (v.enabledMcpjsonServers ?? []).length)
    .map(([p, v]) => ({
      project: p,
      trusted: !!v.hasTrustDialogAccepted,
      approvedServers: v.enabledMcpjsonServers ?? [],
      rejectedServers: v.disabledMcpjsonServers ?? [],
      allProjectServers: !!v.enableAllProjectMcpServers,
    }));
  return { store: '~/.claude.json', present: true, projects };
}

export function gemini(home) {
  const d = readJson(path.join(home, '.gemini', 'trustedFolders.json'));
  if (!d) return { store: '~/.gemini/trustedFolders.json', present: false, projects: [] };
  const projects = Object.entries(d).map(([p, level]) => ({ project: p, level, trusted: level !== 'DO_NOT_TRUST' }));
  return { store: '~/.gemini/trustedFolders.json', present: true, projects };
}

export function codex(home) {
  const p = path.join(home, '.codex', 'config.toml');
  if (!fs.existsSync(p)) return { store: '~/.codex/config.toml', present: false, projects: [] };
  const projects = [];
  let current = null;
  for (const raw of fs.readFileSync(p, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    const h = line.match(/^\[projects\."(.+)"\]$/);
    if (h) { current = { project: h[1], level: null }; projects.push(current); continue; }
    if (line.startsWith('[')) { current = null; continue; }
    const kv = current && line.match(/^trust_level\s*=\s*"([^"]+)"/);
    if (kv) current.level = kv[1];
  }
  return { store: '~/.codex/config.toml', present: true, projects: projects.map((x) => ({ ...x, trusted: x.level === 'trusted' })) };
}

export function cursor(home) {
  const dir = path.join(home, '.cursor', 'projects');
  if (!fs.existsSync(dir)) return { store: '~/.cursor/projects/*/.workspace-trusted', present: false, projects: [] };
  const projects = fs.readdirSync(dir)
    .filter((name) => fs.existsSync(path.join(dir, name, '.workspace-trusted')))
    .map((name) => ({ project: name, trusted: true, note: 'directory name encodes the project path' }));
  return { store: '~/.cursor/projects/*/.workspace-trusted', present: true, projects };
}

export function qwen(home) {
  const d = readJson(path.join(home, '.qwen', 'mcpApprovals.json'));
  if (!d) return { store: '~/.qwen/mcpApprovals.json', present: false, projects: [] };
  const projects = Object.entries(d).map(([p, servers]) => ({
    project: p,
    servers: Object.entries(servers).map(([name, s]) => ({ name, status: s.status, boundToConfigHash: !!s.hash })),
  }));
  return { store: '~/.qwen/mcpApprovals.json', present: true, projects };
}

export function checkTrust(home = os.homedir()) {
  return { home, claude: claude(home), gemini: gemini(home), codex: codex(home), cursor: cursor(home), qwen: qwen(home) };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const hi = args.indexOf('--home');
  const r = checkTrust(hi >= 0 ? path.resolve(args[hi + 1]) : os.homedir());
  if (args.includes('--json')) { console.log(JSON.stringify(r, null, 2)); process.exit(0); }
  for (const name of ['claude', 'gemini', 'codex', 'cursor', 'qwen']) {
    const c = r[name];
    console.log(`\n${name}  (${c.store})${c.present ? '' : '  — not found'}`);
    if (c.present && !c.projects.length) console.log('  no trusted projects');
    for (const p of c.projects) {
      const extra = p.servers ? p.servers.map((s) => `${s.name}=${s.status}`).join(', ')
        : p.approvedServers ? `approved servers: ${p.approvedServers.join(', ') || 'none'}`
        : p.level ?? '';
      console.log(`  ${p.project}  ${p.trusted === false ? '(not trusted) ' : ''}${extra}`);
    }
  }
  console.log('\nGoose reads MCP extensions only from ~/.config/goose/config.yaml; VS Code keeps workspace trust in its own storage (use "Workspaces: Manage Workspace Trust").');
}
