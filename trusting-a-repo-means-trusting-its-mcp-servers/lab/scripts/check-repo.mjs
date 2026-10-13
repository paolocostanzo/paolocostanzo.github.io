#!/usr/bin/env node
// Lists the MCP servers a repository declares for AI coding agents, with the
// exact command each one would run. Read-only: it opens files, never runs them.
//
//   node scripts/check-repo.mjs path/to/repo          # human-readable
//   node scripts/check-repo.mjs path/to/repo --json   # machine-readable
//
// Exit code: 0 = nothing declared, 1 = at least one server declared, 2 = usage error,
// 3 = at least one config file could not be read or parsed (the list may be incomplete;
// takes precedence over 0 and 1, so a CI gate never passes on a file it did not understand).
import fs from 'node:fs';
import path from 'node:path';

// jsonc: VS Code reads .vscode/mcp.json as JSON with comments and trailing commas.
export const LOCATIONS = [
  { file: '.mcp.json', readBy: 'Claude Code (also detected by VS Code)', key: 'mcpServers' },
  { file: '.vscode/mcp.json', readBy: 'VS Code', key: 'servers', jsonc: true },
  { file: '.cursor/mcp.json', readBy: 'Cursor (CLI and IDE)', key: 'mcpServers' },
  { file: '.gemini/settings.json', readBy: 'Gemini CLI', key: 'mcpServers' },
  { file: '.qwen/settings.json', readBy: 'Qwen Code', key: 'mcpServers' },
  { file: '.codex/config.toml', readBy: 'Codex CLI', key: 'toml' },
];

export const EXIT = { CLEAN: 0, DECLARED: 1, USAGE: 2, UNPARSED: 3 };

// Removes // and /* */ comments, then trailing commas, outside strings; then parses.
export function parseJsonc(text) {
  const pass = (src, other) => {
    let out = '';
    for (let i = 0; i < src.length; i++) {
      if (src[i] !== '"') { const [kept, next] = other(src, i); out += kept; i = next; continue; }
      let j = i + 1;
      while (j < src.length && src[j] !== '"') j += src[j] === '\\' ? 2 : 1;
      out += src.slice(i, j + 1); i = j;
    }
    return out;
  };
  const noComments = pass(text, (s, i) => {
    if (s.startsWith('//', i)) { const end = s.indexOf('\n', i); return ['\n', end < 0 ? s.length : end]; }
    if (s.startsWith('/*', i)) {
      const end = s.indexOf('*/', i + 2);
      if (end < 0) throw new Error('unterminated /* comment');
      return [' ', end + 1];
    }
    return [s[i], i];
  });
  return JSON.parse(pass(noComments, (s, i) => {
    if (s[i] !== ',') return [s[i], i];
    let j = i + 1;
    while (/\s/.test(s[j] ?? '')) j++;
    return [s[j] === ']' || s[j] === '}' ? '' : ',', i];
  }));
}

// Splits a TOML line at the first # outside strings; also returns the [ { nesting
// left open, so a multiline array or table can be joined with the following lines.
function tomlScan(line) {
  let depth = 0;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') { i++; while (i < line.length && line[i] !== '"') i += line[i] === '\\' ? 2 : 1; }
    else if (c === "'") { i = line.indexOf("'", i + 1); if (i < 0) break; }
    else if (c === '#') return { code: line.slice(0, i).trim(), depth };
    else if (c === '[' || c === '{') depth++;
    else if (c === ']' || c === '}') depth--;
  }
  return { code: line.trim(), depth };
}

// Reads one value at s[i]: basic or literal string, array, inline table, or a bare
// scalar (number, boolean, date) kept as text. Throws on anything else.
function tomlValue(s, i) {
  const ws = () => { while (/\s/.test(s[i] ?? '')) i++; };
  ws();
  if (s.startsWith('"""', i) || s.startsWith("'''", i)) throw new Error('multiline strings are not supported');
  if (s[i] === '"') {
    let j = i + 1;
    while (j < s.length && s[j] !== '"') j += s[j] === '\\' ? 2 : 1;
    if (j >= s.length) throw new Error('unterminated string');
    return [JSON.parse(s.slice(i, j + 1)), j + 1];
  }
  if (s[i] === "'") {
    const j = s.indexOf("'", i + 1);
    if (j < 0) throw new Error('unterminated string');
    return [s.slice(i + 1, j), j + 1];
  }
  if (s[i] === '[' || s[i] === '{') {
    const table = s[i] === '{', close = table ? '}' : ']';
    const out = table ? {} : [];
    i++;
    for (;;) {
      ws();
      if (s[i] === close) return [out, i + 1];
      if (table) {
        let key; [key, i] = tomlKey(s, i); ws();
        if (s[i] !== '=') throw new Error(`expected = after ${key.join('.')}`);
        let v; [v, i] = tomlValue(s, i + 1);
        out[key.join('.')] = v;
      } else {
        let v; [v, i] = tomlValue(s, i);
        out.push(v);
      }
      ws();
      if (s[i] === ',') i++;
      else if (s[i] !== close) throw new Error(`expected , or ${close}`);
    }
  }
  const bare = s.slice(i).match(/^[A-Za-z0-9_.:+-]+/);
  if (!bare) throw new Error(`unexpected ${JSON.stringify(s.slice(i, i + 10))}`);
  return [bare[0], i + bare[0].length];
}

// Reads a dotted key (bare, "basic" or 'literal' parts) into its parts.
function tomlKey(s, i) {
  const parts = [];
  for (;;) {
    while (/\s/.test(s[i] ?? '')) i++;
    const m = s.slice(i).match(/^(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([A-Za-z0-9_-]+))/);
    if (!m) throw new Error(`cannot read key at ${JSON.stringify(s.slice(i, i + 10))}`);
    parts.push(m[1] !== undefined ? JSON.parse(`"${m[1]}"`) : m[2] ?? m[3]);
    i += m[0].length;
    while (/\s/.test(s[i] ?? '')) i++;
    if (s[i] !== '.') return [parts, i];
    i++;
  }
}

// Best-effort reader for the mcp_servers tables used by Codex: basic and literal
// strings, inline and multiline arrays, inline tables, dotted keys, and the
// [mcp_servers.<name>] / [mcp_servers.<name>.env] headers. Whatever it cannot read
// under mcp_servers is pushed to `errors` instead of being guessed; it is not a
// general TOML parser.
export function parseCodexToml(text, errors = []) {
  const servers = {};
  const server = (name) => (servers[name] ??= { raw: {}, args: [], env: [] });
  // Applies one value at a full key path (table header + dotted key).
  const set = (keyPath, value, raw) => {
    if (keyPath[0] !== 'mcp_servers') return;
    const [, name, field, ...rest] = keyPath;
    if (name === undefined) {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        for (const [n, v] of Object.entries(value)) set(['mcp_servers', n], v, raw);
      } else errors.push(`mcp_servers: unexpected value ${raw}`);
      return;
    }
    const s = server(name);
    if (field === undefined) {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        for (const [k, v] of Object.entries(value)) set(['mcp_servers', name, ...k.split('.')], v, raw);
      } else errors.push(`mcp_servers.${name}: unexpected value ${raw}`);
      return;
    }
    if (field === 'env') {
      if (rest.length) s.env.push(rest.join('.'));
      else if (value && typeof value === 'object' && !Array.isArray(value)) s.env.push(...Object.keys(value));
      else errors.push(`mcp_servers.${name}.env: expected a table, got ${raw}`);
      return;
    }
    if (rest.length) return;
    s.raw[field] = raw;
    if (field === 'command' || field === 'url') {
      if (typeof value === 'string') s[field] = value;
      else errors.push(`mcp_servers.${name}.${field}: expected a string, got ${raw}`);
    }
    if (field === 'args') {
      if (Array.isArray(value) && value.every((v) => typeof v === 'string')) s.args = value;
      else errors.push(`mcp_servers.${name}.args: expected an array of strings, got ${raw}`);
    }
  };

  let table = [];
  let pending = null;
  for (const raw of text.split(/\r?\n/)) {
    const { code, depth } = tomlScan(raw);
    if (pending) {
      pending.text += '\n' + code; pending.depth += depth;
      if (pending.depth > 0) continue;
    } else {
      if (!code) continue;
      if (code.startsWith('[[')) {
        const inside = /^\[\[\s*mcp_servers\b/.test(code);
        if (inside) errors.push(`unsupported array of tables: ${code}`);
        table = ['#array'];
        continue;
      }
      if (code.startsWith('[')) {
        try {
          const [parts, end] = tomlKey(code, 1);
          if (code.slice(end).trim() !== ']') throw new Error('bad header');
          table = parts;
          if (parts[0] === 'mcp_servers' && parts.length >= 2) server(parts[1]);
        } catch {
          table = ['#unknown'];
          errors.push(`cannot read table header: ${code}`);
        }
        continue;
      }
      pending = { text: code, depth };
      if (depth > 0) continue;
    }
    const line = pending.text;
    pending = null;
    let keyPath;
    try {
      const [key, i] = tomlKey(line, 0);
      keyPath = [...table, ...key];
      if (line[i] !== '=') throw new Error('expected =');
      const [value, end] = tomlValue(line, i + 1);
      if (line.slice(end).trim()) throw new Error(`unexpected ${JSON.stringify(line.slice(end).trim())}`);
      set(keyPath, value, line.slice(i + 1).trim());
    } catch (e) {
      // Only lines that may belong to mcp_servers matter; the rest of the file is not ours to judge.
      if ((keyPath ?? table)[0] === 'mcp_servers' || /^\s*["']?mcp_servers\b/.test(line)) {
        errors.push(`${(keyPath ?? table).join('.') || line}: ${e.message}`);
      }
    }
  }
  if (pending) errors.push(`unterminated value: ${pending.text.split('\n')[0]}`);
  return servers;
}

export function checkRepo(dir) {
  const found = [];
  const errors = [];
  for (const loc of LOCATIONS) {
    const target = path.join(dir, loc.file);
    if (!fs.existsSync(target)) continue;
    try {
      const text = fs.readFileSync(target, 'utf8');
      if (loc.key === 'toml') {
        const tomlErrors = [];
        for (const [name, s] of Object.entries(parseCodexToml(text, tomlErrors))) {
          found.push({ file: loc.file, readBy: loc.readBy, name, command: s.command ?? s.url ?? null, args: s.args, env: s.env });
        }
        for (const error of tomlErrors) errors.push({ file: loc.file, error });
      } else {
        const servers = (loc.jsonc ? parseJsonc(text) : JSON.parse(text))[loc.key] ?? {};
        if (typeof servers !== 'object' || Array.isArray(servers)) throw new Error(`"${loc.key}" is not an object`);
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

export function exitCode(r) {
  if (r.errors.length) return EXIT.UNPARSED;
  return r.found.length ? EXIT.DECLARED : EXIT.CLEAN;
}

const USAGE = `usage: check-repo.mjs <repo> [--json]

exit code: 0 = no MCP server declared
           1 = at least one server declared (listed with the command it would run)
           2 = usage error
           3 = a config file could not be read or parsed: the list may be incomplete
               (wins over 0 and 1; inspect the file by hand)`;

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) { console.log(USAGE); process.exit(0); }
  const dir = args.find((a) => !a.startsWith('-'));
  if (!dir) { console.error(USAGE); process.exit(EXIT.USAGE); }
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
    if (r.errors.length) console.log('\nSome configuration could not be read: the list above may be incomplete (exit 3).');
    if (r.found.length) console.log('\nOpening this repository in an agent and trusting it may start the commands above.');
  }
  process.exit(exitCode(r));
}
