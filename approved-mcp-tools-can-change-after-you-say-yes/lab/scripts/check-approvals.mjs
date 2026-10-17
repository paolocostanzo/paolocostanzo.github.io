#!/usr/bin/env node
// Lists the MCP tools you have approved permanently ("always allow") in each AI coding agent. Read-only.
// In the part 3 measurements (15 October 2026) every one of these approvals is keyed by server and tool name only:
// if the server later changes what the tool says or does, the approval still applies.
//
//   node scripts/check-approvals.mjs                  # your home directory
//   node scripts/check-approvals.mjs --project DIR    # also the project-level stores inside DIR
//   node scripts/check-approvals.mjs --json
//   node scripts/check-approvals.mjs --home DIR       # another home (used by the tests)
//
// Store locations are those the lab observed (and cleaned between repetitions, scripts/cleanup_setup.py) on Linux with
// Claude Code 2.1.300, Codex CLI 0.162.1, Cursor CLI 2026.10.14, Gemini CLI 0.64.0, Goose 1.54.0 and Qwen Code 0.25.1.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// A missing file is null; a file that exists but cannot be read is { readError } (it must never look like "not found").
const read = (p) => {
  try { return fs.readFileSync(p, 'utf8'); } catch (e) { return e.code === 'ENOENT' || e.code === 'ENOTDIR' ? null : { readError: e.code ?? String(e) }; }
};
const isErr = (x) => x !== null && typeof x === 'object' && 'readError' in x;
const readJson = (p) => { const t = read(p); if (t === null || isErr(t)) return t; try { return JSON.parse(t); } catch { return undefined; } };
const failed = (label, d) => ({ source: label, unreadable: true, ...(isErr(d) ? { error: d.readError } : { error: 'not valid JSON' }) });
const store = (name, file, entries, note) => ({
  store: name, present: file !== null, unreadable: file === undefined || isErr(file),
  incomplete: entries.some((e) => e.unreadable), entries, ...(note ? { note } : {}) });

// "mcp__server__tool" (Claude Code, Qwen Code) or "mcp__server" (whole server)
function mcpRule(rule, source) {
  const m = typeof rule === 'string' && rule.match(/^mcp__([^_](?:[^]*?[^_])?)(?:__(.+))?$/);
  return m ? { server: m[1], tool: m[2] ?? '*', source } : null;
}

export function claude(home, project) {
  const files = [['~/.claude.json', path.join(home, '.claude.json')],
    ['~/.claude/settings.json', path.join(home, '.claude', 'settings.json')],
    ['~/.claude/settings.local.json', path.join(home, '.claude', 'settings.local.json')]];
  if (project) for (const f of ['settings.json', 'settings.local.json']) files.push([`${project}/.claude/${f}`, path.join(project, '.claude', f)]);
  const out = [];
  let any = null;
  for (const [label, p] of files) {
    const d = readJson(p);
    if (d === null) continue;
    any = any ?? d;
    if (d === undefined || isErr(d)) { out.push(failed(label, d)); continue; }
    for (const r of d.permissions?.allow ?? []) { const e = mcpRule(r, label); if (e) out.push(e); }
    for (const [proj, v] of Object.entries(d.projects ?? {})) {
      for (const r of v?.allowedTools ?? []) { const e = mcpRule(r, `${label} projects[${proj}].allowedTools`); if (e) out.push(e); }
    }
  }
  return store('Claude Code: permissions.allow / allowedTools', any, out);
}

// Codex writes one table per remembered tool: [mcp_servers.<server>.tools.<tool>] then approval_mode = "approve". Keys may be bare,
// "basic" or 'literal' strings, and a line may end with a comment. Any other line that sets approval_mode (inline tables, dotted
// keys, unusual quoting) is reported as not understood, so that an approval is never silently missed.
const KEY = String.raw`(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_-]+))`;
const TOOL_TABLE = new RegExp(String.raw`^\[\s*mcp_servers\s*\.\s*${KEY}\s*\.\s*tools\s*\.\s*${KEY}\s*\]\s*(?:#.*)?$`);
const MODE = /^approval_mode\s*=\s*(?:"([^"]*)"|'([^']*)')\s*(?:#.*)?$/;

function codexToml(t, label, out) {
  let section = null;
  (t ?? '').split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim();
    const h = line.match(TOOL_TABLE);
    if (h) { section = { server: h[1] ?? h[2] ?? h[3], tool: h[4] ?? h[5] ?? h[6] }; return; }
    if (line.startsWith('[')) { section = null; return; }
    if (!/approval_mode/.test(line) || line.startsWith('#')) return;
    const kv = section && line.match(MODE);
    if (kv) { if ((kv[1] ?? kv[2]) === 'approve') out.push({ ...section, source: label }); return; }
    out.push({ source: `${label}:${i + 1}`, unreadable: true, error: 'approval_mode in a form this reader does not understand: check it by hand' });
  });
}

export function codex(home, project) {
  const files = [['~/.codex/config.toml', path.join(home, '.codex', 'config.toml')]];
  if (project) files.push([`${project}/.codex/config.toml`, path.join(project, '.codex', 'config.toml')]);
  const out = [];
  let any = null;
  for (const [label, p] of files) {
    const t = read(p);
    if (t === null) continue;
    any = any ?? t;
    if (isErr(t)) { out.push(failed(label, t)); continue; }
    codexToml(t, label, out);
  }
  return store('Codex CLI: config.toml approval_mode = "approve"', any, out);
}

export function cursor(home, project) {
  const files = [['~/.cursor/cli-config.json', path.join(home, '.cursor', 'cli-config.json')]];
  if (project) files.push([`${project}/.cursor/cli.json`, path.join(project, '.cursor', 'cli.json')]);
  const out = [];
  let any = null;
  for (const [label, p] of files) {
    const d = readJson(p);
    if (d === null) continue;
    any = any ?? d;
    if (d === undefined || isErr(d)) { out.push(failed(label, d)); continue; }
    for (const r of d.permissions?.allow ?? []) {
      const m = typeof r === 'string' && r.match(/^Mcp\(([^:]+):(.+)\)$/);
      if (m) out.push({ server: m[1], tool: m[2], source: label });
    }
  }
  return store('Cursor CLI: cli-config.json / project .cursor/cli.json permissions.allow', any, out);
}

export function qwen(home, project) {
  const files = [['~/.qwen/settings.json', path.join(home, '.qwen', 'settings.json')]];
  if (project) files.push([`${project}/.qwen/settings.json`, path.join(project, '.qwen', 'settings.json')]);
  const out = [];
  let any = null;
  for (const [label, p] of files) {
    const d = readJson(p);
    if (d === null) continue;
    any = any ?? d;
    if (d === undefined || isErr(d)) { out.push(failed(label, d)); continue; }
    for (const r of d.permissions?.allow ?? []) { const e = mcpRule(r, label); if (e) out.push(e); }
  }
  return store('Qwen Code: settings.json permissions.allow', any, out);
}

export function gemini(home, project) {
  const files = [['~/.gemini/policies/auto-saved.toml', path.join(home, '.gemini', 'policies', 'auto-saved.toml')]];
  if (project) files.push([`${project}/.gemini/policies/auto-saved.toml`, path.join(project, '.gemini', 'policies', 'auto-saved.toml')]);
  const out = [];
  let any = null;
  for (const [label, p] of files) {
    const t = read(p);
    if (t === null) continue;
    any = any ?? t;
    if (isErr(t)) { out.push(failed(label, t)); continue; }
    // one rule per [[rule]] block; keep blocks that allow an MCP tool
    for (const block of t.split(/^\s*\[\[/m).slice(1)) {
      const get = (k) => { const m = block.match(new RegExp(`^\\s*${k}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'm')); return m ? (m[1] ?? m[2]) : undefined; };
      if (get('decision') === 'allow' && get('mcpName')) out.push({ server: get('mcpName'), tool: get('toolName') ?? '*', source: label });
    }
  }
  const settingsFiles = [['~/.gemini/settings.json', path.join(home, '.gemini', 'settings.json')]];
  if (project) settingsFiles.push([`${project}/.gemini/settings.json`, path.join(project, '.gemini', 'settings.json')]);
  let settings = null;
  for (const [label, p] of settingsFiles) {
    const d = readJson(p);
    if (d === null) continue;
    settings = settings ?? d;
    if (d === undefined || isErr(d)) { out.push(failed(label, d)); continue; }
    for (const [name, s] of Object.entries(d.mcpServers ?? {})) {
      if (s?.trust === true) out.push({ server: name, tool: '*', source: `${label} mcpServers.trust = true (never asks)` });
    }
  }
  return store('Gemini CLI: policies/auto-saved.toml (permanent approvals are off by default)', any ?? settings, out,
    'session approvals live in memory and end with the session');
}

export function goose(home) {
  const p = path.join(home, '.config', 'goose', 'permission.yaml');
  let t = read(p);
  const out = [];
  if (isErr(t)) { out.push(failed('~/.config/goose/permission.yaml', t)); t = null; }
  let inAllow = false;
  for (const raw of (t ?? '').split(/\r?\n/)) {
    if (/^\s*always_allow\s*:/.test(raw)) {
      inAllow = true;
      const inline = raw.match(/\[(.*)\]/);
      if (inline) { for (const x of inline[1].split(',')) addGoose(x, out); inAllow = false; }
      continue;
    }
    if (inAllow && /^\s*-\s*/.test(raw)) { addGoose(raw.replace(/^\s*-\s*/, ''), out); continue; }
    if (inAllow && raw.trim() && !/^\s*-/.test(raw)) inAllow = false;
  }
  let cfg = read(path.join(home, '.config', 'goose', 'config.yaml'));
  if (isErr(cfg)) { out.push(failed('~/.config/goose/config.yaml', cfg)); cfg = null; }
  const mode = cfg?.match(/^\s*GOOSE_MODE\s*:\s*['"]?([a-z_]+)/m)?.[1] ?? null;
  return store('Goose: ~/.config/goose/permission.yaml always_allow', t ?? cfg ?? (out.length ? { readError: out[0].error } : null), out,
    mode ? `GOOSE_MODE = ${mode}${mode === 'auto' ? ' (every tool call is approved without asking)' : ''}`
      : 'GOOSE_MODE not set in config.yaml: the default is auto (every tool call is approved without asking)');
}

function addGoose(raw, out) {
  const v = raw.trim().replace(/^['"]|['"]$/g, '');
  const m = v.match(/^(.+?)__(.+)$/);
  if (m) out.push({ server: m[1], tool: m[2], source: '~/.config/goose/permission.yaml' });
}

export function checkApprovals(home = os.homedir(), project = null) {
  return {
    home, project,
    claude: claude(home, project), codex: codex(home, project), cursor: cursor(home, project),
    gemini: gemini(home, project), goose: goose(home), qwen: qwen(home, project),
  };
}

const CLIENTS = ['claude', 'codex', 'cursor', 'gemini', 'goose', 'qwen'];

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? path.resolve(args[i + 1]) : null; };
  const r = checkApprovals(opt('--home') ?? os.homedir(), opt('--project'));
  if (args.includes('--json')) { console.log(JSON.stringify(r, null, 2)); process.exit(0); }
  for (const name of CLIENTS) {
    const c = r[name];
    console.log(`\n${c.store}${c.present ? '' : '  — not found'}${c.incomplete ? '  — INCOMPLETE: see below' : ''}`);
    if (c.note) console.log(`  note: ${c.note}`);
    if (c.present && !c.entries.length) console.log('  no permanent MCP tool approvals');
    for (const e of c.entries) {
      console.log(e.unreadable ? `  ${e.source}: could not be read (${e.error})` : `  ${e.server} / ${e.tool}   (${e.source})`);
    }
  }
  if (CLIENTS.some((n) => r[n].incomplete)) console.log('\nINCOMPLETE: at least one store could not be read or understood (above). Check it by hand.');
  console.log('\nEach approval above is keyed by name only: a server that changes the tool keeps it. To withdraw one, remove the entry'
    + ' in the client (or the line in the file shown) and approve again after reading the current definition.'
    + '\nAn empty list is not proof that nothing is approved: modes that approve every call (Goose auto, Claude Code and Qwen Code auto'
    + ' modes) need no entry, server-wide entries cover tools added later, and VS Code and Cursor IDE keep approvals in their own'
    + ' storage (check them in the editor).');
}
